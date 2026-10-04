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
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
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


class BudgetBackpressure(RuntimeError):
    """An off-wire scheduling rejection, never a provider capability result."""


def _int(value, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("PPI_BUDGET_INTEGER_INVALID")
    return value


def budget_policy(state, *, opened_count=0, planned_reservations=None):
    """Reserve actual critical demand, then planned high-priority deep tasks."""
    if state.get("status") != "APPROVED_DYNAMIC":
        raise ValueError("PPI_BUDGET_APPROVED_CAPACITY_REQUIRED")
    envelope = state["global_budget"]
    settings = state["budget_settings"]
    limits = envelope["endpoint_limits"]
    opened = _int(opened_count)
    exit_demand = {"current": 0, "intraday": 0,
        "book": math.ceil(opened * envelope["window_seconds"] / settings["critical_book_seconds"])}
    critical = {e: min(limits[e], exit_demand[e]) for e in ENDPOINTS}
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
        self.protected = {Path(p).resolve() for p in protected if p is not None}
        self._check_path()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = Path(str(self.path) + ".bootstrap.lock")
        self._check_one(lock_path)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as lock:
            if os.fstat(fd).st_nlink != 1:
                raise ValueError("PPI_BUDGET_PATH_ALIAS")
            fcntl.flock(lock, fcntl.LOCK_EX)
            self._check_path()
            if not self.path.exists():
                db = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                os.close(db)
            with closing(self._connect(timeout=.05)) as c, c:
                c.execute("PRAGMA journal_mode=DELETE")
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
                    self._limit_pages(c)
                    c.executescript("""
                    CREATE TABLE IF NOT EXISTS budget_state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS budget_requests(
                      lease TEXT PRIMARY KEY, at REAL NOT NULL, endpoint TEXT NOT NULL,
                      consumer TEXT NOT NULL, priority TEXT NOT NULL, used INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS budget_totals(
                      endpoint TEXT NOT NULL, consumer TEXT NOT NULL, priority TEXT NOT NULL,
                      requested INTEGER NOT NULL, allowed INTEGER NOT NULL,
                      used INTEGER NOT NULL, dropped INTEGER NOT NULL,
                      PRIMARY KEY(endpoint,consumer,priority));
                """)
                    self._put(c, "schema", SCHEMA)
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
        c.execute(f"PRAGMA max_page_count={self.policy['maximum_bytes'] // (2 * page_size)}")

    def _begin_write(self, c):
        # A changing SQLite writer may finish during this bounded 50ms wait.
        # Network requests never hold this SQL lock, and uncertainty still
        # fails closed; the durable wire lease remains independent of it.
        c.execute("BEGIN IMMEDIATE")
        self._limit_pages(c)

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
        self._put(c, bucket, value)
        c.execute("DELETE FROM budget_state WHERE key LIKE 'window:%' AND CAST(substr(key,8) AS INTEGER)<?", (int(now) - 3600,))

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
        if used_by[priority][endpoint] < reserves[priority][endpoint]:
            return None
        remaining = {p: {e: max(0, reserves[p][e] - used_by[p][e]) for e in ENDPOINTS} for p in PRIORITIES}
        limits = self.policy["endpoint_limits"] if limits is None else limits
        global_limit = self.policy["global_limit"] if global_limit is None else global_limit
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
            held = {e: sum(max(0, reserves[p][e] - used_by[p][e]) for p in PRIORITIES[:PRIORITIES.index(priority)]) for e in ENDPOINTS}
            if len(rows) >= cap or spent[endpoint] >= limits[endpoint]:
                return "PPI_BUDGET_EXHAUSTED", None
            if spent[endpoint] + held[endpoint] >= limits[endpoint] or len(rows) + sum(held.values()) >= cap:
                return "PPI_HIGH_PRIORITY_RESERVE_BACKPRESSURE", None
            donors.append(self._borrow(endpoint, priority, rows, reserves, used_by, limits=limits, global_limit=cap))
        # One wire read can discharge the same rank's promises in multiple
        # authorities. Do not invent borrowing when any such own floor exists.
        donor = None if None in donors else next((d for d in donors if d[0] != "COMMON"), ("COMMON", endpoint))
        return None, donor

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
        with closing(self._connect()) as c, c:
            self._begin_write(c)
            now = self._clock(c)
            row = c.execute("SELECT * FROM budget_requests WHERE lease=?", (lease,)).fetchone()
            if not row:
                raise BudgetBackpressure("PPI_BUDGET_LEASE_INVALID")
            self._outcome(c, row["endpoint"], now, error_code or (f"PPI_HTTP_{status_code}" if status_code and status_code >= 400 else None))
            if not row["used"]:
                # A confirmed pre-wire rejection releases its unused claim;
                # emitted receipts are never cleared by policy replacement.
                c.execute("DELETE FROM budget_requests WHERE lease=?", (lease,))
            active = self._get(c, "inflight")
            if active and active["lease"] == lease:
                self._put(c, "inflight", None)

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
        A follower waits at most 50ms, then reports explicit backpressure.
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
        deadline = time.monotonic() + .05
        try:
            while True:
                with closing(self._connect()) as c, c:
                    self._begin_write(c)
                    now = self._clock(c)
                    if now >= stamp(self.policy["expires_at"]).timestamp():
                        raise BudgetBackpressure("PPI_CAPACITY_EXPIRED_BACKPRESSURE")
                    circuits = self._get(c, "circuits", {})
                    if any(circuits.get(k, {}).get("until", 0) > now for k in ("global", "book")):
                        raise BudgetBackpressure("PPI_GLOBAL_CIRCUIT_OPEN")
                    cache = self._get(c, "critical_books", {})
                    flights = self._get(c, "critical_book_flights", {})
                    cache = {k: v for k, v in cache.items() if 0 <= now - v["received_at"] <= age and v["authority"] == authority}
                    cached = cache.get(key)
                    if cached and self._safe_book(cached["book"], now, age) is not None:
                        self._window_total(c, now, "book", consumer, priority, coalesced=1)
                        return deepcopy(cached["book"])
                    cache.pop(key, None)
                    flights = {k: v for k, v in flights.items() if v["until"] > now}
                    active = flights.get(key)
                    if active is None:
                        if len(flights) >= BOOK_CACHE_LIMIT:
                            raise BudgetBackpressure("PPI_BOOK_SINGLE_FLIGHT_CAPACITY")
                        flights[key] = {"lease": token, "until": now + self.policy["lease_seconds"]}
                        self._put(c, "critical_books", cache)
                        self._put(c, "critical_book_flights", flights)
                        break
                if time.monotonic() >= deadline:
                    raise BudgetBackpressure("PPI_BOOK_SINGLE_FLIGHT_BACKPRESSURE")
                time.sleep(.005)
            try:
                result = fetch()
            except BaseException:
                # A hard process kill leaves a bounded durable flight; restart
                # cannot manufacture concurrency or silently use an old book.
                self._complete_book(key, token, authority, age, None)
                raise
            self._complete_book(key, token, authority, age, result)
            return result
        except (OSError, ValueError, sqlite3.Error) as error:
            raise BudgetBackpressure("PPI_BUDGET_STATE_UNAVAILABLE") from error

    def _complete_book(self, key, token, authority, age, payload):
        with closing(self._connect()) as c, c:
            self._begin_write(c)
            now = self._clock(c)
            flights = self._get(c, "critical_book_flights", {})
            flight = flights.get(key)
            if not flight or flight["lease"] != token or flight["until"] <= now:
                raise BudgetBackpressure("PPI_BOOK_SINGLE_FLIGHT_LEASE_INVALID")
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
            _, used_by = self._usage(requests)
            promises = {p: {e: max([0, *(item["priority_reserves"].get(p, {}).get(e, 0) for item in envelopes)]) for e in ENDPOINTS} for p in PRIORITIES}
            limits = {e: min([self.policy["endpoint_limits"][e], *(item["endpoint_limits"][e] for item in envelopes)]) for e in ENDPOINTS}
            cap = min([self.policy["global_limit"], *(item["global_limit"] for item in envelopes)])
            reserves = self._allocate_reserves(promises, limits=limits, global_limit=cap)
            scopes = self._window_scopes(c, now, seconds)
            opened = max([self.policy.get("open_positions_count", 0), *(item["open_positions_count"] for item in envelopes)])
            exit_demand = {e: max([self.policy.get("exit_demand", self.policy.get("priority_reserves", {}).get("EXIT_CRITICAL", {})).get(e, 0),
                *(item.get("exit_demand", {}).get(e, item["priority_reserves"].get("EXIT_CRITICAL", {}).get(e, 0)) for item in envelopes)]) for e in ENDPOINTS}
            exact_envelopes = []
            for envelope in envelopes:
                receipts = [r for r in requests if r["at"] > now - envelope["window_seconds"]]
                _, owned = self._usage(receipts)
                allocated = self._allocate_reserves(envelope["priority_reserves"], limits=envelope["endpoint_limits"], global_limit=envelope["global_limit"])
                scope = scopes if envelope["window_seconds"] == seconds else self._window_scopes(c, now, envelope["window_seconds"])
                donated_by = {p: {e: sum(row["borrowed_out"] for key, row in scope.items()
                    if key.split(":")[0] == e and key.split(":")[2] == p) for e in ENDPOINTS} for p in PRIORITIES}
                exact_envelopes.append({name: envelope[name] for name in ("key", "recommendation_digest", "configuration_fingerprint", "window_seconds", "endpoint_limits", "global_limit", "authority_expires_at", "retain_until")} | {
                    "start_at": datetime.fromtimestamp(now - envelope["window_seconds"], timezone.utc).isoformat(),
                    "end_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                    "admitted_in_window": len(receipts), "used_in_window": sum(r["used"] for r in receipts),
                    "priority_reserves": allocated,
                    "reserved_remaining": {p: {e: max(0, allocated[p][e] - owned[p][e] - donated_by[p][e]) for e in ENDPOINTS} for p in PRIORITIES}})
            for priority in PRIORITIES:
                if any(reserves[priority].values()):
                    consumer = "EXIT_READER" if priority == "EXIT_CRITICAL" else "SCALPING" if priority == "SCALPING_HOT" else "SCANNER"
                    for endpoint in ENDPOINTS:
                        scopes.setdefault(":".join((endpoint, consumer, priority)), dict.fromkeys(WINDOW_FIELDS, 0) | {"denial_reason": {}})
            by_window_scope = []
            donated = {p: {e: sum(row["borrowed_out"] for key, row in scopes.items()
                if key.split(":")[0] == e and key.split(":")[2] == p) for e in ENDPOINTS} for p in PRIORITIES}
            for key, row in sorted(scopes.items()):
                endpoint, consumer, priority = key.split(":")
                by_window_scope.append(row | {"endpoint": endpoint, "consumer": consumer, "priority": priority,
                    "reserved_total": reserves[priority][endpoint],
                    "reserved_remaining": max(0, reserves[priority][endpoint] - used_by[priority][endpoint] - donated[priority][endpoint]),
                    "open_positions_count": opened, "exit_demand": exit_demand[endpoint]})
            window_endpoint = {}
            for endpoint in ENDPOINTS:
                source = [r for r in by_window_scope if r["endpoint"] == endpoint]
                window_endpoint[endpoint] = {name: sum(r[name] for r in source) for name in WINDOW_FIELDS}
                window_endpoint[endpoint].update(reserved_total=sum(v[endpoint] for v in reserves.values()),
                    reserved_remaining=sum(max(0, reserves[p][endpoint] - used_by[p][endpoint] - donated[p][endpoint]) for p in PRIORITIES),
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
                "max_parallel_requests": 1, "real_orders_sent": 0, "real_routes": "NOT_CALLED"}


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
    critical = {"current": 0, "intraday": 0,
        "book": math.ceil(opened * window / previous["critical_book_seconds"])}
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

    def _opened(self, unverified):
        try:
            with closing(sqlite3.connect(self.database.resolve().as_uri() + "?mode=ro", uri=True, timeout=.005)) as c:
                c.execute("PRAGMA query_only=ON")
                c.set_progress_handler(lambda: 1, 100000)
                state = c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
                if not state or tuple(state) != ("PRODUCTION_PAPER", 0):
                    raise ValueError("PPI_BUDGET_PAPER_SOURCE_UNVERIFIED")
                tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "paper_positions" not in tables:
                    raise ValueError("PPI_BUDGET_OPENED_LEDGER_UNAVAILABLE")
                counts = [c.execute(f"SELECT COUNT(*) FROM {table} WHERE status='OPEN'").fetchone()[0]
                    for table in ("paper_positions", "paper_future_positions") if table in tables]
                return sum(counts)
        except (OSError, ValueError, sqlite3.Error):
            return unverified

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
