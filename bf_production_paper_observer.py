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
SYMBOLS = (
    ("GGAL", "ACCIONES", "A-24HS"),
    ("AL30", "BONOS", "A-24HS"),
    ("AAPL", "CEDEARS", "A-24HS"),
)
STOP = False

CATALOG_TYPES = (
    "ACCIONES", "CEDEARS", "BONOS", "ETF", "OPCIONES", "FUTUROS",
    "CAUCIONES", "FCI",
)


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


def _market_open(now=None):
    now = now or datetime.now(TZ)
    if now.weekday() >= 5:
        return False
    minute = now.hour * 60 + now.minute
    return 10 * 60 + 30 <= minute <= 17 * 60


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
    total = 0
    downloaded = now_iso()
    for instrument_type in CATALOG_TYPES:
        payload = reader.search_instruments(instrument_type)
        records = _catalog_records(payload)
        with store.connect() as c:
            c.execute("DELETE FROM instrument_catalog WHERE instrument_type=?", (instrument_type,))
            for row in records:
                ticker = str(row.get("ticker") or row.get("symbol") or row.get("Ticker") or "").strip()
                if not ticker:
                    continue
                description = row.get("description") or row.get("name") or row.get("Descripcion")
                market = row.get("market") or row.get("mercado") or "BYMA"
                settlement = row.get("settlement") or row.get("plazo") or ""
                c.execute("INSERT OR REPLACE INTO instrument_catalog VALUES(?,?,?,?,?,?,?)",
                          (instrument_type, ticker, str(description or "")[:300],
                           str(market or "")[:80], str(settlement or "")[:80], downloaded,
                           json.dumps(row, ensure_ascii=False, default=str)[:8000]))
                total += 1
    _sync_state(store, "PPI_PRODUCTION_CATALOG", "VERDE", total,
                f"Catálogo actualizado para {len(CATALOG_TYPES)} clases.", success=True)
    return total


def _history_count(value):
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return max([_history_count(v) for v in value.values()] or [0])
    return 0


def _download_histories(reader, store):
    start, end = date.today() - timedelta(days=365), date.today()
    total = 0
    for symbol, instrument_type, settlement in SYMBOLS:
        payload = reader.history(symbol, instrument_type, settlement, start, end)
        count = _history_count(payload)
        with store.connect() as c:
            c.execute("INSERT OR REPLACE INTO production_history VALUES(?,?,?,?,?,?,?,?)",
                      (symbol, instrument_type, settlement, start.isoformat(), end.isoformat(),
                       now_iso(), count, json.dumps(payload, ensure_ascii=False, default=str)))
        total += count
    _sync_state(store, "PPI_PRODUCTION_HISTORY", "VERDE", total,
                f"Históricos de {len(SYMBOLS)} instrumentos guardados en modo solo lectura.",
                success=True)
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
        _health(store, "PPI_PRODUCTION_AUTH", "VERDE", "Login de solo lectura correcto.",
                "PPI Producción", success=True)
        store.state(ppi_auth="OK", detail="Login manual de solo lectura correcto; sincronizando datos.")
        catalog = _download_catalog(reader, store)
        histories = _download_histories(reader, store)
        result = f"Login correcto; {catalog} instrumentos y {histories} filas históricas descargadas."
        _finish_command(store, command_id, "OK", result)
        store.state(detail=result)
        return reader
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:500]}"
        _health(store, "PPI_PRODUCTION_AUTH", "ROJO", detail, "PPI Producción")
        _sync_state(store, "PPI_PRODUCTION_CATALOG", "ROJO", 0, detail)
        _finish_command(store, command_id, "ERROR", detail)
        store.state(ppi_auth="ERROR", detail=f"Prueba manual falló: {detail}")
        if reader:
            reader.close()
        return None


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
    if not _market_open():
        store.state(process_state="RUNNING", session_state="MARKET_CLOSED",
                    ppi_auth="NOT_ATTEMPTED", heartbeat_at=now_iso(),
                    detail="Mercado cerrado; no se consume un login productivo.")
    reader = None
    quotes = {}
    last_public_check = 0.0
    try:
        while not STOP:
            if time.time() - last_public_check >= PUBLIC_CHECK_SECONDS:
                _public_probe(store)
                last_public_check = time.time()
            command = _claim_command(store)
            if command:
                reader = _run_command(store, reader, command)
            if not _market_open():
                store.state(process_state="RUNNING", session_state="MARKET_CLOSED",
                            heartbeat_at=now_iso(), real_orders_sent=0,
                            detail="Mercado cerrado; simulador en espera.")
                time.sleep(COMMAND_POLL_SECONDS)
                continue
            if reader is None:
                key, secret = _secret()
                reader = ProductionMarketReader(key, secret, audit=store.audit_http)
                try:
                    reader.login_once()
                    _health(store, "PPI_PRODUCTION_AUTH", "VERDE",
                            "Login automático de solo lectura correcto.",
                            "PPI Producción", success=True)
                    store.state(ppi_auth="OK", session_state="MARKET_OPEN",
                                detail="Login de solo lectura correcto.")
                    _daily_sync(reader, store)
                except Exception as exc:
                    _health(store, "PPI_PRODUCTION_AUTH", "ROJO",
                            f"{type(exc).__name__}: {str(exc)[:300]}", "PPI Producción")
                    store.state(process_state="DEGRADED", ppi_auth="ERROR",
                                heartbeat_at=now_iso(), detail=f"Login fallo: {type(exc).__name__}")
                    return 2
            cycle_ok = 0
            for symbol, asset_class, settlement in SYMBOLS:
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
                        detail=f"{cycle_ok}/{len(SYMBOLS)} instrumentos actualizados; operaciones solo simuladas.")
            _health(store, "PPI_PRODUCTION_MARKETDATA",
                    "VERDE" if cycle_ok else "ROJO",
                    f"{cycle_ok}/{len(SYMBOLS)} instrumentos con cotización útil.",
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
