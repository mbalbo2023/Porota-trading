"""Retained native wire debt cannot consume the critical receipt/storage floor.

Historic rows are explicit offline synthetic SQL evidence. Current sends use
the actual SDK/guard and fake HTTP adapter; nothing here measures productive
provider throughput or grants an operator OPEN approval.
"""
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import sqlite3
from urllib.parse import urlsplit

import pytest

from bd_ppi_readonly_guard import ProductionMarketReader
from cg_paper_workspace import artifact_root
from rc6_dynamic_universe.promotion import RuntimeCapacityController
from rc6_ppi_global_budget import (
    BudgetBackpressure, GlobalPPIBudget, RuntimePPIBudget, budget_policy,
    exit_capacity_contract, exit_retention_preflight, runtime_budget_snapshot,
)
from tests.test_issue465_budget_adversarial import native_opened_store, scoped_book
from tests.test_rc6_convergence_budget_liveness import state_from_policy, ExactController
from tests.test_rc6_exit_reader_cadence import reviewed_native_capacity
from tests.test_rc6_ppi_capacity_benchmark import Clock, wire
from tests.test_rc6_ppi_global_budget import policy, use


def measured_shape(clock, *, window=30, opened=5, maximum_bytes=8 * 1024**2):
    value = policy(clock, limits=dict(current=75, book=75, intraday=75), global_limit=225)
    value.update(window_seconds=window, maximum_bytes=maximum_bytes,
        expires_at=(clock.now() + timedelta(hours=2)).isoformat())
    state = state_from_policy(value)
    return state, budget_policy(state, opened_count=opened)


def historic_lower_receipts(budget, clock, *, count=20000, oldest=3590, newest=31):
    """Legal synthetic 30s capacity history, not attributed to real PPI sends.

    Endpoint sequence gives ~68 current/~68 intraday/~34 book per30s,
    total~169; all remain below the reviewed75/225 caps and lower floors.
    """
    endpoints = ("current", "intraday", "current", "intraday", "book")
    rows = [(f"{index:032x}", clock.now().timestamp() - oldest
        + index * ((oldest - newest) / max(1, count)), endpoints[index % 5],
        "SCANNER", "DISCOVERY", 1) for index in range(count)]
    with closing(sqlite3.connect(budget.path)) as c, c:
        c.executemany("INSERT INTO budget_requests VALUES(?,?,?,?,?,?)", rows)
    return rows


def debt_digest(path):
    with closing(sqlite3.connect(path)) as c:
        rows = c.execute("SELECT * FROM budget_requests ORDER BY lease").fetchall()
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def market_sends(calls):
    return [item for item in calls if urlsplit(item[1]).path.lower().rsplit("/", 1)[-1]
        in {"current", "book", "intraday"}]


def test_native_reviewed_activation_blocks_20000_retained_lower_receipts_before_wire(wire, tmp_path, monkeypatch):
    clock, calls, behavior = wire
    reviewed, recommendation, report, approval = reviewed_native_capacity(wire)
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=5)
    ctl = RuntimeCapacityController(store.path, environ={}, policy=reviewed,
        recommendation=recommendation, report=report, approval=approval)
    arbiter = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    behavior["latency"] = 0
    reader = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=arbiter, consumer="EXIT_READER")
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    try:
        reader.login_once()
        assert scoped_book(reader, identity, "EXIT_CRITICAL")
        clock.advance(6)  # Expire the prior good cache without modifying it.
        with closing(sqlite3.connect(arbiter.path)) as c, c:
            c.execute("DELETE FROM budget_requests")
        historic_lower_receipts(arbiter.budget, clock)
        state = ctl.state(clock.now())
        # Software capacity came from the actual offline native benchmark;
        # physical occupancy is a separate read-only activation condition.
        assert state["global_budget"]["endpoint_limits"]["book"] == 75
        assert state["global_budget"]["global_limit"] == 225
        before, sends = debt_digest(arbiter.path), len(market_sends(calls))
        modes = {p: p.stat().st_mode for p in arbiter.path.parent.iterdir()}
        guarded = exit_retention_preflight(store.path, state, opened_count=5, as_of=clock.now())
        assert guarded["source_status"] == "OBSERVED"
        assert guarded["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
        assert guarded["retained_receipts"] == 20000 and guarded["available_rows"] == 0
        assert "PPI_EXIT_RECEIPT_RESERVE_BACKPRESSURE" in guarded["reason_codes"]
        assert debt_digest(arbiter.path) == before
        assert {p: p.stat().st_mode for p in arbiter.path.parent.iterdir()} == modes
        with pytest.raises(BudgetBackpressure, match="ROW_LIMIT"):
            scoped_book(reader, identity, "EXIT_CRITICAL")
        assert arbiter.activation_contract["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
        with pytest.raises(BudgetBackpressure, match="RECEIPT_RESERVE"):
            arbiter.acquire("current", consumer="SCANNER", priority="DISCOVERY")
        assert len(market_sends(calls)) == sends and debt_digest(arbiter.path) == before
        observed = runtime_budget_snapshot(store.path, as_of=clock.now())
        assert observed["receipt_retention"]["retained_receipts"] == 20000
        assert observed["receipt_retention"]["lower_suspended"]
        # No active-window cap was consumed; the retained physical debt alone
        # caused the rejection and survives reconstruction of a cold caller.
        cold = RuntimePPIBudget(store.path, ctl, clock=clock.now)
        with pytest.raises(BudgetBackpressure, match="RECEIPT_RESERVE"):
            cold.acquire("current", consumer="SCANNER", priority="DISCOVERY")
        assert cold.activation_contract["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
        assert debt_digest(arbiter.path) == before
        clock.advance(12)  # Only now may the oldest historic receipts age out.
        recoverable = exit_retention_preflight(store.path, ctl.state(clock.now()), opened_count=5, as_of=clock.now())
        assert recoverable["status"] == "READY"
        assert recoverable["physically_stored_receipts"] == 20000
        assert recoverable["legally_expired_receipts"] > 0
        assert recoverable["retained_receipts"] < 20000
        assert debt_digest(arbiter.path) == before  # The probe did not prune.
        assert scoped_book(reader, identity, "EXIT_CRITICAL")
        assert len(market_sends(calls)) == sends + 1
        assert arbiter.budget.metrics()["receipt_retention"]["retained_receipts"] < 20000
    finally:
        reader.close()


def test_actual_sdk_three_windows_and_restart_keep_exit_receipt_headroom(wire, tmp_path):
    clock, calls, behavior = wire
    behavior["latency"] = 0
    state, value = measured_shape(clock)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    historic = historic_lower_receipts(budget, clock, count=19985)
    reader = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=budget, consumer="EXIT_READER")
    denied, successful_lower = 0, 0
    try:
        reader.login_once()
        first = budget.acquire("current", consumer="SCANNER", priority="DISCOVERY")
        assert not first["allowed"] and first["reason"] == "PPI_EXIT_RECEIPT_RESERVE_BACKPRESSURE"
        resources = budget.metrics()["receipt_retention"]
        assert resources["required_exit_growth"] == 15 and resources["available_rows"] == 15
        assert resources["status"] == "READY" and resources["lower_suspended"]
        for round_index in range(18):  # Three full native30s windows.
            if round_index in {6, 12}:
                reader.close()
                budget = GlobalPPIBudget(budget.path, deepcopy(value), clock=clock.now)
                reader = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=budget, consumer="EXIT_READER")
                reader.login_once()
            for index in range(5):
                identity = (f"EXIT{index}", "ACCIONES", "BYMA", "ARS", "A-24HS")
                # The native public reader supports unscoped identities too.
                # Exercise real worst-case sends without assuming cache hits;
                # its transport still enforces the same critical budget.
                with reader.read_scope(priority="EXIT_CRITICAL"):
                    assert reader.book(identity[0], identity[1], identity[4])
            try:
                with reader.read_scope(priority="DISCOVERY"):
                    assert reader.current("GGAL", "ACCIONES", "A-24HS")
                successful_lower += 1
            except BudgetBackpressure as error:
                assert str(error) in {"PPI_EXIT_RECEIPT_RESERVE_BACKPRESSURE", "PPI_HIGH_PRIORITY_RESERVE_BACKPRESSURE", "PPI_BUDGET_ROW_LIMIT"}
                denied += 1
            assert budget.metrics()["receipt_retention"]["retained_receipts"] <= 20000
            clock.advance(5)
        assert len(market_sends(calls)) == 90 + successful_lower
        assert denied and successful_lower  # Legal aging also permits fairness.
        with closing(sqlite3.connect(budget.path)) as c:
            exact = c.execute("SELECT * FROM budget_requests WHERE lease=?", (historic[-1][0],)).fetchone()
            assert exact == historic[-1]  # No live synthetic debt was forgiven.
            assert c.execute("SELECT COUNT(*) FROM budget_requests WHERE priority='EXIT_CRITICAL' AND used=1").fetchone()[0] == 90
            assert c.execute("SELECT COUNT(*) FROM budget_requests WHERE priority='EXIT_CRITICAL' AND at>?",
                (clock.now().timestamp() - 30,)).fetchone()[0] == 25
        # Nonbinding telemetry retains the entire second at the boundary;
        # native rolling admission above uses the exact receipt timestamps.
        assert budget.metrics()["window"]["by_endpoint"]["book"]["used"] == 30
    finally:
        reader.close()


@pytest.mark.parametrize("window", [15, 30, 60])
def test_static_exit_retention_capacity_never_invents_room_for_own_critical_demand(tmp_path, window):
    clock = Clock()
    value = policy(clock, limits=dict(current=1000, book=1000, intraday=1000), global_limit=3000)
    value.update(window_seconds=window, maximum_bytes=32 * 1024**2)
    state = state_from_policy(value)
    assert exit_capacity_contract(state, opened_count=27)["status"] == "READY"
    blocked = exit_capacity_contract(state, opened_count=28)
    assert blocked["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
    assert blocked["receipt_retention"]["required_exit_receipts"] == 28 * 721
    assert "PPI_EXIT_RECEIPT_RETENTION_INSUFFICIENT" in blocked["reason_codes"]
    with pytest.raises(ValueError, match="PPI_EXIT_CAPACITY_INSUFFICIENT"):
        budget_policy(state, opened_count=28)
    assert not (tmp_path / "budget.sqlite").exists()


def test_physical_retention_preflight_blocks_small_quota_without_bootstrap_and_keeps_baseline(tmp_path):
    clock = Clock()
    value = policy(clock, limits=dict(current=75, book=75, intraday=75), global_limit=225)
    value["maximum_bytes"] = 65536
    state = state_from_policy(value)
    blocked = exit_retention_preflight(tmp_path / "paper.sqlite", state, opened_count=5, as_of=clock.now())
    assert blocked["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
    assert "PPI_EXIT_RECEIPT_STORAGE_INSUFFICIENT" in blocked["reason_codes"]
    assert not (artifact_root(tmp_path / "paper.sqlite") / "ppi-budget").exists()
    # Intentional cold OFF/SHADOW retain their existing baseline authority;
    # this does not silently turn them into a new dynamic5s SLA.
    for mode in ("OFF_BASELINE", "SHADOW_BASELINE"):
        baseline = RuntimePPIBudget(tmp_path / "paper.sqlite", ExactController({"status": mode}), clock=clock.now)
        assert baseline.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL") == {"allowed": True, "lease": None}


def test_pending_lower_start_rechecks_new_retained_exit_floor_without_using_receipt(tmp_path):
    clock = Clock()
    state, value = measured_shape(clock, opened=0)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    historic_lower_receipts(budget, clock, count=19985)
    pending = budget.acquire("current", consumer="SCANNER", priority="DISCOVERY")
    assert pending["allowed"]
    stronger = budget_policy(state, opened_count=5)
    second = GlobalPPIBudget(budget.path, stronger, clock=clock.now)
    assert second.acquire("book", priority="EXIT_CRITICAL")["reason"] == "PPI_SERIAL_BACKPRESSURE"
    before = debt_digest(budget.path)
    with pytest.raises(BudgetBackpressure, match="RECEIPT_RESERVE"):
        budget.start(pending["lease"])
    assert debt_digest(budget.path) == before
    with closing(sqlite3.connect(budget.path)) as c:
        assert c.execute("SELECT used FROM budget_requests WHERE lease=?", (pending["lease"],)).fetchone() == (0,)
    budget.finish(pending["lease"])
    assert use(second, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]


def test_genuine_sql_full_retained_binding_debt_stays_off_wire_until_legal_aging(wire, tmp_path):
    clock, calls, behavior = wire
    behavior["latency"] = 0
    value = policy(clock, limits=dict(current=75, book=75, intraday=75), global_limit=225)
    value.update(maximum_bytes=65536, expires_at=(clock.now() + timedelta(hours=8)).isoformat())
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    reader = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=budget, consumer="EXIT_READER")
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    try:
        reader.login_once()
        assert scoped_book(reader, identity, "EXIT_CRITICAL")
        with closing(sqlite3.connect(budget.path)) as c:
            live = c.execute("SELECT * FROM budget_requests WHERE used=1").fetchone()
            c.execute("PRAGMA max_page_count=7")
            for index in range(1000):
                try:
                    c.execute("INSERT INTO budget_requests VALUES(?,?,?,?,?,1)",
                        (f"old-{index:032x}", clock.now().timestamp() - 3590,
                         ("current", "book", "intraday")[index % 3], "SCANNER", "DISCOVERY"))
                    c.commit()
                except sqlite3.OperationalError as error:
                    c.rollback()
                    assert error.sqlite_errorcode == sqlite3.SQLITE_FULL
                    break
            else:
                raise AssertionError("native receipt b-trees did not reach SQLite's configured physical quota")
            assert 0 < index < 225  # Bounded synthetic legacy burst, not20k.
        before, sends = debt_digest(budget.path), len(market_sends(calls))
        clock.advance(6)
        with pytest.raises(BudgetBackpressure, match="STATE_UNAVAILABLE"):
            scoped_book(reader, identity, "EXIT_CRITICAL")
        assert len(market_sends(calls)) == sends and debt_digest(budget.path) == before
        with closing(sqlite3.connect(budget.path)) as c:
            assert c.execute("SELECT * FROM budget_requests WHERE lease=?", (live[0],)).fetchone() == live
        clock.advance(6)
        restarted = GlobalPPIBudget(budget.path, value, clock=clock.now)
        reader.close()
        reader = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=restarted, consumer="EXIT_READER")
        reader.login_once()
        assert scoped_book(reader, identity, "EXIT_CRITICAL")
        assert len(market_sends(calls)) == sends + 1
        with closing(sqlite3.connect(budget.path)) as c:
            assert c.execute("SELECT * FROM budget_requests WHERE lease=?", (live[0],)).fetchone() == live
            assert c.execute("SELECT COUNT(*) FROM budget_requests").fetchone()[0] == 2
        assert restarted.path.stat().st_size < value["maximum_bytes"] // 2
    finally:
        reader.close()


def test_recent_used_exit_debt_is_not_mistaken_for_spare_retained_capacity(tmp_path):
    clock = Clock()
    state, value = measured_shape(clock)
    database = tmp_path / "paper.sqlite"
    budget = GlobalPPIBudget(artifact_root(database) / "ppi-budget/global.sqlite", value, clock=clock.now)
    # Explicit synthetic legacy debt from a different historic envelope. The
    # new75/225 recommendation is not asserted as its historic authority.
    historic_lower_receipts(budget, clock, count=18000, oldest=1031, newest=31)
    with closing(sqlite3.connect(budget.path)) as c, c:
        c.execute("UPDATE budget_requests SET priority='EXIT_CRITICAL',consumer='EXIT_READER',endpoint='book'")
    before = debt_digest(budget.path)
    guarded = exit_retention_preflight(database, state, opened_count=5, as_of=clock.now())
    assert guarded["retained_receipts"] == 18000 and guarded["available_rows"] == 2000
    assert guarded["required_exit_growth"] > 2000
    assert guarded["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
    assert "PPI_EXIT_RECEIPT_RESERVE_BACKPRESSURE" in guarded["reason_codes"]
    rejected = budget.acquire("current", priority="DISCOVERY")
    assert not rejected["allowed"] and rejected["reason"] == "PPI_EXIT_RECEIPT_RESERVE_BACKPRESSURE"
    assert debt_digest(budget.path) == before


@pytest.mark.parametrize("condition", ["busy", "WAL", "hardlink", "invalid_authority"])
def test_existing_retention_preflight_unknown_state_grants_no_authority_and_changes_no_files(tmp_path, condition):
    clock = Clock()
    state, value = measured_shape(clock)
    database = tmp_path / "paper.sqlite"
    budget = GlobalPPIBudget(artifact_root(database) / "ppi-budget/global.sqlite", value, clock=clock.now)
    assert use(budget, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    blocker = None
    if condition == "busy":
        blocker = sqlite3.connect(budget.path)
        blocker.execute("BEGIN EXCLUSIVE")
    elif condition == "WAL":
        with closing(sqlite3.connect(budget.path)) as c:
            assert c.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    elif condition == "hardlink":
        (tmp_path / "hardlink.sqlite").hardlink_to(budget.path)
    else:
        broken = GlobalPPIBudget._authority(value, clock.now().timestamp())
        broken.pop("retain_until")
        with closing(sqlite3.connect(budget.path)) as c, c:
            c.execute("UPDATE budget_state SET value=? WHERE key='reservation_envelopes_v1'", (json.dumps([broken]),))
    try:
        files = {p: (p.read_bytes(), p.stat().st_mode) for p in budget.path.parent.iterdir()}
        guarded = exit_retention_preflight(database, state, opened_count=5, as_of=clock.now())
        assert guarded["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
        assert guarded["source_status"] == "UNVERIFIED"
        assert guarded["reason_codes"] == ["PPI_EXIT_RECEIPT_STORAGE_UNVERIFIED"]
        assert runtime_budget_snapshot(database, as_of=clock.now())["status"] == "UNVERIFIED"
        assert {p: (p.read_bytes(), p.stat().st_mode) for p in budget.path.parent.iterdir()} == files
    finally:
        if blocker:
            blocker.rollback()
            blocker.close()


def test_readonly_aging_credit_never_releases_an_inflight_token_or_invents_free_pages(tmp_path):
    clock = Clock()
    state, value = measured_shape(clock)
    database = tmp_path / "paper.sqlite"
    budget = GlobalPPIBudget(artifact_root(database) / "ppi-budget/global.sqlite", value, clock=clock.now)
    pending = budget.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert pending["allowed"]
    budget.start(pending["lease"])
    historic_lower_receipts(budget, clock, count=19999)
    clock.advance(3601)
    before = debt_digest(budget.path)
    guarded = exit_retention_preflight(database, state, opened_count=5, as_of=clock.now())
    assert guarded["retained_receipts"] == 1
    assert guarded["physically_stored_receipts"] == 20000
    assert guarded["legally_expired_receipts"] == 19999
    assert guarded["available_rows"] == 19999
    assert guarded["required_exit_growth"] == 3600  # No credit for uncertain active expiry.
    assert guarded["available_pages"] < guarded["required_pages"]
    assert guarded["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
    assert guarded["reason_codes"] == ["PPI_EXIT_RECEIPT_STORAGE_BACKPRESSURE"]
    assert debt_digest(budget.path) == before
    with closing(sqlite3.connect(budget.path)) as c:
        assert c.execute("SELECT used FROM budget_requests WHERE lease=?", (pending["lease"],)).fetchone() == (1,)
        assert json.loads(c.execute("SELECT value FROM budget_state WHERE key='inflight'").fetchone()[0])["lease"] == pending["lease"]


def test_native_cold_missing_main_checks_every_orphan_before_new_sdk_wire(wire, tmp_path, monkeypatch):
    clock, calls, behavior = wire
    reviewed, recommendation, report, approval = reviewed_native_capacity(wire)
    behavior["latency"] = 0
    for index, suffix in enumerate((None, "-wal", "-shm", "-journal", ".exit-round-degraded",
                                   ".exit-round.lock", ".wire.lock", ".init.lock")):
        case_root = tmp_path / f"case-{index}"
        case_root.mkdir()
        store, _ = native_opened_store(case_root, clock, monkeypatch, count=1)
        ctl = RuntimeCapacityController(store.path, environ={}, policy=reviewed,
            recommendation=recommendation, report=report, approval=approval)
        arbiter = RuntimePPIBudget(store.path, ctl, clock=clock.now)
        if suffix is not None:
            arbiter.path.parent.mkdir(parents=True)
            auxiliary = arbiter.path.with_name(arbiter.path.name + suffix)
            auxiliary.write_bytes(b"OFFLINE_ORPHAN_BUDGET_COMPANION")
            auxiliary.chmod(0o600)
        before = {p: (p.read_bytes(), p.stat().st_mode) for p in arbiter.path.parent.iterdir()} if arbiter.path.parent.exists() else {}
        observed = exit_retention_preflight(store.path, ctl.state(clock.now()), opened_count=1, as_of=clock.now())
        assert not arbiter.path.exists()
        if suffix is None:
            assert observed["status"] == "READY" and observed["source_status"] == "ABSENT"
            assert not arbiter.path.parent.exists()  # No readonly bootstrap.
            assert runtime_budget_snapshot(store.path, as_of=clock.now())["status"] == "ABSENT"
        else:
            assert observed["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
            assert observed["source_status"] == "UNVERIFIED"
            assert runtime_budget_snapshot(store.path, as_of=clock.now())["status"] == "UNVERIFIED"
        reader = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=arbiter, consumer="EXIT_READER")
        try:
            reader.login_once()
            sends = len(market_sends(calls))
            identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
            if suffix is None:
                assert scoped_book(reader, identity, "EXIT_CRITICAL")
                assert len(market_sends(calls)) == sends + 1 and arbiter.path.exists()
            else:
                with pytest.raises(BudgetBackpressure, match="STORAGE_UNVERIFIED"):
                    scoped_book(reader, identity, "EXIT_CRITICAL")
                assert len(market_sends(calls)) == sends
                assert not arbiter.path.exists()
                assert arbiter.activation_contract["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
                assert {p: (p.read_bytes(), p.stat().st_mode) for p in arbiter.path.parent.iterdir()} == before
        finally:
            reader.close()
