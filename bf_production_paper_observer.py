"""Observador productivo con paper trading completo y cero capacidad operativa."""

from __future__ import annotations
from contextlib import closing, contextmanager, nullcontext, suppress
from dataclasses import replace

import faulthandler
import fcntl

import json
import math
import os
import re
import signal
import sqlite3
import statistics
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from itertools import zip_longest
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
from rc6_multisource_discovery import (
    ppi_query_plan, complementary_discovery, canonical_family, canonical_market,
)
from rc6_ppi_global_budget import (
    PAPER_DIRECT_WIRE_BACKPRESSURE,
    assert_native_ppi_response_identity, native_ppi_response_settlement,
)
from rc6_paper_admission_gate import paper_admission_guard
from rc6_scanner_login_interlock import ScannerLoginInterlock
from rc6_scanner_storage_guard import scanner_with_storage_quarantine
from rc6_ppi_scanner_provenance import ScannerPrimaryRecorder, FILENAME as PPI_SCANNER_RECEIPT_FILENAME
from co_market_sessions_hf6 import byma_eod_publication_floor
from scripts.rc6_history_cutoff_repair_once import CUTOFF as HISTORY_CUTOFF


TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
from cg_paper_workspace import database_path, runtime_store

DB_PATH = str(database_path())
SECRET_PATH = os.getenv("PPI_PRODUCTION_SECRET_FILE", "/run/secrets/ppi_production.json")
INTERVAL = max(15, int(os.getenv("PAPER_OBSERVER_INTERVAL_SECONDS", "60")))
IOL_CONTINUOUS_DISPATCH_SECONDS = 30
COMMAND_POLL_SECONDS = max(3, int(os.getenv("PAPER_COMMAND_POLL_SECONDS", "5")))
PUBLIC_CHECK_SECONDS = max(900, int(os.getenv("PUBLIC_SOURCE_CHECK_SECONDS", "21600")))
PUBLIC_STRUCTURED_CAPTURE_SECONDS = max(900, int(os.getenv("PUBLIC_STRUCTURED_CAPTURE_SECONDS", "900")))
# Inside the immutable observer container, persistent market data is mounted
# at /app/data. Host-side systemd pipelines keep their explicit /opt root.
PUBLIC_CAPTURE_PATH = Path(os.getenv(
    "POROTA_PUBLIC_SOURCE_CAPTURE_PATH",
    "/app/data/market/rc6_public_sources_latest.json",
))
PUBLIC_CAPTURE_MAX_BYTES = 16 * 1024 * 1024
PUBLIC_CAPTURE_MAX_AGE_SECONDS = max(1800, 3 * PUBLIC_STRUCTURED_CAPTURE_SECONDS)
LOGIN_COOLDOWN_SECONDS = max(300, int(os.getenv("PPI_LOGIN_COOLDOWN_SECONDS", "900")))
GATEWAY_LOCAL_ATTACH_RETRY_SECONDS = 15  # socket IPC only, never another broker login
BACKGROUND_INGEST_SECONDS = max(
    3600, int(os.getenv("PPI_BACKGROUND_INGEST_SECONDS", "21600"))
)
READINESS_CHECK_SECONDS = max(
    60, int(os.getenv("PAPER_READINESS_CHECK_SECONDS", "300"))
)
COMPLEMENTARY_RECONCILE_SECONDS = max(
    60, int(os.getenv("PAPER_COMPLEMENTARY_RECONCILE_SECONDS", "300"))
)
# CLOSED has no market execution. Full catalog reconciliation allocates a
# large temporary working set and writes SQLite even when the market is shut.
# PREOPEN retains its shorter cadence before entry admission resumes.
OFFHOURS_RECONCILE_SECONDS = max(
    COMPLEMENTARY_RECONCILE_SECONDS,
    int(os.getenv("PAPER_OFFHOURS_RECONCILE_SECONDS", "3600")),
)
CAUCION_LIVE_REFRESH_SECONDS = max(30, int(os.getenv("PAPER_CAUCION_LIVE_REFRESH_SECONDS", "60")))
COMPLEMENTARY_CONTRACT_TTL_SECONDS = max(
    300, int(os.getenv("PAPER_COMPLEMENTARY_CONTRACT_TTL_SECONDS", "86400"))
)
ACTIVE_SYMBOL_LIMIT = max(3, min(int(os.getenv("PAPER_ACTIVE_SYMBOL_LIMIT", "20")), 60))
PPI_CALL_BUDGET_SECONDS = max(0.1, float(os.getenv("PAPER_PPI_CALL_BUDGET_SECONDS", "2")))
SIGNAL_MIN_SAMPLES = max(3, int(os.getenv("PAPER_SIGNAL_MIN_SAMPLES", "6")))
SIGNAL_WINDOW_MINUTES = max(15, int(os.getenv("PAPER_SIGNAL_WINDOW_MINUTES", "90")))
HISTORY_BATCH_LIMIT = max(5, min(int(os.getenv("PPI_HISTORY_BATCH_LIMIT", "40")), 100))
CATALOG_QUERY_SLEEP_SECONDS = max(0.0, float(os.getenv("PPI_CATALOG_QUERY_SLEEP_SECONDS", "0.05")))
CATALOG_DEFERRED_STATUS = "DEFERRED_PROVIDER"
CATALOG_GATEWAY_DEFERRED_STATUS = "DEFERRED_GATEWAY_REATTACH"
CATALOG_PROVIDER_BACKPRESSURE = PAPER_DIRECT_WIRE_BACKPRESSURE | {
    "PPI_AUX_HTTP_401",
    "PPI_AUX_HTTP_429",
    "PPI_AUX_PROVIDER_BACKOFF_ACTIVE",
    "PPI_GATEWAY_SESSION_RECOVERY_PENDING",
}
NATIVE_PROVIDER_BACKPRESSURE = PAPER_DIRECT_WIRE_BACKPRESSURE | {
    "PPI_HTTP_429", "PPI_NATIVE_HTTP_429", "PPI_NATIVE_PROVIDER_BACKOFF_ACTIVE",
}
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
_PPI_REFERENCE_OBSERVED = {
    "FCI": "FCI_CURRENT_REFERENCE_OBSERVED",
    "CAUCIONES": "CAUCION_CURRENT_REFERENCE_OBSERVED",
}

# Only families that can produce the paired native settlement receipt use
# these turns. The dedicated caucion book and fund class/NAV have their own
# input contracts. Manual reference diagnostics remain available.
PPI_DIAGNOSTIC_FAMILY_ROTATION = (
    "ACCIONES", "CEDEARS", "BONOS", "LETRAS", "OBLIGACIONES",
    "OPCIONES", "FUTUROS", "ETFS",
)


def _next_ppi_diagnostic_family(cursor):
    """Keep discovering identities after the first READY key in a family."""
    if type(cursor) is not int or cursor < 0:
        raise ValueError("PPI_DIAGNOSTIC_CURSOR_INVALID")
    size = len(PPI_DIAGNOSTIC_FAMILY_ROTATION)
    return PPI_DIAGNOSTIC_FAMILY_ROTATION[cursor % size], (cursor + 1) % size
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
PAPER_SPOT_FAMILIES = frozenset({"ACCIONES", "CEDEARS", "ETFS"})


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


def _scanner_gateway_reader_lost(error):
    """Only a dead/rotated owner or auth loss needs a new local AF_UNIX pin."""
    from rc6_ppi_gateway_reader_adapter import gateway_reader_needs_reattach
    return gateway_reader_needs_reattach(error)


def _scanner_ppi_error(error):
    from rc6_ppi_gateway_reader_adapter import GatewayEvidenceRejected, SharedFeedUnavailable

    if isinstance(error, (GatewayEvidenceRejected, SharedFeedUnavailable)):
        reason = str(error)
        if re.fullmatch(r"PPI_[A-Z0-9_]{1,100}", reason):
            return reason
    return classify_read_error(error)


def _scanner_iol_error(error):
    from iol_rest_readonly_rc6 import IOLRestError
    from rc6_iol_market_gateway import iol_gateway_reader_needs_reattach

    if isinstance(error, IOLRestError):
        if str(error) == "IOL_REST_HTTP_ERROR" and error.status_code in (401, 403, 429):
            return f"IOL_HTTP_{error.status_code}"
        if re.fullmatch(r"IOL_[A-Z0-9_]{1,100}", str(error)):
            return str(error)
    if iol_gateway_reader_needs_reattach(error):
        return "IOL_GATEWAY_UNAVAILABLE"
    return "IOL_MARKET_OBSERVATION_FAILED"


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


def _cooldown_session_state(phase, now=None):
    """Certify the local postclose window even when PPI login is cooling down.

    The local EOD copier needs a fresh, explicit closed heartbeat. A failed
    login must not turn BOOT_CALENDAR into a synthetic OPEN/PREOPEN receipt.
    Only the independently audited BYMA calendar and postclose clock can
    produce MARKET_CLOSED here; an unknown year or calendar error stays boot.
    """
    local = (now or datetime.now(TZ)).astimezone(TZ)
    if phase != "CLOSED" or local.hour * 60 + local.minute < 17 * 60 + 15:
        return "BOOT_CALENDAR"
    try:
        import ak_byma_calendar as calendar
        if (local.year in calendar.ANIOS_AUDITADOS and
                calendar.es_dia_habil_operativo(local.date())):
            return "MARKET_CLOSED"
    except Exception:
        pass
    return "BOOT_CALENDAR"


def _candidate_universe(*, include_reference_indices=False):
    """Plan de discovery automático.

    No usa watchlist/tickers versionados como autoridad. Los prefijos se generan
    por código y rotan; PPI confirma existencia. IOL/BYMA se fusionan después
    como fuentes complementarias y nunca reemplazan un campo PPI presente.
    """
    return ppi_query_plan(day=datetime.now(TZ).date(),
                          include_reference_indices=include_reference_indices)


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


def normalize_quote(symbol, asset_class, settlement, current, book, *, metadata=None, identity=None):
    terms = financial_catalog.quote_terms(metadata)
    expected = (symbol, asset_class,
                identity[2] if identity is not None else terms.get("market"),
                identity[3] if identity is not None else terms.get("currency"),
                settlement)
    assert_native_ppi_response_identity(current, expected)
    assert_native_ppi_response_identity(book, expected)
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
                 **terms)


def _ppi_native_book_identity(store, symbol, asset_class, settlement, identity=None):
    """Resolve a unique primary PPI five-key before binding diagnostic Book1.

    This is a post-read witness only. Ambiguous identities never acquire
    capacity or trading authority from the optional local journal.
    """
    try:
        with store.connect() as connection:
            rows = connection.execute("""SELECT market,currency
              FROM financial_instrument_catalog
              WHERE ticker=? AND instrument_type=? AND settlement=?
                AND settlement_source='PPI_FIELD'
              LIMIT 2""", (symbol, asset_class, settlement)).fetchall()
            if not rows:
                candidates = connection.execute("""SELECT ticker,instrument_type,market,currency,
                    settlement,settlement_source,metadata_json
                  FROM financial_instrument_catalog
                  WHERE ticker=? AND instrument_type=? AND settlement=?
                    AND status='AVAILABLE' AND settlement_source='REQUEST_CANDIDATE'
                    AND market NOT IN ('','UNKNOWN','NO_VERIFICADO')
                    AND currency NOT IN ('','UNKNOWN','NO_VERIFICADO')
                  LIMIT 2""", (symbol, asset_class, settlement)).fetchall()
                if len(candidates) == 1:
                    record = dict(candidates[0])
                    market, currency = record["market"], record["currency"]
                    if (not financial_catalog.ppi_full_key_evidence_reason(record, database=store.path)
                            and financial_catalog._unique_native_catalog_scope(
                                connection, symbol, asset_class, market, currency)):
                        rows = [(market, currency)]
    except (sqlite3.Error, OSError, ValueError, TypeError):
        raise ValueError("PPI_BOOK_PRIMARY_CATALOG_IDENTITY_UNVERIFIED") from None
    if len(rows) != 1:
        raise ValueError("PPI_BOOK_PRIMARY_IDENTITY_AMBIGUOUS")
    market, currency = (str(value or "").strip().upper() for value in rows[0])
    if (not market or market == "UNKNOWN"
            or not currency or currency == "UNKNOWN"):
        raise ValueError("PPI_BOOK_PRIMARY_CATALOG_IDENTITY_UNVERIFIED")
    exact = (symbol, asset_class, market, currency, settlement)
    if identity is not None:
        if not isinstance(identity, (tuple, list)) or len(identity) != 5:
            raise ValueError("PPI_BOOK_REQUEST_SCOPE_IDENTITY_INVALID")
        if tuple(identity) != exact:
            raise ValueError("PPI_BOOK_REQUEST_SCOPE_IDENTITY_MISMATCH")
    return exact


def _read_iol_scanner_quote(reader, store, *, request, identity, priority, catalog_read_error):
    from bl_candle_engine import record_iol_trade_sample
    from bs_instrument_contracts import aware_datetime
    from iol_rest_readonly_rc6 import IOLRestError
    from rc6_dynamic_universe.routing import EQUITY_FAMILIES
    from rc6_fixed_income_paper_strategy import FAMILIES as FIXED_INCOME_FAMILIES

    if (not identity or len(identity) != 5
            or (identity[0], identity[1], identity[4]) != request):
        raise IOLRestError("IOL_GATEWAY_EXACT_IDENTITY_REQUIRED")
    checked = aware_datetime(now_iso())
    # Test comparators and specialized observations can retain their legacy
    # catalog path. The installed IOL spot profile uses its exact venue terms.
    from rc6_iol_market_gateway import iol_primary_enabled
    live_spot = iol_primary_enabled() and identity[1] in EQUITY_FAMILIES | FIXED_INCOME_FAMILIES | {"OPCIONES"}
    metadata = None
    if not catalog_read_error:
        if live_spot:
            metadata = financial_catalog.iol_spot_catalog(
                store, now=checked, identity=identity, include_contracts=True).get(tuple(identity))
        else:
            metadata = financial_catalog.lookup(store, *request, market=identity[2], currency=identity[3])
    terms = financial_catalog.quote_terms(metadata, now=checked, iol_live_settlement=live_spot)
    observation = reader.observe(identity, contract=terms.get("contract"), priority=priority)
    reason = ("CATALOG_READ_ERROR" if catalog_read_error else terms["opening_block_reason"])
    if metadata and metadata.get("raw", {}).get("iol_local_etf_reference") is True:
        try:
            financial_catalog.persist_iol_etf_observation(store, metadata, observation)
        except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
            store.event("IOL_ETF_CATALOG_WRITE_HOLD", type(exc).__name__)
    if identity[1] not in EQUITY_FAMILIES and not (live_spot and metadata is not None):
        reason = reason or "IOL_SPECIALIZED_ENTRY_EVIDENCE_REQUIRED"
    if reason:
        return replace(observation.quote, opening_block_reason=reason)
    if identity[1] in EQUITY_FAMILIES:
        try:
            record_iol_trade_sample(store, observation, as_of=now_iso())
        except (OSError, sqlite3.Error, ValueError, TypeError, ArithmeticError) as exc:
            with suppress(OSError, sqlite3.Error):
                store.event("IOL_CANDLE_TRADE_SAMPLE_HOLD", type(exc).__name__)
    return observation.quote


def _read_scanner_quote(reader, store, symbol, asset_class, settlement, *,
                        priority="DISCOVERY", identity=None, scanner_primary=None,
                        catalog_read_error=False):
    """Reuse one Current+Book GET pair for an optional private native Book1.

    A missing/ambiguous five-key cannot bind the diagnostic hash; it does not
    authorize retry, new provider requests, BUY or bypass PAPER holds.
    """
    from rc6_iol_market_gateway import IOLGatewayMarketReader
    from rc6_ppi_gateway_reader_adapter import SharedPPIReaderAdapter

    if isinstance(reader, IOLGatewayMarketReader):
        return _read_iol_scanner_quote(reader, store, request=(symbol, asset_class, settlement),
                                       identity=identity, priority=priority,
                                       catalog_read_error=catalog_read_error)

    scope = reader.read_scope(priority=priority, identity=identity) if hasattr(reader, "read_scope") else nullcontext()
    with scope:
        current_capture = None
        if isinstance(reader, SharedPPIReaderAdapter) and identity and not catalog_read_error:
            current_capture = retry_read(lambda: reader.current_with_optional_native_capture(
                symbol, asset_class, settlement), retries=1)
            current = current_capture["native_payload"]
        else:
            current = retry_read(lambda: reader.current(symbol, asset_class, settlement), retries=1)
        try:
            book = retry_read(lambda: reader.book(symbol, asset_class, settlement), retries=1)
        except Exception:
            if current_capture is not None and isinstance(current_capture.get("guard_wire_receipt"), dict):
                _record_scanner_current_sample(store, identity, current_capture, priority)
            raise
    metadata = None
    if not catalog_read_error:
        metadata = financial_catalog.lookup(store, symbol, asset_class, settlement,
            market=identity[2], currency=identity[3]) if identity else financial_catalog.lookup(store, symbol, asset_class, settlement)
    quote = normalize_quote(symbol, asset_class, settlement, current, book,
        metadata=metadata, identity=identity)
    if catalog_read_error:
        # A verified Gateway request for an opened position can still supply
        # an EXIT book when PIT metadata is unreadable. It grants no entry.
        quote = replace(quote, market=identity[2], currency=identity[3],
                        opening_block_reason="CATALOG_READ_ERROR")
    if scanner_primary is None or (priority == "OPENED_CRITICAL" and identity is None):
        return quote
    try:
        exact = _ppi_native_book_identity(
            store, symbol, asset_class, settlement, identity)
        currency, market = quote.monetary_identity()
        if (quote.symbol, quote.asset_class, market, currency, quote.settlement) != exact:
            return quote
    except (TypeError, ValueError, AttributeError):
        return quote
    try:
        capture = getattr(scanner_primary, "capture_raw_book", None)
        result = (capture(quote, raw_book=book,
                          request=(symbol, asset_class, settlement), identity=exact,
                          observed_at=quote.observed_at)
                  if callable(capture) else None)
        if isinstance(result, dict) and result.get("status") == "CAPTURED":
            native_sha = result.get("event_sha256")
            if (isinstance(native_sha, str) and len(native_sha) == 64
                    and all(char in "0123456789abcdef" for char in native_sha)
                    and not quote.time_error(now_iso(), require_trade=False)):
                return replace(quote, ppi_book1_event_sha256=native_sha)
        if not isinstance(result, dict) or result.get("status") not in {"CAPTURED", "DEDUPLICATED"}:
            status = result.get("status") if isinstance(result, dict) else None
            reason = result.get("reason") if isinstance(result, dict) else None
            if status not in {"CAPTURE_INVALID_HOLD", "CAPTURE_CAPACITY_HOLD", "CAPTURE_INTEGRITY_HOLD"}:
                status = "CAPTURE_UNVERIFIED_HOLD"
            if (not isinstance(reason, str) or not 1 <= len(reason) <= 96
                    or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in reason)):
                reason = "PPI_BOOK_CAPTURE_REASON_UNVERIFIED"
            store.event("PPI_SCANNER_NATIVE_BOOK_JOURNAL_HOLD", status + ":" + reason)
    except Exception as origin_error:
        try:
            store.event("PPI_SCANNER_NATIVE_BOOK_JOURNAL_ERROR", type(origin_error).__name__)
        except Exception:
            pass
    return quote


def _record_scanner_current_sample(store, identity, capture, priority):
    """Preserve a guarded Current for the existing sampled candle queue."""
    from bl_candle_engine import record_guarded_current_sample

    symbol, family, market, currency, settlement = identity
    try:
        _ppi_native_book_identity(store, symbol, family, settlement, identity)
        record = financial_catalog.lookup(store, symbol, family, settlement,
                                          market=market, currency=currency)
        checked_at = now_iso()
        if financial_catalog.ppi_full_key_evidence_reason(record, database=store.path, now=checked_at):
            return
        contract = financial_catalog.quote_terms(record).get("contract")
        record_guarded_current_sample(store, identity, capture, priority=priority,
                                      as_of=checked_at, cash_multiplier=str(
                                          getattr(contract, "cash_multiplier", "UNKNOWN")))
    except (OSError, sqlite3.Error, RuntimeError, ValueError, TypeError, KeyError, ArithmeticError) as exc:
        try:
            store.event("PPI_CANDLE_CURRENT_OBSERVATION_HOLD", type(exc).__name__)
        except (OSError, sqlite3.Error):
            pass


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


def _observe_structured_public_capture(store):
    """Consume the host's single public-source publication without HTTP/write.

    The host collector is the only writer of rc6_public_sources_latest.json.
    The observer must not fetch and atomically replace the same file: two
    independent writers used the same .tmp path and raced every 15 minutes.
    """
    try:
        stat = PUBLIC_CAPTURE_PATH.stat()
        if not 0 < stat.st_size <= PUBLIC_CAPTURE_MAX_BYTES:
            raise ValueError("PUBLIC_CAPTURE_SIZE_INVALID")
        age = time.time() - stat.st_mtime
        if not -60 <= age <= PUBLIC_CAPTURE_MAX_AGE_SECONDS:
            raise ValueError("PUBLIC_CAPTURE_MISSING_OR_STALE")
        result = json.loads(PUBLIC_CAPTURE_PATH.read_text(encoding="utf-8"))
        if not isinstance(result, dict) or not isinstance(result.get("sources"), list):
            raise ValueError("PUBLIC_CAPTURE_SCHEMA_INVALID")
        byma = next((item for item in result["sources"]
                     if isinstance(item, dict) and item.get("source") == "BYMA"), {})
        count = int(byma.get("record_count") or 0)
        if count < 0:
            raise ValueError("PUBLIC_CAPTURE_COUNT_INVALID")
        ok = byma.get("status") == "SCRAPED_PUBLIC_DATA"
        state = "VERDE" if ok else "AMARILLO"
        detail = (f"Captura pública compartida, sólo lectura: {count} instrumentos; "
                  f"age_s={int(age)}; SHADOW_ONLY; single_writer=host_collector.")
        _health(store, "BYMA_STRUCTURED_CAPTURE", state, detail,
                "BYMA Open Data", success=ok)
        _sync_state(store, "BYMA_STRUCTURED_CAPTURE", state, count, detail, success=ok)
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        # A missing or stale publication never authorizes market readiness.
        detail = "PUBLIC_CAPTURE_UNVERIFIED:" + type(exc).__name__
        _health(store, "BYMA_STRUCTURED_CAPTURE", "ROJO", detail, "BYMA Open Data")
        _sync_state(store, "BYMA_STRUCTURED_CAPTURE", "ROJO", 0, detail)


def _public_probe(store, *, structured=False):
    if structured:
        return _observe_structured_public_capture(store)
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
    return None


def _claim_command(store):
    with store.connect() as c:
        # The ordinary scanner poll is empty.  An unconditional BEGIN
        # IMMEDIATE every five seconds would reserve the sole SQLite writer
        # despite there being no command to claim.  The first look is only a
        # hint: ALWAYS reselect under the writer lock before claiming one.
        pending = c.execute(
            "SELECT 1 FROM observer_commands WHERE status='QUEUED' LIMIT 1"
        ).fetchone()
        if pending is None:
            return None
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


class _CatalogGatewayDisconnected(RuntimeError):
    """Catalog reads lost their gateway pin before the first catalog write."""


@contextmanager
def _candidate_publication(store):
    """Mark discovery, policy and projection as one incomplete publication.

    Catalog writers are serialized between the observer and deploy runner.
    A nested complementary pass finishes the last candidate write before the
    outer download records its health/source-sync receipts.
    """
    started_at = now_iso()
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""CREATE TABLE IF NOT EXISTS candidate_refresh_state(
            id INTEGER PRIMARY KEY CHECK(id=1),state TEXT NOT NULL,
            checked_at TEXT NOT NULL)""")
        previous = connection.execute(
            "SELECT state,checked_at FROM candidate_refresh_state WHERE id=1"
        ).fetchone()
        connection.execute("""INSERT INTO candidate_refresh_state VALUES(1,'RUNNING',?)
            ON CONFLICT(id) DO UPDATE SET state=excluded.state,
            checked_at=excluded.checked_at""", (started_at,))
    try:
        yield
    except BaseException as error:
        try:
            with store.connect() as connection:
                if isinstance(error, _CatalogGatewayDisconnected):
                    if previous is None:
                        connection.execute("""DELETE FROM candidate_refresh_state
                            WHERE id=1 AND state='RUNNING' AND checked_at=?""", (started_at,))
                    else:
                        connection.execute("""UPDATE candidate_refresh_state SET state=?,checked_at=?
                            WHERE id=1 AND state='RUNNING' AND checked_at=?""",
                            (*previous, started_at))
                else:
                    connection.execute("UPDATE candidate_refresh_state SET state='FAILED' WHERE id=1")
        except Exception as marker_error:
            error.add_note("CANDIDATE_REFRESH_FAILURE_MARKER:" + type(marker_error).__name__)
        raise
    else:
        with store.connect() as connection:
            connection.execute("UPDATE candidate_refresh_state SET state='COMPLETE' WHERE id=1")


def _download_catalog(reader, store):
    try:
        with _candidate_publication(store):
            return _download_catalog_generation(reader, store)
    except _CatalogGatewayDisconnected as exc:
        reason = str(exc)
        _sync_state(store, "PPI_PRODUCTION_CATALOG", CATALOG_GATEWAY_DEFERRED_STATUS, 0, reason)
        _health(store, "PPI_PRODUCTION_CATALOG", "AMARILLO", reason, "PPI Producción")
        store.event("CATALOG_DISCOVERY_DEFERRED", reason)
        raise


def _download_catalog_generation(reader, store):
    """Publica un lote atómico; conserva registros previos como STALE.

    Los filtros de búsqueda y los instrumentos devueltos son entidades
    diferentes. No se inventa una especie por haber consultado un prefijo.
    """
    failures = available = 0
    downloaded = now_iso()
    run_id = uuid.uuid4().hex
    found, query_results = {}, []
    configuration = None
    types_by_family, markets_by_key = {}, {}
    provider_queries = set()
    deferred_reason = ""
    if hasattr(reader, "market_configuration"):
        try:
            configuration = financial_catalog.validate_configuration(reader.market_configuration())
        except Exception as exc:
            reason = _scanner_ppi_error(exc)
            store.event("CATALOG_CONFIGURATION_UNAVAILABLE", reason)
            if reason in CATALOG_PROVIDER_BACKPRESSURE:
                deferred_reason = reason
            elif _scanner_gateway_reader_lost(exc):
                raise _CatalogGatewayDisconnected(reason) from exc
        else:
            for literal in configuration["instrument_types"]:
                types_by_family.setdefault(canonical_family(literal), []).append(literal)
            for literal in configuration["markets"]:
                markets_by_key.setdefault(canonical_market(literal), []).append(literal)
    plan = (_candidate_universe(include_reference_indices=True)
            if "INDICES" in types_by_family else _candidate_universe())
    for ticker_query, provider_type, fallback_settlement, market_query, _, instrument_type in plan:
        if deferred_reason:
            break
        status, detail = "EMPTY_FILTER_RESULT", "Este filtro no devolvió coincidencias; no prueba indisponibilidad."
        count = 0
        name_query = ticker_query
        if configuration is not None:
            declared_types = types_by_family.get(instrument_type, [])
            if provider_type not in configuration["instrument_types"] and len(declared_types) == 1:
                provider_type = declared_types[0]
            declared_markets = markets_by_key.get(canonical_market(market_query), [])
            if market_query not in configuration["markets"] and len(declared_markets) == 1:
                market_query = declared_markets[0]
            missing = ('TYPE' if provider_type not in configuration['instrument_types'] else
                       'MARKET' if market_query not in configuration['markets'] else None)
            if missing:
                query_results.append((run_id, ticker_query, name_query, instrument_type,
                    market_query, missing + '_NOT_ENUMERATED', 0, 'No se consultó: ausente de la configuración actual.'))
                continue
        query_key = (ticker_query, provider_type, market_query)
        if query_key in provider_queries:
            continue
        provider_queries.add(query_key)
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
                    if record['instrument_type'] not in types_by_family:
                        record['capability'] = 'TYPE_NOT_ENUMERATED'
                    elif market_query not in configuration['markets']:
                        record['capability'] = 'MARKET_NOT_ENUMERATED'
                key = tuple(record[k] for k in ("ticker", "instrument_type", "market", "currency", "settlement"))
                if not financial_catalog.retain_ppi_settlement_witness(found.get(key), record):
                    found[key] = record
                count += 1
            if count:
                status, detail, available = "AVAILABLE", f"{count} coincidencia(s).", available + 1
            elif records:
                status, detail = "INVALID_METADATA", "Respuestas sin ticker/clase verificables."
        except Exception as exc:
            failures += 1
            detail = _scanner_ppi_error(exc)
            if detail in CATALOG_PROVIDER_BACKPRESSURE:
                deferred_reason = detail
            elif _scanner_gateway_reader_lost(exc):
                raise _CatalogGatewayDisconnected(detail) from exc
            status = CATALOG_DEFERRED_STATUS if deferred_reason else "ERROR"
        query_results.append((run_id, ticker_query, name_query, instrument_type, market_query, status, count, detail))
        if CATALOG_QUERY_SLEEP_SECONDS and not deferred_reason:
            time.sleep(CATALOG_QUERY_SLEEP_SECONDS)
    if deferred_reason:
        store.event("CATALOG_DISCOVERY_DEFERRED", deferred_reason)
        if not found:
            _sync_state(store, "PPI_PRODUCTION_CATALOG", CATALOG_DEFERRED_STATUS, 0, deferred_reason)
            _health(store, "PPI_PRODUCTION_CATALOG", "AMARILLO", deferred_reason, "PPI Producción")
            return 0
    # PPI keeps identity authority.  Fresh complementary evidence may complete
    # a missing financial contract on that exact identity; otherwise it can only
    # widen the observed SHADOW universe.
    try:
        complements = list(complementary_discovery("/app/data/market"))
        for raw in complements:
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
            existing = c.execute("""SELECT * FROM financial_instrument_catalog
              WHERE ticker=? AND instrument_type=? AND market=? AND currency=? AND settlement=?""",
              (record["ticker"],record["instrument_type"],record["market"],record["currency"],record["settlement"])).fetchone()
            if discovery_source != "PPI_PRIMARY" and existing:
                try:
                    prior_meta=json.loads(existing["metadata_json"] or "{}")
                except (TypeError,ValueError,json.JSONDecodeError):
                    prior_meta={}
                if str(prior_meta.get("_discovery_source") or "").upper()=="PPI_PRIMARY":
                    # Complementary evidence may widen/fill elsewhere, but cannot
                    # replace a still-retained primary catalog identity.
                    continue
            if existing and financial_catalog.retain_ppi_settlement_witness(dict(existing), record):
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
    # Materialize policies again from the just-persisted PPI full keys, then
    # reconcile immediately.  This closes the race where a daily refresh could
    # replace READY capabilities written moments earlier by the deploy runner.
    try:
        import cr_contract_evidence_v2_mass_hf6 as evidence_v2_mass
        mass_result = evidence_v2_mass.collect(
            store, run_id="catalog-refresh-v2-mass-" + uuid.uuid4().hex)
        promoted_after_refresh = _reconcile_complementary_catalog(store)
        store.event("CATALOG_PAPER_POLICY_MATERIALIZATION", json.dumps({
            "promoted": promoted_after_refresh,
            "catalog_available": mass_result.get("catalog_available"),
            "real_routes_used": mass_result.get("real_routes_used"),
        }, sort_keys=True))
    except Exception as exc:
        store.event("CATALOG_PAPER_POLICY_MATERIALIZATION_ERROR", type(exc).__name__)
        raise
    total = len(found)
    rofex_available = sum(r["market"] in {"ROFEX", "A3"} for r in found.values())
    state = "VERDE" if available and not failures and configuration is not None else "AMARILLO" if available else "ROJO"
    detail = (f"{available} búsquedas con coincidencias; {total} identidades únicas; "
              f"{failures} búsquedas fallidas. Ticker y Name siempre no vacíos. "
              f"Configuración actual: {'verificada' if configuration is not None else 'no disponible'}.")
    if deferred_reason:
        detail += f" Lote aplazado: {deferred_reason}."
    _sync_state(store, "PPI_PRODUCTION_CATALOG", CATALOG_DEFERRED_STATUS if deferred_reason else state,
                total, detail, success=bool(available) and not deferred_reason)
    _health(store, "PPI_PRODUCTION_CATALOG", state, detail, "PPI Producción",
            success=bool(available) and not deferred_reason)
    rofex_state = "AMARILLO" if rofex_available else "GRIS"
    rofex_detail = (f"{rofex_available} contrato(s) visible(s); contexto únicamente. "
                    "No se simulan futuros sin multiplicador y margen atribuible."
                    if rofex_available else
                    "Sin contrato ROFEX validado. Contexto desactivado; no afecta contado.")
    _health(store, "ROFEX_MARKETDATA", rofex_state, rofex_detail,
            "PPI Producción / ROFEX", success=bool(rofex_available))
    return total


def _reconcile_complementary_catalog(store):
    with _candidate_publication(store):
        return _reconcile_complementary_catalog_generation(store)


def _reconcile_complementary_catalog_generation(store):
    """Apply complements in strict PPI-primary -> IOL -> BYMA order.

    PPI rows remain the canonical identity whenever they exist. IOL and BYMA
    are gap-fill sources: they may add missing contract/freshness evidence but
    never overwrite explicit higher-priority identity fields.
    """
    from time import sleep
    from rc6_contract_reconciliation_guard import (
        reconciliation_allowed, select_verified_primary)

    market_root = Path(os.getenv("POROTA_MARKET_DATA_ROOT", "/app/data/market"))
    try:
        iol_path = market_root / "iol_family_reference_latest.json"
        if iol_path.is_file():
            payload = json.loads(iol_path.read_text(encoding="utf-8"))
            with store.connect() as connection:
                ppi_catalog = [dict(row) for row in connection.execute(
                    "SELECT * FROM financial_instrument_catalog")]
            from rc6_broker_parity_evidence import ingest_iol_structured_cache
            ingest_iol_structured_cache(store, payload, ppi_catalog)
    except Exception as exc:
        store.event("IOL_EVIDENCE_INGEST_UNAVAILABLE", type(exc).__name__)
    try:
        complementary = complementary_discovery(market_root)
        from rc6_contract_bridge import complements_from_store, is_static_caucion_policy
        complementary += complements_from_store(store)
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
        candidate_tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('candidate_universe','candidate_identity_v2')")}

        def pause_projection(record):
            # Close only an identity whose catalogue or retry status is changing.
            # Other verified PAPER identities stay available through this long
            # off-session rebuild. The final projection restores exact status.
            ticker, family, market = (record[k] for k in (
                "ticker", "instrument_type", "market"))
            if "candidate_universe" in candidate_tables:
                connection.execute("""UPDATE candidate_universe
                  SET can_simulate=0,status='PAUSED_EXPLICIT',detail='PAUSED_EXPLICIT:CATALOG_RECONCILIATION'
                  WHERE ticker=? AND instrument_type=? AND market=?""",
                  (ticker, family, market))
            if "candidate_identity_v2" in candidate_tables:
                connection.execute("""UPDATE candidate_identity_v2
                  SET can_simulate=0,status='PAUSED_EXPLICIT',detail='PAUSED_EXPLICIT:CATALOG_RECONCILIATION'
                  WHERE ticker=? AND instrument_type=? AND market=? AND currency=? AND settlement=?""",
                  (ticker, family, market, record["currency"], record["settlement"]))

        for offset in range(0, len(complementary), 32):
            connection.execute("BEGIN IMMEDIATE")
            for raw in complementary[offset:offset + 32]:
                negative_bridge = (raw.get("source") == "CONTRACT_EVIDENCE_V2"
                                   and (raw.get("contract_bridge") or {}).get("status") == "BLOCKED")
                normalized_bridge = (
                    raw.get("source") == "CONTRACT_EVIDENCE_V2"
                    and (raw.get("contract_bridge") or {}).get("status") == "NORMALIZED"
                )
                if raw.get("instrument_type") == "CAUCIONES":
                    static_caucion_policy = normalized_bridge and is_static_caucion_policy(raw)
                    static_v2_contract = static_caucion_policy
                else:
                    static_caucion_policy = False
                    static_v2_contract = normalized_bridge and (
                        raw.get("financial_contract_v17") or raw.get("paper_family_contract_v1"))
                if not negative_bridge and not static_v2_contract and not financial_catalog.complementary_is_fresh(
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
                # A stale alias does not overrule a unique current PPI identity
                # when ISIN, quote basis and all other dimensions prove equality.
                matches = select_verified_primary(matches, raw, checked_at=checked)
                if len(matches) > 1:
                    family = financial_catalog.canonical_family(
                        raw.get("instrument_type") or raw.get("asset_type") or raw.get("family"))
                    source = financial_catalog.complement_source(raw)
                    # The reconciliation holds BEGIN IMMEDIATE, so use that
                    # connection for the event too; PaperStore.event() opens a
                    # second writer and would self-lock SQLite here.
                    connection.execute("INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)",
                      (checked, "PAPER_ENGINE", "COMPLEMENTARY_IDENTITY_AMBIGUOUS", None,
                       f"{ticker}|{family}|{source}|matches={len(matches)}"))
                    # Keep every colliding primary identity in the explicit retry
                    # ledger.  No row is selected or mutated while the provider
                    # evidence cannot distinguish the canonical PPI identity.
                    for candidate in matches:
                        pause_projection(candidate)
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
                    # A static PAPER policy can complete an existing PPI
                    # identity only. The quote TTL remains in the sweep.
                    if static_caucion_policy:
                        continue
                    # A complement-only identity may enter only if that provider
                    # supplied a complete, internally consistent PAPER contract.
                    try:
                        complement = financial_catalog.normalize_complementary_record(
                            raw, checked, "COMPLEMENTARY_RECONCILE")
                    except (ValueError, TypeError):
                        continue
                    if (complement.get("status") == "AVAILABLE"
                            and str(complement.get("capability") or "").startswith("READY_PAPER_")):
                        pause_projection(complement)
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
                if static_caucion_policy and (primary.get("status") != "AVAILABLE"
                                              or financial_catalog.ppi_full_key_evidence_reason(primary)):
                    continue

                # A policy complement may satisfy a missing PAPER term, but it
                # cannot overturn an explicit primary-provider rejection such as
                # TYPE_NOT_ENUMERATED or UNSUPPORTED_FAMILY.
                # A local review may be reconsidered only with complete, exact
                # canonical V2 evidence. Genuine PPI vetoes and material conflicts
                # cannot be cleared by this reconciliation path.
                if not reconciliation_allowed(primary, raw, checked_at=checked):
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

                pause_projection(primary)
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

            connection.commit()
            sleep(.005)

        # Persist the exact unresolved contract condition even when IOL/BYMA
        # supplied no usable row in this cycle. This is retry state, not a
        # rejection: the scheduler and IOL timer will reconcile it again.
        retry_rows = connection.execute("""SELECT ticker,instrument_type,market,currency,
          settlement,capability,status,last_seen_at,metadata_json
          FROM financial_instrument_catalog""").fetchall()
        for offset in range(0, len(retry_rows), 32):
            connection.execute("BEGIN IMMEDIATE")
            for row in retry_rows[offset:offset + 32]:
                (ticker, family, market, currency, settlement, capability, status,
                 last_seen, metadata_json) = row
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
                try:
                    metadata = json.loads(metadata_json or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    metadata = {}
                bridge_gaps = ((metadata.get("_contract_bridge") or {}).get("gaps", [])
                               if isinstance(metadata, dict) else [])
                missing = sorted({str(gap).split(":", 1)[1]
                                  for gap in bridge_gaps
                                  if str(gap).startswith("MISSING:")})
                if missing:
                    reason = "MISSING_FIELDS:" + ",".join(missing)
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

            connection.commit()
            sleep(.005)

        financial_catalog.sync_candidate_universe(connection, checked, bounded_batch=64)
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


def _periodic_complementary_reconcile(store, phase, last_completed):
    """Defer the full-catalog writer until live market work has finished.

    This reconciliation holds BEGIN IMMEDIATE while it projects the catalog.
    During OPEN it blocked PAPER health and SHADOW readers for minutes.
    Existing identities retain their normal freshness and admission checks;
    missing complements remain unavailable.
    """
    interval = (OFFHOURS_RECONCILE_SECONDS if phase == "CLOSED"
                else COMPLEMENTARY_RECONCILE_SECONDS)
    if phase == "OPEN" or time.time() - last_completed < interval:
        return last_completed
    _reconcile_complementary_catalog(store)
    if phase == "CLOSED":
        memory = _release_idle_catalog_heap()
        store.event("CLOSED_CATALOG_HEAP_RELEASE", json.dumps(memory, sort_keys=True))
    return time.time()


def _caucion_blocked_closed_catalog_tick(store, phase, last_attempt):
    """A blocked PAPER caucion fee ledger must not starve CLOSED catalog repair.

    The local catalog/evidence publication does not grant PAPER admission,
    reach a broker, or trigger any PPI network call. OPEN and T+1 never write.
    """
    if phase != "CLOSED":
        return last_attempt
    try:
        return _periodic_complementary_reconcile(store, phase, last_attempt)
    except Exception as exc:
        reason = ("SQLITE_OPERATIONAL_ERROR" if isinstance(exc, sqlite3.OperationalError)
                  else "CATALOG_RECONCILE_ERROR")
        try:
            store.event("CAUCION_BLOCKED_CATALOG_RECONCILE_ERROR", reason)
        except Exception:
            print("CAUCION_BLOCKED_CATALOG_RECONCILE_ERROR:" + reason,
                  file=sys.stderr, flush=True)
        return time.time()


def _release_idle_catalog_heap():
    """Return free catalog allocations to Linux after the closed-session pass.

    Live broker/reader objects remain untouched. Report the actual RSS change:
    an allocator trim does not imply that memory was reclaimed.
    """
    def rss_kib():
        try:
            with open("/proc/self/status", encoding="ascii") as status:
                for line in status:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1])
        except (OSError, ValueError, IndexError):
            pass
        return None

    before = rss_kib()
    import gc
    gc.collect()
    available = False
    try:
        import ctypes
        trim = ctypes.CDLL(None).malloc_trim
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
        trim(0)
        available = True
    except (AttributeError, OSError):
        pass
    return {"rss_before_kib": before, "rss_after_kib": rss_kib(),
            "allocator_trim_available": available}


def _eligible_symbols(store):
    """Compatibilidad para consumidores de las solicitudes de tres campos."""
    return tuple(_eligible_scanner_identities(store))


def _eligible_scanner_identities(store):
    """Map scanner requests to their exact PIT identities in rotation order."""
    family_order = (
        "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
        "OBLIGACIONES", "OPCIONES", "FUTUROS",
    )
    allowed_sql = ",".join("?" for _ in family_order)
    with store.connect() as c:
        full_keys = c.execute("""SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='candidate_identity_v2'""").fetchone()
        if full_keys:
            # A held sibling with a real contract still makes the three-field
            # request ambiguous. Retired diagnostic placeholders do not.
            aliases = financial_catalog.scanner_diagnostic_aliases_sql(c)
            rows = c.execute(f"""WITH diagnostic_aliases AS ({aliases})
              SELECT p.ticker,p.instrument_type,p.market,p.currency,p.settlement
              FROM candidate_identity_v2 p LEFT JOIN diagnostic_aliases h
                USING(ticker,instrument_type,market,currency,settlement)
              WHERE UPPER(TRIM(p.instrument_type)) IN ({allowed_sql}) AND h.ticker IS NULL
              GROUP BY UPPER(TRIM(p.ticker)),UPPER(TRIM(p.instrument_type)),UPPER(TRIM(p.settlement))
              HAVING COUNT(*)=1 AND MAX(p.can_simulate)=1 AND MAX(p.status)='AVAILABLE'
              ORDER BY CASE WHEN p.ticker IN ('GGAL','AAPL') THEN 0 ELSE 1 END,
                       p.instrument_type,p.ticker,p.settlement""", family_order).fetchall()
        else:
            rows = c.execute(f"""SELECT ticker,instrument_type,market,NULL,settlement
              FROM candidate_universe
              WHERE can_simulate=1 AND status='AVAILABLE'
                AND UPPER(instrument_type) IN ({allowed_sql})
              ORDER BY CASE WHEN ticker IN ('GGAL','AAPL') THEN 0 ELSE 1 END,
                       instrument_type,ticker,settlement""", family_order).fetchall()
    groups = {family: {} for family in family_order}
    for row in rows:
        key = tuple(str(part or "").strip().upper() for part in row)
        ticker, family, _, _, settlement = key
        # The old summary is preserved for diagnostics, without inventing CCY.
        exact = key if all(v not in {"", "UNKNOWN", "NO_VERIFICADO"} for v in key) else None
        groups[family][(ticker, family, settlement)] = exact
    # Rotate all families in the existing order without repeatedly shifting lists.
    return dict(item for turn in zip_longest(*(iter(group.items()) for group in groups.values()))
                for item in turn if item is not None)


def _ppi_direct_rest_probe_candidates(store, *, limit=1, family=None, after=None,
                                      pending_settlement=False):
    """PPI-primary observation only, independent of simulation readiness.

    A zero-READY bootstrap must still be able to *observe* an unequivocal
    native PPI catalog identity. PPI's documented SearchInstrument response
    can omit settlement: REQUEST_CANDIDATE is a native GET hypothesis. A
    verified receipt can later attest the requested term independently.
    An old STALE PPI-primary row is likewise eligible only for this probe.
    Selection itself grants no trading or scanner authority.
    """
    if type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError("PPI_DIRECT_PROBE_BATCH_INVALID")
    if family is not None and (type(family) is not str or family not in OPERATIONAL_FAMILIES):
        raise ValueError("PPI_DIRECT_PROBE_FAMILY_INVALID")
    return _ppi_rest_probe_keys(store, limit=limit, family=family, after=after,
                                pending_settlement=pending_settlement)


def _ppi_rest_probe_keys(store, *, limit, family, after, pending_settlement):
    pending = ""
    contract_priority = ""
    archived_priority = ""
    if pending_settlement:
        # Only identities whose venue and currency can be attested should
        # consume discovery turns. A currency/venue sibling at another term
        # would invalidate the proof when it is published.
        pending = """AND f.status='AVAILABLE' AND f.settlement_source='REQUEST_CANDIDATE'
                  AND NOT EXISTS (
                    SELECT 1 FROM financial_instrument_catalog other
                    WHERE other.ticker=f.ticker AND other.instrument_type=f.instrument_type
                      AND other.status IN ('AVAILABLE','STALE')
                      AND other.market NOT IN ('','UNKNOWN','NO_VERIFICADO')
                      AND other.currency NOT IN ('','UNKNOWN','NO_VERIFICADO')
                      AND (other.market!=f.market OR other.currency!=f.currency)
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM candidate_identity_v2 p
                    WHERE p.ticker=f.ticker AND p.instrument_type=f.instrument_type
                      AND p.market=f.market AND p.currency=f.currency
                      AND p.settlement=f.settlement
                      AND p.status='AVAILABLE' AND p.can_simulate=1
                  )"""
        contract_priority = "CASE WHEN f.capability LIKE 'READY_PAPER_%' THEN 0 ELSE 1 END,"
        # A stored TRADE and Book select the first key to revisit. They are
        # insufficient for live freshness or settlement authority.
        archived_priority = """EXISTS (
                    SELECT 1 FROM market_snapshots q
                    WHERE q.symbol=f.ticker AND q.asset_class=f.instrument_type
                      AND q.settlement=f.settlement AND q.market=f.market
                      AND q.currency=f.currency AND q.source='PRODUCTION_PAPER'
                      AND q.last_kind='TRADE' AND q.trade_at IS NOT NULL
                      AND q.book_at IS NOT NULL
                  ) DESC,"""
    if after is None:
        order = f"""CASE WHEN f.status='AVAILABLE' THEN 0 ELSE 1 END,
                   {contract_priority}
                   {archived_priority}
                   CASE WHEN f.ticker IN ('GGAL','AL30','AAPL') THEN 0 ELSE 1 END,
                   f.last_seen_at DESC,f.instrument_type,f.ticker,f.settlement"""
        position = ()
    else:
        ticker, _, market, currency, settlement = after
        # Stable order within a family even if a catalog refresh changes last_seen_at.
        order = """(f.ticker,f.market,f.currency,f.settlement) <= (?,?,?,?),
                   f.ticker,f.market,f.currency,f.settlement"""
        position = (ticker, market, currency, settlement)
    try:
        with store.connect() as c:
            rows = c.execute(f"""WITH unique_identity AS (
                  SELECT ticker,instrument_type,settlement
                  FROM financial_instrument_catalog
                  WHERE status IN ('AVAILABLE','STALE')
                    AND (? IS NULL OR instrument_type=?)
                    AND settlement_source IN ('PPI_FIELD','REQUEST_CANDIDATE')
                    AND json_valid(metadata_json)
                    AND json_extract(metadata_json,'$._discovery_source')='PPI_PRIMARY'
                  GROUP BY ticker,instrument_type,settlement HAVING COUNT(*)=1
                )
                SELECT f.ticker,f.instrument_type,f.market,f.currency,f.settlement
                FROM financial_instrument_catalog f JOIN unique_identity u
                  ON f.ticker=u.ticker AND f.instrument_type=u.instrument_type
                 AND f.settlement=u.settlement
                WHERE f.status IN ('AVAILABLE','STALE')
                  AND f.settlement_source IN ('PPI_FIELD','REQUEST_CANDIDATE')
                  AND f.market NOT IN ('','UNKNOWN') AND f.currency NOT IN ('','UNKNOWN')
                  AND (? IS NULL OR f.instrument_type=?)
                  AND json_valid(f.metadata_json)
                  AND json_extract(f.metadata_json,'$._discovery_source')='PPI_PRIMARY'
                  {pending}
                ORDER BY {order}
                LIMIT ?""", (family, family, family, family, *position, limit)).fetchall()
        return [tuple(str(v).strip().upper() for v in row) for row in rows]
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return []


def _safe_ppi_evidence_timestamp(value):
    """Persist a bounded ISO clock, never an arbitrary provider body field."""
    if not isinstance(value, str) or not 10 <= len(value) <= 64:
        return "UNVERIFIED"
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None or not 2000 <= stamp.year <= 2100:
            return "UNVERIFIED"
        return stamp.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, TypeError, OverflowError):
        return "UNVERIFIED"


def _ppi_direct_rest_observe_only(reader, store, *, family=None, command_id=None,
                                  pre_wire=None, update_market_clock=False,
                                  confirm_settlement=False, after=None):
    """Read genuine native evidence without inventing provider-side fields.

    Spot/market-book families need Current+Book to claim a fresh executable
    *diagnostic* quote. FCI NAV and CAUCIONES rates are class-specific: a
    guarded Current is only a REFERENCE, never a PPI-signed NAV/TNA, Book,
    economic terms, PAPER_READY or an order. No second reader/login.
    """
    candidates = _ppi_direct_rest_probe_candidates(store, limit=1, family=family, after=after)
    if not candidates:
        return {"status": "CATALOG_IDENTITY_UNAVAILABLE", "paper_ready": False,
                "native_receipts": []}
    return _observe_native_candidate(
        reader, store, candidates[0], command_id=command_id, pre_wire=pre_wire,
        update_market_clock=update_market_clock, confirm_settlement=confirm_settlement)


def _observe_native_candidate(reader, store, key, *, command_id=None, pre_wire=None,
                              update_market_clock=False, confirm_settlement=False):
    ticker, family, market, currency, settlement = key
    observation = {"paper_ready": False}
    if confirm_settlement:
        # Rotation advances even when the attempted key has invalid market data.
        observation["identity"] = list(key)
        observation["settlement_attested"] = False
    witnessed = {}
    try:
        # Require the EXISTING native wire receipt contract. An SDK cache,
        # synthetic Book/Current or pre-send HTTP counter does not establish
        # freshness. No extra REST retry is made to obtain this evidence.
        capture = getattr(reader, "capture_guarded_native", None)
        if not callable(capture):
            raise ValueError("PPI_DIRECT_NATIVE_WIRE_WITNESS_REQUIRED")
        endpoints = (("current",) if family in _PPI_REFERENCE_OBSERVED
                     else ("current", "book"))
        for endpoint in endpoints:
            if pre_wire is not None:
                pre_wire()
            witness = capture(endpoint, key, priority="DISCOVERY")
            receipt = witness.get("guard_wire_receipt") if isinstance(witness, dict) else None
            if (not isinstance(receipt, dict)
                    or receipt.get("schema") != "RC6_PPI_NATIVE_WIRE_RECEIPT_V1"
                    or receipt.get("endpoint") != endpoint
                    or receipt.get("http_method") != "GET"
                    or receipt.get("http_status_code") != 200
                    or receipt.get("adapter_send_invoked") is not True
                    or receipt.get("adapter_response_completed") is not True
                    or receipt.get("request_identity_3key") != [ticker, family, settlement]
                    or receipt.get("claimed_five_key") != list(key)
                    or not isinstance(receipt.get("raw_body_sha256"), str)
                    or len(receipt["raw_body_sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in receipt["raw_body_sha256"])):
                raise ValueError("PPI_DIRECT_NATIVE_WIRE_WITNESS_INVALID")
            # Evidence of an actual HTTP200 must survive even if the *next*
            # REST GET fails or the paired quote is stale. Do not attribute
            # success to a synthetic/current-only Book or imply READY.
            witnessed[endpoint] = witness
            payload = witness.get("native_payload")
            native_time = (payload.get("date") if isinstance(payload, dict)
                           else None)
            settlement_status, settlement_literal = native_ppi_response_settlement(payload)
            store.event("PPI_DIRECT_REST_NATIVE_WIRE_WITNESS",
                        f"command_id={command_id or 0};{ticker}:{family}:{settlement};"
                        f"identity5={ticker}:{family}:{market}:{currency}:{settlement};"
                        f"endpoint={endpoint};http_status=200;"
                        f"wire_received_at_utc={_safe_ppi_evidence_timestamp(receipt.get('received_at_utc'))};"
                        f"market_timestamp_utc={_safe_ppi_evidence_timestamp(native_time)};"
                        f"response_settlement_status={settlement_status};"
                        f"response_settlement_literal={settlement_literal};"
                        f"wire_sha256={receipt['raw_body_sha256']};paper_ready=false")
            if endpoint == "current" and confirm_settlement and len(endpoints) == 2:
                from rc6_ppi_native_request_receipt import SETTLEMENT_PAIR_MAX_SECONDS

                try:
                    trade_at = datetime.fromisoformat(native_time.replace("Z", "+00:00"))
                    age = (datetime.fromisoformat(now_iso()) - trade_at).total_seconds()
                    valid = 0 <= age <= SETTLEMENT_PAIR_MAX_SECONDS and D(payload["price"]) > 0
                except (AttributeError, KeyError, TypeError, ValueError, ArithmeticError):
                    valid = False
                if not valid:
                    reason = "PPI_DISCOVERY_CURRENT_STALE_OR_INVALID"
                    store.event("PPI_DIRECT_REST_PROBE_HOLD", reason)
                    return {**observation, "status": "STALE_OR_INVALID", "reason": reason,
                            "native_receipts": list(witnessed)}
        if family in _PPI_REFERENCE_OBSERVED:
            # PPI SearchInstrument does not promise settlement, and funds/
            # money-market rates are not an equity two-sided order book.
            # Preserve the *actual* native Current receipt but do not insert
            # an executable Quote or infer NAV, TNA or a fund cutoff.
            payload = witnessed["current"].get("native_payload")
            try:
                if not isinstance(payload, dict):
                    raise ValueError("PPI_REFERENCE_CURRENT_PAYLOAD_INVALID")
                raw_at = payload.get("date")
                if not isinstance(raw_at, str) or not 10 <= len(raw_at) <= 60:
                    raise ValueError("PPI_REFERENCE_SOURCE_CLOCK_MISSING")
                mark_at = datetime.fromisoformat(raw_at.replace("Z", "+00:00"))
                if mark_at.tzinfo is None or mark_at.utcoffset() is None:
                    raise ValueError("PPI_REFERENCE_SOURCE_CLOCK_UNVERIFIED")
                age = (datetime.now(timezone.utc) - mark_at.astimezone(
                    timezone.utc)).total_seconds()
                price = Decimal(str(payload.get("price")))
                if (not 0 <= age <= 7 * 86400 or
                        not price.is_finite() or price <= 0):
                    raise ValueError("PPI_REFERENCE_SOURCE_STALE_OR_UNUSABLE")
            except (ValueError, TypeError, KeyError, OverflowError, ArithmeticError):
                store.event("PPI_DIRECT_REST_REFERENCE_HOLD",
                            "family=" + family + ";reason=REFERENCE_UNUSABLE")
                return {**observation, "status": "REFERENCE_UNUSABLE",
                        "native_receipts": list(witnessed)}
            status = _PPI_REFERENCE_OBSERVED[family]
            store.event("PPI_DIRECT_REST_REFERENCE_OBSERVED",
                        f"family={family};identity5={':'.join(key)};"
                        f"wire_sha256={witnessed['current']['guard_wire_receipt']['raw_body_sha256']};"
                        "basis=PPI_NATIVE_CURRENT_ONLY;paper_ready=false")
            return {**observation, "status": status, "identity": list(key),
                    "native_receipts": list(witnessed)}
        witnessed_current = witnessed["current"]
        witnessed_book = witnessed["book"]
        current = witnessed_current["native_payload"]
        book = witnessed_book["native_payload"]
        metadata = financial_catalog.lookup(store, ticker, family, settlement,
                                              market=market, currency=currency)
        quote = normalize_quote(ticker, family, settlement, current, book,
                                metadata=metadata, identity=key)
        # The exact PPI-primary catalog supplies Market/Currency even when
        # capability is not READY and lookup returns no tradeable contract.
        quote = replace(quote, market=market, currency=currency)
        quote.monetary_identity()  # reject any existing contract contradiction
        # HTTP200 on both endpoints proves wire reachability, not a usable
        # Current/Book market-data sample. Require an actual fresh Current
        # trade and a fresh two-sided Book with strictly positive size and a
        # non-crossed spread before labeling the *diagnostic* FRESH_OBSERVED.
        # This is still weaker than PIT/Book1/decision/Book2/costs/EXIT READY.
        if (quote.time_error(now_iso(), require_trade=True) or
                quote.last <= 0 or quote.bid <= 0 or quote.ask <= 0 or
                quote.bid_size <= 0 or quote.ask_size <= 0 or
                quote.bid > quote.ask):
            store.event("PPI_DIRECT_REST_PROBE_HOLD", "NATIVE_CURRENT_OR_BOOK_STALE_OR_INVALID")
            return {**observation, "status": "STALE_OR_INVALID",
                    "native_receipts": list(witnessed)}
        quote = replace(quote, opening_block_reason="PPI_DIRECT_REST_OBSERVE_ONLY")
        store.add_quote(quote)
        proof = witnessed_book.get("settlement_proof")
        if confirm_settlement and isinstance(proof, dict):
            try:
                same_pair = all(isinstance(proof.get(endpoint), dict)
                                and witnessed[endpoint]["guard_wire_receipt"].get("guard_budget_receipt_sha256")
                                and proof[endpoint].get("guard_sha256")
                                == witnessed[endpoint]["guard_wire_receipt"].get("guard_budget_receipt_sha256")
                                for endpoint in ("current", "book"))
                # Both signed references must correspond to this capture,
                # not another eligible Current/Book pair for the same key.
                confirmed = same_pair and financial_catalog.publish_native_settlement_receipt(
                    store, key, proof, now_iso())
                if confirmed:
                    observation["settlement_attested"] = True
                    store.event("PPI_NATIVE_SETTLEMENT_ATTESTED",
                                ":".join(key) + ";source=PPI_NATIVE_REQUEST_RECEIPT")
                    _record_scanner_current_sample(store, key, witnessed_current, "DISCOVERY")
            except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
                store.event("PPI_NATIVE_SETTLEMENT_PUBLISH_HOLD", type(exc).__name__)
        if update_market_clock:
            store.state(last_market_data_at=quote.observed_at)
        # Digest of the *actual* guarded GET body, never its raw JSON, URL
        # or broker credentials. Useful to reconcile with Deploy V2 evidence.
        store.event("PPI_DIRECT_REST_PROBE_OBSERVED",
                    f"{ticker}:{family}:{settlement}:read_only;"
                    f"current_wire_sha256={witnessed_current['guard_wire_receipt']['raw_body_sha256']};"
                    f"book_wire_sha256={witnessed_book['guard_wire_receipt']['raw_body_sha256']}")
        return {**observation, "status": "FRESH_OBSERVED", "identity": list(key),
                "native_receipts": list(witnessed)}
    except ValueError as error:
        if (str(error).startswith(("PPI_POSTCLOSE_", "PPI_OPEN_")) or
                str(error) == "PPI_SINGLE_SESSION_GLOBAL_UNVERIFIED"):
            store.event("PPI_DIRECT_REST_PROBE_HOLD", str(error))
            return {**observation, "status": "PRE_WIRE_GUARD_HOLD",
                    "reason": str(error), "native_receipts": list(witnessed)}
        reason = _scanner_ppi_error(error)
    except Exception as error:
        reason = _scanner_ppi_error(error)
    store.event("PPI_DIRECT_REST_PROBE_HOLD", reason)
    return {**observation, "status": "PROVIDER_OR_CONTRACT_HOLD",
            "reason": reason, "native_receipts": list(witnessed)}


def _discover_native_settlements(reader, store, *, slots, deadline, cursor, after, last_pulse):
    """Fill free scanner turns with guarded native reads during the same cycle."""
    exhausted = set()
    visited = set()
    planned = {}
    result = {"slots": slots, "attempted": 0, "fresh_pairs": 0, "attested": 0}

    def pre_wire():
        if STOP or time.monotonic() >= deadline or not _market_open():
            raise ValueError("PPI_OPEN_DISCOVERY_WINDOW_ELAPSED")
        if store.open_positions() or store.active_future_positions():
            raise ValueError("PPI_OPEN_EXIT_EXPOSURE_PRESENT")

    while (len(visited) < slots and len(exhausted) < len(PPI_DIAGNOSTIC_FAMILY_ROTATION)
           and not STOP and time.monotonic() < deadline):
        candidate_cursor = cursor
        family, cursor = _next_ppi_diagnostic_family(cursor)
        if family in exhausted:
            continue
        if family not in planned:
            previous = after.get(family)
            if previous is None:
                first = _ppi_direct_rest_probe_candidates(
                    store, family=family, after=previous, pending_settlement=True)
                if first and slots > 1:
                    follow = _ppi_rest_probe_keys(store, limit=slots - 1, family=family,
                                                  after=first[0], pending_settlement=True)
                    first.extend(key for key in follow if key != first[0])
                planned[family] = iter(first)
            else:
                planned[family] = iter(_ppi_rest_probe_keys(
                    store, limit=slots, family=family, after=previous, pending_settlement=True))
        key = next(planned[family], None)
        if key is None or key in visited:
            exhausted.add(family)
            continue
        observed = _observe_native_candidate(
            reader, store, key, pre_wire=pre_wire, update_market_clock=True,
            confirm_settlement=True)
        visited.add(key)
        status, reason = observed["status"], observed.get("reason")
        if reason in NATIVE_PROVIDER_BACKPRESSURE:
            # A genuine 429 spent one attempt. Local backpressure did not,
            # unless the Current half of this pair had already reached PPI.
            result["attempted"] += (bool(observed["native_receipts"])
                                    or reason in {"PPI_HTTP_429", "PPI_NATIVE_HTTP_429"})
            result["reason"] = reason
            return candidate_cursor, last_pulse, result
        if status != "PRE_WIRE_GUARD_HOLD" or observed["native_receipts"]:
            result["attempted"] += 1
            after[family] = key
            last_pulse = _pulse_observed_progress(store, last_pulse)
        result["fresh_pairs"] += status == "FRESH_OBSERVED"
        result["attested"] += observed.get("settlement_attested") is True
        if reason == "PPI_NATIVE_DEMAND_IDENTITY_DENIED":
            result.setdefault("identities_denied_by_gateway", []).append(list(key))
            continue
        if status == "PRE_WIRE_GUARD_HOLD" or (
                status == "PROVIDER_OR_CONTRACT_HOLD" and reason not in {
                    "PPI_INSTRUMENT_NOT_FOUND", "PPI_CATALOG_INDEPENDENT_AUTHORITY_UNVERIFIED",
                    "PPI_RESPONSE_IDENTITY_MISMATCH", "PPI_NATIVE_RECEIPT_CLOCK_INVALID"}):
            result["reason"] = reason or status
            break
    return cursor, last_pulse, result


_PPI_POSTCLOSE_COMMAND_PREFIX = "PPI_DIRECT_REST_POSTCLOSE_ONCE:"
_PPI_OPEN_COMMAND_PREFIX = "PPI_DIRECT_REST_OPEN_ONCE:"
_PPI_OPEN_FAMILIES = (
    "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
    "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
)


def _ppi_open_catalog_coverage(store):
    """Separate authoritative unique PPI identities from unresolved catalog.

    SQL aggregates the whole stored denominator. At most 10k small raw PPI
    records are then passed to the existing *pure* full five-key proof helper;
    a PPI_FIELD label alone is never counted as provider attestation. Neither
    this nor the separate REQUEST_CANDIDATE wire probe performs a provider GET.
    """
    try:
        with store.connect() as c:
            total = int(c.execute(
                "SELECT COUNT(*) FROM financial_instrument_catalog").fetchone()[0])
            if not 0 <= total <= 1_000_000:
                return None
            rows = c.execute("""WITH attributed AS (
              SELECT f.instrument_type,f.settlement_source,
                     CASE WHEN json_valid(f.metadata_json)
                       THEN COALESCE(json_extract(f.metadata_json,'$._discovery_source'),'')
                       ELSE '' END AS discovery
                FROM financial_instrument_catalog f
            )
            SELECT instrument_type,COUNT(*) FROM attributed
             WHERE discovery='PPI_PRIMARY'
                OR (discovery='' AND settlement_source IN
                    ('PPI_FIELD','REQUEST_CANDIDATE'))
             GROUP BY instrument_type""").fetchall()
            candidates = c.execute("""WITH identity_groups AS (
                SELECT ticker,instrument_type,settlement,COUNT(*) AS rows_in_identity
                  FROM financial_instrument_catalog
                 GROUP BY ticker,instrument_type,settlement
            )
            SELECT f.ticker,f.instrument_type,f.market,f.currency,f.settlement,
                   f.settlement_source,f.metadata_json
              FROM financial_instrument_catalog f JOIN identity_groups g
                ON f.ticker=g.ticker AND f.instrument_type=g.instrument_type
               AND f.settlement=g.settlement
             WHERE g.rows_in_identity=1
               AND f.status IN ('AVAILABLE','STALE')
               AND f.settlement_source='PPI_FIELD'
               AND f.market NOT IN ('','UNKNOWN')
               AND f.currency NOT IN ('','UNKNOWN')
               AND length(f.metadata_json)<=4096
               AND (CASE WHEN json_valid(f.metadata_json)
                         THEN json_extract(f.metadata_json,'$._discovery_source')
                         ELSE '' END)='PPI_PRIMARY'
             ORDER BY f.instrument_type,f.ticker,f.settlement
             LIMIT 10001""").fetchall()
        if len(candidates) > 10000:
            return None
        exact = dict.fromkeys(_PPI_OPEN_FAMILIES, 0)
        unverified = dict.fromkeys(_PPI_OPEN_FAMILIES, 0)
        counted = 0
        for family, count in rows:
            if family in unverified:
                unverified[family] += int(count)
                counted += int(count)
        for ticker, family, market, currency, settlement, source, raw_json in candidates:
            record = {"ticker": ticker, "instrument_type": family,
                      "market": market, "currency": currency,
                      "settlement": settlement, "settlement_source": source,
                      "metadata_json": raw_json}
            if (family in exact and
                    not financial_catalog.ppi_full_key_evidence_reason(record)):
                exact[family] += 1
                unverified[family] -= 1
        return {"enumerated_exact": exact, "unverified": unverified,
                "catalog_scope": "PPI_CLAIMED_ONLY",
                "outside_ppi_scope": total - counted}
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return None


def _ppi_open_global_session_evidence_gate():
    """Fail closed until *physical* PPI single-session custody is integrated.

    In the proposed PAPER Gateway code path scanner, EXIT and scalping attach
    to the single owner over AF_UNIX rather than creating their own reader.
    This is local custody evidence ONLY: compose still shares UID0/bound
    credentials and legacy entrypoints may authenticate independently.
    Scanner-local `metrics`, socket owner PID/generation, an environment flag,
    a DB command, or process-local budget lease cannot attest account-global
    exclusivity without independent host/process inventory and a physical
    owner-login witness. Neither is certified here.

    This deliberate hard HOLD is checked at OPEN admission and before every
    native Current/Book SEND. It may be replaced only alongside a separately
    reviewed shared-session owner with independent positive evidence; this
    code must never infer singleton status from the command issuer's JSON.
    """
    raise ValueError("PPI_SINGLE_SESSION_GLOBAL_UNVERIFIED")


def _ppi_open_evidence_gate(store, requested_day, *, now=None):
    """No OPEN diagnostic may compete with EXIT or leave PAPER/0 custody."""
    local = (now or datetime.now(TZ)).astimezone(TZ)
    minute = local.hour * 60 + local.minute
    if (requested_day != local.date().isoformat() or
            not _business_day(local.date()) or _market_phase(local) != "OPEN" or
            not (10 * 60 + 30 <= minute < 17 * 60)):
        raise ValueError("PPI_OPEN_COMMAND_EXPIRED_OR_SESSION_INVALID")
    if os.getenv("POROTA_PPI_DIRECT_REST_RECOVERY") != "1":
        raise ValueError("PPI_OPEN_RUNTIME_FLAG_NOT_ENABLED")
    with store.connect() as c:
        state = c.execute("""SELECT mode,real_orders_sent,ppi_auth
            FROM observer_state WHERE id=1""").fetchone()
    if (not state or tuple(state) != ("PRODUCTION_PAPER", 0, "OK") or
            type(state[1]) is not int):
        raise ValueError("PPI_OPEN_PAPER_ZERO_AUTH_NOT_ATTESTED")
    opened, invalid = store.exit_positions()
    if opened or invalid or store.active_future_positions():
        raise ValueError("PPI_OPEN_EXIT_EXPOSURE_PRESENT")
    with store.connect() as c:
        fci_open = c.execute("""SELECT 1 FROM paper_family_lifecycle
            WHERE family='FCI' AND state!='REDEEMED' LIMIT 1""").fetchone()
        caucion_open = c.execute("""SELECT 1 FROM paper_cauciones
            WHERE status NOT IN ('MATURED','CANCELLED_ADMIN')
               OR (status='MATURED' AND settled_at IS NULL)
               OR (status='CANCELLED_ADMIN' AND settled_at IS NOT NULL)
            LIMIT 1""").fetchone()
    if fci_open or caucion_open:
        raise ValueError("PPI_OPEN_EXIT_EXPOSURE_PRESENT")
    try:
        from bt_caucion_paper import CaucionBook
        CaucionBook(store).positions()
    except (ValueError, TypeError, sqlite3.Error, OSError):
        raise ValueError("PPI_OPEN_CAUCION_RECEIPT_UNVERIFIED") from None
    # A670 issuer is an independent witness, not a capability to bypass the
    # scanner's own pre-SEND guard: direct observer_commands writers exist.
    _ppi_open_global_session_evidence_gate()


def _run_ppi_open_command(store, reader, command_id, name, *, now=None):
    """One typed ten-family OPEN witness in the already authenticated scanner.

    At most one Current+Book pair per family; neither native HTTP200 nor a
    fresh quote grants simulation readiness, which requires the independent
    full PIT/Book1/decision/Book2/costs/EXIT admission evidence.
    """
    try:
        parts = name.split(":")
        if (len(parts) != 3 or parts[0] != _PPI_OPEN_COMMAND_PREFIX[:-1] or
                len(parts[1]) != 10 or
                date.fromisoformat(parts[1]).isoformat() != parts[1] or
                parts[2] != "ALL"):
            raise ValueError("PPI_OPEN_COMMAND_FORMAT_INVALID")
        _, requested_day, _ = parts
        with store.connect() as c:
            prior = c.execute("""SELECT 1 FROM observer_commands
                WHERE id<? AND command=? LIMIT 1""", (command_id, name)).fetchone()
            row = c.execute("""SELECT created_at,status FROM observer_commands
                WHERE id=? AND command=?""", (command_id, name)).fetchone()
        if prior:
            raise ValueError("PPI_OPEN_ALREADY_REQUESTED_NO_RETRY")
        if not row or row[1] != "RUNNING":
            raise ValueError("PPI_OPEN_COMMAND_CUSTODY_INVALID")
        issued_at = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        local_now = (now or datetime.now(TZ)).astimezone(TZ)
        if issued_at.tzinfo is None or issued_at.utcoffset() is None:
            raise ValueError("PPI_OPEN_COMMAND_ISSUE_CLOCK_UNVERIFIED")
        if not -5 <= (local_now - issued_at).total_seconds() <= 600:
            raise ValueError("PPI_OPEN_COMMAND_ISSUE_TTL_EXPIRED")
        if reader is None:
            raise ValueError("PPI_OPEN_EXISTING_SCANNER_SESSION_UNAVAILABLE")
        metrics = getattr(reader, "metrics", None)
        if not isinstance(metrics, dict) or metrics.get("authenticated") is not True:
            raise ValueError("PPI_OPEN_EXISTING_SCANNER_AUTH_UNVERIFIED")
        if getattr(reader, "budget_enabled", False) is not True:
            raise ValueError("PPI_OPEN_SHARED_READ_BUDGET_UNVERIFIED")

        def pre_wire():
            wire_now = (now or datetime.now(TZ)).astimezone(TZ)
            if not -5 <= (wire_now - issued_at).total_seconds() <= 600:
                raise ValueError("PPI_OPEN_COMMAND_ISSUE_TTL_EXPIRED")
            current_metrics = getattr(reader, "metrics", None)
            if (not isinstance(current_metrics, dict) or
                    current_metrics.get("authenticated") is not True or
                    getattr(reader, "budget_enabled", False) is not True):
                raise ValueError("PPI_OPEN_SCANNER_SESSION_OR_BUDGET_LOST")
            _ppi_open_evidence_gate(store, requested_day, now=now)

        pre_wire()
        catalog_coverage = _ppi_open_catalog_coverage(store)
        if catalog_coverage is None:
            raise ValueError("PPI_OPEN_CATALOG_UNVERIFIED")
        catalog_counts = catalog_coverage["enumerated_exact"]
        statuses = dict.fromkeys(_PPI_OPEN_FAMILIES, "NOT_ATTEMPTED_ABORTED")
        coverage = {}
        for family in _PPI_OPEN_FAMILIES:
            n = catalog_counts[family] + catalog_coverage["unverified"][family]
            coverage[family] = {"enumerated_exact": catalog_counts[family],
                                "attempted": 0,
                                "native_pair_qa_pass": 0, "ready": 0,
                                "hold": n, "unattempted": n}
        abort = False
        witnessed = False
        for family in _PPI_OPEN_FAMILIES:
            try:
                pre_wire()
            except ValueError:
                statuses[family] = "PRE_WIRE_GUARD_HOLD"
                abort = True
                break
            if not (catalog_counts[family] + catalog_coverage["unverified"][family]):
                statuses[family] = "CATALOG_IDENTITY_UNAVAILABLE"
                continue
            result = _ppi_direct_rest_observe_only(
                reader, store, family=family, command_id=command_id,
                pre_wire=pre_wire)
            status = result.get("status")
            if result.get("paper_ready") is not False:
                raise ValueError("PPI_OPEN_UNEXPECTED_READINESS")
            outcome_receipts = (
                {_PPI_REFERENCE_OBSERVED[family]: ["current"], "REFERENCE_UNUSABLE": ["current"]}
                if family in _PPI_REFERENCE_OBSERVED else
                {"FRESH_OBSERVED": ["current", "book"], "STALE_OR_INVALID": ["current", "book"]}
            )
            if status not in outcome_receipts and status not in {
                "CATALOG_IDENTITY_UNAVAILABLE", "PROVIDER_OR_CONTRACT_HOLD", "PRE_WIRE_GUARD_HOLD"
            }:
                raise ValueError("PPI_OPEN_UNEXPECTED_PROBE_STATUS")
            receipts = result.get("native_receipts")
            if receipts not in ([], ["current"], ["current", "book"]):
                raise ValueError("PPI_OPEN_NATIVE_WIRE_EVIDENCE_INVALID")
            if status in outcome_receipts and receipts != outcome_receipts[status]:
                # A reference proves one Current reading. Only the market-book
                # families can prove a Current+Book pair.
                status = "PROVIDER_OR_CONTRACT_HOLD"
            statuses[family] = status
            attempted = int("current" in receipts)
            coverage[family]["attempted"] = attempted
            coverage[family]["unattempted"] -= attempted
            if status == "FRESH_OBSERVED" and receipts == ["current", "book"]:
                coverage[family]["native_pair_qa_pass"] = 1
            if status in outcome_receipts:
                witnessed = True
            if status in {"PROVIDER_OR_CONTRACT_HOLD", "PRE_WIRE_GUARD_HOLD"}:
                abort = True
                break
        report = {"schema": "RC6_PPI_OPEN_TEN_FAMILY_V1",
                  "day": requested_day, "paper_ready": False,
                  "order_authority": False,
                  "readiness_reason": "FULL_READINESS_NOT_EVIDENCED",
                  "unattempted_reason": "BOUNDED",
                  "statuses": statuses, "coverage": coverage,
                  "unverified": catalog_coverage["unverified"],
                  "catalog_scope": catalog_coverage["catalog_scope"],
                  "outside_ppi_scope": catalog_coverage["outside_ppi_scope"]}
        detail = json.dumps(report, separators=(",", ":"), sort_keys=True)
        if len(detail) > 2000:
            raise ValueError("PPI_OPEN_COVERAGE_RECEIPT_TOO_LARGE")
        _finish_command(store, command_id,
                        "HOLD" if abort or not witnessed else "EVIDENCE", detail)
    except (ValueError, TypeError, sqlite3.Error, RuntimeError, OSError) as error:
        reason = (str(error) if type(error) is ValueError and
                  (str(error).startswith("PPI_OPEN_") or
                   str(error) == "PPI_SINGLE_SESSION_GLOBAL_UNVERIFIED") else
                  "PPI_OPEN_GUARD_" + type(error).__name__.upper())
        _finish_command(store, command_id, "HOLD", reason)
    return reader


def _ppi_postclose_evidence_gate(store, requested_day, *, now=None):
    """Validate only a short observe-only GET window on the scanner's store.

    The scope is checked again before each endpoint; this never pauses the
    broker, changes a position or claims an EXIT/provider quota. Uncertainty
    rejects a diagnostic; it does not disable ordinary PAPER simulation.
    """
    local = (now or datetime.now(TZ)).astimezone(TZ)
    minute = local.hour * 60 + local.minute
    if (requested_day != local.date().isoformat() or
            not _business_day(local.date()) or _market_phase(local) != "CLOSED" or
            not 17 * 60 <= minute < 23 * 60):
        raise ValueError("PPI_POSTCLOSE_COMMAND_EXPIRED_OR_SESSION_INVALID")
    if os.getenv("POROTA_PPI_DIRECT_REST_RECOVERY") != "1":
        raise ValueError("PPI_DIRECT_REST_RUNTIME_FLAG_NOT_ENABLED")
    with store.connect() as c:
        state = c.execute("""SELECT mode,real_orders_sent,ppi_auth
            FROM observer_state WHERE id=1""").fetchone()
    if (not state or tuple(state) != ("PRODUCTION_PAPER", 0, "OK") or
            type(state[1]) is not int):
        raise ValueError("PPI_POSTCLOSE_PAPER_ZERO_AUTH_NOT_ATTESTED")
    opened, invalid = store.exit_positions()
    if opened or invalid or store.active_future_positions():
        raise ValueError("PPI_POSTCLOSE_SUPERVISABLE_EXPOSURE_PRESENT")
    # FCI/caucion positions live in independent PAPER ledgers; neither an
    # empty spot book nor zero futures proves there is no EXIT duty.
    # Missing schema is not evidence of zero exposure.
    with store.connect() as c:
        other_exposure = c.execute("""SELECT 1 FROM paper_family_lifecycle
            WHERE family='FCI' AND state!='REDEEMED' LIMIT 1""").fetchone()
        caucion_open = c.execute("""SELECT 1 FROM paper_cauciones
            WHERE status NOT IN ('MATURED','CANCELLED_ADMIN')
               OR (status='MATURED' AND settled_at IS NULL)
               OR (status='CANCELLED_ADMIN' AND settled_at IS NOT NULL)
            LIMIT 1""").fetchone()
    if other_exposure:
        raise ValueError("PPI_POSTCLOSE_FCI_EXPOSURE_PRESENT")
    if caucion_open:
        raise ValueError("PPI_POSTCLOSE_CAUCION_EXPOSURE_PRESENT")
    # Do not treat a raw CANCELLED_ADMIN label as proof of settlement.
    # The existing ledger projection verifies its immutable cancellation
    # receipt and all legacy fee restatement seals without any DDL/write.
    try:
        from bt_caucion_paper import CaucionBook
        CaucionBook(store).positions()
    except (ValueError, TypeError, sqlite3.Error, OSError):
        raise ValueError("PPI_POSTCLOSE_CAUCION_RECEIPT_UNVERIFIED") from None


def _run_ppi_postclose_command(store, reader, command_id, name, *, now=None):
    """Route one bounded family sample through the ALREADY logged-in reader.

    A crashed RUNNING command must remain a forensic partial/unknown outcome;
    a later identical request cannot silently repeat a completed Current.
    """
    try:
        parts = name.split(":")
        if (len(parts) != 3 or parts[0] != _PPI_POSTCLOSE_COMMAND_PREFIX[:-1]
                or len(parts[1]) != 10 or
                date.fromisoformat(parts[1]).isoformat() != parts[1]
                or parts[2] not in OPERATIONAL_FAMILIES):
            raise ValueError("PPI_POSTCLOSE_COMMAND_FORMAT_INVALID")
        _, requested_day, family = parts
        with store.connect() as c:
            prior = c.execute("""SELECT 1 FROM observer_commands
                WHERE id<? AND command=? LIMIT 1""", (command_id, name)).fetchone()
            row = c.execute("""SELECT created_at,status FROM observer_commands
                WHERE id=? AND command=?""", (command_id, name)).fetchone()
        if prior:
            raise ValueError("PPI_POSTCLOSE_ALREADY_REQUESTED_NO_RETRY")
        if not row or row[1] != "RUNNING":
            raise ValueError("PPI_POSTCLOSE_COMMAND_CUSTODY_INVALID")
        issued_at = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        local_now = (now or datetime.now(TZ)).astimezone(TZ)
        if issued_at.tzinfo is None or issued_at.utcoffset() is None:
            raise ValueError("PPI_POSTCLOSE_COMMAND_ISSUE_CLOCK_UNVERIFIED")
        age_seconds = (local_now - issued_at).total_seconds()
        if not -5 <= age_seconds <= 600:
            raise ValueError("PPI_POSTCLOSE_COMMAND_ISSUE_TTL_EXPIRED")
        if reader is None:
            raise ValueError("PPI_POSTCLOSE_EXISTING_AUTH_SESSION_UNAVAILABLE")
        def pre_wire():
            wire_now = (now or datetime.now(TZ)).astimezone(TZ)
            if not -5 <= (wire_now - issued_at).total_seconds() <= 600:
                raise ValueError("PPI_POSTCLOSE_COMMAND_ISSUE_TTL_EXPIRED")
            _ppi_postclose_evidence_gate(store, requested_day, now=now)
        pre_wire()
        # Existing observe-only function, budget, broker-wire guard and
        # catalog identities. No ProductionMarketReader() or login_once().
        result = _ppi_direct_rest_observe_only(
            reader, store, family=family, command_id=command_id,
            pre_wire=pre_wire)
        if result.get("paper_ready") is not False:
            raise ValueError("PPI_POSTCLOSE_UNEXPECTED_READINESS")
        evidence_status = {"FRESH_OBSERVED", "STALE_OR_INVALID"}
        if family in _PPI_REFERENCE_OBSERVED:
            evidence_status.add(_PPI_REFERENCE_OBSERVED[family])
        status = ("EVIDENCE" if result.get("status") in evidence_status else "HOLD")
        detail = json.dumps({"command_id": command_id, "family": family,
                             "provider_status": result.get("status"),
                             "reason": result.get("reason"),
                             "paper_ready": False, "order_authority": False},
                            sort_keys=True)
        _finish_command(store, command_id, status, detail)
    except (ValueError, TypeError, sqlite3.Error, RuntimeError, OSError) as error:
        # A stable reason code is useful operationally; never log a raw SDK
        # payload or potentially secret-bearing exception to this command.
        reason = (str(error) if type(error) is ValueError and
                  str(error).startswith("PPI_") else
                  "PPI_POSTCLOSE_GUARD_" + type(error).__name__.upper())
        _finish_command(store, command_id, "HOLD", reason)
    return reader


def _active_symbols(store):
    """Compatibilidad: devuelve el primer lote, no el universo histórico."""
    return _eligible_symbols(store)[:ACTIVE_SYMBOL_LIMIT]


def _focus_coverage(store):
    """Diagnóstico del foco histórico de muestreo; no autoriza aperturas.

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


def _exact_pit_ready_spot_requests(store):
    """Spot READY en catálogo y ledger PIT, sin elegir hermanos monetarios.

    La proyección es por identidad completa; un request PPI sin market/currency
    inequívocos queda fuera. No sustituye frescura, sesión, preopen ni el gate
    de contrato individual en la transacción PAPER.
    """
    try:
        with store.connect() as c:
            aliases = financial_catalog.scanner_diagnostic_aliases_sql(c)
            rows = c.execute(f"""WITH
                diagnostic_aliases AS ({aliases}),
                f_request AS (
                  SELECT f.ticker,f.instrument_type,f.settlement
                  FROM financial_instrument_catalog f LEFT JOIN diagnostic_aliases h
                    USING(ticker,instrument_type,market,currency,settlement)
                  WHERE f.instrument_type IN ('ACCIONES','CEDEARS','ETFS') AND h.ticker IS NULL
                  GROUP BY f.ticker,f.instrument_type,f.settlement
                  HAVING COUNT(*)=1),
                p_request AS (
                  SELECT p.ticker,p.instrument_type,p.settlement
                  FROM candidate_identity_v2 p LEFT JOIN diagnostic_aliases h
                    USING(ticker,instrument_type,market,currency,settlement)
                  WHERE p.instrument_type IN ('ACCIONES','CEDEARS','ETFS') AND h.ticker IS NULL
                  GROUP BY p.ticker,p.instrument_type,p.settlement
                  HAVING COUNT(*)=1)
                SELECT f.ticker,f.instrument_type,f.settlement
                FROM financial_instrument_catalog f
                JOIN candidate_identity_v2 p
                  USING(ticker,instrument_type,market,currency,settlement)
                JOIN f_request fr USING(ticker,instrument_type,settlement)
                JOIN p_request pr USING(ticker,instrument_type,settlement)
                WHERE f.status='AVAILABLE' AND f.capability='READY_PAPER_SPOT'
                  AND p.status='AVAILABLE' AND p.can_simulate=1
                ORDER BY f.instrument_type,f.ticker,f.settlement""").fetchall()
    except sqlite3.Error:
        # A missing/incomplete PIT ledger never falls back to ticker-only GO.
        return ()
    return tuple(tuple(row) for row in rows)


def _publish_focus_health(store, coverage):
    missing = "; ".join(
        f"{item['identity'][0]}={item['reason']}" for item in coverage["missing"]
    ) or "ninguno"
    detail = (
        f"Foco efectivo {coverage['matched_count']}/{coverage['configured_count']}; "
        f"meta observacional histórica {coverage['minimum_for_openings']}; "
        f"fuente {coverage['source']}; faltantes: {missing}. "
        f"Ingesta y cierres permanecen activos."
    )
    # En A248 un faltante de la lista histórica es aviso de muestreo, no un
    # fallo operativo ROJO ni una autorización/veto de nuevas entradas.
    advisory_state = "AMARILLO" if coverage["state"] == "ROJO" else coverage["state"]
    _health(store, "PAPER_FOCUS_COVERAGE", advisory_state, detail,
            "Catálogo PPI normalizado", success=coverage["state"] == "VERDE")


def _cycle_plan(store, focus_candidates=None, *, include_legacy_summary=False, iol=False, iol_metrics=None):
    """Existing catalog/quote authority, with a durable feasible active basket."""
    from rc6_performance.scanner import sampling_plan
    catalog_error = None
    checked_at = now_iso()
    try:
        if iol:
            from bs_instrument_contracts import aware_datetime
            iol_keys = financial_catalog.iol_spot_catalog(
                store, now=aware_datetime(checked_at), include_contracts=True)
            identities = {(key[0], key[1], key[4]): key for key in iol_keys}
        else:
            identities = _eligible_scanner_identities(store)
    except sqlite3.Error as exc:
        identities = {}
        catalog_error = getattr(exc, "sqlite_errorname", type(exc).__name__)
    exact = {request: key for request, key in identities.items() if key is not None}
    legacy_catalog = bool(identities) and not exact
    universe = list(identities if include_legacy_summary and legacy_catalog else exact)
    if iol:
        by_family = {}
        for request in universe:
            by_family.setdefault(request[1], []).append(request)
        universe = [request for batch in zip_longest(*by_family.values())
                    for request in batch if request is not None]
    positions = store.open_positions()
    opened = list(dict.fromkeys((p["symbol"], p["asset_class"], p["settlement"])
                                for p in positions))
    cycles = []
    with store.connect() as c:
        row = c.execute("SELECT cursor_after,detail FROM universe_cycle_metrics ORDER BY id DESC LIMIT 1").fetchone()
        if iol and iol_metrics is not None and exact and not catalog_error:
            from rc6_dynamic_universe.iol_capacity import read_cycles
            try:
                cycles = read_cycles(c)
            except sqlite3.Error:
                # Capacity evidence is optional to the existing baseline plan.
                pass
    previous = None
    if row:
        try:
            text = row[1] or ""
            previous = json.loads(text[text.index("{"):]).get("active_plan")
        except (ValueError, TypeError):
            pass
    limit = ACTIVE_SYMBOL_LIMIT
    cadence = INTERVAL
    seconds_per_key = 2 * PPI_CALL_BUDGET_SECONDS
    if iol:
        from iol_shadow_collector_rc6 import CollectionPolicy
        from rc6_iol_market_gateway import ROLE_ENDPOINTS

        policy = CollectionPolicy()
        reported = iol_metrics.get("wire_budget") if isinstance(iol_metrics, dict) else None
        reported = reported if isinstance(reported, dict) else {}
        provider_budget = reported.get("capacity_per_minute")
        calls_per_minute = (min(provider_budget, policy.max_calls_per_minute)
                            if type(provider_budget) is int and provider_budget > 0 else policy.max_calls_per_minute)
        scanner_calls = len(ROLE_ENDPOINTS["scanner"])
        # Leave space for a full EXIT pass and an OAuth request in each rolling
        # window. A shorter polling interval can place several batches in it.
        exit_calls = len(ROLE_ENDPOINTS["exit_reader"]) * max(1, len(opened))
        exit_reserve = reported.get("reserved_exit")
        if type(exit_reserve) is int and exit_reserve > exit_calls:
            exit_calls = exit_reserve
        auth_reserve = reported.get("reserved_auth")
        auth_calls = auth_reserve if type(auth_reserve) is int and auth_reserve >= 1 else 1
        stream_status = iol_metrics.get("trade_coverage") if isinstance(iol_metrics, dict) else None
        subscribed = stream_status.get("continuous_subscribed") if isinstance(stream_status, dict) else None
        if type(subscribed) is int and subscribed > 0:
            cadence = min(cadence, IOL_CONTINUOUS_DISPATCH_SECONDS)
        batches_per_minute = math.ceil(60 / cadence)
        available_calls = max(0, calls_per_minute - exit_calls - auth_calls)
        limit = min(limit, available_calls // (scanner_calls * batches_per_minute))
        seconds_per_key = scanner_calls * max(policy.min_interval_seconds, 60 / calls_per_minute)
        # The independent EXIT reader supervises the whole portfolio even when
        # existing positions exhaust the scanner's observation budget.
        opened = opened[:limit]
    cycle_seconds = cadence + max(limit, len(opened)) * seconds_per_key
    # Preserve historical focus ordering when starting a new IOL sampling plan.
    if iol:
        focus_candidates = tuple(dict.fromkeys((*FOCUS_SYMBOLS, *identities)))
    focus = FOCUS_SYMBOLS if focus_candidates is None else focus_candidates
    budget = dict(limit=limit, cycle_seconds=cycle_seconds, required_samples=SIGNAL_MIN_SAMPLES,
                  window_seconds=SIGNAL_WINDOW_MINUTES * 60)
    symbols, before, after, plan = sampling_plan(
        universe, opened, focus, **budget,
        now=checked_at, cursor=row[0] if row else 0, previous=previous)
    if not iol and not catalog_error and not legacy_catalog:
        from rc6_dynamic_universe.promotion import capacity_controller_from_environment
        controller = capacity_controller_from_environment(store.path)
        if controller.state(checked_at)["status"] == "APPROVED_DYNAMIC":
            full_opened = [(p["symbol"], p["asset_class"], p["market"], p["currency"], p["settlement"])
                           for p in positions if p["asset_class"] in PAPER_SPOT_FAMILIES]
            selected = controller.selection("EQUITY_SPOT", symbols, opened=full_opened, as_of=checked_at)
            if selected["dynamic"]:
                try:
                    symbols, plan = _approved_equity_selection(store, selected, plan, exact)
                except sqlite3.Error as exc:
                    catalog_error = getattr(exc, "sqlite_errorname", type(exc).__name__)
    if catalog_error:
        symbols = tuple(opened)
        universe = []
        plan.update(catalog_status="CATALOG_READ_ERROR", catalog_error=catalog_error,
                    feasible=False, focus_feasible=False, opening_identities=[])
    elif legacy_catalog:
        plan.update(catalog_status="CATALOG_IDENTITY_UNVERIFIED",
                    feasible=False, focus_feasible=False, opening_identities=[])
    else:
        plan["catalog_status"] = "ELIGIBLE_INSTRUMENTS" if exact else "NO_ELIGIBLE_INSTRUMENTS"
    if iol:
        plan["cadence_seconds"] = cadence
        plan["iol_rest_budget"] = {
            "calls_per_minute": calls_per_minute,
            "scanner_batch_limit": limit,
            "scanner_calls_per_instrument": scanner_calls,
            "batches_per_minute": batches_per_minute,
            "planned_exit_calls": exit_calls,
        }
        if auth_calls != 1:
            plan["iol_rest_budget"]["planned_auth_calls"] = auth_calls
        if iol_metrics is not None and exact and not catalog_error:
            from cg_paper_workspace import artifact_root
            from rc6_dynamic_universe.iol_capacity import paper_capacity, prioritize_paper_equities
            from rc6_trader_dashboard.generation import read_shadow

            capacity = paper_capacity(cycles, iol_metrics, plan["iol_rest_budget"], as_of=checked_at)
            report = {}
            if capacity["status"] == "SHADOW_RECOMMENDATION":
                report = read_shadow(artifact_root(store.path) / "dynamic-shadow")["report"]
            symbols, plan = prioritize_paper_equities(
                symbols, plan, exact, opened, capacity, report, as_of=checked_at)
        if exact and not catalog_error:
            next_symbols, _, _, _ = sampling_plan(
                universe, opened, focus, **budget, now=checked_at, cursor=after, previous=plan)
            plan["next_iol_subscriptions"] = [exact[request] for request in next_symbols if request in exact]
    if not plan.get("dynamic_selection"):
        plan["read_scopes"] = [
            {"request": request, "identity": exact[request], "priority": "DISCOVERY"}
            for request in symbols if request in exact and not catalog_error]
    if iol and exact and not catalog_error:
        from rc6_dynamic_universe.iol_coverage import coverage_feasibility, plan_stream_coverage
        from rc6_iol_intraday import FRESH_SECONDS
        from rc6_iol_trade_stream import MAX_TRADES

        priority_keys = [scope["identity"] for scope in plan["read_scopes"]]
        priority_keys.extend(plan.get("next_iol_subscriptions", []))
        prior_coverage = previous.get("iol_stream_coverage") if isinstance(previous, dict) else None
        coverage = plan_stream_coverage(
            exact.values(), priority_keys, prior_coverage, slots=MAX_TRADES, rest_budget=plan["iol_rest_budget"])
        coverage["dispatch"] = coverage_feasibility(coverage, cadence_seconds=cadence,
            freshness_seconds=FRESH_SECONDS, window_minutes=SIGNAL_WINDOW_MINUTES,
            required_samples=SIGNAL_MIN_SAMPLES)
        if isinstance(stream_status, dict):
            coverage["connection_status"] = stream_status
        plan["iol_stream_coverage"] = coverage
    return symbols, len(universe), before, after, plan


def _advance_iol_discovery(reader, coverage, *, active_plan=None):
    """Read the preceding slice after its dwell, then queue the next slice."""
    from iol_rest_readonly_rc6 import IOLRestError
    from rc6_dynamic_universe.iol_coverage import current_trade_receipts, follow_fresh_discovery, stream_batches
    from rc6_iol_market_gateway import PREPARE_TRADES_LIMIT
    from rc6_ppi_shared_feed import SharedFeedUnavailable
    from rc6_iol_intraday import FRESH_SECONDS

    states = {}
    previous = coverage.pop("previous_targets", ())
    if previous:
        # The predecessor remains subscribed until a new prepare() replaces
        # its slots. Capture after the dwell without a provider request.
        try:
            rows = []
            read = getattr(reader, "trade_coverage", None)
            if callable(read):
                for offset in range(0, len(previous), PREPARE_TRADES_LIMIT):
                    rows.extend(read(previous[offset:offset + PREPARE_TRADES_LIMIT]))
                states, coverage["observed_previous_slice"] = current_trade_receipts(
                    rows, at=now_iso(), max_age_seconds=FRESH_SECONDS)
            else:
                coverage["observation_hold_reason"] = "IOL_GATEWAY_COVERAGE_UNAVAILABLE"
        except (IOLRestError, SharedFeedUnavailable) as exc:
            coverage["observation_hold_reason"] = _scanner_iol_error(exc)
        except (TypeError, KeyError, ValueError):
            coverage["observation_hold_reason"] = "IOL_GATEWAY_COVERAGE_UNVERIFIED"
    recent = getattr(reader, "recent_trades", None)
    recent_received_at = {}
    if callable(recent):
        try:
            rows = recent(PREPARE_TRADES_LIMIT)
            candidates, report = current_trade_receipts(rows, at=now_iso(), max_age_seconds=FRESH_SECONDS)
            recent_received_at = {tuple(row["identity"]): row.get("received_at") for row in rows}
            coverage["recent_stream_activity"] = {name: report[name] for name in ("observed_at", "by_family")}
            states = {**candidates, **{key: state for key, state in states.items() if key not in candidates}}
        except (IOLRestError, SharedFeedUnavailable) as exc:
            coverage["recent_stream_hold_reason"] = _scanner_iol_error(exc)
        except (TypeError, KeyError, ValueError):
            coverage["recent_stream_hold_reason"] = "IOL_GATEWAY_COVERAGE_UNVERIFIED"
    followed = follow_fresh_discovery(active_plan, coverage, states)
    if followed and (received_at := recent_received_at.get(coverage["rest_followed_trade"])):
        coverage["followed_trade_received_at"] = received_at
    requested = stream_batches(coverage, PREPARE_TRADES_LIMIT)
    scheduled = 0
    for items in requested:
        try:
            scheduled += reader.prepare_trades(items)
        except (IOLRestError, SharedFeedUnavailable) as exc:
            coverage["preparation_hold_reason"] = _scanner_iol_error(exc)
            # A lost connection is retried at the same cursor. A rejected
            # local scope is an explicit outcome for this slice.
            if str(exc) != "IOL_GATEWAY_IDENTITY_SCOPE_DENIED":
                break
    else:
        coverage["prepared"] = bool(requested)
    coverage["scheduled_subscriptions"] = scheduled
    return states, followed


def _approved_equity_selection(store, selected, baseline_plan, identities):
    """Reuse full-key readiness; financial ranking never becomes a BUY gate."""
    with store.connect() as c:
        rows = c.execute("""SELECT c.ticker,c.instrument_type,c.market,c.currency,c.settlement
            FROM financial_instrument_catalog c JOIN candidate_identity_v2 r
            USING(ticker,instrument_type,market,currency,settlement)
            WHERE c.status='AVAILABLE' AND r.status='AVAILABLE' AND r.can_simulate=1
              AND c.capability LIKE 'READY_PAPER%' AND c.instrument_type IN ('ACCIONES','CEDEARS','ETFS')""").fetchall()
    ready = set(map(tuple, rows)).intersection(identities.values())
    opened = set(selected["opened_priority"])
    keys = [tuple(k) for k in selected["selected"] if tuple(k) in ready or tuple(k) in opened]
    request = lambda k: (k[0], k[1], k[4])
    entry = [request(k) for k in keys if k in ready and k in selected["entry_identities"]]
    scope = []
    for k in keys:
        row = selected["rows"][k]
        priority = "OPENED_CRITICAL" if k in opened else {"HOT": "STRATEGY_HOT", "WARM": "WARM"}.get(row["state"], "DISCOVERY")
        scope.append({"request": request(k), "identity": k, "priority": priority})
    plan = dict(baseline_plan, schema="rc6.approved-dynamic-sampling.v1",
        configuration_fingerprint=selected["policy_state"]["fingerprint"],
        opening_identities=entry, feasible=True, focus_feasible=True,
        active_budget={"status": "FEASIBLE", "authority": "VERIFIED_OPEN_CAPACITY"},
        dynamic_selection=True, read_scopes=scope, cadence_seconds=selected["cadence_seconds"],
        approved_limit=selected["limit"], family_quota=False,
        deep_priority_authority="STRATEGY_TRADEABILITY_CAPACITY", catalog_effect="NONE")
    return tuple(dict.fromkeys(map(request, keys))), plan


def _cycle_symbols(store, focus_candidates=None):
    """Return the historical rotation summary, including legacy triples."""
    return _cycle_plan(store, focus_candidates, include_legacy_summary=True)[:4]


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
        "feasible": (bool(focus_count) and capacity >= SIGNAL_MIN_SAMPLES and
                     (rotation_pool == 0 or rotation_capacity >= SIGNAL_MIN_SAMPLES)),
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


def _runtime_readiness(store, *, record_event=False, iol_metrics=None):
    """Distingue cobertura histórica, presupuesto de señales y PIT exacto."""
    focus = _focus_coverage(store)
    _publish_focus_health(store, focus)
    from rc6_iol_market_gateway import iol_primary_enabled
    iol = iol_primary_enabled()
    pit_ready = () if iol else _exact_pit_ready_spot_requests(store)
    ready_set = set(pit_ready)
    # El foco antiguo sólo influye el orden de lectura cuando también prueba
    # PIT/capacidad. No exige 4/8 ni vetará otro ticker exacto READY_PAPER.
    priority = tuple(dict.fromkeys(
        (*(tuple(key) for key in focus["matched"] if tuple(key) in ready_set),
         *pit_ready)))
    open_count = len(store.open_positions())
    symbols, eligible_total, cursor_before, cursor_after, active_plan = _cycle_plan(
        store, focus_candidates=priority, iol=iol, iol_metrics=iol_metrics,
    )
    catalog_read_error = active_plan["catalog_status"] == "CATALOG_READ_ERROR"
    if iol:
        # Static candidates may accumulate signals. Their exact market proof
        # and session GO still belong to the final broker admission.
        candidates = [tuple(key) for key in active_plan["opening_identities"]]
        focus = dict(focus, source="IOL_EXACT_CATALOG_AWAITING_LIVE_EVIDENCE",
                     matched=candidates, matched_count=len(candidates),
                     configured_count=len(symbols), allow_new_openings=False, state="AMARILLO",
                     missing=[])
    elif active_plan.get("dynamic_selection"):
        native_ready = list(active_plan["opening_identities"])
        focus = dict(focus, source="POINT_IN_TIME_TRADEABILITY_APPROVED_CAPACITY",
            matched=native_ready, matched_count=len(native_ready),
            configured_count=len(symbols), minimum_for_openings=1,
            allow_new_openings=bool(native_ready), state="VERDE" if native_ready else "AMARILLO",
            missing=[{"identity": symbol, "reason": "NATIVE_SIGNAL_WINDOW_NOT_READY"}
                for symbol in symbols if symbol not in native_ready])
        _publish_focus_health(store, focus)
    else:
        # Sólo la capacidad exacta de cada identidad seleccionada puede abrir
        # una señal Spot en fallback. El diagnóstico 4/8 ya no decide GO.
        selected_spot = [key for key in symbols if key[1] in PAPER_SPOT_FAMILIES]
        native_ready = [key for key in selected_spot if not catalog_read_error and key in ready_set]
        focus = dict(focus, source="POINT_IN_TIME_EXACT_SPOT_FALLBACK",
            matched=native_ready, matched_count=len(native_ready),
            configured_count=len(selected_spot), minimum_for_openings=1,
            allow_new_openings=bool(native_ready),
            state="VERDE" if native_ready else "AMARILLO",
            missing=[{"identity": key,
                      "reason": "CATALOG_READ_ERROR" if catalog_read_error else "PIT_SPOT_EXACT_NOT_READY"}
                     for key in selected_spot if key not in native_ready])
    # La métrica existe tanto en fallback como en capacidad PIT dinámica;
    # nunca muestra NO_DATA permanente por usar el modo aprobado.
    _health(store, "PAPER_SPOT_PIT_READINESS", focus["state"],
            f"Identidades Spot candidatas en lote: {focus['matched_count']}/{focus['configured_count']}; "
            f"fuente {focus['source']}; cobertura histórica 4/8 sin veto; "
            "sesión, fuente, contrato y riesgo siguen por instrumento.",
            "Catálogo y candidate_identity_v2", success=not iol and bool(focus["matched"]))
    matched_set = set(focus["matched"])
    selected_focus = sum(symbol in matched_set for symbol in symbols)
    feasibility = _sampling_feasibility(
        len(symbols), eligible_total=eligible_total,
        focus_count=selected_focus, open_count=open_count
    )
    feasibility["full_catalog_feasible"] = feasibility["rotation_feasible"]
    feasibility["active_plan"] = active_plan
    feasibility["feasible"] = active_plan["feasible"]
    feasibility["focus_feasible"] = active_plan["focus_feasible"]
    feasibility["rotation_feasible"] = (not catalog_read_error and
                                        active_plan["active_budget"]["status"] == "FEASIBLE")
    if iol:
        dispatch = active_plan.get("iol_stream_coverage", {}).get("dispatch", {})
        feasibility["full_catalog_feasible"] = dispatch.get("rest_full_signal_window_possible", False)
        feasibility["rotation_feasible"] = (feasibility["full_catalog_feasible"] and
                                            dispatch.get("rotation_within_freshness", False))
    if not feasibility["feasible"]:
        reason = "CATALOG_READ_ERROR" if catalog_read_error else "SAMPLING_INFEASIBLE"
        if record_event:
            store.event(reason, json.dumps(feasibility, sort_keys=True))
        _health(store, "PAPER_SIGNAL_SAMPLING", "ROJO",
                reason + " " + json.dumps(feasibility, sort_keys=True), "Runtime PAPER")
    else:
        _health(store, "PAPER_SIGNAL_SAMPLING", "VERDE",
                "Canasta activa con capacidad teórica suficiente; catálogo completo separado: " +
                json.dumps(feasibility, sort_keys=True), "Runtime PAPER", success=True)
    rotation_state = "VERDE" if feasibility["rotation_feasible"] else "AMARILLO"
    _health(store, "PAPER_SIGNAL_ROTATION", rotation_state,
            "Factibilidad rotativa: " + json.dumps(feasibility, sort_keys=True),
            "Runtime PAPER", success=rotation_state == "VERDE")
    return focus, symbols, eligible_total, cursor_before, cursor_after, feasibility


def _scanner_opening_capacity(request, *, focus, feasibility):
    """Presupuesto de señal + PIT Spot, nunca autorización de ejecución.

    El camino dinámico usa las identidades ya verificadas por la selección
    aprobada; el fallback sólo admite un Spot si el *mismo* request está PIT
    READY. El broker mantiene sus gates por identidad, sesión, riesgo y PAPER/0.
    """
    plan = feasibility["active_plan"]
    if plan.get("catalog_status") == "CATALOG_READ_ERROR":
        return False, "CATALOG_READ_ERROR"
    if not feasibility["feasible"]:
        return False, "SCANNER_INFEASIBLE"
    if request not in {tuple(key) for key in plan["opening_identities"]}:
        return False, "COLD_DISCOVERY_NO_SIGNAL_BUDGET"
    if request[1] in PAPER_SPOT_FAMILIES and request not in {
            tuple(key) for key in focus["matched"]}:
        return False, "PAPER_SPOT_EXACT_PIT_NOT_READY"
    return True, ""


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
        if status == "VALID_ROWS_WITH_REJECTIONS":
            status = "PARTIAL"
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
    """The whole exact-key PPI catalog, ordered by its own durable attempt.

    The old three-field caches remain diagnostic evidence. They cannot grant
    coverage or redirect a request to a different market or currency.
    """
    import cq_history_attempt_ledger_hf6 as history_ledger
    history_ledger.init_schema(store)
    with store.connect() as c:
        rows = c.execute("""SELECT f.ticker,f.instrument_type,f.market,f.currency,f.settlement,
          (SELECT MAX(a.attempted_at) FROM history_attempt_ledger_v2 a
           WHERE a.symbol=f.ticker AND a.instrument_type=f.instrument_type
             AND a.settlement=f.settlement AND json_valid(a.metadata_json)
             AND json_extract(a.metadata_json,'$.market')=f.market
             AND json_extract(a.metadata_json,'$.currency')=f.currency) AS attempted_at
          FROM financial_instrument_catalog f
          WHERE f.status='AVAILABLE' AND f.market<>'UNKNOWN' AND f.currency<>'UNKNOWN'
            AND (f.capability LIKE 'READY_PAPER_%' OR f.instrument_type='INDICES')
            AND json_valid(f.metadata_json)
            AND COALESCE(json_extract(f.metadata_json,'$._discovery_source'),
                         json_extract(f.metadata_json,'$.raw._discovery_source'),'PPI_PRIMARY')='PPI_PRIMARY'
          ORDER BY attempted_at IS NOT NULL,attempted_at,f.instrument_type,f.ticker,
                   f.market,f.currency,f.settlement""").fetchall()
    return [tuple(row[:5]) for row in rows]


def _exact_history_target(store, target):
    if len(target)==5:
        symbol, family, market, currency, settlement=target
        metadata=financial_catalog.lookup(store,symbol,family,settlement,market=market,currency=currency)
    elif len(target)==3:
        # Compatibility callers must still resolve a unique complete catalog
        # identity; lookup refuses ambiguous monetary matches.
        symbol,family,settlement=target
        metadata=financial_catalog.lookup(store,symbol,family,settlement)
        market=(metadata or {}).get('market')
        currency=(metadata or {}).get('currency')
    else:
        return None
    if not metadata or not market or market=='UNKNOWN' or currency not in {'ARS','USD','USD_MEP','USD_CCL'}:
        return None
    return (symbol,family,market,currency,settlement),metadata


def _ppi_history_request_is_ambiguous(connection, identity):
    """PPI history accepts only ticker/family/term, never market or currency.

    A second known catalog identity for that request makes an unlabelled
    provider payload impossible to attribute to the requested exact series.
    The primary catalog key starts with ticker/family, so this read is bounded
    to one provider request and stops at its first conflicting identity.
    """
    symbol,family,market,currency,settlement=identity
    return connection.execute("""SELECT 1 FROM financial_instrument_catalog
      WHERE ticker=? AND instrument_type=? AND settlement=?
        AND (market<>? OR currency<>?) LIMIT 1""",
      (symbol,family,settlement,market,currency)).fetchone() is not None


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
                "SELECT state FROM rc6_history_cutoff_repair_runs WHERE cutoff=?",
                (HISTORY_CUTOFF.isoformat(),),
            ).fetchone()
            return bool(row and str(row[0]).upper() == "COMPLETE")
    except (sqlite3.Error, OSError):
        return False


def _history_end_date(now=None):
    now = now or datetime.now(TZ)
    if now.tzinfo is None or now.utcoffset() is None:
        return None
    now = now.astimezone(TZ)
    # Spot close at 17:00 does not imply published PPI EOD RAW evidence.
    if now < byma_eod_publication_floor(now):
        return None
    day = now.date()
    while day.year == BYMA_CALENDAR_AUDITED_YEAR and not _business_day(day):
        day -= timedelta(days=1)
    return day if day.year == BYMA_CALENDAR_AUDITED_YEAR else None


def _history_daily_session(now):
    day = _history_end_date(now)
    if day is not None and day > HISTORY_CUTOFF and day == now.astimezone(TZ).date():
        return day
    return None


def _history_repair_lock_path(history_store):
    return Path(history_store.path).parent / ".rc6-history-cutoff-repair.lock"


def _history_migration_blocked(store, reason):
    """Preserve the legacy dataset until a complete, reviewed copy is available."""
    store.event("HISTORY_V2_BLOCKED", reason)
    _health(store, "PPI_PRODUCTION_HISTORY", "AMARILLO",
            f"Historico V2 NO_VERIFICADO: {reason}; source preserved; readiness=NONE",
            "PPI_PRODUCTION_HISTORY")
    return 0


def _download_histories(reader, store, *, incremental_only=False, session_only=False):
    """Keep date-range recovery behind the annual cutoff and share its write lock."""
    if not session_only and not _history_cutoff_repair_complete():
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
            if incremental_only:
                raise ValueError('HISTORY_RECOVERY_LOCK_HELD') from None
            return 0
        try:
            # HistoricalStore validates schema metadata directly in a bounded
            # read-only transaction when the database exceeds the private-copy
            # budget. A size-only veto here would also reject a *migrated*
            # database and cannot repair the legacy schema. Keep the scratch
            # limit for smaller sources; fail closed on the actual schema.
            try:
                return _download_histories_locked(reader, store, history_store,
                                                  incremental_only=incremental_only,
                                                  session_only=session_only)
            except ValueError as exc:
                if str(exc) in {"HISTORY_COPY_MIGRATION_REQUIRED",
                                "HISTORY_CLOSE_COPY_MIGRATION_REQUIRED",
                                "SOURCE_BYTE_BUDGET_EXHAUSTED"}:
                    _history_migration_blocked(store, str(exc))
                    if incremental_only:
                        raise
                    return 0
                raise
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _history_provider_instrument_type(metadata, canonical_family):
    """Use PPI's literal provider family only for the read-only history request.

    The canonical family remains authoritative inside Porota storage/readiness.
    This prevents aliases such as OBLIGACIONES -> ON from being sent back to
    PPI as if they were provider enum values.
    """
    raw = (metadata or {}).get("raw") if isinstance(metadata, dict) else {}
    provider = str((raw or {}).get("_provider_instrument_type") or "").strip().upper()
    return provider or str(canonical_family or "").strip().upper()


def _history_covered_target_count(connection, targets):
    """Count covered production-history identities inside the same target scope.

    The numerator and denominator must describe the same identity set. Legacy
    production_history may contain rows from older/wider scopes, so a global
    COUNT(*) can legitimately exceed the current target universe and must never
    be rendered as coverage.
    """
    target_keys = {
        (
            str(symbol or "").strip().upper(),
            str(instrument_type or "").strip().upper(),
            str(settlement or "").strip().upper(),
        )
        for symbol, instrument_type, settlement in targets
    }
    if not target_keys:
        return 0
    covered_keys = {
        (
            str(row[0] or "").strip().upper(),
            str(row[1] or "").strip().upper(),
            str(row[2] or "").strip().upper(),
        )
        for row in connection.execute(
            """SELECT symbol,instrument_type,settlement
               FROM production_history WHERE row_count>0"""
        )
    }
    return len(target_keys & covered_keys)


def _verified_ppi_eod_keys(history_store, exact_keys, session, at):
    """Find genuine same-session, five-key PPI RAW EOD publications.

    Request dates and historical coverage cannot substitute for the actual
    stored source, RAW basis and postclose version-known timestamp.
    """
    if at.tzinfo is None or at.utcoffset() is None:
        return set()
    now = at.astimezone(TZ)
    if now.date() != session:
        return set()
    floor = byma_eod_publication_floor(now)
    if now < floor:
        return set()
    from ct_ppi_history_salvage_hf6 import VERIFIED_PPI_HISTORY_CONTRACTS

    verified = set()
    with history_store.connect() as conn:
        for identity in dict.fromkeys(exact_keys):
            if len(identity) != 5:
                continue
            rows = conn.execute("""SELECT source,version_known_at,metadata_json
              FROM history_canonical_v2 WHERE symbol=? AND instrument_type=?
                AND market=? AND currency=? AND settlement=? AND date=?
                AND price_basis='RAW' AND adjustment_basis='RAW_NO_ADJUSTMENT' AND adjusted=0""",
                (*identity, session.isoformat())).fetchall()
            # Canonical full-key+date RAW identity is unique in the actual
            # schema. Any duplicate or missing record means no source proof.
            if len(rows) != 1 or rows[0][0] != "PPI_PRODUCTION_HISTORY":
                continue
            try:
                metadata = json.loads(rows[0][2])
                if (not isinstance(metadata, dict)
                        or metadata.get("history_quality") not in {"FULL_OHLC", "FULL_OHLCV"}
                        or metadata.get("provider_contract_id") not in VERIFIED_PPI_HISTORY_CONTRACTS):
                    continue
                known = datetime.fromisoformat(str(rows[0][1]).replace("Z", "+00:00"))
                if known.tzinfo and known.utcoffset() is not None and floor <= known <= now:
                    verified.add(identity)
            except (TypeError, ValueError):
                continue
    return verified


def _download_histories_locked(reader, store, history_store, *,
                               incremental_only=False, session_only=False):
    retention_now = datetime.now(TZ)
    end = _history_daily_session(retention_now) if session_only else _history_end_date()
    if end is None:
        return 0
    import ct_ppi_history_salvage_hf6 as history_salvage

    archive_without_contract = session_only and not history_salvage.VERIFIED_PPI_HISTORY_CONTRACTS
    if session_only:
        # The shared cutoff lock is held here. Persist the existing per-day
        # attempt before a GET so a restarted scanner cannot repeat the batch.
        if not _source_sync_due(store, "PPI_PRODUCTION_HISTORY", retention_now):
            return 0
        _sync_state(store, "PPI_PRODUCTION_HISTORY", "AMARILLO", 0,
                    f"session_requested={end.isoformat()}; HISTORY_SESSION_ATTEMPT_STARTED")
    from rc6_data_retention import bounded_daily_start
    import cu_history_store_v2_hf6 as history_v2
    import cq_history_attempt_ledger_hf6 as history_ledger
    history_v2.init_schema(history_store)
    history_ledger.init_schema(store)
    total, batch_statuses = 0, []
    archived_ohlcv = 0
    exact_targets, requests = [], []
    already_verified = 0
    for target in _historical_targets(store):
        resolved=_exact_history_target(store,target)
        if resolved is None:
            batch_statuses.append('ERROR')
            store.event('HISTORY_V2_ERROR',f'{target[0]}: HISTORY_FULL_IDENTITY_MISSING')
            continue
        identity,metadata=resolved
        if identity in exact_targets:
            continue
        exact_targets.append(identity)
        if session_only:
            requests.append((identity, end, end, metadata))
            if archive_without_contract and len(requests) >= HISTORY_BATCH_LIMIT:
                break
            continue
        symbol,family,market,currency,settlement=identity
        with history_store.connect() as c:
            row=c.execute("""SELECT MAX(date) FROM history_canonical_v2
              WHERE symbol=? AND instrument_type=? AND market=? AND currency=?
                AND settlement=? AND price_basis='RAW' AND adjustment_basis='RAW_NO_ADJUSTMENT'""",
              identity).fetchone()
        latest=str(row[0])[:10] if row and row[0] else None
        if incremental_only and not latest:
            # A post-close recovery cannot silently turn into an annual
            # bootstrap when an exact series has no canonical baseline.
            continue
        requested_start=(date.fromisoformat(latest)+timedelta(days=1)
                         if latest else end-timedelta(days=365))
        start=bounded_daily_start(requested_start,retention_now)
        if start<=end:
            request_end=min(end,start+timedelta(days=29)) if incremental_only else end
            requests.append((identity,start,request_end,metadata))
    if session_only and not archive_without_contract:
        verified = _verified_ppi_eod_keys(history_store, exact_targets, end, retention_now)
        requests = [request for request in requests if request[0] not in verified]
        already_verified = len(verified)

    from bl_candle_engine import canonical
    from fb_raw_evidence_exact_v1 import enabled as exact_enabled, archive_wrapper as archive_exact
    for identity,start,request_end,metadata in requests[:HISTORY_BATCH_LIMIT]:
        symbol,family,market,currency,settlement=identity
        attempted=now_iso()
        try:
            # Run only for the bounded scheduled batch. The existing attempt
            # ledger rotates rejected identities instead of retrying the same
            # ambiguous provider request every cycle.
            with store.connect() as c:
                if _ppi_history_request_is_ambiguous(c,identity):
                    raise ValueError('HISTORY_PROVIDER_IDENTITY_AMBIGUOUS')
            payload=reader.history(symbol,_history_provider_instrument_type(metadata,family),settlement,start,request_end)
            attempted=now_iso()
            row_key=canonical([list(identity),start.isoformat(),request_end.isoformat(),attempted])
            wrapper={'symbol':symbol,'asset_class':family,'market':market,'currency':currency,
                'settlement':settlement,'date_from':start.isoformat(),'date_to':request_end.isoformat(),
                'metadata':metadata,'payload_json':json.dumps(payload,ensure_ascii=False,default=str),
                'authority':'RAW_PROVIDER_EVIDENCE_ONLY'}
            if exact_enabled():
                archive_exact(row_key=row_key,wrapper=wrapper,recorded_at=attempted,quality='UNVALIDATED')
            else:
                from bl_candle_engine import archive_raw
                with store.connect() as c:
                    archive_raw(c,origin='PPI_HISTORY',row_key=row_key,payload=wrapper,
                        recorded_at=attempted,quality='UNVALIDATED')
            # Universal sink validation, atomic steps and its durable attempt
            # own accepted rows. Filtering a bad provider row here would hide it.
            result=history_salvage.ingest_ppi_payload(store,symbol=symbol,instrument_type=family,
                market=market,currency=currency,settlement=settlement,payload=payload,
                requested_from=start,requested_to=request_end,attempted_at=attempted,history_store=history_store)
            if archive_without_contract:
                # The archive and rejection ledger retain the evidence. The
                # ingest still requires a verified contract for canonical rows.
                archived_ohlcv += _history_count(payload,as_of=attempted,date_from=start,date_to=request_end)
            count=int(result['full_ohlc_rows'])
            status=result['storage_quality']
            total+=count
            batch_statuses.append(status)
            with store.connect() as c:
                # Retain the old diagnostic cache only when the three-field
                # identity is unique; production coverage never reads it.
                unique=c.execute("""SELECT COUNT(*) FROM financial_instrument_catalog
                  WHERE ticker=? AND instrument_type=? AND settlement=? AND status='AVAILABLE'""",
                  (symbol,family,settlement)).fetchone()[0]==1
                if unique:
                    c.execute('INSERT OR REPLACE INTO production_history_attempts VALUES(?,?,?,?,?,?,?)',
                        (symbol,family,settlement,attempted,status,count,'NON_BINDING_LEGACY_CACHE; canonical full identity in v2 ledger'))
                    if status=='VALID_PAYLOAD':
                        c.execute('INSERT OR REPLACE INTO production_history VALUES(?,?,?,?,?,?,?,?)',
                            (symbol,family,settlement,start.isoformat(),request_end.isoformat(),attempted,count,
                             json.dumps(payload,ensure_ascii=False,default=str)))
            store.event('HISTORY_V2_INGEST',f'{symbol}: accepted={count}; rejected={result["rejected_rows"]}; full_key={identity}; readiness=NONE')
        except Exception as exc:
            batch_statuses.append('ERROR')
            # The provider-key rejection is a fixed, safe diagnostic code;
            # arbitrary broker exceptions must never leak payloads or URLs.
            reason=('HISTORY_PROVIDER_IDENTITY_AMBIGUOUS'
                    if str(exc)=='HISTORY_PROVIDER_IDENTITY_AMBIGUOUS'
                    else type(exc).__name__)
            history_ledger.append_attempt(store,history_ledger.HistoryAttempt(
                symbol=symbol,instrument_type=family,settlement=settlement,source='PPI_PRODUCTION_HISTORY',
                attempted_at=attempted,state='ERROR',requested_from=start.isoformat(),requested_to=request_end.isoformat(),
                error_class=type(exc).__name__,detail=f'{reason}; retry follows durable full-key saga',
                metadata={'market':market,'currency':currency,'identity':list(identity),
                          'guard_reason':reason,'readiness_implication':'NONE'}))
            store.event('HISTORY_V2_ERROR',f'{symbol}: {reason}')
    # Certify only the bounded exact-key batch requested in this cycle.
    # Neither a requested end date nor global historical coverage proves EOD.
    batch_keys=[identity for identity,_,_,_ in requests[:HISTORY_BATCH_LIMIT]]
    with history_store.connect() as c:
        coverage=history_v2.coverage_inventory(
            c, batch_keys if session_only else exact_targets, as_of=now_iso(),
            consumer='PPI_HISTORY_INGEST_MONITOR',session=end.isoformat(),minimum_rows=30)
    exact_eod = 0
    if not archive_without_contract:
        exact_eod = len(_verified_ppi_eod_keys(history_store, batch_keys, end, datetime.now(TZ)))
    exact_required=len(batch_keys)
    current_session_verified=bool(exact_required and exact_eod==exact_required)
    semantics=_history_batch_semantics(batch_statuses)
    state=semantics['state']
    if not batch_statuses or archived_ohlcv:
        state='AMARILLO'
    if state=='VERDE' and not current_session_verified:
        state='AMARILLO'
    provenance = (f"HISTORY_PROVENANCE_UNVERIFIED; ohlcv_archivado_sin_certificar={archived_ohlcv}; "
                  if archive_without_contract else
                  "source_raw_provenance=OBSERVER_PPI_HISTORY_CANONICAL_V2_RAW_FULL_KEY; ")
    detail=(f"session={end.isoformat()}; "
        f"source_session_verified={end.isoformat()}:{exact_eod}/{exact_required}; "
        f"{provenance}"
        f"Lote aceptado={semantics['full_valid']}/{min(len(requests),HISTORY_BATCH_LIMIT)}; "
        f"parciales={semantics['partial_with_valid_evidence']}; errores={semantics['hard_failures']}; "
        f"cobertura exacta {coverage['covered']}/{coverage['denominator']}; "
        f"suficientes30={coverage['sufficient']}; faltantes={coverage['missing']}; "
        f"cohort={coverage['cohort_sha256']}; consumer={coverage['consumer']}; "
        f"cut={coverage['as_of']}; session_requested={coverage['session']}; readiness=NONE; "
        f"same_session_already_verified={already_verified}; "
        f"by_currency={json.dumps(coverage['by_currency'],sort_keys=True)}; "
        f"{total} filas FULL_OHLC válidas hasta {end.isoformat()}.")
    usable=bool(semantics['usable'] and current_session_verified and state=='VERDE')
    _sync_state(store,'PPI_PRODUCTION_HISTORY',state,total,detail,success=usable)
    _health(store,'PPI_PRODUCTION_HISTORY',state,detail,'PPI Producción',success=usable)
    return total


def _source_sync_due(store, source, now=None):
    """Use the *local* trading date for UTC-persisted source attempts.

    A UTC timestamp after 21:00 ART already starts the following UTC day.
    Comparing its literal YYYY-MM-DD to today in Argentina would trigger
    repeated catalog/history downloads on each CLOSED scanner cycle.
    """
    now = now or datetime.now(TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    today = now.astimezone(TZ).date()
    with store.connect() as connection:
        cursor = connection.execute("SELECT * FROM source_sync WHERE source=?", (source,))
        row = cursor.fetchone()
        receipt = dict(zip((column[0] for column in cursor.description), row)) if row else {}
    last_attempt = receipt.get("last_attempt_at")
    if not last_attempt:
        return True
    try:
        raw = str(last_attempt).strip()
        if len(raw) == 10:
            # Older local-day receipts predate the UTC timestamp format.
            attempted_date = date.fromisoformat(raw)
        else:
            attempt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            # Interpret legacy naive timestamps as local, never as the host's
            # possibly different OS timezone.
            if attempt.tzinfo is None:
                attempt = attempt.replace(tzinfo=TZ)
            attempted_local = attempt.astimezone(TZ)
            attempted_date = attempted_local.date()
            status = receipt.get("status")
            if status and source == "PPI_PRODUCTION_CATALOG" and attempted_date == today:
                if status == CATALOG_DEFERRED_STATUS:
                    # Any longer provider hold still applies at the gateway.
                    return (now - attempt).total_seconds() >= LOGIN_COOLDOWN_SECONDS
                if status == CATALOG_GATEWAY_DEFERRED_STATUS:
                    return (now - attempt).total_seconds() >= GATEWAY_LOCAL_ATTACH_RETRY_SECONDS
                if status == "ROJO":
                    # Unclassified failures, including older releases, can be
                    # revisited once after the same session closes.
                    close = now.astimezone(TZ).replace(
                        hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE,
                        second=0, microsecond=0)
                    return attempted_local < close <= now
        # A future receipt or clock skew does not authorize a provider storm.
        return attempted_date < today
    except (ValueError, TypeError):
        # Corrupt receipts stay NO_VERIFICADO; do not retry every 5 seconds.
        return False


def _history_daily_sync_due(store, now=None):
    """The current session can advance independently of prior data gaps."""
    now = now or datetime.now(TZ)
    return (_history_daily_session(now) is not None
            and _source_sync_due(store, "PPI_PRODUCTION_HISTORY", now))


def _daily_sync_needed(store, now=None):
    """Trade-session sync only; never redownload Friday on a closed weekend."""
    now = now or datetime.now(TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    if not _business_day(now.astimezone(TZ).date()):
        return False
    return (
        _source_sync_due(store, "PPI_PRODUCTION_CATALOG", now)
        or _history_daily_sync_due(store, now)
    )


def _daily_sync(reader, store, now=None):
    """Return true when the scanner must drop its obsolete local reader."""
    now = now or datetime.now(TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    now = now.astimezone(TZ)
    # A CLOSED scanner may remain alive across weekends/holidays.  The
    # historical backfill has a separate explicit business-day admission.
    # Do not re-fetch the previous session's catalog/history just because a
    # non-trading calendar date changed.
    if not _business_day(now.date()):
        return
    catalog_due = _source_sync_due(store, "PPI_PRODUCTION_CATALOG", now)
    history_due = _history_daily_sync_due(store, now)
    if not catalog_due and not history_due:
        return
    try:
        catalog = _download_catalog(reader, store) if catalog_due else 0
        histories = _download_histories(reader, store, session_only=True) if history_due else 0
        store.event(
            "DAILY_READONLY_SYNC",
            f"{catalog} instrumentos; {histories} filas históricas; "
            f"catalog_due={catalog_due}; history_due={history_due}.",
        )
    except _CatalogGatewayDisconnected as exc:
        reader.close()
        store.event("PPI_GATEWAY_REATTACH_REQUIRED", str(exc))
        return True
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:500]}"
        _sync_state(store, "PPI_PRODUCTION_DAILY_SYNC", "ROJO", 0, detail)
        store.event("DAILY_READONLY_SYNC_ERROR", detail)


def _background_ingest_due(store, now=None):
    """At most one historical attempt after the BYMA EOD publication floor."""
    now = now or datetime.now(TZ)
    if now.tzinfo is None or now.utcoffset() is None:
        return False
    local_now = now.astimezone(TZ)
    if not _business_day(local_now.date()):
        return False
    if local_now < byma_eod_publication_floor(local_now):
        return False
    with store.connect() as connection:
        source = connection.execute("""SELECT last_attempt_at FROM source_sync
          WHERE source='PPI_PRODUCTION_HISTORY'""").fetchone()
        background = connection.execute("""SELECT checked_at FROM api_health
          WHERE component='PPI_BACKGROUND_INGEST'""").fetchone()
    # A blocked history migration cannot advance source_sync. Its own health
    # timestamp still records the attempt, including a failed one, so CLOSED
    # does not retry and write another event every scanner poll (~5 seconds).
    for row in (source, background):
        if not row or not row[0]:
            continue
        try:
            last = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
            if last.tzinfo is None:
                last = last.replace(tzinfo=TZ)
            if last.astimezone(TZ).date() >= local_now.date():
                return False
        except (TypeError, ValueError):
            continue
    return True

def _background_ingest(reader, store, *, force=False):
    """Completa históricos una vez por rueda después del cierre; force no salta el límite."""
    if not _background_ingest_due(store):
        return None
    try:
        rows = _download_histories(reader, store, session_only=True)
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


def _run_command(store, reader, command, gemini_gate=None, login_interlock=None):
    from rc6_iol_market_gateway import iol_primary_enabled

    command_id, name = command
    if iol_primary_enabled() and (name == "LOGIN_AND_SYNC" or name.startswith(
            (_PPI_OPEN_COMMAND_PREFIX, _PPI_POSTCLOSE_COMMAND_PREFIX))):
        _finish_command(store, command_id, "NOT_APPLICABLE", "IOL_REST_PROFILE_PPI_COMMAND_RETIRED")
        return reader
    if name.startswith(_PPI_OPEN_COMMAND_PREFIX):
        return _run_ppi_open_command(store, reader, command_id, name)
    if name.startswith(_PPI_POSTCLOSE_COMMAND_PREFIX):
        return _run_ppi_postclose_command(store, reader, command_id, name)
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
    if reader is None and login_interlock is not None:
        # A caller-supplied manual interlock still gates diagnostic commands.
        # Normal scanner startup delegates the actual login reservation to
        # the gateway owner and passes no second scanner-local reservation.
        decision = login_interlock.reserve()
        if decision != "ALLOWED":
            _finish_command(store, command_id,
                            "COOLDOWN" if decision == "COOLDOWN" else "ERROR",
                            "PPI_LOGIN_INTERLOCK_" + decision)
            store.state(process_state="DEGRADED",
                        ppi_auth="COOLDOWN" if decision == "COOLDOWN" else "ERROR",
                        real_orders_sent=0, detail="Comando manual bloqueado por interlock.")
            return None
    try:
        if reader is None:
            from rc6_ppi_gateway_reader_adapter import connect_paper_gateway_reader
            reader = connect_paper_gateway_reader(store.path, consumer="scanner")
            store.event("PPI_GATEWAY_ATTACHED", "consumer=scanner_manual;no_sdk_login")
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
    except _CatalogGatewayDisconnected as exc:
        reader.close()
        reason = str(exc)
        store.event("PPI_GATEWAY_REATTACH_REQUIRED", reason)
        _finish_command(store, command_id, "PARTIAL", reason)
        store.state(ppi_auth="ERROR", detail=reason)
        return None
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
    from rc6_iol_market_gateway import iol_primary_enabled
    title, detail = messages[current]
    if iol_primary_enabled():
        detail = {
            "PREOPEN": "Preapertura IOL. La admisión PAPER requiere evidencia de rueda.",
            "OPEN": "Rueda abierta. La admisión PAPER sigue en HOLD hasta calificar IOL.",
            "CLOSED": "Rueda cerrada. Se conservan históricos y dashboard.",
        }[current]
    at = now_iso()
    local_day = datetime.now(TZ).date().isoformat()
    try:
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            enqueue(c, f"session-phase:{current}:{local_day}", "SESSION_PHASE",
                    f"{title}\n{detail}\n{at}", at, priority=15)
    except Exception as exc:
        store.event("PHASE_NOTICE_ERROR", f"{current}: {type(exc).__name__}")


def _pulse_observed_progress(store, last_pulse, *, monotonic_now=None):
    """Beat only after a completed scanner stage, at most once per 25 seconds.

    A blocked provider call cannot refresh this pulse. The existing cycle
    metrics still distinguish a completed cycle from per-symbol progress.
    """
    instant = time.monotonic() if monotonic_now is None else monotonic_now
    if instant - last_pulse < 25:
        return last_pulse
    store.state(heartbeat_at=now_iso(), real_orders_sent=0)
    return instant


def _write_scanner_metrics(store, stage, values):
    """Persist optional telemetry without making contention fatal to scanning.

    Trading writes never use this path. A deferred metric remains missing and
    produces an explicit, redacted diagnostic; it never becomes a success.
    """
    statements = {
        "SYMBOL": """INSERT INTO symbol_cycle_metrics
          (cycle_started_at,symbol,latency_ms,state,detail) VALUES(?,?,?,?,?)""",
        "UNIVERSE": """INSERT INTO universe_cycle_metrics
          (started_at,finished_at,eligible_total,selected_count,successful_count,
           failed_count,duration_seconds,cursor_before,cursor_after,recommended_limit,detail)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
    }
    statement = statements[stage]
    try:
        with closing(store.connect()) as c:
            # Local budget for optional metrics, never the broker connection.
            c.execute("PRAGMA busy_timeout=200")
            with c:
                c.execute(statement, values)
                if stage == "UNIVERSE":
                    c.execute("DELETE FROM symbol_cycle_metrics WHERE id NOT IN (SELECT id FROM symbol_cycle_metrics ORDER BY id DESC LIMIT 10000)")
                    c.execute("DELETE FROM universe_cycle_metrics WHERE id NOT IN (SELECT id FROM universe_cycle_metrics ORDER BY id DESC LIMIT 5000)")
    except sqlite3.OperationalError as exc:
        code = getattr(exc, "sqlite_errorcode", None)
        # Extended BUSY/LOCKED codes retain the primary code in the low byte.
        if not isinstance(code, int) or code & 0xff not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise
        print(json.dumps({"event": "SCANNER_METRICS_DEFERRED", "stage": stage,
                          "sqlite_errorcode": code,
                          "sqlite_errorname": getattr(exc, "sqlite_errorname", None),
                          "observed_at": now_iso(), "persisted": False}),
              file=sys.stderr, flush=True)
        return False
    return True


CAUCION_LEDGER_RECHECK_SECONDS = 3600


def _scanner_trade_temporal_rejection(q, broker, store, *, checked_at, observe_only=False):
    """Reject stale trade for entry while preserving independent fresh-Book EXIT.

    A stale Book remains fully rejected. No entry path sees a rejected trade.
    """
    data_error = q.time_error(
        checked_at, require_trade=True,
        max_age_seconds=broker.quote_max_age_seconds,
        max_trade_age_seconds=broker.trade_max_age_seconds)
    if not data_error:
        return ""
    if not observe_only:
        if q.time_error(checked_at, max_age_seconds=broker.quote_max_age_seconds):
            broker.record_observation_rejection(q, data_error)
        else:
            broker.on_quote(
                q, allow_new_openings=False,
                opening_block_reason=data_error,
                evaluate_when_blocked=False)
    store.event("DATA_REJECTED", f"{q.symbol}: {data_error}")
    return data_error


def _settle_cauciones_fail_closed(broker, blocked, next_retry, tick):
    """Keep an invalid PAPER caucion ledger out of the admission loop.

    A broken historical fee authority must not be silently migrated, used for
    cash/equity, or retried on every 5-second scanner tick. The durable DB
    remains authoritative; an hourly read-validation can detect a separately
    reviewed reconciliation without any automatic ledger modification.
    """
    if broker is None or (blocked and tick < next_retry):
        return blocked, next_retry
    try:
        broker.settle_cauciones()
    except ValueError as exc:
        if not str(exc).startswith("CAUCION_LEDGER_INVALID:"):
            raise
        return True, tick + CAUCION_LEDGER_RECHECK_SECONDS
    return False, 0.0


def run():
    from rc6_iol_market_gateway import (
        connect_iol_gateway_reader, iol_gateway_reader_needs_reattach, iol_primary_enabled,
    )
    from rc6_ppi_gateway_reader_adapter import connect_paper_gateway_reader

    iol = iol_primary_enabled()
    source_label = "IOL REST" if iol else "PPI Producción"
    auth_component = "IOL_REST_AUTH" if iol else "PPI_PRODUCTION_AUTH"
    market_component = "IOL_REST_MARKETDATA" if iol else "PPI_PRODUCTION_MARKETDATA"
    read_error_code = _scanner_iol_error if iol else _scanner_ppi_error
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    # Sólo ante un arranque anormalmente lento se vuelca la pila Python para
    # localizar I/O local; no contiene ni lee secretos.
    faulthandler.enable()
    # Startup watchdog only: repeating dumps every 45s made a healthy long-lived
    # scanner emit perpetual "Timeout (0:00:45)!" traces, obscuring real crashes.
    # Disarm it as soon as the runtime reaches the normal loop below.
    faulthandler.dump_traceback_later(45, repeat=False)
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
                detail=f"Inicializando Gateway de sólo lectura {source_label} antes del motor PAPER.")
    # La fase de mercado se resuelve dentro del ciclo, después de publicar
    # BOOT_COMMAND/BOOT_CALENDAR. Así un calendario lento nunca deja al
    # operador con STARTING/CHECKING sin evidencia del punto de arranque.
    reader = None
    login_interlock = None  # only gateway reserves the physical PPI login
    quotes = {}
    # Diagnostic evidence only: reuse the scanner's existing current+book
    # result. PRODUCTION_PAPER remains an execution mode, not PPI provenance.
    scanner_primary = None if iol else ScannerPrimaryRecorder(
        Path(os.getenv("DATA_DIR", "/app/data")) / "market" / PPI_SCANNER_RECEIPT_FILENAME)
    last_public_check = 0.0
    last_direct_rest_probe = 0.0
    direct_probe_family_cursor = 0
    direct_probe_after = {}
    last_structured_capture = 0.0
    last_readiness_check = 0.0
    last_complementary_reconcile = 0.0
    last_caucion_live_refresh = 0.0
    caucion_ledger_blocked = False
    next_caucion_ledger_recheck = 0.0
    next_login_at = 0.0
    last_phase = None  # Publicar BOOT_* antes de resolver el calendario.
    startup_watchdog_armed = True
    try:
        while not STOP:
            if startup_watchdog_armed:
                faulthandler.cancel_dump_traceback_later()
                startup_watchdog_armed = False
            # A contract can mature when CLOSED; invalid historical PAPER fee
            # evidence blocks every new admission and is rechecked hourly.
            caucion_ledger_blocked, next_caucion_ledger_recheck = (
                _settle_cauciones_fail_closed(
                    broker, caucion_ledger_blocked, next_caucion_ledger_recheck,
                    time.monotonic()))
            # Breadcrumbs de arranque: permiten distinguir qué paso local
            # antecede al login sin tocar órdenes ni datos de mercado.
            if reader is None and not caucion_ledger_blocked:
                store.state(session_state="BOOT_COMMAND", ppi_auth="NOT_ATTEMPTED",
                            heartbeat_at=now_iso(), real_orders_sent=0,
                            detail="Arranque: revisando comandos locales.")
            # La lectura de PPI tiene prioridad sobre mantenimiento y sondas.
            # No se bloquea una rueda recién abierta con backups/reportes.
            command = _claim_command(store)
            if command:
                reader = _run_command(store, reader, command, None, login_interlock)
            if reader is None and not caucion_ledger_blocked:
                store.state(session_state="BOOT_CALENDAR", ppi_auth="NOT_ATTEMPTED",
                            heartbeat_at=now_iso(), real_orders_sent=0,
                            detail="Arranque: verificando calendario operativo.")
            phase = _market_phase()
            previous_phase = last_phase
            if phase != last_phase:
                _announce_phase(store, previous_phase, phase)
                last_phase = phase
            if caucion_ledger_blocked:
                store.state(process_state="DEGRADED",
                            session_state="MARKET_CLOSED" if phase == "CLOSED" else phase,
                            heartbeat_at=now_iso(), real_orders_sent=0,
                            detail="CAUCION_LEDGER_INVALID: PAPER admission blocked; "
                                   "cost evidence requires reconciliation.")
                if not iol:
                    last_complementary_reconcile = _caucion_blocked_closed_catalog_tick(
                        store, phase, last_complementary_reconcile)
                if phase != "OPEN":
                    time.sleep(max(30, COMMAND_POLL_SECONDS))
                    continue
            # Autenticación, catálogo e históricos son útiles también durante
            # noches, fines de semana y feriados. Sólo current/book y la
            # estrategia quedan condicionados a rueda abierta.
            attached = reader is None
            if attached:
                if time.time() < next_login_at:
                    store.state(process_state="DEGRADED", ppi_auth="NOT_ATTEMPTED" if iol else "COOLDOWN",
                                session_state=_cooldown_session_state(phase),
                                heartbeat_at=now_iso(), real_orders_sent=0,
                                detail=f"Gateway {source_label} en espera de reconexión local.")
                    time.sleep(COMMAND_POLL_SECONDS)
                    continue
                store.state(session_state="BOOT_IOL_GATEWAY" if iol else "BOOT_PPI_GATEWAY",
                            ppi_auth="NOT_ATTEMPTED", heartbeat_at=now_iso(), real_orders_sent=0,
                            detail=f"Arranque: conectando al Gateway {source_label}.")
                try:
                    store.state(session_state="BOOT_IOL_GATEWAY_IDENTITY" if iol else "BOOT_PPI_GATEWAY_IDENTITY",
                                ppi_auth="NOT_ATTEMPTED",
                                heartbeat_at=now_iso(), real_orders_sent=0,
                                detail="Arranque: comprobando PID/generación del propietario.")
                    if iol:
                        reader = connect_iol_gateway_reader(store.path, consumer="scanner")
                    else:
                        reader = connect_paper_gateway_reader(store.path, consumer="scanner")
                    store.event("IOL_GATEWAY_ATTACHED" if iol else "PPI_GATEWAY_ATTACHED",
                                "consumer=scanner;no_sdk_login")
                    _health(store, auth_component, "AMARILLO" if iol else "VERDE",
                            "Gateway local verificado; sesión IOL se comprueba al leer." if iol else
                            "Gateway local autenticado; identidad del productor comprobada.",
                            source_label, success=not iol)
                    # A successful login can follow a 900s interlock hold.
                    # Clear its DEGRADED process state atomically with auth;
                    # readiness remains STARTING until the PAPER engine and
                    # closed-session services finish initialization.
                    store.state(process_state="STARTING", ppi_auth="NOT_ATTEMPTED" if iol else "OK",
                                session_state=phase, heartbeat_at=now_iso(),
                                real_orders_sent=0,
                                detail="Gateway único conectado; inicializando motor PAPER.")
                except Exception as exc:
                    reason = read_error_code(exc)
                    _health(store, auth_component, "ROJO", f"{type(exc).__name__}: {reason}", source_label)
                    store.state(process_state="DEGRADED", ppi_auth="NOT_ATTEMPTED" if iol else "ERROR",
                                heartbeat_at=now_iso(), detail=f"Gateway {source_label}: {reason}")
                    local_attach_failure = iol or reader is None or _scanner_gateway_reader_lost(exc)
                    if reader is not None:
                        reader.close()
                    reader = None
                    next_login_at = time.time() + (
                        GATEWAY_LOCAL_ATTACH_RETRY_SECONDS if local_attach_failure
                        else LOGIN_COOLDOWN_SECONDS)
                    time.sleep(COMMAND_POLL_SECONDS)
                    continue
            if broker is None:
                # The inherited PPI receipt cannot authorize opens in the IOL
                # profile. A live IOL receipt and exact authority still have to
                # be qualified before this testing profile can admit entries.
                broker = broker_from_environment(store, ai_gate=None, require_ai=False,
                                                 ai_mode="OFF", context_fn=None,
                                                 admission_guard=paper_admission_guard)
                # Validate before the first strategy tick, including startup
                # during OPEN with an already invalid historical ledger.
                caucion_ledger_blocked, next_caucion_ledger_recheck = (
                    _settle_cauciones_fail_closed(
                        broker, caucion_ledger_blocked, next_caucion_ledger_recheck,
                        time.monotonic()))
                if caucion_ledger_blocked and phase != "OPEN":
                    continue
            if phase == "PREOPEN" and not iol:
                # Verify the scanner's pin even when preopen issues no market GET.
                try:
                    reason = ("" if reader.metrics.get("authenticated") is True
                              else "PPI_GATEWAY_SESSION_RECOVERY_PENDING")
                except Exception as exc:
                    if not _scanner_gateway_reader_lost(exc):
                        raise
                    reason = _scanner_ppi_error(exc)
                if reason:
                    reader.close()
                    reader = None
                    next_login_at = time.time() + GATEWAY_LOCAL_ATTACH_RETRY_SECONDS
                    _health(store, "PPI_PRODUCTION_AUTH", "ROJO", reason, "PPI Producción")
                    store.event("PPI_GATEWAY_REATTACH_REQUIRED", reason)
                    store.state(process_state="DEGRADED", ppi_auth="ERROR", session_state=phase,
                                heartbeat_at=now_iso(), real_orders_sent=0, detail=reason)
                    continue
            # Login listo: recién ahora corren servicios no críticos por TTL.
            if not caucion_ledger_blocked:
                if not iol and attached and phase != "OPEN" and _daily_sync(reader, store):
                    reader = None
                    next_login_at = time.time() + GATEWAY_LOCAL_ATTACH_RETRY_SECONDS
                    continue
                operations.service_tick(store, phase)
                if not iol and phase == "OPEN" and time.time() - last_caucion_live_refresh >= CAUCION_LIVE_REFRESH_SECONDS:
                    try:
                        import dj_caucion_live_ppi_rc6 as caucion_live
                        if caucion_live.collection_window(datetime.now(TZ)):
                            result=caucion_live.refresh(reader,store,now=datetime.now(TZ))
                            store.event("CAUCION_LIVE_BOOK_REFRESH", json.dumps(result,sort_keys=True))
                            last_caucion_live_refresh=time.time()
                    except Exception as exc:
                        store.event("CAUCION_LIVE_BOOK_REFRESH_ERROR", type(exc).__name__)
                if time.time() - last_public_check >= PUBLIC_CHECK_SECONDS:
                    _public_probe(store)
                    last_public_check = time.time()
                should_capture = phase == "OPEN" and time.time() - last_structured_capture >= PUBLIC_STRUCTURED_CAPTURE_SECONDS
                closing_snapshot = previous_phase == "OPEN" and phase == "CLOSED"
                if should_capture or closing_snapshot:
                    _public_probe(store, structured=True)
                    last_structured_capture = time.time()
                if not iol:
                    last_complementary_reconcile = _periodic_complementary_reconcile(
                        store, phase, last_complementary_reconcile)
            if phase == "CLOSED":
                # El catálogo se renueva una vez por fecha local. Los históricos
                # avanzan en lotes con TTL aunque sea noche, fin de semana o feriado.
                if not iol and _daily_sync(reader, store):
                    reader = None
                    next_login_at = time.time() + GATEWAY_LOCAL_ATTACH_RETRY_SECONDS
                    continue
                if not iol:
                    _background_ingest(reader, store)
                if time.time() - last_readiness_check >= READINESS_CHECK_SECONDS:
                    _runtime_readiness(store)
                    _publish_economic_shadow_health(store)
                    last_readiness_check = time.time()
                store.state(process_state="WAITING_MARKET", session_state="MARKET_CLOSED",
                            heartbeat_at=now_iso(), real_orders_sent=0,
                            detail=f"Gateway {source_label} conectado. Estrategia y market data vivo detenidos por mercado cerrado.")
                time.sleep(COMMAND_POLL_SECONDS)
                continue
            if phase == "PREOPEN":
                store.state(process_state="READY_PREOPEN", session_state="PREOPEN",
                            heartbeat_at=now_iso(), real_orders_sent=0,
                            detail="Preapertura: Gateway conectado; cero evaluaciones y cero operaciones.")
            if phase != "OPEN":
                time.sleep(COMMAND_POLL_SECONDS)
                continue
            dispatch_started = time.monotonic()
            iol_metrics = None
            if iol:
                try:
                    iol_metrics = reader.metrics
                except Exception as exc:
                    if not iol_gateway_reader_needs_reattach(exc):
                        raise
            (focus, symbols, eligible_total, cursor_before, cursor_after,
             feasibility) = _runtime_readiness(store, record_event=True, iol_metrics=iol_metrics)
            active_plan = feasibility["active_plan"]
            catalog_read_error = active_plan.get("catalog_status") == "CATALOG_READ_ERROR"
            observation_hold = ("CAUCION_LEDGER_INVALID" if caucion_ledger_blocked else
                                "CATALOG_READ_ERROR" if catalog_read_error else "")
            fresh_observation = False
            # Readiness completed; a long current/book round must not leave
            # the heartbeat frozen at the previous cycle's completion.
            store.state(heartbeat_at=now_iso(), real_orders_sent=0)
            last_cycle_pulse = time.monotonic()
            cycle_deadline = last_cycle_pulse + min(
                INTERVAL, active_plan.get("cadence_seconds", INTERVAL))
            cycle_started = now_iso()
            cycle_clock = time.perf_counter()
            cycle_ok = 0
            cycle_failures = 0
            cycle_requested = 0
            cycle_stream_wait = 0
            cycle_identity_rejections = 0
            cycle_temporal_rejections = 0
            cycle_session_invalid = False
            latencies = []
            iol_pairs = []
            if iol:
                from rc6_dynamic_universe.iol_capacity import cycle_evidence, paired_sample
                from rc6_dynamic_universe.iol_coverage import book_followup
            coverage = active_plan.get("iol_stream_coverage") if iol else None
            stream_states = {}
            if coverage:
                stream_states, followed = _advance_iol_discovery(reader, coverage, active_plan=active_plan)
                if followed:
                    cold, probed = followed
                    symbols = tuple(probed if request == cold else request for request in symbols)
                feasibility["iol_stream_subscriptions"] = coverage["scheduled_subscriptions"]
                if coverage.get("preparation_hold_reason"):
                    store.event("IOL_TRADE_PREPARE_HOLD", coverage["preparation_hold_reason"])
            scopes = {tuple(s["request"]): s for s in active_plan.get("read_scopes", [])}
            opened_identities = {}
            if iol or getattr(reader, "budget_enabled", False):
                for p in store.open_positions():
                    opened_identities.setdefault((p["symbol"], p["asset_class"], p["settlement"]), set()).add(
                        (p["symbol"], p["asset_class"], p["market"], p["currency"], p["settlement"]))
            for symbol, asset_class, settlement in symbols:
                if STOP:
                    break
                symbol_clock = time.perf_counter()
                try:
                    request_key = (symbol, asset_class, settlement)
                    scope = scopes.get(request_key, {})
                    exact_opened = opened_identities.get(request_key, set())
                    priority, scope_identity = scope.get("priority", "DISCOVERY"), scope.get("identity")
                    if exact_opened:
                        priority = "OPENED_CRITICAL"
                        scope_identity = next(iter(exact_opened)) if len(exact_opened) == 1 else None
                    elif priority == "OPENED_CRITICAL":
                        priority = "DISCOVERY"
                    if scope_identity is None:
                        cycle_identity_rejections += 1
                        cycle_failures += 1
                        reason = "IOL_GATEWAY_EXACT_IDENTITY_REQUIRED" if iol else "PPI_GATEWAY_EXACT_IDENTITY_REQUIRED"
                        store.event("DATA_REJECTED", f"{symbol}: {reason}")
                        continue
                    if (iol and priority == "DISCOVERY" and
                            stream_states.get(tuple(scope_identity)) == "IOL_TRADE_SAMPLE_ABSENT"):
                        # Opening with no provider trade would fail following
                        # the REST quote anyway. Let discovery gather the
                        # trade before spending an asset, quote or book call.
                        cycle_stream_wait += 1
                        continue
                    cycle_requested += 1
                    q = _read_scanner_quote(reader, store, symbol, asset_class, settlement,
                        priority=priority, identity=scope_identity, scanner_primary=scanner_primary,
                        catalog_read_error=catalog_read_error)
                    if coverage and tuple(scope_identity) == coverage.get("rest_followed_trade"):
                        coverage["followed_book"] = book_followup(
                            coverage.get("followed_trade_received_at"), q.book_at, q.observed_at)
                    if observation_hold:
                        q = replace(q, opening_block_reason=observation_hold)
                    if scanner_primary is not None:
                        try:
                            scanner_primary.capture(q, request=request_key)
                        except Exception as origin_error:
                            # A receipt failure cannot interrupt PAPER quote/EXIT.
                            print("PPI_SCANNER_PRIMARY_CAPTURE_ERROR=" + type(origin_error).__name__,
                                  file=sys.stderr, flush=True)
                    # Stale Book never reaches the broker; stale trade with a
                    # fresh Book can supervise exits only, never new entry.
                    store.add_quote(q)
                    quotes[(symbol, asset_class, settlement, q.currency, q.market)] = q
                    if _scanner_trade_temporal_rejection(
                            q, broker, store, checked_at=now_iso(), observe_only=bool(observation_hold)):
                        cycle_temporal_rejections += 1
                        continue
                    if iol and asset_class in PAPER_SPOT_FAMILIES and (pair := paired_sample(q, scope_identity)):
                        iol_pairs.append(pair)
                    # The independent EXIT supervisor can use the exact
                    # persisted Book without granting a new entry or cash.
                    if not observation_hold:
                        allow_openings, opening_reason = _scanner_opening_capacity(
                            request_key, focus=focus, feasibility=feasibility)
                        # La apertura de CEDEAR exige rueda regular de su subyacente.
                        cedear_allowed, cedear_reason = cedear_opening_gate(asset_class, datetime.now(TZ))
                        if not cedear_allowed:
                            allow_openings = False
                            opening_reason = cedear_reason
                        broker.on_quote(
                            q, allow_new_openings=allow_openings,
                            opening_block_reason=opening_reason,
                            evaluate_when_blocked=(
                                not allow_openings and request_key in set(map(tuple, focus["matched"]))
                            ),
                        )
                    cycle_ok += 1
                    store.state(last_market_data_at=q.observed_at)
                except ReadOnlyPolicyViolation as exc:
                    store.event("HTTP_BLOCKED", str(exc))
                    store.state(process_state="DEGRADED", detail="La barrera bloqueo una ruta no permitida.")
                except Exception as exc:
                    cycle_failures += 1
                    read_error = read_error_code(exc)
                    store.event("DATA_ERROR", f"{symbol}: {read_error}")
                    if (session_invalid(exc) or _scanner_gateway_reader_lost(exc)
                            or iol and iol_gateway_reader_needs_reattach(exc)):
                        cycle_session_invalid = True
                        store.event("IOL_GATEWAY_REATTACH_REQUIRED" if iol else "PPI_GATEWAY_REATTACH_REQUIRED",
                                    "Gateway cambió/no responde o auth inválida; reconexión local")
                        break
                    if read_error in NATIVE_PROVIDER_BACKPRESSURE or iol and (
                            getattr(exc, "status_code", None) in (401, 429)
                            or read_error == "IOL_GATEWAY_RATE_HOLD"):
                        observation_hold = read_error
                        break
                finally:
                    latency = round((time.perf_counter() - symbol_clock) * 1000, 2)
                    latencies.append(latency)
                    _write_scanner_metrics(store, "SYMBOL",
                          (cycle_started, symbol, latency,
                           "VERDE" if latency < INTERVAL * 500 else "AMARILLO",
                           f"current + book; {source_label} sólo lectura"))
                    last_cycle_pulse = _pulse_observed_progress(store, last_cycle_pulse)
            if (not iol and not observation_hold and not cycle_session_invalid
                    and os.getenv("POROTA_PPI_DIRECT_REST_RECOVERY") == "1"
                    and last_direct_rest_probe + 60 <= time.monotonic() < cycle_deadline
                    and not store.open_positions() and not store.active_future_positions()):
                # Retain the existing diagnostic turn with a full basket.
                # An empty basket can use its ordinary scanner turns here.
                slots = max(1, ACTIVE_SYMBOL_LIMIT - len(symbols))
                last_direct_rest_probe = time.monotonic()
                direct_probe_family_cursor, last_cycle_pulse, discovery = _discover_native_settlements(
                    reader, store, slots=slots, deadline=cycle_deadline,
                    cursor=direct_probe_family_cursor, after=direct_probe_after,
                    last_pulse=last_cycle_pulse)
                fresh_observation = discovery["fresh_pairs"] > 0
                feasibility["native_settlement_discovery"] = discovery
                detail = (f"turnos={slots};intentos={discovery['attempted']};"
                          f"pares_frescos={discovery['fresh_pairs']};plazos={discovery['attested']}")
                if discovery.get("reason"):
                    detail += ";reason=" + discovery["reason"]
                _health(store, "PPI_DIRECT_REST_RECOVERY",
                        "VERDE" if fresh_observation else "AMARILLO",
                        detail, "PPI Producción", success=fresh_observation)
            if scanner_primary is not None:
                try:
                    primary_receipt = scanner_primary.publish()
                    print("PPI_SCANNER_PRIMARY_RECEIPT=" + json.dumps(primary_receipt, sort_keys=True),
                          file=sys.stderr, flush=True)
                except Exception as origin_error:
                    print("PPI_SCANNER_PRIMARY_PUBLISH_ERROR=" + type(origin_error).__name__,
                          file=sys.stderr, flush=True)
            if not observation_hold:
                try:
                    broker.mark_equity(quotes)
                except ValueError as exc:
                    if not str(exc).startswith("CAUCION_LEDGER_INVALID:"):
                        raise
                    # Another service can change the ledger during this cycle.
                    # An invalid valuation must also enter the hourly hold.
                    caucion_ledger_blocked = True
                    next_caucion_ledger_recheck = time.monotonic() + CAUCION_LEDGER_RECHECK_SECONDS
                    observation_hold = "CAUCION_LEDGER_INVALID"
                    store.event(observation_hold, "Valuación PAPER suspendida hasta validar el ledger.")
            feasibility["observation_counts"] = {
                "selected": len(symbols), "instruments_dispatched": cycle_requested,
                "identity_rejections": cycle_identity_rejections,
                "temporal_rejections": cycle_temporal_rejections,
            }
            if iol:
                feasibility["observation_counts"]["waiting_for_discovery_trade"] = cycle_stream_wait
            if observation_hold:
                feasibility["opening_hold_reason"] = observation_hold
            duration = round(time.perf_counter() - cycle_clock, 3)
            avg_seconds = (statistics.fmean(latencies) / 1000) if latencies else INTERVAL
            budget = max(1.0, INTERVAL * 0.75)
            recommended = max(5, min(60, int(budget / max(avg_seconds, .05))))
            if cycle_failures / max(1, len(symbols)) > .10:
                recommended = min(recommended, max(5, ACTIVE_SYMBOL_LIMIT - 5))
            scanner_limit = active_plan.get("iol_rest_budget", {}).get("scanner_batch_limit", ACTIVE_SYMBOL_LIMIT)
            recommended = min(recommended, scanner_limit) if iol else recommended
            cycle_finished = now_iso()
            try:
                metrics = reader.metrics
                if iol:
                    authenticated = metrics.get("authenticated") is True
                    _health(store, auth_component, "VERDE" if authenticated else "AMARILLO",
                            "Sesión IOL vigente." if authenticated else "Sesión IOL sin vigencia acreditada.",
                            source_label, success=authenticated)
                elif metrics.get("authenticated") is False:
                    cycle_session_invalid = True
            except Exception as exc:
                if not (_scanner_gateway_reader_lost(exc) or iol and iol_gateway_reader_needs_reattach(exc)):
                    raise
                cycle_session_invalid = True
                metrics = None  # keep last physical owner HTTP counts, never false zeros
                store.event("IOL_GATEWAY_METRICS_UNVERIFIED" if iol else "PPI_GATEWAY_METRICS_UNVERIFIED",
                            type(exc).__name__)
            if iol:
                feasibility["iol_capacity_cycle"] = cycle_evidence(
                    metrics, iol_pairs, active_plan.get("iol_rest_budget", {}),
                    complete=not (cycle_session_invalid or observation_hold or STOP))
                if metrics and isinstance(metrics.get("wire_usage"), dict):
                    feasibility["iol_wire_usage"] = metrics["wire_usage"]
                if metrics and isinstance(metrics.get("wire_budget"), dict):
                    feasibility["iol_wire_budget"] = metrics["wire_budget"]
            _write_scanner_metrics(store, "UNIVERSE",
                  (cycle_started, cycle_finished, eligible_total, len(symbols), cycle_ok,
                   cycle_failures, duration, cursor_before, cursor_after, recommended,
                   f"Límite={scanner_limit}; foco+rotación; factibilidad={json.dumps(feasibility,sort_keys=True)}"))
            if cycle_session_invalid:
                _health(store, auth_component, "ROJO",
                        "Sesión del gateway pendiente de recuperación o de nueva conexión local.",
                        source_label)
            cycle_detail = (f"{cycle_ok}/{len(symbols)} instrumentos actualizados de "
                            f"{eligible_total} elegibles; rotación activa; operaciones solo simuladas.")
            if observation_hold:
                cycle_detail = f"{observation_hold}: observación sin nuevas aperturas PAPER. " + cycle_detail
            if cycle_session_invalid:
                cycle_detail = "Login fallo: sesión del gateway pendiente o desconectada. " + cycle_detail
            store.state(process_state="DEGRADED" if observation_hold or cycle_session_invalid or not cycle_ok else "RUNNING",
                        session_state="MARKET_OPEN", heartbeat_at=now_iso(),
                        ppi_auth="NOT_ATTEMPTED" if iol else "ERROR" if cycle_session_invalid else "OK",
                        **({"http_allowed": metrics["http_allowed"],
                            "http_blocked": metrics["http_blocked"]} if metrics and not iol else {}),
                        real_orders_sent=0,
                        detail=cycle_detail)
            market_detail = f"{active_plan['catalog_status']} sin consultas de cotización en el ciclo."
            if fresh_observation:
                market_detail = "Par Current/Book nativo fresco en observación."
            if cycle_requested:
                market_detail = f"{cycle_ok}/{len(symbols)} instrumentos con cotización útil."
            _health(store, market_component,
                    "VERDE" if cycle_ok or fresh_observation else "ROJO" if cycle_requested else "AMARILLO",
                    market_detail, source_label, success=bool(cycle_ok or fresh_observation))
            _publish_economic_shadow_health(store)
            if cycle_session_invalid:
                reader.close()
                reader = None
                next_login_at = time.time() + 60
            cadence = active_plan.get("cadence_seconds", INTERVAL)
            time.sleep(max(0, cadence - (time.monotonic() - dispatch_started)) if iol else cadence)
    finally:
        if reader:
            reader.close()
        store.state(process_state="STOPPED", heartbeat_at=now_iso(), real_orders_sent=0,
                    detail="Observador detenido ordenadamente.")
    return 0


if __name__ == "__main__":
    # SQLITE_FULL/ENOSPC must not turn a degraded PAPER scanner into hundreds
    # of container restarts. Quarantine is in-process and never writes SQLite;
    # it stays red/stale until normal startup succeeds after disk recovery.
    sys.exit(scanner_with_storage_quarantine(
        run, stopped=lambda: STOP,
        emit=lambda item: print(json.dumps(item, sort_keys=True),
                                file=sys.stderr, flush=True)))
