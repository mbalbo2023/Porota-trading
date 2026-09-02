"""Archivo histórico de respaldo basado en la API pública Data912.

Data912 se usa únicamente para contexto diario, indicadores, backtesting,
aprendizaje y reparación batch de históricos. Nunca reemplaza a PPI para
cotización vigente, book, saldo, sizing, decisión de entrada/salida ni
ejecución.

HF6-v2 añade dos invariantes explícitos:
- DATA912_EXECUTION_ALLOWED=False;
- la actualización batch puede ser dirigida por el universo de Porota mediante
  ``refresh_symbols`` en lugar de dejar que Data912 decida qué especies cubrir.
"""

import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

import requests

import al_historical_ingest as hist

logger = logging.getLogger("data912_history")

DATA912_BASE = os.getenv("DATA912_BASE", "https://data912.com").rstrip("/")
DATA912_TIMEOUT_SECONDS = float(os.getenv("DATA912_TIMEOUT_SECONDS", "30"))
DATA912_RETRIES = int(os.getenv("DATA912_RETRIES", "5"))
DATA912_REFRESH_ENABLED = os.getenv("DATA912_REFRESH_ENABLED", "true").lower() == "true"
DATA912_EXECUTION_ALLOWED = False
DATA912_MIN_LIQUIDITY_ARS = float(
    os.getenv("DATA912_MIN_LIQUIDITY_ARS", os.getenv("MIN_LIQUIDITY_ARS", "5000000"))
)
STATE_PATH = os.getenv("DATA912_STATE_PATH", "./data/data912_refresh_state.json")

GROUPS = {
    "ACCIONES": ("/live/arg_stocks", "/historical/stocks/{ticker}"),
    "CEDEARS": ("/live/arg_cedears", "/historical/cedears/{ticker}"),
    "BONOS": ("/live/arg_bonds", "/historical/bonds/{ticker}"),
}


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _session() -> requests.Session:
    client = requests.Session()
    client.headers.update({"User-Agent": "Porota-Trading/17.0 hf6 historical-backup"})
    return client


def _get_json(client: requests.Session, path: str):
    last_error = None
    for attempt in range(1, DATA912_RETRIES + 1):
        try:
            response = client.get(DATA912_BASE + path, timeout=DATA912_TIMEOUT_SECONDS)
            if response.status_code == 200:
                return response.json()
            if response.status_code == 429:
                wait = min(30, attempt * 5)
                logger.warning("Data912 limitó %s; reintento en %ss.", path, wait)
                time.sleep(wait)
                continue
            last_error = RuntimeError(f"Data912 {path}: HTTP {response.status_code}")
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
        if attempt < DATA912_RETRIES:
            time.sleep(min(20, attempt * 3))
    raise RuntimeError(str(last_error or f"Data912 no respondió para {path}"))


def _primary_liquid(rows: list) -> List[Tuple[str, float]]:
    symbols = {
        str(row.get("symbol") or "").strip().upper()
        for row in rows
        if str(row.get("symbol") or "").strip()
    }
    selected: Dict[str, float] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        base = symbol
        if re.search(r"\.[CD]$", symbol):
            base = symbol[:-2]
        elif symbol.endswith(("C", "D")) and symbol[:-1] in symbols:
            base = symbol[:-1]
        if base != symbol and base in symbols:
            continue
        price = _number(row.get("c"))
        volume = _number(row.get("v"))
        traded_ars = price * volume
        if price > 0 and volume > 0 and traded_ars >= DATA912_MIN_LIQUIDITY_ARS:
            selected[symbol] = max(traded_ars, selected.get(symbol, 0))
    return sorted(selected.items(), key=lambda item: item[1], reverse=True)


def _normalize(rows: list, cutoff: str) -> List[tuple]:
    candles = []
    for row in rows if isinstance(rows, list) else []:
        day = str(row.get("date") or "")[:10]
        close = _number(row.get("c"))
        if not day or day < cutoff or close <= 0:
            continue
        candles.append((
            day,
            _number(row.get("o")) or None,
            _number(row.get("h")) or None,
            _number(row.get("l")) or None,
            close,
            _number(row.get("v")),
        ))
    return candles


def _write_state(payload: dict) -> None:
    try:
        folder = os.path.dirname(STATE_PATH) or "."
        os.makedirs(folder, exist_ok=True)
        temporary = f"{STATE_PATH}.{os.getpid()}.tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
        os.replace(temporary, STATE_PATH)
    except Exception as exc:
        logger.warning("No se pudo guardar el estado de Data912: %s", exc)


def refresh(days: int = hist.HIST_DEFAULT_DAYS) -> dict:
    """Actualiza el universo líquido propio de Data912.

    Se conserva por compatibilidad y como control complementario, pero HF6-v2
    no usa esta selección como universo autoritativo de Porota. Para reparar
    el histórico del catálogo de Porota se debe usar ``refresh_symbols``.
    """
    if not DATA912_REFRESH_ENABLED:
        return {"ok": False, "disabled": True, "reason": "DATA912_REFRESH_ENABLED=false"}

    started = datetime.now().isoformat()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    client = _session()
    selected = successful = empty = failed = rows_written = 0
    hist.init_db()
    _write_state({"status": "running", "started_at": started, "mode": "provider_universe"})

    try:
        for asset_class, (live_path, historical_path) in GROUPS.items():
            live = _get_json(client, live_path)
            if not isinstance(live, list):
                raise RuntimeError(f"Data912 {asset_class}: respuesta live inválida")
            candidates = _primary_liquid(live)
            selected += len(candidates)
            logger.info("Data912 %s: %d candidatos líquidos primarios.",
                        asset_class, len(candidates))
            for symbol, _traded_ars in candidates:
                try:
                    raw = _get_json(client, historical_path.format(ticker=symbol))
                    candles = _normalize(raw, cutoff)
                    if not candles:
                        empty += 1
                        logger.info("Data912 %s/%s: sin histórico.", asset_class, symbol)
                    else:
                        rows_written += hist.guardar_velas(
                            symbol, asset_class, candles, "DATA912", adjusted=False)
                        successful += 1
                except Exception as exc:
                    failed += 1
                    logger.warning("Data912 %s/%s falló: %s", asset_class, symbol, exc)
                time.sleep(hist.HIST_BATCH_SLEEP)

        finished = datetime.now().isoformat()
        with hist._conn() as connection:
            connection.execute(
                """INSERT INTO ingest_runs
                   (started_at, finished_at, source, symbols_ok, symbols_failed,
                    rows_written, notes) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (started, finished, "DATA912", successful, failed + empty, rows_written,
                 f"seleccionados={selected}; sin_historico={empty}; "
                 f"errores={failed}; dias={days}; umbral_ars={DATA912_MIN_LIQUIDITY_ARS}"),
            )
            connection.commit()
        result = {
            "ok": successful > 0,
            "selected": selected,
            "successful": successful,
            "without_history": empty,
            "failed": failed,
            "rows_written": rows_written,
            "finished_at": finished,
            "execution_allowed": DATA912_EXECUTION_ALLOWED,
        }
        _write_state({"status": "ok" if result["ok"] else "error", **result})
        if not result["ok"]:
            raise RuntimeError("Data912 no devolvió histórico para ningún instrumento.")
        logger.info("Data912 actualizado: %s", result)
        return result
    except Exception as exc:
        _write_state({"status": "error", "started_at": started, "error": str(exc)[:500]})
        raise


def ensure_symbol(symbol: str, asset_class: str,
                  days: int = hist.HIST_DEFAULT_DAYS) -> int:
    """Completa bajo demanda un símbolo descubierto por PPI que falta localmente."""
    config = GROUPS.get(asset_class)
    if not config:
        return 0
    client = _session()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    raw = _get_json(client, config[1].format(ticker=symbol))
    candles = _normalize(raw, cutoff)
    return hist.guardar_velas(symbol, asset_class, candles, "DATA912", adjusted=False)


def refresh_symbols(targets: Iterable[Tuple[str, str]],
                    days: int = hist.HIST_DEFAULT_DAYS,
                    *, batch_limit: Optional[int] = None) -> dict:
    """Repara automáticamente históricos elegidos por Porota.

    ``targets`` contiene pares ``(symbol, asset_class)`` provenientes del
    catálogo/readiness de Porota. Sólo se aceptan familias explícitamente
    soportadas por Data912. Esta función escribe exclusivamente en la base
    histórica y nunca devuelve un precio apto para ejecutar una orden.
    """
    if not DATA912_REFRESH_ENABLED:
        return {"ok": False, "disabled": True, "reason": "DATA912_REFRESH_ENABLED=false",
                "execution_allowed": DATA912_EXECUTION_ALLOWED}

    normalized: list[Tuple[str, str]] = []
    seen = set()
    for raw_symbol, raw_family in targets:
        symbol = str(raw_symbol or "").strip().upper()
        family = str(raw_family or "").strip().upper()
        key = (symbol, family)
        if not symbol or family not in GROUPS or key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    if batch_limit is not None:
        normalized = normalized[:max(0, int(batch_limit))]

    started = datetime.now().isoformat()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    client = _session()
    hist.init_db()
    successful = empty = failed = rows_written = 0
    results = []
    _write_state({"status": "running", "started_at": started,
                  "mode": "porota_targets", "selected": len(normalized)})

    for symbol, family in normalized:
        path = GROUPS[family][1].format(ticker=symbol)
        try:
            raw = _get_json(client, path)
            candles = _normalize(raw, cutoff)
            if not candles:
                empty += 1
                state = "EMPTY_OR_INVALID"
                written = 0
            else:
                written = hist.guardar_velas(symbol, family, candles, "DATA912", adjusted=False)
                rows_written += written
                successful += 1
                state = "HISTORICAL_ROWS_WRITTEN"
            results.append({"symbol": symbol, "asset_class": family,
                            "state": state, "rows_written": written})
        except Exception as exc:
            failed += 1
            results.append({"symbol": symbol, "asset_class": family,
                            "state": "ERROR", "error_class": type(exc).__name__,
                            "detail": str(exc)[:300], "rows_written": 0})
            logger.warning("Data912 batch %s/%s falló: %s", family, symbol, exc)
        time.sleep(hist.HIST_BATCH_SLEEP)

    finished = datetime.now().isoformat()
    with hist._conn() as connection:
        connection.execute(
            """INSERT INTO ingest_runs
               (started_at, finished_at, source, symbols_ok, symbols_failed,
                rows_written, notes) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (started, finished, "DATA912_POROTA_BATCH", successful, failed + empty,
             rows_written,
             f"seleccionados_por_porota={len(normalized)}; sin_historico={empty}; "
             f"errores={failed}; dias={days}; execution_allowed=false"),
        )
        connection.commit()

    result = {
        "ok": failed == 0 and empty == 0 if normalized else True,
        "selected": len(normalized),
        "successful": successful,
        "without_history": empty,
        "failed": failed,
        "rows_written": rows_written,
        "started_at": started,
        "finished_at": finished,
        "execution_allowed": DATA912_EXECUTION_ALLOWED,
        "results": results,
    }
    _write_state({"status": "ok" if result["ok"] else "partial", **result})
    return result


def archived_instruments(min_candles: int = hist.HIST_MIN_DAYS_USABLE) -> List[dict]:
    hist.init_db()
    with hist._conn() as connection:
        rows = connection.execute(
            """SELECT symbol, asset_class, COUNT(*) AS candles
               FROM market_historical_ohlcv
               GROUP BY symbol, asset_class HAVING COUNT(*) >= ?
               ORDER BY asset_class, symbol""",
            (min_candles,),
        ).fetchall()
    return [{"symbol": row[0], "asset_class": row[1], "candles": row[2]}
            for row in rows]


def archived_liquidity(symbol: str, asset_class: str,
                       days_back: int = 5) -> Optional[float]:
    """Volumen medio operado del archivo; None distingue ausencia de iliquidez."""
    hist.init_db()
    with hist._conn() as connection:
        rows = connection.execute(
            """SELECT close, volume FROM market_historical_ohlcv
               WHERE symbol=? AND asset_class=? ORDER BY date DESC LIMIT ?""",
            (symbol, asset_class, days_back),
        ).fetchall()
    if not rows:
        return None
    volumes = [_number(row[1]) for row in rows]
    prices = [_number(row[0]) for row in rows]
    avg_volume = sum(volumes) / len(volumes)
    avg_price = sum(prices) / len(prices)
    return avg_volume if asset_class == "BONOS" else avg_price * avg_volume
