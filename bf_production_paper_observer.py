"""Observador productivo con paper trading completo y cero capacidad operativa."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from bd_ppi_readonly_guard import ProductionMarketReader, ReadOnlyPolicyViolation
from be_paper_engine import D, PaperBroker, PaperStore, Quote, now_iso
from bh_paper_gemini import GeminiPaperGate
import bi_operational_services as operations


VERSION = "16.3.5"
TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
DB_PATH = os.getenv("PAPER_DB_PATH", "data/observer/observer_production.db")
SECRET_PATH = os.getenv("PPI_PRODUCTION_SECRET_FILE", "/run/secrets/ppi_production.json")
INTERVAL = max(15, int(os.getenv("PAPER_OBSERVER_INTERVAL_SECONDS", "60")))
COMMAND_POLL_SECONDS = max(3, int(os.getenv("PAPER_COMMAND_POLL_SECONDS", "5")))
PUBLIC_CHECK_SECONDS = max(900, int(os.getenv("PUBLIC_SOURCE_CHECK_SECONDS", "21600")))
LOGIN_COOLDOWN_SECONDS = max(300, int(os.getenv("PPI_LOGIN_COOLDOWN_SECONDS", "900")))
ACTIVE_SYMBOL_LIMIT = max(3, min(int(os.getenv("PAPER_ACTIVE_SYMBOL_LIMIT", "20")), 60))
HISTORY_BATCH_LIMIT = max(5, min(int(os.getenv("PPI_HISTORY_BATCH_LIMIT", "40")), 100))
CATALOG_QUERY_SLEEP_SECONDS = max(0.0, float(os.getenv("PPI_CATALOG_QUERY_SLEEP_SECONDS", "0.05")))
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
# Semillas amplias; PPI sigue siendo quien confirma existencia, clase y mercado.
# La lista no habilita por si sola ningun instrumento y una coincidencia devuelta
# por el broker se persiste individualmente en el universo observable.
DISCOVERY_SEEDS = {
    "ACCIONES": ("GGAL", "YPFD", "PAMP", "BMA", "BBAR", "SUPV", "CEPU", "TXAR",
                 "ALUA", "LOMA", "COME", "EDN", "TGSU2", "TGNO4", "TRAN", "BYMA",
                 "CRES", "HARG", "IRSA", "TECO2", "VALO", "MIRG", "MOLI", "AGRO"),
    "CEDEARS": ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "SPY",
                "DIA", "QQQ", "IWM", "KO", "MCD", "WMT", "DIS", "NFLX", "AMD",
                "INTC", "AVGO", "ORCL", "IBM", "CRM", "JPM", "BAC", "V", "MA",
                "XOM", "CVX", "GOLD", "VALE", "PBR", "BABA", "MELI", "NU"),
    "BONOS": ("AL29", "AL30", "AL35", "AE38", "AL41", "GD29", "GD30", "GD35",
              "GD38", "GD41", "GD46", "BPOA7", "BPOB7", "TX26", "TX28", "TZX27"),
    "ETF": ("SPY", "DIA", "QQQ", "IWM"),
    # Filtros de búsqueda, no tickers confirmados ni autorización de operación.
    "LETRAS": ("LETRA",), "ON": ("YPF",),
    "CAUCIONES": ("CAUCION",), "FCI": ("FONDO",),
    "INDICES": ("MERVAL", "SPMERVAL"),
}
STOP = False

SAFE_PAPER_TYPES = {"ACCIONES", "CEDEARS", "BONOS", "ETF", "ETFS"}
SETTLEMENT_BY_TYPE = {
    "ACCIONES": "A-24HS", "CEDEARS": "A-24HS", "BONOS": "A-24HS",
    "ETF": "A-24HS", "ETFS": "A-24HS", "OPCIONES": "INMEDIATA",
    "LETRAS": "A-24HS", "ON": "A-24HS", "OBLIGACIONES": "A-24HS",
    "INDICES": "A-24HS", "FUTUROS": "A-24HS", "CAUCIONES": "INMEDIATA", "FCI": "INMEDIATA",
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
    """Todas las familias; descubrir no equivale a habilitar una operación."""
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
    for kind, tickers in DISCOVERY_SEEDS.items():
        for ticker in tickers:
            rows.add((ticker, kind, SETTLEMENT_BY_TYPE.get(kind, "A-24HS"),
                      "BYMA", kind in SAFE_PAPER_TYPES))
    # Contexto derivado: se valida disponibilidad, nunca entra al paper broker
    # sin multiplicador, vencimiento y margen atribuible al contrato.
    rows.add(("DLR", "FUTUROS", "A-24HS", "ROFEX", False))
    rows.add(("GGAL", "OPCIONES", "INMEDIATA", "BYMA", False))
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
    operations.init_schema(store)


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
            "Los planes oficiales de instrumentos/market data requieren alta o suscripcion, incluso los gratuitos; no se usan endpoints ocultos.",
            "catálogo oficial BYMA")
    _sync_state(store, "BYMA_CALENDAR", "VERDE", 1,
                f"Calendario local auditado: fase actual {_market_phase()}.", success=True)
    _health(store, "BYMA_CALENDAR", "VERDE",
            f"Calendario operativo auditado; fase actual {_market_phase()}.",
            "ak_byma_calendar", success=True)


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
        exact_match = False
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
                    actual_type = str(row.get("instrumentType") or row.get("type") or
                                      row.get("Tipo") or instrument_type).upper()
                    actual_can_simulate = actual_type in SAFE_PAPER_TYPES and str(market).upper() == "BYMA"
                    c.execute("INSERT OR REPLACE INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)",
                              (ticker.upper(), actual_type, str(settlement or fallback_settlement),
                               str(market or market_query), int(actual_can_simulate), "AVAILABLE",
                               f"Descubierto por PPI a partir de {ticker_query}.", downloaded))
                    total += 1
                    exact_match |= (ticker.upper(), actual_type, str(settlement)) == (ticker_query.upper(), instrument_type, fallback_settlement)
            if market_query == "ROFEX" and records:
                rofex_available += len(records)
        except Exception as exc:
            failures += 1
            status = "ERROR"
            detail = f"{type(exc).__name__}: {str(exc)[:260]}"
        if not exact_match:
            with store.connect() as c:
                c.execute("INSERT OR REPLACE INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)",
                          (ticker_query, instrument_type, fallback_settlement, market_query,
                           0, "QUERY_ONLY" if status == "AVAILABLE" else status, detail, downloaded))
        if CATALOG_QUERY_SLEEP_SECONDS:
            time.sleep(CATALOG_QUERY_SLEEP_SECONDS)
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


def _eligible_symbols(store):
    """Universo completo elegible, balanceado por clase y sin duplicados."""
    core = list(CORE_SYMBOLS)
    try:
        with store.connect() as c:
            rows = c.execute("""SELECT ticker,instrument_type,settlement FROM candidate_universe
              WHERE can_simulate=1 AND status='AVAILABLE'
              ORDER BY CASE WHEN ticker IN ('GGAL','AL30','AAPL') THEN 0 ELSE 1 END,
              instrument_type,ticker""").fetchall()
        seen = {value[0] for value in core}
        groups = {kind: [] for kind in ("ACCIONES", "CEDEARS", "BONOS", "ETF", "ETFS")}
        for ticker, kind, settlement in rows:
            if ticker not in seen and kind in groups:
                groups[kind].append((ticker, kind, settlement))
        while any(groups.values()):
            for kind in groups:
                if groups[kind]:
                    value = groups[kind].pop(0)
                    if value[0] not in seen:
                        core.append(value)
                        seen.add(value[0])
    except Exception:
        pass
    return tuple(core)


def _active_symbols(store):
    """Compatibilidad: devuelve el primer lote, no el universo histórico."""
    return _eligible_symbols(store)[:ACTIVE_SYMBOL_LIMIT]


def _cycle_symbols(store):
    """Abiertas primero, incluso fuera del catalogo; candidatos rotativos."""
    universe = list(_eligible_symbols(store))
    opened = list(dict.fromkeys((p["symbol"], p["asset_class"], p["settlement"])
                                for p in store.open_positions()))
    if not universe:
        return tuple(opened), 0, 0, 0
    with store.connect() as c:
        row = c.execute("SELECT cursor_after FROM universe_cycle_metrics ORDER BY id DESC LIMIT 1").fetchone()
    cursor = int(row[0] if row else 0) % len(universe)
    selected = list(opened)
    seen = set(opened)
    visited = 0
    while len(selected) < ACTIVE_SYMBOL_LIMIT and visited < len(universe):
        candidate = universe[(cursor + visited) % len(universe)]
        visited += 1
        if candidate not in seen:
            selected.append(candidate)
            seen.add(candidate)
    return tuple(selected), len(universe), cursor, (cursor + visited) % len(universe)


def _gemini_context(store, symbol):
    """Contexto compacto: historico PPI y fase BYMA, sin red adicional."""
    result = {"market_phase": _market_phase(), "source": "PPI_PRODUCTION_READ_ONLY"}
    try:
        with store.connect() as c:
            row = c.execute("""SELECT row_count,date_from,date_to,downloaded_at
              FROM production_history WHERE symbol=? ORDER BY downloaded_at DESC LIMIT 1""",
              (symbol,)).fetchone()
        if row:
            result["historical"] = dict(row)
    except Exception:
        pass
    return result


def _gemini_health(store, gate):
    try:
        result = gate.healthcheck()
        detail = f"Modelo {result['model']} disponible; contrato JSON correcto; porton critico activo."
        _health(store, "GEMINI_DECISION", "VERDE", detail, "Google Gemini", success=True)
        return True, detail
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:500]}. Porton cerrado: no se abren posiciones paper."
        _health(store, "GEMINI_DECISION", "ROJO", detail, "Google Gemini")
        return False, detail


def _history_count(value):
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return max([_history_count(v) for v in value.values()] or [0])
    return 0


def _historical_targets(store):
    """Todo el universo validado; índices se conservan como benchmark."""
    try:
        with store.connect() as c:
            rows = c.execute("""SELECT u.ticker,u.instrument_type,u.settlement,
              h.downloaded_at FROM candidate_universe u
              LEFT JOIN production_history h ON h.symbol=u.ticker
                AND h.instrument_type=u.instrument_type AND h.settlement=u.settlement
              WHERE u.status='AVAILABLE' AND (u.can_simulate=1 OR u.instrument_type='INDICES')
              ORDER BY h.downloaded_at IS NOT NULL,h.downloaded_at,u.instrument_type,u.ticker""").fetchall()
        if rows:
            return [(r[0], r[1], r[2]) for r in rows]
    except Exception:
        pass
    return list(CORE_SYMBOLS)


def _download_histories(reader, store):
    start, end = date.today() - timedelta(days=365), date.today()
    total = successes = failures = 0
    all_symbols = _historical_targets(store)
    symbols = all_symbols[:HISTORY_BATCH_LIMIT]
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
    with store.connect() as c:
        covered = c.execute("SELECT COUNT(*) FROM production_history").fetchone()[0]
    detail = (f"Lote histórico {successes}/{len(symbols)}; cobertura acumulada "
              f"{covered}/{len(all_symbols)} instrumentos; {total} filas en este lote; "
              f"{failures} fallidos. La descarga completa es incremental para no saturar PPI.")
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


def _run_command(store, reader, command, gemini_gate=None):
    command_id, name = command
    if name == "GEMINI_PREFLIGHT":
        if gemini_gate is None:
            detail = "Gemini no configurado. Porton cerrado."
            _finish_command(store, command_id, "ERROR", detail)
            return reader
        ok, detail = _gemini_health(store, gemini_gate)
        _finish_command(store, command_id, "OK" if ok else "ERROR", detail)
        return reader
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
    if gemini_gate is None:
        problems.append("Gemini: no configurado")
    else:
        gemini_ok, gemini_detail = _gemini_health(store, gemini_gate)
        if not gemini_ok:
            problems.append("Gemini: " + gemini_detail[:120])
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
    store.state(process_state="STARTING", session_state="CHECKING",
                ppi_auth="NOT_ATTEMPTED", heartbeat_at=now_iso(),
                real_orders_sent=0, detail="Inicializando servicios 24x7.")
    operations.service_tick(store, _market_phase(), force=True)
    gemini_gate = None
    try:
        gemini_gate = GeminiPaperGate()
        _gemini_health(store, gemini_gate)
    except Exception as exc:
        _health(store, "GEMINI_DECISION", "ROJO",
                f"{type(exc).__name__}: {str(exc)[:500]}. Porton cerrado.", "Google Gemini")
    broker = PaperBroker(store,
                         initial_cash=os.getenv("PAPER_INITIAL_CAPITAL_ARS", "1000000"),
                         initial_cash_usd=os.getenv("PAPER_INITIAL_CAPITAL_USD", "0"),
                         risk_pct=os.getenv("PAPER_RISK_PER_TRADE", "0.005"),
                         max_positions=os.getenv("PAPER_MAX_OPEN_POSITIONS", "3"),
                         max_position_pct=os.getenv("PAPER_MAX_POSITION_PCT", "0.25"),
                         max_total_exposure_pct=os.getenv("PAPER_MAX_TOTAL_EXPOSURE_PCT", "0.60"),
                         ai_gate=gemini_gate, require_ai=True,
                         context_fn=lambda symbol: _gemini_context(store, symbol))
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
            # Una caución vence por contrato, aunque el mercado esté cerrado
            # o falle el login. No depende de cotizaciones ni de Gemini.
            broker.settle_cauciones()
            # Estos trabajos continúan con la rueda cerrada: dashboard, SRE,
            # backups, noticias, macro, reportes y resumen Telegram son 24x7.
            operations.service_tick(store, _market_phase())
            if time.time() - last_public_check >= PUBLIC_CHECK_SECONDS:
                _public_probe(store)
                last_public_check = time.time()
            command = _claim_command(store)
            if command:
                reader = _run_command(store, reader, command, gemini_gate)
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
            symbols, eligible_total, cursor_before, cursor_after = _cycle_symbols(store)
            cycle_started = now_iso()
            cycle_clock = time.perf_counter()
            cycle_ok = 0
            cycle_failures = 0
            latencies = []
            for symbol, asset_class, settlement in symbols:
                if STOP:
                    break
                symbol_clock = time.perf_counter()
                try:
                    current = reader.current(symbol, asset_class, settlement)
                    book = reader.book(symbol, asset_class, settlement)
                    q = normalize_quote(symbol, asset_class, settlement, current, book)
                    if q.last <= 0:
                        store.event("DATA_REJECTED", f"{symbol}: cotizacion sin precio util")
                        continue
                    store.add_quote(q)
                    quotes[(symbol, asset_class, settlement)] = q
                    broker.on_quote(q)
                    cycle_ok += 1
                    store.state(last_market_data_at=q.observed_at)
                except ReadOnlyPolicyViolation as exc:
                    store.event("HTTP_BLOCKED", str(exc))
                    store.state(process_state="DEGRADED", detail="La barrera bloqueo una ruta no permitida.")
                except Exception as exc:
                    cycle_failures += 1
                    store.event("DATA_ERROR", f"{symbol}: {type(exc).__name__}: {str(exc)[:180]}")
                finally:
                    latency = round((time.perf_counter() - symbol_clock) * 1000, 2)
                    latencies.append(latency)
                    with store.connect() as c:
                        c.execute("""INSERT INTO symbol_cycle_metrics
                          (cycle_started_at,symbol,latency_ms,state,detail) VALUES(?,?,?,?,?)""",
                          (cycle_started, symbol, latency,
                           "VERDE" if latency < INTERVAL * 500 else "AMARILLO",
                           "current + book; PPI Producción solo lectura"))
            broker.mark_equity(quotes)
            duration = round(time.perf_counter() - cycle_clock, 3)
            avg_seconds = (statistics.fmean(latencies) / 1000) if latencies else INTERVAL
            budget = max(1.0, INTERVAL * 0.75)
            recommended = max(5, min(60, int(budget / max(avg_seconds, .05))))
            if cycle_failures / max(1, len(symbols)) > .10:
                recommended = min(recommended, max(5, ACTIVE_SYMBOL_LIMIT - 5))
            with store.connect() as c:
                c.execute("""INSERT INTO universe_cycle_metrics
                  (started_at,finished_at,eligible_total,selected_count,successful_count,
                   failed_count,duration_seconds,cursor_before,cursor_after,recommended_limit,detail)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                  (cycle_started, now_iso(), eligible_total, len(symbols), cycle_ok,
                   cycle_failures, duration, cursor_before, cursor_after, recommended,
                   f"Límite configurado={ACTIVE_SYMBOL_LIMIT}; rotación activa; dos lecturas PPI por instrumento"))
                c.execute("DELETE FROM symbol_cycle_metrics WHERE id NOT IN (SELECT id FROM symbol_cycle_metrics ORDER BY id DESC LIMIT 10000)")
                c.execute("DELETE FROM universe_cycle_metrics WHERE id NOT IN (SELECT id FROM universe_cycle_metrics ORDER BY id DESC LIMIT 5000)")
            metrics = reader.metrics
            store.state(process_state="RUNNING" if cycle_ok else "DEGRADED",
                        session_state="MARKET_OPEN", heartbeat_at=now_iso(),
                        http_allowed=metrics["http_allowed"],
                        http_blocked=metrics["http_blocked"], real_orders_sent=0,
                        detail=(f"{cycle_ok}/{len(symbols)} instrumentos actualizados de "
                                f"{eligible_total} elegibles; rotación activa; operaciones solo simuladas."))
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
