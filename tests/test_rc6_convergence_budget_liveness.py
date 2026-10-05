"""#468/#469 budget counterexamples through real offline PAPER callers."""
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
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
from rc6_dynamic_universe.common import digest
from rc6_dynamic_universe.promotion import APPROVAL_SCHEMA, DEFAULT_POLICY, build_recommendation
from tests.test_rc6_capacity_promotion import approved, controller
from tests.test_issue465_budget_adversarial import native_opened_store, scoped_book
from tests.test_issue465_budget_adversarial import _single_flight_worker
from tests.test_issue465_budget_adversarial import exit_floor
from tests.test_rc6_future_paper_lifecycle import dlr
from tests.test_rc6_ppi_capacity_benchmark import Clock, wire, measure
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
    # Two expired futures remain pending in this explicit offline ledger.
    # Their historical execution rule is synthetic, never provider evidence.
    clock = Clock("2026-10-05T14:00:00+00:00")
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=5)
    seed_offline_pending_futures(store, monkeypatch)
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
        assert c.execute("SELECT COUNT(*) FROM paper_future_positions WHERE status='ACTIVE' AND julianday(expires_at)<julianday(?)",
            (clock.now().isoformat(),)).fetchone()[0] == 2


def seed_offline_pending_futures(store, monkeypatch):
    """Native lifecycle fixture, without inventing historical A3/PPI evidence.

    A dated synthetic execution-grid rule permits simulated August entries.
    The source remains explicitly synthetic at the October supervision cutoff.
    Five exact series, including two overdue ACTIVE positions, never duplicate
    an identity or authorize a new provider entry.
    """
    import rc6_ppi_future_contract_policy as contract_policy
    original = contract_policy.standard_dlr_terms
    source = "OFFLINE_SYNTHETIC_PENDING_LEDGER_ONLY"
    known_at = "2026-08-24T13:00:00+00:00"

    def offline_terms(symbol):
        terms = original(symbol)
        return terms | {"price_tick_source": source, "price_tick_known_at": known_at} if terms else None

    monkeypatch.setattr(contract_policy, "standard_dlr_terms", offline_terms)
    executor = FamilyPaperExecutor(store)
    for index, month in enumerate(("AGO", "SEP", "OCT", "NOV", "DIC")):
        symbol = f"DLR/{month}26"
        terms = offline_terms(symbol)
        contract = replace(dlr(), symbol=symbol, metadata_source=source, expires_at=terms["expires_at"],
            price_tick=Decimal(terms["price_tick"]), price_tick_source=source, price_tick_known_at=known_at)
        executor.open_future(contract, lifecycle_id=f"FUT-{index}", event_id=f"OPEN-FUT-{index}",
            entry_price="1500", quantity="1", entry_cost="100", occurred_at="2026-08-24T14:00:00+00:00",
            detail={"provider_entry_history_status": "NO_VERIFICADO", "real_orders_sent": 0,
                "historical_grid_effectivity": "OFFLINE_SYNTHETIC", "entry_authority": False})


def test_u07_unknown_ledger_is_not_misreported_as_zero(tmp_path):
    assert supervisable_position_count(tmp_path / "missing.sqlite") is None
    assert supervisable_position_count(tmp_path / "missing.sqlite", 99) == 99
    assert not (tmp_path / "missing.sqlite").exists()


def test_u05_capacity_cannot_claim_more_identities_than_complete_round_can_observe():
    clock = Clock()
    value = policy(clock, limits=dict(current=1000, book=1000, intraday=1000), global_limit=1000)
    # Sixty-four identities at five seconds would itself overflow the retained
    # receipt ceiling. Isolate the distinct tracking bound with a feasible
    # explicitly configured cadence/quota, rather than claiming that envelope.
    value.update(critical_book_seconds=60, maximum_bytes=32 * 1024**2)
    state = state_from_policy(value)
    assert exit_capacity_contract(state, opened_count=64)["status"] == "READY"
    blocked = exit_capacity_contract(state, opened_count=65)
    assert blocked["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
    assert blocked["identity_tracking_limit"] == 64 and blocked["identity_tracking_gap"] == 1
    assert not blocked["global_gap"] and not any(blocked["endpoint_gaps"].values())
    with pytest.raises(ValueError, match="PPI_EXIT_CAPACITY_INSUFFICIENT"):
        budget_policy(state, opened_count=65)
    malformed = value | {"open_positions_count": 65}
    with pytest.raises(ValueError, match="PPI_EXIT_IDENTITY_TRACKING_INSUFFICIENT"):
        validate_policy(malformed)


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


def measured_small_approval(fake_wire):
    """Use measured native SDK calls; never forge a smaller approval envelope."""
    report = measure(fake_wire, cadence_seconds=30)
    at = fake_wire[0].now()
    configuration = deepcopy(DEFAULT_POLICY)
    configuration["safety_factor"] = .25
    recommendation = build_recommendation(report, policy=configuration, as_of=at)
    assert recommendation["status"] == "SHADOW_RECOMMENDATION"
    assert recommendation["global_budget"]["endpoint_limits"]["book"] == 5
    approval = {"schema": APPROVAL_SCHEMA, "approved": True, "reviewer": "explicit-offline-review",
        "reviewed_at": at.isoformat(), "recommendation_digest": recommendation["recommendation_digest"],
        "runtime_policy_fingerprint": recommendation["runtime_policy_fingerprint"]}
    approval["approval_digest"] = digest(approval)
    configuration.update(mode="APPROVED", approved_recommendation_digest=recommendation["recommendation_digest"])
    return configuration, recommendation, report, approval, at


@pytest.mark.parametrize("case", ["five_spots_cap15", "five_spots_cap5", "five_spots_five_active_cap5", "missing_ledger"])
def test_u05_real_controller_cold_activation_failure_blocks_every_native_market_send(wire, tmp_path, monkeypatch, case):
    clock, calls, _ = wire
    clock.at = Clock("2026-10-05T14:00:00+00:00").at
    values = approved(wire) if case == "five_spots_cap15" else measured_small_approval(wire)
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=5)
    if case == "five_spots_five_active_cap5":
        seed_offline_pending_futures(store, monkeypatch)
    elif case == "missing_ledger":
        with store.connect() as c:
            c.execute("ALTER TABLE paper_positions RENAME TO missing_opened_ledger")
    ctl = controller(values, database=store.path)
    runtime = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=runtime, consumer="SCANNER")
    reason = "CAPACITY_OPENED_LEDGER_UNVERIFIED" if case == "missing_ledger" else "PPI_EXIT_CAPACITY_INSUFFICIENT"
    try:
        reader.login_once()
        before = len(calls)
        for priority in ("DISCOVERY", "OPENED_CRITICAL", "EXIT_CRITICAL"):
            for endpoint in ("current", "book", "intraday"):
                with reader.read_scope(priority=priority, identity=IDENTITY):
                    with pytest.raises(BudgetBackpressure, match=reason):
                        getattr(reader, endpoint)(IDENTITY[0], IDENTITY[1], IDENTITY[4])
        assert len(calls) == before
        assert runtime.budget is None and not runtime.path.exists()
        assert runtime.activation_contract["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
        expected_count = None if case == "missing_ledger" else 10 if "five_active" in case else 5
        assert runtime.activation_contract["open_positions_count"] == expected_count
        if expected_count is not None:
            assert runtime.activation_contract["exit_demand"]["book"] == expected_count * 6
        with store.connect() as c:
            assert c.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0
    finally:
        reader.close()


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("failure", ["insufficient", "missing_ledger"])
def test_u05_blocked_reactivation_reuses_only_existing_exit_caps_cadence_and_debt(wire, tmp_path, monkeypatch, restart, failure):
    clock, calls, _ = wire
    values = measured_small_approval(wire)
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=0)
    ctl = controller(values, database=store.path)
    runtime = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=runtime, consumer="SCANNER")
    try:
        reader.login_once()
        reader.current(IDENTITY[0], IDENTITY[1], IDENTITY[4])
        original_policy = deepcopy(runtime.budget.policy)
        assert runtime.budget.metrics()["global"]["used"] == 1
    finally:
        reader.close()
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=5)
    reason = "PPI_EXIT_CAPACITY_INSUFFICIENT"
    if failure == "missing_ledger":
        with store.connect() as c:
            c.execute("ALTER TABLE paper_positions RENAME TO missing_opened_ledger")
        reason = "CAPACITY_OPENED_LEDGER_UNVERIFIED"
    if restart:
        runtime = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=runtime, consumer="EXIT_READER")
    try:
        reader.login_once()
        before = len(calls)
        with pytest.raises(BudgetBackpressure, match=reason):
            scoped_book(reader, IDENTITY, "OPENED_CRITICAL")
        assert len(calls) == before
        assert scoped_book(reader, IDENTITY, "EXIT_CRITICAL")["bids"]
        assert len(calls) == before + 1
        assert runtime.budget.policy == original_policy
        assert runtime.budget.metrics()["global"]["used"] == 2
        assert runtime.activation_contract["status"] == "ACTIVATION_BLOCKED_EXIT_CAPACITY"
        if failure == "missing_ledger":
            assert runtime.activation_contract["exit_demand"] is None
        else:
            assert runtime.activation_contract["exit_demand"]["book"] == 30
        # Existing receipts still enforce the reviewed cap5. No promised
        # reserve is truncated, and no impossible activation becomes READY.
        for _ in range(4):
            assert use(runtime, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
        denied = runtime.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
        assert not denied["allowed"] and denied["reason"] == "PPI_BUDGET_EXHAUSTED"
        assert runtime.budget.policy["endpoint_limits"]["book"] == 5
    finally:
        reader.close()


@pytest.mark.parametrize("mode", ["OFF", "SHADOW"])
def test_intentional_cold_baseline_keeps_original_native_reads_and_no_sidecar(wire, tmp_path, mode):
    values = approved(wire)
    values[0]["mode"] = mode
    clock, calls, _ = wire
    database = tmp_path / "absent-paper.sqlite"
    runtime = RuntimePPIBudget(database, controller(values, database=database), clock=clock.now)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=runtime, consumer="EXIT_READER")
    try:
        reader.login_once()
        before = len(calls)
        for _ in range(2):
            assert scoped_book(reader, IDENTITY, "EXIT_CRITICAL")["bids"]
        assert len(calls) == before + 2
        assert runtime.budget is None and runtime.activation_contract is None
        assert not runtime.path.exists() and not database.exists()
    finally:
        reader.close()


@pytest.mark.parametrize("failure", ["new_positions", "missing_ledger", "approval_revoked"])
def test_u05_native_start_revalidates_original_lower_scope_and_preserves_used_debt(wire, tmp_path, monkeypatch, failure):
    clock, calls, _ = wire
    values = approved(wire)
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=0)
    ctl = controller(values, database=store.path)
    runtime = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=runtime, consumer="SCANNER")
    original_start = runtime.start
    changed = []
    try:
        reader.login_once()
        reader.current(IDENTITY[0], IDENTITY[1], IDENTITY[4])
        prior_policy = deepcopy(runtime.budget.policy)
        with closing(sqlite3.connect(runtime.path)) as c:
            prior_receipt = c.execute("SELECT * FROM budget_requests WHERE used=1").fetchone()

        def changed_before_start(lease):
            if not changed:
                changed.append(lease)
                if failure == "new_positions":
                    native_opened_store(tmp_path, clock, monkeypatch, count=5)
                elif failure == "missing_ledger":
                    with store.connect() as c:
                        c.execute("ALTER TABLE paper_positions RENAME TO missing_opened_ledger")
                else:
                    ctl.inputs["approval"]["approved"] = False
            # A changed caller scope cannot relabel the already-issued lease.
            with reader.read_scope(priority="EXIT_CRITICAL"):
                original_start(lease)

        monkeypatch.setattr(runtime, "start", changed_before_start)
        before = len(calls)
        reason = {"new_positions": "PPI_EXIT_CAPACITY_INSUFFICIENT",
            "missing_ledger": "CAPACITY_OPENED_LEDGER_UNVERIFIED",
            "approval_revoked": "PPI_CAPACITY_REVALIDATION_BACKPRESSURE"}[failure]
        with pytest.raises(BudgetBackpressure, match=reason):
            reader.intraday(IDENTITY[0], IDENTITY[1], IDENTITY[4])
        assert len(calls) == before and len(changed) == 1
        assert runtime.budget.policy == prior_policy
        with closing(sqlite3.connect(runtime.path)) as c:
            assert c.execute("SELECT * FROM budget_requests").fetchall() == [prior_receipt]
            assert json.loads(c.execute("SELECT value FROM budget_state WHERE key='inflight'").fetchone()[0]) is None
        metrics = runtime.budget.metrics()["global"]
        # dropped counts admission rejection; this claim was admitted then
        # safely cancelled before wire and therefore retains allowed=2.
        assert metrics == {"requested": 2, "allowed": 2, "used": 1, "dropped": 0}
        # A new, authentic EXIT request remains within the unchanged budget.
        assert scoped_book(reader, IDENTITY, "EXIT_CRITICAL")["bids"]
        assert len(calls) == before + 1
        assert runtime.budget.metrics()["global"]["used"] == 2
    finally:
        reader.close()


def test_u05_native_start_cannot_renew_expired_admission_through_baseline_fallback(wire, tmp_path, monkeypatch):
    clock, calls, _ = wire
    values = approved(wire)
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=0)
    ctl = controller(values, database=store.path)
    runtime = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=runtime, consumer="SCANNER")
    try:
        reader.login_once()
        reader.current(IDENTITY[0], IDENTITY[1], IDENTITY[4])
        prior_policy = deepcopy(runtime.budget.policy)
        expires = datetime.fromisoformat(prior_policy["expires_at"])
        clock.advance((expires-clock.now()).total_seconds()-1)
        assert ctl.state(clock.now())["status"] == "APPROVED_DYNAMIC"
        original_start = runtime.start

        def expires_before_start(lease):
            clock.advance(2)
            ctl.inputs["approval"]["approved"] = False
            original_start(lease)

        monkeypatch.setattr(runtime, "start", expires_before_start)
        before = len(calls)
        with pytest.raises(BudgetBackpressure, match="PPI_CAPACITY_EXPIRED_BACKPRESSURE"):
            reader.intraday(IDENTITY[0], IDENTITY[1], IDENTITY[4])
        assert len(calls) == before
        assert runtime.budget.policy == prior_policy
        assert runtime.budget.metrics()["global"]["used"] == 1
        with closing(sqlite3.connect(runtime.path)) as c:
            assert c.execute("SELECT COUNT(*) FROM budget_requests WHERE used=0").fetchone()[0] == 0
    finally:
        reader.close()


def test_f01_interleaved_native_scanner_and_exit_preserve_complete_floor(wire, tmp_path):
    clock, calls, _ = wire
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock, common=2), clock=clock.now)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=budget, consumer="SCANNER")
    try:
        reader.login_once()
        before = len(calls)
        for index in range(5):
            lower = (f"LOWER{index}", *IDENTITY[1:])
            if index < 2:
                assert scoped_book(reader, lower, "OPENED_CRITICAL")["bids"]
            else:
                with pytest.raises(BudgetBackpressure, match="RESERVE"):
                    scoped_book(reader, lower, "OPENED_CRITICAL")
            assert scoped_book(reader, (f"EXIT{index}", *IDENTITY[1:]), "EXIT_CRITICAL")["bids"]
        assert len(calls) == before + 7
        metrics = budget.metrics()
        assert metrics["global"] == {"requested": 10, "allowed": 7, "used": 7, "dropped": 3}
        assert metrics["by_endpoint"]["book"]["used"] == 7
        assert sum(row["used"] for row in metrics["by_scope"] if row["priority"] == "EXIT_CRITICAL") == 5
    finally:
        reader.close()


def test_f01_native_exit_transient_retry_is_bounded_and_cannot_retry_backpressure(wire, tmp_path, monkeypatch):
    from bd_ppi_readonly_guard import retry_read
    clock, calls, _ = wire
    original_send = requests.adapters.HTTPAdapter.send
    attempts = []

    def interrupted_book(adapter, request, **kwargs):
        if urlsplit(request.url).path.lower().endswith("/book"):
            attempts.append(request.url)
            if len(attempts) == 1:
                raise requests.exceptions.ConnectionError("connection reset")
        return original_send(adapter, request, **kwargs)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", interrupted_book)
    value = exit_floor(clock, books=2)
    value["exit_demand"] = {"book": 2}
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=budget, consumer="EXIT_READER")
    try:
        reader.login_once()
        assert retry_read(lambda: scoped_book(reader, IDENTITY, "EXIT_CRITICAL"), retries=1, pause=clock.advance)["bids"]
        assert len(attempts) == 2 and budget.metrics()["global"]["used"] == 2
        before = len(calls)
        other = ("YPFD", *IDENTITY[1:])
        with pytest.raises(BudgetBackpressure, match="EXHAUSTED"):
            retry_read(lambda: scoped_book(reader, other, "EXIT_CRITICAL"), retries=2, pause=clock.advance)
        assert len(attempts) == 2 and len(calls) == before
        assert budget.metrics()["global"]["requested"] == 3
    finally:
        reader.close()


def _native_kill_boundary(path, value, at, stage, ready, hold, wire_count):
    """Interrupt the actual guarded SDK path at each durable wire boundary."""
    clock = Clock(at)
    budget = GlobalPPIBudget(path, value, clock=clock.now)
    actual_send = requests.adapters.HTTPAdapter.send
    actual_start = budget.start

    def blocked_start(token):
        ready.put(token)
        assert hold.wait(30)
        actual_start(token)

    def blocked_wire(adapter, request, **kwargs):
        if urlsplit(request.url).path.lower().endswith("/current"):
            with wire_count.get_lock():
                wire_count.value += 1
            if stage == "after_start":
                with closing(sqlite3.connect(path)) as c:
                    token = c.execute("SELECT lease FROM budget_requests WHERE used=1").fetchone()[0]
                ready.put(token)
                assert hold.wait(30)
        return actual_send(adapter, request, **kwargs)

    if stage == "before_start":
        budget.start = blocked_start
    requests.adapters.HTTPAdapter.send = blocked_wire
    reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=budget, consumer="SCANNER")
    try:
        reader.login_once()
        reader.current(IDENTITY[0], IDENTITY[1], IDENTITY[4])
    finally:
        reader.close()
        requests.adapters.HTTPAdapter.send = actual_send


@pytest.mark.parametrize("stage", ["before_start", "after_start"])
def test_f01_native_process_kill_preserves_exact_pre_or_post_wire_receipt(wire, tmp_path, monkeypatch, stage):
    clock, calls, _ = wire
    value = exit_floor(clock, common=1, global_limit=6)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    ctx = multiprocessing.get_context("fork")
    ready, hold, wire_count = ctx.Queue(), ctx.Event(), ctx.Value("i", 0)
    child = ctx.Process(target=_native_kill_boundary,
        args=(str(budget.path), value, clock.now().isoformat(), stage, ready, hold, wire_count))
    actual_send = requests.adapters.HTTPAdapter.send

    def counted_parent_wire(adapter, request, **kwargs):
        if "/marketdata/" in urlsplit(request.url).path.lower():
            with wire_count.get_lock():
                wire_count.value += 1
        return actual_send(adapter, request, **kwargs)

    child.start()
    try:
        token = ready.get(timeout=8)
        with closing(sqlite3.connect(budget.path)) as c:
            before = c.execute("SELECT * FROM budget_requests WHERE lease=?", (token,)).fetchone()
        emitted = int(stage == "after_start")
        assert before[-1] == emitted and wire_count.value == emitted
        child.kill()
        child.join(5)
        assert not child.is_alive() and child.exitcode < 0
        # The OS mutex is gone, but the durable crashed lease is still live.
        denied = budget.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
        assert not denied["allowed"] and denied["reason"] == "PPI_SERIAL_BACKPRESSURE"
        with closing(sqlite3.connect(budget.path)) as c:
            assert c.execute("SELECT * FROM budget_requests WHERE lease=?", (token,)).fetchone() == before
        clock.advance(61)
        monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", counted_parent_wire)
        reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=budget, consumer="EXIT_READER")
        try:
            reader.login_once()
            assert scoped_book(reader, IDENTITY, "EXIT_CRITICAL")["bids"]
        finally:
            reader.close()
        assert wire_count.value == emitted + 1
        assert budget.metrics()["global"]["used"] == emitted + 1
        with closing(sqlite3.connect(budget.path)) as c:
            assert c.execute("SELECT * FROM budget_requests WHERE lease=?", (token,)).fetchone() == before
            assert c.execute("SELECT COUNT(*) FROM budget_requests WHERE used=1").fetchone()[0] == emitted + 1
    finally:
        # A killed owner may poison its Event's internal mutex. Do not reuse
        # hold to release it after SIGKILL; ensure the process is reaped.
        if child.is_alive():
            child.kill()
            child.join(5)
        ready.cancel_join_thread()
        ready.close()
