"""Observador productivo con paper trading completo y cero capacidad operativa."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import statistics
import sys
import time
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from bd_ppi_readonly_guard import ProductionMarketReader, ReadOnlyPolicyViolation
from be_paper_engine import D, PaperBroker, PaperStore, Quote, now_iso
import bi_operational_services as operations
import bu_instrument_catalog as financial_catalog
from _version import VERSION


TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
from cg_paper_workspace import database_path, runtime_store

DB_PATH = str(database_path())
SECRET_PATH = os.getenv("PPI_PRODUCTION_SECRET_FILE", "/run/secrets/ppi_production.json")
INTERVAL = max(15, int(os.getenv("PAPER_OBSERVER_INTERVAL_SECONDS", "60")))
COMMAND_POLL_SECONDS = max(3, int(os.getenv("PAPER_COMMAND_POLL_SECONDS", "5")))
PUBLIC_CHECK_SECONDS = max(900, int(os.getenv("PUBLIC_SOURCE_CHECK_SECONDS", "21600")))
LOGIN_COOLDOWN_SECONDS = max(300, int(os.getenv("PPI_LOGIN_COOLDOWN_SECONDS", "900")))
ACTIVE_SYMBOL_LIMIT = max(3, min(int(os.getenv("PAPER_ACTIVE_SYMBOL_LIMIT", "20")), 60))
PPI_CALL_BUDGET_SECONDS = max(0.1, float(os.getenv("PAPER_PPI_CALL_BUDGET_SECONDS", "2")))
SIGNAL_MIN_SAMPLES = max(3, int(os.getenv("PAPER_SIGNAL_MIN_SAMPLES", "6")))
SIGNAL_WINDOW_MINUTES = max(15, int(os.getenv("PAPER_SIGNAL_WINDOW_MINUTES", "90")))
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
# Núcleo operativo observado en todos los ciclos. La lista no habilita un
# contrato: catálogo, identidad monetaria, puntas y profundidad siguen siendo
# obligatorios. Las posiciones abiertas conservan prioridad absoluta.
FOCUS_SYMBOLS = (
    ("GGAL", "ACCIONES", "A-24HS"),
    ("YPFD", "ACCIONES", "A-24HS"),
    ("PAMP", "ACCIONES", "A-24HS"),
    ("BMA", "ACCIONES", "A-24HS"),
    ("BBAR", "ACCIONES", "A-24HS"),
    ("SUPV", "ACCIONES", "A-24HS"),
    ("CEPU", "ACCIONES", "A-24HS"),
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
    "LETRAS": ("LETRA", "S", "T"), "ON": ("YPF", "MRC", "YMC"),
    "CAUCIONES": ("CAUCION",), "FCI": ("FONDO",),
    "OPCIONES": ("GFG", "YPF", "PAM"),
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


def normalize_quote(symbol, asset_class, settlement, current, book, *, metadata=None):
    # Current documenta price/date en la raíz. Un cierre diario o un precio
    # anidado arbitrario no constituye el último negocio de este instrumento.
    last = D(current.get("price")) if isinstance(current, dict) else Decimal(0)
    bids, asks = _levels(book, "bid"), _levels(book, "ask")
    bid, bid_size = _level(bids[0]) if bids else (Decimal(0), Decimal(0))
    ask, ask_size = _level(asks[0]) if asks else (Decimal(0), Decimal(0))
    # Algunas respuestas planas traen las puntas sin arreglos.
    bid = bid or _walk_numbers(book, ("bidprice", "bid", "compra")) or Decimal(0)
    ask = ask or _walk_numbers(book, ("offerprice", "askprice", "ask", "venta")) or Decimal(0)
    bid_size = bid_size or _walk_numbers(book, ("bidsize", "bidquantity")) or Decimal(0)
    ask_size = ask_size or _walk_numbers(book, ("offersize", "asksize", "offerquantity")) or Decimal(0)
    return Quote(symbol, asset_class, settlement, last, bid, ask, bid_size, ask_size, now_iso(),
                 book_at=book.get("date") if isinstance(book, dict) else None,
                 trade_at=current.get("date") if isinstance(current, dict) else None,
                 last_kind="TRADE" if last > 0 else "UNAVAILABLE",
                 **financial_catalog.quote_terms(metadata))


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
        CREATE TABLE IF NOT EXISTS production_history_attempts(
          symbol TEXT NOT NULL, instrument_type TEXT NOT NULL, settlement TEXT NOT NULL,
          attempted_at TEXT NOT NULL, state TEXT NOT NULL, valid_rows INTEGER NOT NULL,
          detail TEXT NOT NULL, PRIMARY KEY(symbol,instrument_type,settlement));
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
    financial_catalog.init_schema(store)


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
    """Publica un lote atómico; conserva registros previos como STALE.

    Los filtros de búsqueda y los instrumentos devueltos son entidades
    diferentes. No se inventa una especie por haber consultado un prefijo.
    """
    failures = available = 0
    downloaded = now_iso()
    run_id = uuid.uuid4().hex
    found, query_results = {}, []
    configuration = None
    if hasattr(reader, "market_configuration"):
        try:
            configuration = financial_catalog.validate_configuration(reader.market_configuration())
        except Exception as exc:
            store.event("CATALOG_CONFIGURATION_UNAVAILABLE", type(exc).__name__)
    for ticker_query, instrument_type, fallback_settlement, market_query, _ in _candidate_universe():
        status, detail = "EMPTY_FILTER_RESULT", "Este filtro no devolvió coincidencias; no prueba indisponibilidad."
        count = 0
        name_query = {("OPCIONES", "GFG"): "GALICIA", ("OPCIONES", "YPF"): "YPF",
                      ("OPCIONES", "PAM"): "PAMPA", ("LETRAS", "S"): "LETRA",
                      ("LETRAS", "T"): "LETRA", ("ON", "MRC"): "MASTELLONE",
                      ("ON", "YMC"): "YPF"}.get((instrument_type, ticker_query), ticker_query)
        if configuration is not None:
            missing = ('TYPE' if instrument_type not in configuration['instrument_types'] else
                       'MARKET' if market_query not in configuration['markets'] else None)
            if missing:
                query_results.append((run_id, ticker_query, name_query, instrument_type,
                    market_query, missing + '_NOT_ENUMERATED', 0, 'No se consultó: ausente de la configuración actual.'))
                continue
        try:
            payload = reader.search_instruments(
                ticker_query, instrument_type, name=name_query, market=market_query)
            if not isinstance(payload, list) or any(not isinstance(r, dict) for r in payload):
                raise ValueError('PPI_SEARCH_INVALID_SHAPE')
            records = payload  # contrato público: lista directa; no extraer de errores anidados
            for raw in records:
                try:
                    record = financial_catalog.normalize_record(raw, fallback_settlement, downloaded, run_id)
                except (ValueError, TypeError):
                    continue
                if configuration is not None:
                    if record['instrument_type'] not in configuration['instrument_types']:
                        record['capability'] = 'TYPE_NOT_ENUMERATED'
                    elif record['market'] not in configuration['markets']:
                        record['capability'] = 'MARKET_NOT_ENUMERATED'
                key = tuple(record[k] for k in ("ticker", "instrument_type", "market", "currency", "settlement"))
                found[key] = record
                count += 1
            if count:
                status, detail, available = "AVAILABLE", f"{count} coincidencia(s).", available + 1
            elif records:
                status, detail = "INVALID_METADATA", "Respuestas sin ticker/clase verificables."
        except Exception as exc:
            failures += 1
            status = "ERROR"
            detail = f"{type(exc).__name__}: {str(exc)[:260]}"
        query_results.append((run_id, ticker_query, name_query, instrument_type, market_query, status, count, detail))
        if CATALOG_QUERY_SLEEP_SECONDS:
            time.sleep(CATALOG_QUERY_SLEEP_SECONDS)
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        if configuration is not None:
            for name, payload in configuration.items():
                c.execute("INSERT OR REPLACE INTO broker_market_configuration VALUES(?,?,?)",
                          (name, downloaded, json.dumps(payload, ensure_ascii=False)))
        c.execute("UPDATE financial_instrument_catalog SET status='STALE'")
        c.execute("UPDATE candidate_universe SET status='STALE',can_simulate=0")
        for record in found.values():
            financial_catalog.persist(c, record)
            c.execute("INSERT OR REPLACE INTO instrument_catalog VALUES(?,?,?,?,?,?,?)",
                      (record["instrument_type"], record["ticker"], record["description"],
                       record["market"], record["settlement"], downloaded,
                       json.dumps(record["raw"], ensure_ascii=False, default=str)))
            c.execute("INSERT OR REPLACE INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)",
                      (record["ticker"], record["instrument_type"], record["settlement"], record["market"],
                       int(record["capability"] == "READY_PAPER_SPOT"), "AVAILABLE", record["capability"], downloaded))
        c.executemany("INSERT INTO catalog_query_results VALUES(?,?,?,?,?,?,?,?)", query_results)
        financial_catalog.persist_family_coverage(c, configuration, query_results,
                                                  found.values(), run_id, downloaded)
    total = len(found)
    rofex_available = sum(r["market"] in {"ROFEX", "A3"} for r in found.values())
    state = "VERDE" if available and not failures and configuration is not None else "AMARILLO" if available else "ROJO"
    detail = (f"{available} búsquedas con coincidencias; {total} identidades únicas; "
              f"{failures} búsquedas fallidas. Ticker y Name siempre no vacíos. "
              f"Configuración actual: {'verificada' if configuration is not None else 'no disponible'}.")
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
    """Universo observable. La capacidad financiera se verifica por contrato."""
    core = list(CORE_SYMBOLS)
    try:
        with store.connect() as c:
            normalized_count = c.execute("SELECT COUNT(*) FROM financial_instrument_catalog").fetchone()[0]
            if normalized_count:
                rows = c.execute("""SELECT DISTINCT ticker,instrument_type,settlement FROM financial_instrument_catalog
                  WHERE status='AVAILABLE' ORDER BY instrument_type,ticker,settlement""").fetchall()
            else:
                rows = c.execute("""SELECT ticker,instrument_type,settlement FROM candidate_universe
              WHERE can_simulate=1 AND status='AVAILABLE'
              ORDER BY CASE WHEN ticker IN ('GGAL','AL30','AAPL') THEN 0 ELSE 1 END,
              instrument_type,ticker""").fetchall()
        seen = set(core)
        groups = {kind: [] for kind in ("ACCIONES", "CEDEARS", "BONOS", "ETF", "ETFS", "LETRAS", "ON", "OBLIGACIONES",
                                        "OPCIONES", "FUTUROS", "CAUCIONES", "FCI")}
        for ticker, kind, settlement in rows:
            if (ticker, kind, settlement) not in seen and kind in groups:
                groups[kind].append((ticker, kind, settlement))
        while any(groups.values()):
            for kind in groups:
                if groups[kind]:
                    value = groups[kind].pop(0)
                    if value not in seen:
                        core.append(value)
                        seen.add(value)
    except Exception:
        pass
    return tuple(core)


def _active_symbols(store):
    """Compatibilidad: devuelve el primer lote, no el universo histórico."""
    return _eligible_symbols(store)[:ACTIVE_SYMBOL_LIMIT]


def _cycle_symbols(store):
    """Abiertas y foco primero; el resto del universo mantiene rotación."""
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
    available = set(universe)
    focus_budget = min(len(FOCUS_SYMBOLS), max(1, ACTIVE_SYMBOL_LIMIT // 2))
    focus_added = 0
    for candidate in FOCUS_SYMBOLS:
        if len(selected) >= max(ACTIVE_SYMBOL_LIMIT, len(opened)):
            break
        if focus_added >= focus_budget:
            break
        if candidate in available and candidate not in seen:
            selected.append(candidate)
            seen.add(candidate)
            focus_added += 1
    visited = 0
    while len(selected) < ACTIVE_SYMBOL_LIMIT and visited < len(universe):
        candidate = universe[(cursor + visited) % len(universe)]
        visited += 1
        if candidate not in seen:
            selected.append(candidate)
            seen.add(candidate)
    return tuple(selected), len(universe), cursor, (cursor + visited) % len(universe)


def _sampling_feasibility(selected_count):
    """Cota conservadora: pausa más dos lecturas PPI por instrumento."""
    cycle_seconds = INTERVAL + max(0, selected_count) * 2 * PPI_CALL_BUDGET_SECONDS
    capacity = int(SIGNAL_WINDOW_MINUTES * 60 / max(cycle_seconds, 1))
    return {
        "feasible": capacity >= SIGNAL_MIN_SAMPLES,
        "estimated_cycle_seconds": round(cycle_seconds, 2),
        "estimated_samples_per_window": capacity,
        "required_samples": SIGNAL_MIN_SAMPLES,
        "window_minutes": SIGNAL_WINDOW_MINUTES,
    }


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


def _history_count(value, *, as_of=None, date_from=None, date_to=None):
    # El contrato documentado es una lista de OHLC/volumen. Una lista de
    # mensajes de error o claves anidadas no equivale a una serie financiera.
    from bs_instrument_contracts import aware_datetime, decimal_value
    at=aware_datetime(as_of or now_iso())
    seen=set()
    valid = 0
    for row in value if isinstance(value,list) else []:
        try:
            if not isinstance(row,dict):
                continue
            source_at=aware_datetime(row.get('date'))
            if source_at>at or source_at in seen:
                continue
            source_day=source_at.astimezone(TZ).date()
            if (date_from and source_day<date_from) or (date_to and source_day>date_to):
                continue
            prices={k:decimal_value(row.get(k),k,positive=True) for k in ('openingPrice','max','min','price')}
            decimal_value(row.get('volume'),'volume',nonnegative=True)
            if prices['min']<=min(prices['openingPrice'],prices['price'])<=max(prices['openingPrice'],prices['price'])<=prices['max']:
                valid+=1
                seen.add(source_at)
        except (ValueError,TypeError):
            continue
    return valid


def _historical_targets(store):
    """Todo el universo validado; índices se conservan como benchmark."""
    try:
        with store.connect() as c:
            rows = c.execute("""SELECT u.ticker,u.instrument_type,u.settlement,
              h.downloaded_at FROM candidate_universe u
              LEFT JOIN production_history h ON h.symbol=u.ticker
                AND h.instrument_type=u.instrument_type AND h.settlement=u.settlement
              LEFT JOIN production_history_attempts a ON a.symbol=u.ticker
                AND a.instrument_type=u.instrument_type AND a.settlement=u.settlement
              WHERE u.status='AVAILABLE' AND (u.can_simulate=1 OR u.instrument_type='INDICES')
              ORDER BY COALESCE(a.attempted_at,h.downloaded_at) IS NOT NULL,
                COALESCE(a.attempted_at,h.downloaded_at),u.instrument_type,u.ticker""").fetchall()
        if rows:
            return [(r[0], r[1], r[2]) for r in rows]
    except Exception:
        pass
    return list(CORE_SYMBOLS)


def _download_histories(reader, store):
    end = datetime.now(TZ).date()
    start = end - timedelta(days=365)
    total = successes = failures = 0
    all_symbols = _historical_targets(store)
    symbols = all_symbols[:HISTORY_BATCH_LIMIT]
    for symbol, instrument_type, settlement in symbols:
        attempted = now_iso()
        try:
            payload = reader.history(symbol, instrument_type, settlement, start, end)
            attempted = now_iso()
            count = _history_count(payload,as_of=attempted,date_from=start,date_to=end)
            from bl_candle_engine import archive_raw, canonical
            metadata = financial_catalog.lookup(store,symbol,instrument_type,settlement)
            expected = len(payload) if isinstance(payload,list) else 0
            status = 'VALID_PAYLOAD' if count and count==expected else 'PARTIAL' if count else 'EMPTY_OR_INVALID'
            with store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                archive_raw(c,origin='PPI_HISTORY',row_key=canonical([symbol,instrument_type,settlement,attempted]),
                    payload={'symbol':symbol,'asset_class':instrument_type,'settlement':settlement,
                        'date_from':start.isoformat(),'date_to':end.isoformat(),'metadata':metadata,
                        'valid_rows':count,'payload_json':json.dumps(payload,ensure_ascii=False,default=str)},
                    recorded_at=attempted,quality=status)
                c.execute('INSERT OR REPLACE INTO production_history_attempts VALUES(?,?,?,?,?,?,?)',
                          (symbol,instrument_type,settlement,attempted,status,count,
                           'Sólo estructura OHLC; ajuste, unidad de volumen y publicación pendientes de confirmar'))
                # Vacío o parcial NO reemplaza la última descarga completa.
                if status=='VALID_PAYLOAD':
                    c.execute("INSERT OR REPLACE INTO production_history VALUES(?,?,?,?,?,?,?,?)",
                              (symbol, instrument_type, settlement, start.isoformat(), end.isoformat(),
                               attempted, count, json.dumps(payload, ensure_ascii=False, default=str)))
            total += count
            if status=='VALID_PAYLOAD':
                successes += 1
            else:
                failures += 1
        except Exception as exc:
            failures += 1
            with store.connect() as c:
                c.execute('INSERT OR REPLACE INTO production_history_attempts VALUES(?,?,?,?,?,?,?)',
                          (symbol,instrument_type,settlement,attempted,'ERROR',0,type(exc).__name__))
            store.event("HISTORY_ERROR", f"{symbol}: {type(exc).__name__}: {str(exc)[:180]}")
    state = "VERDE" if successes and not failures else "AMARILLO" if successes else "ROJO"
    with store.connect() as c:
        covered = c.execute("SELECT COUNT(*) FROM production_history WHERE row_count>0").fetchone()[0]
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
            detail = "IA intradiaria desactivada por política; no participa de decisiones PAPER."
            _finish_command(store, command_id, "NOT_APPLICABLE", detail)
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
    if gemini_gate is not None:
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
    store = runtime_store(DB_PATH)
    _support_schema(store)
    store.state(process_state="STARTING", session_state="CHECKING",
                ppi_auth="NOT_ATTEMPTED", heartbeat_at=now_iso(),
                real_orders_sent=0, detail="Inicializando servicios 24x7.")
    operations.service_tick(store, _market_phase(), force=True)
    from bv_paper_runtime import broker_from_environment
    # La IA queda disponible como módulo offline, pero no se inicializa, no se
    # consulta y no participa de ninguna decisión intradiaria.
    broker = broker_from_environment(store, ai_gate=None, require_ai=False,
                                     ai_mode="OFF", context_fn=None)
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
            # o falle el login. No depende de cotizaciones ni de IA.
            broker.settle_cauciones()
            # Estos trabajos continúan con la rueda cerrada: dashboard, SRE,
            # backups, noticias, macro, reportes y resumen Telegram son 24x7.
            operations.service_tick(store, _market_phase())
            if time.time() - last_public_check >= PUBLIC_CHECK_SECONDS:
                _public_probe(store)
                last_public_check = time.time()
            command = _claim_command(store)
            if command:
                reader = _run_command(store, reader, command, None)
            phase = _market_phase()
            # Autenticación, catálogo e históricos son útiles también durante
            # noches, fines de semana y feriados. Sólo current/book y la
            # estrategia quedan condicionados a rueda abierta.
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
            if phase == "CLOSED":
                store.state(process_state="WAITING_MARKET", session_state="MARKET_CLOSED",
                            heartbeat_at=now_iso(), real_orders_sent=0,
                            detail="PPI de solo lectura activo; ingesta histórica disponible. "
                                   "Estrategia y market data vivo detenidos por mercado cerrado.")
                time.sleep(COMMAND_POLL_SECONDS)
                continue
            if phase == "PREOPEN":
                store.state(process_state="READY_PREOPEN", session_state="PREOPEN",
                            heartbeat_at=now_iso(), real_orders_sent=0,
                            detail="Preapertura: login e ingesta activos; cero evaluaciones y cero operaciones.")
            if phase != "OPEN":
                time.sleep(COMMAND_POLL_SECONDS)
                continue
            symbols, eligible_total, cursor_before, cursor_after = _cycle_symbols(store)
            feasibility = _sampling_feasibility(len(symbols))
            if not feasibility["feasible"]:
                store.event("SAMPLING_INFEASIBLE", json.dumps(feasibility, sort_keys=True))
                _health(store, "PAPER_SIGNAL_SAMPLING", "ROJO",
                        "Configuración incapaz de reunir la ventana mínima: " +
                        json.dumps(feasibility, sort_keys=True), "Runtime PAPER")
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
                    metadata = financial_catalog.lookup(store, symbol, asset_class, settlement)
                    q = normalize_quote(symbol, asset_class, settlement, current, book, metadata=metadata)
                    store.add_quote(q)
                    quotes[(symbol, asset_class, settlement, q.currency, q.market)] = q
                    broker.on_quote(q)
                    data_error = q.time_error(
                        now_iso(), require_trade=True,
                        max_age_seconds=broker.quote_max_age_seconds,
                        max_trade_age_seconds=broker.trade_max_age_seconds)
                    if data_error:
                        store.event("DATA_REJECTED", f"{symbol}: {data_error}")
                        continue
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
                   f"Límite={ACTIVE_SYMBOL_LIMIT}; foco+rotación; factibilidad={json.dumps(feasibility,sort_keys=True)}"))
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
