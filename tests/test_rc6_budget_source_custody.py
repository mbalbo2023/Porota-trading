"""Native PAPER ledger reads preserve main/WAL/SHM custody off provider wire.

Timing faults advance only a monotonic test seam. They are software guard
evidence, never host/PPI latency measurements or an OPEN capacity approval.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import gc
import json
import os
from pathlib import Path
import sqlite3
import time
import threading
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
from tests.test_issue465_budget_adversarial import scoped_book


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


@pytest.mark.parametrize("scenario", ("verified", "no_admission", "unverified_source", "discarded", "read_error", "foreign_observer"))
def test_late_native_round_keeps_only_its_verified_first_admission_count(native_ledger, wire, monkeypatch, scenario):
    from bd_ppi_readonly_guard import ProductionMarketReader
    clock, store, controller = native_ledger
    runtime = budgets.RuntimePPIBudget(store.path, controller, clock=clock.now)
    budget = runtime._current(priority="EXIT_CRITICAL")
    original_policy = deepcopy(budget.policy)
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=runtime, consumer="EXIT_READER")
    try:
        reader.login_once()
        if scenario == "unverified_source":
            # A warm budget retains its old EXIT floor; a missing native
            # source cannot create first-admission evidence from that policy.
            primary = Path(store.path)
            held = primary.with_suffix(".held-offline-fixture")
            primary.rename(held)
            try:
                assert scoped_book(reader, identity, "EXIT_CRITICAL")["bids"]
            finally:
                held.rename(primary)
        elif scenario == "read_error":
            wire[2]["status"] = 503
            with pytest.raises(Exception):
                scoped_book(reader, identity, "EXIT_CRITICAL")
        elif scenario != "no_admission":
            assert scoped_book(reader, identity, "EXIT_CRITICAL")["bids"]
        if scenario == "discarded":
            reader.discard_exit_round_scope()
        before = source_custody_snapshot(store.path)
        with closing(sqlite3.connect(runtime.path)) as c:
            receipts = c.execute("SELECT * FROM budget_requests ORDER BY lease").fetchall()
        # No producer deadline remains. Even diagnostics must not capture a
        # new PRIMARY snapshot just to recover the historical display count.
        actual_copy = source_reads.readonly_copy
        monkeypatch.setattr(source_reads, "readonly_copy", lambda *a, **kw: pytest.fail("Late round captured source"))
        if scenario == "foreign_observer":
            with ThreadPoolExecutor(max_workers=1) as pool:
                observed = pool.submit(reader.observe_exit_round, elapsed_seconds=6, deadline_seconds=5).result(5)
        else:
            observed = reader.observe_exit_round(elapsed_seconds=6, deadline_seconds=5)
        assert observed["status"] == "DEGRADED" and observed["lower_suspended"]
        assert observed["fresh_required_count"] is None
        assert observed["required_scope_verified_current"] is False
        assert observed["required_identities_count"] == (10 if scenario == "verified" else None)
        assert observed["required_scope_basis"] == ("FROZEN_AT_FIRST_ADMISSION" if scenario == "verified" else "UNVERIFIED")
        if scenario == "verified":
            frozen = observed["frozen_admission_scope"]
            assert frozen["identity_count"] == frozen["reserved_open_positions_count"] == 10
            assert frozen["reserved_exit_demand"]["book"] == 60
            assert frozen["configuration_fingerprint"] == original_policy["configuration_fingerprint"]
            assert frozen["recommendation_digest"] == original_policy["recommendation_digest"]
            assert frozen["expires_at"] == original_policy["expires_at"]
        else:
            assert observed["frozen_admission_scope"] is None
        snapshot = budgets.runtime_budget_snapshot(store.path, as_of=clock.now())
        assert snapshot["status"] == "DEGRADED"
        assert snapshot["exit_service"]["round"] == {k: v for k, v in observed.items() if k != "lower_suspended"}
        assert source_custody_snapshot(store.path) == before
        assert budget.policy == original_policy
        with closing(sqlite3.connect(runtime.path)) as c:
            assert c.execute("SELECT * FROM budget_requests ORDER BY lease").fetchall() == receipts
        # Consumed, discarded, failed and foreign scopes never leak into the
        # following producer round, even while the old policy still says10.
        following = reader.observe_exit_round(elapsed_seconds=6, deadline_seconds=5)
        assert following["required_identities_count"] is None
        assert following["frozen_admission_scope"] is None and following["lower_suspended"]
        # A later LOWER decision has its own .15s custody bound; it may read
        # current scope, but cannot borrow the unresolved EXIT round's floor.
        monkeypatch.setattr(source_reads, "readonly_copy", actual_copy)
        assert not runtime.acquire("current", consumer="SCANNER", priority="DISCOVERY")["allowed"]
    finally:
        reader.discard_exit_round_scope()
        reader.close()


def test_native_current_future_scope_cannot_be_replaced_by_the_first_admission_subset(native_ledger, wire):
    from bd_ppi_readonly_guard import ProductionMarketReader
    from be_paper_engine import D, PaperBroker
    from tests.test_production_paper_v1634 import quote
    clock, store, controller = native_ledger
    initial = controller.state(clock.now())
    assert initial["status"] == "APPROVED_DYNAMIC"
    assert initial["exit_capacity"]["open_positions_count"] == 10
    # Precondition comes from the real capacity arithmetic, with the default
    # quota unchanged. Endpoint66 is feasible; its retained receipts are not.
    growth_contract = budgets.exit_capacity_contract(initial, opened_count=11)
    assert initial["budget_settings"]["maximum_bytes"] == 8 * 1024**2
    assert growth_contract["exit_demand"]["book"] == 66
    assert growth_contract["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
    assert growth_contract["reason_codes"] == ["PPI_EXIT_RECEIPT_STORAGE_INSUFFICIENT"]
    assert growth_contract["receipt_retention"]["storage_gap_bytes"] > 0
    runtime = budgets.RuntimePPIBudget(store.path, controller, clock=clock.now)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=runtime, consumer="EXIT_READER")
    # Use the actual durable five-key contracts, including their settlement;
    # an advisory list is never the required primary scope.
    positions, invalid = store.exit_positions()
    assert not invalid
    positions += [{**p, "asset_class": "FUTUROS"} for p in store.active_future_positions()]
    identities = [(p["symbol"], p["asset_class"], p["market"], p["currency"], p["settlement"]) for p in positions]
    try:
        reader.login_once()
        for identity in identities:
            assert scoped_book(reader, identity, "EXIT_CRITICAL")["bids"]
        original_policy = deepcopy(runtime.budget.policy)
        assert original_policy["open_positions_count"] == 10
        assert original_policy["exit_demand"]["book"] == 60
        with closing(sqlite3.connect(runtime.path)) as c:
            receipts = c.execute("SELECT * FROM budget_requests ORDER BY lease").fetchall()
        # A fresh quote cannot remove the five futures' overnight carry. The
        # actual admission API must preserve its risk veto and count10.
        q = quote(symbol="EXIT_FIRST_ADMISSION_CHANGE", at=clock.now().isoformat())
        store.add_quote(q)
        broker = PaperBroker(store, clock_fn=lambda: clock.now().isoformat())
        opened = broker._open(q, D(".8"), {})
        assert opened == (False, "DAILY_RISK_STALE_MARKS", None)
        assert budgets.supervisable_position_count(store.path) == 10
        # DRIVER_ADVERSARIAL_PRIMARY_LEDGER_WRITER, NO_ENTRY_AUTHORITY:
        # inject one well-typed five-key row into this isolated PRIMARY only.
        # This is a ledger growth attack, never a financial fill or authority
        # to enter. Cash, risk, contracts, fills and all other tables are kept.
        with closing(store.connect()) as c, c:
            columns = [row[1] for row in c.execute("PRAGMA table_info(paper_positions)")]
            row = dict(c.execute("SELECT * FROM paper_positions WHERE symbol='GGAL' AND status='OPEN'").fetchone())
            row.update(paper_id="PAPER-ADVERSARIAL-NO-ENTRY-AUTHORITY", symbol=q.symbol,
                asset_class="ACCIONES", market="BYMA", currency="ARS", settlement="A-24HS",
                status="OPEN", opened_at=clock.now().isoformat(),
                features_json=json.dumps({"fixture_kind": "DRIVER_ADVERSARIAL_PRIMARY_LEDGER_WRITER",
                    "entry_authority": False, "real_orders_sent": 0}, sort_keys=True))
            for name in ("closed_at", "exit_price", "exit_cost", "gross_pnl", "net_pnl", "close_reason"):
                row[name] = None
            def sql_name(name):
                return '"' + name.replace('"', '""') + '"'
            other_tables = [name for name, in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                if name != "paper_positions"]
            before_tables = {name: [tuple(r) for r in c.execute("SELECT * FROM " + sql_name(name))]
                for name in other_tables}
            c.execute("INSERT INTO paper_positions (" + ",".join(map(sql_name, columns)) + ") VALUES ("
                + ",".join("?" for _ in columns) + ")", [row[name] for name in columns])
            assert {name: [tuple(r) for r in c.execute("SELECT * FROM " + sql_name(name))]
                for name in other_tables} == before_tables
            assert tuple(c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()) == ("PRODUCTION_PAPER", 0)
        assert len(store.open_positions()) == 6 and len(store.active_future_positions()) == 5
        gc.collect()  # The adversarial fixture writer is complete, not a fill.
        assert budgets.supervisable_position_count(store.path) == 11
        assert len(budgets._supervisable_identity_digests(store.path)) == 11
        changed = controller.state(clock.now())
        assert changed["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
        assert changed["exit_capacity"] == growth_contract
        observed = reader.observe_exit_round(elapsed_seconds=.1, deadline_seconds=5)
        assert observed["status"] == "DEGRADED" and observed["lower_suspended"]
        assert observed["reason"] == "PPI_EXIT_ROUND_INCOMPLETE_OR_UNVERIFIED"
        assert observed["frozen_admission_scope"]["identity_count"] == 10
        assert observed["required_identities_count"] == 10
        assert observed["fresh_required_count"] is None
        assert observed["required_scope_verified_current"] is False
        assert observed["required_scope_basis"] == "FROZEN_AT_FIRST_ADMISSION"
        assert observed["covered_identities_count"] == 0
        assert runtime.activation_contract["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
        assert "PPI_EXIT_RECEIPT_STORAGE_INSUFFICIENT" in runtime.activation_contract["reason_codes"]
        assert runtime.budget.policy == original_policy
        metrics = runtime.budget.metrics()
        assert metrics["exit_service"]["lower_suspended"]
        assert metrics["window"]["by_endpoint"]["book"]["exit_demand"] == 60
        with closing(sqlite3.connect(runtime.path)) as c:
            assert c.execute("SELECT * FROM budget_requests ORDER BY lease").fetchall() == receipts
        wires = len(wire[1])
        with pytest.raises(budgets.BudgetBackpressure, match="PPI_EXIT_CAPACITY_INSUFFICIENT"):
            runtime.acquire("current", consumer="SCANNER", priority="DISCOVERY")
        assert len(wire[1]) == wires
        # Warm EXIT retains the old budget. A blocked next generation cannot
        # mint an approved11 scope or inherit the consumed historical10.
        reader.discard_exit_round_scope()
        assert scoped_book(reader, identities[0], "EXIT_CRITICAL")["bids"]
        late = reader.observe_exit_round(elapsed_seconds=6, deadline_seconds=5)
        assert late["required_identities_count"] is None
        assert late["frozen_admission_scope"] is None
        assert late["fresh_required_count"] is None and not late["required_scope_verified_current"]
        assert late["status"] == "DEGRADED" and late["lower_suspended"]
    finally:
        reader.discard_exit_round_scope()
        reader.close()


def test_same_native_count_with_changed_identity_never_clears_from_frozen_scope(native_ledger):
    from bd_ppi_readonly_guard import ProductionMarketReader
    clock, store, controller = native_ledger
    runtime = budgets.RuntimePPIBudget(store.path, controller, clock=clock.now)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=runtime, consumer="EXIT_READER")
    positions, invalid = store.exit_positions()
    assert not invalid
    positions += [{**p, "asset_class": "FUTUROS"} for p in store.active_future_positions()]
    identities = [(p["symbol"], p["asset_class"], p["market"], p["currency"], p["settlement"]) for p in positions]
    try:
        reader.login_once()
        for identity in identities:
            assert scoped_book(reader, identity, "EXIT_CRITICAL")["bids"]
        # Adversarial SQL writer over the isolated native fixture. This is an
        # identity drift attack, not a financial fill or a provider contract.
        with store.connect() as c, c:
            c.execute("UPDATE paper_positions SET symbol='OFFLINE_IDENTITY_DRIFT' WHERE symbol='EXIT4' AND status='OPEN'")
        assert budgets.supervisable_position_count(store.path) == 10
        observed = reader.observe_exit_round(elapsed_seconds=.1, deadline_seconds=5)
        assert observed["status"] == "DEGRADED" and observed["lower_suspended"]
        assert observed["reason"] == "PPI_EXIT_ROUND_INCOMPLETE_OR_UNVERIFIED"
        assert observed["frozen_admission_scope"]["identity_count"] == 10
        assert observed["required_identities_count"] == observed["fresh_required_count"] == 10
        assert observed["required_scope_verified_current"] is True
        assert observed["covered_identities_count"] == 9
        assert runtime.budget.policy["exit_demand"]["book"] == 60
        assert not runtime.acquire("current", consumer="SCANNER", priority="DISCOVERY")["allowed"]
    finally:
        reader.discard_exit_round_scope()
        reader.close()


@pytest.mark.parametrize("discard_while_active", (False, True))
def test_native_concurrent_admission_cannot_share_frozen_round_authority_or_duplicate_wire(native_ledger, wire, monkeypatch, discard_while_active):
    import requests
    from bd_ppi_readonly_guard import ProductionMarketReader
    clock, store, controller = native_ledger
    runtime = budgets.RuntimePPIBudget(store.path, controller, clock=clock.now)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=runtime, consumer="EXIT_READER")
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    entered, release, second = threading.Event(), threading.Event(), threading.Event()
    original_send = requests.adapters.HTTPAdapter.send
    original_freeze = runtime._freeze_exit_admission
    def blocked_body(adapter, request, **kwargs):
        if request.url.split("?", 1)[0].lower().endswith("/book"):
            entered.set()
            assert release.wait(5)
        return original_send(adapter, request, **kwargs)
    def traced_freeze(scope, budget):
        result = original_freeze(scope, budget)
        if entered.is_set():
            second.set()
        return result
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", blocked_body)
    monkeypatch.setattr(runtime, "_freeze_exit_admission", traced_freeze)
    try:
        reader.login_once()
        before = len(wire[1])
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(scoped_book, reader, identity, "EXIT_CRITICAL")
            assert entered.wait(5)
            if discard_while_active:
                reader.discard_exit_round_scope()
            following = pool.submit(scoped_book, reader, identity, "EXIT_CRITICAL")
            try:
                assert second.wait(5)
                observed = reader.observe_exit_round(elapsed_seconds=.1, deadline_seconds=5)
                assert observed["status"] == "DEGRADED" and observed["lower_suspended"]
                assert observed["required_identities_count"] is None
                assert observed["frozen_admission_scope"] is None
                assert observed["fresh_required_count"] is None
                assert not observed["required_scope_verified_current"]
            finally:
                release.set()
            assert first.result(5)["bids"] and following.result(5)["bids"]
        assert len(wire[1]) - before == 1
        assert runtime.budget.metrics()["global"]["used"] == 1
        assert runtime.budget.metrics()["exit_service"]["lower_suspended"]
        assert runtime.budget.policy["exit_demand"]["book"] == 60
    finally:
        release.set()
        reader.discard_exit_round_scope()
        reader.close()


@pytest.mark.parametrize("writer_mode", ("IMMEDIATE", "EXCLUSIVE"))
def test_committed_native_exit_follower_does_not_compete_with_writer_or_use_uncertain_state(native_ledger, wire, monkeypatch, writer_mode):
    import requests
    from bd_ppi_readonly_guard import ProductionMarketReader
    clock, store, controller = native_ledger
    runtime = budgets.RuntimePPIBudget(store.path, controller, clock=clock.now)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=runtime, consumer="EXIT_READER")
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    entered, release, polling, writer_held, polled_under_writer = [threading.Event() for _ in range(5)]
    follower = {"thread": None, "write_begins": 0}
    original_send = requests.adapters.HTTPAdapter.send
    original_begin = budgets.GlobalPPIBudget._begin_write
    original_pending = budgets.GlobalPPIBudget._book_flight_pending

    def blocked_body(adapter, request, **kwargs):
        if request.url.split("?", 1)[0].lower().endswith("/book"):
            entered.set()
            assert release.wait(5)
        return original_send(adapter, request, **kwargs)

    def traced_begin(budget, connection):
        if threading.get_ident() == follower["thread"]:
            follower["write_begins"] += 1
        return original_begin(budget, connection)

    def traced_pending(budget, *args, **kwargs):
        result = original_pending(budget, *args, **kwargs)
        if result[0] and threading.get_ident() == follower["thread"]:
            polling.set()
            if writer_held.is_set():
                polled_under_writer.set()
        return result

    def following_book():
        follower["thread"] = threading.get_ident()
        return scoped_book(reader, identity, "EXIT_CRITICAL")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", blocked_body)
    monkeypatch.setattr(budgets.GlobalPPIBudget, "_begin_write", traced_begin)
    monkeypatch.setattr(budgets.GlobalPPIBudget, "_book_flight_pending", traced_pending)
    try:
        reader.login_once()
        before = len(wire[1])
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(scoped_book, reader, identity, "EXIT_CRITICAL")
            assert entered.wait(5)
            following = pool.submit(following_book)
            try:
                assert polling.wait(5), "Follower must commit its authority and EXIT pressure before polling"
                assert follower["write_begins"] == 2
                with closing(sqlite3.connect(runtime.path, timeout=.05)) as writer:
                    writer.execute("BEGIN " + writer_mode)
                    writer.execute("UPDATE budget_state SET value=value WHERE key='schema'")
                    writer_held.set()
                    if writer_mode == "IMMEDIATE":
                        assert polled_under_writer.wait(1), "Committed waiting snapshot should remain readable"
                        # Hold a real reserved writer beyond the original 50ms
                        # write wait. No new admission or cached value is used.
                        time.sleep(.075)
                        assert not following.done()
                        assert follower["write_begins"] == 2
                    else:
                        # An unreadable snapshot retains the original bounded
                        # fail-closed behavior; polling cannot serve old bytes.
                        started = time.monotonic()
                        with pytest.raises(budgets.BudgetBackpressure, match="STATE_UNAVAILABLE") as denied:
                            following.result(1)
                        assert time.monotonic() - started < 1
                        assert isinstance(denied.value.__cause__, sqlite3.OperationalError)
                        assert denied.value.__cause__.sqlite_errorcode == sqlite3.SQLITE_BUSY
                        assert follower["write_begins"] == 2
                    assert len(wire[1]) == before
                    writer.rollback()
            finally:
                writer_held.clear()
                release.set()
            assert first.result(5)["bids"]
            if writer_mode == "IMMEDIATE":
                assert following.result(5)["bids"]
                assert follower["write_begins"] == 3
        assert len(wire[1]) - before == 1
        assert runtime.budget.metrics()["global"]["used"] == 1
        observed = reader.observe_exit_round(elapsed_seconds=.1, deadline_seconds=5)
        assert observed["status"] == "DEGRADED" and observed["lower_suspended"]
        assert observed["frozen_admission_scope"] is None
        assert not observed["required_scope_verified_current"]
    finally:
        release.set()
        reader.discard_exit_round_scope()
        reader.close()


def test_committed_follower_rejects_clock_rollback_between_readonly_observations(native_ledger, wire, monkeypatch):
    import requests
    from bd_ppi_readonly_guard import ProductionMarketReader
    clock, store, controller = native_ledger
    runtime = budgets.RuntimePPIBudget(store.path, controller, clock=clock.now)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=runtime, consumer="EXIT_READER")
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    entered, release = threading.Event(), threading.Event()
    follower = {"thread": None, "observations": [], "committed_floor": None}
    original_send = requests.adapters.HTTPAdapter.send
    original_pending = budgets.GlobalPPIBudget._book_flight_pending

    def blocked_body(adapter, request, **kwargs):
        if request.url.split("?", 1)[0].lower().endswith("/book"):
            entered.set()
            assert release.wait(5)
        return original_send(adapter, request, **kwargs)

    def observed_pending(budget, *args, **kwargs):
        result = original_pending(budget, *args, **kwargs)
        if threading.get_ident() == follower["thread"]:
            assert result[0]
            follower["observations"].append(result[1])
            with closing(budget._connect()) as connection:
                committed = budget._get(connection, "last_clock", None)
            if len(follower["observations"]) == 1:
                follower["committed_floor"] = committed
                clock.advance(1)
            elif len(follower["observations"]) == 2:
                assert committed == follower["committed_floor"]
                assert result[1] > committed
                clock.advance(-.5)  # Still above committed floor, below the prior read.
        return result

    def following_book():
        follower["thread"] = threading.get_ident()
        return scoped_book(reader, identity, "EXIT_CRITICAL")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", blocked_body)
    monkeypatch.setattr(budgets.GlobalPPIBudget, "_book_flight_pending", observed_pending)
    try:
        reader.login_once()
        before = len(wire[1])
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(scoped_book, reader, identity, "EXIT_CRITICAL")
            assert entered.wait(5)
            following = pool.submit(following_book)
            try:
                with pytest.raises(budgets.BudgetBackpressure, match="PPI_BUDGET_CLOCK_ROLLBACK"):
                    following.result(5)
                assert len(follower["observations"]) == 2
                assert len(wire[1]) == before
            finally:
                clock.advance(.5)
                release.set()
            assert first.result(5)["bids"]
        assert len(wire[1]) - before == 1
        assert runtime.budget.metrics()["global"]["used"] == 1
    finally:
        release.set()
        reader.discard_exit_round_scope()
        reader.close()


def test_pending_native_follower_samples_clock_after_acquiring_committed_snapshot(native_ledger, wire, monkeypatch):
    import requests
    from bd_ppi_readonly_guard import ProductionMarketReader
    clock, store, controller = native_ledger
    runtime = budgets.RuntimePPIBudget(store.path, controller, clock=clock.now)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=runtime, consumer="EXIT_READER")
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    entered, release = threading.Event(), threading.Event()
    original_send = requests.adapters.HTTPAdapter.send
    original_get = budgets.GlobalPPIBudget._get
    observer = threading.get_ident()
    injected = []

    def blocked_body(adapter, request, **kwargs):
        if request.url.split("?", 1)[0].lower().endswith("/book"):
            entered.set()
            assert release.wait(5)
        return original_send(adapter, request, **kwargs)

    def finishing_writer_before_snapshot(budget, connection, key, *args):
        if threading.get_ident() == observer and key == "last_clock" and not injected:
            injected.append(True)
            clock.advance(1)
            with closing(budget._connect()) as writer, writer:
                budget._begin_write(writer)
                budget._clock(writer)
        return original_get(connection, key, *args)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", blocked_body)
    try:
        reader.login_once()
        before = len(wire[1])
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(scoped_book, reader, identity, "EXIT_CRITICAL")
            assert entered.wait(5)
            try:
                budget = runtime.budget
                key = budgets.digest(list(identity))
                authority = budgets.digest({name: budget.policy[name] for name in
                    ("configuration_fingerprint", "recommendation_digest")})
                with closing(budget._connect()) as connection:
                    floor = original_get(connection, "last_clock", None)
                    flight = original_get(connection, "critical_book_flights", {})[key]
                with monkeypatch.context() as patch:
                    patch.setattr(budgets.GlobalPPIBudget, "_get", finishing_writer_before_snapshot)
                    pending, observed_at = budget._book_flight_pending(key, flight["lease"], authority, 5,
                        critical=True, last_observed_clock=floor)
                assert injected == [True] and pending is True
                assert observed_at == clock.now().timestamp() and observed_at > floor
                assert len(wire[1]) == before
            finally:
                release.set()
            assert first.result(5)["bids"]
        assert len(wire[1]) - before == 1
        assert runtime.budget.metrics()["global"]["used"] == 1
    finally:
        release.set()
        reader.discard_exit_round_scope()
        reader.close()


def test_round_rejects_impossible_frozen_capture_clock_in_writer_and_reader(native_ledger):
    from datetime import timedelta
    from bd_ppi_readonly_guard import ProductionMarketReader
    clock, store, controller = native_ledger
    runtime = budgets.RuntimePPIBudget(store.path, controller, clock=clock.now)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=runtime, consumer="EXIT_READER")
    try:
        reader.login_once()
        assert scoped_book(reader, ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS"), "EXIT_CRITICAL")["bids"]
        valid = reader.observe_exit_round(elapsed_seconds=6, deadline_seconds=5)
        impossible = deepcopy(valid["frozen_admission_scope"])
        impossible["captured_at"] = (clock.now() + timedelta(microseconds=1)).isoformat()
        rejected = runtime.budget.observe_exit_round(elapsed_seconds=6, deadline_seconds=5,
            required_identity_digests=None, frozen_admission_scope=impossible)
        assert rejected["status"] == "DEGRADED" and rejected["lower_suspended"]
        assert "required_identities_count" not in rejected
        # Mutate only a synthetic writer-owned sidecar receipt to exercise the
        # independent read projection; no primary or production DB is changed.
        with closing(sqlite3.connect(runtime.path)) as c, c:
            record = runtime.budget._get(c, "critical_exit_round")
            record["frozen_admission_scope"] = impossible
            runtime.budget._put(c, "critical_exit_round", record)
        assert budgets.runtime_budget_snapshot(store.path, as_of=clock.now())["status"] == "UNVERIFIED"
        assert runtime.budget.metrics()["exit_service"]["lower_suspended"]
    finally:
        reader.discard_exit_round_scope()
        reader.close()


@pytest.mark.parametrize("fault", ("other_valid_bindings", "valid_empty_historical_scope", "identity_bool", "identity_over_limit",
    "reserved_too_small", "demand_bool", "demand_negative", "demand_missing", "configuration_malformed", "recommendation_malformed"))
def test_historical_metadata_schema_never_authorizes_an_unknown_current_scope(tmp_path, fault):
    """Durable metadata schema guard; payload is synthetic, not a ledger capture."""
    from rc6_dynamic_universe.common import digest
    from tests.test_rc6_ppi_capacity_benchmark import Clock
    from tests.test_rc6_ppi_global_budget import policy
    clock = Clock()
    database = tmp_path/"paper.sqlite"
    value = policy(clock, limits=dict.fromkeys(budgets.ENDPOINTS, 75), reserves={"EXIT_CRITICAL": {"book": 60}})
    value.update(open_positions_count=10, exit_demand={"current": 0, "book": 60, "intraday": 0})
    budget = budgets.GlobalPPIBudget(artifact_root(database)/"ppi-budget/global.sqlite", value, clock=clock.now)
    historical = {"basis": "FROZEN_AT_FIRST_ADMISSION", "identity_count": 10,
        "identity_scope_digest": "c"*64, "captured_at": clock.now().isoformat(),
        "configuration_fingerprint": value["configuration_fingerprint"], "recommendation_digest": value["recommendation_digest"],
        "expires_at": value["expires_at"], "reserved_open_positions_count": 10,
        "reserved_exit_demand": deepcopy(value["exit_demand"])}
    control = budget.observe_exit_round(elapsed_seconds=.1, deadline_seconds=5,
        required_identity_digests=None, frozen_admission_scope=historical)
    assert control["status"] == "DEGRADED" and control["lower_suspended"]
    assert control["fresh_required_count"] is None and not control["required_scope_verified_current"]
    changed = deepcopy(historical)
    if fault == "other_valid_bindings":
        changed.update(configuration_fingerprint="d"*64, recommendation_digest="e"*64)
    elif fault == "valid_empty_historical_scope":
        changed.update(identity_count=0, identity_scope_digest=digest([]))
    elif fault == "identity_bool":
        changed["identity_count"] = True
    elif fault == "identity_over_limit":
        changed["identity_count"] = changed["reserved_open_positions_count"] = 65
    elif fault == "reserved_too_small":
        changed["reserved_open_positions_count"] = 9
    elif fault == "demand_bool":
        changed["reserved_exit_demand"]["book"] = True
    elif fault == "demand_negative":
        changed["reserved_exit_demand"]["book"] = -1
    elif fault == "demand_missing":
        del changed["reserved_exit_demand"]["current"]
    elif fault == "configuration_malformed":
        changed["configuration_fingerprint"] = "UNVERIFIED"
    else:
        changed["recommendation_digest"] = "UNVERIFIED"
    observed = budget.observe_exit_round(elapsed_seconds=.1, deadline_seconds=5,
        required_identity_digests=None, frozen_admission_scope=changed)
    assert observed["status"] == "DEGRADED" and observed["lower_suspended"]
    valid = fault in {"other_valid_bindings", "valid_empty_historical_scope"}
    if valid:
        assert observed["required_identities_count"] == changed["identity_count"]
        assert observed["fresh_required_count"] is None and not observed["required_scope_verified_current"]
        assert observed["frozen_admission_scope"] == changed
    else:
        assert observed["reason"] == "PPI_EXIT_ROUND_MEASUREMENT_INVALID"
        assert "required_identities_count" not in observed
    with closing(sqlite3.connect(budget.path)) as c, c:
        record = budget._get(c, "critical_exit_round")
        record["frozen_admission_scope"] = changed
        if valid:
            record["required_identities_count"] = changed["identity_count"]
        budget._put(c, "critical_exit_round", record)
    snapshot = budgets.runtime_budget_snapshot(database, as_of=clock.now())
    assert snapshot["status"] == ("DEGRADED" if valid else "UNVERIFIED")
    assert budget.metrics()["exit_service"]["lower_suspended"]
    assert not budget.acquire("current", consumer="SCANNER", priority="DISCOVERY")["allowed"]
