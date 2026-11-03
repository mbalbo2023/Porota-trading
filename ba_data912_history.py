"""Archivo histórico de respaldo basado en la API pública Data912.

Data912 se usa únicamente para contexto diario, indicadores, backtesting,
aprendizaje y reparación batch de históricos. Nunca reemplaza a PPI para
cotización vigente, book, saldo, sizing, decisión de entrada/salida ni
ejecución.

HF6-v2 añade invariantes explícitos:
- DATA912_EXECUTION_ALLOWED=False;
- el batch puede ser dirigido por el universo de Porota mediante
  ``refresh_symbols``;
- Data912 sólo completa huecos y nunca pisa una vela histórica de mayor
  autoridad (PPI/BYMA/IOL o una serie marcada adjusted).
"""

import json
import logging
import math
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
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
    # La ruta se conserva para leer legado; no puede ingresar a nuevos refresh.
    "BONOS": ("/live/arg_bonds", "/historical/bonds/{ticker}"),
}
OPERATIONAL_FAMILIES = frozenset(("ACCIONES", "CEDEARS"))

# Data912 es fallback. Nunca debe degradar una fila que ya llegó de una fuente
# de mayor autoridad ni una serie ajustada.
AUTHORITATIVE_SOURCE_PREFIXES = ("PPI", "BYMA", "IOL")


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
    reason = 'DATA912_PROVIDER_UNAVAILABLE'
    for attempt in range(1, DATA912_RETRIES + 1):
        try:
            response = client.get(DATA912_BASE + path, timeout=DATA912_TIMEOUT_SECONDS)
            if response.status_code == 200:
                return response.json()
            if response.status_code == 429:
                reason = 'DATA912_RATE_LIMIT'
                wait = min(30, attempt * 5)
                logger.warning("Data912 limitó %s; reintento en %ss.", path, wait)
                time.sleep(wait)
                continue
            last_error = RuntimeError(f"Data912 {path}: HTTP {response.status_code}")
            reason = 'DATA912_HTTP_ERROR'
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            reason = 'DATA912_TIMEOUT' if isinstance(exc,getattr(requests,'Timeout',TimeoutError)) else 'DATA912_PAYLOAD_INVALID'
        if attempt < DATA912_RETRIES:
            time.sleep(min(20, attempt * 3))
    # Exception text may contain provider URLs, headers or tokens. Retain a
    # fixed taxonomy, never raw connector diagnostics in caches/reports.
    raise RuntimeError(reason) from None


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
        # Provider-universe selection is advisory too: a monetary threshold
        # requires explicit ARS and quantity/money semantics from this row.
        if str(row.get('currency') or '').upper()!='ARS':continue
        price = _number(row.get("c"))
        volume = _number(row.get("v"))
        unit=str(row.get('volume_kind') or 'UNKNOWN').upper()
        traded_ars = volume if unit=='MONEY' else price*volume if unit=='QUANTITY' else None
        if (traded_ars is not None and all(math.isfinite(x) for x in (price,volume,traded_ars))
                and price>0 and volume>0 and traded_ars>=DATA912_MIN_LIQUIDITY_ARS):
            selected[symbol] = max(traded_ars, selected.get(symbol, 0))
    return sorted(selected.items(), key=lambda item: item[1], reverse=True)


def _normalize(rows: list, cutoff: str, *, as_of=None, return_rejections=False) -> List[tuple]:
    """Typed, per-row validation; partial payloads are never called complete."""
    candles = []
    rejected = []
    known = as_of or datetime.now(timezone.utc)
    from cu_history_store_v2_hf6 import instant, ART
    known = instant(known)
    by_day = {}
    if not isinstance(rows,list):
        rejected=[{'index':-1,'reason':'PAYLOAD_NOT_LIST'}]
        return ([],rejected) if return_rejections else []
    for index,row in enumerate(rows if isinstance(rows,list) else []):
        try:
            if not isinstance(row,dict): raise ValueError('ROW_NOT_OBJECT')
            raw_day=str(row.get('date') or '')
            try:
                if len(raw_day)==10:
                    day=date.fromisoformat(raw_day)
                    if raw_day!=day.isoformat():raise ValueError('DATE_INVALID')
                else:
                    source_at=instant(raw_day)
                    day=source_at.astimezone(ART).date()
                    if source_at>known:raise ValueError('DATE_IN_FUTURE')
            except ValueError as exc:
                if str(exc)=='DATE_IN_FUTURE':raise
                raise ValueError('DATE_INVALID') from None
            if day>known.astimezone(ART).date(): raise ValueError('DATE_IN_FUTURE')
            if day.isoformat()<cutoff: raise ValueError('DATE_BEFORE_REQUEST')
            if day.weekday()>=5: raise ValueError('SESSION_WEEKEND')
            from ak_byma_calendar import ANIOS_AUDITADOS,motivo_no_operativo
            if day.year in ANIOS_AUDITADOS and motivo_no_operativo(day): raise ValueError('SESSION_NOT_OPERATIONAL')
            values=[]
            for field in ('o','h','l','c','v'):
                if row.get(field) in (None,''): raise ValueError(field.upper()+'_MISSING')
                if isinstance(row[field],bool):raise ValueError(field.upper()+'_BOOLEAN')
                number=float(row[field])
                if not math.isfinite(number): raise ValueError(field.upper()+'_NONFINITE')
                if number<0 if field=='v' else number<=0: raise ValueError(field.upper()+'_NONPOSITIVE')
                values.append(number)
            opening,high,low,close,volume=values
            if not low<=min(opening,close)<=max(opening,close)<=high: raise ValueError('OHLC_INCONSISTENT')
            normalized=hist.HistoricalRow((day.isoformat(),opening,high,low,close,volume),
                currency=row.get('currency') or 'UNKNOWN',volume_kind=row.get('volume_kind') or 'UNKNOWN')
            by_day.setdefault(day,[]).append((index,normalized))
        except (ValueError,TypeError,OverflowError) as exc:
            reason=str(exc) if isinstance(exc,ValueError) and str(exc).replace('_','').isalnum() else 'ROW_INVALID'
            rejected.append({'index':index,'reason':reason})
    for day in sorted(by_day):
        entries=by_day[day]
        if len({(tuple(row),row.currency,row.volume_kind) for _,row in entries})>1:
            rejected.extend({'index':index,'reason':'DUPLICATE_DAY_CONFLICT'} for index,_ in entries)
            continue
        candles.append(entries[0][1])
        rejected.extend({'index':index,'reason':'DUPLICATE_DAY'} for index,_ in entries[1:])
    rejected.sort(key=lambda row:row['index'])
    candles.sort(key=lambda row:row[0])
    return (candles,rejected) if return_rejections else candles


def _is_authoritative(source, adjusted) -> bool:
    text = str(source or "").upper()
    return bool(adjusted) or any(text.startswith(prefix) for prefix in AUTHORITATIVE_SOURCE_PREFIXES)


def _store_data912_candles(symbol: str, asset_class: str,
                           candles: List[tuple]) -> Tuple[int, int]:
    """Store Data912 only where no higher-authority row already exists.

    Returns (written, protected). The helper deliberately reads the current
    canonical store before calling the legacy UPSERT, so Data912 can refresh
    its own rows while PPI/BYMA/IOL rows remain untouched.
    """
    if not candles:
        return 0, 0
    hist.init_db()
    dates = [str(row[0]) for row in candles]
    existing = {}
    with hist._conn() as connection:
        # A one-year batch stays well below SQLite's usual bind limit.
        placeholders = ",".join("?" for _ in dates)
        rows = connection.execute(
            f"""SELECT date,source,adjusted FROM market_historical_ohlcv
                WHERE symbol=? AND asset_class=? AND date IN ({placeholders})""",
            (symbol, asset_class, *dates),
        ).fetchall()
        existing = {str(row[0]): (row[1], row[2]) for row in rows}

    safe = []
    protected = 0
    for candle in candles:
        prior = existing.get(str(candle[0]))
        if prior and _is_authoritative(prior[0], prior[1]):
            protected += 1
            continue
        safe.append(candle)
    written = hist.guardar_velas(symbol, asset_class, safe, "DATA912", adjusted=False) if safe else 0
    return written, protected


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

    Se conserva por compatibilidad y control complementario. HF6-v2 usa
    ``refresh_symbols`` para reparar el catálogo gobernado por Porota.
    """
    if not DATA912_REFRESH_ENABLED:
        return {"ok": False, "disabled": True, "reason": "DATA912_REFRESH_ENABLED=false"}

    started = datetime.now().isoformat()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    client = _session()
    selected = successful = empty = failed = rows_written = protected_rows = 0
    hist.init_db()
    _write_state({"status": "running", "started_at": started, "mode": "provider_universe"})

    try:
        for asset_class, (live_path, historical_path) in GROUPS.items():
            if asset_class not in OPERATIONAL_FAMILIES:
                logger.info("Data912 %s: omitida fuera de alcance operativo.", asset_class)
                continue
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
                        written, protected = _store_data912_candles(symbol, asset_class, candles)
                        rows_written += written
                        protected_rows += protected
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
                 f"seleccionados={selected}; sin_historico={empty}; errores={failed}; "
                 f"protegidas_fuente_superior={protected_rows}; dias={days}; "
                 f"umbral_ars={DATA912_MIN_LIQUIDITY_ARS}"),
            )
            connection.commit()
        result = {
            "ok": successful > 0,
            "selected": selected,
            "successful": successful,
            "without_history": empty,
            "failed": failed,
            "rows_written": rows_written,
            "protected_rows": protected_rows,
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
    """Completa un símbolo sin sobreescribir fuentes históricas superiores."""
    family = str(asset_class or "").upper()
    if family not in OPERATIONAL_FAMILIES:
        return 0
    config = GROUPS.get(family)
    if not config:
        return 0
    client = _session()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    raw = _get_json(client, config[1].format(ticker=symbol))
    candles = _normalize(raw, cutoff)
    written, _protected = _store_data912_candles(symbol, asset_class, candles)
    return written


def refresh_symbols(targets: Iterable[Tuple[str, str]],
                    days: int = hist.HIST_DEFAULT_DAYS,
                    *, batch_limit: Optional[int] = None) -> dict:
    """Repara automáticamente históricos elegidos por Porota.

    Sólo se aceptan familias explícitamente soportadas por Data912. Esta
    función escribe exclusivamente histórico y nunca devuelve un precio apto
    para ejecutar una orden. Las filas PPI/BYMA/IOL existentes quedan
    protegidas frente al fallback.
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
        if not symbol or family not in OPERATIONAL_FAMILIES or family not in GROUPS or key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    if batch_limit is not None:
        normalized = normalized[:max(0, int(batch_limit))]

    started = datetime.now().isoformat()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    client = _session()
    hist.init_db()
    successful = empty = failed = rows_written = protected_rows = 0
    results = []
    _write_state({"status": "running", "started_at": started,
                  "mode": "porota_targets", "selected": len(normalized),
                  "execution_allowed": False})

    for symbol, family in normalized:
        path = GROUPS[family][1].format(ticker=symbol)
        try:
            raw = _get_json(client, path)
            candles = _normalize(raw, cutoff)
            if not candles:
                empty += 1
                state = "EMPTY_OR_INVALID"
                written = protected = 0
            else:
                written, protected = _store_data912_candles(symbol, family, candles)
                rows_written += written
                protected_rows += protected
                successful += 1
                state = "HISTORICAL_ROWS_WRITTEN" if written else "ONLY_AUTHORITATIVE_ROWS_PRESENT"
            results.append({"symbol": symbol, "asset_class": family,
                            "state": state, "rows_written": written,
                            "protected_rows": protected})
        except Exception as exc:
            failed += 1
            results.append({"symbol": symbol, "asset_class": family,
                            "state": "ERROR", "error_class": type(exc).__name__,
                            "detail": str(exc)[:300], "rows_written": 0,
                            "protected_rows": 0})
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
             f"errores={failed}; protegidas_fuente_superior={protected_rows}; "
             f"dias={days}; execution_allowed=false"),
        )
        connection.commit()

    result = {
        "ok": failed == 0 and empty == 0 if normalized else True,
        "selected": len(normalized),
        "successful": successful,
        "without_history": empty,
        "failed": failed,
        "rows_written": rows_written,
        "protected_rows": protected_rows,
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
                       days_back: int = 5, *, currency=None) -> Optional[float]:
    """Cash per row, with explicit currency and unit; UNKNOWN is non-binding."""
    if type(days_back) is not int or days_back<1:raise ValueError('HISTORY_LOOKBACK_POSITIVE_REQUIRED')
    hist.init_db()
    with hist._conn() as connection:
        rows = connection.execute(
            """SELECT close,volume,currency,volume_kind,cash_turnover FROM market_historical_ohlcv
               WHERE symbol=? AND asset_class=? ORDER BY date DESC LIMIT ?""",
            (symbol, asset_class, days_back),
        ).fetchall()
    if not rows:
        return None
    if not currency: return None
    from bs_instrument_contracts import cash_currency
    currency=cash_currency(currency)
    amounts=[]
    for price,volume,row_currency,kind,cash in rows:
        if row_currency!=currency: return None
        try:
            if cash is not None:
                amount=float(cash)
            elif kind=='MONEY':
                amount=float(volume)
            elif kind=='QUANTITY' and asset_class in OPERATIONAL_FAMILIES:
                amount=float(price)*float(volume)
            else:return None  # Nominal/contract multipliers require a contract.
        except (TypeError,ValueError,OverflowError):return None
        if not math.isfinite(amount) or amount<0: return None
        amounts.append(amount)
    average=sum(amount/len(amounts) for amount in amounts)
    return average if math.isfinite(average) else None
