"""Colector intradiario y scanner de scalping PAPER v17 HF3.

Usa exclusivamente ``MarketData/Intraday`` mediante la fachada PPI de solo
lectura. No expone métodos de órdenes ni convierte datos incompletos en fills.
El volumen requiere un contrato explícito de unidad y acumulación de PPI.
Continuidad y solapamiento no demuestran la unidad ni la cadencia del proveedor.
"""
from __future__ import annotations

from contextlib import closing, nullcontext
from collections import OrderedDict

import hashlib
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from bs_instrument_contracts import aware_datetime, family_name
from rc6_signal_contracts import register_exact_time
from co_market_sessions_hf6 import byma_paper_spot_open
from fg_intraday_contract_policy_rc6 import classify_revision, previous_for_session


TZ = ZoneInfo("America/Argentina/Buenos_Aires")
FOCUS = ("GGAL", "YPFD", "PAMP", "BMA", "BBAR", "SUPV", "CEPU", "AAPL")
# This strategy is an intraday spot strategy.  Contract support elsewhere in
# RC6 does not make a family suitable for this scanner: fixed income, funds,
# derivatives and cauciones have different economics/lifecycles.
SCALPING_STRATEGY_FAMILIES = frozenset({"ACCIONES", "CEDEARS", "ETFS"})
SCALPING_HEALTHY_RUNTIME_STATES = frozenset({"RUNNING", "WAITING_MARKET"})
# A live intraday identity needs at least two stable observations. With the old
# 24-row batch, eight permanent focus symbols left only ~16 rotation slots and
# the current ~1.2k request universe needed ~447 minutes for two passes: longer
# than the regular 390-minute BYMA PAPER session. 40 is the already-supported
# upper bound and leaves enough capacity even with the focus/open-position set.
DEFAULT_INTRADAY_SCAN_SECONDS = 180
DEFAULT_INTRADAY_BATCH_LIMIT = 40
# A newly fetched minute must be current when an entry is evaluated/promoted.
# Two minutes cover minute finalization and match the existing book/mutable
# window. The 45-minute history window is for indicators, never freshness;
# refreshing an old payload must not renew the underlying source evidence.
INTRADAY_SOURCE_MAX_AGE_SECONDS = 120
# Negative responses do not establish permanent vendor capability. Re-probes
# consume the ordinary read-only budget, with no authority to evaluate entry.
INTRADAY_NEGATIVE_TTL_SECONDS = 900
INTRADAY_NEGATIVE_MAX_TTL_SECONDS = 3600
INTRADAY_REPROBE_MIN_SPACING_SECONDS = 60
INTRADAY_CAPABILITY_CACHE_MAX_ENTRIES = 2048
INTRADAY_CAPABILITY_SCHEMA = "rc6.intraday-capability.v1"
INTRADAY_CAPABILITY_WARMUP_STATE = "INTRADAY_CAPABILITY_WARMUP_PENDING"
INTRADAY_CAPABILITY_REJECTED_STATE = "INTRADAY_CAPABILITY_WARMUP_REJECTED_CLOSED_POINTS"
INTRADAY_CAPABILITY_CLOSED_STATES = frozenset({"PPI_INSTRUMENT_NOT_FOUND",
    "INTRADAY_CAPABILITY_REPROBE_PENDING", "INTRADAY_CAPABILITY_REPROBE_FAILED",
    INTRADAY_CAPABILITY_WARMUP_STATE, INTRADAY_CAPABILITY_REJECTED_STATE})


def _unique_capability_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("INTRADAY_CAPABILITY_DUPLICATE_KEY")
        result[key] = value
    return result


def _finite_capability_number(encoded):
    number = float(encoded)
    if not math.isfinite(number):
        raise ValueError("INTRADAY_CAPABILITY_NONFINITE_NUMBER")
    return number


def _capability_detail(contract):
    """The existing identity state durably owns the recovery/warmup barrier."""
    encoded = (contract or {}).get("detail") or "{}"
    if len(encoded) > 16 * 1024:
        raise ValueError("INTRADAY_CAPABILITY_STATE_BOUND")
    try:
        detail = json.loads(encoded, object_pairs_hook=_unique_capability_keys,
            parse_float=_finite_capability_number, parse_constant=_finite_capability_number)
    except (ValueError, TypeError):
        if ((contract or {}).get("state") in INTRADAY_CAPABILITY_CLOSED_STATES
                or '"capability"' in encoded or encoded.lstrip().startswith(("{", "["))):
            raise ValueError("INTRADAY_CAPABILITY_STATE_INVALID")
        return None
    if not isinstance(detail, dict) or detail.get("schema") != INTRADAY_CAPABILITY_SCHEMA:
        if (contract or {}).get("state") in INTRADAY_CAPABILITY_CLOSED_STATES or (isinstance(detail, dict) and "capability" in detail):
            raise ValueError("INTRADAY_CAPABILITY_STATE_INVALID")
        return None
    capability = detail.get("capability")
    if not isinstance(capability, dict):
        raise ValueError("INTRADAY_CAPABILITY_STATE_INVALID")
    for field, count in (("identity", 5), ("request", 3), ("negative_origin_identity", 5)):
        values = capability.get(field)
        if (not isinstance(values, list) or len(values) != count
                or any(not isinstance(value, str) or not 0 < len(value) <= 256 for value in values)):
            raise ValueError("INTRADAY_CAPABILITY_STATE_INVALID")
    request = [capability["identity"][index] for index in (0, 1, 4)]
    if (capability["request"] != request
            or [capability["negative_origin_identity"][index] for index in (0, 1, 4)] != request):
        raise ValueError("INTRADAY_CAPABILITY_IDENTITY_MISMATCH")
    try:
        first, last, due = [aware_datetime(capability[field]) for field in
            ("first_seen_at", "last_seen_at", "retry_due_at")]
        checked = aware_datetime(contract["checked_at"])
        if (not first <= last <= due or last > checked
                or (due - last).total_seconds() > INTRADAY_NEGATIVE_MAX_TTL_SECONDS):
            raise ValueError("INTRADAY_CAPABILITY_STATE_INVALID")
        if not capability.get("warmup_reset_required"):
            if not aware_datetime(capability["warmup_after"]) == aware_datetime(capability["recovered_at"]) == last:
                raise ValueError("INTRADAY_CAPABILITY_STATE_INVALID")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("INTRADAY_CAPABILITY_STATE_INVALID") from exc
    if (type(capability.get("attempts")) is not int or not 1 <= capability["attempts"] <= 2147483647
            or type(capability.get("consecutive_failures")) is not int
            or not 0 <= capability["consecutive_failures"] <= 3
            or not isinstance(capability.get("warmup_reset_required"), bool)
            or not isinstance(capability.get("catalog_config_fingerprint"), str)
            or len(capability["catalog_config_fingerprint"]) != 64
            or any(char not in "0123456789abcdef" for char in capability["catalog_config_fingerprint"])
            or capability.get("session_id") != last.astimezone(TZ).date().isoformat()
            or not isinstance(capability.get("identity"), list) or len(capability["identity"]) != 5
            or ((contract or {}).get("state") in {INTRADAY_CAPABILITY_WARMUP_STATE, INTRADAY_CAPABILITY_REJECTED_STATE}
                and capability["warmup_reset_required"])):
        raise ValueError("INTRADAY_CAPABILITY_STATE_INVALID")
    cooldown = min(INTRADAY_NEGATIVE_MAX_TTL_SECONDS, INTRADAY_NEGATIVE_TTL_SECONDS *
        2 ** (max(1, capability["consecutive_failures"]) - 1))
    if (due - last).total_seconds() != cooldown:
        raise ValueError("INTRADAY_CAPABILITY_STATE_INVALID")
    return dict(capability)


def _capability_fingerprint(record, dynamic_selection):
    """Only this request's contract and effective config invalidate a negative.

    Catalog capture timestamps/run IDs and market-data clocks are excluded:
    refreshing the same catalog must not hammer an unsupported vendor route.
    """
    if any(not isinstance(item, str) or not 0 < len(item) <= 256 for item in _identity(record)):
        raise ValueError("INTRADAY_CAPABILITY_IDENTITY_BOUND")
    encoded_metadata = record.get("metadata_json") or "{}"
    if len(encoded_metadata) > 128 * 1024:
        raise ValueError("INTRADAY_CATALOG_METADATA_BOUND")
    raw = json.loads(encoded_metadata)
    raw = raw if isinstance(raw, dict) else {}
    metadata = {key: value for key, value in raw.items() if key in {
        "financial_contract_v17", "paper_family_contract_v1", "_contract_bridge",
        "_contract_conflicts", "_discovery_source", "_availability_source"}}
    def terms(value):
        if isinstance(value, dict):
            return {key: terms(item) for key, item in value.items() if key not in {
                "observed_at", "checked_at", "received_at", "first_received_at",
                "last_verified_at", "captured_at", "collected_at", "last_seen_at", "run_id"}}
        return [terms(item) for item in value] if isinstance(value, list) else value
    metadata = terms(metadata)
    config = {key: os.getenv(key) for key in (
        "PAPER_SCALPING_MODE", "PAPER_INTRADAY_SCAN_SECONDS", "PAPER_INTRADAY_BATCH_LIMIT",
        "PAPER_SCALPING_MIN_NET_MARGIN", "PAPER_SCALPING_MAX_SPREAD",
        "PAPER_SCALPING_SCORE_THRESHOLD", "PAPER_SCALPING_RISK_PER_TRADE",
        "PAPER_SCALPING_STOP_LOSS_PCT", "PAPER_SCALPING_TARGET_GAIN_PCT",
        "PAPER_SCALPING_MAX_HOLD_MINUTES", "PAPER_SCALPING_MAX_OPEN_POSITIONS")}
    value = {"schema": INTRADAY_CAPABILITY_SCHEMA, "identity": _identity(record),
        "status": record.get("status"), "capability": record.get("capability"),
        "settlement_source": record.get("settlement_source"), "contract": metadata,
        "config": config, "dynamic": dynamic_selection.get("dynamic", False),
        "dynamic_fingerprint": dynamic_selection.get("policy_state", {}).get("fingerprint"),
        "dynamic_configuration_fingerprint": dynamic_selection.get("policy_state", {}).get("configuration_fingerprint"),
        "effective_cadence_seconds": dynamic_selection.get("cadence_seconds"),
        "approved_limit": dynamic_selection.get("limit")}
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


class IntradayCapabilityCache:
    """Bounded LRU over durable request-scoped negative/recovery metadata.

    Eviction only drops a memory copy. The existing full-identity contract row
    retains cooldown and warmup, so neither eviction nor restart permits a BUY
    or an early retry. There is no new catalog status/AVAILABLE authority.
    """
    def __init__(self, maximum=INTRADAY_CAPABILITY_CACHE_MAX_ENTRIES):
        if not 1 <= maximum <= INTRADAY_CAPABILITY_CACHE_MAX_ENTRIES:
            raise ValueError("INTRADAY_CAPABILITY_CACHE_BOUND_INVALID")
        self.maximum = maximum
        self.entries = OrderedDict()
        self.evictions = 0

    @staticmethod
    def request(record):
        return (record["ticker"], record["instrument_type"], record["settlement"])

    def remember(self, record, value):
        key = self.request(record)
        self.entries[key] = dict(value, identity=list(_identity(record)))
        self.entries.move_to_end(key)
        while len(self.entries) > self.maximum:
            self.entries.popitem(last=False)
            self.evictions = min(2147483647, self.evictions + 1)
        return dict(self.entries[key])

    def restore(self, record, contract):
        # The request omits market/currency. Never transfer state to a sibling
        # full identity; selectors already deny simultaneously ambiguous aliases.
        value = _capability_detail(contract)
        if value is not None:
            if value.get("identity") != list(_identity(record)):
                raise ValueError("INTRADAY_CAPABILITY_IDENTITY_MISMATCH")
            return self.remember(record, value)
        cached = self.entries.get(self.request(record))
        return dict(cached) if cached and cached.get("identity") == list(_identity(record)) else None

    def decision(self, value, *, at, fingerprint):
        if value is None:
            return {"allowed": True, "reprobe": False, "reason": "NO_NEGATIVE_CAPABILITY"}
        now = aware_datetime(at)
        last = aware_datetime(value["last_seen_at"])
        if now < last:
            return {"allowed": False, "reprobe": False, "reason": "INTRADAY_CAPABILITY_CLOCK_ROLLBACK"}
        session_id = now.astimezone(TZ).date().isoformat()
        changed_session = session_id != value.get("session_id")
        changed_config = fingerprint != value.get("catalog_config_fingerprint")
        if not value["warmup_reset_required"] and not changed_session and not changed_config:
            return {"allowed": True, "reprobe": False, "reason": "RECOVERED_REQUIRES_NATIVE_WARMUP"}
        due = (now - last).total_seconds() >= INTRADAY_REPROBE_MIN_SPACING_SECONDS if (
            changed_session or changed_config) else now >= aware_datetime(value["retry_due_at"])
        reason = ("INTRADAY_CAPABILITY_SESSION_REPROBE" if changed_session else
                  "INTRADAY_CAPABILITY_CATALOG_CONFIG_REPROBE" if changed_config else
                  "INTRADAY_CAPABILITY_TTL_REPROBE")
        return {"allowed": due, "reprobe": due, "reason": reason if due else "INTRADAY_CAPABILITY_COOLDOWN"}

    def begin_probe(self, record, previous, *, at, fingerprint, reason):
        """Persistable attempt admission precedes the wire, even across death."""
        now = aware_datetime(at)
        if now < aware_datetime(previous["last_seen_at"]):
            raise ValueError("INTRADAY_CAPABILITY_CLOCK_ROLLBACK")
        session_id = now.astimezone(TZ).date().isoformat()
        same_context = (previous.get("session_id") == session_id
            and previous.get("catalog_config_fingerprint") == fingerprint)
        failures = previous["consecutive_failures"] if same_context else 0
        cooldown = min(INTRADAY_NEGATIVE_MAX_TTL_SECONDS, INTRADAY_NEGATIVE_TTL_SECONDS *
            2 ** (max(1, failures) - 1))
        pending = dict(previous, last_seen_at=_stamp(now),
            retry_due_at=_stamp(now + timedelta(seconds=cooldown)), session_id=session_id,
            attempts=min(2147483647, previous["attempts"] + 1),
            last_result="READ_ONLY_REPROBE_PENDING", reason_code=reason,
            capability_status="REPROBE_PENDING", catalog_config_fingerprint=fingerprint,
            consecutive_failures=failures, warmup_reset_required=True, warmup_after=None,
            reprobe_reason=reason, probe_started_at=_stamp(now))
        return self.remember(record, pending)

    def outcome(self, record, previous, *, at, fingerprint, result, recovered=False,
                probe_reason=None, attempt_started=False):
        now = aware_datetime(at)
        session_id = now.astimezone(TZ).date().isoformat()
        previous = previous or {}
        if previous and now < aware_datetime(previous.get("probe_started_at") or previous["last_seen_at"]):
            raise ValueError("INTRADAY_CAPABILITY_CLOCK_ROLLBACK")
        same_context = (previous.get("session_id") == session_id
            and previous.get("catalog_config_fingerprint") == fingerprint)
        failures = min(3, int(previous.get("consecutive_failures", 0)) + 1) if same_context else 1
        cooldown = INTRADAY_NEGATIVE_TTL_SECONDS if recovered else min(INTRADAY_NEGATIVE_MAX_TTL_SECONDS,
            INTRADAY_NEGATIVE_TTL_SECONDS * (2 ** (failures - 1)))
        value = {"first_seen_at": previous.get("first_seen_at") or _stamp(now),
            "last_seen_at": _stamp(now), "retry_due_at": _stamp(now + timedelta(seconds=cooldown)),
            "session_id": session_id, "attempts": min(2147483647, int(previous.get("attempts", 0)) + int(not attempt_started)),
            "last_result": result, "recovered_at": _stamp(now) if recovered else None,
            "reason_code": ("INTRADAY_CAPABILITY_RECOVERED_WARMUP_REQUIRED" if recovered else
                result if result == "PPI_INSTRUMENT_NOT_FOUND" else "INTRADAY_CAPABILITY_REPROBE_FAILED"),
            "capability_status": ("RECOVERED_WARMUP" if recovered else
                "UNSUPPORTED" if result == "PPI_INSTRUMENT_NOT_FOUND" else "REPROBE_FAILED"),
            "catalog_config_fingerprint": fingerprint, "consecutive_failures": 0 if recovered else failures,
            "warmup_reset_required": not recovered, "warmup_after": _stamp(now) if recovered else None,
            "identity": list(_identity(record)), "request": list(self.request(record)),
            "negative_origin_identity": previous.get("negative_origin_identity") or list(_identity(record))}
        value["reprobe_reason"] = probe_reason
        return self.remember(record, value)


def _invalidate_intraday_capability(store, record, capability, *, at):
    # Invalidate stale BUY authority before publishing the negative. Historical
    # source points/candidates are preserved for audit and excluded by the epoch.
    previous = previous_for_session(_state(store, _identity(record)), received_at=at) or {}
    detail = json.dumps({"schema": INTRADAY_CAPABILITY_SCHEMA, "capability": capability},
        sort_keys=True, separators=(",", ":"), allow_nan=False)
    state = ("INTRADAY_CAPABILITY_REPROBE_PENDING" if capability.get("capability_status") == "REPROBE_PENDING" else
        "PPI_INSTRUMENT_NOT_FOUND" if capability["last_result"] == "PPI_INSTRUMENT_NOT_FOUND" else
        "INTRADAY_CAPABILITY_REPROBE_FAILED")
    with store.connect() as connection:
        register_exact_time(connection)
        connection.execute("""INSERT OR REPLACE INTO ppi_intraday_contract_state
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (*_identity(record), state,
          0, 0, previous.get("changed_closed_points", 0), 0, None, _stamp(at), detail))


def _capability_event(store, kind, record, capability):
    if kind == "INTRADAY_SCALPING_UNSUPPORTED":
        # Existing SHADOW readers require the native code and terminal identity
        # JSON exactly. Detailed telemetry is a separate structured event.
        store.event(kind, f"{record['ticker']}: PPI_INSTRUMENT_NOT_FOUND;shadow_identity=" +
            json.dumps(_identity(record), separators=(",", ":")))
        kind = "INTRADAY_SCALPING_CAPABILITY_NEGATIVE"
    store.event(kind, json.dumps(capability, sort_keys=True,
        separators=(",", ":"), allow_nan=False))


def _intraday_read_error_code(reader, error, classify_read_error):
    """Keep SDK body exceptions behind the current sanitized transport result.

    Rate-limit/authentication statuses take precedence even if a malformed
    vendor body says Instrument not found. Other exact native classifications,
    including a legitimate 404 capability gap, remain request-scoped.
    """
    native = classify_read_error(error)
    transport = getattr(reader, "last_read_error_code", None)
    transport = transport if isinstance(transport, str) else None
    if transport in {"PPI_HTTP_401", "PPI_HTTP_403", "PPI_SESSION_INVALID"}:
        return "PPI_SESSION_INVALID"
    if transport == "PPI_HTTP_429":
        return transport
    if native == "PPI_EXCEPTION" and transport in {
            "PPI_HTTP_408", "PPI_HTTP_500", "PPI_HTTP_502", "PPI_HTTP_503", "PPI_HTTP_504"}:
        return transport
    return native


def shadow_sampling_plan(catalog, *, at, session_open, frozen, capacity,
                         observations=(), events=(), opened=(), previous=None,
                         phase="OPEN", policy=None):
    """Separate dense SHADOW scheduler; no change to the productive 40 policy.

    Uses the existing exact equity-family contract. Caller supplies empirical
    capacity and a preopen hash, not an arbitrary replacement batch number.
    This adapter never evaluates/promotes a PAPER candidate or writes its DB.
    Confirmed interval-volume/freshness/economics/risk still belong to the
    existing evaluator and broker; a SHADOW HOT state grants no BUY authority.
    """
    from rc6_dynamic_universe.orchestrator import UniverseOrchestrator, EnginePolicy
    selected_policy = policy or EnginePolicy()
    if selected_policy.engine != "SCALPING":
        raise ValueError("SCALPING_STRATEGY_REQUIRED")
    return UniverseOrchestrator(catalog, policy=selected_policy).plan(
        at=at, session_open=session_open, frozen=frozen, capacity=capacity,
        observations=observations, events=events, opened=opened,
        previous=previous, phase=phase)


def _stamp(value):
    return aware_datetime(value).astimezone(timezone.utc).isoformat(timespec="microseconds")


def _decimal(value, *, positive=False, nonnegative=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("INTRADAY_INVALID_NUMBER") from exc
    if not result.is_finite() or (positive and result <= 0) or (nonnegative and result < 0):
        raise ValueError("INTRADAY_NUMBER_OUT_OF_RANGE")
    return result


def _field(row, name):
    if not isinstance(row, dict):
        return None
    return {str(key).lower(): value for key, value in row.items()}.get(name.lower())


def normalize_payload(payload, *, received_at, local_day=None):
    """Valida la forma observada de PPI sin inventar OHLC ni unidad nominal."""
    if not isinstance(payload, list):
        raise ValueError("INTRADAY_NOT_A_LIST")
    received = aware_datetime(received_at).astimezone(timezone.utc)
    local_day = local_day or received.astimezone(TZ).date()
    points = []
    seen = set()
    for raw in payload:
        if not isinstance(raw, dict):
            raise ValueError("INTRADAY_ROW_NOT_OBJECT")
        event = aware_datetime(_field(raw, "date")).astimezone(timezone.utc)
        price = _decimal(_field(raw, "price"), positive=True)
        volume = _decimal(_field(raw, "volume"), nonnegative=True)
        if event > received + timedelta(seconds=5):
            raise ValueError("INTRADAY_EVENT_IN_FUTURE")
        if event.astimezone(TZ).date() != local_day:
            continue
        event_at = event.isoformat(timespec="microseconds")
        if event_at in seen:
            raise ValueError("INTRADAY_DUPLICATE_MINUTE")
        seen.add(event_at)
        points.append((event_at, price, volume))
    if points != sorted(points, key=lambda item: item[0]):
        raise ValueError("INTRADAY_NOT_ASCENDING")
    return points


INTRADAY_POINTS_DDL = """CREATE TABLE IF NOT EXISTS ppi_intraday_points(
  symbol TEXT NOT NULL, asset_class TEXT NOT NULL, market TEXT NOT NULL,
  currency TEXT NOT NULL, settlement TEXT NOT NULL, event_at TEXT NOT NULL,
  price TEXT NOT NULL, volume TEXT NOT NULL, first_received_at TEXT NOT NULL,
  last_verified_at TEXT NOT NULL, source TEXT NOT NULL,
  PRIMARY KEY(symbol,asset_class,market,currency,settlement,event_at))"""
INTRADAY_TEMPORAL_INDEX = "idx_intraday_event_julian_desc"
INTRADAY_TEMPORAL_INDEX_DDL = ("CREATE INDEX IF NOT EXISTS " + INTRADAY_TEMPORAL_INDEX
    + " ON ppi_intraday_points(julianday(event_at) DESC)")


def require_intraday_temporal_index(connection):
    """Verify the exact expression/direction without migrating a Source."""
    row = connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                             (INTRADAY_TEMPORAL_INDEX,)).fetchone()
    normalized = lambda value: "".join(value.upper().split()).replace("IFNOTEXISTS", "")
    if not row or normalized(row[0] or "") != normalized(INTRADAY_TEMPORAL_INDEX_DDL):
        raise RuntimeError("SOURCE_TEMPORAL_INDEX_REQUIRED")
    terms = [tuple(row) for row in connection.execute(
        "PRAGMA index_xinfo(" + INTRADAY_TEMPORAL_INDEX + ")")]
    if terms != [(0, -2, None, 1, "BINARY", 1), (1, -1, None, 0, "BINARY", 0)]:
        raise RuntimeError("SOURCE_TEMPORAL_INDEX_REQUIRED")


def _source_query_schema(connection):
    connection.execute(INTRADAY_POINTS_DDL)
    connection.execute(INTRADAY_TEMPORAL_INDEX_DDL)
    require_intraday_temporal_index(connection)


def prepare_shadow_source_index(store):
    """Parent-owned additive migration before its first SHADOW child.

    The caller holds the existing runtime lock. SQLite serializes a genuine
    writer; contention or an interrupted build aborts before children start.
    No child/capture path can call this migration, and the connection closes
    before the parent's schema-ready mark becomes visible to its children.
    """
    with closing(store.connect()) as connection, connection:
        state = connection.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if not state or tuple(state) != ("PRODUCTION_PAPER", 0):
            raise RuntimeError("PAPER_SAFETY_REQUIRED")
        connection.execute("PRAGMA busy_timeout=350")
        deadline = time.monotonic() + 2.0
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        try:
            connection.execute("BEGIN IMMEDIATE")
            _source_query_schema(connection)
        finally:
            connection.set_progress_handler(None, 0)


def init_schema(store):
    """Initialize native inputs once, including the canonical #456 authority.

    PaperStore already owns this immutable evidence table. A standalone
    Intraday store uses the identical table/index before entering its loop;
    evaluation performs only its existing transactional INSERT OR IGNORE.
    """
    with closing(store.connect()) as connection, connection:
        register_exact_time(connection)
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS decision_evidence_snapshots(
          decision_key TEXT PRIMARY KEY, captured_at TEXT NOT NULL,
          schema_version TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
          payload_json TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_decision_evidence_captured
          ON decision_evidence_snapshots(captured_at, decision_key);
        CREATE TABLE IF NOT EXISTS ppi_intraday_contract_state(
          symbol TEXT NOT NULL, asset_class TEXT NOT NULL, market TEXT NOT NULL,
          currency TEXT NOT NULL, settlement TEXT NOT NULL, state TEXT NOT NULL,
          observations INTEGER NOT NULL, stable_overlap INTEGER NOT NULL,
          changed_closed_points INTEGER NOT NULL, new_points INTEGER NOT NULL,
          last_source_at TEXT, checked_at TEXT NOT NULL, detail TEXT NOT NULL,
          PRIMARY KEY(symbol,asset_class,market,currency,settlement));
        CREATE TABLE IF NOT EXISTS scalping_candidates(
          id INTEGER PRIMARY KEY AUTOINCREMENT, evaluated_at TEXT NOT NULL,
          symbol TEXT NOT NULL, asset_class TEXT NOT NULL, market TEXT NOT NULL,
          currency TEXT NOT NULL, settlement TEXT NOT NULL, action TEXT NOT NULL,
          score TEXT NOT NULL, price TEXT, volume TEXT, points INTEGER NOT NULL,
          reason TEXT NOT NULL, economics_json TEXT NOT NULL,
          UNIQUE(symbol,asset_class,market,currency,settlement,evaluated_at));
        CREATE INDEX IF NOT EXISTS idx_scalping_candidates_at ON scalping_candidates(evaluated_at,id);
        CREATE TABLE IF NOT EXISTS intraday_scalping_worker_state(
          id INTEGER PRIMARY KEY CHECK(id=1), heartbeat_at TEXT NOT NULL,
          state TEXT NOT NULL, cursor INTEGER NOT NULL, selected INTEGER NOT NULL,
          successful INTEGER NOT NULL, failed INTEGER NOT NULL,
          points_inserted INTEGER NOT NULL, confirmed_identities INTEGER NOT NULL,
          candidates INTEGER NOT NULL, real_orders_sent INTEGER NOT NULL,
          detail TEXT NOT NULL);
        """)
        _source_query_schema(connection)
        connection.execute("""CREATE INDEX IF NOT EXISTS idx_intraday_identity_time
            ON ppi_intraday_points(symbol,asset_class,market,currency,settlement,event_at)""")


def _identity(record):
    return tuple(record[key] for key in
                 ("ticker", "instrument_type", "market", "currency", "settlement"))


def _market_open(at):
    # Fuente única RC5: intervalo regular BYMA PAPER [10:30,17:00).
    return byma_paper_spot_open(aware_datetime(at))


def select_batch(store, *, limit, cursor=0):
    """Return only full-key READY identities supported by this strategy.

    ``candidate_identity_v2`` is the runtime readiness authority.  Falling
    back to every AVAILABLE catalog row used to spend most rotations on
    families that this strategy must reject later and could also collapse two
    currency/market identities into one request.  Absence of the v2 gate is
    therefore fail-closed for scalping.
    """
    if not 8 <= limit <= 40:
        raise ValueError("INTRADAY_BATCH_LIMIT_OUT_OF_RANGE")
    with store.connect() as connection:
        register_exact_time(connection)
        if not connection.execute("""SELECT 1 FROM sqlite_master
          WHERE type='table' AND name='candidate_identity_v2'""").fetchone():
            return [], cursor, 0
        placeholders = ",".join("?" for _ in SCALPING_STRATEGY_FAMILIES)
        rows = [dict(row) for row in connection.execute(f"""
          SELECT c.ticker,c.instrument_type,c.market,c.currency,c.settlement,
                 c.capability,c.status,c.settlement_source,c.metadata_json
          FROM financial_instrument_catalog c
          JOIN candidate_identity_v2 r
            ON r.ticker=c.ticker AND r.instrument_type=c.instrument_type
           AND r.market=c.market AND r.currency=c.currency
           AND r.settlement=c.settlement
          WHERE c.status='AVAILABLE' AND r.status='AVAILABLE'
            AND r.can_simulate=1
            AND UPPER(c.instrument_type) IN ({placeholders})
            AND c.capability LIKE 'READY_PAPER_%'
            AND c.currency<>'UNKNOWN' AND c.market<>'UNKNOWN'
          ORDER BY c.instrument_type,c.market,c.currency,c.ticker,c.settlement
        """, tuple(sorted(SCALPING_STRATEGY_FAMILIES))).fetchall()]
        opened = {row[0] for row in connection.execute(
            "SELECT DISTINCT symbol FROM paper_positions WHERE status='OPEN'").fetchall()}
    unique = {}
    for row in rows:
        try:
            family = family_name(row["instrument_type"])
        except ValueError:
            continue
        if family not in SCALPING_STRATEGY_FAMILIES:
            continue
        # The PPI intraday route does not take currency/market.  More than one
        # READY full identity for the same literal request is ambiguous and is
        # excluded instead of choosing an arbitrary sibling.
        request_key = (row["ticker"], row["instrument_type"], row["settlement"])
        unique.setdefault(request_key, []).append(row)
    rows = [values[0] for values in unique.values() if len(values) == 1]
    priority = [row for row in rows if row["ticker"] in opened or row["ticker"] in FOCUS]
    priority_keys = {_identity(row) for row in priority}
    rotation = [row for row in rows if _identity(row) not in priority_keys]
    slots = max(0, limit - len(priority))
    if rotation and slots:
        start = cursor % len(rotation)
        selected = [rotation[(start + index) % len(rotation)] for index in range(min(slots, len(rotation)))]
        next_cursor = (start + len(selected)) % len(rotation)
    else:
        selected, next_cursor = [], cursor
    return (priority + selected)[:limit], next_cursor, len(rows)


def select_runtime_batch(store, *, limit, cursor=0, at, controller=None):
    """OFF returns the original batch; approved selection has its own gate."""
    baseline, next_cursor, universe = select_batch(store, limit=limit, cursor=cursor)
    from rc6_dynamic_universe.promotion import capacity_controller_from_environment
    controller = controller or capacity_controller_from_environment(getattr(store, "path", None))
    if controller.state(at)["status"] != "APPROVED_DYNAMIC":
        return baseline, next_cursor, universe, {"dynamic": False}
    with store.connect() as c:
        opened = [tuple(r) for r in c.execute("""SELECT symbol,asset_class,market,currency,settlement
            FROM paper_positions WHERE status='OPEN' AND asset_class IN ('ACCIONES','CEDEARS','ETFS')""")]
        rows = [dict(r) for r in c.execute("""SELECT c.ticker,c.instrument_type,c.market,c.currency,c.settlement,
            c.capability,c.status,c.settlement_source,c.metadata_json FROM financial_instrument_catalog c JOIN candidate_identity_v2 r
            USING(ticker,instrument_type,market,currency,settlement)
            WHERE c.status='AVAILABLE' AND r.status='AVAILABLE' AND r.can_simulate=1
              AND c.capability LIKE 'READY_PAPER_%' AND c.instrument_type IN ('ACCIONES','CEDEARS','ETFS')""")]
    selection = controller.selection("SCALPING", baseline, as_of=at, opened=opened)
    if not selection["dynamic"]:
        return baseline, next_cursor, universe, selection
    aliases = {}
    for row in rows:
        aliases.setdefault((row["ticker"], row["instrument_type"], row["settlement"]), []).append(row)
    ready = {_identity(values[0]): values[0] for values in aliases.values() if len(values) == 1}
    selected = [ready[k] for k in selection["selected"] if k in ready]
    return selected, cursor, universe, selection


def _state(store, identity):
    with store.connect() as connection:
        register_exact_time(connection)
        row = connection.execute("""SELECT * FROM ppi_intraday_contract_state
          WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=?""",
          identity).fetchone()
    return dict(row) if row else None


def _request_capability_state(store, record):
    """A retired catalog alias cannot erase the same literal wire's negative.

    Only this request is queried, with one bounded result. The original full
    identity remains provenance; state/points/counters of sibling identities
    are never used for contract confirmation or entry.
    """
    current = _state(store, _identity(record))
    capability = _capability_detail(current)
    with store.connect() as connection:
        register_exact_time(connection)
        row = connection.execute("""SELECT * FROM ppi_intraday_contract_state
            WHERE symbol=? AND asset_class=? AND settlement=?
              AND state IN ('PPI_INSTRUMENT_NOT_FOUND','INTRADAY_CAPABILITY_REPROBE_FAILED',
                            'INTRADAY_CAPABILITY_REPROBE_PENDING','INTRADAY_CAPABILITY_WARMUP_PENDING',
                            'INTRADAY_CAPABILITY_WARMUP_REJECTED_CLOSED_POINTS')
            ORDER BY rc6_instant_us(checked_at) DESC,market,currency LIMIT 1""",
            IntradayCapabilityCache.request(record)).fetchone()
    if row is None:
        return current
    context = dict(row)
    negative = _capability_detail(context)
    source_identity = [context[key] for key in ("symbol", "asset_class", "market", "currency", "settlement")]
    if negative is None or negative.get("identity") != source_identity:
        raise ValueError("INTRADAY_CAPABILITY_IDENTITY_MISMATCH")
    if capability and aware_datetime(capability["last_seen_at"]) >= aware_datetime(negative["last_seen_at"]):
        return current
    rebound = dict(negative, identity=list(_identity(record)),
        negative_origin_identity=negative.get("negative_origin_identity") or source_identity)
    # Adapt the cache context only. persist_payload still reads its own full
    # identity; no alias history or closed-point rejection is inherited.
    return dict(context, detail=json.dumps({"schema": INTRADAY_CAPABILITY_SCHEMA,
        "capability": rebound}, sort_keys=True, separators=(",", ":"), allow_nan=False))


def persist_payload(store, record, points, *, received_at, capability_recovery=None):
    identity = _identity(record)
    received_at = _stamp(received_at)
    points = [(_stamp(t), _decimal(p, positive=True), _decimal(v, nonnegative=True)) for t,p,v in points]
    if any(aware_datetime(t) > aware_datetime(received_at) for t,_,_ in points):
        raise ValueError("INTRADAY_EVENT_IN_FUTURE")
    # Contract state is trading-session scoped. A rejection from a prior local
    # trading day must never poison the next session.
    previous = previous_for_session(_state(store, identity), received_at=received_at)
    capability = capability_recovery or _capability_detail(previous)
    if capability and aware_datetime(received_at) < aware_datetime(capability.get("probe_started_at") or capability["last_seen_at"]):
        raise ValueError("INTRADAY_CAPABILITY_CLOCK_ROLLBACK")
    payload_present = bool(points)
    if capability_recovery is not None:
        # A successful probe establishes a new causal epoch. Its historical
        # backfill is audit-only; every indicator point must arrive after it.
        previous = dict(previous or {}, observations=0, stable_overlap=0)
    if capability and capability.get("warmup_reset_required") and payload_present:
        # Direct/restarted callers cannot turn a negative into confirmed data
        # without performing the same fresh, read-only recovery protocol.
        source_age = (aware_datetime(received_at) - aware_datetime(points[-1][0])).total_seconds()
        if not 0 <= source_age <= INTRADAY_SOURCE_MAX_AGE_SECONDS:
            raise ValueError("INTRADAY_REPROBE_NO_FRESH_SOURCE")
        capability = dict(capability, warmup_reset_required=False,
            warmup_after=_stamp(received_at), recovered_at=_stamp(received_at),
            last_seen_at=_stamp(received_at), session_id=aware_datetime(received_at).astimezone(TZ).date().isoformat(),
            retry_due_at=_stamp(aware_datetime(received_at) + timedelta(seconds=INTRADAY_NEGATIVE_TTL_SECONDS)),
            attempts=min(2147483647, capability["attempts"] + 1), consecutive_failures=0,
            last_result="READ_ONLY_RECOVERED", reason_code="INTRADAY_CAPABILITY_RECOVERED_WARMUP_REQUIRED",
            capability_status="RECOVERED_WARMUP")
        previous = dict(previous or {}, observations=0, stable_overlap=0)
    if capability and aware_datetime(received_at) < aware_datetime(capability["last_seen_at"]):
        raise ValueError("INTRADAY_CAPABILITY_CLOCK_ROLLBACK")
    warmup_after = capability.get("warmup_after") if capability else None
    if warmup_after:
        epoch = aware_datetime(warmup_after)
        if epoch > aware_datetime(received_at):
            raise ValueError("INTRADAY_CAPABILITY_CLOCK_ROLLBACK")
        points = [point for point in points if aware_datetime(point[0]) > epoch]
    stable = changed = inserted = refreshed = 0
    down_steps = sum(1 for left, right in zip(points, points[1:]) if right[2] < left[2])
    from rc6_signal_contracts import volume_contract, quantity_activity
    volume_evidence, volume_reason = None, ""
    try:
        volume_evidence = volume_contract(record, received_at)
        quantity_activity([p[2] for p in points], volume_evidence)
    except ValueError as exc:
        volume_reason = str(exc)
    with store.connect() as connection:
        register_exact_time(connection)
        connection.execute("BEGIN IMMEDIATE")
        for event_at, price, volume in points:
            existing = connection.execute("""SELECT price,volume FROM ppi_intraday_points
              WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=? AND event_at=?""",
              (*identity, event_at)).fetchone()
            values = (format(price, "f"), format(volume, "f"))
            if existing:
                decision = classify_revision(
                    event_at=event_at, received_at=received_at,
                    old_price=existing[0], old_volume=existing[1],
                    new_price=values[0], new_volume=values[1])
                if decision["action"] == "SAME":
                    if decision["age_seconds"] > 120:
                        stable += 1
                    connection.execute("""UPDATE ppi_intraday_points SET last_verified_at=?
                      WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=? AND event_at=?""",
                      (received_at, *identity, event_at))
                elif decision["action"] == "REFRESH_MUTABLE":
                    # PPI may finalize the still-forming recent minute. Persist
                    # the newest baseline now so it is not falsely rejected once
                    # the point ages beyond the mutable window.
                    connection.execute("""UPDATE ppi_intraday_points
                      SET price=?,volume=?,last_verified_at=?
                      WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=? AND event_at=?""",
                      (*values, received_at, *identity, event_at))
                    refreshed += 1
                else:
                    # A genuine revision to a closed minute remains fail-closed.
                    changed += 1
                continue
            connection.execute("""INSERT INTO ppi_intraday_points VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (*identity, event_at, *values, received_at, received_at, "PPI_MARKETDATA_INTRADAY"))
            inserted += 1
        # A re-probe's backfill supplies no post-recovery sample. Counting that
        # response would spend one of the fresh confirmation observations.
        observations = (previous or {}).get("observations", 0) + int(bool(points) or not warmup_after)
        prior_stable = (previous or {}).get("stable_overlap", 0)
        prior_changed = (previous or {}).get("changed_closed_points", 0)
        confirmed_now = (observations >= 2 and stable >= 5 and inserted >= 1
                         and not volume_reason and changed == 0 and prior_changed == 0)
        state = ("REJECTED_MUTABLE_CLOSED_POINTS" if changed or prior_changed
                 else "EMPTY_INTRADAY_PAYLOAD" if not payload_present
                 else volume_reason if volume_reason
                 else "CONFIRMED_INTERVAL_VOLUME" if confirmed_now or (
                    (previous or {}).get("state") == "CONFIRMED_INTERVAL_VOLUME"
                    and changed == 0 and prior_changed == 0)
                 else "PENDING_LIVE_CONFIRMATION")
        volume_contract_state = state
        if warmup_after:
            # Recovery is a distinct durable state until the *whole* native
            # warmup is satisfied. Even replacing metadata with valid legacy
            # text cannot borrow pre-epoch history before all 15 source samples.
            # Empty and rejected native transitions retain that authority too;
            # closed-minute rejection remains stronger than an ordinary warmup.
            if state == "REJECTED_MUTABLE_CLOSED_POINTS":
                state = INTRADAY_CAPABILITY_REJECTED_STATE
            elif state != "CONFIRMED_INTERVAL_VOLUME":
                state = INTRADAY_CAPABILITY_WARMUP_STATE
            else:
                post_epoch_samples = connection.execute("""SELECT COUNT(*) FROM (
                SELECT 1 FROM ppi_intraday_points
                WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=?
                  AND rc6_instant_us(event_at)>rc6_instant_us(?) LIMIT 15)""",
                    (*identity, warmup_after)).fetchone()[0]
                if post_epoch_samples < 15:
                    state = INTRADAY_CAPABILITY_WARMUP_STATE
        last_source = points[-1][0] if points else None
        detail = (f"observaciones={observations}; solapamiento_estable={stable}; "
                  f"cerrados_modificados={changed}; nuevos={inserted}; "
                  f"mutables_refrescados={refreshed}; descensos_volumen={down_steps}")
        if capability or volume_evidence:
            detail = json.dumps({"schema": INTRADAY_CAPABILITY_SCHEMA if capability else "rc6.intraday-source-contract.v1",
                **({"capability": capability} if capability else {}),
                "volume_contract": volume_evidence, "volume_contract_state": volume_contract_state,
                "contract_detail": detail},
                sort_keys=True, separators=(",", ":"), allow_nan=False)
        connection.execute("""INSERT OR REPLACE INTO ppi_intraday_contract_state
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (*identity, state, observations, max(prior_stable, stable), prior_changed + changed,
           inserted, last_source, received_at, detail))
    return {"state": state, "inserted": inserted, "stable": stable,
            "changed": changed, "refreshed": refreshed, "down_steps": down_steps}


SCALPING_PAPER_CAPABILITIES = frozenset({
    "READY_PAPER_SPOT", "READY_PAPER", "READY_PAPER_SHADOW",
    "READY_SHADOW", "READY_SHADOW_COMPLEMENTED", "READY_SHADOW_PARTIAL",
})


def _intraday_freshness_reason(contract, *, at, latest_event_at):
    """Fail closed unless the current source and indicator tail agree and age safely."""
    capability = _capability_detail(contract)
    if capability and capability.get("warmup_reset_required"):
        return contract.get("state") or "INTRADAY_CAPABILITY_REPROBE_REQUIRED"
    if contract.get("state") != "CONFIRMED_INTERVAL_VOLUME":
        return contract.get("state") or "PENDING_LIVE_CONFIRMATION"
    source_at = contract.get("last_source_at")
    checked_at = contract.get("checked_at")
    if not source_at or not checked_at or not latest_event_at:
        return "NO_CURRENT_INTRADAY_SOURCE"
    try:
        now = aware_datetime(at)
        source = aware_datetime(source_at)
        checked = aware_datetime(checked_at)
        latest = aware_datetime(latest_event_at)
    except (ValueError, TypeError):
        return "INVALID_INTRADAY_TIMESTAMP"
    ages = [(now - value).total_seconds() for value in (source, checked, latest)]
    if any(age < 0 for age in ages) or source > checked:
        return "INTRADAY_TIMESTAMP_IN_FUTURE"
    if any(age > INTRADAY_SOURCE_MAX_AGE_SECONDS for age in ages):
        return "STALE_INTRADAY_SOURCE"
    if source != latest:
        return "INTRADAY_SOURCE_MISMATCH"
    return ""


def evaluate_candidate(store, record, *, at):
    identity = _identity(record)
    contract = _state(store, identity) or {}
    capability = _capability_detail(contract)
    warmup_after = (capability or {}).get("warmup_after")
    reason = ""
    action = "HOLD"
    score = Decimal("0")
    economics = {"binding": True, "execution_enabled": False, "passed": False}
    from rc6_signal_contracts import event_window_contract, volume_contract, quantity_activity
    with store.connect() as connection:
        register_exact_time(connection)
        points = connection.execute("""SELECT event_at,price,volume,first_received_at,last_verified_at,source FROM ppi_intraday_points
          WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=?
            AND rc6_instant_us(event_at)>=rc6_instant_us(?) AND rc6_instant_us(event_at)<=rc6_instant_us(?)
            AND rc6_instant_us(first_received_at)<=rc6_instant_us(?)
            AND rc6_instant_us(last_verified_at)<=rc6_instant_us(?)
            AND (? IS NULL OR rc6_instant_us(event_at)>rc6_instant_us(?))
          ORDER BY event_at DESC LIMIT 30""",
          (*identity, _stamp(aware_datetime(at)-timedelta(minutes=45)), _stamp(at), _stamp(at), _stamp(at),
           warmup_after, warmup_after)).fetchall()
        quote = connection.execute("""SELECT * FROM market_snapshots
          WHERE symbol=? AND asset_class=? AND settlement=? AND currency=? AND market=?
          ORDER BY id DESC LIMIT 1""",
          (record["ticker"],record["instrument_type"],record["settlement"],
           record["currency"],record["market"])).fetchone()
    points = list(reversed(points))
    temporal = event_window_contract(points, at)
    economics["temporal_contract"] = temporal
    freshness_reason = _intraday_freshness_reason(
        contract, at=at, latest_event_at=points[-1]["event_at"] if points else None)
    if record.get("capability") not in SCALPING_PAPER_CAPABILITIES:
        reason = record.get("capability") or "CONTRACT_NOT_SIMULATABLE"
    elif freshness_reason:
        reason = freshness_reason
    elif temporal["reason_code"]:
        reason = temporal["reason_code"]
    elif not quote:
        reason = "NO_CURRENT_BOOK"
    else:
        try:
            prices = [_decimal(row["price"], positive=True) for row in points]
            explicit_volume = volume_contract(record, at)
            volumes = quantity_activity([row["volume"] for row in points], explicit_volume)
            economics["volume_contract"] = explicit_volume
            bid, ask = _decimal(quote["bid"], positive=True), _decimal(quote["ask"], positive=True)
            if ask < bid:
                raise ValueError("CROSSED_BOOK")
            quote_age = (aware_datetime(at)-aware_datetime(quote["book_at"])).total_seconds()
            if not 0 <= quote_age <= 120:
                raise ValueError("STALE_BOOK")
            short = sum(prices[-3:]) / 3
            long = sum(prices[-15:]) / 15
            momentum = short / long - 1
            spread = ask / bid - 1
            observed_range = max(prices[-15:]) / min(prices[-15:]) - 1
            import au_fee_schedule
            one_leg = Decimal(str(au_fee_schedule.costo_por_tramo(family_name(record["instrument_type"]))))
            modeled_roundtrip = one_leg * 2 + spread + Decimal("0.0004")
            required_move = modeled_roundtrip + Decimal(os.getenv("PAPER_SCALPING_MIN_NET_MARGIN", "0.005"))
            score = max(Decimal(0), min(Decimal(1), Decimal("0.5") + momentum*40 - spread*10))
            economics.update({
                "modeled_roundtrip_fraction": str(modeled_roundtrip),
                "required_move_fraction": str(required_move),
                "observed_event_window_range_fraction": str(observed_range),
                "observed_event_span_seconds": temporal["observed_span_seconds"],
                "spread_fraction": str(spread), "momentum": str(momentum),
            })
            if sum(volumes[-5:]) <= 0:
                reason = "NO_RECENT_VOLUME"
            elif spread > Decimal(os.getenv("PAPER_SCALPING_MAX_SPREAD", "0.005")):
                reason = "SPREAD_TOO_WIDE"
            elif observed_range <= required_move:
                reason = "ECONOMICS_BINDING_RANGE_BELOW_COST"
            elif score < Decimal(os.getenv("PAPER_SCALPING_SCORE_THRESHOLD", "0.68")):
                reason = "SCALPING_SCORE_BELOW_THRESHOLD"
            else:
                action, reason = "BUY_CANDIDATE", "VALIDATED_SCALPING_CANDIDATE"
                economics["passed"] = True
        except (ValueError, TypeError, InvalidOperation) as exc:
            reason = str(exc) or type(exc).__name__
    evaluated = _stamp(at)
    with store.connect() as connection:
        register_exact_time(connection)
        connection.execute("""INSERT OR IGNORE INTO scalping_candidates
          VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (evaluated,*identity,action,str(score),str(points[-1]["price"]) if points else None,
           str(points[-1]["volume"]) if points else None,len(points),reason,
           json.dumps(economics,sort_keys=True)))
        quote_evidence = dict(quote) if quote else {
            "symbol": record["ticker"], "asset_class": record["instrument_type"],
            "market": record["market"], "currency": record["currency"],
            "settlement": record["settlement"], "observed_at": evaluated,
            "metadata_source": "NO_CURRENT_BOOK; IDENTITY_ONLY"}
        _record_native_entry_snapshot(connection, record, points, quote_evidence, contract,
            action=action, score=score, reason=reason, economics=economics, evaluated=evaluated)
    return action


def _record_native_entry_snapshot(connection, record, points, quote, contract, *,
                                  action, score, reason, economics, evaluated):
    """Same native writer/input vector; telemetry has no execution callback."""
    import hashlib
    from rc6_dynamic_universe.common import digest
    from rc6_shadow_runtime.entry_signals import native_entry_snapshot, NATIVE_INPUT_SCHEMA
    key = "scalping-native:" + digest(_identity(record)) + ":" + evaluated + ":" + str(quote.get("book_at"))
    config = {"version": "SCALPING_HF3_NATIVE_V1", "samples": 15, "window_minutes": 45,
        "min_net_margin": os.getenv("PAPER_SCALPING_MIN_NET_MARGIN", "0.005"),
        "max_spread": os.getenv("PAPER_SCALPING_MAX_SPREAD", "0.005"),
        "score_threshold": os.getenv("PAPER_SCALPING_SCORE_THRESHOLD", "0.68"),
        "source_max_age_seconds": INTRADAY_SOURCE_MAX_AGE_SECONDS}
    inputs = {"schema": NATIVE_INPUT_SCHEMA,
        "price_samples": [{"price": str(p["price"]), "source_at": p["event_at"],
            "received_at": p["last_verified_at"], "first_received_at": p["first_received_at"],
            "source": p["source"]} for p in points],
        "momentum": economics.get("momentum"), "spread_fraction": economics.get("spread_fraction"),
        "samples": len(points), "rvol": None,
        "temporal_contract": economics.get("temporal_contract"),
        "raw_volume_samples": [str(p["volume"]) for p in points],
        "volume_contract": economics.get("volume_contract"),
        "activity": {"interval_volume_contract": contract.get("state", "NO_VERIFICADO"),
            "volume_unit": (economics.get("volume_contract") or {}).get("unit", "NO_VERIFICADO"),
            "volume_contract": economics.get("volume_contract"), "rvol_status": "NO_VERIFICADO"}}
    exact_quote = {name: quote.get(name) for name in ("symbol", "asset_class", "settlement", "currency", "market",
        "bid", "ask", "bid_size", "ask_size", "last", "book_at", "trade_at", "observed_at")}
    exact_quote["metadata_source"] = quote.get("metadata_source") or quote.get("source")
    from bq_exit_policy import PaperSessionPolicy
    policy = PaperSessionPolicy()
    if policy.supports(exact_quote) and policy.close_at_eod:
        _, close_at = policy.bounds(evaluated, exact_quote)
        inputs["eod_at"] = _stamp(close_at - timedelta(minutes=policy.exit_minutes))
        inputs["eod_policy"] = {"source": "bq_exit_policy.PaperSessionPolicy",
            "exit_minutes": policy.exit_minutes, "close_at_eod": policy.close_at_eod}
    else:
        inputs["eod_at"] = None
        inputs["eod_reason"] = "NATIVE_EOD_HORIZON_UNAVAILABLE"
    snapshot = native_entry_snapshot(decision_key=key, quote=exact_quote, action=action,
        score=str(score), reason=reason, strategy_id="SCALPING_BASELINE", strategy_version=config["version"],
        signal_at=evaluated, decision_at=evaluated, configuration_fingerprint=digest(config),
        entry_signal_inputs=inputs, economics=economics, git_sha=os.getenv("POROTA_BUILD_SHA"))
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    connection.execute("""INSERT OR IGNORE INTO decision_evidence_snapshots
        (decision_key,captured_at,schema_version,payload_sha256,payload_json) VALUES(?,?,?,?,?)""",
        (key, snapshot["captured_at"], "rc6.decision-inputs.v1", hashlib.sha256(encoded.encode()).hexdigest(), encoded))


def promote_paper_candidate(store, record, *, at):
    """Convertir un candidato validado en fill exclusivamente simulado.

    Comparte caja, límite global, supervisor, daily-risk y ledger con el motor
    principal. No importa ni invoca ningún cliente de órdenes PPI.
    """
    mode = os.getenv("PAPER_SCALPING_MODE", "ACTIVE_OBSERVE").upper()
    if mode != "ACTIVE_PAPER":
        return "OBSERVE_ONLY"
    contract = _state(store, _identity(record)) or {}
    with store.connect() as connection:
        register_exact_time(connection)
        scalp_open = 0
        for row in connection.execute("SELECT features_json FROM paper_positions WHERE status='OPEN'"):
            try:
                scalp_open += int(json.loads(row[0] or "{}").get("execution_style") == "SCALPING_PAPER")
            except (ValueError, TypeError):
                continue
        if scalp_open >= int(os.getenv("PAPER_SCALPING_MAX_OPEN_POSITIONS", "1")):
            return "SCALPING_POSITION_LIMIT"
        quote_row = connection.execute("""SELECT * FROM market_snapshots
          WHERE symbol=? AND asset_class=? AND settlement=? AND currency=? AND market=?
          ORDER BY id DESC LIMIT 1""",
          (record["ticker"],record["instrument_type"],record["settlement"],
           record["currency"],record["market"])).fetchone()
        candidate = connection.execute("""SELECT * FROM scalping_candidates
          WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=?
          ORDER BY id DESC LIMIT 1""", _identity(record)).fetchone()
        latest = connection.execute("""SELECT event_at FROM ppi_intraday_points
          WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=?
          ORDER BY event_at DESC LIMIT 1""", _identity(record)).fetchone()
    freshness_reason = _intraday_freshness_reason(
        contract, at=at, latest_event_at=latest["event_at"] if latest else None)
    if freshness_reason:
        return freshness_reason
    if not quote_row or not candidate or candidate["action"] != "BUY_CANDIDATE":
        return "CANDIDATE_NOT_AVAILABLE"
    # An entry can be delayed after evaluation. Recheck both inputs before
    # loading the broker; a fresh book alone cannot revive a stale signal.
    try:
        candidate_at = aware_datetime(candidate["evaluated_at"])
        capability = _capability_detail(contract)
        if capability and capability.get("warmup_after"):
            if candidate_at <= aware_datetime(capability["warmup_after"]):
                return "SCALPING_CANDIDATE_BEFORE_CAPABILITY_RECOVERY"
        candidate_age = (aware_datetime(at)-candidate_at).total_seconds()
    except (ValueError, TypeError):
        return "INVALID_SCALPING_CANDIDATE_TIMESTAMP"
    if not 0 <= candidate_age <= INTRADAY_SOURCE_MAX_AGE_SECONDS:
        return "STALE_SCALPING_CANDIDATE"
    try:
        quote_age = (aware_datetime(at)-aware_datetime(quote_row["book_at"])).total_seconds()
    except (ValueError, TypeError):
        return "INVALID_BOOK_TIMESTAMP"
    if not 0 <= quote_age <= 120:
        return "STALE_BOOK"
    from be_paper_engine import Quote
    from bv_paper_runtime import broker_from_environment
    q = Quote(
        symbol=quote_row["symbol"], asset_class=quote_row["asset_class"],
        settlement=quote_row["settlement"], last=_decimal(quote_row["last"],nonnegative=True),
        bid=_decimal(quote_row["bid"],positive=True), ask=_decimal(quote_row["ask"],positive=True),
        bid_size=_decimal(quote_row["bid_size"],nonnegative=True),
        ask_size=_decimal(quote_row["ask_size"],nonnegative=True),
        observed_at=quote_row["observed_at"], currency=quote_row["currency"],
        market=quote_row["market"], metadata_source=quote_row["metadata_source"],
        opening_block_reason=quote_row["opening_block_reason"], book_at=quote_row["book_at"],
        trade_at=quote_row["trade_at"], last_kind=quote_row["last_kind"])
    from rc6_dynamic_universe.common import digest
    prefix = "scalping-native:" + digest(_identity(record)) + ":" + str(candidate["evaluated_at"]) + ":"
    with store.connect() as connection:
        frozen_rows = connection.execute("""SELECT decision_key,payload_sha256,payload_json
            FROM decision_evidence_snapshots WHERE substr(decision_key,1,?)=?""", (len(prefix),prefix)).fetchall()
    if len(frozen_rows) != 1:
        return "SCALPING_NATIVE_EVIDENCE_UNAVAILABLE"
    frozen = dict(frozen_rows[0])
    if hashlib.sha256(frozen["payload_json"].encode()).hexdigest() != frozen["payload_sha256"]:
        return "SCALPING_NATIVE_EVIDENCE_HASH_INVALID"
    native = json.loads(frozen["payload_json"])
    economics = json.loads(candidate["economics_json"] or "{}")
    # Range screening is retrospective; the shared broker recomputes actual
    # net reward/risk for its Stop/TP and complete PAPER cost contract.
    economics.update(binding=True, execution_enabled=False, model="SCALPING_EVENT_RANGE_SCREEN_V1")
    features = {
        "execution_style": "SCALPING_PAPER",
        "scalping_max_hold_minutes": int(os.getenv("PAPER_SCALPING_MAX_HOLD_MINUTES", "30")),
        "intraday_points": candidate["points"], "intraday_score": candidate["score"],
        "candidate_reason": candidate["reason"], "economics": economics,
        "scalping_range_screen": dict(economics),
        "signal_snapshot_key": frozen["decision_key"], "signal_snapshot_sha256": frozen["payload_sha256"],
        "entry_signal_inputs": native["inputs_used"]["entry_signal_inputs"],
    }
    key = "SCALPING:" + ":".join(_identity(record)) + ":" + str(candidate["evaluated_at"])
    features["native_decision_key"] = key
    if not store.record_decision(key, q, "BUY", Decimal(candidate["score"]),
                                 "Candidato scalping validado", features):
        return "DUPLICATE_DECISION"
    broker = broker_from_environment(
        store, risk_pct=os.getenv("PAPER_SCALPING_RISK_PER_TRADE", "0.001"),
        stop_loss_pct=os.getenv("PAPER_SCALPING_STOP_LOSS_PCT", "0.008"),
        target_gain_pct=os.getenv("PAPER_SCALPING_TARGET_GAIN_PCT", "0.02"))
    opened, reason, paper_id = broker.admit_paper_candidate(q, Decimal(candidate["score"]), features)
    store.record_gates(q, key, "APPROVE", "NOT_USED",
                       "APPROVE" if opened else "BLOCKED",
                       "OPENED_SIMULATED" if opened else "BLOCKED", reason,
                       paper_id=paper_id, detail=features)
    if opened:
        store.event("SCALPING_PAPER_FILLED_BUY", f"{q.symbol}: fill simulado", paper_id)
        return "OPENED_SIMULATED"
    return "BLOCKED:" + reason


def _heartbeat(store, *, at, state, cursor, selected=0, successful=0, failed=0,
               inserted=0, confirmed=0, candidates=0, detail=""):
    with store.connect() as connection:
        register_exact_time(connection)
        orders = connection.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()
        real_orders = int(orders[0]) if orders else -1
        connection.execute("""INSERT OR REPLACE INTO intraday_scalping_worker_state
          VALUES(1,?,?,?,?,?,?,?,?,?,?,?)""",
          (_stamp(at),state,cursor,selected,successful,failed,inserted,confirmed,
           candidates,real_orders,detail))


def run_worker(store, stop, *, clock_fn):
    """Proceso independiente; un error de PPI nunca detiene reloj ni salidas."""
    if os.getenv("POROTA_RUNTIME_SCHEMA_READY", "").strip() == "1":
        with closing(store.connect()) as connection:
            connection.execute("PRAGMA query_only=ON")
            require_intraday_temporal_index(connection)
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"ppi_intraday_contract_state", "scalping_candidates",
                    "intraday_scalping_worker_state"}.issubset(tables):
                raise RuntimeError("INTRADAY_PARENT_SCHEMA_REQUIRED")
    else:
        init_schema(store)
    from bd_ppi_readonly_guard import (ProductionMarketReader, retry_read,
                                       classify_read_error,
                                       instrument_not_found)
    from bf_production_paper_observer import _secret
    cursor = 0
    reader = None
    next_login = 0.0
    capability_cache = IntradayCapabilityCache()
    interval = max(60, int(os.getenv(
        "PAPER_INTRADAY_SCAN_SECONDS", str(DEFAULT_INTRADAY_SCAN_SECONDS))))
    batch_limit = max(8, min(40, int(os.getenv(
        "PAPER_INTRADAY_BATCH_LIMIT", str(DEFAULT_INTRADAY_BATCH_LIMIT)))))
    # Each rotating batch gets a second, fresh DB-selected pass on the next
    # cycle before the cursor advances. This makes PENDING_LIVE_CONFIRMATION a
    # bounded warm-up state instead of waiting for a full-universe rotation.
    paired_recheck = False
    paired_next_cursor = cursor
    try:
        while not stop.is_set():
            at = aware_datetime(clock_fn())
            if not _market_open(at):
                paired_recheck = False
                paired_next_cursor = cursor
                _heartbeat(store,at=at,state="WAITING_MARKET",cursor=cursor,
                           detail="Scanner activo; espera ventana intradiaria 10:30-17:00 Argentina")
                stop.wait(20)
                continue
            if reader is None:
                if time.monotonic() < next_login:
                    _heartbeat(store,at=at,state="LOGIN_COOLDOWN",cursor=cursor)
                    stop.wait(20)
                    continue
                try:
                    reader = ProductionMarketReader(*_secret(), audit=store.audit_http, consumer="SCALPING")
                    reader.login_once()
                    store.event("PPI_LOGIN", "owner=scalping")
                except Exception as exc:
                    if reader:
                        reader.close()
                    reader = None
                    next_login = time.monotonic() + 900
                    _heartbeat(store,at=at,state="LOGIN_ERROR",cursor=cursor,failed=1,
                               detail=type(exc).__name__)
                    stop.wait(20)
                    continue
            # Keep the rotation cursor fixed for one extra cycle. Calling
            # select_batch again revalidates exact full-key readiness instead
            # of replaying stale record dictionaries from memory.
            selected, next_cursor, universe, dynamic_selection = select_runtime_batch(
                store, limit=batch_limit, cursor=cursor, at=at)
            phase = "RECHECK" if paired_recheck else "BASELINE"
            if dynamic_selection["dynamic"]:
                phase = "APPROVED_DYNAMIC"
                paired_recheck = False
                paired_next_cursor = cursor
            elif paired_recheck:
                cursor = paired_next_cursor
                paired_recheck = False
            else:
                paired_next_cursor = next_cursor
                paired_recheck = True
            opened_keys = set()
            if getattr(reader, "budget_enabled", False) is True:
                opened_keys = {(p["symbol"], p["asset_class"], p["market"], p["currency"], p["settlement"])
                    for p in store.open_positions() if p["asset_class"] in SCALPING_STRATEGY_FAMILIES}
            successful = failed = inserted = confirmed = candidates = 0
            unsupported_this_batch = 0
            cooldown_skipped = reprobes = recovered = 0
            invalid_session = False
            for record in selected:
                if stop.is_set():
                    break
                capability = None
                reprobe = False
                fingerprint = ""
                read_error_code = None
                try:
                    key = _identity(record)
                    capability = capability_cache.restore(record, _request_capability_state(store, record))
                    fingerprint = _capability_fingerprint(record, dynamic_selection)
                    decision = capability_cache.decision(capability, at=at, fingerprint=fingerprint)
                    if not decision["allowed"]:
                        cooldown_skipped += 1
                        continue
                    reprobe = decision["reprobe"]
                    if reprobe:
                        # Invalidate old signal authority before the read-only
                        # wire call, also for session/config-triggered probes.
                        capability = capability_cache.begin_probe(record, capability, at=at,
                            fingerprint=fingerprint, reason=decision["reason"])
                        _invalidate_intraday_capability(store, record, capability, at=at)
                        reprobes += 1
                    if dynamic_selection["dynamic"]:
                        state = dynamic_selection["rows"][key]["state"]
                        priority = "OPENED_CRITICAL" if key in dynamic_selection["opened_priority"] else {"HOT": "SCALPING_HOT", "WARM": "WARM"}.get(state, "DISCOVERY")
                    else:
                        priority = "OPENED_CRITICAL" if key in opened_keys else "DISCOVERY"
                    scope = (reader.read_scope(priority=priority, identity=key)
                        if getattr(reader, "budget_enabled", False) is True and hasattr(reader, "read_scope") else nullcontext())
                    with scope:
                        try:
                            payload = retry_read(lambda: reader.intraday(
                                record["ticker"],record["instrument_type"],record["settlement"]),
                                retries=0 if reprobe else 1)
                        except Exception as read_error:
                            # Authentication/HTTP diagnostics belong only to
                            # the actual read. Local persistence/decoder errors
                            # cannot borrow an earlier transport result or turn
                            # malformed stored text into a new login attempt.
                            read_error_code = _intraday_read_error_code(reader, read_error, classify_read_error)
                            raise
                    received = _stamp(clock_fn())
                    if aware_datetime(received) < at:
                        raise ValueError("INTRADAY_CAPABILITY_CLOCK_ROLLBACK")
                    points = normalize_payload(payload,received_at=received)
                    if reprobe:
                        if not points or not 0 <= (aware_datetime(received) - aware_datetime(points[-1][0])).total_seconds() <= INTRADAY_SOURCE_MAX_AGE_SECONDS:
                            raise ValueError("INTRADAY_REPROBE_NO_FRESH_SOURCE")
                        recovery = capability_cache.outcome(record, capability, at=received,
                            fingerprint=fingerprint, result="READ_ONLY_RECOVERED", recovered=True,
                            probe_reason=decision["reason"], attempt_started=True)
                        result = persist_payload(store, record, points, received_at=received,
                            capability_recovery=recovery)
                        _capability_event(store, "INTRADAY_SCALPING_CAPABILITY_RECOVERED", record, recovery)
                        recovered += 1
                    else:
                        result = persist_payload(store,record,points,received_at=received)
                    inserted += result["inserted"]
                    confirmed += int(result["state"] == "CONFIRMED_INTERVAL_VOLUME")
                    candidate_action = "HOLD" if reprobe else evaluate_candidate(store,record,at=received)
                    if candidate_action == "BUY_CANDIDATE":
                        candidates += 1
                        result_action = ("DISCOVERY_OR_WARMUP_NO_ENTRY_AUTHORITY" if dynamic_selection["dynamic"]
                            and key not in dynamic_selection["entry_identities"] else
                            promote_paper_candidate(store,record,at=_stamp(clock_fn())))
                        store.event("SCALPING_PAPER_PROMOTION", f"{record['ticker']}: {result_action}")
                    successful += 1
                except Exception as exc:
                    failure_at = aware_datetime(clock_fn())
                    if failure_at < at or isinstance(exc, ValueError) and str(exc) == "INTRADAY_CAPABILITY_CLOCK_ROLLBACK":
                        # The durable admitted attempt remains pending. Publishing
                        # a negative/recovery at an earlier wall time would itself
                        # falsify its causal epoch; the next normal tick can retry.
                        failed += 1
                        store.event("INTRADAY_SCALPING_CLOCK_ROLLBACK", record["ticker"])
                        stop.wait(0.75)
                        continue
                    error_code = read_error_code or "PPI_" + type(exc).__name__.upper()
                    if read_error_code == "PPI_INSTRUMENT_NOT_FOUND" and instrument_not_found(exc):
                        negative_at = _stamp(clock_fn())
                        negative = capability_cache.outcome(record, capability, at=negative_at,
                            fingerprint=fingerprint, result="PPI_INSTRUMENT_NOT_FOUND",
                            probe_reason=decision["reason"] if reprobe else None, attempt_started=reprobe)
                        _invalidate_intraday_capability(store, record, negative, at=negative_at)
                        unsupported_this_batch += 1
                        _capability_event(store, "INTRADAY_SCALPING_UNSUPPORTED", record, negative)
                    else:
                        if reprobe:
                            retry_at = _stamp(clock_fn())
                            negative = capability_cache.outcome(record, capability, at=retry_at,
                                fingerprint=fingerprint, result=error_code,
                                probe_reason=decision["reason"], attempt_started=True)
                            _invalidate_intraday_capability(store, record, negative, at=retry_at)
                            _capability_event(store, "INTRADAY_SCALPING_CAPABILITY_REPROBE_FAILED", record, negative)
                        failed += 1
                        store.event("INTRADAY_SCALPING_ERROR",
                                    f"{record['ticker']}: {error_code};shadow_identity=" +
                                    json.dumps(_identity(record), separators=(",", ":")))
                        if error_code == "PPI_SESSION_INVALID":
                            invalid_session = True
                            break
                stop.wait(0.75)
            metrics = reader.metrics if reader else {"http_blocked": 0}
            state = "SECURITY_BLOCK" if metrics.get("http_blocked") or False else \
                    "DEGRADED" if failed and successful else "ERROR" if failed else "RUNNING"
            _heartbeat(store,at=clock_fn(),state=state,cursor=cursor,selected=len(selected),
                       successful=successful,failed=failed,inserted=inserted,
                       confirmed=confirmed,candidates=candidates,
                       detail=(f"universo={universe}; lote={len(selected)}; scanner activo; "
                               f"fase_confirmacion={phase}; paired_recheck={int(not dynamic_selection['dynamic'])}; "
                               f"intraday_unavailable={unsupported_this_batch}; "
                               f"unsupported_cached={sum(v['warmup_reset_required'] for v in capability_cache.entries.values())}; "
                               f"capability_cache_entries={len(capability_cache.entries)}; "
                               f"capability_cache_bound={capability_cache.maximum}; "
                               f"capability_cache_evictions={capability_cache.evictions}; "
                               f"capability_cooldown_skipped={cooldown_skipped}; "
                               f"capability_reprobes={reprobes}; capability_recovered={recovered}; "
                               f"modo={os.getenv('PAPER_SCALPING_MODE','ACTIVE_OBSERVE')}; "
                               "fills exclusivamente PAPER; órdenes reales bloqueadas"))
            if invalid_session:
                paired_recheck = False
                paired_next_cursor = cursor
                reader.close()
                reader = None
                next_login = time.monotonic() + 60
            stop.wait(dynamic_selection.get("cadence_seconds", interval))
    finally:
        if reader:
            reader.close()
        _heartbeat(store,at=clock_fn(),state="STOPPED",cursor=cursor,
                   detail="Proceso detenido; no se enviaron órdenes")
