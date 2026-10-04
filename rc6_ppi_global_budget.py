"""Versioned cross-process wire budget for approved PPI market reads.

Only current/book/intraday are measured. One private sidecar database arbitrates
all engines and actual retries. No trading DB, broker or provider is imported.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import uuid

from rc6_dynamic_universe.common import digest, stamp

SCHEMA = "RC6_GLOBAL_PPI_BUDGET_V1"
ENDPOINTS = ("current", "book", "intraday")
PRIORITIES = ("EXIT_CRITICAL", "OPENED_CRITICAL", "SCALPING_HOT", "STRATEGY_HOT", "WARM", "DISCOVERY")


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
    critical = {"current": min(limits["current"], opened),
        "intraday": min(limits["intraday"], opened),
        "book": min(limits["book"], math.ceil(opened * envelope["window_seconds"] / settings["critical_book_seconds"]))}
    reserves = {"EXIT_CRITICAL": critical}
    for priority in ("SCALPING_HOT", "STRATEGY_HOT", "WARM"):
        demand = (planned_reservations or {}).get(priority, {})
        reserves[priority] = {e: min(limits[e], _int(demand.get(e, 0))) for e in ENDPOINTS}
    return {"schema": SCHEMA, "version": "rc6-global-ppi-v1",
        "recommendation_digest": state["recommendation_digest"],
        "configuration_fingerprint": state["configuration_fingerprint"],
        "window_seconds": envelope["window_seconds"], "endpoint_limits": dict(limits),
        "global_limit": envelope["global_limit"], "max_parallel_requests": 1,
        "safety_reserve": envelope["safety_reserve"], "priority_reserves": reserves,
        "expires_at": state["expires_at"], **settings}


def validate_policy(policy):
    if (policy.get("schema") != SCHEMA or policy.get("max_parallel_requests") != 1
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

    def acquire(self, endpoint, *, consumer="UNSCOPED", priority="DISCOVERY"):
        if endpoint not in ENDPOINTS or priority not in PRIORITIES:
            raise BudgetBackpressure("PPI_BUDGET_SCOPE_INVALID")
        # Fixed consumer labels bound totals cardinality and never store ticker,
        # URL, broker payload, credentials, account data or arbitrary messages.
        consumer = consumer if consumer in {"SCANNER", "SCALPING", "EXIT_READER", "TREASURY", "UNSCOPED"} else "UNSCOPED"
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
                if lease and lease["until"] > now:
                    reason = reason or "PPI_SERIAL_BACKPRESSURE"
                elif lease:
                    self._put(c, "inflight", None)
                    self._put(c, "abandoned_lease_observed", True)
                # Rolling windows prevent a boundary burst. A new policy may
                # widen the window; retain up to one hour of bounded receipts.
                c.execute("DELETE FROM budget_requests WHERE at<?", (now - 3600,))
                if c.execute("SELECT COUNT(*) FROM budget_requests").fetchone()[0] >= 20000:
                    reason = reason or "PPI_BUDGET_ROW_LIMIT"
                rows = list(c.execute("SELECT * FROM budget_requests WHERE at>?", (now - self.policy["window_seconds"],)))
                spent = {e: sum(r["endpoint"] == e for r in rows) for e in ENDPOINTS}
                used_by = {p: {e: sum(r["priority"] == p and r["endpoint"] == e for r in rows) for e in ENDPOINTS} for p in PRIORITIES}
                # Existing critical demand can be served by either opened or
                # exit-reader, but their shared reservation is counted once.
                used_by["EXIT_CRITICAL"] = {e: used_by["EXIT_CRITICAL"][e] + used_by["OPENED_CRITICAL"][e] for e in ENDPOINTS}
                reserves = self.policy.get("priority_reserves", {})
                effective = self._get(c, "reserves", {})
                reserve_at = self._get(c, "reserve_at", now)
                if now - reserve_at >= self.policy["window_seconds"]:
                    effective, reserve_at = {}, now
                # Never lower an already promised reserve in this window.
                for p, values in reserves.items():
                    effective[p] = {e: max(effective.get(p, {}).get(e, 0), values.get(e, 0)) for e in ENDPOINTS}
                self._put(c, "reserves", effective)
                self._put(c, "reserve_at", reserve_at)
                rank = PRIORITIES.index(priority)
                prior = [] if rank <= 1 else PRIORITIES[:rank]
                held = {e: sum(max(0, effective.get(p, {}).get(e, 0) - used_by[p][e]) for p in prior) for e in ENDPOINTS}
                if len(rows) >= self.policy["global_limit"] or spent[endpoint] >= self.policy["endpoint_limits"][endpoint]:
                    reason = reason or "PPI_BUDGET_EXHAUSTED"
                elif (spent[endpoint] + held[endpoint] >= self.policy["endpoint_limits"][endpoint]
                        or len(rows) + sum(held.values()) >= self.policy["global_limit"]):
                    reason = reason or "PPI_HIGH_PRIORITY_RESERVE_BACKPRESSURE"
                if reason:
                    self._total(c, endpoint, consumer, priority, dropped=1)
                    self._put(c, "last_backpressure", {"reason": reason, "at": now, "endpoint": endpoint})
                    return {"allowed": False, "reason": reason, "lease": None}
                token = uuid.uuid4().hex
                c.execute("INSERT INTO budget_requests VALUES(?,?,?,?,?,0)", (token, now, endpoint, consumer, priority))
                self._put(c, "inflight", {"lease": token, "until": now + self.policy["lease_seconds"]})
                self._put(c, "policy_fingerprint", digest(self.policy))
                self._put(c, "last_policy", self.policy)
                self._total(c, endpoint, consumer, priority, allowed=1)
                return {"allowed": True, "lease": token, "reason": "PPI_BUDGET_ALLOWED"}
        except (OSError, ValueError, sqlite3.Error) as error:
            raise BudgetBackpressure("PPI_BUDGET_STATE_UNAVAILABLE") from error

    def start(self, lease):
        with closing(self._connect()) as c, c:
            self._begin_write(c)
            self._clock(c)
            row = c.execute("SELECT * FROM budget_requests WHERE lease=?", (lease,)).fetchone()
            active = self._get(c, "inflight")
            if not row or not active or active["lease"] != lease or row["used"]:
                raise BudgetBackpressure("PPI_BUDGET_LEASE_INVALID")
            c.execute("UPDATE budget_requests SET used=1 WHERE lease=?", (lease,))
            self._total(c, row["endpoint"], row["consumer"], row["priority"], used=1)

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
            active = self._get(c, "inflight")
            if active and active["lease"] == lease:
                self._put(c, "inflight", None)

    def report_error(self, endpoint, code):
        with closing(self._connect()) as c, c:
            self._begin_write(c)
            self._outcome(c, endpoint, self._clock(c), code)

    def metrics(self):
        with closing(self._connect()) as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM budget_totals ORDER BY endpoint,consumer,priority")]
            totals = {name: sum(r[name] for r in rows) for name in ("requested", "allowed", "used", "dropped")}
            return {"schema": SCHEMA, "global": totals, "by_scope": rows,
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
    critical = {"current": opened, "intraday": opened,
        "book": math.ceil(opened * window / previous["critical_book_seconds"])}
    limits = {"current": max(equity, opened), "intraday": max(scalping, opened),
        "book": max(equity, opened) + critical["book"]}
    inputs = {"equity_limit": equity, "scalping_limit": scalping,
        "window_seconds": window, "opened": opened, "critical": critical}
    return dict(previous, version="rc6-global-ppi-safe-factual-baseline-v1",
        authority="SAFE_FACTUAL_BASELINE; OPEN_CAPACITY_NO_VERIFICADO",
        window_seconds=window, endpoint_limits=limits, global_limit=sum(limits.values()),
        configuration_fingerprint=digest(inputs), priority_reserves={"EXIT_CRITICAL": critical},
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

    def start(self, lease):
        if lease:
            self.budget.start(lease)

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
