"""Versioned cross-process wire budget for approved PPI market reads.

Only current/book/intraday are measured. One private sidecar database arbitrates
all engines and actual retries. No trading DB, broker or provider is imported.
"""
from __future__ import annotations

from contextlib import closing, contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from copy import deepcopy
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
SUPERVISABLE_POSITION_STATES = (("paper_positions", "OPEN"), ("paper_future_positions", "ACTIVE"))


def supervisable_position_count(database, unverified=None):
    """Count every exit-supervised family in one verified PAPER snapshot.

    None means unknown, never zero. Readers and capacity approval can consume
    the same table/state contract without importing a broker or provider.
    """
    try:
        with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True, timeout=.005)) as c:
            c.execute("PRAGMA query_only=ON")
            c.set_progress_handler(lambda: 1, 100000)
            c.execute("BEGIN")
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


def _int(value, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("PPI_BUDGET_INTEGER_INVALID")
    return value


def exit_capacity_contract(state, *, opened_count=0):
    """Fail activation before a truncated reserve can promise impossible exits."""
    opened = _int(opened_count)
    envelope, settings = state["global_budget"], state["budget_settings"]
    window, cadence = _int(envelope["window_seconds"], 1), _int(settings["critical_book_seconds"], 1)
    demand = {"current": 0, "intraday": 0, "book": (opened * window + cadence - 1) // cadence}
    gaps = {e: max(0, demand[e] - _int(envelope["endpoint_limits"][e], 1)) for e in ENDPOINTS}
    global_gap = max(0, sum(demand.values()) - _int(envelope["global_limit"], 1))
    blocked = bool(global_gap or any(gaps.values()))
    return {"schema": "RC6_EXIT_CAPACITY_CONTRACT_V1", "open_positions_count": opened,
        "exit_demand": demand, "endpoint_gaps": gaps, "global_gap": global_gap,
        "status": "ACTIVATION_BLOCKED_EXIT_CAPACITY" if blocked else "READY",
        "reason_codes": ["PPI_EXIT_CAPACITY_INSUFFICIENT"] if blocked else [],
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
    for endpoint, value in policy.get("exit_demand", {}).items():
        if endpoint not in ENDPOINTS:
            raise ValueError("PPI_BUDGET_EXIT_DEMAND_INVALID")
        _int(value)
    demand = {e: policy.get("exit_demand", {}).get(e, 0) for e in ENDPOINTS}
    if any(demand[e] > policy["endpoint_limits"][e] for e in ENDPOINTS) or sum(demand.values()) > policy["global_limit"]:
        raise ValueError("PPI_EXIT_CAPACITY_INSUFFICIENT")
    return policy


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
        pages = (self.policy["maximum_bytes"] - 512) // (2 * page_size + 8)
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
                if priority != "EXIT_CRITICAL" and any(
                        p.get("status") == "DEGRADED" or p.get("until", 0) > now for p in pressure.values()):
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
                c.execute("DELETE FROM budget_requests WHERE at<?", (now - 3600,))
                if c.execute("SELECT COUNT(*) FROM budget_requests").fetchone()[0] >= 20000:
                    reason = reason or "PPI_BUDGET_ROW_LIMIT"
                rows = list(c.execute("SELECT * FROM budget_requests WHERE at>?", (now - 3600,)))
                envelopes = self._envelopes(c, now, record=True)
                constrained, donor = self._admission(endpoint, priority, rows, envelopes, now)
                reason = reason or constrained
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
                circuits = self._get(c, "circuits", {})
                if any(circuits.get(k, {}).get("until", 0) > now for k in ("global", row["endpoint"])):
                    raise BudgetBackpressure("PPI_GLOBAL_CIRCUIT_OPEN")
                receipts = list(c.execute("SELECT * FROM budget_requests WHERE lease!=? AND at>?", (lease, now - 3600)))
                reason, _ = self._admission(row["endpoint"], row["priority"], receipts, self._envelopes(c, now, record=True), now)
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
                if time.monotonic() >= deadline:
                    if critical:
                        self._exit_degraded(key, "PPI_BOOK_EXIT_DEADLINE_EXCEEDED", time.monotonic() - started)
                        raise BudgetBackpressure("PPI_BOOK_EXIT_DEADLINE_EXCEEDED")
                    raise BudgetBackpressure("PPI_BOOK_SINGLE_FLIGHT_BACKPRESSURE")
                time.sleep(min(.005, max(0, deadline - time.monotonic())))
            monitor = None
            if critical:
                monitor = threading.Timer(max(0, deadline - time.monotonic()),
                    self._monitor_exit_deadline, args=(key, token, started, deadline))
                monitor.daemon = True
                monitor.start()
            try:
                result = fetch()
            except BaseException:
                # A hard process kill leaves a bounded durable flight; restart
                # cannot manufacture concurrency or silently use an old book.
                self._complete_book(key, token, authority, age, None)
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
            raise BudgetBackpressure("PPI_BUDGET_STATE_UNAVAILABLE") from error

    def _exit_waiting(self, c, key, now, owner):
        pressure = self._get(c, "critical_exit_pressure", {})
        old = pressure.get(key, {})
        if key not in pressure and len(pressure) >= BOOK_CACHE_LIMIT:
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
                    "by_identity_digest": self._get(c, "critical_exit_service", {}),
                    "pressure_by_identity_digest": self._get(c, "critical_exit_pressure", {}),
                    "lower_suspended": any(p.get("status") == "DEGRADED" or p.get("until", 0) > now
                        for p in self._get(c, "critical_exit_pressure", {}).values())},
                "telemetry_retention": self._telemetry_retention(c, now, seconds),
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

    def _opened(self, unverified):
        return supervisable_position_count(self.database, unverified)

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

    def _current(self):
        state = self.controller.state(self.clock())
        if state["status"] != "APPROVED_DYNAMIC":
            if self.budget is None:
                saved = self._previous()
                if saved is None:
                    return None
                self.budget = GlobalPPIBudget(self.path, saved, clock=self.clock,
                    protected=[self.database, *self.controller.input_paths])
            previous = self.budget.policy
            opened = self._opened(max(20, _native_integer(self.controller.environ, "PAPER_MAX_OPEN_POSITIONS", 5)))
            self.budget.policy = validate_policy(baseline_budget_policy(previous,
                opened_count=opened, as_of=self.clock(), environ=self.controller.environ))
            return self.budget
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
        opened = max(opened, self._opened(max(state["global_budget"]["endpoint_limits"].values())))
        self.activation_contract = exit_capacity_contract(state, opened_count=opened)
        if self.activation_contract["status"] != "READY":
            raise BudgetBackpressure("PPI_EXIT_CAPACITY_INSUFFICIENT")
        policy = budget_policy(state, opened_count=opened, planned_reservations=planned)
        if self.budget is None:
            protected = [self.database, *self.controller.input_paths]
            self.budget = GlobalPPIBudget(self.path, policy, clock=self.clock, protected=protected)
        else:
            self.budget.policy = validate_policy(policy)
        return self.budget

    def acquire(self, endpoint, *, consumer="UNSCOPED", priority="DISCOVERY"):
        budget = self._current()
        return budget.acquire(endpoint, consumer=consumer, priority=priority) if budget else {"allowed": True, "lease": None}

    def coalesced_book(self, identity, fetch, *, consumer, priority):
        budget = self._current()
        # Invalid/missing/expired approval uses the original factual read path.
        # It never continues a dynamic cache or changes baseline cadence.
        if budget is None or budget.policy.get("authority", "").startswith("SAFE_FACTUAL_BASELINE"):
            return fetch()
        return budget.coalesced_book(identity, fetch, consumer=consumer, priority=priority)

    def start(self, lease):
        if lease:
            self.budget.start(lease)

    def wire_scope(self, lease):
        return self.budget.wire_scope(lease) if lease and self.budget else nullcontext()

    def finish(self, lease, **outcome):
        if lease:
            self.budget.finish(lease, **outcome)

    def report_error(self, endpoint, code):
        if self.budget:
            self.budget.report_error(endpoint, code)


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
        "global": None, "exit_service": None, "telemetry_retention": None}
    try:
        path = artifact_root(database) / "ppi-budget/global.sqlite"
        if any(p.is_symlink() for p in [path, *path.parents]):
            raise ValueError("PPI_BUDGET_PATH_ALIAS")
        try:
            metadata = path.stat()
        except FileNotFoundError:
            return result
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("PPI_BUDGET_PATH_ALIAS")
        for suffix in ("-journal", "-wal", "-shm"):
            auxiliary = Path(str(path) + suffix)
            try:
                aux_metadata = auxiliary.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(aux_metadata.st_mode) or aux_metadata.st_nlink != 1:
                raise ValueError("PPI_BUDGET_PATH_ALIAS")
            if suffix in {"-wal", "-shm"}:
                raise ValueError("PPI_BUDGET_JOURNAL_MODE_INVALID")
        # Refuse a WAL main before SQLite can create a missing SHM companion.
        with path.open("rb") as stream:
            header = stream.read(32)
        if header[:16] != b"SQLite format 3\x00" or header[18:20] != b"\x01\x01":
            raise ValueError("PPI_BUDGET_JOURNAL_MODE_INVALID")
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.005)) as c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA query_only=ON")
            c.set_progress_handler(lambda: 1, 100000)
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
            if (not isinstance(policy, dict) or not isinstance(pressure, dict) or not isinstance(service, dict)
                    or len(pressure) > BOOK_CACHE_LIMIT or len(service) > BOOK_CACHE_LIMIT):
                raise ValueError("PPI_BUDGET_OBSERVATION_BOUNDS_INVALID")
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
            suspended = any(p.get("status") == "DEGRADED" or p.get("until", 0) > at.timestamp() for p in pressure.values())
            degraded = any(p.get("status") == "DEGRADED" for p in pressure.values())
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
            return result | {"status": "DEGRADED" if degraded else "OBSERVED",
                "reason_codes": ["PPI_EXIT_DEADLINE_LOWER_SUSPENDED"] if degraded else [],
                "source_last_clock": last_clock,
                "age_seconds": at.timestamp() - last_clock if last_clock is not None else None,
                "global": totals,
                "exit_service": {"deadline_seconds": policy.get("critical_book_seconds"),
                    "lower_suspended": suspended, "by_identity_digest": service,
                    "pressure_by_identity_digest": pressure},
                "telemetry_retention": {"status": "BOUNDED_PARTIAL" if partial else "COMPLETE_RETAINED_WINDOW",
                    "window_complete": not partial, "earliest_retained_at": earliest,
                    "counter_resolution_seconds": 1,
                    **{key: retained[key] for key in ("evicted_buckets", "dropped_updates", "last_dropped_at") if key in retained}},
                "authority": "READ_ONLY_OBSERVATION; OPEN_CAPACITY_NOT_CERTIFIED"}
    except (OSError, ValueError, TypeError, InvalidOperation, sqlite3.Error):
        return result | {"status": "UNVERIFIED", "reason_codes": ["PPI_BUDGET_OBSERVATION_UNAVAILABLE"]}
