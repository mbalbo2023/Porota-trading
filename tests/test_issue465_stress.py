"""Actual offline load/locks/isolated-process regressions, not microbenchmarks."""
from contextlib import closing, contextmanager
from datetime import timedelta
import multiprocessing as mp
from pathlib import Path
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from queue import SimpleQueue

import pytest

from scripts.rc6_issue465_stress import (AT, fixture_database, run_stress, sha256)
from rc6_dynamic_universe.runtime import read_runtime
from rc6_ppi_global_budget import BudgetBackpressure, GlobalPPIBudget, SCHEMA


def record(result, name):
    root = os.getenv("ISSUE465_EVIDENCE_DIR")
    if not root and os.getenv("RUNNER_TEMP"):
        root = str(Path(os.environ["RUNNER_TEMP"])/"porota-predeploy-evidence"/"issue465-stress")
    if root:
        Path(root).mkdir(parents=True, exist_ok=True)
        (Path(root)/(name+".json")).write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
        # Committed synthetic causal witnesses and frozen focal metadata are
        # uploaded with the new Actions artifact; no private dataset is copied.
        audits = Path(__file__).resolve().parents[1]/"docs"/"audits"
        for evidence_name in ("ISSUE465_REAUDIT_RED.json", "ISSUE465_REAUDIT_FOCAL.json"):
            source = audits/evidence_name
            if source.is_file() and not source.is_symlink():
                assert source.stat().st_size <= 10*1024**2
                shutil.copyfile(source, Path(root)/evidence_name)


def test_10x_catalog_5x_observations_exercises_all_shadow_labs_and_five_factual_exits(tmp_path):
    result = run_stress(tmp_path / "load")
    record(result, "catalog10x-observations5x")
    assert result["catalog_multiplier"] == 10 and result["observation_multiplier"] == 5
    assert result["observations_materialized"] == 60000
    assert result["shadow"]["full_pipeline_exercised"], json.dumps(result["shadow"])
    assert result["shadow"]["cycle_handled"]
    assert result["shadow"]["cycle_completion"]
    assert result["shadow"]["observation_read_truncated"]
    assert result["shadow"]["committed_sequence"] == 2
    assert result["shadow"]["catalog_ready_count"] == 12000
    assert result["factual_exits"]["closed"] == 5
    assert result["source_database_unchanged"] and result["real_orders_sent"] == 0


@pytest.mark.parametrize("quota", [128*1024**2, 1024])
def test_slow_disk_or_shadow_quota_failure_cannot_block_actual_exit_supervisor(tmp_path, quota):
    result = run_stress(tmp_path / "isolation", catalog_count=100,
                        slow_disk=True, maximum_bytes=quota, allow_fail_closed=quota == 1024)
    record(result, "slow-disk-quota-"+str(quota))
    assert result["factual_exits"]["closed"] == result["factual_exits"]["sell_fills"] == 5
    assert result["source_database_unchanged"]
    if quota == 1024:
        assert not result["completion_required"]
        assert not result["slow_fsync_exit_isolation_proven"]
        assert not result["shadow"]["cycle_completion"]
        assert result["shadow"]["status"] == "SHADOW_FAIL_CLOSED"
        assert result["shadow"]["reason"] in {
            "SHADOW_EVIDENCE_CAPACITY_REACHED", "RETENTION_HARD_BYTES_CAPACITY_REACHED"}
    else:
        assert result["shadow"]["cycle_completion"], result["shadow"]
        assert result["shadow"]["fsync"]["fsync_entered"] and result["shadow"]["fsync"]["fsync_completed"]
        assert result["slow_fsync_exit_isolation_proven"]


def test_canonical_factory_stress_uses_private_native_roots_and_matching_fingerprint():
    from rc6_shadow_runtime.worker import ShadowRuntime
    from rc6_shadow_runtime.persistence import shadow_evidence_root, shadow_archive_root
    from unittest.mock import patch
    from cg_paper_workspace import artifact_root
    from rc6_audit_evidence.sqlite_scratch import LOCK
    from scripts.rc6_sqlite_scratch_guard import runtime_settings
    # Canonical scratch requires real disk, while pytest's /tmp may be tmpfs.
    with tempfile.TemporaryDirectory(prefix=".rc6-canonical-stress-", dir=Path.cwd()) as directory:
        result = run_stress(Path(directory) / "canonical", catalog_count=20, canonical_runtime=True, slow_disk=True)
        shadow = result["shadow"]
        assert result["canonical_runtime_requested"] and shadow["canonical_factory"] and shadow["cycle_completion"]
        assert result["source_database_unchanged"] and result["slow_fsync_exit_isolation_proven"]
        assert result["source_custody_before"] == result["source_custody_after"]
        assert result["source_custody_scope"] == "MAIN_WAL_SHM_JOURNAL_BYTES_AND_ALL_CUSTODY_STATS_NOATIME"
        assert result["factual_exits"]["sell_fills"] == 5
        env = shadow["fixture_environment"]
        scratch = artifact_root(result["database"]) / "sqlite-read-scratch"
        assert {name: env[name] for name in runtime_settings(scratch)} == runtime_settings(scratch)
        assert scratch.stat().st_mode & 0o777 == 0o700
        assert {path.name for path in scratch.iterdir()} == {LOCK}
        with patch.dict(os.environ, env, clear=True):
            restored = ShadowRuntime.from_environment(result["database"])
            assert str(shadow_evidence_root(result["database"])) == result["evidence_root"]
            assert str(shadow_archive_root(result["database"])) == shadow["archive_root"]
            assert list(map(str, restored.source_roots)) == shadow["source_roots"]
            assert restored.configuration_fingerprint(AT) == shadow["configuration_fingerprint"]


def test_wal_writer_lock_does_not_turn_shadow_into_writer_or_unbounded_query(tmp_path):
    path = tmp_path / "source.db"
    fixture_database(path, catalog_count=101)
    before = sha256(path)
    writer = sqlite3.connect(path)
    writer.execute("BEGIN IMMEDIATE")
    try:
        result = read_runtime(path, as_of=AT, row_limit=101, query_budget_seconds=2)
        assert len(result["catalog"]) == 101 and len(result["observations"]) <= 202
        assert result["observation_read_truncated"]
        with pytest.raises(ValueError, match="CATALOG_READ_TRUNCATED"):
            read_runtime(path, as_of=AT, row_limit=100, query_budget_seconds=2)
    finally:
        writer.rollback()
        writer.close()
    assert sha256(path) == before


def _confirmation_deadline(deadline):
    if time.monotonic() >= deadline:
        raise BudgetBackpressure("PPI_STRESS_CONFIRMATION_DEADLINE")


def _native_busy_state(error):
    cause = error.__cause__
    return (isinstance(error, BudgetBackpressure) and str(error) == "PPI_BUDGET_STATE_UNAVAILABLE"
            and isinstance(cause, sqlite3.OperationalError)
            and type(getattr(cause, "sqlite_errorcode", None)) is int
            and cause.sqlite_errorcode == sqlite3.SQLITE_BUSY)


def _lease_snapshot(budget, lease, deadline):
    """Read an exact claim without mutating it or hiding an unverifiable read."""
    _confirmation_deadline(deadline)
    with closing(sqlite3.connect(budget.path.as_uri()+"?mode=ro", uri=True, timeout=.05)) as c:
        c.execute("PRAGMA query_only=ON")
        c.execute("BEGIN")
        row = c.execute("SELECT * FROM budget_requests WHERE lease=?", (lease,)).fetchone()
        active = c.execute("SELECT value FROM budget_state WHERE key='inflight'").fetchone()
    _confirmation_deadline(deadline)
    if row is None:
        raise BudgetBackpressure("PPI_BUDGET_LEASE_INVALID")
    return tuple(row), json.loads(active[0]) if active else None


def _same_lease_start(budget, lease, faults, deadline):
    before = _lease_snapshot(budget, lease, deadline)
    _confirmation_deadline(deadline)
    assert before[0][-1] == 0 and before[1] and before[1]["lease"] == lease
    while True:
        _confirmation_deadline(deadline)
        try:
            budget.start(lease)
            _confirmation_deadline(deadline)
        except BudgetBackpressure as error:
            if not _native_busy_state(error):
                raise
            faults.append({"phase": "START_STATE_UNAVAILABLE", "lease": lease, "native_code": 5})
            observed = _lease_snapshot(budget, lease, deadline)
            _confirmation_deadline(deadline)
            assert observed == before
            _confirmation_deadline(deadline)
            time.sleep(.005)
        else:
            debt, active = _lease_snapshot(budget, lease, deadline)
            _confirmation_deadline(deadline)
            assert debt[0] == lease and debt[2:5] == before[0][2:5] and debt[-1] == 1
            assert active and active["lease"] == lease
            return


def _same_lease_finish(budget, lease, faults, deadline, *, completed, wire_leases):
    """Offline completion confirmation, never another acquisition or wire."""
    assert completed is True, "modeled read completion must be verified"
    assert wire_leases.count(lease) == 1, "one same-lease modeled wire must be verified"
    before = _lease_snapshot(budget, lease, deadline)
    _confirmation_deadline(deadline)
    assert before[0][-1] == 1 and before[1] and before[1]["lease"] == lease
    while True:
        _confirmation_deadline(deadline)
        try:
            budget.finish(lease)
            _confirmation_deadline(deadline)
        except BudgetBackpressure as error:
            if not _native_busy_state(error):
                raise
            faults.append({"phase": "FINISH_STATE_UNAVAILABLE", "lease": lease, "native_code": 5})
            observed = _lease_snapshot(budget, lease, deadline)
            _confirmation_deadline(deadline)
            assert observed == before
            _confirmation_deadline(deadline)
            time.sleep(.005)
        else:
            debt, active = _lease_snapshot(budget, lease, deadline)
            _confirmation_deadline(deadline)
            assert debt == before[0] and active is None
            return


def _modeled_read(lease, wire_leases):
    assert lease not in wire_leases, "an admitted lease can emit only one modeled wire"
    wire_leases.append(lease)
    return True


def _complete_admitted_wire(budget, lease, faults, wire_leases, deadline):
    _confirmation_deadline(deadline)
    # Entry/read uncertainty remains fatal. Only fully verified native start
    # or finish SQLITE_BUSY=5 may confirm the same lease under this ownership.
    with budget.wire_scope(lease):
        _confirmation_deadline(deadline)
        _same_lease_start(budget, lease, faults, deadline)
        _confirmation_deadline(deadline)
        completed = _modeled_read(lease, wire_leases)
        _confirmation_deadline(deadline)
        _same_lease_finish(budget, lease, faults, deadline, completed=completed, wire_leases=wire_leases)
        _confirmation_deadline(deadline)
    _confirmation_deadline(deadline)


def _burst_process(path, config, priority, count, out, endpoint="book", ready=None, initialized=None, lifecycle=None):
    budget = GlobalPPIBudget(path, config, clock=lambda: AT)
    if initialized is not None:
        initialized.put(priority)
        assert ready.wait(10), "admission barrier was not released"
    admitted, denied = 0, 0
    reasons = {}
    faults, wire_leases = [], []
    for _ in range(count):
        try:
            result = budget.acquire(endpoint, consumer="EXIT_READER" if priority == "EXIT_CRITICAL" else "SCANNER",
                                    priority=priority)
        except BudgetBackpressure as error:
            # Only the exact native writer contention is a known off-wire
            # denial. Unknown IO/SQLite/lease faults remain explicit failures.
            if not _native_busy_state(error):
                raise
            faults.append({"phase": "ACQUIRE_STATE_UNAVAILABLE", "lease": None, "native_code": 5})
            result = {"allowed": False, "reason": str(error)}
        if result["allowed"]:
            _complete_admitted_wire(budget, result["lease"], faults, wire_leases, time.monotonic()+5)
            admitted += 1
        else:
            denied += 1
            reason = result["reason"]
            assert reason in {"PPI_BUDGET_STATE_UNAVAILABLE", "PPI_SERIAL_BACKPRESSURE",
                              "PPI_HIGH_PRIORITY_RESERVE_BACKPRESSURE", "PPI_BUDGET_EXHAUSTED"}
            reasons[reason] = reasons.get(reason, 0)+1
    out.put((priority, admitted, denied, reasons))
    if lifecycle is not None:
        source = Path(sys.modules[GlobalPPIBudget.__module__].__file__)
        lifecycle.put({"priority": priority, "endpoint": endpoint, "faults": faults,
                       "wire_leases": wire_leases, "modeled_wires": len(wire_leases),
                       "budget_source_path": str(source), "budget_source_sha256": sha256(source)})


@pytest.mark.parametrize("endpoint", ["book", "intraday"])
def test_multiprocess_burst_cannot_spend_exit_floor(tmp_path, endpoint):
    path = str(tmp_path/"budget.sqlite")
    config = {"schema": SCHEMA, "version": "synthetic-stress", "recommendation_digest": "a"*64,
        "configuration_fingerprint": "b"*64, "window_seconds": 30,
        "endpoint_limits": {"current": 5, "book": 5, "intraday": 5}, "global_limit": 15,
        "max_parallel_requests": 1, "safety_reserve": "NOT_PROVIDER_CAPACITY_CLAIM",
        "priority_reserves": {"EXIT_CRITICAL": {"book": 5}},
        "expires_at": (AT+timedelta(hours=1)).isoformat(), "critical_book_seconds": 5,
        "lease_seconds": 60, "breaker_seconds": 60, "session_breaker_seconds": 900,
        "server_error_threshold": 2, "maximum_bytes": 8*1024**2}
    budget = GlobalPPIBudget(path, config, clock=lambda: AT)
    ctx = mp.get_context("spawn")
    out, initialized, lifecycle = ctx.Queue(), ctx.Queue(), ctx.Queue()
    ready = ctx.Event()
    priorities = (("OPENED_CRITICAL", "SCALPING_HOT", "DISCOVERY") if endpoint == "book"
                  else ("SCALPING_HOT", "STRATEGY_HOT", "DISCOVERY"))
    children = [ctx.Process(target=_burst_process, args=(path, config, p, 30, out, endpoint, ready, initialized, lifecycle))
                for p in priorities]
    exit_child = None
    try:
        # Initialize before contesting admission; separate A guards stress
        # actual startup contention. No constructor fault is caught here.
        for priority, child in zip(priorities, children):
            child.start()
            assert initialized.get(timeout=10) == priority
        ready.set()
        for child in children:
            child.join(20)
            if child.is_alive():
                child.terminate(); child.join(5)
            assert child.exitcode == 0
        results = [out.get(timeout=5) for _ in children]
        lifecycle_results = [lifecycle.get(timeout=5) for _ in children]
        lower_admitted = sum(admitted for _, admitted, _, _ in results)
        if endpoint == "book":
            assert lower_admitted == 0, results
        else:
            assert 1 <= lower_admitted <= 5, results
        assert all(admitted+denied == 30 and sum(reasons.values()) == denied
                   for _, admitted, denied, reasons in results)
        exit_child = ctx.Process(target=_burst_process,
            args=(path, config, "EXIT_CRITICAL", 5, out, "book", None, None, lifecycle))
        exit_child.start(); exit_child.join(20)
        if exit_child.is_alive():
            exit_child.terminate(); exit_child.join(5)
        assert exit_child.exitcode == 0
        assert out.get(timeout=5) == ("EXIT_CRITICAL", 5, 0, {})
        lifecycle_results.append(lifecycle.get(timeout=5))
        source = Path(sys.modules[GlobalPPIBudget.__module__].__file__)
        assert all(r["budget_source_path"] == str(source) and r["budget_source_sha256"] == sha256(source)
                   for r in lifecycle_results)
        metrics = budget.metrics()
        assert metrics["global"]["used"] == 5+lower_admitted
        assert metrics["global"]["allowed"] == 5+lower_admitted
        wire_leases = [lease for r in lifecycle_results for lease in r["wire_leases"]]
        assert len(set(wire_leases)) == len(wire_leases) == 5+lower_admitted
        with closing(sqlite3.connect(budget.path.as_uri()+"?mode=ro", uri=True, timeout=.05)) as c:
            receipts = list(c.execute("SELECT lease,used FROM budget_requests"))
        assert set(receipts) == {(lease, 1) for lease in wire_leases}
        record({"synthetic": True, "endpoint": endpoint, "burst": results, "metrics": metrics,
                "lifecycle": lifecycle_results, "modeled_wires": len(wire_leases),
                "sqlite_wait_policy_ms": 50, "real_orders_sent": 0}, "multiprocess-budget-"+endpoint)
    finally:
        ready.set()
        for child in [*children, *([exit_child] if exit_child is not None else [])]:
            if child.pid is not None:
                if child.is_alive():
                    child.terminate()
                child.join(2)
                if child.is_alive():
                    child.kill(); child.join(2)
                assert not child.is_alive(), "fixture process cleanup did not complete"
        for queue in (out, initialized, lifecycle):
            queue.cancel_join_thread(); queue.close()


def _locked_burst_process(path, config, ready, release, out):
    # Initialize the actual native reader before the parent's deliberate writer
    # lock, then exercise the same child burst path used by the stress.
    GlobalPPIBudget(path, config, clock=lambda: AT)
    ready.set()
    assert release.wait(10)
    _burst_process(path, config, "DISCOVERY", 3, out)


def test_native_budget_writer_lock_is_explicit_child_denial_without_wire_or_exit_loss(tmp_path):
    path = str(tmp_path/"budget.sqlite")
    config = {"schema": SCHEMA, "recommendation_digest": "a"*64,
        "configuration_fingerprint": "b"*64, "window_seconds": 30,
        "endpoint_limits": {"current": 5, "book": 5, "intraday": 5}, "global_limit": 15,
        "max_parallel_requests": 1, "priority_reserves": {"EXIT_CRITICAL": {"book": 5}},
        "expires_at": (AT+timedelta(hours=1)).isoformat(), "critical_book_seconds": 5,
        "lease_seconds": 60, "breaker_seconds": 60, "session_breaker_seconds": 900,
        "server_error_threshold": 2, "maximum_bytes": 8*1024**2}
    budget = GlobalPPIBudget(path, config, clock=lambda: AT)
    ctx = mp.get_context("spawn")
    ready, release, out = ctx.Event(), ctx.Event(), ctx.Queue()
    child = ctx.Process(target=_locked_burst_process, args=(path, config, ready, release, out))
    writer = sqlite3.connect(path)
    try:
        child.start()
        assert ready.wait(10)
        writer.execute("BEGIN IMMEDIATE")
        release.set()
        child.join(5)
        assert child.exitcode == 0
        assert out.get(timeout=2) == ("DISCOVERY", 0, 3,
                                      {"PPI_BUDGET_STATE_UNAVAILABLE": 3})
    finally:
        writer.rollback(); writer.close()
        if child.is_alive():
            child.terminate(); child.join(5)
        out.close(); out.join_thread()
    assert budget.metrics()["global"]["used"] == 0
    for _ in range(5):
        request = budget.acquire("book", priority="EXIT_CRITICAL", consumer="EXIT_READER")
        assert request["allowed"]
        budget.start(request["lease"]); budget.finish(request["lease"])
    assert budget.metrics()["global"]["used"] == 5
    record({"synthetic": True, "locked_child_denied": 3, "factual_exit_admitted": 5,
            "denial_reason": "PPI_BUDGET_STATE_UNAVAILABLE", "real_orders_sent": 0},
           "native-budget-writer-lock")


def _lifecycle_policy():
    return {"schema": SCHEMA, "version": "synthetic-stress", "recommendation_digest": "a"*64,
        "configuration_fingerprint": "b"*64, "window_seconds": 30,
        "endpoint_limits": {"current": 5, "book": 5, "intraday": 5}, "global_limit": 15,
        "max_parallel_requests": 1, "safety_reserve": "NOT_PROVIDER_CAPACITY_CLAIM",
        "priority_reserves": {"EXIT_CRITICAL": {"book": 5}},
        "expires_at": (AT+timedelta(hours=1)).isoformat(), "critical_book_seconds": 5,
        "lease_seconds": 60, "breaker_seconds": 60, "session_breaker_seconds": 900,
        "server_error_threshold": 2, "maximum_bytes": 8*1024**2}


def _lifecycle_fixture(tmp_path, *, used=False):
    budget = GlobalPPIBudget(tmp_path/"budget.sqlite", _lifecycle_policy(), clock=lambda: AT)
    result = budget.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert result["allowed"]
    if used:
        with budget.wire_scope(result["lease"]):
            budget.start(result["lease"])
    return budget, result["lease"]


def _lifecycle_contents(path):
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=.05)) as c:
        c.execute("PRAGMA query_only=ON")
        c.execute("BEGIN")
        return {table: list(c.execute("SELECT * FROM "+table+" ORDER BY 1,2"))
                for table in ("budget_state", "budget_requests", "budget_totals")}


def test_native_start_and_post_completion_finish_locks_confirm_same_lease_without_resends(tmp_path, monkeypatch):
    actual_start, actual_finish = GlobalPPIBudget.start, GlobalPPIBudget.finish
    actual_read = _modeled_read
    modeled, attempts, denials = [], {"start": [], "finish": []}, []
    injected = set()
    def read_once(lease, markers):
        result = actual_read(lease, markers)
        modeled.append(lease)
        return result
    monkeypatch.setattr(sys.modules[__name__], "_modeled_read", read_once)
    def locked_once(phase, operation):
        def wrapped(budget, lease):
            attempts[phase].append(lease)
            if phase in injected:
                return operation(budget, lease)
            injected.add(phase)
            before = _lifecycle_contents(budget.path)
            debt, active = _lease_snapshot(budget, lease, time.monotonic()+2)
            assert active["lease"] == lease and debt[-1] == (phase == "finish")
            assert modeled.count(lease) == (phase == "finish")
            with closing(sqlite3.connect(budget.path)) as writer:
                writer.execute("BEGIN IMMEDIATE")
                try:
                    with pytest.raises(BudgetBackpressure) as caught:
                        operation(budget, lease)
                    assert _native_busy_state(caught.value)
                    assert _lifecycle_contents(budget.path) == before
                    assert _lease_snapshot(budget, lease, time.monotonic()+2) == (debt, active)
                    denials.append({"phase": phase, "lease": lease, "used": debt[-1],
                                   "inflight_lease": active["lease"], "conserved": True})
                    raise caught.value
                finally:
                    writer.rollback()
        return wrapped
    monkeypatch.setattr(GlobalPPIBudget, "start", locked_once("start", actual_start))
    monkeypatch.setattr(GlobalPPIBudget, "finish", locked_once("finish", actual_finish))
    out, lifecycle = SimpleQueue(), SimpleQueue()
    path = tmp_path/"budget.sqlite"
    _burst_process(str(path), _lifecycle_policy(), "EXIT_CRITICAL", 5, out, lifecycle=lifecycle)
    assert out.get() == ("EXIT_CRITICAL", 5, 0, {})
    result = lifecycle.get()
    assert len(set(modeled)) == len(modeled) == result["modeled_wires"] == 5
    assert result["wire_leases"] == modeled
    first = modeled[0]
    assert attempts["start"] == attempts["finish"] == [first, *modeled]
    assert result["faults"] == [
        {"phase": "START_STATE_UNAVAILABLE", "lease": first, "native_code": 5},
        {"phase": "FINISH_STATE_UNAVAILABLE", "lease": first, "native_code": 5}]
    contents = _lifecycle_contents(path)
    assert set((r[0], r[-1]) for r in contents["budget_requests"]) == {(lease, 1) for lease in modeled}
    assert json.loads(dict(contents["budget_state"])["inflight"]) is None
    assert sum(r[-2] for r in contents["budget_totals"]) == 5
    record({"synthetic": True, "lifecycle": result, "native_denials": denials,
            "completed_modeled_wires": 5, "same_lease_no_resends": True,
            "sqlite_wait_policy_ms": 50, "real_orders_sent": 0}, "native-same-lease-lifecycle")


def _actual_unknown_fault(tmp_path, name):
    if name == "state_no_cause":
        return BudgetBackpressure("PPI_BUDGET_STATE_UNAVAILABLE")
    if name == "io":
        try:
            (tmp_path/"absent-io-fixture").read_bytes()
        except FileNotFoundError as error:
            return error
    if name == "sqlite_other":
        try:
            sqlite3.connect((tmp_path/"absent-native-state").as_uri()+"?mode=ro", uri=True)
        except sqlite3.OperationalError as error:
            assert error.sqlite_errorcode == sqlite3.SQLITE_CANTOPEN
            return error
    if name == "sqlite_locked":
        with closing(sqlite3.connect(":memory:")) as c:
            c.execute("CREATE TABLE fixture(a)")
            c.executemany("INSERT INTO fixture VALUES(?)", [(1,), (2,)])
            cursor = c.execute("SELECT * FROM fixture")
            try:
                c.execute("DROP TABLE fixture")
            except sqlite3.OperationalError as error:
                assert error.sqlite_errorcode == sqlite3.SQLITE_LOCKED
                return error
            finally:
                cursor.close()
    if name == "untyped_busy":
        with closing(sqlite3.connect(tmp_path/"raw-busy.sqlite")) as a, closing(
                sqlite3.connect(tmp_path/"raw-busy.sqlite", timeout=.005)) as b:
            a.execute("BEGIN IMMEDIATE")
            try:
                b.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                assert error.sqlite_errorcode == sqlite3.SQLITE_BUSY
                return error
            finally:
                a.rollback()
    raise AssertionError("native fixture did not produce its required fault")


@pytest.mark.parametrize("phase", ["start", "finish"])
@pytest.mark.parametrize("cause", ["io", "sqlite_locked", "sqlite_other", "untyped_busy", "state_no_cause"])
def test_lifecycle_never_retries_unknown_native_or_untyped_fault(tmp_path, monkeypatch, phase, cause):
    budget, lease = _lifecycle_fixture(tmp_path)
    fault = _actual_unknown_fault(tmp_path, cause)
    actual = getattr(budget, phase)
    attempts, markers, faults = [], [], []
    def unknown(arg):
        attempts.append(arg)
        if cause in {"untyped_busy", "state_no_cause"}:
            raise fault
        def fail_write(_):
            raise fault
        with monkeypatch.context() as patch:
            patch.setattr(budget, "_begin_write", fail_write)
            actual(arg)
    monkeypatch.setattr(budget, phase, unknown)
    with pytest.raises((BudgetBackpressure, sqlite3.OperationalError)) as caught:
        _complete_admitted_wire(budget, lease, faults, markers, time.monotonic()+2)
    assert attempts == [lease] and not faults
    if cause in {"untyped_busy", "state_no_cause"}:
        assert caught.value is fault
    else:
        assert str(caught.value) == "PPI_BUDGET_STATE_UNAVAILABLE" and caught.value.__cause__ is fault
    debt, active = _lease_snapshot(budget, lease, time.monotonic()+2)
    assert active["lease"] == lease and debt[-1] == (phase == "finish")
    assert markers == ([lease] if phase == "finish" else [])
    assert budget.metrics()["global"] == {"requested": 1, "allowed": 1, "used": int(phase == "finish"), "dropped": 0}


@pytest.mark.parametrize("cause", ["io", "sqlite_locked", "state_no_cause"])
def test_burst_never_converts_unknown_acquisition_fault_into_a_denial(tmp_path, monkeypatch, cause):
    path = tmp_path/"budget.sqlite"
    GlobalPPIBudget(path, _lifecycle_policy(), clock=lambda: AT)
    before = _lifecycle_contents(path)
    fault = _actual_unknown_fault(tmp_path, cause)
    def unknown_write(*_):
        raise fault
    monkeypatch.setattr(GlobalPPIBudget, "_begin_write", unknown_write)
    out = SimpleQueue()
    with pytest.raises(BudgetBackpressure) as caught:
        _burst_process(str(path), _lifecycle_policy(), "DISCOVERY", 30, out)
    assert out.empty() and _lifecycle_contents(path) == before
    assert (caught.value is fault if cause == "state_no_cause" else caught.value.__cause__ is fault)


@pytest.mark.parametrize("used", [False, True])
def test_unverifiable_readonly_lease_snapshot_is_fatal_without_any_retry_or_mutation(tmp_path, used):
    budget, lease = _lifecycle_fixture(tmp_path, used=used)
    before = _lifecycle_contents(budget.path)
    markers, faults = ([lease] if used else []), []
    with closing(sqlite3.connect(budget.path)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        try:
            with pytest.raises(sqlite3.OperationalError) as caught:
                if used:
                    _same_lease_finish(budget, lease, faults, time.monotonic()+2, completed=True, wire_leases=markers)
                else:
                    _same_lease_start(budget, lease, faults, time.monotonic()+2)
            assert caught.value.sqlite_errorcode == sqlite3.SQLITE_BUSY and not faults
        finally:
            writer.rollback()
    assert _lifecycle_contents(budget.path) == before


@pytest.mark.parametrize("phase", ["start", "finish"])
@pytest.mark.parametrize("boundary", ["expired_available", "after_read", "after_operation"])
def test_lifecycle_deadlines_before_and_after_reads_and_mutations_remain_fatal(tmp_path, monkeypatch, phase, boundary):
    budget, lease = _lifecycle_fixture(tmp_path, used=phase == "finish")
    before = _lifecycle_contents(budget.path)
    clock = {"at": time.monotonic()}
    deadline = clock["at"]+2
    monkeypatch.setattr(time, "monotonic", lambda: clock["at"])
    attempts, faults = [], []
    actual = getattr(budget, phase)
    def operation(arg):
        attempts.append(arg)
        actual(arg)
        if boundary == "after_operation":
            clock["at"] = deadline+1
    monkeypatch.setattr(budget, phase, operation)
    if boundary == "expired_available":
        clock["at"] = deadline+1
    elif boundary == "after_read":
        actual_snapshot = _lease_snapshot
        def late_read(*args):
            result = actual_snapshot(*args)
            clock["at"] = deadline+1
            return result
        monkeypatch.setattr(sys.modules[__name__], "_lease_snapshot", late_read)
    with pytest.raises(BudgetBackpressure, match="^PPI_STRESS_CONFIRMATION_DEADLINE$"):
        if phase == "finish":
            _same_lease_finish(budget, lease, faults, deadline, completed=True, wire_leases=[lease])
        else:
            _same_lease_start(budget, lease, faults, deadline)
    after = _lifecycle_contents(budget.path)
    assert not faults
    if boundary != "after_operation":
        assert not attempts and after == before
    else:
        assert attempts == [lease]
        assert len(after["budget_requests"]) == 1 and after["budget_requests"][0][-1] == 1
        active = json.loads(dict(after["budget_state"])["inflight"])
        assert active is None if phase == "finish" else active["lease"] == lease


@pytest.mark.parametrize("boundary", ["wire_enter", "modeled_read", "wire_exit"])
def test_wire_scope_and_modeled_read_post_deadlines_cannot_acknowledge_success(tmp_path, monkeypatch, boundary):
    budget, lease = _lifecycle_fixture(tmp_path)
    before = _lifecycle_contents(budget.path)
    clock = {"at": time.monotonic()}
    deadline = clock["at"]+2
    monkeypatch.setattr(time, "monotonic", lambda: clock["at"])
    actual_scope, actual_read = budget.wire_scope, _modeled_read
    @contextmanager
    def scope(arg):
        with actual_scope(arg):
            if boundary == "wire_enter":
                clock["at"] = deadline+1
            yield
        if boundary == "wire_exit":
            clock["at"] = deadline+1
    monkeypatch.setattr(budget, "wire_scope", scope)
    def read(arg, markers):
        result = actual_read(arg, markers)
        if boundary == "modeled_read":
            clock["at"] = deadline+1
        return result
    monkeypatch.setattr(sys.modules[__name__], "_modeled_read", read)
    markers, faults = [], []
    with pytest.raises(BudgetBackpressure, match="^PPI_STRESS_CONFIRMATION_DEADLINE$"):
        _complete_admitted_wire(budget, lease, faults, markers, deadline)
    after = _lifecycle_contents(budget.path)
    assert not faults and markers == ([] if boundary == "wire_enter" else [lease])
    if boundary == "wire_enter":
        assert after == before
    else:
        assert after["budget_requests"][0][-1] == 1
        active = json.loads(dict(after["budget_state"])["inflight"])
        assert active is None if boundary == "wire_exit" else active["lease"] == lease


@pytest.mark.parametrize("failure", ["unverified", "missing_marker", "duplicate_marker", "wrong_marker", "unknown_lease"])
def test_finish_requires_verified_completion_and_one_exact_valid_lease_marker(tmp_path, monkeypatch, failure):
    budget, lease = _lifecycle_fixture(tmp_path, used=True)
    before = _lifecycle_contents(budget.path)
    arg = "unknown" if failure == "unknown_lease" else lease
    markers = ([arg] if failure in {"unverified", "unknown_lease"} else
               [] if failure == "missing_marker" else [lease, lease] if failure == "duplicate_marker" else ["wrong"])
    attempts, faults = [], []
    actual = budget.finish
    def operation(token):
        attempts.append(token)
        actual(token)
    monkeypatch.setattr(budget, "finish", operation)
    with budget.wire_scope(lease):
        with pytest.raises(BudgetBackpressure if failure == "unknown_lease" else AssertionError):
            _same_lease_finish(budget, arg, faults, time.monotonic()+2,
                              completed=failure != "unverified", wire_leases=markers)
    assert not attempts and not faults and _lifecycle_contents(budget.path) == before


@pytest.mark.parametrize("phase", ["start", "finish"])
def test_apparently_busy_after_mutated_state_cannot_retry_or_emit_another_wire(tmp_path, monkeypatch, phase):
    budget, lease = _lifecycle_fixture(tmp_path)
    cause = _actual_unknown_fault(tmp_path, "untyped_busy")
    actual = getattr(budget, phase)
    attempts, markers, faults = [], [], []
    def ambiguous(arg):
        attempts.append(arg)
        actual(arg)
        raise BudgetBackpressure("PPI_BUDGET_STATE_UNAVAILABLE") from cause
    monkeypatch.setattr(budget, phase, ambiguous)
    with pytest.raises(AssertionError):
        _complete_admitted_wire(budget, lease, faults, markers, time.monotonic()+2)
    assert attempts == [lease] and len(faults) == 1
    assert markers == ([lease] if phase == "finish" else [])
    assert budget.metrics()["global"]["used"] == 1
    contents = _lifecycle_contents(budget.path)
    assert len(contents["budget_requests"]) == 1 and contents["budget_requests"][0][-1] == 1
