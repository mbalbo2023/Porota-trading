"""Observador productivo con paper trading completo y cero capacidad operativa."""

from __future__ import annotations

import faulthandler
import fcntl
faulthandler.enable()
# Debe instalarse antes de importar módulos internos: si uno queda esperando
# I/O local, el volcado permite identificarlo sin exponer secretos.
faulthandler.dump_traceback_later(45, repeat=True)

import json
import math
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

from bd_ppi_readonly_guard import (ProductionMarketReader, ReadOnlyPolicyViolation,
                                   retry_read, session_invalid, classify_read_error)
from be_paper_engine import D, PaperBroker, PaperStore, Quote, now_iso
import bi_operational_services as operations
import bu_instrument_catalog as financial_catalog
from _version import VERSION
from am_us_equity_calendar_rc6 import cedear_opening_gate
from rc6_source_consolidation import collect_public_sources
from rc6_multisource_discovery import ppi_query_plan, complementary_discovery, canonical_family


TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
from cg_paper_workspace import database_path, runtime_store

DB_PATH = str(database_path())
SECRET_PATH = os.getenv("PPI_PRODUCTION_SECRET_FILE", "/run/secrets/ppi_production.json")
INTERVAL = max(15, int(os.getenv("PAPER_OBSERVER_INTERVAL_SECONDS", "60")))
COMMAND_POLL_SECONDS = max(3, int(os.getenv("PAPER_COMMAND_POLL_SECONDS", "5")))
PUBLIC_CHECK_SECONDS = max(900, int(os.getenv("PUBLIC_SOURCE_CHECK_SECONDS", "21600")))
PUBLIC_STRUCTURED_CAPTURE_SECONDS = max(900, int(os.getenv("PUBLIC_STRUCTURED_CAPTURE_SECONDS", "900")))
PUBLIC_CAPTURE_PATH = Path(os.getenv("POROTA_PUBLIC_SOURCE_CAPTURE_PATH", "/opt/porota-trading/data/market/rc6_public_sources_latest.json"))
LOGIN_COOLDOWN_SECONDS = max(300, int(os.getenv("PPI_LOGIN_COOLDOWN_SECONDS", "900")))
BACKGROUND_INGEST_SECONDS = max(
    3600, int(os.getenv("PPI_BACKGROUND_INGEST_SECONDS", "21600"))
)
READINESS_CHECK_SECONDS = max(
    60, int(os.getenv("PAPER_READINESS_CHECK_SECONDS", "300"))
)
COMPLEMENTARY_RECONCILE_SECONDS = max(
    60, int(os.getenv("PAPER_COMPLEMENTARY_RECONCILE_SECONDS", "300"))
)
COMPLEMENTARY_CONTRACT_TTL_SECONDS = max(
    300, int(os.getenv("PAPER_COMPLEMENTARY_CONTRACT_TTL_SECONDS", "86400"))
)
ACTIVE_SYMBOL_LIMIT = max(3, min(int(os.getenv("PAPER_ACTIVE_SYMBOL_LIMIT", "20")), 60))
PPI_CALL_BUDGET_SECONDS = max(0.1, float(os.getenv("PAPER_PPI_CALL_BUDGET_SECONDS", "2")))
SIGNAL_MIN_SAMPLES = max(3, int(os.getenv("PAPER_SIGNAL_MIN_SAMPLES", "6")))
SIGNAL_WINDOW_MINUTES = max(15, int(os.getenv("PAPER_SIGNAL_WINDOW_MINUTES", "90")))
HISTORY_BATCH_LIMIT = max(5, min(int(os.getenv("PPI_HISTORY_BATCH_LIMIT", "40")), 100))
CATALOG_QUERY_SLEEP_SECONDS = max(0.0, float(os.getenv("PPI_CATALOG_QUERY_SLEEP_SECONDS", "0.05")))
MARKET_OPEN_HOUR = int(os.getenv("MARKET_OPEN_HOUR", "10"))
MARKET_OPEN_MINUTE = int(os.getenv("MARKET_OPEN_MINUTE", "30"))
MARKET_CLOSE_HOUR = int(os.getenv("MARKET_CLOSE_HOUR", "17"))
MARKET_CLOSE_MINUTE = int(os.getenv("MARKET_CLOSE_MINUTE", "0"))
PREOPEN_MINUTES = max(5, int(os.getenv("PAPER_PREOPEN_MINUTES", "15")))
# Universo PAPER/SHADOW ampliado. PPI sigue siendo primario; IOL y BYMA
# complementan. Las familias sin contrato completo permanecen en HOLD/SHADOW
# y nunca habilitan dinero real.
OPERATIONAL_FAMILIES = frozenset({
    "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES",
    "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
})
CORE_SYMBOLS = (
    ("GGAL", "ACCIONES", "A-24HS"),
    ("AAPL", "CEDEARS", "A-24HS"),
)
# Núcleo operativo observado en todos los ciclos. La lista no habilita un
# contrato: catálogo, identidad monetaria, puntas y profundidad siguen siendo
# obligatorios. Las posiciones abiertas conservan prioridad absoluta.
DEFAULT_FOCUS_SYMBOLS = (
    ("GGAL", "ACCIONES", "A-24HS"),
    ("YPFD", "ACCIONES", "A-24HS"),
    ("PAMP", "ACCIONES", "A-24HS"),
    ("BMA", "ACCIONES", "A-24HS"),
    ("BBAR", "ACCIONES", "A-24HS"),
    ("SUPV", "ACCIONES", "A-24HS"),
    ("CEPU", "ACCIONES", "A-24HS"),
    ("AAPL", "CEDEARS", "A-24HS"),
)


def configured_focus(raw=None):
    """Foco versionado configurable, con estructura estricta y sin duplicados."""
    raw = os.getenv("PAPER_FOCUS_SYMBOLS", "") if raw is None else raw
    if not str(raw).strip():
        return DEFAULT_FOCUS_SYMBOLS
    result = []
    for item in str(raw).split(","):
        parts = tuple(part.strip().upper() for part in item.split(":"))
        if len(parts) != 3 or not all(parts):
            raise ValueError("PAPER_FOCUS_SYMBOLS requiere SIMBOLO:CLASE:PLAZO")
        if parts not in result:
            result.append(parts)
    if not 1 <= len(result) <= 24:
        raise ValueError("PAPER_FOCUS_SYMBOLS admite entre 1 y 24 identidades")
    return tuple(result)


FOCUS_SYMBOLS = configured_focus()
FOCUS_MINIMUM_FOR_OPENINGS = max(
    1, min(int(os.getenv("PAPER_FOCUS_MINIMUM_FOR_OPENINGS", "4")), len(FOCUS_SYMBOLS))
)
# Discovery is generated dynamically in rc6_multisource_discovery.py.
# DEFAULT_FOCUS_SYMBOLS only prioritizes sampling; it never defines the universe.
DISCOVERY_SEEDS = {}
STOP = False

SAFE_PAPER_TYPES = set(OPERATIONAL_FAMILIES)
SETTLEMENT_BY_TYPE = {"ACCIONES": "A-24HS", "CEDEARS": "A-24HS"}
WATCHLIST_PATH = Path(os.getenv("INSTRUMENT_WATCHLIST_PATH", "n_instrument_watchlist.json"))  # legacy, not universe authority


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


# Calendario BYMA RC6, fuente auditada: https://www.byma.com.ar/mercado/calendario-bursatil
# Está deliberadamente embebido en el observer: el primer pulso no debe depender
# de una importación diferida ni de I/O del filesystem del contenedor.
BYMA_CALENDAR_AUDITED_YEAR = 2026
BYMA_NON_OPERATIVE_DAYS = frozenset({
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-03-23", "2026-03-24",
    "2026-04-02", "2026-04-03", "2026-05-01", "2026-05-25", "2026-06-15",
    "2026-07-09", "2026-07-10", "2026-08-17", "2026-10-12", "2026-11-06",
    "2026-11-23", "2026-12-07", "2026-12-08", "2026-12-24", "2026-12-25",
    "2026-12-31",
})


def _business_day(day):
    """Use the single audited BYMA calendar authority and fail closed."""
    try:
        import ak_byma_calendar as calendar
        return bool(calendar.es_dia_habil_operativo(day))
    except Exception:
        return False


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
    """Plan de discovery automático.

    No usa watchlist/tickers versionados como autoridad. Los prefijos se generan
    por código y rotan; PPI confirma existencia. IOL/BYMA se fusionan después
    como fuentes complementarias y nunca reemplazan un campo PPI presente.
    """
    return ppi_query_plan(day=datetime.now(TZ).date())


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
        CREATE TABLE IF NOT EXISTS complementary_contract_retry(
          ticker TEXT NOT NULL, instrument_type TEXT NOT NULL, market TEXT NOT NULL,
          currency TEXT NOT NULL, settlement TEXT NOT NULL, source TEXT NOT NULL,
          observed_at TEXT, state TEXT NOT NULL, reason TEXT NOT NULL,
          last_attempt_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(ticker,instrument_type,market,currency,settlement,source));
        CREATE TABLE IF NOT EXISTS paper_book_consumption(
          fill_id INTEGER PRIMARY KEY REFERENCES paper_fills(id),
          instrument_key TEXT NOT NULL, book_at TEXT NOT NULL,
          side TEXT NOT NULL, quantity TEXT NOT NULL, book_json TEXT NOT NULL);
        """)
    operations.init_schema(store)
    financial_catalog.init_schema(store)


def _health(store, component, state, detail, source, success=False):
    checked = now_iso()
    with store.connect() as c:
        previous = c.execute("SELECT state,last_success_at FROM api_health WHERE component=?", (component,)).fetchone()
        previous_state = previous[0] if previous else None
        previous_success = previous[1] if previous else None
        last_success = checked if success else previous_success
        c.execute("""INSERT INTO api_health VALUES(?,?,?,?,?,?)
          ON CONFLICT(component) DO UPDATE SET state=excluded.state,detail=excluded.detail,checked_at=excluded.checked_at,last_success_at=excluded.last_success_at,source=excluded.source""",
          (component,state,str(detail)[:1000],checked,last_success,source))
        try:
            from rc6_operational_alerts import enqueue_transition
            row=c.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()
            enqueue_transition(c,component=component,previous_state=previous_state,state=state,detail=detail,checked_at=checked,last_success_at=last_success,source=source,real_orders_sent=(row[0] if row else 0))
        except Exception as exc:
            c.execute("INSERT INTO paper_events(event_at,source,event_type,paper_id,detail) VALUES(?,?,?,?,?)",(checked,'OPERATIONAL_ALERT_BRIDGE','OPERATIONAL_ALERT_ERROR',None,type(exc).__name__))


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


def _public_probe(store, *, structured=False):
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
    if not structured:
        return
    try:
        result = collect_public_sources()
        PUBLIC_CAPTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = PUBLIC_CAPTURE_PATH.with_suffix(PUBLIC_CAPTURE_PATH.suffix + ".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.chmod(temporary, 0o644)
        temporary.replace(PUBLIC_CAPTURE_PATH)
        byma = next((item for item in result.get("sources", []) if item.get("source") == "BYMA"), {})
        count = int(byma.get("record_count") or 0)
        ok = byma.get("status") == "SCRAPED_PUBLIC_DATA"
        _health(store, "BYMA_STRUCTURED_CAPTURE", "VERDE" if ok else "AMARILLO",
                f"Captura pública estructurada: {count} instrumentos; SHADOW_ONLY.", "BYMA Open Data", success=ok)
        _sync_state(store, "BYMA_STRUCTURED_CAPTURE", "VERDE" if ok else "AMARILLO", count,
                    f"Captura estructurada persistida; SHADOW_ONLY.", success=ok)
    except Exception as exc:
        _health(store, "BYMA_STRUCTURED_CAPTURE", "ROJO", f"{type(exc).__name__}: {str(exc)[:180]}", "BYMA Open Data")
        _sync_state(store, "BYMA_STRUCTURED_CAPTURE", "ROJO", 0, f"{type(exc).__name__}: {str(exc)[:180]}")


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
    for ticker_query, provider_type, fallback_settlement, market_query, _, instrument_type in _candidate_universe():
        status, detail = "EMPTY_FILTER_RESULT", "Este filtro no devolvió coincidencias; no prueba indisponibilidad."
        count = 0
        name_query = ticker_query
        if configuration is not None:
            missing = ('TYPE' if provider_type not in configuration['instrument_types'] else
                       'MARKET' if market_query not in configuration['markets'] else None)
            if missing:
                query_results.append((run_id, ticker_query, name_query, instrument_type,
                    market_query, missing + '_NOT_ENUMERATED', 0, 'No se consultó: ausente de la configuración actual.'))
                continue
        try:
            payload = reader.search_instruments(
                ticker_query, provider_type, name=name_query, market=market_query)
            if not isinstance(payload, list) or any(not isinstance(r, dict) for r in payload):
                raise ValueError('PPI_SEARCH_INVALID_SHAPE')
            records = payload  # contrato público: lista directa; no extraer de errores anidados
            for raw in records:
                try:
                    record = financial_catalog.normalize_record(raw, fallback_settlement, downloaded, run_id)
                except (ValueError, TypeError):
                    continue
                if configuration is not None:
                    declared_families = {canonical_family(value) for value in configuration['instrument_types']}
                    if record['instrument_type'] not in declared_families:
                        record['capability'] = 'TYPE_NOT_ENUMERATED'
                    elif market_query not in configuration['markets']:
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
    # PPI keeps identity authority.  Fresh complementary evidence may complete
    # a missing financial contract on that exact identity; otherwise it can only
    # widen the observed SHADOW universe.
    try:
        for raw in complementary_discovery("/app/data/market"):
            try:
                record = financial_catalog.normalize_complementary_record(raw, downloaded, run_id)
            except (ValueError, TypeError):
                continue
            key = tuple(record[k] for k in ("ticker","instrument_type","market","currency","settlement"))
            fresh = financial_catalog.complementary_is_fresh(
                raw, max_age_seconds=COMPLEMENTARY_CONTRACT_TTL_SECONDS)
            if key in found and fresh:
                found[key] = financial_catalog.complete_with_complement(found[key], raw)
            elif key not in found:
                if not fresh:
                    record["status"] = "OBSERVED_SHADOW"
                found[key] = record
    except Exception as exc:
        store.event("COMPLEMENTARY_DISCOVERY_UNAVAILABLE", type(exc).__name__)

    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        if configuration is not None:
            for name, payload in configuration.items():
                c.execute("INSERT OR REPLACE INTO broker_market_configuration VALUES(?,?,?)",
                          (name, downloaded, json.dumps(payload, ensure_ascii=False)))
        c.execute("""UPDATE financial_instrument_catalog SET status='STALE'
          WHERE julianday(last_seen_at) < julianday(?, '-14 days')""", (downloaded,))
        c.execute("""UPDATE candidate_universe SET status='STALE',can_simulate=0
          WHERE julianday(last_checked_at) < julianday(?, '-14 days')""", (downloaded,))
        for record in found.values():
            discovery_source = str((record.get("raw") or {}).get("_discovery_source") or "PPI_PRIMARY")
            existing = c.execute("""SELECT metadata_json,last_seen_at FROM financial_instrument_catalog
              WHERE ticker=? AND instrument_type=? AND market=? AND currency=? AND settlement=?""",
              (record["ticker"],record["instrument_type"],record["market"],record["currency"],record["settlement"])).fetchone()
            if discovery_source != "PPI_PRIMARY" and existing:
                try:
                    prior_meta=json.loads(existing[0] or "{}")
                except (TypeError,ValueError,json.JSONDecodeError):
                    prior_meta={}
                if str(prior_meta.get("_discovery_source") or "").upper()=="PPI_PRIMARY":
                    # Complementary evidence may widen/fill elsewhere, but cannot
                    # replace a still-retained primary catalog identity.
                    continue
            financial_catalog.persist(c, record)
            c.execute("INSERT OR REPLACE INTO instrument_catalog VALUES(?,?,?,?,?,?,?)",
                      (record["instrument_type"], record["ticker"], record["description"],
                       record["market"], record["settlement"], downloaded,
                       json.dumps(record["raw"], ensure_ascii=False, default=str)))
            c.execute("INSERT OR REPLACE INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)",
                      (record["ticker"], record["instrument_type"], record["settlement"], record["market"],
                       int(record["status"] == "AVAILABLE" and
                           str(record["capability"]).startswith("READY_PAPER_")),
                       record["status"], record["capability"], downloaded))
        financial_catalog.sync_candidate_universe(c, downloaded)
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


def _reconcile_complementary_catalog(store):
    """Apply complements in strict PPI-primary -> IOL -> BYMA order.

    PPI rows remain the canonical identity whenever they exist. IOL and BYMA
    are gap-fill sources: they may add missing contract/freshness evidence but
    never overwrite explicit higher-priority identity fields.
    """
    try:
        complementary = complementary_discovery("/app/data/market")
    except Exception as exc:
        store.event("COMPLEMENTARY_RECONCILIATION_UNAVAILABLE", type(exc).__name__)
        return 0
    # An empty or temporarily unavailable complementary snapshot never deletes
    # the PPI identity or closes the retry loop.  We still inventory every
    # incomplete contract below, so the next periodic reconciliation can retry.
    checked = now_iso()
    promoted = inserted = refreshed = 0
    retry_pending = 0
    applied_by_source = {}

    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for raw in complementary:
            if not financial_catalog.complementary_is_fresh(
                    raw, max_age_seconds=COMPLEMENTARY_CONTRACT_TTL_SECONDS):
                continue

            ticker = str(raw.get("ticker") or raw.get("symbol") or "").strip().upper()
            if not ticker:
                continue
            candidates = connection.execute(
                "SELECT * FROM financial_instrument_catalog WHERE ticker=?",
                (ticker,)).fetchall()
            matches = [
                dict(candidate) for candidate in candidates
                if financial_catalog.complement_matches_primary(dict(candidate), raw)
            ]
            if len(matches) > 1:
                family = financial_catalog.canonical_family(
                    raw.get("instrument_type") or raw.get("asset_type") or raw.get("family"))
                source = financial_catalog.complement_source(raw)
                store.event(
                    "COMPLEMENTARY_IDENTITY_AMBIGUOUS",
                    f"{ticker}|{family}|{source}|matches={len(matches)}")
                # Keep every colliding primary identity in the explicit retry
                # ledger.  No row is selected or mutated while the provider
                # evidence cannot distinguish the canonical PPI identity.
                for candidate in matches:
                    connection.execute("""INSERT INTO complementary_contract_retry
                      VALUES(?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(ticker,instrument_type,market,currency,settlement,source)
                      DO UPDATE SET observed_at=excluded.observed_at,state=excluded.state,
                        reason=excluded.reason,last_attempt_at=excluded.last_attempt_at,
                        attempts=complementary_contract_retry.attempts+1""",
                      (candidate["ticker"], candidate["instrument_type"], candidate["market"],
                       candidate["currency"], candidate["settlement"], source,
                       raw.get("provider_observed_at") or raw.get("observed_at"),
                       "PENDING_SPECIAL",
                       f"COMPLEMENTARY_IDENTITY_AMBIGUOUS:matches={len(matches)}",
                       checked, 1))
                continue

            if not matches:
                # A complement-only identity may enter only if that provider
                # supplied a complete, internally consistent PAPER contract.
                try:
                    complement = financial_catalog.normalize_complementary_record(
                        raw, checked, "COMPLEMENTARY_RECONCILE")
                except (ValueError, TypeError):
                    continue
                if (complement.get("status") == "AVAILABLE"
                        and str(complement.get("capability") or "").startswith("READY_PAPER_")):
                    financial_catalog.persist(connection, complement)
                    connection.execute("""INSERT OR REPLACE INTO instrument_catalog
                      VALUES(?,?,?,?,?,?,?)""",
                      (complement["instrument_type"], complement["ticker"],
                       complement["description"], complement["market"],
                       complement["settlement"], checked,
                       json.dumps(complement["raw"], ensure_ascii=False, default=str)))
                    inserted += 1
                    promoted += 1
                    source = financial_catalog.complement_source(raw)
                    applied_by_source[source] = applied_by_source.get(source, 0) + 1
                continue

            primary = matches[0]
            try:
                primary["raw"] = json.loads(primary.pop("metadata_json"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

            before_ready = (
                primary.get("status") == "AVAILABLE"
                and str(primary.get("capability") or "").startswith("READY_PAPER_"))
            before = (
                primary.get("status"), primary.get("capability"),
                primary.get("last_seen_at"), primary.get("currency"),
                primary.get("settlement"), primary.get("market"),
                json.dumps(primary.get("raw") or {}, sort_keys=True, default=str),
            )
            merged = financial_catalog.complete_with_complement(primary, raw)
            after = (
                merged.get("status"), merged.get("capability"),
                merged.get("last_seen_at"), merged.get("currency"),
                merged.get("settlement"), merged.get("market"),
                json.dumps(merged.get("raw") or {}, sort_keys=True, default=str),
            )
            if after == before:
                continue

            # Hard invariant: a complement must not mutate the primary identity.
            for field in ("ticker","instrument_type","market","currency","settlement",
                          "settlement_source","description","last_seen_at","run_id"):
                if merged.get(field) != primary.get(field):
                    raise RuntimeError(f"COMPLEMENT_OVERWRITE_BLOCKED:{field}:{ticker}")

            financial_catalog.persist(connection, merged)
            connection.execute("""INSERT OR REPLACE INTO instrument_catalog
              VALUES(?,?,?,?,?,?,?)""",
              (merged["instrument_type"], merged["ticker"], merged["description"],
               merged["market"], merged["settlement"], checked,
               json.dumps(merged["raw"], ensure_ascii=False, default=str)))
            refreshed += 1
            source = financial_catalog.complement_source(raw)
            applied_by_source[source] = applied_by_source.get(source, 0) + 1
            now_ready = (
                merged.get("status") == "AVAILABLE"
                and str(merged.get("capability") or "").startswith("READY_PAPER_"))
            if now_ready and not before_ready:
                promoted += 1

        # Persist the exact unresolved contract condition even when IOL/BYMA
        # supplied no usable row in this cycle. This is retry state, not a
        # rejection: the scheduler and IOL timer will reconcile it again.
        for row in connection.execute("""SELECT ticker,instrument_type,market,currency,
          settlement,capability,status,last_seen_at FROM financial_instrument_catalog""").fetchall():
            ticker, family, market, currency, settlement, capability, status, last_seen = row
            ready = (status == "AVAILABLE"
                     and str(capability or "").startswith("READY_PAPER_"))
            if ready:
                connection.execute("""DELETE FROM complementary_contract_retry
                  WHERE ticker=? AND instrument_type=? AND market=? AND currency=? AND settlement=?""",
                  (ticker, family, market, currency, settlement))
                continue
            reason = str(capability or "").strip()
            if not (reason.startswith("NEEDS_") or reason.startswith("READY_CONTRACT_")):
                continue
            connection.execute("""INSERT INTO complementary_contract_retry
              VALUES(?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(ticker,instrument_type,market,currency,settlement,source)
              DO UPDATE SET observed_at=excluded.observed_at,state=excluded.state,
                reason=excluded.reason,last_attempt_at=excluded.last_attempt_at,
                attempts=complementary_contract_retry.attempts+1""",
              (ticker, family, market, currency, settlement,
               "CONTRACT_EVIDENCE_RECONCILER", last_seen, "PENDING_RETRY",
               reason, checked, 1))
            retry_pending += 1

        financial_catalog.sync_candidate_universe(connection, checked)
        ready_total = connection.execute("""SELECT COUNT(*) FROM financial_instrument_catalog
          WHERE status='AVAILABLE' AND capability LIKE 'READY_PAPER_%'""").fetchone()[0]
        catalog_total = connection.execute(
            "SELECT COUNT(*) FROM financial_instrument_catalog").fetchone()[0]
        ready_by_family = {
            str(family): int(count)
            for family, count in connection.execute("""SELECT instrument_type,COUNT(*)
              FROM financial_instrument_catalog
              WHERE status='AVAILABLE' AND capability LIKE 'READY_PAPER_%'
              GROUP BY instrument_type ORDER BY instrument_type""").fetchall()
        }

    store.event(
        "COMPLEMENTARY_CATALOG_RECONCILIATION",
        f"promoted_this_run={promoted}; inserted_this_run={inserted}; "
        f"refreshed_this_run={refreshed}; ready_total={ready_total}; "
        f"pending_total={catalog_total-ready_total}; "
        f"pending_contract_retry={retry_pending}; "
        f"ready_by_family={json.dumps(ready_by_family,sort_keys=True)}; "
        f"sources={json.dumps(applied_by_source,sort_keys=True)}; "
        "precedence=PPI>IOL>BYMA; real_orders=blocked")
    return promoted



def _eligible_symbols(store):
    """Devuelve el universo completo de familias en rotación PAPER/SHADOW.

    El catálogo puede contener más familias que el lote activo. La selección
    posterior mantiene una ventana limitada y un cursor persistente; aquí no
    se descartan familias por pertenecer a renta fija, cauciones o derivados.
    """
    core = []
    family_order = (
        "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
        "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
    )
    allowed_sql = ",".join(f"'{family}'" for family in family_order)
    try:
        with store.connect() as c:
            normalized_count = c.execute(
                "SELECT COUNT(*) FROM financial_instrument_catalog"
            ).fetchone()[0]
            if normalized_count:
                rows = c.execute(f"""SELECT DISTINCT ticker,instrument_type,settlement
                  FROM financial_instrument_catalog
                  WHERE status='AVAILABLE'
                    AND capability LIKE 'READY_PAPER_%'
                    AND UPPER(instrument_type) IN ({allowed_sql})
                  ORDER BY CASE WHEN ticker IN ('GGAL','AAPL') THEN 0 ELSE 1 END,
                           instrument_type,ticker,settlement""").fetchall()
            else:
                rows = c.execute(f"""SELECT ticker,instrument_type,settlement
                  FROM candidate_universe
                  WHERE can_simulate=1 AND status='AVAILABLE'
                    AND UPPER(instrument_type) IN ({allowed_sql})
                  ORDER BY CASE WHEN ticker IN ('GGAL','AAPL') THEN 0 ELSE 1 END,
                  instrument_type,ticker""").fetchall()
        seen = set(core)
        groups = {kind: [] for kind in family_order}
        for ticker, kind, settlement in rows:
            kind = str(kind or "").upper()
            value = (str(ticker).strip().upper(), kind, settlement)
            if value not in seen and kind in groups:
                groups[kind].append(value)
        # Round-robin por familia: evita que acciones/CEDEARs consuman todo
        # el lote y garantiza que las familias nuevas entren en la rotación.
        while any(groups.values()):
            for kind in family_order:
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


def _focus_coverage(store):
    """Contrasta el foco declarado con identidades PAPER realmente habilitadas.

    Un ticker parecido no alcanza: tipo y liquidacion deben coincidir, y el
    catalogo normalizado debe declarar READY_PAPER_SPOT. El fallback legado se
    usa solo cuando aun no existe ningun registro normalizado.
    """
    configured = tuple(FOCUS_SYMBOLS)
    observed_by_ticker = {}
    ready = set()
    source = "PPI_FINANCIAL_CATALOG"
    with store.connect() as c:
        normalized_count = c.execute(
            "SELECT COUNT(*) FROM financial_instrument_catalog"
        ).fetchone()[0]
        placeholders = ",".join("?" for _ in configured)
        tickers = tuple(value[0] for value in configured)
        if normalized_count:
            rows = c.execute(f"""SELECT ticker,instrument_type,settlement,status,capability
              FROM financial_instrument_catalog WHERE ticker IN ({placeholders})""",
              tickers).fetchall()
            for ticker, kind, settlement, status, capability in rows:
                identity = (ticker, kind, settlement)
                observed_by_ticker.setdefault(ticker, []).append(
                    {"identity": identity, "status": status, "capability": capability}
                )
                if status == "AVAILABLE" and capability == "READY_PAPER_SPOT":
                    ready.add(identity)
        else:
            source = "LEGACY_CANDIDATE_UNIVERSE"
            rows = c.execute(f"""SELECT ticker,instrument_type,settlement,status,can_simulate,detail
              FROM candidate_universe WHERE ticker IN ({placeholders})""",
              tickers).fetchall()
            for ticker, kind, settlement, status, can_simulate, detail in rows:
                identity = (ticker, kind, settlement)
                observed_by_ticker.setdefault(ticker, []).append(
                    {"identity": identity, "status": status, "capability": detail}
                )
                if status == "AVAILABLE" and int(can_simulate) == 1:
                    ready.add(identity)

    matched = tuple(value for value in configured if value in ready)
    missing = []
    for identity in configured:
        if identity in ready:
            continue
        candidates = observed_by_ticker.get(identity[0], [])
        if not candidates:
            reason = "NO_OBSERVADO_EN_CATALOGO"
        elif any(item["identity"] == identity for item in candidates):
            exact = next(item for item in candidates if item["identity"] == identity)
            reason = f"{exact['status']}:{exact['capability']}"
        else:
            actual = ",".join(
                f"{item['identity'][1]}/{item['identity'][2]}:{item['capability']}"
                for item in candidates[:4]
            )
            reason = "IDENTIDAD_DISTINTA:" + actual
        missing.append({"identity": identity, "reason": reason})

    count = len(matched)
    state = ("VERDE" if count == len(configured) else
             "AMARILLO" if count >= FOCUS_MINIMUM_FOR_OPENINGS else "ROJO")
    return {
        "state": state,
        "source": source,
        "configured_count": len(configured),
        "matched_count": count,
        "minimum_for_openings": FOCUS_MINIMUM_FOR_OPENINGS,
        "allow_new_openings": count >= FOCUS_MINIMUM_FOR_OPENINGS,
        "matched": matched,
        "missing": missing,
    }


def _publish_focus_health(store, coverage):
    missing = "; ".join(
        f"{item['identity'][0]}={item['reason']}" for item in coverage["missing"]
    ) or "ninguno"
    detail = (
        f"Foco efectivo {coverage['matched_count']}/{coverage['configured_count']}; "
        f"mínimo para nuevas aperturas simuladas {coverage['minimum_for_openings']}; "
        f"fuente {coverage['source']}; faltantes: {missing}. "
        f"Ingesta y cierres permanecen activos."
    )
    _health(store, "PAPER_FOCUS_COVERAGE", coverage["state"], detail,
            "Catálogo PPI normalizado", success=coverage["state"] == "VERDE")


def _cycle_symbols(store, focus_candidates=None):
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
    for candidate in tuple(FOCUS_SYMBOLS if focus_candidates is None else focus_candidates):
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


def _sampling_feasibility(selected_count, *, eligible_total=None, focus_count=None,
                          open_count=0):
    """Cota conservadora separada para foco y universo rotativo."""
    cycle_seconds = INTERVAL + max(0, selected_count) * 2 * PPI_CALL_BUDGET_SECONDS
    window_seconds = SIGNAL_WINDOW_MINUTES * 60
    capacity = int(window_seconds / max(cycle_seconds, 1))
    focus_count = selected_count if focus_count is None else max(0, focus_count)
    eligible_total = selected_count if eligible_total is None else max(0, eligible_total)
    rotation_slots = max(0, selected_count - max(0, open_count) - focus_count)
    rotation_pool = max(0, eligible_total - focus_count - max(0, open_count))
    rotation_turns = (math.ceil(rotation_pool / rotation_slots)
                      if rotation_pool and rotation_slots else None)
    rotation_capacity = (int(window_seconds / max(cycle_seconds * rotation_turns, 1))
                         if rotation_turns else 0)
    return {
        "feasible": bool(focus_count) and capacity >= SIGNAL_MIN_SAMPLES,
        "focus_feasible": bool(focus_count) and capacity >= SIGNAL_MIN_SAMPLES,
        "rotation_feasible": (rotation_pool == 0 or
                              (rotation_slots > 0 and rotation_capacity >= SIGNAL_MIN_SAMPLES)),
        "estimated_cycle_seconds": round(cycle_seconds, 2),
        "estimated_samples_per_window": capacity,
        "focus_count": focus_count,
        "focus_estimated_samples_per_window": capacity if focus_count else 0,
        "rotation_slots": rotation_slots,
        "rotation_pool": rotation_pool,
        "rotation_turns": rotation_turns,
        "rotation_estimated_samples_per_window": rotation_capacity,
        "required_samples": SIGNAL_MIN_SAMPLES,
        "window_minutes": SIGNAL_WINDOW_MINUTES,
    }


def _economic_shadow_summary(store):
    """Resume la jornada local sin reinterpretar decisiones historicas."""
    today = datetime.now(TZ).date()
    result = {"evaluated": 0, "passed": 0, "failed": 0,
              "opened_with_failure": 0, "blocked_with_failure": 0}
    with store.connect() as c:
        rows = c.execute("""SELECT evaluated_at,final_result,detail_json
          FROM trade_gate_evaluations ORDER BY id DESC LIMIT 5000""").fetchall()
    for evaluated_at, final_result, detail_json in rows:
        try:
            evaluated = datetime.fromisoformat(str(evaluated_at).replace("Z", "+00:00"))
            if evaluated.astimezone(TZ).date() != today:
                continue
            economics = json.loads(detail_json or "{}").get("economics")
            if not isinstance(economics, dict) or "passed" not in economics:
                continue
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        result["evaluated"] += 1
        if bool(economics["passed"]):
            result["passed"] += 1
        else:
            result["failed"] += 1
            if final_result == "OPENED_SIMULATED":
                result["opened_with_failure"] += 1
            elif final_result == "BLOCKED":
                result["blocked_with_failure"] += 1
    return result


def _publish_economic_shadow_health(store):
    summary = _economic_shadow_summary(store)
    binding = os.getenv("PAPER_ECONOMIC_GATE_MODE", "SHADOW").upper() == "BINDING"
    if not summary["evaluated"]:
        state = "AMARILLO"
        detail = "Sin señales BUY evaluadas hoy; esperando evidencia de rueda."
    elif summary["failed"]:
        state = ("ROJO" if binding and summary["opened_with_failure"] else
                 "VERDE" if binding else "AMARILLO")
        detail = (
            f"Evaluadas={summary['evaluated']}; aprueban={summary['passed']}; "
            f"fallan={summary['failed']}; abrieron simuladas pese al fallo="
            f"{summary['opened_with_failure']}; bloqueadas={summary['blocked_with_failure']}."
        )
    else:
        state = "VERDE"
        detail = f"Evaluadas={summary['evaluated']}; todas superaron el umbral económico."
    detail += (" BINDING bloquea toda señal que no cubra costos y reward/risk neto; "
               "no habilita dinero real." if binding else
               " SHADOW valida el pipeline; no demuestra rentabilidad ni habilita dinero real.")
    _health(store, "PAPER_ECONOMIC_GATE_SHADOW", state, detail,
            "Motor matemático Python", success=state == "VERDE")
    return summary


def _runtime_readiness(store, *, record_event=False):
    """Publica la capacidad declarada antes de permitir nuevas aperturas."""
    focus = _focus_coverage(store)
    _publish_focus_health(store, focus)
    open_count = len(store.open_positions())
    symbols, eligible_total, cursor_before, cursor_after = _cycle_symbols(
        store, focus_candidates=focus["matched"]
    )
    matched_set = set(focus["matched"])
    selected_focus = sum(symbol in matched_set for symbol in symbols)
    feasibility = _sampling_feasibility(
        len(symbols), eligible_total=eligible_total,
        focus_count=selected_focus, open_count=open_count
    )
    if not feasibility["focus_feasible"]:
        if record_event:
            store.event("SAMPLING_INFEASIBLE", json.dumps(feasibility, sort_keys=True))
        _health(store, "PAPER_SIGNAL_SAMPLING", "ROJO",
                "El foco no puede reunir la ventana mínima: " +
                json.dumps(feasibility, sort_keys=True), "Runtime PAPER")
    else:
        _health(store, "PAPER_SIGNAL_SAMPLING", "VERDE",
                "Foco con capacidad teórica suficiente: " +
                json.dumps(feasibility, sort_keys=True), "Runtime PAPER", success=True)
    rotation_state = "VERDE" if feasibility["rotation_feasible"] else "AMARILLO"
    _health(store, "PAPER_SIGNAL_ROTATION", rotation_state,
            "Factibilidad rotativa: " + json.dumps(feasibility, sort_keys=True),
            "Runtime PAPER", success=rotation_state == "VERDE")
    return focus, symbols, eligible_total, cursor_before, cursor_after, feasibility


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

def _history_batch_semantics(statuses):
    """Clasifica el lote sin descartar evidencia parcial válida.

    VALID_PAYLOAD es completo; PARTIAL conserva evidencia usable pero obliga
    a AMARILLO; EMPTY_OR_INVALID y ERROR son fallas duras. Cualquier estado
    desconocido falla cerrado para no maquillar degradación de históricos.
    """
    allowed = {"VALID_PAYLOAD", "PARTIAL", "EMPTY_OR_INVALID", "ERROR"}
    normalized = []
    for raw in statuses:
        status = str(raw).strip().upper()
        if status not in allowed:
            raise ValueError(f"PPI_HISTORY_BATCH_UNKNOWN_STATUS:{raw}")
        normalized.append(status)
    full_valid = normalized.count("VALID_PAYLOAD")
    partial = normalized.count("PARTIAL")
    empty_invalid = normalized.count("EMPTY_OR_INVALID")
    errors = normalized.count("ERROR")
    usable = full_valid + partial
    hard_failures = empty_invalid + errors
    if normalized and full_valid == len(normalized):
        state = "VERDE"
    elif usable:
        state = "AMARILLO"
    else:
        state = "ROJO"
    return {
        "full_valid": full_valid,
        "partial_with_valid_evidence": partial,
        "empty_invalid": empty_invalid,
        "errors": errors,
        "usable": usable,
        "hard_failures": hard_failures,
        "state": state,
    }


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


def _history_cutoff_repair_complete() -> bool:
    try:
        from cv_history_store_adapter_hf6 import default_history_store
        with default_history_store().connect() as c:
            exists = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rc6_history_cutoff_repair_runs'"
            ).fetchone()
            if not exists:
                return False
            row = c.execute(
                "SELECT state FROM rc6_history_cutoff_repair_runs WHERE cutoff='2026-09-21'"
            ).fetchone()
            return bool(row and str(row[0]).upper() == "COMPLETE")
    except (sqlite3.Error, OSError):
        return False


def _history_end_date(now=None):
    now = now or datetime.now(TZ)
    close_minute = MARKET_CLOSE_HOUR * 60 + MARKET_CLOSE_MINUTE
    if now.hour * 60 + now.minute < close_minute:
        return None
    day = now.date()
    while day.year == BYMA_CALENDAR_AUDITED_YEAR and not _business_day(day):
        day -= timedelta(days=1)
    return day if day.year == BYMA_CALENDAR_AUDITED_YEAR else None


def _history_repair_lock_path(history_store):
    return Path(history_store.path).parent / ".rc6-history-cutoff-repair.lock"


def _download_histories(reader, store):
    # The one-time cutoff repair must finish before scheduled PPI history writes.
    # This prevents startup/manual sync from repeating an annual request or racing
    # the resumable importer.
    if not _history_cutoff_repair_complete():
        return 0
    from cv_history_store_adapter_hf6 import default_history_store
    history_store = default_history_store()
    lock_path = _history_repair_lock_path(history_store)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            store.event("HISTORY_V2_LOCKED", "La reparación histórica RC6 está activa; no se duplica la consulta.")
            return 0
        try:
            return _download_histories_locked(reader, store, history_store)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _download_histories_locked(reader, store, history_store):
    end = _history_end_date()
    if end is None:
        return 0
    total = 0
    batch_statuses = []
    all_symbols = _historical_targets(store)
    targets = []
    for symbol, instrument_type, settlement in all_symbols:
        metadata = financial_catalog.lookup(store, symbol, instrument_type, settlement)
        market = str((metadata or {}).get("market") or "").strip().upper()
        if not market or market == "UNKNOWN":
            batch_statuses.append("ERROR")
            store.event("HISTORY_V2_ERROR", f"{symbol}: HISTORY_MARKET_IDENTITY_MISSING")
            continue
        with history_store.connect() as c:
            row = c.execute(
                """SELECT MAX(date) FROM history_canonical_v2
                   WHERE symbol=? AND instrument_type=? AND market=? AND settlement=?""",
                (symbol, instrument_type, market, settlement),
            ).fetchone()
        latest = str(row[0])[:10] if row and row[0] else None
        start = (date.fromisoformat(latest) + timedelta(days=1)
                 if latest else end - timedelta(days=365))
        if start <= end:
            targets.append((symbol, instrument_type, settlement, market, start, metadata))
    symbols = targets[:HISTORY_BATCH_LIMIT]
    if not symbols:
        detail = f"Sin fechas históricas nuevas hasta {end.isoformat()}; no se repite descarga anual."
        _sync_state(store, "PPI_PRODUCTION_HISTORY", "VERDE", 0, detail, success=True)
        _health(store, "PPI_PRODUCTION_HISTORY", "VERDE", detail, "PPI Producción", success=True)
        return 0

    from bl_candle_engine import canonical
    from fb_raw_evidence_exact_v1 import enabled as exact_evidence_enabled, archive_wrapper as archive_exact_wrapper
    for symbol, instrument_type, settlement, market, start, metadata in symbols:
        attempted = now_iso()
        try:
            payload = reader.history(symbol, instrument_type, settlement, start, end)
            attempted = now_iso()
            bounded_payload = [
                row for row in payload
                if isinstance(row, dict) and start.isoformat() <= str(row.get("date", ""))[:10] <= end.isoformat()
            ] if isinstance(payload, list) else payload
            count = _history_count(bounded_payload, as_of=attempted,
                                   date_from=start, date_to=end)
            expected = len(bounded_payload) if isinstance(bounded_payload, list) else 0
            status = 'VALID_PAYLOAD' if count and count == expected else 'PARTIAL' if count else 'EMPTY_OR_INVALID'
            row_key = canonical([symbol, instrument_type, settlement, attempted])
            history_wrapper = {'symbol':symbol,'asset_class':instrument_type,'settlement':settlement,
                        'date_from':start.isoformat(),'date_to':end.isoformat(),'metadata':metadata,
                        'valid_rows':count,'payload_json':json.dumps(payload,ensure_ascii=False,default=str)}
            external_exact = exact_evidence_enabled()
            if external_exact:
                archive_exact_wrapper(row_key=row_key, wrapper=history_wrapper,
                                      recorded_at=attempted, quality=status)
            with store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                if not external_exact:
                    from bl_candle_engine import archive_raw
                    archive_raw(c,origin='PPI_HISTORY',row_key=row_key,payload=history_wrapper,
                                recorded_at=attempted,quality=status)
                c.execute('INSERT OR REPLACE INTO production_history_attempts VALUES(?,?,?,?,?,?,?)',
                          (symbol,instrument_type,settlement,attempted,status,count,
                           'PPI incremental desde el último cierre canónico; OHLC y fuente registrados'))
                if status=='VALID_PAYLOAD':
                    c.execute("INSERT OR REPLACE INTO production_history VALUES(?,?,?,?,?,?,?,?)",
                              (symbol, instrument_type, settlement, start.isoformat(), end.isoformat(),
                               attempted, count, json.dumps(payload, ensure_ascii=False, default=str)))

            try:
                import ct_ppi_history_salvage_hf6 as history_salvage
                v2_result = history_salvage.ingest_ppi_payload(
                    store, symbol=symbol, instrument_type=instrument_type,
                    market=market, settlement=settlement, payload=bounded_payload,
                    requested_from=start, requested_to=end, attempted_at=attempted,
                    history_store=history_store)
                store.event("HISTORY_V2_INGEST",
                            f"{symbol}: valid={v2_result.get('valid_rows',0)}; "
                            f"versions={v2_result.get('versions_appended',0)}")
            except Exception as exc:
                store.event("HISTORY_V2_ERROR", f"{symbol}: {type(exc).__name__}: {str(exc)[:180]}")
            total += count
            batch_statuses.append(status)
        except Exception as exc:
            batch_statuses.append('ERROR')
            with store.connect() as c:
                c.execute('INSERT OR REPLACE INTO production_history_attempts VALUES(?,?,?,?,?,?,?)',
                          (symbol,instrument_type,settlement,attempted,'ERROR',0,type(exc).__name__))
            store.event("HISTORY_ERROR", f"{symbol}: {type(exc).__name__}: {str(exc)[:180]}")
    semantics = _history_batch_semantics(batch_statuses)
    state = semantics["state"]
    with store.connect() as c:
        covered = c.execute("SELECT COUNT(*) FROM production_history WHERE row_count>0").fetchone()[0]
    detail = (f"Lote histórico incremental={semantics['full_valid']}/{len(symbols)}; "
              f"parciales usables={semantics['partial_with_valid_evidence']}; "
              f"fallas duras={semantics['hard_failures']} "
              f"(vacío/inválido={semantics['empty_invalid']}, errores={semantics['errors']}); "
              f"cobertura acumulada {covered}/{len(all_symbols)} instrumentos; "
              f"{total} filas válidas desde el último cierre hasta {end.isoformat()}.")
    usable = bool(semantics["usable"])
    _sync_state(store, "PPI_PRODUCTION_HISTORY", state, total, detail, success=usable)
    _health(store, "PPI_PRODUCTION_HISTORY", state, detail, "PPI Producción", success=usable)
    return total


def _daily_sync_needed(store):
    """Una bajada de catálogo por día; un error no provoca reintentos en bucle."""
    with store.connect() as connection:
        rows = connection.execute("""SELECT source,last_attempt_at FROM source_sync
          WHERE source IN ('PPI_PRODUCTION_CATALOG','PPI_PRODUCTION_HISTORY')""").fetchall()
    attempts = {row[0]: str(row[1] or "")[:10] for row in rows}
    today = datetime.now(TZ).date().isoformat()
    return any(attempts.get(source) != today for source in
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


def _background_ingest_due(store, now=None):
    """Como máximo un intento histórico por rueda, después del cierre."""
    now = now or datetime.now(TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    local_now = now.astimezone(TZ)
    if not _business_day(local_now.date()):
        return False
    close_minute = MARKET_CLOSE_HOUR * 60 + MARKET_CLOSE_MINUTE
    if local_now.hour * 60 + local_now.minute < close_minute:
        return False
    with store.connect() as connection:
        row = connection.execute("""SELECT last_attempt_at FROM source_sync
          WHERE source='PPI_PRODUCTION_HISTORY'""").fetchone()
    if not row or not row[0]:
        return True
    try:
        last = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=TZ)
        local_last = last.astimezone(TZ)
        return local_last.date() < local_now.date()
    except (TypeError, ValueError):
        return True

def _background_ingest(reader, store, *, force=False):
    """Completa históricos una vez por rueda después del cierre; force no salta el límite."""
    if not _background_ingest_due(store):
        return None
    try:
        rows = _download_histories(reader, store)
        with store.connect() as connection:
            source = connection.execute("""SELECT status,detail FROM source_sync
              WHERE source='PPI_PRODUCTION_HISTORY'""").fetchone()
        state = str(source[0] if source else "AMARILLO")
        detail = (
            f"Lote histórico incremental completado: {rows} filas válidas; "
            f"próximo intento no antes de {BACKGROUND_INGEST_SECONDS // 3600} h. "
            f"resultado de origen={state}. No se consultaron cotizaciones ni "
            "libros fuera de rueda."
        )
        _health(store, "PPI_BACKGROUND_INGEST", state, detail,
                "PPI Producción / History", success=state == "VERDE")
        store.event("BACKGROUND_READONLY_INGEST", detail)
        return rows
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:500]}"
        _health(store, "PPI_BACKGROUND_INGEST", "ROJO", detail,
                "PPI Producción / History")
        store.event("BACKGROUND_READONLY_INGEST_ERROR", detail)
        return None


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
            store.event("PPI_LOGIN", "owner=scanner_manual")
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


def _announce_phase(store, previous, current):
    """Aviso idempotente por transición; nunca confunde arranque con rueda."""
    if previous is None or previous == current:
        return
    messages = {
        "PREOPEN": ("🟡 POROTA — PREAPERTURA",
                    "Login e ingesta activos. Cero evaluaciones y cero operaciones."),
        "OPEN": ("🟢 POROTA — RUEDA ABIERTA",
                 "Motor Python evaluando. Operaciones 100% simuladas. Órdenes reales: NINGUNA."),
        "CLOSED": ("🌙 POROTA — RUEDA CERRADA",
                   "Estrategia detenida. Ingesta histórica y dashboard continúan activos."),
    }
    if current not in messages:
        return
    from bn_telegram_bus import enqueue
    title, detail = messages[current]
    at = now_iso()
    local_day = datetime.now(TZ).date().isoformat()
    try:
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            enqueue(c, f"session-phase:{current}:{local_day}", "SESSION_PHASE",
                    f"{title}\n{detail}\n{at}", at, priority=15)
    except Exception as exc:
        store.event("PHASE_NOTICE_ERROR", f"{current}: {type(exc).__name__}")


def run():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    # Sólo ante un arranque anormalmente lento se vuelca la pila Python para
    # localizar I/O local; no contiene ni lee secretos.
    faulthandler.enable()
    faulthandler.dump_traceback_later(45, repeat=True)
    # Bajo el runtime padre, el esquema y la identidad ya fueron validados
    # una sola vez. El scanner no debe competir por el lock de arranque.
    if os.getenv("POROTA_RUNTIME_SCHEMA_READY", "").strip() == "1":
        store = PaperStore(DB_PATH)
    else:
        store = runtime_store(DB_PATH)
        _support_schema(store)
    store.state(process_state="STARTING", session_state="CHECKING",
                ppi_auth="NOT_ATTEMPTED", heartbeat_at=now_iso(),
                real_orders_sent=0, detail="Inicializando servicios 24x7.")
    # El login PPI no depende del motor. Se posterga la construcción de
    # componentes de decisión/settlement hasta que la sesión de sólo lectura
    # esté confirmada, para que un componente local lento no simule un fallo
    # de autenticación ni bloquee el primer pulso.
    from bv_paper_runtime import broker_from_environment
    broker = None
    store.state(process_state="STARTING", session_state="CHECKING",
                ppi_auth="NOT_ATTEMPTED", real_orders_sent=0,
                detail="Iniciando sesión PPI de solo lectura antes del motor PAPER.")
    # La fase de mercado se resuelve dentro del ciclo, después de publicar
    # BOOT_COMMAND/BOOT_CALENDAR. Así un calendario lento nunca deja al
    # operador con STARTING/CHECKING sin evidencia del punto de arranque.
    reader = None
    quotes = {}
    last_public_check = 0.0
    last_structured_capture = 0.0
    last_readiness_check = 0.0
    last_complementary_reconcile = 0.0
    next_login_at = 0.0
    last_phase = None  # Publicar BOOT_* antes de resolver el calendario.
    try:
        while not STOP:
            # Una caución vence por contrato, aunque el mercado esté cerrado
            # o falle el login. No depende de cotizaciones ni de IA.
            if broker is not None:
                broker.settle_cauciones()
            # Breadcrumbs de arranque: permiten distinguir qué paso local
            # antecede al login sin tocar órdenes ni datos de mercado.
            if reader is None:
                store.state(session_state="BOOT_COMMAND", ppi_auth="NOT_ATTEMPTED",
                            heartbeat_at=now_iso(), real_orders_sent=0,
                            detail="Arranque: revisando comandos locales.")
            # La lectura de PPI tiene prioridad sobre mantenimiento y sondas.
            # No se bloquea una rueda recién abierta con backups/reportes.
            command = _claim_command(store)
            if command:
                reader = _run_command(store, reader, command, None)
            if reader is None:
                store.state(session_state="BOOT_CALENDAR", ppi_auth="NOT_ATTEMPTED",
                            heartbeat_at=now_iso(), real_orders_sent=0,
                            detail="Arranque: verificando calendario operativo.")
            phase = _market_phase()
            previous_phase = last_phase
            if phase != last_phase:
                _announce_phase(store, previous_phase, phase)
                last_phase = phase
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
                store.state(session_state="BOOT_PPI_SECRET", ppi_auth="NOT_ATTEMPTED",
                            heartbeat_at=now_iso(), real_orders_sent=0,
                            detail="Arranque: leyendo secreto PPI local.")
                key, secret = _secret()
                store.state(session_state="BOOT_PPI_CLIENT", ppi_auth="NOT_ATTEMPTED",
                            heartbeat_at=now_iso(), real_orders_sent=0,
                            detail="Arranque: creando cliente PPI de solo lectura.")
                reader = ProductionMarketReader(key, secret, audit=store.audit_http)
                try:
                    store.state(session_state="BOOT_PPI_LOGIN", ppi_auth="NOT_ATTEMPTED",
                                heartbeat_at=now_iso(), real_orders_sent=0,
                                detail="Arranque: autenticando PPI de solo lectura.")
                    reader.login_once()
                    store.event("PPI_LOGIN", "owner=scanner")
                    _health(store, "PPI_PRODUCTION_AUTH", "VERDE",
                            "Login automático de solo lectura correcto.",
                            "PPI Producción", success=True)
                    store.state(ppi_auth="OK", session_state=phase,
                                detail="Login de solo lectura correcto; inicializando motor PAPER.")
                    # La IA continúa OFF; este paso no envía órdenes ni llama
                    # rutas operativas. Sólo prepara decisiones simuladas.
                    broker = broker_from_environment(store, ai_gate=None, require_ai=False,
                                                     ai_mode="OFF", context_fn=None)
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
            # Login listo: recién ahora corren servicios no críticos por TTL.
            operations.service_tick(store, phase)
            if time.time() - last_public_check >= PUBLIC_CHECK_SECONDS:
                _public_probe(store)
                last_public_check = time.time()
            should_capture = phase == "OPEN" and time.time() - last_structured_capture >= PUBLIC_STRUCTURED_CAPTURE_SECONDS
            closing_snapshot = previous_phase == "OPEN" and phase == "CLOSED"
            if should_capture or closing_snapshot:
                _public_probe(store, structured=True)
                last_structured_capture = time.time()
            if time.time() - last_complementary_reconcile >= COMPLEMENTARY_RECONCILE_SECONDS:
                _reconcile_complementary_catalog(store)
                last_complementary_reconcile = time.time()
            if phase == "CLOSED":
                # El catálogo se renueva una vez por fecha local. Los históricos
                # avanzan en lotes con TTL aunque sea noche, fin de semana o feriado.
                _daily_sync(reader, store)
                _background_ingest(reader, store)
                if time.time() - last_readiness_check >= READINESS_CHECK_SECONDS:
                    _runtime_readiness(store)
                    _publish_economic_shadow_health(store)
                    last_readiness_check = time.time()
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
            (focus, symbols, eligible_total, cursor_before, cursor_after,
             feasibility) = _runtime_readiness(store, record_event=True)
            cycle_started = now_iso()
            cycle_clock = time.perf_counter()
            cycle_ok = 0
            cycle_failures = 0
            cycle_session_invalid = False
            latencies = []
            for symbol, asset_class, settlement in symbols:
                if STOP:
                    break
                symbol_clock = time.perf_counter()
                try:
                    current = retry_read(
                        lambda: reader.current(symbol, asset_class, settlement), retries=1)
                    book = retry_read(
                        lambda: reader.book(symbol, asset_class, settlement), retries=1)
                    metadata = financial_catalog.lookup(store, symbol, asset_class, settlement)
                    q = normalize_quote(symbol, asset_class, settlement, current, book, metadata=metadata)
                    # Persistir la observación sirve para trazabilidad, pero un
                    # trade/libro stale nunca alcanza al motor ni a sus gates.
                    store.add_quote(q)
                    quotes[(symbol, asset_class, settlement, q.currency, q.market)] = q
                    data_error = q.time_error(
                        now_iso(), require_trade=True,
                        max_age_seconds=broker.quote_max_age_seconds,
                        max_trade_age_seconds=broker.trade_max_age_seconds)
                    if data_error:
                        store.event("DATA_REJECTED", f"{symbol}: {data_error}")
                        continue
                    allow_openings = bool(focus["allow_new_openings"])
                    opening_reason = (
                        f"Cobertura prioritaria insuficiente: "
                        f"{focus['matched_count']}/{focus['configured_count']}"
                    )
                    # Para CEDEARs se conserva la observación BYMA, pero la
                    # apertura requiere también rueda regular del subyacente US.
                    cedear_allowed, cedear_reason = cedear_opening_gate(asset_class, datetime.now(TZ))
                    if not cedear_allowed:
                        allow_openings = False
                        opening_reason = cedear_reason
                    broker.on_quote(
                        q, allow_new_openings=allow_openings,
                        opening_block_reason=opening_reason,
                    )
                    cycle_ok += 1
                    store.state(last_market_data_at=q.observed_at)
                except ReadOnlyPolicyViolation as exc:
                    store.event("HTTP_BLOCKED", str(exc))
                    store.state(process_state="DEGRADED", detail="La barrera bloqueo una ruta no permitida.")
                except Exception as exc:
                    cycle_failures += 1
                    store.event("DATA_ERROR", f"{symbol}: {classify_read_error(exc)}")
                    if session_invalid(exc):
                        cycle_session_invalid = True
                        store.event("PPI_SESSION_INVALID",
                                    "Sesión de market data expirada; ciclo interrumpido y relogin controlado")
                        break
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
            store.state(process_state="DEGRADED" if cycle_session_invalid or not cycle_ok else "RUNNING",
                        session_state="MARKET_OPEN", heartbeat_at=now_iso(),
                        ppi_auth="ERROR" if cycle_session_invalid else "OK",
                        http_allowed=metrics["http_allowed"],
                        http_blocked=metrics["http_blocked"], real_orders_sent=0,
                        detail=(f"{cycle_ok}/{len(symbols)} instrumentos actualizados de "
                                f"{eligible_total} elegibles; rotación activa; operaciones solo simuladas."))
            _health(store, "PPI_PRODUCTION_MARKETDATA",
                    "VERDE" if cycle_ok else "ROJO",
                    f"{cycle_ok}/{len(symbols)} instrumentos con cotización útil.",
                    "PPI Producción", success=bool(cycle_ok))
            _publish_economic_shadow_health(store)
            if cycle_session_invalid:
                reader.close()
                reader = None
                next_login_at = time.time() + 60
            time.sleep(INTERVAL)
    finally:
        if reader:
            reader.close()
        store.state(process_state="STOPPED", heartbeat_at=now_iso(), real_orders_sent=0,
                    detail="Observador detenido ordenadamente.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
