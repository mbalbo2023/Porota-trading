"""Native EXIT producer schedule over dated PAPER ledgers and the SDK fake wire.

Monotonic time is controlled at the producer seam. This verifies software
cadence and degradation, not current PPI latency or an operator OPEN approval.
"""
from copy import deepcopy
from contextlib import closing
import sqlite3
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

import bd_ppi_readonly_guard as readonly
import bf_production_paper_observer as observer
import bv_paper_runtime as runtime_module
import rc6_dynamic_universe.promotion as promotion
from be_paper_engine import PaperBroker
from rc6_dynamic_universe.common import digest
from rc6_ppi_global_budget import BudgetBackpressure, RuntimePPIBudget, supervisable_position_count
from tests.test_issue465_budget_adversarial import native_opened_store
from tests.test_issue465_budget_adversarial import scoped_book
from tests.test_rc6_convergence_budget_liveness import seed_offline_pending_futures
from tests.test_rc6_ppi_capacity_benchmark import wire, measure


def reviewed_native_capacity(fake_wire):
    report = measure(fake_wire, batches=(100,), cadence_seconds=30)
    at = fake_wire[0].now()
    policy = deepcopy(promotion.DEFAULT_POLICY)
    rec = promotion.build_recommendation(report, policy=policy, as_of=at)
    assert rec["status"] == "SHADOW_RECOMMENDATION"
    assert rec["global_budget"]["endpoint_limits"]["book"] == 75
    approval = {"schema": promotion.APPROVAL_SCHEMA, "approved": True,
        "reviewer": "OFFLINE_EXPLICIT_REVIEW_ONLY", "reviewed_at": at.isoformat(),
        "recommendation_digest": rec["recommendation_digest"],
        "runtime_policy_fingerprint": rec["runtime_policy_fingerprint"]}
    approval["approval_digest"] = digest(approval)
    policy.update(mode="APPROVED", approved_recommendation_digest=rec["recommendation_digest"])
    return policy, rec, report, approval


class NativeRoundStop:
    def __init__(self, clock, completed, limit):
        self.clock, self.completed, self.limit = clock, completed, limit
        self.waits, self.stopped = [], False

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        assert seconds >= 0
        self.waits.append(float(seconds))
        self.clock.advance(seconds)
        if len(self.completed) >= self.limit:
            self.stopped = True
        return self.stopped


def native_mixed_store(tmp_path, clock, monkeypatch):
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=5)
    seed_offline_pending_futures(store, monkeypatch)
    assert supervisable_position_count(store.path) == 10
    return store


@pytest.mark.parametrize("scenario", ["feasible", "slow_round", "OFF", "SHADOW"])
def test_native_exit_worker_services_mixed_positions_with_honest_cadence(wire, tmp_path, monkeypatch, scenario):
    clock, calls, behavior = wire
    values = reviewed_native_capacity(wire)
    store = native_mixed_store(tmp_path, clock, monkeypatch)
    policy, rec, report, approval = values
    if scenario in {"OFF", "SHADOW"}:
        policy["mode"] = scenario
    ctl = promotion.RuntimeCapacityController(store.path, environ={}, policy=policy,
        recommendation=rec, report=report, approval=approval)
    arbiter = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    if scenario not in {"OFF", "SHADOW"}:
        state = ctl.state(clock.now())
        assert state["status"] == "APPROVED_DYNAMIC"
        assert state["exit_capacity"]["open_positions_count"] == 10
        assert state["exit_capacity"]["exit_demand"]["book"] == 60
        assert state["exit_capacity"]["status"] == "READY"
    if scenario == "slow_round":
        # Each book remains within the per-request deadline. Their accumulated
        # elapsed time still misses the fleet cadence and must be observable.
        behavior["latency"] = .8

    completed, starts, readers = [], [], []
    actual_reader = readonly.ProductionMarketReader
    actual_collect = runtime_module.collect_exit_books
    actual_status = runtime_module._reader_status_write

    def reader_factory(*args, **kwargs):
        result = actual_reader(*args, **kwargs, budget=arbiter)
        readers.append(result)
        return result

    def traced_collect(*args, **kwargs):
        starts.append(clock.mono())
        return actual_collect(*args, **kwargs)

    def traced_status(source, state, detail=""):
        result = actual_status(source, state, detail)
        if "round_seconds=" in detail:
            row = {"state": state, "detail": detail, "at": clock.mono()}
            if "exit_round=" in detail and completed:
                completed[-1] = row
            else:
                completed.append(row)
        return result

    monkeypatch.setattr(promotion, "capacity_controller_from_environment", lambda database=None: ctl)
    monkeypatch.setattr(readonly, "ProductionMarketReader", reader_factory)
    monkeypatch.setattr(observer, "_secret", lambda: ("OFFLINE_KEY", "OFFLINE_SECRET"))
    monkeypatch.setattr(observer, "_market_phase", lambda: "OPEN")
    monkeypatch.setattr(observer, "now_iso", lambda: clock.now().isoformat())
    monkeypatch.setattr(runtime_module, "now_iso", lambda: clock.now().isoformat())
    monkeypatch.setattr(runtime_module, "time", SimpleNamespace(monotonic=clock.mono))
    monkeypatch.setattr(runtime_module, "collect_exit_books", traced_collect)
    monkeypatch.setattr(runtime_module, "_reader_status_write", traced_status)
    limit = 3 if scenario == "feasible" else 1
    stop = NativeRoundStop(clock, completed, limit)
    before = len(calls)
    runtime_module.run_reader(store, stop)
    assert len(readers) == 1 and len(starts) == len(completed) == limit
    native_calls = calls[before:]
    books = [url for _, url in native_calls if urlsplit(url).path.lower().endswith("/book")]
    expected_symbols = {"GGAL", "EXIT1", "EXIT2", "EXIT3", "EXIT4", "DLR/AGO26", "DLR/SEP26", "DLR/OCT26", "DLR/NOV26", "DLR/DIC26"}
    from urllib.parse import parse_qs
    assert {parse_qs(urlsplit(url).query)["ticker"][0] for url in books} == expected_symbols
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'").fetchone()[0] == 5
        assert c.execute("SELECT COUNT(*) FROM paper_future_positions WHERE status='ACTIVE'").fetchone()[0] == 5
        assert c.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0
    if scenario == "feasible":
        assert starts[1]-starts[0] == pytest.approx(5)
        assert starts[2]-starts[1] == pytest.approx(5)
        assert all(row["state"] == "READY" and "deadline_missed=False" in row["detail"] for row in completed)
        assert all(wait > 4 for wait in stop.waits)
        metrics = arbiter.budget.metrics()
        assert metrics["by_endpoint"]["book"]["used"] == len(books)
        assert len(metrics["exit_service"]["by_identity_digest"]) == 10
        assert metrics["exit_service"]["round"]["status"] == "COMPLETE"
        assert metrics["exit_service"]["round"]["required_identities_count"] == 10
        # Probe actual lower wire after the three native rounds. Only common
        # book capacity is usable; complete remaining EXIT/OPENED floors hold.
        scanner = actual_reader("OFFLINE_KEY", "OFFLINE_SECRET", budget=arbiter, consumer="SCANNER")
        try:
            scanner.login_once()
            accepted = 0
            for index in range(20):
                try:
                    scanner.book(f"LOWER{index}", "ACCIONES", "A-24HS")
                    accepted += 1
                except BudgetBackpressure as error:
                    assert "RESERVE" in str(error)
                    break
            assert accepted == 5
        finally:
            scanner.close()
        # The real financial supervisor also visits all five exact futures.
        # Overnight carry is due for the August fixture, including its two
        # expired contracts. The native daily-risk latch has higher priority
        # and supplies the final close reason; no policy ordering is changed.
        supervisor = PaperBroker(store, clock_fn=lambda: clock.now().isoformat())
        from rc6_paper_family_lifecycle import future_position_contract
        for position in store.active_future_positions():
            assert supervisor._future_time_exit_cause(position,
                future_position_contract(position), clock.now().isoformat()) == "OVERNIGHT_CARRY_EXIT"
        supervisor.supervise_futures(clock.now().isoformat())
        with store.connect() as c:
            intents = c.execute("SELECT lifecycle_id,cause,state FROM paper_future_exit_intents").fetchall()
            assert len(intents) == 5
            assert all(row["cause"] in {"DAILY_RISK_STALE_MARKS", "DAILY_RISK_LATCHED"} for row in intents)
            assert all(row["state"] == "CLOSED" for row in intents)
        assert supervisable_position_count(store.path) == 5
    elif scenario == "slow_round":
        assert completed[0]["state"] == "DEGRADED"
        assert "deadline_missed=True" in completed[0]["detail"]
        assert completed[0]["at"]-starts[0] > 5
        assert stop.waits == [0.0]
        metrics = arbiter.budget.metrics()
        assert metrics["exit_service"]["round"]["status"] == "DEGRADED"
        assert metrics["exit_service"]["round"]["required_identities_count"] == 10
        assert metrics["exit_service"]["lower_suspended"]
        assert not arbiter.acquire("current", consumer="SCANNER", priority="DISCOVERY")["allowed"]
    else:
        assert "baseline_cadence=NO_VERIFICADO" in completed[0]["detail"]
        assert stop.waits == [1.0] * 10 + [5.0]
        assert len(books) == 10  # original factual path does not coalesce
        assert arbiter.budget is None and not arbiter.path.exists()


@pytest.mark.parametrize("failure", ["late", "read_failure", "incomplete", "stale", "unknown_ledger"])
def test_native_round_debt_survives_restart_and_only_whole_fresh_scope_clears(wire, tmp_path, monkeypatch, failure):
    from rc6_ppi_global_budget import runtime_budget_snapshot
    clock, calls, _ = wire
    values = reviewed_native_capacity(wire)
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=2)
    ctl = promotion.RuntimeCapacityController(store.path, environ={}, policy=values[0],
        recommendation=values[1], report=values[2], approval=values[3])
    arbiter = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    reader = readonly.ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=arbiter, consumer="EXIT_READER")
    identities = [(symbol, "ACCIONES", "BYMA", "ARS", "A-24HS") for symbol in ("GGAL", "EXIT1")]
    try:
        reader.login_once()
        for identity in identities[:1 if failure == "incomplete" else 2]:
            assert scoped_book(reader, identity, "EXIT_CRITICAL")["bids"]
        if failure == "stale":
            clock.advance(6)
        if failure == "unknown_ledger":
            with store.connect() as c:
                c.execute("ALTER TABLE paper_positions RENAME TO missing_opened_ledger")
        original_policy = deepcopy(arbiter.budget.policy)
        with closing(sqlite3.connect(arbiter.path)) as c:
            receipts = c.execute("SELECT * FROM budget_requests ORDER BY lease").fetchall()
        result = reader.observe_exit_round(elapsed_seconds=6 if failure == "late" else .1,
            deadline_seconds=5, failures=int(failure == "read_failure"))
        assert result["status"] == "DEGRADED" and result["lower_suspended"]
        observed = runtime_budget_snapshot(store.path, as_of=clock.now())
        assert observed["status"] == "DEGRADED" and observed["exit_service"]["round_guard_active"]
        assert observed["exit_service"]["round"]["status"] == "DEGRADED"
        with closing(sqlite3.connect(arbiter.path)) as c:
            assert c.execute("SELECT * FROM budget_requests ORDER BY lease").fetchall() == receipts
        assert arbiter.budget.policy == original_policy
        if failure == "unknown_ledger":
            with store.connect() as c:
                c.execute("ALTER TABLE missing_opened_ledger RENAME TO paper_positions")
        reader.close()
        restored = RuntimePPIBudget(store.path, ctl, clock=clock.now)
        lower = readonly.ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=restored, consumer="SCANNER")
        try:
            lower.login_once()
            before = len(calls)
            with pytest.raises(BudgetBackpressure, match="LOWER_SUSPENDED"):
                lower.current("LOWER", "ACCIONES", "A-24HS")
            assert len(calls) == before
            # A single successful EXIT cannot clear the durable whole-round
            # barrier, even when individual book pressure has been discharged.
            assert scoped_book(lower, identities[0], "EXIT_CRITICAL")["bids"]
            assert restored.budget.metrics()["exit_service"]["lower_suspended"]
            clock.advance(6)
            for identity in identities:
                assert scoped_book(lower, identity, "EXIT_CRITICAL")["bids"]
            result = lower.observe_exit_round(elapsed_seconds=.1, deadline_seconds=5)
            assert result["status"] == "COMPLETE" and not result["lower_suspended"]
            assert result["covered_identities_count"] == result["required_identities_count"] == 2
            assert lower.current("LOWER", "ACCIONES", "A-24HS")["price"]
        finally:
            lower.close()
    finally:
        reader.close()


def test_native_round_busy_has_durable_cross_process_barrier_and_read_only_evidence(wire, tmp_path, monkeypatch):
    from rc6_ppi_global_budget import GlobalPPIBudget, runtime_budget_snapshot
    clock, calls, _ = wire
    values = reviewed_native_capacity(wire)
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=1)
    ctl = promotion.RuntimeCapacityController(store.path, environ={}, policy=values[0],
        recommendation=values[1], report=values[2], approval=values[3])
    arbiter = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    reader = readonly.ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=arbiter, consumer="EXIT_READER")
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    try:
        reader.login_once()
        assert scoped_book(reader, identity, "EXIT_CRITICAL")["bids"]
        with closing(sqlite3.connect(arbiter.path)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            result = reader.observe_exit_round(elapsed_seconds=6, deadline_seconds=5)
            assert result == {"status": "DEGRADED", "lower_suspended": True,
                "reason": "PPI_EXIT_ROUND_STATE_UNAVAILABLE"}
            blocker.rollback()
        original_policy = deepcopy(arbiter.budget.policy)
        # A new independent arbiter observes the marker even though the
        # failed SQLite write could not publish a round JSON record.
        restored = GlobalPPIBudget(arbiter.path, original_policy, clock=clock.now)
        before = len(calls)
        denied = restored.acquire("current", consumer="SCANNER", priority="DISCOVERY")
        assert not denied["allowed"] and denied["reason"] == "PPI_EXIT_DEADLINE_LOWER_SUSPENDED"
        assert len(calls) == before
        before_files = {p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
            for p in arbiter.path.parent.iterdir() if p.is_file()}
        observed = runtime_budget_snapshot(store.path, as_of=clock.now())
        assert observed["status"] == "DEGRADED" and observed["exit_service"]["round_guard_active"]
        assert observed["exit_service"]["round"] is None
        assert before_files == {p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
            for p in arbiter.path.parent.iterdir() if p.is_file()}
        assert reader.observe_exit_round(elapsed_seconds=.1, deadline_seconds=5)["status"] == "COMPLETE"
        assert not arbiter.budget.metrics()["exit_service"]["lower_suspended"]
        assert arbiter.budget.policy == original_policy
    finally:
        reader.close()


def test_native_round_degradation_after_acquire_blocks_lower_start_without_wire_or_debt_loss(wire, tmp_path, monkeypatch):
    clock, calls, _ = wire
    values = reviewed_native_capacity(wire)
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=1)
    ctl = promotion.RuntimeCapacityController(store.path, environ={}, policy=values[0],
        recommendation=values[1], report=values[2], approval=values[3])
    arbiter = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    reader = readonly.ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=arbiter, consumer="SCANNER")
    actual_start = arbiter.start

    def degrade_before_start(token):
        assert arbiter.observe_exit_round(elapsed_seconds=6, deadline_seconds=5)["status"] == "DEGRADED"
        actual_start(token)

    monkeypatch.setattr(arbiter, "start", degrade_before_start)
    try:
        reader.login_once()
        before = len(calls)
        with pytest.raises(BudgetBackpressure, match="LOWER_SUSPENDED"):
            reader.current("GGAL", "ACCIONES", "A-24HS")
        assert len(calls) == before
        metrics = arbiter.budget.metrics()
        assert metrics["global"] == {"requested": 1, "allowed": 1, "used": 0, "dropped": 0}
        assert metrics["exit_service"]["lower_suspended"]
        with closing(sqlite3.connect(arbiter.path)) as c:
            assert c.execute("SELECT COUNT(*) FROM budget_requests").fetchone()[0] == 0
            assert c.execute("SELECT value FROM budget_state WHERE key='inflight'").fetchone()[0] == "null"
    finally:
        reader.close()


@pytest.mark.parametrize("fault", ["elapsed_nan", "elapsed_bool", "elapsed_negative", "deadline_changed",
    "failures_bool", "failures_negative", "unverified_scope"])
def test_round_measurement_cannot_forge_completion_or_clear_durable_lower_barrier(tmp_path, fault):
    from rc6_ppi_global_budget import GlobalPPIBudget
    from tests.test_rc6_ppi_capacity_benchmark import Clock
    from tests.test_rc6_ppi_global_budget import policy, use
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    assert use(budget, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    params = dict(elapsed_seconds=.1, deadline_seconds=5, failures=0, required_identity_digests=set())
    params.update({"elapsed_nan": {"elapsed_seconds": float("nan")},
        "elapsed_bool": {"elapsed_seconds": True}, "elapsed_negative": {"elapsed_seconds": -1},
        "deadline_changed": {"deadline_seconds": 60}, "failures_bool": {"failures": True},
        "failures_negative": {"failures": -1}, "unverified_scope": {"required_identity_digests": None}}[fault])
    with closing(sqlite3.connect(budget.path)) as c:
        original = c.execute("SELECT * FROM budget_requests").fetchall()
    result = budget.observe_exit_round(**params)
    assert result["status"] == "DEGRADED" and result["lower_suspended"]
    assert not budget.acquire("current", consumer="SCANNER", priority="DISCOVERY")["allowed"]
    with closing(sqlite3.connect(budget.path)) as c:
        assert c.execute("SELECT * FROM budget_requests").fetchall() == original
    assert budget.metrics()["global"]["used"] == 1


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_round_marker_aliases_fail_closed_without_modifying_target(tmp_path, alias):
    from pathlib import Path
    import os
    from rc6_ppi_global_budget import GlobalPPIBudget
    from tests.test_rc6_ppi_capacity_benchmark import Clock
    from tests.test_rc6_ppi_global_budget import policy
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    target = tmp_path / "protected.txt"
    target.write_bytes(b"UNCHANGED_OFFLINE_TARGET")
    marker = Path(str(budget.path) + ".exit-round-degraded")
    if alias == "symlink":
        marker.symlink_to(target)
    else:
        os.link(target, marker)
    result = budget.observe_exit_round(elapsed_seconds=.1, deadline_seconds=5, required_identity_digests=set())
    assert result["status"] == "DEGRADED" and result["lower_suspended"]
    with pytest.raises(BudgetBackpressure, match="STATE_UNAVAILABLE"):
        budget.acquire("current", consumer="SCANNER", priority="DISCOVERY")
    assert target.read_bytes() == b"UNCHANGED_OFFLINE_TARGET"
