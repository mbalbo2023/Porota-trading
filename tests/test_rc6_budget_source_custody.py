"""Native PAPER ledger reads preserve main/WAL/SHM custody off provider wire.

Timing faults advance only a monotonic test seam. They are software guard
evidence, never host/PPI latency measurements or an OPEN capacity approval.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
import gc
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import unquote

import pytest

from cg_paper_workspace import artifact_root
from rc6_audit_evidence import sqlite_snapshot
from rc6_dynamic_universe.promotion import RuntimeCapacityController
import rc6_ppi_global_budget as budgets
from rc6_shadow_runtime import source_reads
from scripts.rc6_issue465_stress import source_custody_snapshot
from tests.test_rc6_exit_reader_cadence import native_mixed_store, reviewed_native_capacity
from tests.test_rc6_ppi_capacity_benchmark import wire


@pytest.fixture
def native_ledger(wire, tmp_path, monkeypatch):
    clock = wire[0]
    policy, recommendation, report, approval = reviewed_native_capacity(wire)
    store = native_mixed_store(tmp_path, clock, monkeypatch)
    controller = RuntimeCapacityController(store.path, environ={}, policy=policy,
        recommendation=recommendation, report=report, approval=approval)
    # Finalize native fixture writer cycles before measuring the reader.
    gc.collect()
    with closing(store.connect()) as anchor:
        anchor.execute("SELECT COUNT(*) FROM paper_positions").fetchone()
        yield clock, store, controller


def forbid_primary_sqlite(monkeypatch, database):
    original = sqlite3.connect
    captures = []
    def guarded(value, *args, **kwargs):
        path = Path(unquote(str(value).split("?", 1)[0].removeprefix("file:"))).absolute()
        assert path != Path(database), "PRIMARY_SQLITE_OPEN_FORBIDDEN"
        captures.append(path)
        return original(value, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", guarded)
    return captures


@pytest.mark.parametrize("caller", ("count", "digests", "controller", "runtime", "round"))
def test_native_budget_callers_preserve_primary_custody_and_mixed_exit_math(native_ledger, monkeypatch, caller):
    clock, store, controller = native_ledger
    initial = controller.state(clock.now())
    assert initial["status"] == "APPROVED_DYNAMIC"
    assert initial["exit_capacity"]["open_positions_count"] == 10
    assert initial["exit_capacity"]["exit_demand"]["book"] == 60
    runtime = budgets.RuntimePPIBudget(store.path, controller, clock=clock.now)
    runtime._current(priority="EXIT_CRITICAL")
    before = source_custody_snapshot(store.path)
    assert before["-shm"]["st_size"] > 0, "Native real WAL/SHM must be present"
    captures = forbid_primary_sqlite(monkeypatch, store.path)
    if caller == "count":
        assert budgets.supervisable_position_count(store.path) == 10
    elif caller == "digests":
        assert len(budgets._supervisable_identity_digests(store.path)) == 10
    elif caller == "controller":
        observed = controller.state(clock.now())
        assert observed["exit_capacity"] == initial["exit_capacity"]
        assert observed["fingerprint"] == initial["fingerprint"]
    elif caller == "runtime":
        # The inherited producer cadence covers report validation too; each
        # ledger capture independently remains capped at its new0.15s bound.
        budget = runtime._current(priority="EXIT_CRITICAL", deadline=time.monotonic()+5)
        assert budget.policy["open_positions_count"] == 10
        assert budget.policy["exit_demand"]["book"] == 60
    else:
        observed = runtime.observe_exit_round(elapsed_seconds=.1, deadline_seconds=5)
        # No books were served: observing the correct scope cannot invent them.
        assert observed["status"] == "DEGRADED" and observed["lower_suspended"]
        assert observed["required_identities_count"] == 10
        assert observed["covered_identities_count"] == 0
    assert captures
    assert source_custody_snapshot(store.path) == before
    assert all(not path.exists() for path in captures if path.name == "snapshot.sqlite")


@pytest.mark.parametrize("caller", ("count", "digests", "controller", "runtime"))
def test_inherited_expired_deadline_never_captures_primary_or_becomes_zero(native_ledger, monkeypatch, caller):
    clock, store, controller = native_ledger
    before = source_custody_snapshot(store.path)
    monkeypatch.setattr(source_reads, "readonly_copy", lambda *a, **kw: pytest.fail("Expired capture started"))
    expired = time.monotonic()-1
    if caller == "count":
        assert budgets.supervisable_position_count(store.path, deadline=expired) is None
        assert budgets.supervisable_position_count(store.path, 777, deadline=expired) == 777
    elif caller == "digests":
        assert budgets._supervisable_identity_digests(store.path, deadline=expired) is None
    elif caller == "controller":
        state = controller.state(clock.now(), deadline=expired)
        assert state["status"] == "BASELINE_FAIL_CLOSED"
        assert state["reason_codes"] == ["CAPACITY_OPENED_LEDGER_UNVERIFIED"]
        assert not state["production_limits_modified"]
    else:
        runtime = budgets.RuntimePPIBudget(store.path, controller, clock=clock.now)
        with pytest.raises(budgets.BudgetBackpressure, match="OPENED_LEDGER_UNVERIFIED"):
            runtime._current(priority="DISCOVERY", deadline=expired)
        assert runtime.budget is None
    assert source_custody_snapshot(store.path) == before


@pytest.mark.parametrize("inherited,latency", ((2., .2), (.01, .02)))
def test_capture_cannot_reset_the_new_total_guard_or_an_earlier_deadline(native_ledger, monkeypatch, inherited, latency):
    _, store, _ = native_ledger
    before = source_custody_snapshot(store.path)
    monotonic = [time.monotonic()]
    original_read = sqlite_snapshot._read
    def delayed_read(*args, **kwargs):
        value = original_read(*args, **kwargs)
        monotonic[0] += latency
        return value
    monkeypatch.setattr(time, "monotonic", lambda: monotonic[0])
    monkeypatch.setattr(sqlite_snapshot, "_read", delayed_read)
    captures = forbid_primary_sqlite(monkeypatch, store.path)
    assert budgets.supervisable_position_count(store.path, deadline=monotonic[0]+inherited) is None
    assert not captures, "Deadline must expire during capture before private SQLite opens"
    assert source_custody_snapshot(store.path) == before


def test_mandatory_cleanup_time_cannot_turn_an_expired_capture_into_success(native_ledger, monkeypatch):
    _, store, _ = native_ledger
    before = source_custody_snapshot(store.path)
    monotonic = [time.monotonic()]
    actual = source_reads.source_connection
    @contextmanager
    def delayed_cleanup(*args, **kwargs):
        with actual(*args, **kwargs) as value:
            yield value
        monotonic[0] += .2
    monkeypatch.setattr(time, "monotonic", lambda: monotonic[0])
    monkeypatch.setattr(source_reads, "source_connection", delayed_cleanup)
    captures = forbid_primary_sqlite(monkeypatch, store.path)
    assert budgets.supervisable_position_count(store.path) is None
    assert captures and all(not path.exists() for path in captures)
    assert source_custody_snapshot(store.path) == before


@pytest.mark.parametrize("alias", ("symlink", "parent_symlink", "hardlink"))
def test_ledger_alias_is_unknown_without_source_sqlite_or_repair(native_ledger, tmp_path, monkeypatch, alias):
    clock, store, controller = native_ledger
    database = Path(store.path)
    candidate = tmp_path / "alias.sqlite"
    if alias == "symlink":
        candidate.symlink_to(database)
    elif alias == "parent_symlink":
        candidate = tmp_path / "alias-parent"
        candidate.symlink_to(database.parent, target_is_directory=True)
        candidate = candidate / database.name
    else:
        os.link(database, candidate)
    before = source_custody_snapshot(database) if alias != "hardlink" else None
    monkeypatch.setattr(source_reads, "readonly_copy", lambda *a, **kw: pytest.fail("Alias copied"))
    try:
        assert budgets.supervisable_position_count(candidate) is None
        assert budgets._supervisable_identity_digests(candidate) is None
        controller.database = candidate
        state = controller.state(clock.now())
        assert state["status"] == "BASELINE_FAIL_CLOSED"
        assert state["reason_codes"] == ["CAPACITY_OPENED_LEDGER_UNVERIFIED"]
        if before is not None:
            assert source_custody_snapshot(database) == before
    finally:
        (candidate.parent if alias == "parent_symlink" else candidate).unlink()


def test_shared_scratch_quota_failure_blocks_approval_without_mutating_primary(native_ledger, monkeypatch):
    clock, store, controller = native_ledger
    before = source_custody_snapshot(store.path)
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(store.path))
    monkeypatch.setenv("POROTA_SQLITE_SCRATCH_ROOT", str(artifact_root(store.path)/"sqlite-read-scratch"))
    monkeypatch.setenv("POROTA_SQLITE_SCRATCH_MAX_BYTES", "1")
    monkeypatch.setenv("POROTA_SQLITE_SCRATCH_RESERVE_BYTES", str(2*1024*1024*1024))
    monkeypatch.setenv("POROTA_SQLITE_SCRATCH_MIN_FREE_INODE_PERCENT", "10")
    assert budgets.supervisable_position_count(store.path) is None
    assert budgets._supervisable_identity_digests(store.path) is None
    state = controller.state(clock.now())
    assert state["status"] == "BASELINE_FAIL_CLOSED"
    assert state["reason_codes"] == ["CAPACITY_OPENED_LEDGER_UNVERIFIED"]
    assert source_custody_snapshot(store.path) == before


def test_actual_native_writer_during_capture_stays_unknown_without_reader_source_effects(native_ledger, monkeypatch):
    _, store, _ = native_ledger
    actual = sqlite_snapshot._read
    writer_after = []
    def interleaved(member, expected, **kwargs):
        result = actual(member, expected, **kwargs)
        if Path(member) == Path(store.path) and not writer_after:
            # This intentional fixture writer is distinct from the reader.
            store.state(process_state="SOURCE_CHANGED_DURING_CAPTURE")
            gc.collect()
            writer_after.append(source_custody_snapshot(store.path))
        return result
    monkeypatch.setattr(sqlite_snapshot, "_read", interleaved)
    assert budgets.supervisable_position_count(store.path) is None
    assert writer_after
    assert source_custody_snapshot(store.path) == writer_after[0]


@pytest.mark.parametrize("mode", ("OFF", "SHADOW"))
def test_baseline_capacity_state_does_not_capture_a_ledger_even_with_expired_deadline(native_ledger, monkeypatch, mode):
    clock, store, controller = native_ledger
    controller.inputs["policy"] = deepcopy(controller.inputs["policy"])
    controller.inputs["policy"]["mode"] = mode
    before = source_custody_snapshot(store.path)
    monkeypatch.setattr(source_reads, "readonly_copy", lambda *a, **kw: pytest.fail("Baseline captured source"))
    state = controller.state(clock.now(), deadline=time.monotonic()-1)
    assert state["status"] == mode+"_BASELINE"
    assert state["real_orders_sent"] == 0
    assert source_custody_snapshot(store.path) == before


def test_native_hot_rollback_journal_is_unknown_until_writer_finishes(wire, tmp_path, monkeypatch):
    from tests.test_issue465_budget_adversarial import native_opened_store
    store, _ = native_opened_store(tmp_path, wire[0], monkeypatch, count=5)
    gc.collect()
    with closing(sqlite3.connect(store.path)) as writer:
        assert writer.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE observer_state SET process_state='SOURCE_CAPTURE_BUSY_TEST' WHERE id=1")
        before = source_custody_snapshot(store.path)
        assert before["-journal"]["st_size"] > 0
        with monkeypatch.context() as read_guard:
            forbid_primary_sqlite(read_guard, store.path)
            assert budgets.supervisable_position_count(store.path) is None
            assert budgets._supervisable_identity_digests(store.path) is None
            assert source_custody_snapshot(store.path) == before
        writer.rollback()
    assert budgets.supervisable_position_count(store.path) == 5


def test_observer_includes_its_own_capture_time_and_never_borrows_another_round(native_ledger, monkeypatch):
    clock, store, controller = native_ledger
    runtime = budgets.RuntimePPIBudget(store.path, controller, clock=clock.now)
    budget = runtime._current(priority="EXIT_CRITICAL")
    identities = budgets._supervisable_identity_digests(store.path)
    # Existing debt must remain suspended when no complete round is verified.
    budget.observe_exit_round(elapsed_seconds=6, deadline_seconds=5, required_identity_digests=identities)
    monotonic = [time.monotonic()]
    actual = source_reads.source_connection
    @contextmanager
    def delayed_capture(*args, **kwargs):
        with actual(*args, **kwargs) as value:
            monotonic[0] += .02
            yield value
    before = source_custody_snapshot(store.path)
    monkeypatch.setattr(time, "monotonic", lambda: monotonic[0])
    monkeypatch.setattr(source_reads, "source_connection", delayed_capture)
    result = runtime.observe_exit_round(elapsed_seconds=4.99, deadline_seconds=5)
    assert result["status"] == "DEGRADED" and result["lower_suspended"]
    assert result["elapsed_seconds"] > 5
    assert result["reason"] == "PPI_EXIT_ROUND_DEADLINE_EXCEEDED"
    assert budget.metrics()["exit_service"]["lower_suspended"]
    assert source_custody_snapshot(store.path) == before
