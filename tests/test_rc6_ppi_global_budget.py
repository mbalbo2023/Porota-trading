"""Actual fake-wire retries and processes share a bounded serial request budget."""
from copy import deepcopy
from datetime import timedelta
import json
import multiprocessing
from pathlib import Path

import pytest
import requests

from bd_ppi_readonly_guard import ProductionMarketReader, ReadOnlyPolicyViolation, ReadOnlyTransportGuard, retry_read
from rc6_ppi_global_budget import (BudgetBackpressure, GlobalPPIBudget, RuntimePPIBudget, budget_policy, SCHEMA)
from tests.test_rc6_ppi_capacity_benchmark import Clock, wire
from tests.test_rc6_capacity_promotion import approved, controller


def policy(clock, *, limits=None, global_limit=None, reserves=None):
    limits = limits or {"current": 3, "book": 3, "intraday": 3}
    return {"schema": SCHEMA, "version": "test-measured-serial-v1",
        "recommendation_digest": "a" * 64, "configuration_fingerprint": "b" * 64,
        "window_seconds": 30, "endpoint_limits": limits,
        "global_limit": global_limit or sum(limits.values()), "max_parallel_requests": 1,
        "safety_reserve": "WITHHELD_BY_MEASURED_SAFETY_FACTOR; not spendable",
        "priority_reserves": reserves or {}, "expires_at": (clock.now() + timedelta(hours=1)).isoformat(),
        "critical_book_seconds": 5, "lease_seconds": 60,
        "breaker_seconds": 60, "session_breaker_seconds": 900,
        "server_error_threshold": 2, "maximum_bytes": 8 * 1024**2}


def use(budget, endpoint, **scope):
    result = budget.acquire(endpoint, **scope)
    if result["allowed"]:
        budget.start(result["lease"])
        budget.finish(result["lease"])
    return result


def test_exit_reserve_cannot_be_consumed_by_discovery(tmp_path):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock, reserves={"EXIT_CRITICAL": {"book": 2}}), clock=clock.now)
    assert use(budget, "book", consumer="SCANNER")["allowed"]
    assert not use(budget, "book", consumer="SCALPING")["allowed"]
    assert use(budget, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    assert use(budget, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    assert budget.metrics()["by_endpoint"]["book"] == {"requested": 4, "allowed": 3, "used": 3, "dropped": 1}
    assert budget.path.stat().st_mode & 0o777 == 0o600


def test_scalping_hot_reservation_precedes_other_strategy_and_discovery(tmp_path):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock,
        reserves={"SCALPING_HOT": {"intraday": 2}}), clock=clock.now)
    assert use(budget, "intraday", consumer="SCANNER", priority="STRATEGY_HOT")["allowed"]
    assert not use(budget, "intraday", consumer="SCANNER", priority="STRATEGY_HOT")["allowed"]
    assert use(budget, "intraday", consumer="SCALPING", priority="SCALPING_HOT")["allowed"]
    assert use(budget, "intraday", consumer="SCALPING", priority="SCALPING_HOT")["allowed"]


def test_all_endpoints_and_consumers_share_one_global_allowance(tmp_path):
    clock = Clock()
    config = policy(clock, global_limit=3)
    first = GlobalPPIBudget(tmp_path / "budget.sqlite", config, clock=clock.now)
    second = GlobalPPIBudget(tmp_path / "budget.sqlite", deepcopy(config), clock=clock.now)
    assert use(first, "current", consumer="SCANNER")["allowed"]
    assert use(second, "intraday", consumer="SCALPING")["allowed"]
    assert use(second, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    assert not use(first, "book", consumer="SCANNER")["allowed"]
    assert first.metrics()["global"] == {"requested": 4, "allowed": 3, "used": 3, "dropped": 1}
    clock.advance(31)
    assert use(second, "current")["allowed"]


def test_serial_lease_survives_restart_and_uses_conservative_abandoned_timeout(tmp_path):
    clock = Clock()
    config = policy(clock)
    first = GlobalPPIBudget(tmp_path / "budget.sqlite", config, clock=clock.now)
    result = first.acquire("current", consumer="SCANNER")
    first.start(result["lease"])
    second = GlobalPPIBudget(first.path, config, clock=clock.now)
    blocked = second.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert not blocked["allowed"] and blocked["reason"] == "PPI_SERIAL_BACKPRESSURE"
    clock.advance(59)
    assert not second.acquire("intraday")["allowed"]
    clock.advance(2)
    assert use(second, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    with pytest.raises(BudgetBackpressure):
        first.start(result["lease"])


@pytest.mark.parametrize("code", ["PPI_HTTP_429", "PPI_SESSION_INVALID", "PPI_HTTP_401", "PPI_HTTP_403"])
def test_global_and_endpoint_breaker_crosses_engine_and_policy_boundaries(tmp_path, code):
    clock = Clock()
    config = policy(clock)
    first = GlobalPPIBudget(tmp_path / "budget.sqlite", config, clock=clock.now)
    result = first.acquire("intraday", consumer="SCALPING", priority="SCALPING_HOT")
    first.start(result["lease"])
    first.finish(result["lease"], error_code=code)
    changed = deepcopy(config)
    changed["recommendation_digest"] = "c" * 64
    second = GlobalPPIBudget(first.path, changed, clock=clock.now)
    blocked = second.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert not blocked["allowed"] and blocked["reason"] == "PPI_GLOBAL_CIRCUIT_OPEN"
    assert set(second.metrics()["circuits"]) == {"global", "intraday"}


def test_repeated_408_5xx_opens_both_affected_endpoint_and_global(tmp_path):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    for endpoint, status in (("current", 408), ("book", 503)):
        result = budget.acquire(endpoint)
        budget.start(result["lease"])
        budget.finish(result["lease"], status_code=status)
    assert not budget.acquire("intraday")["allowed"]
    assert budget.metrics()["circuits"]["global"]["code"] == "PPI_REPEATED_408_5XX"
    clock.advance(61)
    assert use(budget, "intraday")["allowed"]


def test_instrument_gap_is_scoped_and_empty_body_never_becomes_provider_zero(tmp_path):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    for code in ("PPI_INSTRUMENT_NOT_FOUND", "PPI_EMPTY_OR_NON_JSON_AFTER_RETRY"):
        result = budget.acquire("current")
        budget.start(result["lease"])
        budget.finish(result["lease"], error_code=code)
    assert use(budget, "book")["allowed"] and budget.metrics()["circuits"] == {}
    assert "provider_available" not in json.dumps(budget.metrics())


def test_clock_rollback_and_expired_capacity_deny_off_wire(tmp_path):
    clock = Clock()
    config = policy(clock)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", config, clock=clock.now)
    assert use(budget, "book")["allowed"]
    clock.advance(-1)
    with pytest.raises(BudgetBackpressure, match="CLOCK_ROLLBACK"):
        budget.acquire("current")
    clock.advance(3602)
    blocked = budget.acquire("current")
    assert not blocked["allowed"] and "EXPIRED" in blocked["reason"]


def test_approved_envelope_reserves_actual_opened_demand_once(wire):
    values = approved(wire)
    state = controller(values).state(values[-1])
    config = budget_policy(state, opened_count=2, planned_reservations={"SCALPING_HOT": {"book": 3, "intraday": 3}})
    assert config["priority_reserves"]["EXIT_CRITICAL"] == {"current": 0, "book": 12, "intraday": 0}
    assert config["priority_reserves"]["OPENED_CRITICAL"] == {"current": 2, "book": 2, "intraday": 2}
    assert config["global_limit"] == 45 and config["max_parallel_requests"] == 1


def test_actual_adapter_and_retry_are_budgeted_once_and_denial_never_sends(wire, tmp_path):
    clock, calls, behavior = wire
    config = policy(clock, limits={"current": 2, "book": 2, "intraday": 2}, global_limit=2)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", config, clock=clock.now)
    guard = ReadOnlyTransportGuard(budget=budget, consumer="SCANNER").install()
    behavior["mode"] = "nonjson"
    try:
        with pytest.raises(requests.exceptions.JSONDecodeError):
            retry_read(lambda: requests.get("https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Current").json(),
                retries=1, pause=clock.advance)
        assert len(calls) == 2
        with pytest.raises(BudgetBackpressure):
            requests.get("https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book")
        assert len(calls) == 2 and guard.calls_allowed == 2
        assert budget.metrics()["global"] == {"requested": 3, "allowed": 2, "used": 2, "dropped": 1}
    finally:
        guard.restore()


def test_real_sdk_reader_scopes_and_http_breaker_account_actual_market_endpoints(wire, tmp_path):
    clock, calls, behavior = wire
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    reader = ProductionMarketReader("PRIVATE_KEY", "PRIVATE_SECRET", budget=budget, consumer="SCALPING")
    try:
        reader.login_once()
        with reader.read_scope(priority="SCALPING_HOT", identity=("TEST000", "ACCIONES", "BYMA", "ARS", "A-24HS")):
            reader.intraday("TEST000", "ACCIONES", "A-24HS")
        behavior["status"] = 429
        with pytest.raises(Exception):
            reader.current("TEST000", "ACCIONES", "A-24HS")
        before = len(calls)
        with reader.read_scope(priority="EXIT_CRITICAL"):
            with pytest.raises(BudgetBackpressure):
                reader.book("TEST000", "ACCIONES", "A-24HS")
        assert len(calls) == before == 3  # login is outside the measured market envelope
        metrics = budget.metrics()
        assert metrics["global"]["used"] == 2 and metrics["global"]["dropped"] == 1
        assert metrics["by_endpoint"]["intraday"]["used"] == 1
        assert metrics["by_scope"][0]["consumer"] == "SCALPING"
        assert "PRIVATE" not in json.dumps(metrics)
    finally:
        reader.close()


def test_sdk_session_error_after_http200_is_global_not_a_provider_zero(tmp_path):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    reader = ProductionMarketReader("test-key", "test-secret", budget=budget)
    try:
        class Market:
            def intraday(self, *args): raise RuntimeError("invalid token")
        reader._market = lambda: Market()
        with pytest.raises(RuntimeError, match="invalid token"):
            reader.intraday("TEST", "ACCIONES", "A-24HS")
        assert not budget.acquire("book", priority="EXIT_CRITICAL")["allowed"]
        assert budget.metrics()["global"]["used"] == 0
    finally:
        reader.close()


@pytest.mark.parametrize("route", ["Order/Confirm", "Order/Budget", "Order/Cancel", "Account/Movements"])
def test_budget_cannot_weaken_original_real_route_guard(wire, tmp_path, route):
    clock, calls, _ = wire
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    guard = ReadOnlyTransportGuard(budget=budget).install()
    try:
        with pytest.raises(ReadOnlyPolicyViolation):
            requests.post("https://clientapi.portfoliopersonal.com/api/1.0/" + route)
        assert not calls and budget.metrics()["global"]["requested"] == 0
    finally:
        guard.restore()


def test_budget_sidecar_rejects_trading_db_symlink_hardlink_and_private_quota(tmp_path):
    import os
    clock = Clock()
    source = tmp_path / "source.sqlite"
    source.write_text("private fixture")
    alias = tmp_path / "alias.sqlite"
    alias.symlink_to(source)
    with pytest.raises(ValueError, match="ALIAS"):
        GlobalPPIBudget(alias, policy(clock), clock=clock.now)
    alias.unlink(); os.link(source, alias)
    with pytest.raises(ValueError, match="ALIAS"):
        GlobalPPIBudget(alias, policy(clock), clock=clock.now)
    alias.unlink()
    with pytest.raises(ValueError, match="ALIAS"):
        GlobalPPIBudget(source, policy(clock), protected=[source])
    tiny = policy(clock); tiny["maximum_bytes"] = 1024
    with pytest.raises(ValueError, match="BOUNDS"):
        GlobalPPIBudget(tmp_path / "tiny.sqlite", tiny, clock=clock.now)
    assert source.read_text() == "private fixture"


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_disappearing_sqlite_auxiliary_metadata_is_safe(tmp_path, monkeypatch, suffix):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    auxiliary = Path(str(budget.path) + suffix)
    auxiliary.write_bytes(b"transient fixture")
    original_lstat = Path.lstat
    disappeared = []
    def disappearing_lstat(path):
        if path == auxiliary and not disappeared:
            auxiliary.unlink()
            disappeared.append(path)
        return original_lstat(path)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", disappearing_lstat)
        budget._check_path()
    assert disappeared == [auxiliary] and not auxiliary.exists()
    assert use(budget, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]


def test_auxiliary_disappearance_after_exists_cannot_race_validated_metadata(tmp_path, monkeypatch):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    journal = Path(str(budget.path) + "-journal")
    journal.write_bytes(b"transient fixture")
    original_exists = Path.exists
    def visible_then_deleted(path):
        exists = original_exists(path)
        if path == journal and exists:
            journal.unlink()
        return exists
    # If an implementation checks exists before stat, SQLite can delete the
    # journal after the positive check. Reusing validated lstat metadata avoids
    # both that alias-check race and a second quota-accounting lookup.
    with monkeypatch.context() as patch:
        patch.setattr(Path, "exists", visible_then_deleted)
        budget._check_path()
    journal.unlink(missing_ok=True)
    assert use(budget, "current", consumer="SCANNER")["allowed"]


@pytest.mark.parametrize("kind", ["symlink", "dangling_symlink", "hardlink", "directory", "fifo", "protected", "quota"])
def test_auxiliary_aliases_and_quota_remain_fail_closed(tmp_path, kind):
    import os
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    journal = Path(str(budget.path) + "-journal")
    source = tmp_path / "protected-input.json"
    source.write_text("private fixture")
    if kind == "symlink":
        journal.symlink_to(source)
    elif kind == "dangling_symlink":
        journal.symlink_to(tmp_path / "missing-input.json")
    elif kind == "hardlink":
        os.link(source, journal)
    elif kind == "directory":
        journal.mkdir()
    elif kind == "fifo":
        os.mkfifo(journal, 0o600)
    elif kind == "protected":
        budget.protected.add(journal.resolve())
    else:
        with journal.open("wb") as stream:
            stream.truncate(budget.policy["maximum_bytes"])
    with pytest.raises(ValueError, match="CAPACITY_REACHED" if kind == "quota" else "ALIAS"):
        budget._check_path()
    assert source.read_text() == "private fixture"


@pytest.mark.parametrize("error", [PermissionError(13, "unreadable auxiliary"), OSError(5, "auxiliary IO failure")])
def test_auxiliary_metadata_os_errors_are_not_ignored(tmp_path, monkeypatch, error):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    journal = Path(str(budget.path) + "-journal")
    original_lstat = Path.lstat
    def unreadable_lstat(path):
        if path == journal:
            raise error
        return original_lstat(path)
    monkeypatch.setattr(Path, "lstat", unreadable_lstat)
    with pytest.raises(type(error)) as caught:
        budget._check_path()
    assert caught.value is error


def test_initialized_budget_restart_does_not_write_under_an_active_sqlite_writer(tmp_path):
    import sqlite3
    clock = Clock()
    path = tmp_path / "budget.sqlite"
    budget = GlobalPPIBudget(path, policy(clock), clock=clock.now)
    assert use(budget, "current", consumer="SCANNER")["allowed"]
    metrics = budget.metrics()
    before = path.read_bytes()
    with sqlite3.connect(path, timeout=.005) as writer:
        writer.execute("BEGIN IMMEDIATE")
        restarted = GlobalPPIBudget(path, policy(clock), clock=clock.now)
        assert restarted.metrics() == metrics and path.read_bytes() == before
        writer.rollback()
    assert use(restarted, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]


def _hold_native_sqlite_writer(path, ready, release):
    import sqlite3
    with sqlite3.connect(path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE budget_state SET value=value WHERE key='schema'")
        ready.set()
        assert release.wait(timeout=2)


def test_admission_waits_bounded_for_a_changing_native_sqlite_writer(tmp_path):
    import threading
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    ctx = multiprocessing.get_context("fork")
    ready, release = ctx.Event(), ctx.Event()
    writer = ctx.Process(target=_hold_native_sqlite_writer, args=(str(budget.path), ready, release))
    writer.start()
    assert ready.wait(timeout=2)
    finish = threading.Timer(.02, release.set)
    finish.start()
    try:
        admitted = use(budget, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
    finally:
        release.set()
        finish.cancel()
        writer.join(timeout=2)
    assert writer.exitcode == 0 and admitted["allowed"]
    assert budget.metrics()["global"] == {"requested": 1, "allowed": 1, "used": 1, "dropped": 0}


def _process_admission(path, config, at, ready, output):
    clock = Clock(at)
    budget = GlobalPPIBudget(path, config, clock=clock.now)
    ready.wait()
    count = 0
    for _ in range(4):
        if use(budget, "book", consumer="SCANNER")["allowed"]:
            count += 1
    output.put(count)


def test_two_processes_cannot_claim_the_same_full_budget(tmp_path):
    clock = Clock()
    config = policy(clock, limits={"current": 3, "book": 3, "intraday": 3}, global_limit=3)
    path = str(tmp_path / "budget.sqlite")
    budget = GlobalPPIBudget(path, config, clock=clock.now)
    ctx = multiprocessing.get_context("fork")
    ready, output = ctx.Event(), ctx.Queue()
    workers = [ctx.Process(target=_process_admission, args=(path, config, clock.now().isoformat(), ready, output)) for _ in range(2)]
    for p in workers: p.start()
    ready.set()
    for p in workers:
        p.join(timeout=10)
        assert p.exitcode == 0
    count = sum(output.get(timeout=2) for _ in workers)
    assert 0 < count <= 3
    metrics = budget.metrics()["global"]
    assert metrics["allowed"] == metrics["used"] == count
    assert metrics["requested"] == metrics["allowed"] + metrics["dropped"] == 8


@pytest.mark.parametrize("invalid", ["expired", "approval_corrupt", "policy_corrupt", "native_env_invalid", "opened_ledger_missing"])
def test_previously_active_budget_falls_back_and_exit_reader_remains_live(wire, tmp_path, invalid):
    from be_paper_engine import PaperStore
    values = approved(wire)
    clock = wire[0]
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    ctl = controller(values)
    runtime = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    assert use(runtime, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    if invalid == "expired":
        clock.advance(2800)
    elif invalid == "approval_corrupt":
        ctl.inputs["approval"]["approved"] = False
    elif invalid == "policy_corrupt":
        ctl.inputs["policy"] = {"mode": "BAD"}
    else:
        ctl.inputs["approval"]["approved"] = False
        if invalid == "native_env_invalid":
            ctl.environ.update({k: "invalid" for k in ("PAPER_ACTIVE_SYMBOL_LIMIT", "PAPER_INTRADAY_BATCH_LIMIT",
                "PAPER_OBSERVER_INTERVAL_SECONDS", "PAPER_INTRADAY_SCAN_SECONDS", "PAPER_MAX_OPEN_POSITIONS")})
        else:
            with store.connect() as c:
                c.execute("ALTER TABLE paper_positions RENAME TO missing_opened_ledger")
    result = use(runtime, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert result["allowed"]
    assert ctl.state(clock.now())["production_limits_modified"] is False
    assert runtime.budget.policy["version"] == "rc6-global-ppi-safe-factual-baseline-v1"
    assert runtime.budget.policy["endpoint_limits"]["current"] == 20
    assert runtime.budget.policy["endpoint_limits"]["intraday"] == 40
    assert "NO_VERIFICADO" in runtime.budget.metrics()["policy_authority"]


def test_invalid_approval_and_restart_cannot_erase_prior_global_circuit(wire, tmp_path):
    from be_paper_engine import PaperStore
    values = approved(wire)
    clock = wire[0]
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    ctl = controller(values)
    runtime = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    result = runtime.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
    runtime.start(result["lease"])
    runtime.finish(result["lease"], status_code=429)
    ctl.inputs["approval"]["approved"] = False
    restored = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    assert not restored.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    assert restored.budget.metrics()["circuits"]["global"]["code"] == "PPI_HTTP_429"
    clock.advance(61)
    assert use(restored, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
