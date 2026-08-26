"""Observador productivo con paper trading completo y cero capacidad operativa."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from bd_ppi_readonly_guard import ProductionMarketReader, ReadOnlyPolicyViolation
from be_paper_engine import D, PaperBroker, PaperStore, Quote, now_iso


VERSION = "16.3.4"
TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
DB_PATH = os.getenv("PAPER_DB_PATH", "data/observer/observer_production.db")
SECRET_PATH = os.getenv("PPI_PRODUCTION_SECRET_FILE", "/run/secrets/ppi_production.json")
INTERVAL = max(15, int(os.getenv("PAPER_OBSERVER_INTERVAL_SECONDS", "60")))
COMMAND_POLL_SECONDS = max(3, int(os.getenv("PAPER_COMMAND_POLL_SECONDS", "5")))
PUBLIC_CHECK_SECONDS = max(900, int(os.getenv("PUBLIC_SOURCE_CHECK_SECONDS", "21600")))
LOGIN_COOLDOWN_SECONDS = max(300, int(os.getenv("PPI_LOGIN_COOLDOWN_SECONDS", "900")))
ACTIVE_SYMBOL_LIMIT = max(3, min(int(os.getenv("PAPER_ACTIVE_SYMBOL_LIMIT", "12")), 30))
MARKET_OPEN_HOUR = int(os.getenv("MARKET_OPEN_HOUR", "11"))
MARKET_OPEN_MINUTE = int(os.getenv("MARKET_OPEN_MINUTE", "0"))
MARKET_CLOSE_HOUR = int(os.getenv("MARKET_CLOSE_HOUR", "17"))
MARKET_CLOSE_MINUTE = int(os.getenv("MARKET_CLOSE_MINUTE", "0"))
PREOPEN_MINUTES = max(5, int(os.getenv("PAPER_PREOPEN_MINUTES", "15")))
CORE_SYMBOLS = (
    ("GGAL", "ACCIONES", "A-24HS"),
    ("AL30", "BONOS", "A-24HS"),
    ("AAPL", "CEDEARS", "A-24HS"),
)
STOP = False

SAFE_PAPER_TYPES = {"ACCIONES", "CEDEARS", "BONOS", "ETF", "ETFS"}
SETTLEMENT_BY_TYPE = {
    "ACCIONES": "A-24HS", "CEDEARS": "A-24HS", "BONOS": "A-24HS",
    "ETF": "A-24HS", "ETFS": "A-24HS", "OPCIONES": "A-24HS",
    "FUTUROS": "A-24HS", "CAUCIONES": "INMEDIATA", "FCI": "INMEDIATA",
}
WATCHLIST_PATH = Path(os.getenv("INSTRUMENT_WATCHLIST_PATH", "n_instrument_watchlist.json"))


def _stop(*_):
    global STOP
    STOP = True


def _secret():
    with open(SECRET_PATH, encoding="utf-8") as f:
        value = json.load(f)
    key, secret = value.get("api_key", ""), value.get("api_secret", "")
    if not key or not secret:
        raise RuntimeError("El archivo secreto productivo esta incompleto.")
    return key, secret


def _business_day(day):
    try:
        import ak_byma_calendar as calendar
        return bool(calendar.es_dia_habil_operativo(day))
    except Exception:
        return day.weekday() < 5


def _market_phase(now=None):
    now = now or datetime.now(TZ)
    if not _business_day(now.date()):
        return "CLOSED"
    minute = now.hour * 60 + now.minute
    opening = MARKET_OPEN_HOUR * 60 + MARKET_OPEN_MINUTE
    closing = MARKET_CLOSE_HOUR * 60 + MARKET_CLOSE_MINUTE
    if opening - PREOPEN_MINUTES <= minute < opening:
        return "PREOPEN"
    if opening <= minute < closing:
        return "OPEN"
    return "CLOSED"


def _market_open(now=None):
    return _market_phase(now) == "OPEN"


def _candidate_universe():
    """Candidatos locales y del archivo curado; derivados son solo contexto."""
    rows = {(ticker, kind, settlement, "BYMA", True)
            for ticker, kind, settlement in CORE_SYMBOLS}
    try:
        data = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
        for asset_class, block in data.items():
            if str(asset_class).startswith("_") or not isinstance(block, dict):
                continue
            kind = str(block.get("instrument_type") or asset_class).upper()
            settlement = str(block.get("settlement") or SETTLEMENT_BY_TYPE.get(kind, "A-24HS"))
            for ticker in block.get("tickers", []):
                if str(ticker).strip():
                    rows.add((str(ticker).strip().upper(), kind, settlement, "BYMA",
                              kind in SAFE_PAPER_TYPES))
    except Exception:
        pass
    # Contexto derivado: se valida disponibilidad, nunca entra al paper broker
    # sin multiplicador, vencimiento y margen atribuible al contrato.
    rows.add(("DLR", "FUTUROS", "A-24HS", "ROFEX", False))
    rows.add(("GGAL", "OPCIONES", "A-24HS", "BYMA", False))
    return sorted(rows, key=lambda value: (not value[4], value[1], value[0]))


def _walk_numbers(value, keys):
    """Encuentra el primer numero bajo cualquiera de las claves, sin asumir SDK."""
    if isinstance(value, dict):
        lowered = {str(k).lower(): v for k, v in value.items()}
        for key in keys:
            if key.lower() in lowered and lowered[key.lower()] not in (None, ""):
                candidate = D(lowered[key.lower()], "-1")
                if candidate >= 0:
                    return candidate
        for child in value.values():
            found = _walk_numbers(child, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _walk_numbers(child, keys)
            if found is not None:
                return found
    return None


def _levels(book, side):
    if not isinstance(book, dict):
        return []
    aliases = ("bids", "bid", "buy", "compras") if side == "bid" else (
        "offers", "offer", "asks", "ask", "sell", "ventas")
    for key, value in book.items():
        if str(key).lower() in aliases:
            return value if isinstance(value, list) else [value]
    for value in book.values():
        if isinstance(value, dict):
            found = _levels(value, side)
            if found:
                return found
    return []


def _level(level):
    price = _walk_numbers(level, ("price", "precio"))
    size = _walk_numbers(level, ("quantity", "size", "cantidad", "volume"))
    return price or Decimal(0), size or Decimal(0)


def normalize_quote(symbol, asset_class, settlement, current, book):
    last = _walk_numbers(current, ("price", "last", "lastprice", "ultimo", "close")) or Decimal(0)
    bids, asks = _levels(book, "bid"), _levels(book, "ask")
    bid, bid_size = _level(bids[0]) if bids else (Decimal(0), Decimal(0))
    ask, ask_size = _level(asks[0]) if asks else (Decimal(0), Decimal(0))
    # Algunas respuestas planas traen las puntas sin arreglos.
    bid = bid or _walk_numbers(book, ("bidprice", "bid", "compra")) or Decimal(0)
    ask = ask or _walk_numbers(book, ("offerprice", "askprice", "ask", "venta")) or Decimal(0)
    bid_size = bid_size or _walk_numbers(book, ("bidsize", "bidquantity")) or Decimal(0)
    ask_size = ask_size or _walk_numbers(book, ("offersize", "asksize", "offerquantity")) or Decimal(0)
    if last <= 0 and bid > 0 and ask > 0:
        last = (bid + ask) / 2
    return Quote(symbol, asset_class, settlement, last, bid, ask, bid_size, ask_size, now_iso())


def _support_schema(store):
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS observer_commands(
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
          command TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT,
          finished_at TEXT, result TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS api_health(
          component TEXT PRIMARY KEY, state TEXT NOT NULL, detail TEXT NOT NULL,
          checked_at TEXT NOT NULL, last_success_at TEXT, source TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS instrument_catalog(
          instrument_type TEXT NOT NULL, ticker TEXT NOT NULL, description TEXT,
          market TEXT, settlement TEXT, downloaded_at TEXT NOT NULL, raw_json TEXT,
          PRIMARY KEY(instrument_type,ticker));
        CREATE TABLE IF NOT EXISTS production_history(
          symbol TEXT NOT NULL, instrument_type TEXT NOT NULL,
          settlement TEXT NOT NULL, date_from TEXT NOT NULL, date_to TEXT NOT NULL,
          downloaded_at TEXT NOT NULL, row_count INTEGER NOT NULL, payload_json TEXT,
          PRIMARY KEY(symbol,instrument_type,settlement));
        CREATE TABLE IF NOT EXISTS source_sync(
          source TEXT PRIMARY KEY, status TEXT NOT NULL, last_attempt_at TEXT NOT NULL,
          last_success_at TEXT, items INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS candidate_universe(
          ticker TEXT NOT NULL, instrument_type TEXT NOT NULL, settlement TEXT NOT NULL,
          market TEXT NOT NULL, can_simulate INTEGER NOT NULL, status TEXT NOT NULL,
          detail TEXT NOT NULL, last_checked_at TEXT NOT NULL,
          PRIMARY KEY(ticker,instrument_type,market));
        """)


def _health(store, component, state, detail, source, success=False):
    checked = now_iso()
    with store.connect() as c:
        previous = c.execute(
            "SELECT last_success_at FROM api_health WHERE component=?", (component,)
        ).fetchone()
        last_success = checked if success else (previous[0] if previous else None)
        c.execute("""INSERT INTO api_health VALUES(?,?,?,?,?,?)
          ON CONFLICT(component) DO UPDATE SET state=excluded.state,
          detail=excluded.detail,checked_at=excluded.checked_at,
          last_success_at=excluded.last_success_at,source=excluded.source""",
          (component, state, str(detail)[:1000], checked, last_success, source))


def _sync_state(store, source, status, items, detail, success=False):
    attempted = now_iso()
    with store.connect() as c:
        previous = c.execute(
            "SELECT last_success_at FROM source_sync WHERE source=?", (source,)
        ).fetchone()
        last_success = attempted if success else (previous[0] if previous else None)
        c.execute("""INSERT INTO source_sync VALUES(?,?,?,?,?,?)
          ON CONFLICT(source) DO UPDATE SET status=excluded.status,
          last_attempt_at=excluded.last_attempt_at,last_success_at=excluded.last_success_at,
          items=excluded.items,detail=excluded.detail""",
          (source, status, attempted, last_success, int(items), str(detail)[:1000]))


def _public_probe(store):
    sources = (
        ("BYMA_OPEN_DATA", "https://open.bymadata.com.ar/"),
        ("BYMA_WEB", "https://www.byma.com.ar/"),
    )
    for component, url in sources:
        try:
            request = Request(url, headers={"User-Agent": "PorotaReadOnlyHealth/16.3.4"}, method="GET")
            with urlopen(request, timeout=12) as response:
                response.read(512)
                ok = 200 <= response.status < 400
                _health(store, component, "VERDE" if ok else "AMARILLO",
                        f"HTTP {response.status}; consulta pública GET sin credenciales.",
                        "sonda pública oficial", success=ok)
                _sync_state(store, component, "VERDE" if ok else "AMARILLO", 0,
                            f"HTTP {response.status}; disponibilidad web oficial.", success=ok)
        except Exception as exc:
            _health(store, component, "ROJO", f"{type(exc).__name__}: {str(exc)[:180]}",
                    "sonda pública oficial")
            _sync_state(store, component, "ROJO", 0,
                        f"{type(exc).__name__}: {str(exc)[:180]}")
    _health(store, "BYMA_INSTRUMENTS_API", "GRIS",
            "La API oficial de instrumentos requiere alta/acceso de BYMA; no se usan endpoints no documentados.",
            "catálogo oficial BYMA")


def _claim_command(store):
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT id,command FROM observer_commands WHERE status='QUEUED' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            return None
        c.execute("UPDATE observer_commands SET status='RUNNING',started_at=? WHERE id=?",
                  (now_iso(), row[0]))
        return int(row[0]), str(row[1])


def _finish_command(store, command_id, status, result):
    with store.connect() as c:
        c.execute("UPDATE observer_commands SET status=?,finished_at=?,result=? WHERE id=?",
                  (status, now_iso(), str(result)[:2000], command_id))


def _catalog_records(value):
    found = []
    def walk(node):
        if isinstance(node, dict):
            ticker = node.get("ticker") or node.get("symbol") or node.get("Ticker")
            if ticker:
                found.append(node)
            else:
                for child in node.values():
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(value)
    return found


def _download_catalog(reader, store):
    """Valida candidatos concretos. Nunca confunde catálogo con login."""
    total = failures = available = rofex_available = 0
    downloaded = now_iso()
    with store.connect() as c:
        c.execute("DELETE FROM instrument_catalog")
    for ticker_query, instrument_type, fallback_settlement, market_query, can_simulate in _candidate_universe():
        status, detail = "UNAVAILABLE", "La búsqueda no devolvió coincidencias."
        try:
            payload = reader.search_instruments(
                ticker_query, instrument_type, name=ticker_query, market=market_query)
            records = _catalog_records(payload)
            if records:
                status, detail, available = "AVAILABLE", f"{len(records)} coincidencia(s).", available + 1
            with store.connect() as c:
                for row in records:
                    ticker = str(row.get("ticker") or row.get("symbol") or row.get("Ticker") or "").strip()
                    if not ticker:
                        continue
                    description = row.get("description") or row.get("name") or row.get("Descripcion")
                    market = row.get("market") or row.get("mercado") or market_query
                    settlement = row.get("settlement") or row.get("plazo") or fallback_settlement
                    c.execute("INSERT OR REPLACE INTO instrument_catalog VALUES(?,?,?,?,?,?,?)",
                              (instrument_type, ticker, str(description or "")[:300],
                               str(market or "")[:80], str(settlement or "")[:80], downloaded,
                               json.dumps(row, ensure_ascii=False, default=str)[:8000]))
                    total += 1
            if market_query == "ROFEX" and records:
                rofex_available += len(records)
        except Exception as exc:
            failures += 1
            status = "ERROR"
            detail = f"{type(exc).__name__}: {str(exc)[:260]}"
        with store.connect() as c:
            c.execute("INSERT OR REPLACE INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)",
                      (ticker_query, instrument_type, fallback_settlement, market_query,
                       int(can_simulate), status, detail, downloaded))
    state = "VERDE" if available and not failures else "AMARILLO" if available else "ROJO"
    detail = (f"{available} candidatos validados; {total} instrumentos devueltos; "
              f"{failures} búsquedas fallidas. Ticker y Name siempre no vacíos.")
    _sync_state(store, "PPI_PRODUCTION_CATALOG", state, total, detail,
                success=bool(available))
    _health(store, "PPI_PRODUCTION_CATALOG", state, detail, "PPI Producción",
            success=bool(available))
    rofex_state = "AMARILLO" if rofex_available else "GRIS"
    rofex_detail = (f"{rofex_available} contrato(s) visible(s); contexto únicamente. "
                    "No se simulan futuros sin multiplicador y margen atribuible."
                    if rofex_available else
                    "Sin contrato ROFEX validado. Contexto desactivado; no afecta contado.")
    _health(store, "ROFEX_MARKETDATA", rofex_state, rofex_detail,
            "PPI Producción / ROFEX", success=bool(rofex_available))
    return total


def _active_symbols(store):
    """Universo seguro: núcleo más candidatos validados, con tope operativo."""
    core = list(CORE_SYMBOLS)
    try:
        with store.connect() as c:
            rows = c.execute("""SELECT ticker,instrument_type,settlement FROM candidate_universe
              WHERE can_simulate=1 AND status='AVAILABLE'
              ORDER BY CASE WHEN ticker IN ('GGAL','AL30','AAPL') THEN 0 ELSE 1 END,
              instrument_type,ticker""").fetchall()
        seen = {value[0] for value in core}
        for ticker, kind, settlement in rows:
            if ticker not in seen:
                core.append((ticker, kind, settlement))
                seen.add(ticker)
    except Exception:
        pass
    return tuple(core[:ACTIVE_SYMBOL_LIMIT])


def _history_count(value):
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return max([_history_count(v) for v in value.values()] or [0])
    return 0


def _download_histories(reader, store):
    start, end = date.today() - timedelta(days=365), date.today()
    total = successes = failures = 0
    symbols = _active_symbols(store)
    for symbol, instrument_type, settlement in symbols:
        try:
            payload = reader.history(symbol, instrument_type, settlement, start, end)
            count = _history_count(payload)
            with store.connect() as c:
                c.execute("INSERT OR REPLACE INTO production_history VALUES(?,?,?,?,?,?,?,?)",
                          (symbol, instrument_type, settlement, start.isoformat(), end.isoformat(),
                           now_iso(), count, json.dumps(payload, ensure_ascii=False, default=str)))
            total += count
            successes += 1
        except Exception as exc:
            failures += 1
            store.event("HISTORY_ERROR", f"{symbol}: {type(exc).__name__}: {str(exc)[:180]}")
    state = "VERDE" if successes and not failures else "AMARILLO" if successes else "ROJO"
    detail = (f"Históricos de {successes}/{len(symbols)} instrumentos; {total} filas; "
              f"{failures} fallidos. Solo lectura.")
    _sync_state(store, "PPI_PRODUCTION_HISTORY", state, total, detail,
                success=bool(successes))
    _health(store, "PPI_PRODUCTION_HISTORY", state, detail, "PPI Producción",
            success=bool(successes))
    return total


def _daily_sync_needed(store):
    """Una bajada diaria; reiniciar el proceso no multiplica consultas."""
    with store.connect() as connection:
        rows = connection.execute("""SELECT source,last_success_at FROM source_sync
          WHERE source IN ('PPI_PRODUCTION_CATALOG','PPI_PRODUCTION_HISTORY')""").fetchall()
    success = {row[0]: str(row[1] or "")[:10] for row in rows}
    today = datetime.now(TZ).date().isoformat()
    return any(success.get(source) != today for source in
               ("PPI_PRODUCTION_CATALOG", "PPI_PRODUCTION_HISTORY"))


def _daily_sync(reader, store):
    if not _daily_sync_needed(store):
        return
    try:
        catalog = _download_catalog(reader, store)
        histories = _download_histories(reader, store)
        store.event("DAILY_READONLY_SYNC",
                    f"{catalog} instrumentos; {histories} filas históricas.")
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:500]}"
        _sync_state(store, "PPI_PRODUCTION_DAILY_SYNC", "ROJO", 0, detail)
        store.event("DAILY_READONLY_SYNC_ERROR", detail)


def _run_command(store, reader, command):
    command_id, name = command
    if name != "LOGIN_AND_SYNC":
        _finish_command(store, command_id, "ERROR", "Comando no permitido.")
        return reader
    try:
        if reader is None:
            key, secret = _secret()
            reader = ProductionMarketReader(key, secret, audit=store.audit_http)
            reader.login_once()
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:500]}"
        _health(store, "PPI_PRODUCTION_AUTH", "ROJO", detail, "PPI Producción")
        _finish_command(store, command_id, "ERROR", detail)
        store.state(ppi_auth="ERROR", detail=f"Prueba manual falló: {detail}")
        if reader:
            reader.close()
        return None
    _health(store, "PPI_PRODUCTION_AUTH", "VERDE", "Login de solo lectura correcto.",
            "PPI Producción", success=True)
    store.state(ppi_auth="OK", detail="Login correcto; sincronizando configuración, catálogo e históricos.")
    problems = []
    try:
        catalog = _download_catalog(reader, store)
    except Exception as exc:
        catalog = 0
        problems.append(f"catálogo: {type(exc).__name__}")
        _sync_state(store, "PPI_PRODUCTION_CATALOG", "ROJO", 0, str(exc)[:500])
    try:
        histories = _download_histories(reader, store)
    except Exception as exc:
        histories = 0
        problems.append(f"históricos: {type(exc).__name__}")
        _sync_state(store, "PPI_PRODUCTION_HISTORY", "ROJO", 0, str(exc)[:500])
    suffix = ("; advertencias: " + ", ".join(problems)) if problems else ""
    result = (f"Login correcto; {catalog} instrumentos y {histories} filas históricas "
              f"descargadas{suffix}. Fuera de rueda no se ejecutó la estrategia.")
    _finish_command(store, command_id, "PARTIAL" if problems else "OK", result)
    store.state(detail=result)
    return reader


def run():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    store = PaperStore(DB_PATH)
    _support_schema(store)
    broker = PaperBroker(store,
                         initial_cash=os.getenv("PAPER_INITIAL_CAPITAL_ARS", "1000000"),
                         risk_pct=os.getenv("PAPER_RISK_PER_TRADE", "0.005"),
                         max_positions=os.getenv("PAPER_MAX_OPEN_POSITIONS", "3"),
                         max_position_pct=os.getenv("PAPER_MAX_POSITION_PCT", "0.25"),
                         max_total_exposure_pct=os.getenv("PAPER_MAX_TOTAL_EXPOSURE_PCT", "0.60"))
    store.state(process_state="STARTING", session_state="CHECKING",
                ppi_auth="NOT_ATTEMPTED", real_orders_sent=0,
                detail="Simulacion productiva inicializando.")
    if _market_phase() == "CLOSED":
        store.state(process_state="WAITING_MARKET", session_state="MARKET_CLOSED",
                    ppi_auth="NOT_ATTEMPTED", heartbeat_at=now_iso(),
                    detail="Proceso disponible y en espera; estrategia detenida por mercado cerrado.")
    reader = None
    quotes = {}
    last_public_check = 0.0
    next_login_at = 0.0
    try:
        while not STOP:
            if time.time() - last_public_check >= PUBLIC_CHECK_SECONDS:
                _public_probe(store)
                last_public_check = time.time()
            command = _claim_command(store)
            if command:
                reader = _run_command(store, reader, command)
            phase = _market_phase()
            if phase == "CLOSED":
                store.state(process_state="WAITING_MARKET", session_state="MARKET_CLOSED",
                            heartbeat_at=now_iso(), real_orders_sent=0,
                            detail="Proceso disponible; estrategia y simulación en espera por mercado cerrado.")
                time.sleep(COMMAND_POLL_SECONDS)
                continue
            if phase == "PREOPEN":
                store.state(process_state="READY_PREOPEN", session_state="PREOPEN",
                            heartbeat_at=now_iso(), real_orders_sent=0,
                            detail="Preapertura: sólo login y sincronización; cero evaluaciones y cero operaciones.")
            if reader is None:
                if time.time() < next_login_at:
                    store.state(process_state="DEGRADED", ppi_auth="COOLDOWN",
                                heartbeat_at=now_iso(), real_orders_sent=0,
                                detail="PPI en espera de reintento; no se abre una tormenta de logins.")
                    time.sleep(COMMAND_POLL_SECONDS)
                    continue
                key, secret = _secret()
                reader = ProductionMarketReader(key, secret, audit=store.audit_http)
                try:
                    reader.login_once()
                    _health(store, "PPI_PRODUCTION_AUTH", "VERDE",
                            "Login automático de solo lectura correcto.",
                            "PPI Producción", success=True)
                    store.state(ppi_auth="OK", session_state=phase,
                                detail="Login de solo lectura correcto.")
                    _daily_sync(reader, store)
                except Exception as exc:
                    _health(store, "PPI_PRODUCTION_AUTH", "ROJO",
                            f"{type(exc).__name__}: {str(exc)[:300]}", "PPI Producción")
                    store.state(process_state="DEGRADED", ppi_auth="ERROR",
                                heartbeat_at=now_iso(), detail=f"Login fallo: {type(exc).__name__}")
                    reader.close()
                    reader = None
                    next_login_at = time.time() + LOGIN_COOLDOWN_SECONDS
                    time.sleep(COMMAND_POLL_SECONDS)
                    continue
            if phase != "OPEN":
                time.sleep(COMMAND_POLL_SECONDS)
                continue
            symbols = _active_symbols(store)
            cycle_ok = 0
            for symbol, asset_class, settlement in symbols:
                if STOP:
                    break
                try:
                    current = reader.current(symbol, asset_class, settlement)
                    book = reader.book(symbol, asset_class, settlement)
                    q = normalize_quote(symbol, asset_class, settlement, current, book)
                    if q.last <= 0:
                        store.event("DATA_REJECTED", f"{symbol}: cotizacion sin precio util")
                        continue
                    store.add_quote(q)
                    quotes[symbol] = q
                    broker.on_quote(q)
                    cycle_ok += 1
                    store.state(last_market_data_at=q.observed_at)
                except ReadOnlyPolicyViolation as exc:
                    store.event("HTTP_BLOCKED", str(exc))
                    store.state(process_state="DEGRADED", detail="La barrera bloqueo una ruta no permitida.")
                except Exception as exc:
                    store.event("DATA_ERROR", f"{symbol}: {type(exc).__name__}: {str(exc)[:180]}")
            broker.mark_equity(quotes)
            metrics = reader.metrics
            store.state(process_state="RUNNING" if cycle_ok else "DEGRADED",
                        session_state="MARKET_OPEN", heartbeat_at=now_iso(),
                        http_allowed=metrics["http_allowed"],
                        http_blocked=metrics["http_blocked"], real_orders_sent=0,
                        detail=f"{cycle_ok}/{len(symbols)} instrumentos actualizados; operaciones solo simuladas.")
            _health(store, "PPI_PRODUCTION_MARKETDATA",
                    "VERDE" if cycle_ok else "ROJO",
                    f"{cycle_ok}/{len(symbols)} instrumentos con cotización útil.",
                    "PPI Producción", success=bool(cycle_ok))
            time.sleep(INTERVAL)
    finally:
        if reader:
            reader.close()
        store.state(process_state="STOPPED", heartbeat_at=now_iso(), real_orders_sent=0,
                    detail="Observador detenido ordenadamente.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
