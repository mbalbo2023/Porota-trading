"""Versioned cross-process wire budget for approved PPI market reads.

Only current/book/intraday are measured. One private sidecar database arbitrates
all engines and actual retries. No trading DB, broker or provider is imported.
"""
from __future__ import annotations

from contextlib import closing, contextmanager, nullcontext
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from copy import deepcopy
from math import ceil, isfinite
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
import uuid

from rc6_dynamic_universe.common import digest, stamp

SCHEMA = "RC6_GLOBAL_PPI_BUDGET_V1"
ENDPOINTS = ("current", "book", "intraday")
PRIORITIES = ("EXIT_CRITICAL", "OPENED_CRITICAL", "SCALPING_HOT", "STRATEGY_HOT", "WARM", "DISCOVERY")
CONSUMERS = ("SCANNER", "SCALPING", "EXIT_READER", "TREASURY", "UNSCOPED")
WINDOW_FIELDS = ("requested", "admitted", "used", "dropped", "borrowed_in", "borrowed_out", "coalesced")
BOOK_CACHE_LIMIT = 64
BOOK_CACHE_BYTES = 16384
AUTHORITY_LIMIT = 64
RECEIPT_LIMIT = 20000
RECEIPT_RETENTION_SECONDS = 3600
# Fixed receipt labels plus both SQLite b-trees fit this conservative page
# allowance. Binding state and the largest permitted critical cache payloads
# receive separate space; this does not enlarge the configured physical quota.
RECEIPT_STORAGE_BYTES = 512
CRITICAL_STATE_STORAGE_BYTES = 128 * 1024
SUPERVISABLE_POSITION_STATES = (("paper_positions", "OPEN"), ("paper_future_positions", "ACTIVE"))
EXIT_ROUND_PRESSURE_KEY = digest({"critical_exit_scope": "ALL_ACTIVE_PAPER_POSITIONS_ROUND_V1"})
# New custody guard, not a preexisting execution deadline: the former 5ms
# SQLite timeout bounded only lock waiting. Capture, SQL and cleanup share this
# conservative total bound; an inherited earlier absolute deadline wins.
LEDGER_SNAPSHOT_SECONDS = .15


@contextmanager
def _ledger_snapshot(database, *, deadline=None):
    end = time.monotonic() + LEDGER_SNAPSHOT_SECONDS
    if deadline is not None:
        if (isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                or not Decimal(str(deadline)).is_finite()):
            raise ValueError("PPI_BUDGET_LEDGER_DEADLINE_INVALID")
        end = min(end, deadline)
    if time.monotonic() >= end:
        raise ValueError("PPI_BUDGET_LEDGER_DEADLINE_EXHAUSTED")
    from rc6_shadow_runtime.source_reads import source_connection
    with source_connection(database, deadline=end) as (connection, _):
        connection.execute("PRAGMA busy_timeout=5")
        # Preserve the original 100000-VM-step ceiling while checking time
        # throughout SQL too, instead of replacing the copy's deadline guard.
        steps = 0
        def stop():
            nonlocal steps
            steps += 100
            return int(steps >= 100000 or time.monotonic() >= end)
        connection.set_progress_handler(stop, 100)
        connection.execute("BEGIN")
        yield connection
    # Cleanup is mandatory even on a timeout. Its elapsed time cannot turn an
    # expired capture into a successful fresh ledger observation.
    if time.monotonic() >= end:
        raise ValueError("PPI_BUDGET_LEDGER_DEADLINE_EXHAUSTED")


def _supervisable_identity_digests(database, *, deadline=None):
    """Verified exact five-key ledger scope, without provider/catalog inference."""
    try:
        with _ledger_snapshot(database, deadline=deadline) as c:
            state = c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
            if not state or tuple(state) != ("PRODUCTION_PAPER", 0):
                return None
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "paper_positions" not in tables:
                return None
            rows = list(c.execute("SELECT symbol,asset_class,market,currency,settlement FROM paper_positions WHERE status='OPEN'"))
            if "paper_future_positions" in tables:
                rows.extend(c.execute("SELECT symbol,'FUTUROS',market,currency,settlement FROM paper_future_positions WHERE status='ACTIVE'"))
            if len(rows) > BOOK_CACHE_LIMIT or any(
                    any(not isinstance(v, str) or not v.strip() for v in row) for row in rows):
                return None
            return {digest(list(row)) for row in rows}
    except (OSError, ValueError, sqlite3.Error):
        return None


def supervisable_position_count(database, unverified=None, *, deadline=None):
    """Count every exit-supervised family in one verified PAPER snapshot.

    None means unknown, never zero. Readers and capacity approval can consume
    the same table/state contract without importing a broker or provider.
    The new 0.15s custody guard includes a private NoAtime capture and cleanup;
    it never replaces an inherited earlier monotonic execution deadline.
    """
    try:
        with _ledger_snapshot(database, deadline=deadline) as c:
            state = c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
            if not state or tuple(state) != ("PRODUCTION_PAPER", 0):
                raise ValueError("PPI_BUDGET_PAPER_SOURCE_UNVERIFIED")
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "paper_positions" not in tables:
                raise ValueError("PPI_BUDGET_OPENED_LEDGER_UNAVAILABLE")
            return sum(c.execute(f"SELECT COUNT(*) FROM {table} WHERE status=?", (status,)).fetchone()[0]
                for table, status in SUPERVISABLE_POSITION_STATES if table in tables)
    except (OSError, ValueError, sqlite3.Error):
        return unverified


class BudgetBackpressure(RuntimeError):
    """An off-wire scheduling rejection, never a provider capability result."""


def _frozen_admission_summary(value):
    """Bounded historical evidence only; never a current service authority."""
    if value is None:
        return None
    if (not isinstance(value, dict) or value.get("basis") != "FROZEN_AT_FIRST_ADMISSION"
            or not isinstance(value.get("identity_count"), int) or isinstance(value["identity_count"], bool)
            or not 0 <= value["identity_count"] <= BOOK_CACHE_LIMIT
            or any(not isinstance(value.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", value[field])
                for field in ("identity_scope_digest", "configuration_fingerprint", "recommendation_digest"))
            or not isinstance(value.get("reserved_open_positions_count"), int)
            or isinstance(value["reserved_open_positions_count"], bool)
            or not value["identity_count"] <= value["reserved_open_positions_count"] <= BOOK_CACHE_LIMIT
            or not isinstance(value.get("reserved_exit_demand"), dict)
            or set(value["reserved_exit_demand"]) != set(ENDPOINTS)
            or any(not isinstance(value.get(field), str) for field in ("captured_at", "expires_at"))):
        raise ValueError("PPI_EXIT_ROUND_MEASUREMENT_INVALID")
    if stamp(value["captured_at"]) >= stamp(value["expires_at"]):
        raise ValueError("PPI_EXIT_ROUND_MEASUREMENT_INVALID")
    for demand in value["reserved_exit_demand"].values():
        _int(demand)
    return {field: deepcopy(value[field]) for field in (
        "basis", "identity_count", "identity_scope_digest", "captured_at",
        "configuration_fingerprint", "recommendation_digest", "expires_at",
        "reserved_open_positions_count", "reserved_exit_demand")}


def _int(value, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("PPI_BUDGET_INTEGER_INVALID")
    return value


def exit_capacity_contract(state, *, opened_count=0):
    """Fail activation before a truncated reserve can promise impossible exits."""
    opened = _int(opened_count)
    envelope, settings = state["global_budget"], state["budget_settings"]
    window, cadence = _int(envelope["window_seconds"], 1), _int(settings["critical_book_seconds"], 1)
    demand = {"current": 0, "intraday": 0, "book": max(opened, (opened * window + cadence - 1) // cadence)}
    gaps = {e: max(0, demand[e] - _int(envelope["endpoint_limits"][e], 1)) for e in ENDPOINTS}
    global_gap = max(0, sum(demand.values()) - _int(envelope["global_limit"], 1))
    tracking_gap = max(0, opened - BOOK_CACHE_LIMIT)
    retention_rows = opened * (RECEIPT_RETENTION_SECONDS // cadence + 1)
    maximum_bytes = _int(settings["maximum_bytes"], 1)
    main_bytes = ((maximum_bytes - 608) // (2 * 4096 + 8)) * 4096
    retention_bytes = (retention_rows * RECEIPT_STORAGE_BYTES
        + opened * (BOOK_CACHE_BYTES + 256) + CRITICAL_STATE_STORAGE_BYTES
        + max(512, maximum_bytes // 64) + 7 * 4096) if opened else 7 * 4096
    row_gap = max(0, retention_rows - RECEIPT_LIMIT)
    byte_gap = max(0, retention_bytes - main_bytes)
    blocked = bool(global_gap or any(gaps.values()) or tracking_gap or row_gap or byte_gap)
    return {"schema": "RC6_EXIT_CAPACITY_CONTRACT_V1", "open_positions_count": opened,
        "exit_demand": demand, "endpoint_gaps": gaps, "global_gap": global_gap,
        "identity_tracking_limit": BOOK_CACHE_LIMIT, "identity_tracking_gap": tracking_gap,
        "receipt_retention": {"receipt_limit": RECEIPT_LIMIT,
            "retention_seconds": RECEIPT_RETENTION_SECONDS,
            "required_exit_receipts": retention_rows, "receipt_gap": row_gap,
            "required_main_bytes": retention_bytes, "available_main_bytes": main_bytes,
            "storage_gap_bytes": byte_gap,
            "storage_allowance": "CONSERVATIVE_FIXED_RECEIPT_AND_CRITICAL_STATE_PAGE_RESERVE"},
        "status": "ACTIVATION_BLOCKED_EXIT_CAPACITY" if blocked else "READY",
        "reason_codes": (["PPI_EXIT_CAPACITY_INSUFFICIENT"] if global_gap or any(gaps.values()) else [])
            + (["PPI_EXIT_IDENTITY_TRACKING_INSUFFICIENT"] if tracking_gap else [])
            + (["PPI_EXIT_RECEIPT_RETENTION_INSUFFICIENT"] if row_gap else [])
            + (["PPI_EXIT_RECEIPT_STORAGE_INSUFFICIENT"] if byte_gap else []),
        "deadline_seconds": settings["critical_book_seconds"], "real_orders_sent": 0}


def budget_policy(state, *, opened_count=0, planned_reservations=None):
    """Reserve actual critical demand, then planned high-priority deep tasks."""
    if state.get("status") != "APPROVED_DYNAMIC":
        raise ValueError("PPI_BUDGET_APPROVED_CAPACITY_REQUIRED")
    envelope = state["global_budget"]
    settings = state["budget_settings"]
    limits = envelope["endpoint_limits"]
    opened = _int(opened_count)
    exit_contract = exit_capacity_contract(state, opened_count=opened)
    if exit_contract["status"] != "READY":
        raise ValueError("PPI_EXIT_CAPACITY_INSUFFICIENT")
    exit_demand = exit_contract["exit_demand"]
    critical = dict(exit_demand)
    reserves = {"EXIT_CRITICAL": critical}
    # OPENED is a separate claimant. Its reads can never discharge EXIT's
    # indelegable floor, even when the requests refer to the same position.
    reserves["OPENED_CRITICAL"] = {e: min(limits[e], opened) for e in ENDPOINTS}
    for priority in ("SCALPING_HOT", "STRATEGY_HOT", "WARM"):
        demand = (planned_reservations or {}).get(priority, {})
        reserves[priority] = {e: min(limits[e], _int(demand.get(e, 0))) for e in ENDPOINTS}
    return {"schema": SCHEMA, "version": "rc6-global-ppi-v1",
        "recommendation_digest": state["recommendation_digest"],
        "configuration_fingerprint": state["configuration_fingerprint"],
        "window_seconds": envelope["window_seconds"], "endpoint_limits": dict(limits),
        "global_limit": envelope["global_limit"], "max_parallel_requests": 1,
        "safety_reserve": envelope["safety_reserve"], "priority_reserves": reserves,
        "expires_at": state["expires_at"], "open_positions_count": opened,
        "exit_demand": exit_demand, **settings}


def validate_policy(policy):
    if (not isinstance(policy, dict) or policy.get("schema") != SCHEMA or policy.get("max_parallel_requests") != 1
            or set(policy.get("endpoint_limits", {})) != set(ENDPOINTS)):
        raise ValueError("PPI_BUDGET_POLICY_INVALID")
    for value in policy["endpoint_limits"].values():
        _int(value, 1)
    for name in ("window_seconds", "global_limit", "critical_book_seconds", "lease_seconds", "breaker_seconds", "session_breaker_seconds", "server_error_threshold", "maximum_bytes"):
        _int(policy[name], 1)
    if (policy["lease_seconds"] < 45 or not 65536 <= policy["maximum_bytes"] <= 32 * 1024**2
            or policy["window_seconds"] > 3600
            or policy["global_limit"] > sum(policy["endpoint_limits"].values())):
        raise ValueError("PPI_BUDGET_BOUNDS_INVALID")
    for field in ("recommendation_digest", "configuration_fingerprint"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(policy.get(field, ""))):
            raise ValueError("PPI_BUDGET_FINGERPRINT_INVALID")
    for priority, values in policy.get("priority_reserves", {}).items():
        if priority not in PRIORITIES:
            raise ValueError("PPI_BUDGET_PRIORITY_INVALID")
        for endpoint, value in values.items():
            if endpoint not in ENDPOINTS or _int(value) > policy["endpoint_limits"][endpoint]:
                raise ValueError("PPI_BUDGET_RESERVATION_INVALID")
    stamp(policy["expires_at"])
    _int(policy.get("open_positions_count", 0))
    if policy.get("open_positions_count", 0) > BOOK_CACHE_LIMIT:
        raise ValueError("PPI_EXIT_IDENTITY_TRACKING_INSUFFICIENT")
    for endpoint, value in policy.get("exit_demand", {}).items():
        if endpoint not in ENDPOINTS:
            raise ValueError("PPI_BUDGET_EXIT_DEMAND_INVALID")
        _int(value)
    demand = {e: policy.get("exit_demand", {}).get(e, 0) for e in ENDPOINTS}
    if any(demand[e] > policy["endpoint_limits"][e] for e in ENDPOINTS) or sum(demand.values()) > policy["global_limit"]:
        raise ValueError("PPI_EXIT_CAPACITY_INSUFFICIENT")
    return policy


def _receipt_projection(policy, envelopes, receipts, now, *, protected_lease=None):
    """Peak retained growth of the same critical stream under every authority.

    Exact historic expirations release space only when their one-hour debt
    retention ends. Used EXIT receipts are not spare capacity: a recent burst
    may still coexist with a whole new hour of critical reads. An immediate
    sweep protects the unknown phase of a restarted reader. Older raw policies
    that promise one sweep per window keep that explicit promise, rather than
    being silently upgraded to a new cadence. No declared demand reserves only
    one emergency receipt, without asserting a critical service guarantee.
    """
    plans = []
    for envelope in envelopes:
        window = _int(envelope["window_seconds"], 1)
        cadence = _int(envelope.get("critical_book_seconds", policy["critical_book_seconds"]), 1)
        opened = _int(envelope.get("open_positions_count", 0))
        expiry = envelope["authority_expires_at"]
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not Decimal(str(expiry)).is_finite():
            raise ValueError("PPI_BUDGET_AUTHORITY_BOUNDS_INVALID")
        demand = sum(_int(v) for v in envelope.get("exit_demand", {}).values())
        if not demand:
            demand = sum(_int(v) for v in envelope.get("priority_reserves", {}).get("EXIT_CRITICAL", {}).values())
        if not demand or expiry <= now:
            continue
        burst = opened or demand
        period = max(cadence, (burst * window + demand - 1) // demand)
        waves = min(RECEIPT_RETENTION_SECONDS // period + 1,
            max(0, ceil((expiry - now) / period)))
        if waves:
            plans.append((burst, period, waves))
    times = {0}
    for _, period, waves in plans:
        times.update(range(0, min(RECEIPT_RETENTION_SECONDS, period * (waves - 1)) + 1, period))
    timestamps = sorted(row["at"] for row in receipts if row["lease"] != protected_lease)
    growth = 1
    for offset in times:
        wanted = max([1, *(burst * min(waves, offset // period + 1) for burst, period, waves in plans)])
        expired = bisect_left(timestamps, now + offset - RECEIPT_RETENTION_SECONDS)
        growth = max(growth, wanted - expired)
    burst = max([0, *(p[0] for p in plans)])
    return {"receipt_limit": RECEIPT_LIMIT, "retention_seconds": RECEIPT_RETENTION_SECONDS,
        "retained_receipts": len(receipts), "available_rows": max(0, RECEIPT_LIMIT - len(receipts)),
        "required_exit_growth": growth, "critical_sweep_receipts": burst,
        "storage_reserve_bytes": growth * RECEIPT_STORAGE_BYTES
            + (burst * (BOOK_CACHE_BYTES + 256) + CRITICAL_STATE_STORAGE_BYTES
               + max(512, policy["maximum_bytes"] // 64) if burst else 0)}


def _receipt_resources(c, policy, envelopes, receipts, now):
    active_row = c.execute("SELECT value,length(CAST(value AS BLOB)) FROM budget_state WHERE key='inflight'").fetchone()
    if active_row and active_row[1] > 65536:
        raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
    active = json.loads(active_row[0]) if active_row else None
    if active is not None and (not isinstance(active, dict) or not isinstance(active.get("lease"), str)):
        raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
    active_lease = active["lease"] if active else None
    # Admission performs exactly this legal deletion before any growth. A
    # readonly activation probe may credit aged rows, but never an inflight
    # token or hypothetical free pages; those still require factual cleanup.
    live = [row for row in receipts if row["at"] >= now - RECEIPT_RETENTION_SECONDS
        or row["lease"] == active_lease]
    result = _receipt_projection(policy, envelopes, live, now, protected_lease=active_lease) | {
        "physically_stored_receipts": len(receipts),
        "legally_expired_receipts": len(receipts) - len(live)}
    page_size = c.execute("PRAGMA page_size").fetchone()[0]
    maximum_pages = (policy["maximum_bytes"] - 608) // (2 * page_size + 8)
    available_pages = max(0, maximum_pages - c.execute("PRAGMA page_count").fetchone()[0]
        + c.execute("PRAGMA freelist_count").fetchone()[0])
    required_pages = (result["storage_reserve_bytes"] + page_size - 1) // page_size
    reasons = (["PPI_EXIT_RECEIPT_RESERVE_BACKPRESSURE"]
        if result["available_rows"] < result["required_exit_growth"] else [])
    if available_pages < required_pages:
        reasons.append("PPI_EXIT_RECEIPT_STORAGE_BACKPRESSURE")
    return result | {"required_pages": required_pages, "available_pages": available_pages,
        "status": "ACTIVATION_BLOCKED_EXIT_CAPACITY" if reasons else "READY",
        "reason_codes": reasons,
        "lower_suspended": result["available_rows"] <= result["required_exit_growth"]
            or available_pages < ceil((result["storage_reserve_bytes"] + RECEIPT_STORAGE_BYTES) / page_size)}


def exit_retention_preflight(database, state, *, opened_count, as_of):
    """Read-only physical activation guard over canonical retained wire debt.

    A missing sidecar is an empty allocation only when the static critical
    retention fits the configured quota. Unknown existing state grants no new
    activation. This never prunes receipts, bootstraps SQLite, sets a clock,
    changes permissions, or converts a journal mode.
    """
    from cg_paper_workspace import artifact_root
    at = stamp(as_of)
    contract = exit_capacity_contract(state, opened_count=opened_count)
    static = contract["receipt_retention"]
    base = {"status": contract["status"], "reason_codes": contract["reason_codes"],
        "receipt_limit": RECEIPT_LIMIT, "retention_seconds": RECEIPT_RETENTION_SECONDS,
        "retained_receipts": None, "required_exit_growth": static["required_exit_receipts"],
        "available_rows": None, "required_pages": ceil(static["required_main_bytes"] / 4096),
        "available_pages": static["available_main_bytes"] // 4096,
        "inspection_mode": "READ_ONLY", "source_status": "UNVERIFIED", "as_of": at.isoformat()}
    if contract["status"] != "READY":
        return base
    try:
        policy = budget_policy(state | {"status": "APPROVED_DYNAMIC"}, opened_count=opened_count)
        path = artifact_root(database) / "ppi-budget/global.sqlite"
        if any(p.is_symlink() for p in [path, *path.parents]):
            raise ValueError("PPI_BUDGET_PATH_ALIAS")
        try:
            metadata = path.stat()
        except FileNotFoundError:
            metadata = None
        if metadata is not None and (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1):
            raise ValueError("PPI_BUDGET_PATH_ALIAS")
        for suffix in ("-journal", "-wal", "-shm", ".exit-round-degraded", ".exit-round.lock", ".wire.lock", ".init.lock"):
            auxiliary = Path(str(path) + suffix)
            try:
                aux = auxiliary.lstat()
            except FileNotFoundError:
                continue
            if (metadata is None or not stat.S_ISREG(aux.st_mode) or aux.st_nlink != 1
                    or suffix in {"-wal", "-shm"}):
                raise ValueError("PPI_BUDGET_PATH_ALIAS")
        if metadata is None:
            return base | {"source_status": "ABSENT", "retained_receipts": 0, "available_rows": RECEIPT_LIMIT}
        with path.open("rb") as stream:
            header = stream.read(32)
        if header[:16] != b"SQLite format 3\x00" or header[18:20] != b"\x01\x01":
            raise ValueError("PPI_BUDGET_JOURNAL_MODE_INVALID")
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.005)) as c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA query_only=ON")
            c.set_progress_handler(lambda: 1, 1000000)
            c.execute("BEGIN")
            if c.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise ValueError("PPI_BUDGET_JOURNAL_MODE_INVALID")
            if {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")} != {
                    "budget_state", "budget_requests", "budget_totals"}:
                raise ValueError("PPI_BUDGET_SCHEMA_MISMATCH")

            def read(key, default=None):
                row = c.execute("SELECT value,length(CAST(value AS BLOB)) FROM budget_state WHERE key=?", (key,)).fetchone()
                if row is None:
                    return default
                if row[1] > 65536:
                    raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
                return json.loads(row[0])

            if read("schema") != SCHEMA or read("last_clock", at.timestamp()) > at.timestamp():
                raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
            saved = read("reservation_envelopes_v1")
            if saved is None:
                previous = read("last_policy")
                if previous:
                    previous = validate_policy(previous)
                    saved = [GlobalPPIBudget._authority(previous,
                        read("reserve_at", read("last_clock", at.timestamp())))]
                elif read("reserves"):
                    raise ValueError("PPI_BUDGET_LEGACY_AUTHORITY_UNKNOWN")
                else:
                    saved = []
            if not isinstance(saved, list) or len(saved) > AUTHORITY_LIMIT:
                raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
            current = GlobalPPIBudget._authority(policy, at.timestamp())
            envelopes = [item for item in saved if item["retain_until"] > at.timestamp()]
            # Each authority constrains the same stream, not distinct wires.
            # Keeping the candidate alongside an old matching envelope is the
            # conservative union and avoids mutating any stored promise.
            envelopes.append(current)
            receipts = list(c.execute("SELECT * FROM budget_requests LIMIT ?", (RECEIPT_LIMIT + 1,)))
            if len(receipts) > RECEIPT_LIMIT:
                raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
            resources = _receipt_resources(c, policy, envelopes, receipts, at.timestamp())
            return base | resources | {"source_status": "OBSERVED"}
    except (OSError, ValueError, TypeError, KeyError, OverflowError, sqlite3.Error):
        return base | {"status": "ACTIVATION_BLOCKED_EXIT_CAPACITY", "source_status": "UNVERIFIED",
            "reason_codes": ["PPI_EXIT_RECEIPT_STORAGE_UNVERIFIED"],
            "retained_receipts": None, "available_rows": None, "available_pages": None}


class GlobalPPIBudget:
    """Atomic admission plus a durable serial lease across worker processes.

    A crashed worker's outstanding lease blocks all sends for at least 60s
    (greater than the guard's 10+30s timeout); restart cannot manufacture spare
    concurrency. Counters/circuits survive changes of recommendation/config.
    """
    def __init__(self, path, policy, *, clock=None, protected=()):
        self.path = Path(path).absolute()
        self.policy = validate_policy(policy)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.deadline_alarm_unavailable = False
        try:
            self.protected = {Path(p).resolve() for p in protected if p is not None}
            self._bootstrap()
        except (OSError, sqlite3.Error) as error:
            # Startup contention is an off-wire denial just like admission.
            # Never expose native IO messages or create an ungoverned sender.
            raise BudgetBackpressure("PPI_BUDGET_STATE_UNAVAILABLE") from error

    def _bootstrap(self):
        self._check_path()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = Path(str(self.path) + ".bootstrap.lock")
        self._check_one(lock_path)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as lock:
            if os.fstat(fd).st_nlink != 1:
                raise ValueError("PPI_BUDGET_PATH_ALIAS")
            deadline = time.monotonic() + .05
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as error:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise BudgetBackpressure("PPI_BUDGET_STATE_UNAVAILABLE") from error
                    time.sleep(min(.005, remaining))
            self._check_path()
            if not self.path.exists():
                db = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                os.close(db)
            with closing(self._connect(timeout=.05)) as c, c:
                # All initialized validation shares one read snapshot. Even a
                # setter to the existing mode can race a worker's commit lock;
                # foreign/WAL state must never be converted by a restart.
                c.execute("BEGIN")
                if c.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                    raise ValueError("PPI_BUDGET_JOURNAL_MODE_INVALID")
                tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                expected_tables = {"budget_state", "budget_requests", "budget_totals"}
                if tables - expected_tables:
                    raise ValueError("PPI_BUDGET_SEPARATE_DATABASE_REQUIRED")
                old_schema = self._get(c, "schema") if "budget_state" in tables else None
                if old_schema is not None and (old_schema != SCHEMA or tables != expected_tables):
                    raise ValueError("PPI_BUDGET_SCHEMA_MISMATCH")
                # Restart validates an existing bootstrap without racing an
                # active worker with redundant DDL or a schema write. Only an
                # unfinished first bootstrap may create the fixed tables.
                if old_schema is None:
                    c.rollback()
                    if c.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                        raise ValueError("PPI_BUDGET_JOURNAL_MODE_INVALID")
                    self._begin_write(c)
                    # Native executescript would commit the transaction before
                    # DDL. Keep the fixed schema and marker atomic, while IF
                    # NOT EXISTS preserves a known interrupted bootstrap.
                    for statement in (
                        "CREATE TABLE IF NOT EXISTS budget_state(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
                        """CREATE TABLE IF NOT EXISTS budget_requests(
                      lease TEXT PRIMARY KEY, at REAL NOT NULL, endpoint TEXT NOT NULL,
                      consumer TEXT NOT NULL, priority TEXT NOT NULL, used INTEGER NOT NULL)""",
                        """CREATE TABLE IF NOT EXISTS budget_totals(
                      endpoint TEXT NOT NULL, consumer TEXT NOT NULL, priority TEXT NOT NULL,
                      requested INTEGER NOT NULL, allowed INTEGER NOT NULL,
                      used INTEGER NOT NULL, dropped INTEGER NOT NULL,
                      PRIMARY KEY(endpoint,consumer,priority))""",
                    ):
                        c.execute(statement)
                    self._put(c, "schema", SCHEMA)
            if self.path.stat().st_mode & 0o777 != 0o600:
                os.chmod(self.path, 0o600)

    def _check_one(self, path):
        if path.resolve() in self.protected:
            raise ValueError("PPI_BUDGET_PATH_ALIAS")
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            # SQLite deletes its transient journal at commit. One validated
            # metadata read avoids exists/stat races; other OS errors remain
            # failures, and a missing protected path was rejected above.
            return None
        if (metadata.st_nlink == 0
                and path == Path(str(self.path) + "-journal")
                and stat.S_ISREG(metadata.st_mode)):
            # A name lookup can retain SQLite's own inode while commit
            # unlinks its DELETE journal. Zero links is never a valid file:
            # confirm an owned, same-device journal, then relookup once.
            parent_metadata = self.path.parent.lstat()
            owner = os.geteuid()
            if (not stat.S_ISDIR(parent_metadata.st_mode)
                    or parent_metadata.st_uid != owner
                    or metadata.st_uid != owner
                    or metadata.st_dev != parent_metadata.st_dev):
                raise ValueError("PPI_BUDGET_PATH_ALIAS")
            if path.resolve() in self.protected:
                raise ValueError("PPI_BUDGET_PATH_ALIAS")
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                return None
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("PPI_BUDGET_PATH_ALIAS")
        return metadata

    def _check_path(self):
        if any(p.is_symlink() for p in [self.path.parent, *self.path.parents]):
            raise ValueError("PPI_BUDGET_DIRECTORY_ALIAS")
        size = 0
        for suffix in ("", "-journal", "-wal", "-shm"):
            metadata = self._check_one(Path(str(self.path) + suffix))
            if suffix in {"", "-journal"} and metadata is not None:
                # Reuse the same alias-checked snapshot for quota accounting.
                # Another process may remove an auxiliary file immediately.
                size += metadata.st_size
        for suffix in (".exit-round-degraded", ".exit-round.lock"):
            metadata = self._check_one(Path(str(self.path) + suffix))
            if metadata is not None:
                size += metadata.st_size
        if size >= self.policy["maximum_bytes"]:
            raise ValueError("PPI_BUDGET_CAPACITY_REACHED")

    def _connect(self, *, timeout=.05):
        self._check_path()
        c = sqlite3.connect(self.path, timeout=timeout)
        c.row_factory = sqlite3.Row
        c.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
        return c

    def _limit_pages(self, c):
        # max_page_count belongs to this connection. Set it only after native
        # write admission, avoiding a competing pre-transaction lock upgrade.
        page_size = c.execute("PRAGMA page_size").fetchone()[0]
        # DELETE journal needs a header and an eight-byte receipt per page.
        # Bound DB+worst-case full journal, rather than half the DB alone.
        pages = (self.policy["maximum_bytes"] - 608) // (2 * page_size + 8)
        c.execute(f"PRAGMA max_page_count={pages}")

    def _begin_write(self, c):
        # A changing SQLite writer may finish during this bounded 50ms wait.
        # Network requests never hold this SQL lock, and uncertainty still
        # fails closed; the durable wire lease remains independent of it.
        c.execute("BEGIN IMMEDIATE")
        self._limit_pages(c)
        self._maintenance(c)

    def _maintenance(self, c):
        """Housekeeping precedes even the durable clock/telemetry writes.

        Telemetry is disposable evidence with an explicit retained horizon.
        Wire receipts, reservations, circuits and leases never make room for
        it. A full older sidecar can DELETE stale telemetry without INSERT.
        """
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='budget_state'").fetchone():
            return
        now = stamp(self.clock()).timestamp()
        if now < self._get(c, "last_clock", now):
            raise BudgetBackpressure("PPI_BUDGET_CLOCK_ROLLBACK")
        # Binding debt is released only after the maximum permitted policy
        # window. Do this before metadata writes, so legal aging can recover a
        # full sidecar without deleting any still-live receipt or lease.
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='budget_requests'").fetchone():
            active = self._get(c, "inflight")
            c.execute("DELETE FROM budget_requests WHERE at<? AND lease!=?",
                (now - RECEIPT_RETENTION_SECONDS, active["lease"] if active else ""))
        c.execute("DELETE FROM budget_state WHERE key LIKE 'window:%' AND CAST(substr(key,8) AS INTEGER)<?", (int(now) - 3600,))
        self._trim_telemetry(c, self._telemetry_bytes())

    def _telemetry_bytes(self):
        # Sparse seconds remain exact while retained. Under storage pressure
        # trim the oldest evidence, not the physical call-debt authority.
        return max(512, self.policy["maximum_bytes"] // 64)

    def _trim_telemetry(self, c, maximum, *, keep=None):
        rows = list(c.execute("SELECT key,length(CAST(value AS BLOB)) AS size FROM budget_state WHERE key LIKE 'window:%' ORDER BY CAST(substr(key,8) AS INTEGER)"))
        size = sum(r["size"] for r in rows)
        removed = 0
        for row in rows:
            if size <= maximum:
                break
            if row["key"] == keep:
                continue
            c.execute("DELETE FROM budget_state WHERE key=?", (row["key"],))
            size -= row["size"]
            removed += 1
        if removed:
            pressure = self._get(c, "telemetry_pressure", {"evicted_buckets": 0, "dropped_updates": 0})
            pressure["evicted_buckets"] += removed
            self._put(c, "telemetry_pressure", pressure)
        return size

    @staticmethod
    def _get(c, key, default=None):
        row = c.execute("SELECT value FROM budget_state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def _put(c, key, value):
        c.execute("INSERT OR REPLACE INTO budget_state VALUES(?,?)", (key, json.dumps(value, separators=(",", ":"))))

    def _clock(self, c):
        now = stamp(self.clock()).timestamp()
        before = self._get(c, "last_clock", now)
        if now < before:
            raise BudgetBackpressure("PPI_BUDGET_CLOCK_ROLLBACK")
        self._put(c, "last_clock", now)
        return now

    def _total(self, c, endpoint, consumer, priority, requested=0, allowed=0, used=0, dropped=0):
        c.execute("""INSERT INTO budget_totals VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(endpoint,consumer,priority) DO UPDATE SET
            requested=requested+excluded.requested,allowed=allowed+excluded.allowed,
            used=used+excluded.used,dropped=dropped+excluded.dropped""",
            (endpoint, consumer, priority, requested, allowed, used, dropped))

    def _window_total(self, c, now, endpoint, consumer, priority, *, reason=None, **counts):
        # One fixed-cardinality aggregate per UTC second. Unlike a log of
        # denials, a burst cannot grow this without bound. The admission ledger
        # below remains the authority for rolling-window reserved capacity.
        bucket = "window:" + str(int(now))
        value = self._get(c, bucket, {})
        key = ":".join((endpoint, consumer, priority))
        row = value.setdefault(key, dict.fromkeys(WINDOW_FIELDS, 0) | {"denial_reason": {}})
        for name, count in counts.items():
            row[name] += count
        if reason:
            row["denial_reason"][reason] = row["denial_reason"].get(reason, 0) + 1
        maximum = self._telemetry_bytes()
        encoded = len(json.dumps(value, separators=(",", ":")).encode())
        if encoded > maximum:
            pressure = self._get(c, "telemetry_pressure", {"evicted_buckets": 0, "dropped_updates": 0})
            pressure.update(last_dropped_at=now, dropped_updates=pressure["dropped_updates"] + 1)
            self._put(c, "telemetry_pressure", pressure)
            return
        previous = c.execute("SELECT length(CAST(value AS BLOB)) FROM budget_state WHERE key=?", (bucket,)).fetchone()
        self._trim_telemetry(c, maximum - encoded + (previous[0] if previous else 0), keep=bucket)
        self._put(c, bucket, value)

    @staticmethod
    def _authority(policy, now):
        binding = {name: policy[name] for name in ("recommendation_digest", "configuration_fingerprint",
            "window_seconds", "endpoint_limits", "global_limit")}
        return dict(binding, key=digest(binding), observed_at=now,
            authority_expires_at=stamp(policy["expires_at"]).timestamp(),
            retain_until=max(stamp(policy["expires_at"]).timestamp(), now + policy["window_seconds"]),
            priority_reserves=deepcopy(policy.get("priority_reserves", {})),
            open_positions_count=policy.get("open_positions_count", 0),
            critical_book_seconds=policy["critical_book_seconds"],
            exit_demand=deepcopy(policy.get("exit_demand", {})))

    def _envelopes(self, c, now, *, record=False):
        # Authorities can coexist in different live workers. An independent
        # process cannot replace an old valid cap, shrink its rolling window,
        # or erase its EXIT promise. Each authority constrains the SAME wire
        # receipts until both its authority and its last observed window end.
        saved = self._get(c, "reservation_envelopes_v1")
        if saved is None:
            saved = []
            legacy = self._get(c, "reserves", {})
            previous = self._get(c, "last_policy")
            if legacy or previous:
                if previous is None:
                    # A reservation without its originating bounds/expiry
                    # cannot be safely reconstructed from a replacement.
                    raise BudgetBackpressure("PPI_BUDGET_LEGACY_AUTHORITY_UNKNOWN")
                previous = validate_policy(previous)
                at = self._get(c, "reserve_at", self._get(c, "last_clock", now))
                inherited = self._authority(previous, at)
                inherited["priority_reserves"] = legacy or inherited["priority_reserves"]
                saved.append(inherited)
        envelopes = {item["key"]: deepcopy(item) for item in saved if item["retain_until"] > now}
        if now < stamp(self.policy["expires_at"]).timestamp():
            current = self._authority(self.policy, now)
            old = envelopes.get(current["key"])
            if old:
                for priority in PRIORITIES:
                    values = current["priority_reserves"].setdefault(priority, {})
                    for endpoint in ENDPOINTS:
                        values[endpoint] = max(values.get(endpoint, 0), old["priority_reserves"].get(priority, {}).get(endpoint, 0))
                current["authority_expires_at"] = max(current["authority_expires_at"], old["authority_expires_at"])
                current["observed_at"] = now if record else old["observed_at"]
                current["retain_until"] = max(old["retain_until"], current["authority_expires_at"],
                    current["observed_at"] + current["window_seconds"])
                current["open_positions_count"] = max(current["open_positions_count"], old["open_positions_count"])
                current["critical_book_seconds"] = min(current["critical_book_seconds"],
                    old.get("critical_book_seconds", current["critical_book_seconds"]))
                current["exit_demand"] = {e: max(current["exit_demand"].get(e, 0), old["exit_demand"].get(e, 0)) for e in ENDPOINTS}
            envelopes[current["key"]] = current
        if len(envelopes) > AUTHORITY_LIMIT:
            raise BudgetBackpressure("PPI_BUDGET_AUTHORITY_OVERLAP_LIMIT")
        result = list(envelopes.values())
        if record:
            self._put(c, "reservation_envelopes_v1", result)
        return result

    def _allocate_reserves(self, promises, *, limits=None, global_limit=None):
        # Oversubscribed plans cannot create capacity. Allocate independently
        # by rank, protecting EXIT book first also under a tighter global cap.
        # An impossible demand is exposed separately, never claimed reserved.
        left = dict(self.policy["endpoint_limits"] if limits is None else limits)
        global_left = self.policy["global_limit"] if global_limit is None else global_limit
        result = {p: dict.fromkeys(ENDPOINTS, 0) for p in PRIORITIES}
        for priority in PRIORITIES:
            for endpoint in ("book", "current", "intraday"):
                units = min(promises.get(priority, {}).get(endpoint, 0), left[endpoint], global_left)
                result[priority][endpoint] = units
                left[endpoint] -= units
                global_left -= units
        return result

    @staticmethod
    def _usage(rows):
        spent = {e: 0 for e in ENDPOINTS}
        used_by = {p: dict.fromkeys(ENDPOINTS, 0) for p in PRIORITIES}
        for row in rows:
            spent[row["endpoint"]] += 1
            used_by[row["priority"]][row["endpoint"]] += 1
        return spent, used_by

    def _borrow(self, endpoint, priority, rows, reserves, used_by, *, limits=None, global_limit=None):
        # Borrowing is always upward. First use unreserved common capacity;
        # only an already higher-ranked claimant may consume a lower floor.
        limits = self.policy["endpoint_limits"] if limits is None else limits
        global_limit = self.policy["global_limit"] if global_limit is None else global_limit
        remaining = self._remaining_reserves(reserves, rows, limits=limits, global_limit=global_limit)
        if remaining[priority][endpoint] > 0:
            return None
        common_endpoint = limits[endpoint] - sum(r["endpoint"] == endpoint for r in rows) - sum(v[endpoint] for v in remaining.values())
        common_global = global_limit - len(rows) - sum(sum(v.values()) for v in remaining.values())
        if common_endpoint > 0 and common_global > 0:
            return "COMMON", endpoint
        for donor in reversed(PRIORITIES[PRIORITIES.index(priority) + 1:]):
            if remaining[donor][endpoint] > 0:
                return donor, endpoint
        # A global-only shortage can borrow a lower-ranked promise on another
        # endpoint; its endpoint capacity itself was not consumed.
        for donor in reversed(PRIORITIES[PRIORITIES.index(priority) + 1:]):
            for donor_endpoint in ENDPOINTS:
                if remaining[donor][donor_endpoint] > 0:
                    return donor, donor_endpoint
        return "COMMON", endpoint

    def _admission(self, endpoint, priority, receipts, envelopes, now):
        donors = []
        for envelope in envelopes:
            rows = [row for row in receipts if row["at"] > now - envelope["window_seconds"]]
            spent, used_by = self._usage(rows)
            limits = envelope["endpoint_limits"]
            cap = envelope["global_limit"]
            reserves = self._allocate_reserves(envelope["priority_reserves"], limits=limits, global_limit=cap)
            remaining = self._remaining_reserves(reserves, rows, limits=limits, global_limit=cap)
            held = {e: sum(remaining[p][e] for p in PRIORITIES[:PRIORITIES.index(priority)]) for e in ENDPOINTS}
            if len(rows) >= cap or spent[endpoint] >= limits[endpoint]:
                return "PPI_BUDGET_EXHAUSTED", None
            if spent[endpoint] + held[endpoint] >= limits[endpoint] or len(rows) + sum(held.values()) >= cap:
                return "PPI_HIGH_PRIORITY_RESERVE_BACKPRESSURE", None
            donors.append(self._borrow(endpoint, priority, rows, reserves, used_by, limits=limits, global_limit=cap))
        # One wire read can discharge the same rank's promises in multiple
        # authorities. Do not invent borrowing when any such own floor exists.
        donor = None if None in donors else next((d for d in donors if d[0] != "COMMON"), ("COMMON", endpoint))
        return None, donor

    def _receipt_admission(self, c, priority, receipts, envelopes, now):
        if priority == "EXIT_CRITICAL":
            return None
        resources = _receipt_resources(c, self.policy, envelopes, receipts, now)
        # The lower claim is additional to all reserved future EXIT writes.
        if resources["available_rows"] <= resources["required_exit_growth"]:
            return "PPI_EXIT_RECEIPT_RESERVE_BACKPRESSURE"
        page_size = c.execute("PRAGMA page_size").fetchone()[0]
        pages = (resources["storage_reserve_bytes"] + RECEIPT_STORAGE_BYTES + page_size - 1) // page_size
        if resources["available_pages"] < pages:
            return "PPI_EXIT_RECEIPT_STORAGE_BACKPRESSURE"
        return None

    def _remaining_reserves(self, reserves, receipts, *, limits, global_limit):
        # Precise remaining promises come from live commitment timestamps and
        # actual residual caps, never from rounded borrowed-out telemetry.
        # Hierarchical reallocation accounts for higher-priority borrowing on
        # another endpoint as well as direct exhaustion of the donor endpoint.
        spent, owned = self._usage(receipts)
        wanted = {p: {e: max(0, reserves[p][e] - owned[p][e]) for e in ENDPOINTS} for p in PRIORITIES}
        return self._allocate_reserves(wanted,
            limits={e: max(0, limits[e] - spent[e]) for e in ENDPOINTS},
            global_limit=max(0, global_limit - len(receipts)))

    def _wire_fd(self):
        path = Path(str(self.path) + ".wire.lock")
        self._check_one(path)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        if os.fstat(fd).st_nlink != 1:
            os.close(fd)
            raise ValueError("PPI_BUDGET_PATH_ALIAS")
        return fd

    def _wire_busy(self):
        path = Path(str(self.path) + ".wire.lock")
        if self._check_one(path) is None:
            return False
        fd = self._wire_fd()
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)

    @contextmanager
    def wire_scope(self, lease):
        # requests' read timeout governs inactivity rather than total body
        # duration. Keep an independent OS lock through the actual full body;
        # a live slow sender cannot lose serial ownership to clock expiry.
        fd = None
        try:
            try:
                fd = self._wire_fd()
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise BudgetBackpressure("PPI_SERIAL_BACKPRESSURE") from error
                with closing(self._connect()) as c:
                    now = stamp(self.clock()).timestamp()
                    active = self._get(c, "inflight")
                    if not active or active["lease"] != lease or active["until"] <= now:
                        raise BudgetBackpressure("PPI_BUDGET_LEASE_INVALID")
            except (OSError, ValueError, sqlite3.Error) as error:
                raise BudgetBackpressure("PPI_BUDGET_STATE_UNAVAILABLE") from error
            # Preserve the provider exception type; only setup failures are
            # budget-state failures. The SQL connection is closed before wire.
            yield
        finally:
            if fd is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def acquire(self, endpoint, *, consumer="UNSCOPED", priority="DISCOVERY"):
        if endpoint not in ENDPOINTS or priority not in PRIORITIES:
            raise BudgetBackpressure("PPI_BUDGET_SCOPE_INVALID")
        # Fixed consumer labels bound totals cardinality and never store ticker,
        # URL, broker payload, credentials, account data or arbitrary messages.
        consumer = consumer if consumer in CONSUMERS else "UNSCOPED"
        try:
            with closing(self._connect()) as c, c:
                self._begin_write(c)
                now = self._clock(c)
                self._total(c, endpoint, consumer, priority, requested=1)
                reason = None
                pressure = self._get(c, "critical_exit_pressure", {})
                if priority != "EXIT_CRITICAL" and (self._round_guard_active() or any(
                        p.get("status") == "DEGRADED" or p.get("until", 0) > now for p in pressure.values())):
                    reason = "PPI_EXIT_DEADLINE_LOWER_SUSPENDED"
                if now >= stamp(self.policy["expires_at"]).timestamp():
                    reason = "PPI_CAPACITY_EXPIRED_BACKPRESSURE"
                circuit = self._get(c, "circuits", {})
                for key in ("global", endpoint):
                    if circuit.get(key, {}).get("until", 0) > now:
                        reason = "PPI_GLOBAL_CIRCUIT_OPEN" if key == "global" else "PPI_ENDPOINT_CIRCUIT_OPEN"
                        break
                lease = self._get(c, "inflight")
                wire_busy = self._wire_busy()
                if wire_busy or (lease and lease["until"] > now):
                    reason = reason or "PPI_SERIAL_BACKPRESSURE"
                elif lease:
                    self._put(c, "inflight", None)
                    self._put(c, "abandoned_lease_observed", True)
                # Rolling windows prevent a boundary burst. A new policy may
                # widen the window; retain up to one hour of bounded receipts.
                if c.execute("SELECT COUNT(*) FROM budget_requests").fetchone()[0] >= RECEIPT_LIMIT:
                    reason = reason or "PPI_BUDGET_ROW_LIMIT"
                rows = list(c.execute("SELECT * FROM budget_requests"))
                envelopes = self._envelopes(c, now, record=True)
                constrained, donor = self._admission(endpoint, priority, rows, envelopes, now)
                reason = reason or constrained
                reason = reason or self._receipt_admission(c, priority, rows, envelopes, now)
                if reason:
                    self._total(c, endpoint, consumer, priority, dropped=1)
                    self._window_total(c, now, endpoint, consumer, priority, requested=1, dropped=1, reason=reason)
                    self._put(c, "last_backpressure", {"reason": reason, "at": now, "endpoint": endpoint})
                    return {"allowed": False, "reason": reason, "lease": None}
                token = uuid.uuid4().hex
                c.execute("INSERT INTO budget_requests VALUES(?,?,?,?,?,0)", (token, now, endpoint, consumer, priority))
                self._put(c, "inflight", {"lease": token, "until": now + self.policy["lease_seconds"],
                    "lease_seconds": self.policy["lease_seconds"]})
                self._put(c, "policy_fingerprint", digest(self.policy))
                self._put(c, "last_policy", self.policy)
                self._total(c, endpoint, consumer, priority, allowed=1)
                self._window_total(c, now, endpoint, consumer, priority, requested=1, admitted=1, borrowed_in=int(donor is not None))
                if donor is not None and donor[0] != "COMMON":
                    # Attribution is explicitly to the reserve owner rather
                    # than inventing a donor HTTP consumer.
                    self._window_total(c, now, donor[1], "UNSCOPED", donor[0], borrowed_out=1)
                return {"allowed": True, "lease": token, "reason": "PPI_BUDGET_ALLOWED"}
        except (OSError, ValueError, sqlite3.Error) as error:
            raise BudgetBackpressure("PPI_BUDGET_STATE_UNAVAILABLE") from error

    def start(self, lease):
        try:
            with closing(self._connect()) as c, c:
                self._begin_write(c)
                now = self._clock(c)
                row = c.execute("SELECT * FROM budget_requests WHERE lease=?", (lease,)).fetchone()
                active = self._get(c, "inflight")
                if not row or not active or active["lease"] != lease or active["until"] <= now or row["used"]:
                    raise BudgetBackpressure("PPI_BUDGET_LEASE_INVALID")
                if now >= stamp(self.policy["expires_at"]).timestamp():
                    raise BudgetBackpressure("PPI_CAPACITY_EXPIRED_BACKPRESSURE")
                pressure = self._get(c, "critical_exit_pressure", {})
                if row["priority"] != "EXIT_CRITICAL" and (self._round_guard_active() or any(
                        p.get("status") == "DEGRADED" or p.get("until", 0) > now for p in pressure.values())):
                    raise BudgetBackpressure("PPI_EXIT_DEADLINE_LOWER_SUSPENDED")
                circuits = self._get(c, "circuits", {})
                if any(circuits.get(k, {}).get("until", 0) > now for k in ("global", row["endpoint"])):
                    raise BudgetBackpressure("PPI_GLOBAL_CIRCUIT_OPEN")
                receipts = list(c.execute("SELECT * FROM budget_requests WHERE lease!=?", (lease,)))
                envelopes = self._envelopes(c, now, record=True)
                reason, _ = self._admission(row["endpoint"], row["priority"], receipts, envelopes, now)
                reason = reason or self._receipt_admission(c, row["priority"], receipts, envelopes, now)
                if reason:
                    raise BudgetBackpressure(reason)
                # Admission can be paused. Both rolling wire debt and crashed
                # sender coverage must originate at actual start, atomically.
                active["until"] = max(active["until"], now + max(active.get("lease_seconds", 0), self.policy["lease_seconds"]))
                self._put(c, "inflight", active)
                c.execute("UPDATE budget_requests SET used=1,at=? WHERE lease=?", (now, lease))
                self._total(c, row["endpoint"], row["consumer"], row["priority"], used=1)
                self._window_total(c, now, row["endpoint"], row["consumer"], row["priority"], used=1)
        except (OSError, ValueError, sqlite3.Error) as error:
            raise BudgetBackpressure("PPI_BUDGET_STATE_UNAVAILABLE") from error

    def _outcome(self, c, endpoint, now, code):
        if not code or code == "PPI_INSTRUMENT_NOT_FOUND":
            return
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", code):
            code = "PPI_READ_ERROR"
        if code in {"PPI_HTTP_429", "PPI_SESSION_INVALID", "PPI_HTTP_401", "PPI_HTTP_403"}:
            duration = self.policy["session_breaker_seconds"] if "SESSION" in code or code in {"PPI_HTTP_401", "PPI_HTTP_403"} else self.policy["breaker_seconds"]
            circuit = self._get(c, "circuits", {})
            for key in (endpoint, "global"):
                circuit[key] = {"until": max(now + duration, circuit.get(key, {}).get("until", 0)), "code": code}
            self._put(c, "circuits", circuit)
        elif code == "PPI_HTTP_408" or code.startswith("PPI_HTTP_5"):
            failures = self._get(c, "server_failures", {})
            for key in (endpoint, "global"):
                last = failures.get(key, {})
                count = last.get("count", 0) + 1 if now - last.get("at", now) <= self.policy["breaker_seconds"] else 1
                failures[key] = {"count": count, "at": now}
                if count >= self.policy["server_error_threshold"]:
                    circuit = self._get(c, "circuits", {})
                    circuit[key] = {"until": now + self.policy["breaker_seconds"], "code": "PPI_REPEATED_408_5XX"}
                    self._put(c, "circuits", circuit)
            self._put(c, "server_failures", failures)
        self._put(c, "last_error", {"endpoint": endpoint, "code": code, "at": now})

    def finish(self, lease, *, status_code=None, error_code=None):
        try:
            with closing(self._connect()) as c, c:
                self._begin_write(c)
                now = self._clock(c)
                row = c.execute("SELECT * FROM budget_requests WHERE lease=?", (lease,)).fetchone()
                if not row:
                    raise BudgetBackpressure("PPI_BUDGET_LEASE_INVALID")
                self._outcome(c, row["endpoint"], now, error_code or (f"PPI_HTTP_{status_code}" if status_code and status_code >= 400 else None))
                if not row["used"]:
                    # Only confirmed pre-wire rejection can release a claim.
                    # An uncertain finish rolls back and retains both the
                    # conservative inflight lease and any emitted wire debt.
                    c.execute("DELETE FROM budget_requests WHERE lease=?", (lease,))
                active = self._get(c, "inflight")
                if active and active["lease"] == lease:
                    self._put(c, "inflight", None)
        except (OSError, ValueError, sqlite3.Error) as error:
            raise BudgetBackpressure("PPI_BUDGET_STATE_UNAVAILABLE") from error

    def report_error(self, endpoint, code):
        with closing(self._connect()) as c, c:
            self._begin_write(c)
            self._outcome(c, endpoint, self._clock(c), code)

    @staticmethod
    def _safe_book(payload, now, age):
        """Cache only bounded public depth, with native source/receipt clocks."""
        try:
            if not isinstance(payload, dict) or not 0 <= now - stamp(payload["date"]).timestamp() <= age:
                return None
            depth = {}
            for target, alternatives in (("bids", ("bids",)), ("offers", ("offers", "asks"))):
                levels = next((payload[k] for k in alternatives if k in payload), None)
                if not isinstance(levels, list) or not 1 <= len(levels) <= 20:
                    return None
                safe = []
                for item in levels:
                    price, quantity = Decimal(str(item["price"])), Decimal(str(item["quantity"]))
                    if not price.is_finite() or not quantity.is_finite() or price <= 0 or quantity < 0:
                        return None
                    safe.append({"price": str(price), "quantity": str(quantity)})
                depth[target] = safe
            if Decimal(depth["bids"][0]["price"]) > Decimal(depth["offers"][0]["price"]):
                return None
            result = {"date": payload["date"], **depth}
            return result if len(json.dumps(result).encode()) <= BOOK_CACHE_BYTES else None
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return None

    def _book_flight_pending(self, key, lease, authority, age, *, critical, last_observed_clock):
        """Observe a committed wait without competing with its finishing writer.

        This snapshot grants no cache, service, or send authority. A changed
        flight/cache/pressure returns to the existing authoritative write,
        which rechecks the clock, policy, circuits and retained envelopes.
        """
        with closing(self._connect()) as c:
            c.execute("PRAGMA query_only=ON")
            c.execute("BEGIN")
            # A deferred reader acquires its snapshot on the first SELECT.
            # Sample time after that boundary: a finishing writer may advance
            # the committed floor between BEGIN and the snapshot acquisition.
            committed_clock = self._get(c, "last_clock", None)
            now = stamp(self.clock()).timestamp()
            if now < last_observed_clock or (committed_clock is not None and now < committed_clock):
                raise BudgetBackpressure("PPI_BUDGET_CLOCK_ROLLBACK")
            if now >= stamp(self.policy["expires_at"]).timestamp():
                raise BudgetBackpressure("PPI_CAPACITY_EXPIRED_BACKPRESSURE")
            circuits = self._get(c, "circuits", {})
            if any(circuits.get(k, {}).get("until", 0) > now for k in ("global", "book")):
                raise BudgetBackpressure("PPI_GLOBAL_CIRCUIT_OPEN")
            cached = self._get(c, "critical_books", {}).get(key)
            if (cached and cached["authority"] == authority
                    and 0 <= now - cached["received_at"] <= age
                    and self._safe_book(cached["book"], now, age) is not None):
                return False, now
            active = self._get(c, "critical_book_flights", {}).get(key)
            if not active or active["lease"] != lease or active["until"] <= now:
                return False, now
            if critical:
                pressure = self._get(c, "critical_exit_pressure", {}).get(key)
                if not pressure or pressure.get("until", 0) <= now:
                    return False, now
            return True, now

    def coalesced_book(self, identity, fetch, *, consumer, priority):
        """Off-wire, exact-identity single-flight for critical opened books.

        Only successful fresh native depth is shared, for at most one exit
        cadence (5s). Provider calls still pass acquire/start/finish in the
        existing HTTP guard. SQLite is never locked during fetch or polling.
        EXIT followers wait up to their critical cadence. A slow existing
        wire call is joined, never preempted or duplicated. Waiting/degraded
        EXIT pressure suspends subsequent lower-priority admissions.
        """
        if priority not in {"EXIT_CRITICAL", "OPENED_CRITICAL"}:
            return fetch()
        if (not isinstance(identity, (tuple, list)) or len(identity) != 5
                or any(not isinstance(x, str) or not x or len(x) > 256 for x in identity)):
            raise BudgetBackpressure("PPI_BUDGET_EXACT_IDENTITY_REQUIRED")
        consumer = consumer if consumer in CONSUMERS else "UNSCOPED"
        key = digest(list(identity))
        authority = digest({k: self.policy[k] for k in ("configuration_fingerprint", "recommendation_digest")})
        age = min(5, self.policy["critical_book_seconds"])
        token = uuid.uuid4().hex
        critical = priority == "EXIT_CRITICAL"
        started = time.monotonic()
        deadline = started + (self.policy["critical_book_seconds"] if critical else .05)
        fetch_failure = None
        try:
            # The valid authority was observed even when its ensuing read
            # meets a breaker or occupied flight. Commit the promise before
            # those terminal rejections can roll back the cache transaction.
            with closing(self._connect()) as c, c:
                self._begin_write(c)
                now = self._clock(c)
                if now >= stamp(self.policy["expires_at"]).timestamp():
                    raise BudgetBackpressure("PPI_CAPACITY_EXPIRED_BACKPRESSURE")
                self._envelopes(c, now, record=True)
            while True:
                with closing(self._connect()) as c, c:
                    self._begin_write(c)
                    now = self._clock(c)
                    if now >= stamp(self.policy["expires_at"]).timestamp():
                        raise BudgetBackpressure("PPI_CAPACITY_EXPIRED_BACKPRESSURE")
                    circuits = self._get(c, "circuits", {})
                    if any(circuits.get(k, {}).get("until", 0) > now for k in ("global", "book")):
                        raise BudgetBackpressure("PPI_GLOBAL_CIRCUIT_OPEN")
                    # A fresh cache hit still observes current opened demand.
                    # Publish its EXIT promise before another process can use
                    # the remaining wire floor for newly opened identities.
                    self._envelopes(c, now, record=True)
                    cache = self._get(c, "critical_books", {})
                    flights = self._get(c, "critical_book_flights", {})
                    cache = {k: v for k, v in cache.items() if 0 <= now - v["received_at"] <= age and v["authority"] == authority}
                    cached = cache.get(key)
                    if cached and self._safe_book(cached["book"], now, age) is not None:
                        elapsed = time.monotonic() - started
                        if critical and elapsed >= self.policy["critical_book_seconds"]:
                            # Commit the observed authority before recording
                            # degradation in its own transaction. Raising
                            # inside this transaction would erase the alarm.
                            c.commit()
                            self._exit_degraded(key, "PPI_BOOK_EXIT_DEADLINE_EXCEEDED", elapsed)
                            raise BudgetBackpressure("PPI_BOOK_EXIT_DEADLINE_EXCEEDED")
                        self._window_total(c, now, "book", consumer, priority, coalesced=1)
                        if critical:
                            self._exit_served(c, key, now, elapsed)
                        return deepcopy(cached["book"])
                    cache.pop(key, None)
                    flights = {k: v for k, v in flights.items() if v["until"] > now}
                    active = flights.get(key)
                    if active is None:
                        if len(flights) >= BOOK_CACHE_LIMIT:
                            raise BudgetBackpressure("PPI_BOOK_SINGLE_FLIGHT_CAPACITY")
                        flights[key] = {"lease": token, "until": now + self.policy["lease_seconds"],
                            "owner_priority": priority, "started_at": now}
                        self._put(c, "critical_books", cache)
                        self._put(c, "critical_book_flights", flights)
                        if critical:
                            self._exit_waiting(c, key, now, active)
                        break
                    if critical:
                        self._exit_waiting(c, key, now, active)
                # The authority and waiting EXIT pressure above are committed.
                # Rewriting them every poll needlessly competes with the owner
                # and round observer. Poll only this same live flight; cache
                # service or new leadership still requires the write above.
                last_observed_clock = now
                while True:
                    if time.monotonic() >= deadline:
                        if critical:
                            self._exit_degraded(key, "PPI_BOOK_EXIT_DEADLINE_EXCEEDED", time.monotonic() - started)
                            raise BudgetBackpressure("PPI_BOOK_EXIT_DEADLINE_EXCEEDED")
                        raise BudgetBackpressure("PPI_BOOK_SINGLE_FLIGHT_BACKPRESSURE")
                    time.sleep(min(.005, max(0, deadline - time.monotonic())))
                    pending, last_observed_clock = self._book_flight_pending(
                        key, active["lease"], authority, age, critical=critical,
                        last_observed_clock=last_observed_clock)
                    if not pending:
                        break
            monitor = None
            if critical:
                monitor = threading.Timer(max(0, deadline - time.monotonic()),
                    self._monitor_exit_deadline, args=(key, token, started, deadline))
                monitor.daemon = True
                monitor.start()
            try:
                result = fetch()
            except BaseException as error:
                # A hard process kill leaves a bounded durable flight; restart
                # cannot manufacture concurrency or silently use an old book.
                self._complete_book(key, token, authority, age, None)
                # requests transport failures inherit OSError. Preserve the
                # fetch's original type only after cleanup is verified, so
                # native transient retry is not mistaken for SQLite failure.
                fetch_failure = error
                raise
            finally:
                if monitor is not None:
                    monitor.cancel()
            self._complete_book(key, token, authority, age, result)
            if critical:
                elapsed = time.monotonic() - started
                if elapsed >= self.policy["critical_book_seconds"]:
                    self._exit_degraded(key, "PPI_BOOK_EXIT_DEADLINE_EXCEEDED", elapsed)
                    raise BudgetBackpressure("PPI_BOOK_EXIT_DEADLINE_EXCEEDED")
                with closing(self._connect()) as c, c:
                    self._begin_write(c)
                    now = self._clock(c)
                    if self._safe_book(result, now, age) is not None:
                        self._exit_served(c, key, now, elapsed)
            return result
        except (OSError, ValueError, sqlite3.Error) as error:
            if error is fetch_failure:
                raise
            raise BudgetBackpressure("PPI_BUDGET_STATE_UNAVAILABLE") from error

    def _exit_waiting(self, c, key, now, owner):
        pressure = self._get(c, "critical_exit_pressure", {})
        old = pressure.get(key, {})
        if key != EXIT_ROUND_PRESSURE_KEY and key not in pressure and len(
                set(pressure) - {EXIT_ROUND_PRESSURE_KEY}) >= BOOK_CACHE_LIMIT:
            # Never erase an unresolved identity to admit new lower activity.
            raise BudgetBackpressure("PPI_BOOK_EXIT_PRESSURE_CAPACITY")
        pressure[key] = old | {"status": old.get("status", "WAITING"),
            "first_wait_at": old.get("first_wait_at", now),
            "until": max(old.get("until", 0), now + self.policy["lease_seconds"]),
            "owner_priority": (owner or {}).get("owner_priority", "EXIT_CRITICAL"),
            "deadline_seconds": self.policy["critical_book_seconds"]}
        self._put(c, "critical_exit_pressure", pressure)

    def _exit_served(self, c, key, now, elapsed):
        self.deadline_alarm_unavailable = False
        pressure = self._get(c, "critical_exit_pressure", {})
        pressure.pop(key, None)
        self._put(c, "critical_exit_pressure", pressure)
        service = self._get(c, "critical_exit_service", {})
        service[key] = {"last_served_at": now, "elapsed_seconds": elapsed,
            "deadline_seconds": self.policy["critical_book_seconds"], "status": "SERVED"}
        while len(service) > BOOK_CACHE_LIMIT:
            del service[min(service, key=lambda item: service[item]["last_served_at"])]
        self._put(c, "critical_exit_service", service)

    def _exit_degraded(self, key, reason, elapsed):
        with closing(self._connect()) as c, c:
            self._begin_write(c)
            now = self._clock(c)
            self._mark_exit_degraded(c, key, now, reason, elapsed)

    def _mark_exit_degraded(self, c, key, now, reason, elapsed):
        self._exit_waiting(c, key, now, None)
        pressure = self._get(c, "critical_exit_pressure", {})
        pressure[key].update(status="DEGRADED", reason=reason, elapsed_seconds=elapsed, degraded_at=now)
        self._put(c, "critical_exit_pressure", pressure)

    def _round_guard_active(self):
        return self._check_one(Path(str(self.path) + ".exit-round-degraded")) is not None

    def _sync_directory(self):
        directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _round_guard(self, active):
        # SQLite BUSY must not turn an unrecorded failed round into permission
        # for another process. This fixed-size private marker is only a lower
        # suspension barrier; it grants no send authority and holds no lease.
        marker = Path(str(self.path) + ".exit-round-degraded")
        self._check_one(marker)
        if active:
            fd = os.open(marker, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            try:
                if os.fstat(fd).st_nlink != 1:
                    raise ValueError("PPI_BUDGET_PATH_ALIAS")
                os.write(fd, b"PPI_EXIT_ROUND_UNVERIFIED\n")
                os.fsync(fd)
            finally:
                os.close(fd)
        else:
            marker.unlink(missing_ok=True)
        self._sync_directory()

    def observe_exit_round(self, *, elapsed_seconds, deadline_seconds, failures=0,
                           required_identity_digests=None, frozen_admission_scope=None):
        """Publish round debt; only a verified complete fresh round releases it.

        This producer API never edits requests, leases, circuits or capacity.
        Unavailable state returns a fixed DEGRADED diagnosis so EXIT keeps
        running, while a durable sidecar marker also blocks LOWER after BUSY.
        """
        unavailable = {"status": "DEGRADED", "lower_suspended": True,
            "reason": "PPI_EXIT_ROUND_STATE_UNAVAILABLE"}
        lock_fd = None
        lock_held = False
        try:
            self._check_path()
            lock_path = Path(str(self.path) + ".exit-round.lock")
            self._check_one(lock_path)
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            if os.fstat(lock_fd).st_nlink != 1:
                raise ValueError("PPI_BUDGET_PATH_ALIAS")
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_held = True
            self._round_guard(True)
            for value in (elapsed_seconds, deadline_seconds):
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not Decimal(str(value)).is_finite() or value < 0):
                    return unavailable | {"reason": "PPI_EXIT_ROUND_MEASUREMENT_INVALID"}
            if deadline_seconds != self.policy["critical_book_seconds"] or deadline_seconds <= 0:
                return unavailable | {"reason": "PPI_EXIT_ROUND_MEASUREMENT_INVALID"}
            if isinstance(failures, bool) or not isinstance(failures, int) or failures < 0:
                return unavailable | {"reason": "PPI_EXIT_ROUND_MEASUREMENT_INVALID"}
            if required_identity_digests is not None and (not isinstance(required_identity_digests, set)
                    or len(required_identity_digests) > BOOK_CACHE_LIMIT
                    or any(not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key)
                        for key in required_identity_digests)):
                return unavailable | {"reason": "PPI_EXIT_ROUND_MEASUREMENT_INVALID"}
            # This projection records a previously verified admission. It is
            # never substituted for the current identity set in the service
            # or debt-release decision below.
            try:
                frozen = _frozen_admission_summary(frozen_admission_scope)
            except (ValueError, TypeError, InvalidOperation):
                return unavailable | {"reason": "PPI_EXIT_ROUND_MEASUREMENT_INVALID"}
            with closing(self._connect()) as c, c:
                self._begin_write(c)
                now = self._clock(c)
                if frozen is not None and stamp(frozen["captured_at"]).timestamp() > now:
                    raise ValueError("PPI_EXIT_ROUND_MEASUREMENT_INVALID")
                service = self._get(c, "critical_exit_service", {})
                covered = {key for key, record in service.items() if record.get("status") == "SERVED"
                    and 0 <= now - record.get("last_served_at", 0) <= deadline_seconds}
                pressure = self._get(c, "critical_exit_pressure", {})
                ongoing = required_identity_digests is not None and any(
                    key in required_identity_digests and (value.get("status") == "DEGRADED" or value.get("until", 0) > now)
                    for key, value in pressure.items())
                reason = ("PPI_EXIT_ROUND_DEADLINE_EXCEEDED" if elapsed_seconds > deadline_seconds else
                    "PPI_EXIT_ROUND_READ_FAILURES" if failures else
                    "PPI_EXIT_ROUND_INCOMPLETE_OR_UNVERIFIED" if required_identity_digests is None
                        or not required_identity_digests <= covered else
                    "PPI_EXIT_ROUND_CRITICAL_IN_PROGRESS" if ongoing else None)
                if reason:
                    self._mark_exit_degraded(c, EXIT_ROUND_PRESSURE_KEY, now, reason, elapsed_seconds)
                else:
                    # Retire ghosts only with the entire verified current
                    # scope covered. Never release HTTP ownership or receipts.
                    pressure = {key: value for key, value in pressure.items()
                        if key != EXIT_ROUND_PRESSURE_KEY and key in required_identity_digests}
                    self._put(c, "critical_exit_pressure", pressure)
                round_state = {"status": "DEGRADED" if reason else "COMPLETE", "recorded_at": now,
                    "elapsed_seconds": elapsed_seconds, "deadline_seconds": deadline_seconds,
                    "failures": failures, "required_identities_count": len(required_identity_digests)
                        if required_identity_digests is not None else frozen["identity_count"] if frozen else None,
                    "fresh_required_count": len(required_identity_digests) if required_identity_digests is not None else None,
                    "required_scope_verified_current": required_identity_digests is not None,
                    "required_scope_basis": "CURRENT_VERIFIED_PAPER_LEDGER" if required_identity_digests is not None
                        else "FROZEN_AT_FIRST_ADMISSION" if frozen else "UNVERIFIED",
                    "frozen_admission_scope": frozen,
                    "covered_identities_count": len(covered & required_identity_digests)
                        if required_identity_digests is not None else 0,
                    "reason": reason}
                self._put(c, "critical_exit_round", round_state)
            if not reason:
                self._round_guard(False)
            suspended = bool(reason) or any(p.get("status") == "DEGRADED" or p.get("until", 0) > now
                for p in pressure.values())
            return round_state | {"lower_suspended": suspended}
        except (BudgetBackpressure, OSError, ValueError, TypeError, InvalidOperation, sqlite3.Error):
            if lock_held:
                # In particular, failed directory sync after clearing must
                # not leave an acknowledged DEGRADED round without a barrier.
                try:
                    self._round_guard(True)
                except (OSError, ValueError):
                    pass
            return unavailable
        finally:
            if lock_fd is not None:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)

    def _monitor_exit_deadline(self, key, token, started, deadline):
        """Alarm during an existing HTTP body, without manipulating its owner."""
        try:
            if time.monotonic() < deadline:
                return
            with closing(self._connect()) as c, c:
                self._begin_write(c)
                now = self._clock(c)
                flight = self._get(c, "critical_book_flights", {}).get(key)
                # A timer racing successful completion must not resurrect a
                # pressure record for a flight that no longer owns the token.
                if flight and flight["lease"] == token:
                    self._mark_exit_degraded(c, key, now, "PPI_BOOK_EXIT_DEADLINE_EXCEEDED", time.monotonic() - started)
        except (OSError, ValueError, sqlite3.Error, BudgetBackpressure):
            # IO uncertainty does not release the wire lock/lease or permit a
            # duplicate request. Expose failure to the current worker health.
            self.deadline_alarm_unavailable = True

    def _complete_book(self, key, token, authority, age, payload):
        with closing(self._connect()) as c, c:
            self._begin_write(c)
            now = self._clock(c)
            flights = self._get(c, "critical_book_flights", {})
            flight = flights.get(key)
            if not flight or flight["lease"] != token or flight["until"] <= now:
                raise BudgetBackpressure("PPI_BOOK_SINGLE_FLIGHT_LEASE_INVALID")
            self._envelopes(c, now, record=True)
            flights.pop(key)
            self._put(c, "critical_book_flights", flights)
            cache = self._get(c, "critical_books", {})
            cache.pop(key, None)
            safe = self._safe_book(payload, now, age)
            if safe is not None:
                cache[key] = {"authority": authority, "received_at": now, "book": safe}
                while len(cache) > BOOK_CACHE_LIMIT:
                    del cache[min(cache, key=lambda k: cache[k]["received_at"])]
            self._put(c, "critical_books", cache)

    def _window_scopes(self, c, now, seconds):
        scopes = {}
        for bucket in c.execute("SELECT value FROM budget_state WHERE key LIKE 'window:%' AND CAST(substr(key,8) AS INTEGER)>=?", (int(now - seconds),)):
            for key, item in json.loads(bucket[0]).items():
                row = scopes.setdefault(key, dict.fromkeys(WINDOW_FIELDS, 0) | {"denial_reason": {}})
                for name in WINDOW_FIELDS:
                    row[name] += item[name]
                for reason, count in item["denial_reason"].items():
                    row["denial_reason"][reason] = row["denial_reason"].get(reason, 0) + count
        return scopes

    def metrics(self):
        with closing(self._connect()) as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM budget_totals ORDER BY endpoint,consumer,priority")]
            totals = {name: sum(r[name] for r in rows) for name in ("requested", "allowed", "used", "dropped")}
            now = stamp(self.clock()).timestamp()
            last_clock = self._get(c, "last_clock", now)
            if now < last_clock:
                raise BudgetBackpressure("PPI_BUDGET_CLOCK_ROLLBACK")
            envelopes = self._envelopes(c, now)
            seconds = max([self.policy["window_seconds"], *(e["window_seconds"] for e in envelopes)])
            requests = list(c.execute("SELECT * FROM budget_requests WHERE at>?", (now - seconds,)))
            promises = {p: {e: max([0, *(item["priority_reserves"].get(p, {}).get(e, 0) for item in envelopes)]) for e in ENDPOINTS} for p in PRIORITIES}
            limits = {e: min([self.policy["endpoint_limits"][e], *(item["endpoint_limits"][e] for item in envelopes)]) for e in ENDPOINTS}
            cap = min([self.policy["global_limit"], *(item["global_limit"] for item in envelopes)])
            reserves = self._allocate_reserves(promises, limits=limits, global_limit=cap)
            scopes = self._window_scopes(c, now, seconds)
            opened = max([self.policy.get("open_positions_count", 0), *(item["open_positions_count"] for item in envelopes)])
            exit_demand = {e: max([self.policy.get("exit_demand", self.policy.get("priority_reserves", {}).get("EXIT_CRITICAL", {})).get(e, 0),
                *(item.get("exit_demand", {}).get(e, item["priority_reserves"].get("EXIT_CRITICAL", {}).get(e, 0)) for item in envelopes)]) for e in ENDPOINTS}
            receipt_retention = _receipt_resources(c, self.policy, envelopes,
                list(c.execute("SELECT * FROM budget_requests")), now)
            exact_envelopes = []
            exact_remaining = []
            free_limits = dict(limits)
            free_global = cap
            for envelope in envelopes:
                receipts = [r for r in requests if r["at"] > now - envelope["window_seconds"]]
                spent, _ = self._usage(receipts)
                allocated = self._allocate_reserves(envelope["priority_reserves"], limits=envelope["endpoint_limits"], global_limit=envelope["global_limit"])
                remaining = self._remaining_reserves(allocated, receipts, limits=envelope["endpoint_limits"], global_limit=envelope["global_limit"])
                exact_remaining.append(remaining)
                free_limits = {e: min(free_limits[e], max(0, envelope["endpoint_limits"][e] - spent[e])) for e in ENDPOINTS}
                free_global = min(free_global, max(0, envelope["global_limit"] - len(receipts)))
                exact_envelopes.append({name: envelope[name] for name in ("key", "recommendation_digest", "configuration_fingerprint", "window_seconds", "endpoint_limits", "global_limit", "authority_expires_at", "retain_until")} | {
                    "start_at": datetime.fromtimestamp(now - envelope["window_seconds"], timezone.utc).isoformat(),
                    "end_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                    "admitted_in_window": len(receipts), "used_in_window": sum(r["used"] for r in receipts),
                    "priority_reserves": allocated,
                    "reserved_remaining": remaining})
            remaining = self._allocate_reserves({p: {e: min(reserves[p][e], max([0,
                *(item[p][e] for item in exact_remaining)])) for e in ENDPOINTS} for p in PRIORITIES},
                limits=free_limits, global_limit=free_global)
            for priority in PRIORITIES:
                if any(reserves[priority].values()):
                    consumer = "EXIT_READER" if priority == "EXIT_CRITICAL" else "SCALPING" if priority == "SCALPING_HOT" else "SCANNER"
                    for endpoint in ENDPOINTS:
                        scopes.setdefault(":".join((endpoint, consumer, priority)), dict.fromkeys(WINDOW_FIELDS, 0) | {"denial_reason": {}})
            by_window_scope = []
            for key, row in sorted(scopes.items()):
                endpoint, consumer, priority = key.split(":")
                by_window_scope.append(row | {"endpoint": endpoint, "consumer": consumer, "priority": priority,
                    "reserved_total": reserves[priority][endpoint],
                    "reserved_remaining": remaining[priority][endpoint],
                    "open_positions_count": opened, "exit_demand": exit_demand[endpoint]})
            window_endpoint = {}
            for endpoint in ENDPOINTS:
                source = [r for r in by_window_scope if r["endpoint"] == endpoint]
                window_endpoint[endpoint] = {name: sum(r[name] for r in source) for name in WINDOW_FIELDS}
                window_endpoint[endpoint].update(reserved_total=sum(v[endpoint] for v in reserves.values()),
                    reserved_remaining=sum(remaining[p][endpoint] for p in PRIORITIES),
                    open_positions_count=opened, exit_demand=exit_demand[endpoint],
                    denial_reason={reason: sum(r["denial_reason"].get(reason, 0) for r in source) for reason in {reason for r in source for reason in r["denial_reason"]}})
            return {"schema": SCHEMA, "global": totals, "by_scope": rows,
                "window": {"start_at": datetime.fromtimestamp(now - seconds, timezone.utc).isoformat(),
                    "end_at": datetime.fromtimestamp(now, timezone.utc).isoformat(), "counter_resolution_seconds": 1,
                    "policy_window_seconds": self.policy["window_seconds"], "effective_window_seconds": seconds,
                    "active_envelopes": exact_envelopes,
                    "reservation_summary": "CONSERVATIVE_UNION; admission checks every exact active envelope",
                    "by_endpoint": window_endpoint, "by_scope": by_window_scope,
                    "reserve_hierarchy": list(PRIORITIES), "lower_priority_exit_borrowing": "FORBIDDEN",
                    "exit_unreserved_demand": {e: max(0, exit_demand[e] - reserves["EXIT_CRITICAL"][e]) for e in ENDPOINTS}},
                "policy_authority": self.policy.get("authority", "EXPLICIT_APPROVED_OPEN_CAPACITY"),
                "by_endpoint": {e: {name: sum(r[name] for r in rows if r["endpoint"] == e) for name in totals} for e in ENDPOINTS},
                "circuits": self._get(c, "circuits", {}), "last_backpressure": self._get(c, "last_backpressure"),
                "exit_service": {"deadline_seconds": self.policy["critical_book_seconds"],
                    "deadline_alarm_unavailable": self.deadline_alarm_unavailable,
                    "round": self._get(c, "critical_exit_round"),
                    "round_guard_active": self._round_guard_active(),
                    "by_identity_digest": self._get(c, "critical_exit_service", {}),
                    "pressure_by_identity_digest": self._get(c, "critical_exit_pressure", {}),
                    "lower_suspended": receipt_retention["lower_suspended"] or self._round_guard_active() or any(p.get("status") == "DEGRADED" or p.get("until", 0) > now
                        for p in self._get(c, "critical_exit_pressure", {}).values())},
                "telemetry_retention": self._telemetry_retention(c, now, seconds),
                "receipt_retention": receipt_retention,
                "max_parallel_requests": 1, "real_orders_sent": 0, "real_routes": "NOT_CALLED"}

    def _telemetry_retention(self, c, now, seconds):
        earliest = c.execute("SELECT MIN(CAST(substr(key,8) AS INTEGER)) FROM budget_state WHERE key LIKE 'window:%'").fetchone()[0]
        pressure = self._get(c, "telemetry_pressure", {})
        partial = bool(pressure.get("dropped_updates") or pressure.get("evicted_buckets") and (
            earliest is None or earliest > int(now - seconds)))
        return {"status": "BOUNDED_PARTIAL" if partial else "COMPLETE_RETAINED_WINDOW",
            "maximum_telemetry_bytes": self._telemetry_bytes(), "earliest_retained_at": earliest,
            "counter_resolution_seconds": 1, "window_complete": not partial,
            "authority": "NON_BINDING; exact rolling wire debt is budget_requests", **pressure}


def _native_integer(env, name, default):
    try:
        return int(env.get(name, default))
    except (TypeError, ValueError):
        return int(default)


def baseline_budget_policy(previous, *, opened_count, as_of, environ=None):
    """Known factual fallback, without continuing an expired OPEN claim.

    The original scanners retain 20/40 defaults and their original cadence.
    Their finite burst envelope also reserves the existing five-second exit
    reader demand. Retained counters/circuits/leases cannot be cleared by a
    policy change. This never enables new dynamic requests or a faster worker.
    """
    env = os.environ if environ is None else environ
    opened = _int(opened_count)
    equity = max(3, min(60, _native_integer(env, "PAPER_ACTIVE_SYMBOL_LIMIT", 20)))
    scalping = max(8, min(40, _native_integer(env, "PAPER_INTRADAY_BATCH_LIMIT", 40)))
    window = min(3600, max(15, _native_integer(env, "PAPER_OBSERVER_INTERVAL_SECONDS", 60)),
        max(60, _native_integer(env, "PAPER_INTRADAY_SCAN_SECONDS", 180)))
    cadence = _int(previous["critical_book_seconds"], 1)
    critical = {"current": 0, "intraday": 0, "book": (opened * window + cadence - 1) // cadence}
    limits = {"current": max(equity, opened), "intraday": max(scalping, opened),
        "book": max(equity, opened) + critical["book"]}
    inputs = {"equity_limit": equity, "scalping_limit": scalping,
        "window_seconds": window, "opened": opened, "critical": critical}
    return dict(previous, version="rc6-global-ppi-safe-factual-baseline-v1",
        authority="SAFE_FACTUAL_BASELINE; OPEN_CAPACITY_NO_VERIFICADO",
        window_seconds=window, endpoint_limits=limits, global_limit=sum(limits.values()),
        configuration_fingerprint=digest(inputs), priority_reserves={"EXIT_CRITICAL": critical},
        open_positions_count=opened, exit_demand=critical,
        expires_at=(stamp(as_of) + timedelta(seconds=window + 60)).isoformat(),
        safety_reserve="BASELINE_AUTHORITY_ONLY; dynamic expansion disabled")


class RuntimePPIBudget:
    """Revalidates approval at actual sends, with local SHADOW demand reserves."""
    def __init__(self, database, controller, *, clock=None):
        from cg_paper_workspace import artifact_root
        self.database = Path(database)
        self.path = artifact_root(database) / "ppi-budget/global.sqlite"
        self.controller = controller
        self.budget = None
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.activation_contract = None
        self._admission_scopes = {}
        self._scope_lock = threading.Lock()
        self._exit_round_scope = None
        self._exit_round_reads = 0

    def _opened(self, unverified, *, deadline=None):
        return supervisable_position_count(self.database, unverified, deadline=deadline)

    def _previous(self):
        if not self.path.exists():
            return None
        if self.path.is_symlink() or self.path.stat().st_nlink != 1:
            raise BudgetBackpressure("PPI_BUDGET_PATH_ALIAS")
        try:
            with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.005)) as c:
                c.execute("PRAGMA query_only=ON")
                row = c.execute("SELECT value FROM budget_state WHERE key='last_policy' AND length(value)<=65536").fetchone()
                return validate_policy(json.loads(row[0])) if row else None
        except (ValueError, sqlite3.Error) as error:
            raise BudgetBackpressure("PPI_BUDGET_STATE_UNAVAILABLE") from error

    def _blocked_activation(self, state, reason, priority):
        """An unsuccessful activation cannot turn into unleased lower sends.

        A previously committed budget may still service EXIT under its exact
        caps, cadence, expiry, receipts and circuits. A cold reader has no
        verified floor to reuse and must remain blocked off wire.
        """
        self.activation_contract = deepcopy(state.get("exit_capacity") or {
            "schema": "RC6_EXIT_CAPACITY_CONTRACT_V1",
            "status": "ACTIVATION_BLOCKED_EXIT_CAPACITY",
            "open_positions_count": None, "exit_demand": None,
            "reason_codes": [reason], "deadline_seconds": None,
            "real_orders_sent": 0,
        })
        self.activation_contract["status"] = "ACTIVATION_BLOCKED_EXIT_CAPACITY"
        retention = state.get("exit_receipt_capacity")
        if retention:
            self.activation_contract["receipt_retention_runtime"] = deepcopy(retention)
        self.activation_contract["reason_codes"] = list(dict.fromkeys([
            *self.activation_contract.get("reason_codes", []), reason,
            *((retention or {}).get("reason_codes", []))]))
        if priority != "EXIT_CRITICAL":
            raise BudgetBackpressure(reason)
        if self.budget is None:
            saved = self._previous()
            if saved is None:
                raise BudgetBackpressure(reason)
            self.budget = GlobalPPIBudget(self.path, saved, clock=self.clock,
                protected=[self.database, *self.controller.input_paths])
        return self.budget

    def _current(self, *, priority="DISCOVERY", deadline=None):
        state = self.controller.state(self.clock(), **({"deadline": deadline} if deadline is not None else {}))
        if state["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY":
            retention_reasons = state.get("exit_receipt_capacity", {}).get("reason_codes", [])
            return self._blocked_activation(state,
                retention_reasons[0] if retention_reasons else "PPI_EXIT_CAPACITY_INSUFFICIENT", priority)
        if "CAPACITY_OPENED_LEDGER_UNVERIFIED" in state.get("reason_codes", ()):
            return self._blocked_activation(state, "CAPACITY_OPENED_LEDGER_UNVERIFIED", priority)
        if state["status"] != "APPROVED_DYNAMIC":
            if self.budget is None:
                saved = self._previous()
                if saved is None:
                    return None
                self.budget = GlobalPPIBudget(self.path, saved, clock=self.clock,
                    protected=[self.database, *self.controller.input_paths])
            previous = self.budget.policy
            opened = self._opened(max(20, _native_integer(self.controller.environ, "PAPER_MAX_OPEN_POSITIONS", 5)),
                deadline=deadline)
            self.budget.policy = validate_policy(baseline_budget_policy(previous,
                opened_count=opened, as_of=self.clock(), environ=self.controller.environ))
            return self.budget
        # Recheck the ledger at the send seam too. Controllers without a
        # database are useful pure policy readers, but grant no wire authority
        # to a runtime whose durable PAPER position count is unknown.
        durable_opened = self._opened(None, deadline=deadline)
        if durable_opened is None:
            return self._blocked_activation(state, "CAPACITY_OPENED_LEDGER_UNVERIFIED", priority)
        opened = 0
        planned = {}
        try:
            report = self.controller.shadow_report()
            if (report.get("capacity_policy", {}).get("configuration_fingerprint") == state["configuration_fingerprint"]
                    and 0 <= (stamp(self.clock()) - stamp(report["as_of"])).total_seconds() <= state["global_budget"]["window_seconds"]):
                keys = set()
                for engine, plan in report.get("engines", {}).items():
                    keys.update(tuple(k) for k in plan.get("opened_priority", []))
                    rows = {tuple(r["identity"]): r for r in plan.get("telemetry", [])}
                    for k in map(tuple, plan.get("selected", [])):
                        if k in keys:
                            continue
                        row = rows.get(k, {})
                        priority = "SCALPING_HOT" if row.get("state") == "HOT" and engine == "SCALPING" else "STRATEGY_HOT" if row.get("state") == "HOT" else "WARM" if row.get("state") == "WARM" else "DISCOVERY"
                        demand = planned.setdefault(priority, {})
                        for endpoint in state["engine_profiles"][engine]["profile"]["endpoints"]:
                            demand[endpoint] = demand.get(endpoint, 0) + 1
                opened = len(keys)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        # Reserve positions from the existing durable ledger too; unavailable
        # shadow state cannot allow discovery to steal opened books.
        opened = max(opened, durable_opened)
        self.activation_contract = exit_capacity_contract(state, opened_count=opened)
        if self.activation_contract["status"] != "READY":
            return self._blocked_activation(state | {"exit_capacity": self.activation_contract},
                "PPI_EXIT_CAPACITY_INSUFFICIENT", priority)
        retention = exit_retention_preflight(self.database, state, opened_count=opened, as_of=self.clock())
        self.activation_contract["receipt_retention_runtime"] = retention
        if retention["status"] != "READY":
            self.activation_contract["status"] = "ACTIVATION_BLOCKED_EXIT_CAPACITY"
            self.activation_contract["reason_codes"] += retention["reason_codes"]
            return self._blocked_activation(state | {"exit_capacity": self.activation_contract},
                retention["reason_codes"][0], priority)
        policy = budget_policy(state, opened_count=opened, planned_reservations=planned)
        if self.budget is None:
            protected = [self.database, *self.controller.input_paths]
            self.budget = GlobalPPIBudget(self.path, policy, clock=self.clock, protected=protected)
        else:
            self.budget.policy = validate_policy(policy)
        return self.budget

    def acquire(self, endpoint, *, consumer="UNSCOPED", priority="DISCOVERY"):
        budget = self._current(priority=priority)
        if budget is None:
            return {"allowed": True, "lease": None}
        admitted_policy = deepcopy(budget.policy)
        result = budget.acquire(endpoint, consumer=consumer, priority=priority)
        if result["allowed"]:
            with self._scope_lock:
                self._admission_scopes[result["lease"]] = {
                    "priority": priority, "expires_at": admitted_policy["expires_at"],
                    "approved_dynamic": not admitted_policy.get("authority", "").startswith("SAFE_FACTUAL_BASELINE"),
                }
                # Only one durable lease can be live. Older abandoned scopes
                # cannot regain authority through a subsequent token.
                while len(self._admission_scopes) > BOOK_CACHE_LIMIT:
                    del self._admission_scopes[next(iter(self._admission_scopes))]
        return result

    def discard_exit_round_scope(self):
        """Forget admission evidence between producer rounds; no I/O or lease edit."""
        with self._scope_lock:
            self._exit_round_scope = None

    @contextmanager
    def _exit_round_read(self):
        # One temporal set (at most64 digests) belongs to the first admission.
        # An overlapping reader cannot inherit it as its own round evidence.
        with self._scope_lock:
            if self._exit_round_scope is None:
                self._exit_round_scope = {"owner": threading.get_ident(), "attempted": False,
                    "invalidated": bool(self._exit_round_reads), "frozen": None}
            scope = self._exit_round_scope
            if self._exit_round_reads or scope["owner"] != threading.get_ident():
                scope["invalidated"], scope["frozen"] = True, None
            self._exit_round_reads += 1
        try:
            yield scope
        except BaseException:
            with self._scope_lock:
                scope["invalidated"], scope["frozen"] = True, None
            raise
        finally:
            with self._scope_lock:
                self._exit_round_reads -= 1

    def _freeze_exit_admission(self, scope, budget):
        with self._scope_lock:
            if scope is not self._exit_round_scope or scope["attempted"] or scope["invalidated"]:
                return
            scope["attempted"] = True
            if (budget is None or budget.policy.get("authority", "").startswith("SAFE_FACTUAL_BASELINE")
                    or (self.activation_contract or {}).get("status") != "READY"):
                return
            policy = deepcopy(budget.policy)
            if stamp(self.clock()) >= stamp(policy["expires_at"]):
                return
            identities = _supervisable_identity_digests(self.database)
            captured_at = stamp(self.clock())
            # The policy count can include SHADOW demand. Only the native
            # five-key set proves the identity count; reserve must cover it.
            if (identities is None or len(identities) > policy["open_positions_count"]
                    or captured_at >= stamp(policy["expires_at"])):
                return
            scope["frozen"] = {"identities": frozenset(identities), "summary": {
                "basis": "FROZEN_AT_FIRST_ADMISSION", "identity_count": len(identities),
                "identity_scope_digest": digest(sorted(identities)), "captured_at": captured_at.isoformat(),
                "configuration_fingerprint": policy["configuration_fingerprint"],
                "recommendation_digest": policy["recommendation_digest"], "expires_at": policy["expires_at"],
                "reserved_open_positions_count": policy["open_positions_count"],
                "reserved_exit_demand": deepcopy(policy["exit_demand"])}}

    def coalesced_book(self, identity, fetch, *, consumer, priority):
        exit_reader = consumer == "EXIT_READER" and priority == "EXIT_CRITICAL"
        with self._exit_round_read() if exit_reader else nullcontext(None) as scope:
            budget = self._current(priority=priority)
            if exit_reader:
                self._freeze_exit_admission(scope, budget)
            # Invalid/missing/expired approval uses the original factual path.
            # It never continues a dynamic cache or changes baseline cadence.
            if budget is None or budget.policy.get("authority", "").startswith("SAFE_FACTUAL_BASELINE"):
                return fetch()
            return budget.coalesced_book(identity, fetch, consumer=consumer, priority=priority)

    def start(self, lease):
        if lease:
            with self._scope_lock:
                scope = self._admission_scopes.get(lease)
            if scope is None:
                raise BudgetBackpressure("PPI_BUDGET_LEASE_SCOPE_UNVERIFIED")
            if stamp(self.clock()) >= stamp(scope["expires_at"]):
                raise BudgetBackpressure("PPI_CAPACITY_EXPIRED_BACKPRESSURE")
            priority = scope["priority"]
            state = self.controller.state(self.clock())
            if scope["approved_dynamic"] and state["status"] != "APPROVED_DYNAMIC":
                if state["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY":
                    budget = self._blocked_activation(state, "PPI_EXIT_CAPACITY_INSUFFICIENT", priority)
                elif "CAPACITY_OPENED_LEDGER_UNVERIFIED" in state.get("reason_codes", ()):
                    budget = self._blocked_activation(state, "CAPACITY_OPENED_LEDGER_UNVERIFIED", priority)
                elif priority == "EXIT_CRITICAL":
                    # EXIT retains the original live authority. It cannot
                    # renew an expired approval or raise its committed caps.
                    budget = self.budget
                else:
                    raise BudgetBackpressure("PPI_CAPACITY_REVALIDATION_BACKPRESSURE")
            else:
                budget = self._current(priority=priority)
            if budget is None:
                raise BudgetBackpressure("PPI_CAPACITY_REVALIDATION_BACKPRESSURE")
            budget.start(lease)

    def wire_scope(self, lease):
        return self.budget.wire_scope(lease) if lease and self.budget else nullcontext()

    def finish(self, lease, **outcome):
        if lease:
            self.budget.finish(lease, **outcome)
            with self._scope_lock:
                self._admission_scopes.pop(lease, None)

    def report_error(self, endpoint, code):
        if self.budget:
            self.budget.report_error(endpoint, code)

    def observe_exit_round(self, *, elapsed_seconds, deadline_seconds, failures=0):
        """Explicit producer write, distinct from runtime_budget_snapshot."""
        entered = time.monotonic()
        # Serialize the temporal admission evidence with this observation.
        # Existing HTTP bodies remain owned by their original wire leases.
        with self._scope_lock:
            scope = self._exit_round_scope
            frozen = deepcopy(scope["frozen"]["summary"]) if (scope and scope["frozen"]
                and not scope["invalidated"] and scope["owner"] == threading.get_ident()
                and not self._exit_round_reads) else None
            concurrent = bool(self._exit_round_reads) or bool(scope and scope["invalidated"])
            try:
                return self._observe_exit_round(elapsed_seconds=elapsed_seconds,
                    deadline_seconds=deadline_seconds, failures=failures, entered=entered,
                    frozen=frozen, concurrent=concurrent)
            finally:
                self._exit_round_scope = None

    def _observe_exit_round(self, *, elapsed_seconds, deadline_seconds, failures, entered, frozen, concurrent):
        try:
            measured = all(not isinstance(value, bool) and isinstance(value, (int, float))
                and isfinite(value) and value >= 0 for value in (elapsed_seconds, deadline_seconds))
        except OverflowError:
            measured = False
        # This is the producer's remaining round budget, not another cadence
        # starting after its books. All ledger captures inherit one absolute.
        deadline = entered + max(0, deadline_seconds - elapsed_seconds) if measured else entered
        try:
            budget = self._current(priority="EXIT_CRITICAL", deadline=deadline)
            if budget is None:
                return {"status": "BASELINE_NOT_MEASURED", "lower_suspended": None}
            identities = _supervisable_identity_digests(self.database, deadline=deadline)
            state = self.controller.state(self.clock(), deadline=deadline)
            if (concurrent or state.get("status") != "APPROVED_DYNAMIC"
                    or (self.activation_contract or {}).get("status") != "READY"):
                identities = None
            observed_elapsed = elapsed_seconds + (time.monotonic() - entered) if measured else elapsed_seconds
            return budget.observe_exit_round(elapsed_seconds=observed_elapsed,
                deadline_seconds=deadline_seconds, failures=failures,
                required_identity_digests=identities, frozen_admission_scope=frozen)
        except (BudgetBackpressure, OSError, ValueError, TypeError, sqlite3.Error):
            return {"status": "DEGRADED", "lower_suspended": True,
                "reason": "PPI_EXIT_ROUND_STATE_UNAVAILABLE"}


def budget_from_environment(database=None, *, clock=None):
    from rc6_dynamic_universe.promotion import capacity_controller_from_environment
    controller = capacity_controller_from_environment(database)
    state = controller.state(clock() if clock else None)
    if database is None:
        from cg_paper_workspace import database_path
        try:
            database = database_path()
        except ValueError:
            if state.get("mode") != "APPROVED":
                return None
            raise
        controller.database = Path(database)
    if state.get("mode") != "APPROVED" and controller.environ.get("POROTA_DYNAMIC_CAPACITY_MODE", "").upper() != "APPROVED":
        from cg_paper_workspace import artifact_root
        if not (artifact_root(database) / "ppi-budget/global.sqlite").exists():
            return None
    return RuntimePPIBudget(database, controller, clock=clock)


def runtime_budget_snapshot(database, *, as_of=None):
    """Inspect committed budget evidence without constructing a writer.

    No bootstrap, clock update, maintenance, permission change or directory
    creation occurs. Missing/foreign/contested evidence stays distinguishable
    from a healthy budget; this is an observation, not OPEN authorization.
    """
    from cg_paper_workspace import artifact_root
    at = stamp(as_of or datetime.now(timezone.utc))
    result = {"schema": "RC6_RUNTIME_BUDGET_SNAPSHOT_V1", "as_of": at.isoformat(),
        "status": "ABSENT", "reason_codes": ["PPI_BUDGET_NOT_OBSERVED"],
        "inspection_mode": "READ_ONLY", "source_last_clock": None, "age_seconds": None,
        "global": None, "exit_service": None, "telemetry_retention": None, "receipt_retention": None}
    try:
        path = artifact_root(database) / "ppi-budget/global.sqlite"
        if any(p.is_symlink() for p in [path, *path.parents]):
            raise ValueError("PPI_BUDGET_PATH_ALIAS")
        try:
            metadata = path.stat()
        except FileNotFoundError:
            metadata = None
        if metadata is not None and (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1):
            raise ValueError("PPI_BUDGET_PATH_ALIAS")
        round_guard_active = False
        for suffix in ("-journal", "-wal", "-shm", ".exit-round-degraded", ".exit-round.lock", ".wire.lock", ".init.lock"):
            auxiliary = Path(str(path) + suffix)
            try:
                aux_metadata = auxiliary.lstat()
            except FileNotFoundError:
                continue
            if metadata is None or not stat.S_ISREG(aux_metadata.st_mode) or aux_metadata.st_nlink != 1:
                raise ValueError("PPI_BUDGET_PATH_ALIAS")
            if suffix in {"-wal", "-shm"}:
                raise ValueError("PPI_BUDGET_JOURNAL_MODE_INVALID")
            if suffix == ".exit-round-degraded":
                round_guard_active = True
        if metadata is None:
            return result
        # Refuse a WAL main before SQLite can create a missing SHM companion.
        with path.open("rb") as stream:
            header = stream.read(32)
        if header[:16] != b"SQLite format 3\x00" or header[18:20] != b"\x01\x01":
            raise ValueError("PPI_BUDGET_JOURNAL_MODE_INVALID")
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.005)) as c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA query_only=ON")
            c.set_progress_handler(lambda: 1, 1000000)
            c.execute("BEGIN")
            if c.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise ValueError("PPI_BUDGET_JOURNAL_MODE_INVALID")
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables != {"budget_state", "budget_requests", "budget_totals"}:
                raise ValueError("PPI_BUDGET_SCHEMA_MISMATCH")

            def read(key, default=None):
                row = c.execute("SELECT value,length(CAST(value AS BLOB)) FROM budget_state WHERE key=?", (key,)).fetchone()
                if row is None:
                    return default
                if row[1] > 65536:
                    raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
                return json.loads(row[0])

            if read("schema") != SCHEMA:
                raise ValueError("PPI_BUDGET_SCHEMA_MISMATCH")
            last_clock = read("last_clock")
            if last_clock is not None and (isinstance(last_clock, bool)
                    or not isinstance(last_clock, (int, float)) or not Decimal(str(last_clock)).is_finite()):
                raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
            if last_clock is not None and last_clock > at.timestamp():
                raise ValueError("PPI_BUDGET_CLOCK_AHEAD_OF_INSPECTION")
            policy = read("last_policy", {})
            pressure = read("critical_exit_pressure", {})
            service = read("critical_exit_service", {})
            round_record = read("critical_exit_round")
            if (not isinstance(policy, dict) or not isinstance(pressure, dict) or not isinstance(service, dict)
                    or len(set(pressure) - {EXIT_ROUND_PRESSURE_KEY}) > BOOK_CACHE_LIMIT or len(service) > BOOK_CACHE_LIMIT):
                raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
            receipt_retention = None
            if policy:
                policy = validate_policy(policy)
                envelopes = read("reservation_envelopes_v1", [])
                if not isinstance(envelopes, list) or len(envelopes) > AUTHORITY_LIMIT:
                    raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
                envelopes = [item for item in envelopes if item["retain_until"] > at.timestamp()]
                receipts = list(c.execute("SELECT * FROM budget_requests LIMIT ?", (RECEIPT_LIMIT + 1,)))
                if len(receipts) > RECEIPT_LIMIT:
                    raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
                receipt_retention = _receipt_resources(c, policy, envelopes, receipts, at.timestamp())
            # Emit a fixed whitelist, never arbitrary fields from durable JSON.
            def project(records, fields):
                output = {}
                for key, record in records.items():
                    if not re.fullmatch(r"[0-9a-f]{64}", key) or not isinstance(record, dict):
                        raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
                    for field in fields:
                        if field not in record:
                            continue
                        value = record[field]
                        if field == "status":
                            if value not in {"WAITING", "DEGRADED", "SERVED"}:
                                raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
                        elif field == "owner_priority":
                            if value not in PRIORITIES:
                                raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
                        elif field == "reason":
                            if not isinstance(value, str) or not re.fullmatch(r"PPI_[A-Z0-9_]{1,95}", value):
                                raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
                        elif (isinstance(value, bool) or not isinstance(value, (int, float))
                                or not Decimal(str(value)).is_finite() or value < 0):
                            raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
                        elif field in {"first_wait_at", "last_served_at", "degraded_at"} and value > at.timestamp():
                            raise ValueError("PPI_BUDGET_CLOCK_AHEAD_OF_INSPECTION")
                    output[key] = {field: record[field] for field in fields if field in record}
                return output

            pressure = project(pressure, ("status", "first_wait_at", "until", "owner_priority",
                "deadline_seconds", "reason", "elapsed_seconds", "degraded_at"))
            service = project(service, ("status", "last_served_at", "elapsed_seconds", "deadline_seconds"))
            round_summary = None
            if round_record is not None:
                if not isinstance(round_record, dict) or round_record.get("status") not in {"COMPLETE", "DEGRADED"}:
                    raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
                round_summary = {"status": round_record["status"]}
                for field in ("recorded_at", "elapsed_seconds", "deadline_seconds", "failures",
                              "required_identities_count", "covered_identities_count"):
                    value = round_record.get(field)
                    if field == "required_identities_count" and value is None:
                        round_summary[field] = None
                        continue
                    if (isinstance(value, bool) or not isinstance(value, (int, float))
                            or not Decimal(str(value)).is_finite() or value < 0):
                        raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
                    if field == "recorded_at" and value > at.timestamp():
                        raise ValueError("PPI_BUDGET_CLOCK_AHEAD_OF_INSPECTION")
                    round_summary[field] = value
                reason = round_record.get("reason")
                if reason is not None and (not isinstance(reason, str) or not re.fullmatch(r"PPI_[A-Z0-9_]{1,95}", reason)):
                    raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
                round_summary["reason"] = reason
                # Old committed rounds have no admission projection. New
                # evidence distinguishes a historical count from fresh scope.
                if "required_scope_verified_current" in round_record:
                    verified = round_record["required_scope_verified_current"]
                    fresh = round_record.get("fresh_required_count")
                    basis = round_record.get("required_scope_basis")
                    frozen = _frozen_admission_summary(round_record.get("frozen_admission_scope"))
                    if (not isinstance(verified, bool)
                            or basis not in {"CURRENT_VERIFIED_PAPER_LEDGER", "FROZEN_AT_FIRST_ADMISSION", "UNVERIFIED"}
                            or (fresh is not None and (isinstance(fresh, bool) or not isinstance(fresh, int)
                                or not 0 <= fresh <= BOOK_CACHE_LIMIT))
                            or verified != (fresh is not None)
                            or (verified and (basis != "CURRENT_VERIFIED_PAPER_LEDGER"
                                or round_summary["required_identities_count"] != fresh))
                            or (not verified and basis == "CURRENT_VERIFIED_PAPER_LEDGER")
                            or (basis == "FROZEN_AT_FIRST_ADMISSION" and (frozen is None
                                or round_summary["required_identities_count"] != frozen["identity_count"]))
                            or (basis == "UNVERIFIED" and round_summary["required_identities_count"] is not None)
                            or (frozen is not None and stamp(frozen["captured_at"]).timestamp() > round_summary["recorded_at"])):
                        raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
                    round_summary.update(required_scope_verified_current=verified, fresh_required_count=fresh,
                        required_scope_basis=basis, frozen_admission_scope=frozen)
            suspended = round_guard_active or any(p.get("status") == "DEGRADED" or p.get("until", 0) > at.timestamp() for p in pressure.values())
            degraded = round_guard_active or any(p.get("status") == "DEGRADED" for p in pressure.values())
            totals = dict.fromkeys(("requested", "allowed", "used", "dropped"), 0)
            for row in c.execute("SELECT requested,allowed,used,dropped FROM budget_totals"):
                for key in totals:
                    totals[key] += _int(row[key])
            retained = read("telemetry_pressure", {})
            if not isinstance(retained, dict):
                raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
            for key in ("evicted_buckets", "dropped_updates"):
                _int(retained.get(key, 0))
            earliest = c.execute("SELECT MIN(CAST(substr(key,8) AS INTEGER)) FROM budget_state WHERE key LIKE 'window:%'").fetchone()[0]
            seconds = policy.get("window_seconds")
            if seconds is not None:
                _int(seconds, 1)
            partial = bool(retained.get("dropped_updates") or retained.get("evicted_buckets") and (
                earliest is None or seconds is None or earliest > int(at.timestamp() - seconds)))
            receipt_blocked = bool(receipt_retention and receipt_retention["status"] != "READY")
            return result | {"status": "DEGRADED" if degraded or receipt_blocked else "OBSERVED",
                "reason_codes": (["PPI_EXIT_DEADLINE_LOWER_SUSPENDED"] if degraded else [])
                    + (receipt_retention["reason_codes"] if receipt_blocked else []),
                "source_last_clock": last_clock,
                "age_seconds": at.timestamp() - last_clock if last_clock is not None else None,
                "global": totals,
                "receipt_retention": receipt_retention,
                "exit_service": {"deadline_seconds": policy.get("critical_book_seconds"),
                    "round": round_summary, "round_guard_active": round_guard_active,
                    "lower_suspended": suspended or bool(receipt_retention and receipt_retention["lower_suspended"]), "by_identity_digest": service,
                    "pressure_by_identity_digest": pressure},
                "telemetry_retention": {"status": "BOUNDED_PARTIAL" if partial else "COMPLETE_RETAINED_WINDOW",
                    "window_complete": not partial, "earliest_retained_at": earliest,
                    "counter_resolution_seconds": 1,
                    **{key: retained[key] for key in ("evicted_buckets", "dropped_updates", "last_dropped_at") if key in retained}},
                "authority": "READ_ONLY_OBSERVATION; OPEN_CAPACITY_NOT_CERTIFIED"}
    except (OSError, ValueError, TypeError, KeyError, OverflowError, InvalidOperation, sqlite3.Error):
        return result | {"status": "UNVERIFIED", "reason_codes": ["PPI_BUDGET_OBSERVATION_UNAVAILABLE"]}
