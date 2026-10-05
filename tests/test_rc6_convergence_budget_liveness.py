"""#468/#469 budget counterexamples through real offline PAPER callers."""
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
import hashlib
import multiprocessing
import sqlite3
import threading
import time
from urllib.parse import urlsplit

import pytest
import requests

import bf_production_paper_observer as observer
from bd_ppi_readonly_guard import ProductionMarketReader
from bq_exit_policy import PaperSessionPolicy
from bv_paper_runtime import collect_exit_books
from rc6_paper_family_lifecycle import FamilyPaperExecutor
from rc6_ppi_future_contract_policy import standard_dlr_terms
from rc6_ppi_global_budget import (
    BudgetBackpressure, GlobalPPIBudget, RuntimePPIBudget, WINDOW_FIELDS,
    budget_policy, exit_capacity_contract, supervisable_position_count, validate_policy,
    runtime_budget_snapshot,
)
from tests.test_issue465_budget_adversarial import native_opened_store, scoped_book
from tests.test_issue465_budget_adversarial import _single_flight_worker
from tests.test_rc6_future_paper_lifecycle import dlr
from tests.test_rc6_ppi_capacity_benchmark import Clock, wire
from tests.test_rc6_ppi_global_budget import policy, use


SCOPES = (
    ("book", "SCANNER", "OPENED_CRITICAL"),
    ("intraday", "SCALPING", "SCALPING_HOT"),
    ("intraday", "SCALPING", "WARM"),
    ("intraday", "SCALPING", "DISCOVERY"),
    ("current", "SCANNER", "STRATEGY_HOT"),
    ("book", "SCANNER", "WARM"),
)
IDENTITY = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")


def state_from_policy(value):
    return {
        "status": "APPROVED_DYNAMIC",
        "global_budget": {key: deepcopy(value[key]) for key in (
            "window_seconds", "endpoint_limits", "global_limit", "safety_reserve")},
        "budget_settings": {key: value[key] for key in (
            "critical_book_seconds", "lease_seconds", "breaker_seconds",
            "session_breaker_seconds", "server_error_threshold", "maximum_bytes")},
        "recommendation_digest": value["recommendation_digest"],
        "configuration_fingerprint": value["configuration_fingerprint"],
        "expires_at": value["expires_at"], "engine_profiles": {},
    }


def critical_policy(clock, *, opened=1, cadence=5, common=1):
    value = policy(clock, limits={"current": 20, "book": opened * 30 // cadence + common, "intraday": 20})
    value["critical_book_seconds"] = cadence
    return budget_policy(state_from_policy(value), opened_count=opened)


class ExactController:
    """Caller seam substitutes only reviewed capacity evidence, never PPI."""
    input_paths = ()
    environ = {}

    def __init__(self, state):
        self.current = state

    def state(self, _at=None):
        return deepcopy(self.current)

    def shadow_report(self):
        return {}


def test_u01_real_exit_caller_joins_slow_opened_owner_in_three_windows(wire, tmp_path, monkeypatch):
    clock, calls, _ = wire
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=1)
    monkeypatch.setattr(observer, "now_iso", lambda: clock.now().isoformat())
    entered = threading.Event()
    original = requests.adapters.HTTPAdapter.send

    def slow_send(adapter, request, **kwargs):
        if urlsplit(request.url).path.lower().endswith("/book"):
            entered.set()
            time.sleep(.120)
        return original(adapter, request, **kwargs)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", slow_send)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", critical_policy(clock), clock=clock.now)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=budget, consumer="SCANNER")
    owner_results = []
    try:
        reader.login_once()
        for cycle in range(3):
            entered.clear()

            def owner():
                try:
                    owner_results.append(scoped_book(reader, IDENTITY, "OPENED_CRITICAL"))
                except BaseException as error:
                    owner_results.append(error)

            thread = threading.Thread(target=owner)
            thread.start()
            assert entered.wait(2)
            started = time.monotonic()
            failures = collect_exit_books(reader, store, PaperSessionPolicy(), clock.now().isoformat())
            elapsed = time.monotonic() - started
            thread.join(2)
            assert not thread.is_alive() and failures == 0
            assert .05 < elapsed < budget.policy["critical_book_seconds"]
            assert len([url for _, url in calls if "/Book?" in url]) == cycle + 1
            assert budget.metrics()["exit_service"]["lower_suspended"] is False
            clock.advance(31)
    finally:
        reader.close()
    assert all(isinstance(result, dict) for result in owner_results)
    metrics = budget.metrics()
    assert metrics["by_endpoint"]["book"]["used"] == 3
    assert len(metrics["exit_service"]["by_identity_digest"]) == 1
    with store.connect() as c:
        assert c.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0
    raw = budget.path.read_bytes()
    for secret in (b"OFFLINE_KEY", b"OFFLINE_SECRET", b"NEVER_PERSIST_THIS_TOKEN"):
        assert secret not in raw


def test_u01_deadline_is_monotonic_and_degradation_survives_restart_without_second_wire(wire, tmp_path, monkeypatch):
    clock, calls, _ = wire
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=1)
    monkeypatch.setattr(observer, "now_iso", lambda: clock.now().isoformat())
    entered, release = threading.Event(), threading.Event()
    original = requests.adapters.HTTPAdapter.send

    def paused_send(adapter, request, **kwargs):
        if urlsplit(request.url).path.lower().endswith("/book"):
            entered.set()
            assert release.wait(4)
        return original(adapter, request, **kwargs)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", paused_send)
    value = critical_policy(clock, cadence=1)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=budget, consumer="SCANNER")
    results = []

    def owner():
        try:
            results.append(scoped_book(reader, IDENTITY, "OPENED_CRITICAL"))
        except BaseException as error:
            results.append(error)

    thread = threading.Thread(target=owner)
    try:
        reader.login_once()
        before = clock.now()
        thread.start()
        assert entered.wait(2)
        started = time.monotonic()
        assert collect_exit_books(reader, store, PaperSessionPolicy(), before.isoformat()) == 1
        elapsed = time.monotonic() - started
        assert .9 <= elapsed < 1.4 and clock.now() == before
        restarted = GlobalPPIBudget(budget.path, value, clock=clock.now)
        service = restarted.metrics()["exit_service"]
        assert service["lower_suspended"]
        assert {p["status"] for p in service["pressure_by_identity_digest"].values()} == {"DEGRADED"}
        lower = restarted.acquire("current", consumer="SCANNER", priority="DISCOVERY")
        assert not lower["allowed"] and lower["reason"] == "PPI_EXIT_DEADLINE_LOWER_SUSPENDED"
        # The original request is still the sole owner; no pretend HTTP
        # preemption, new receipt or overlapping provider request occurred.
        assert restarted.metrics()["global"]["used"] == 1
        release.set()
        thread.join(2)
        assert not thread.is_alive() and isinstance(results[0], dict)
        assert collect_exit_books(reader, store, PaperSessionPolicy(), clock.now().isoformat()) == 0
        assert budget.metrics()["exit_service"]["lower_suspended"] is False
        assert len([url for _, url in calls if "/Book?" in url]) == 1
        assert use(restarted, "current", consumer="SCANNER", priority="DISCOVERY")["allowed"]
    finally:
        release.set()
        thread.join(3)
        reader.close()


@pytest.mark.parametrize("book_cap,global_cap", [(15, 60), (30, 10), (5, 45)])
def test_u05_impossible_exit_envelope_blocks_every_identity_in_three_windows(tmp_path, monkeypatch, book_cap, global_cap):
    clock = Clock()
    opened = 10 if book_cap == 5 else 5
    value = policy(clock, limits={"current": 20, "book": book_cap, "intraday": 40}, global_limit=global_cap)
    ctl = ExactController(state_from_policy(value))
    monkeypatch.setenv("PAPER_EMERGENCY_MAX_OPEN_POSITIONS", str(opened))
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=opened)
    runtime = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    for _ in range(3):
        contract = exit_capacity_contract(ctl.current, opened_count=opened)
        assert contract["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
        assert contract["exit_demand"]["book"] == opened * 6
        for priority in ("DISCOVERY", "OPENED_CRITICAL", "EXIT_CRITICAL"):
            with pytest.raises(BudgetBackpressure, match="PPI_EXIT_CAPACITY_INSUFFICIENT"):
                runtime.acquire("book", consumer="EXIT_READER", priority=priority)
        assert runtime.activation_contract == contract
        assert runtime.budget is None and not runtime.path.exists()
        clock.advance(31)


def high_floor_policy(clock, maximum_bytes):
    limits = dict(current=10000, book=10000, intraday=10000)
    value = policy(clock, limits=limits, reserves={"EXIT_CRITICAL": limits})
    return value | {"maximum_bytes": maximum_bytes,
        "expires_at": (clock.now() + timedelta(hours=8)).isoformat()}


@pytest.mark.parametrize("maximum_bytes,seconds", [(65536, 120), (8 * 1024**2, 3100)])
def test_u02_six_scopes_per_second_stay_bounded_and_exit_recovers_after_aging(tmp_path, maximum_bytes, seconds):
    clock = Clock()
    value = high_floor_policy(clock, maximum_bytes)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    for _ in range(seconds):
        for endpoint, consumer, priority in SCOPES:
            result = budget.acquire(endpoint, consumer=consumer, priority=priority)
            assert not result["allowed"] and result["reason"] == "PPI_HIGH_PRIORITY_RESERVE_BACKPRESSURE"
        clock.advance(1)
    metrics = budget.metrics()
    assert metrics["global"] == {"requested": seconds * 6, "allowed": 0, "used": 0, "dropped": seconds * 6}
    assert metrics["telemetry_retention"]["evicted_buckets"] > 0
    if maximum_bytes == 65536:
        assert metrics["telemetry_retention"]["status"] == "BOUNDED_PARTIAL"
    else:
        assert metrics["telemetry_retention"]["window_complete"]
    assert budget.path.stat().st_size < maximum_bytes // 2
    clock.advance(4000)
    restarted = GlobalPPIBudget(budget.path, value, clock=clock.now)
    assert use(restarted, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    assert restarted.metrics()["global"]["used"] == 1
    with closing(sqlite3.connect(budget.path)) as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def fill_legacy_telemetry_until_sql_full(budget, clock, scopes):
    row = {**dict.fromkeys(WINDOW_FIELDS, 0), "denial_reason": {"PPI_HIGH_PRIORITY_RESERVE_BACKPRESSURE": 1}}
    bucket = json.dumps({":".join(scope): row for scope in scopes}, separators=(",", ":"))
    with closing(sqlite3.connect(budget.path)) as c:
        page_size = c.execute("PRAGMA page_size").fetchone()[0]
        c.execute(f"PRAGMA max_page_count={budget.policy['maximum_bytes'] // (2 * page_size)}")
        for offset in range(10000):
            try:
                c.execute("INSERT OR REPLACE INTO budget_state VALUES(?,?)",
                    ("window:" + str(int(clock.now().timestamp())), bucket))
                c.commit()
                clock.advance(1)
            except sqlite3.OperationalError as error:
                c.rollback()
                assert error.sqlite_errorcode == sqlite3.SQLITE_FULL
                return offset
    raise AssertionError("legacy sidecar never reached its physical SQL quota")


@pytest.mark.parametrize("maximum_bytes,scopes", [(65536, SCOPES[:1]), (8 * 1024**2, SCOPES)])
def test_u02_genuinely_full_legacy_sqlite_recovers_on_restart_and_keeps_live_wire_debt(tmp_path, maximum_bytes, scopes, monkeypatch):
    clock = Clock()
    value = high_floor_policy(clock, maximum_bytes)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    assert use(budget, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    offset = fill_legacy_telemetry_until_sql_full(budget, clock, scopes)
    assert offset > 0
    # A real previous sender's already-used receipt is recent at restart.
    # It must survive telemetry recovery, not be reset with a fresh sidecar.
    with closing(sqlite3.connect(budget.path)) as c, c:
        c.execute("INSERT INTO budget_requests VALUES('live-debt',?,'book','EXIT_READER','EXIT_CRITICAL',1)",
            (clock.now().timestamp() - .5,))
        c.execute("UPDATE budget_totals SET requested=requested+1,allowed=allowed+1,used=used+1 WHERE endpoint='book'")
    original = sqlite3.connect
    peak_bytes = [0]

    def measured_connect(*args, **kwargs):
        c = original(*args, **kwargs)

        def sample(_statement):
            size = 0
            for suffix in ("", "-journal", "-wal", "-shm"):
                try:
                    size += budget.path.with_name(budget.path.name + suffix).stat().st_size
                except FileNotFoundError:
                    pass
            peak_bytes[0] = max(peak_bytes[0], size)

        c.set_trace_callback(sample)
        return c

    monkeypatch.setattr(sqlite3, "connect", measured_connect)
    restarted = GlobalPPIBudget(budget.path, value, clock=clock.now)
    assert use(restarted, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    with closing(sqlite3.connect(budget.path)) as c:
        assert c.execute("SELECT used FROM budget_requests WHERE lease='live-debt'").fetchone() == (1,)
    assert restarted.metrics()["global"]["used"] == 3
    clock.advance(4000)
    again = GlobalPPIBudget(budget.path, value, clock=clock.now)
    assert use(again, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    assert again.metrics()["global"]["used"] == 4
    assert peak_bytes[0] <= maximum_bytes


def test_u07_five_spots_and_five_active_futures_share_reserved_exit_capacity_after_restart(tmp_path, monkeypatch):
    # Five independently supported 2026 monthly series are unexpired here.
    clock = Clock("2026-07-01T14:00:00+00:00")
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=5)
    executor = FamilyPaperExecutor(store)
    for index, month in enumerate(("AGO", "SEP", "OCT", "NOV", "DIC")):
        symbol = f"DLR/{month}26"
        contract = replace(dlr(), symbol=symbol, expires_at=standard_dlr_terms(symbol)["expires_at"])
        executor.open_future(contract, lifecycle_id=f"FUT-{index}", event_id=f"OPEN-FUT-{index}",
            entry_price="1500", quantity="1", entry_cost="100", occurred_at=clock.now().isoformat())
    assert supervisable_position_count(store.path) == 10
    value = policy(clock, limits=dict(current=5, book=60, intraday=5), global_limit=60)
    ctl = ExactController(state_from_policy(value))
    runtime = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    actual = runtime._current()
    assert actual.policy["open_positions_count"] == 10
    assert actual.policy["exit_demand"]["book"] == 60
    for endpoint in ("current", "book", "intraday"):
        assert not runtime.acquire(endpoint, consumer="SCALPING", priority="DISCOVERY")["allowed"]
    assert use(runtime, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    restored = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    assert restored._current().policy["open_positions_count"] == 10
    assert restored.budget.metrics()["global"]["used"] == 1
    assert use(restored, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    impossible = deepcopy(ctl.current)
    impossible["global_budget"]["endpoint_limits"]["book"] = 5
    impossible["global_budget"]["global_limit"] = 15
    failed = RuntimePPIBudget(store.path, ExactController(impossible), clock=clock.now)
    with pytest.raises(BudgetBackpressure, match="PPI_EXIT_CAPACITY_INSUFFICIENT"):
        failed.acquire("book", consumer="SCANNER", priority="DISCOVERY")
    assert failed.activation_contract["open_positions_count"] == 10
    with store.connect() as c:
        assert c.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM paper_future_positions WHERE status='ACTIVE'").fetchone()[0] == 5


def test_u07_unknown_ledger_is_not_misreported_as_zero(tmp_path):
    assert supervisable_position_count(tmp_path / "missing.sqlite") is None
    assert supervisable_position_count(tmp_path / "missing.sqlite", 99) == 99
    assert not (tmp_path / "missing.sqlite").exists()


def inventory(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()}


def test_budget_observation_absent_is_read_only_and_does_not_bootstrap(tmp_path):
    from cg_paper_workspace import artifact_root
    database = tmp_path / "paper.sqlite"
    before = inventory(tmp_path)
    result = runtime_budget_snapshot(database, as_of=Clock().now())
    assert result["status"] == "ABSENT" and result["exit_service"] is None
    assert inventory(tmp_path) == before
    assert not artifact_root(database).exists()


@pytest.mark.parametrize("degraded", [False, True])
def test_budget_observation_exposes_committed_sanitized_state_without_writes(tmp_path, monkeypatch, degraded):
    clock = Clock()
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=1)
    ctl = ExactController(state_from_policy(critical_policy(clock)))
    runtime = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    budget = runtime._current()
    assert use(runtime, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    key = "a" * 64
    if degraded:
        budget._exit_degraded(key, "PPI_BOOK_EXIT_DEADLINE_EXCEEDED", 5.1)
    before = inventory(tmp_path)
    observed = runtime_budget_snapshot(store.path, as_of=clock.now())
    assert observed["status"] == ("DEGRADED" if degraded else "OBSERVED")
    assert observed["exit_service"]["lower_suspended"] is degraded
    assert observed["global"]["used"] == 1
    assert observed["source_last_clock"] == clock.now().timestamp() and observed["age_seconds"] == 0
    assert inventory(tmp_path) == before
    assert "GGAL" not in json.dumps(observed)


@pytest.mark.parametrize("foreign", ["wal", "alias", "corrupt", "future_clock", "toxic_field"])
def test_budget_observation_foreign_state_stays_explicitly_unverified_and_unchanged(tmp_path, monkeypatch, foreign):
    clock = Clock()
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=1)
    runtime = RuntimePPIBudget(store.path, ExactController(state_from_policy(critical_policy(clock))), clock=clock.now)
    budget = runtime._current()
    assert use(runtime, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    if foreign == "wal":
        with closing(sqlite3.connect(budget.path)) as c:
            c.execute("PRAGMA journal_mode=WAL")
        assert not budget.path.with_name(budget.path.name + "-shm").exists()
    elif foreign == "alias":
        original = budget.path.with_name("original.sqlite")
        budget.path.rename(original)
        budget.path.symlink_to(original)
    elif foreign == "corrupt":
        budget.path.write_bytes(b"incomplete SQLite header")
    elif foreign == "future_clock":
        with closing(sqlite3.connect(budget.path)) as c, c:
            c.execute("UPDATE budget_state SET value=? WHERE key='last_clock'", (str(clock.now().timestamp() + 1),))
    else:
        with closing(sqlite3.connect(budget.path)) as c, c:
            c.execute("INSERT OR REPLACE INTO budget_state VALUES('critical_exit_pressure',?)",
                (json.dumps({"a" * 64: {"status": "DEGRADED", "reason": "PRIVATE_BROKER_BODY", "until": 1}}),))
    before = inventory(tmp_path)
    result = runtime_budget_snapshot(store.path, as_of=clock.now())
    assert result["status"] == "UNVERIFIED" and result["exit_service"] is None
    assert "PRIVATE_BROKER_BODY" not in json.dumps(result)
    assert inventory(tmp_path) == before


@pytest.mark.parametrize("exclusive", [False, True])
def test_budget_observation_does_not_publish_uncommitted_pressure_or_touch_locked_source(tmp_path, monkeypatch, exclusive):
    clock = Clock()
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=1)
    runtime = RuntimePPIBudget(store.path, ExactController(state_from_policy(critical_policy(clock))), clock=clock.now)
    budget = runtime._current()
    assert use(runtime, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    with closing(sqlite3.connect(budget.path)) as writer:
        writer.execute("BEGIN EXCLUSIVE" if exclusive else "BEGIN IMMEDIATE")
        writer.execute("INSERT OR REPLACE INTO budget_state VALUES('critical_exit_pressure',?)",
            (json.dumps({"a" * 64: {"status": "DEGRADED", "reason": "PPI_BOOK_EXIT_DEADLINE_EXCEEDED", "until": clock.now().timestamp()+60}}),))
        before = inventory(tmp_path)
        started = time.monotonic()
        observed = runtime_budget_snapshot(store.path, as_of=clock.now())
        assert time.monotonic() - started < .2
        assert observed["status"] == ("UNVERIFIED" if exclusive else "OBSERVED")
        assert inventory(tmp_path) == before
        writer.rollback()


def test_u01_slow_opened_owner_coalesces_across_real_processes_without_duplicate_fetch(tmp_path):
    clock = Clock()
    config = critical_policy(clock)
    path = str(tmp_path / "budget.sqlite")
    GlobalPPIBudget(path, config, clock=clock.now)
    context = multiprocessing.get_context("fork")
    ready, release = context.Event(), context.Event()
    output, calls = context.Queue(), context.Value("i", 0)
    children = [context.Process(target=_single_flight_worker,
        args=(path, config, clock.now().isoformat(), ready, release, output, calls, owner)) for owner in (True, False)]
    children[0].start()
    assert ready.wait(2)
    children[1].start()
    timer = threading.Timer(.120, release.set)
    timer.start()
    try:
        for child in children:
            child.join(4)
            assert not child.is_alive() and child.exitcode == 0
        results = dict(output.get(timeout=1) for _ in children)
        assert results == {True: clock.now().isoformat(), False: clock.now().isoformat()}
        assert calls.value == 1
    finally:
        release.set()
        timer.cancel()
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(2)


def test_u01_exit_owner_alarms_during_its_existing_http_body_without_cancelling_wire(wire, tmp_path, monkeypatch):
    clock, calls, _ = wire
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=1)
    runtime = RuntimePPIBudget(store.path, ExactController(state_from_policy(critical_policy(clock, cadence=1))), clock=clock.now)
    entered, release = threading.Event(), threading.Event()
    original = requests.adapters.HTTPAdapter.send

    def held_send(adapter, request, **kwargs):
        if urlsplit(request.url).path.lower().endswith("/book"):
            entered.set()
            assert release.wait(4)
        return original(adapter, request, **kwargs)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", held_send)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=runtime, consumer="EXIT_READER")
    outcome = []

    def owner():
        try:
            outcome.append(scoped_book(reader, IDENTITY, "EXIT_CRITICAL"))
        except BaseException as error:
            outcome.append(error)

    thread = threading.Thread(target=owner)
    try:
        reader.login_once()
        thread.start()
        assert entered.wait(2)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            observed = runtime_budget_snapshot(store.path, as_of=clock.now())
            if observed["status"] == "DEGRADED":
                break
            time.sleep(.005)
        assert observed["status"] == "DEGRADED" and thread.is_alive()
        assert observed["exit_service"]["lower_suspended"]
        denied = runtime.acquire("current", consumer="SCANNER", priority="DISCOVERY")
        assert not denied["allowed"] and denied["reason"] == "PPI_EXIT_DEADLINE_LOWER_SUSPENDED"
        release.set()
        thread.join(2)
        assert not thread.is_alive()
        assert isinstance(outcome[0], BudgetBackpressure) and str(outcome[0]) == "PPI_BOOK_EXIT_DEADLINE_EXCEEDED"
        assert scoped_book(reader, IDENTITY, "EXIT_CRITICAL")["bids"]
        assert runtime_budget_snapshot(store.path, as_of=clock.now())["status"] == "OBSERVED"
        assert len([url for _, url in calls if "/Book?" in url]) == 1
    finally:
        release.set()
        thread.join(3)
        reader.close()
