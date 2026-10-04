"""#465 F-01: actual SQLite, SDK fake wire and canonical caller regressions.

No network, productive store, broker credential, or real-order route is used.
The exact auditor reproduction intentionally fails at #463's frozen head.
"""
from copy import deepcopy
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import fcntl
import json
import multiprocessing
import sqlite3
import threading
import time
from urllib.parse import urlsplit

import pytest
import requests

from bd_ppi_readonly_guard import ProductionMarketReader, ReadOnlyPolicyViolation, ReadOnlyTransportGuard, retry_read
from be_paper_engine import D, PaperBroker, PaperStore
from bq_exit_policy import PaperSessionPolicy
import bf_production_paper_observer as observer
from bv_paper_runtime import collect_exit_books
from rc6_ppi_global_budget import BudgetBackpressure, GlobalPPIBudget, RuntimePPIBudget, PRIORITIES, budget_policy, validate_policy
from tests.test_rc6_ppi_global_budget import policy, use
from tests.test_rc6_ppi_capacity_benchmark import Clock, wire
from tests.test_rc6_capacity_promotion import approved, controller
from tests.test_production_paper_v1634 import quote


def exit_floor(clock, *, books=5, common=0, global_limit=None):
    value = policy(clock, limits={"current": 20, "book": books + common, "intraday": 20},
        global_limit=global_limit, reserves={"EXIT_CRITICAL": {"book": books}})
    return value | {"open_positions_count": books, "exit_demand": {"book": books}}


def book_payload(clock):
    return {"date": clock.now().isoformat(), "bids": [{"price": 99, "quantity": 1000}],
        "offers": [{"price": 101, "quantity": 1000}]}


def scoped_book(reader, identity, priority):
    with reader.read_scope(priority=priority, identity=identity):
        return reader.book(identity[0], identity[1], identity[4])


def test_auditor_exact_five_opened_cannot_steal_last_exit_capacity(tmp_path):
    clock = Clock()
    value = exit_floor(clock)
    value["endpoint_limits"] = dict(current=5, book=5, intraday=5)
    value["global_limit"] = 15
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    opened = [use(budget, "book", consumer="SCANNER", priority="OPENED_CRITICAL") for _ in range(5)]
    assert all(not row["allowed"] and row["reason"] == "PPI_HIGH_PRIORITY_RESERVE_BACKPRESSURE" for row in opened)
    assert all(use(budget, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"] for _ in range(5))
    assert budget.metrics()["by_endpoint"]["book"] == dict(requested=10, allowed=5, used=5, dropped=5)


@pytest.mark.parametrize("priority", PRIORITIES[1:])
def test_every_lower_priority_preserves_each_endpoint_floor(tmp_path, priority):
    clock = Clock()
    value = policy(clock, limits=dict(current=2, book=2, intraday=2),
        reserves={"EXIT_CRITICAL": dict(current=2, book=2, intraday=2)})
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    for endpoint in ("current", "book", "intraday"):
        assert not use(budget, endpoint, priority=priority)["allowed"]
        assert use(budget, endpoint, priority="EXIT_CRITICAL")["allowed"]
        assert use(budget, endpoint, priority="EXIT_CRITICAL")["allowed"]


@pytest.mark.parametrize("exit_first", [False, True])
def test_reverse_order_burst_endpoint_and_global_remain_coherent(tmp_path, exit_first):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock, global_limit=7), clock=clock.now)
    if exit_first:
        assert all(use(budget, "book", priority="EXIT_CRITICAL")["allowed"] for _ in range(5))
    allowed = sum(use(budget, "current", consumer="SCANNER")["allowed"] for _ in range(50))
    assert allowed == 2
    if not exit_first:
        assert all(use(budget, "book", priority="EXIT_CRITICAL")["allowed"] for _ in range(5))
    metrics = budget.metrics()
    assert metrics["global"]["used"] == 7
    assert metrics["by_endpoint"]["book"]["used"] == 5
    assert not use(budget, "intraday", priority="SCALPING_HOT")["allowed"]


def test_tighter_global_cap_exposes_unserviceable_demand_without_lower_borrow(tmp_path):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock, global_limit=3), clock=clock.now)
    assert not use(budget, "current", priority="OPENED_CRITICAL")["allowed"]
    assert all(use(budget, "book", priority="EXIT_CRITICAL")["allowed"] for _ in range(3))
    assert not use(budget, "book", priority="EXIT_CRITICAL")["allowed"]
    metrics = budget.metrics()["window"]
    assert metrics["by_endpoint"]["book"]["reserved_total"] == 3
    assert metrics["exit_unreserved_demand"]["book"] == 2


def test_independent_opened_floor_precedes_scalping_and_discovery(tmp_path):
    clock = Clock()
    value = policy(clock, limits=dict(current=4, book=4, intraday=4),
        reserves={"EXIT_CRITICAL": {"book": 2}, "OPENED_CRITICAL": {"book": 1}})
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    assert use(budget, "book", priority="SCALPING_HOT")["allowed"]
    assert not use(budget, "book", priority="SCALPING_HOT")["allowed"]
    assert use(budget, "book", priority="OPENED_CRITICAL")["allowed"]
    assert all(use(budget, "book", priority="EXIT_CRITICAL")["allowed"] for _ in range(2))


def test_other_process_policy_cannot_lower_promised_exit_floor(tmp_path):
    clock = Clock()
    value = exit_floor(clock)
    first = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    assert not use(first, "book", priority="OPENED_CRITICAL")["allowed"]
    second_value = deepcopy(value)
    second_value["priority_reserves"] = {}
    second = GlobalPPIBudget(first.path, second_value, clock=clock.now)
    assert not use(second, "book", priority="DISCOVERY")["allowed"]
    assert use(first, "book", priority="EXIT_CRITICAL")["allowed"]


def test_exit_demand_metrics_are_independent_of_planned_lower_reservations():
    clock = Clock()
    original = policy(clock, limits=dict(current=5, book=5, intraday=5))
    state = dict(status="APPROVED_DYNAMIC", global_budget={name: original[name] for name in
        ("window_seconds", "endpoint_limits", "global_limit", "safety_reserve")},
        budget_settings={name: original[name] for name in ("critical_book_seconds", "lease_seconds",
            "breaker_seconds", "session_breaker_seconds", "server_error_threshold", "maximum_bytes")},
        recommendation_digest=original["recommendation_digest"],
        configuration_fingerprint=original["configuration_fingerprint"], expires_at=original["expires_at"])
    value = budget_policy(state, opened_count=5, planned_reservations={
        "SCALPING_HOT": {"current": 1}, "STRATEGY_HOT": {"book": 3}, "WARM": {"intraday": 2}})
    assert value["exit_demand"] == dict(current=0, book=30, intraday=0)
    assert value["priority_reserves"]["EXIT_CRITICAL"] == dict(current=0, book=5, intraday=0)


@pytest.mark.parametrize("replacement_window", [15, 60])
@pytest.mark.parametrize("elapsed", [16, 31])
def test_other_window_and_live_authority_cannot_erase_exit_promise(tmp_path, replacement_window, elapsed):
    clock = Clock()
    original = exit_floor(clock)
    first = GlobalPPIBudget(tmp_path / "budget.sqlite", original, clock=clock.now)
    assert not use(first, "book", priority="OPENED_CRITICAL")["allowed"]
    clock.advance(elapsed)
    replacement = dict(original, window_seconds=replacement_window,
        priority_reserves={}, configuration_fingerprint="c" * 64,
        open_positions_count=0, exit_demand={})
    second = GlobalPPIBudget(first.path, replacement, clock=clock.now)
    lower = [use(second, "book", priority="DISCOVERY") for _ in range(5)]
    assert all(not row["allowed"] for row in lower)
    restarted = GlobalPPIBudget(first.path, original, clock=clock.now)
    assert all(use(restarted, "book", priority="EXIT_CRITICAL")["allowed"] for _ in range(5))
    assert second.metrics()["by_endpoint"]["book"]["used"] == 5


@pytest.mark.parametrize("endpoint", ["current", "book", "intraday"])
def test_shorter_window_or_greater_caps_cannot_forget_actual_used_debt(tmp_path, endpoint):
    clock = Clock()
    original = policy(clock, limits=dict(current=1, book=1, intraday=1), global_limit=1)
    first = GlobalPPIBudget(tmp_path / "budget.sqlite", original, clock=clock.now)
    assert use(first, endpoint)["allowed"]
    clock.advance(16)
    replacement = dict(original, window_seconds=15, endpoint_limits=dict(current=10, book=10, intraday=10),
        global_limit=30, configuration_fingerprint="c" * 64)
    second = GlobalPPIBudget(first.path, replacement, clock=clock.now)
    assert use(second, endpoint)["reason"] == "PPI_BUDGET_EXHAUSTED"
    metrics = second.metrics()["window"]
    assert metrics["effective_window_seconds"] == 30
    assert metrics["by_endpoint"][endpoint]["used"] == 1
    assert any(row["window_seconds"] == 30 and row["global_limit"] == 1 for row in metrics["active_envelopes"])
    clock.advance(15)
    assert use(second, endpoint)["allowed"]


def test_longer_window_counts_preexisting_wire_until_its_own_boundary(tmp_path):
    clock = Clock()
    original = policy(clock, limits=dict(current=1, book=1, intraday=1), global_limit=1)
    original["window_seconds"] = 15
    first = GlobalPPIBudget(tmp_path / "budget.sqlite", original, clock=clock.now)
    assert use(first, "book")["allowed"]
    clock.advance(16)
    replacement = dict(original, window_seconds=60, configuration_fingerprint="c" * 64)
    second = GlobalPPIBudget(first.path, replacement, clock=clock.now)
    assert not use(second, "book")["allowed"]
    clock.advance(45)
    assert use(second, "book")["allowed"]


def test_expired_authority_keeps_unelapsed_promise_but_cannot_emit(tmp_path):
    clock = Clock()
    original = exit_floor(clock)
    original["expires_at"] = (clock.now() + timedelta(seconds=2)).isoformat()
    first = GlobalPPIBudget(tmp_path / "budget.sqlite", original, clock=clock.now)
    assert not use(first, "book", priority="OPENED_CRITICAL")["allowed"]
    clock.advance(3)
    replacement = dict(original, priority_reserves={}, exit_demand={}, open_positions_count=0,
        configuration_fingerprint="c" * 64, window_seconds=15,
        expires_at=(clock.now() + timedelta(hours=1)).isoformat())
    second = GlobalPPIBudget(first.path, replacement, clock=clock.now)
    assert not use(second, "book")["allowed"]
    assert first.acquire("book", priority="EXIT_CRITICAL")["reason"] == "PPI_CAPACITY_EXPIRED_BACKPRESSURE"
    clock.advance(28)
    assert use(second, "book")["allowed"]


def test_replacement_policy_clock_rollback_does_not_discard_original_promise(tmp_path):
    clock = Clock()
    original = exit_floor(clock)
    first = GlobalPPIBudget(tmp_path / "budget.sqlite", original, clock=clock.now)
    assert not use(first, "book", priority="OPENED_CRITICAL")["allowed"]
    clock.advance(16)
    assert not use(first, "book", priority="OPENED_CRITICAL")["allowed"]
    replacement = dict(original, priority_reserves={}, configuration_fingerprint="c" * 64, window_seconds=15)
    second = GlobalPPIBudget(first.path, replacement, clock=clock.now)
    clock.advance(-1)
    with pytest.raises(BudgetBackpressure, match="CLOCK_ROLLBACK"):
        second.acquire("book")
    clock.advance(1)
    assert not use(second, "book")["allowed"]
    assert use(first, "book", priority="EXIT_CRITICAL")["allowed"]


def test_known_legacy_authority_is_migrated_without_losing_its_floor(tmp_path):
    clock = Clock()
    original = exit_floor(clock)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", original, clock=clock.now)
    with sqlite3.connect(budget.path) as connection:
        for key, value in (("reserves", original["priority_reserves"]),
            ("reserve_at", clock.now().timestamp()), ("last_policy", original)):
            connection.execute("INSERT INTO budget_state VALUES(?,?)", (key, json.dumps(value)))
    clock.advance(31)
    replacement = dict(original, priority_reserves={}, configuration_fingerprint="c" * 64, window_seconds=15)
    second = GlobalPPIBudget(budget.path, replacement, clock=clock.now)
    assert not use(second, "book")["allowed"]
    assert use(budget, "book", priority="EXIT_CRITICAL")["allowed"]


def test_unknown_legacy_authority_is_explicitly_closed_and_preserved(tmp_path):
    clock = Clock()
    original = exit_floor(clock)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", original, clock=clock.now)
    with sqlite3.connect(budget.path) as connection:
        for key, value in (("reserves", original["priority_reserves"]), ("reserve_at", clock.now().timestamp())):
            connection.execute("INSERT INTO budget_state VALUES(?,?)", (key, json.dumps(value)))
    with pytest.raises(BudgetBackpressure, match="LEGACY_AUTHORITY_UNKNOWN"):
        budget.acquire("book", priority="EXIT_CRITICAL")
    with sqlite3.connect(budget.path) as connection:
        assert json.loads(connection.execute("SELECT value FROM budget_state WHERE key='reserves'").fetchone()[0]) == original["priority_reserves"]


def test_delayed_actual_start_renews_serial_lease_from_wire_timestamp(tmp_path):
    clock = Clock()
    original = policy(clock)
    first = GlobalPPIBudget(tmp_path / "budget.sqlite", original, clock=clock.now)
    pending = first.acquire("book", priority="EXIT_CRITICAL")
    clock.advance(59)
    first.start(pending["lease"])
    clock.advance(2)
    second = GlobalPPIBudget(first.path, original, clock=clock.now)
    assert second.acquire("book", priority="EXIT_CRITICAL")["reason"] == "PPI_SERIAL_BACKPRESSURE"
    clock.advance(58)
    assert second.acquire("book", priority="EXIT_CRITICAL")["allowed"]


def test_actual_delayed_wire_remains_in_rolling_cap_after_admission_ages_out(wire, tmp_path):
    clock, calls, behavior = wire
    original = policy(clock, limits=dict(current=1, book=1, intraday=1), global_limit=1)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", original, clock=clock.now)
    actual_start = budget.start
    def delayed_start(token):
        clock.advance(29)
        actual_start(token)
    budget.start = delayed_start
    guard = ReadOnlyTransportGuard(budget=budget).install()
    try:
        requests.get("https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book")
        budget.start = actual_start
        clock.advance(2)
        with pytest.raises(BudgetBackpressure, match="EXHAUSTED"):
            requests.get("https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book")
        assert len(calls) == 1
        assert budget.metrics()["window"]["by_endpoint"]["book"]["used"] == 1
        clock.advance(28)
        requests.get("https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book")
        assert len(calls) == 2
    finally:
        guard.restore()


def test_new_stronger_promise_between_admission_and_start_is_rechecked(tmp_path):
    clock = Clock()
    original = policy(clock, limits=dict(current=5, book=5, intraday=5))
    first = GlobalPPIBudget(tmp_path / "budget.sqlite", original, clock=clock.now)
    pending = first.acquire("book", priority="OPENED_CRITICAL")
    clock.advance(1)
    stronger = dict(original, priority_reserves={"EXIT_CRITICAL": {"book": 5}},
        configuration_fingerprint="c" * 64)
    second = GlobalPPIBudget(first.path, stronger, clock=clock.now)
    assert second.acquire("book", priority="OPENED_CRITICAL")["reason"] == "PPI_SERIAL_BACKPRESSURE"
    with pytest.raises(BudgetBackpressure, match="RESERVE"):
        first.start(pending["lease"])
    assert first.metrics()["global"]["used"] == 0


def test_live_authority_cardinality_is_bounded_without_clearing_earlier_floors(tmp_path):
    clock = Clock()
    original = exit_floor(clock)
    original["expires_at"] = (clock.now() + timedelta(seconds=2)).isoformat()
    first = GlobalPPIBudget(tmp_path / "budget.sqlite", original, clock=clock.now)
    for index in range(64):
        changed = dict(original, configuration_fingerprint=f"{index:064x}")
        candidate = GlobalPPIBudget(first.path, changed, clock=clock.now)
        assert not use(candidate, "book")["allowed"]
    overflow = GlobalPPIBudget(first.path, dict(original, configuration_fingerprint="f" * 64), clock=clock.now)
    with pytest.raises(BudgetBackpressure, match="AUTHORITY_OVERLAP_LIMIT"):
        overflow.acquire("book")
    with sqlite3.connect(first.path) as connection:
        active = json.loads(connection.execute("SELECT value FROM budget_state WHERE key='reservation_envelopes_v1'").fetchone()[0])
        assert len(active) == 64 and first.path.stat().st_size < 1024**2
    clock.advance(31)
    fresh = dict(original, configuration_fingerprint="f" * 64,
        expires_at=(clock.now() + timedelta(hours=1)).isoformat())
    candidate = GlobalPPIBudget(first.path, fresh, clock=clock.now)
    assert use(candidate, "book", priority="EXIT_CRITICAL")["allowed"]
    assert len(candidate.metrics()["window"]["active_envelopes"]) == 1


@pytest.mark.parametrize("kind", ["endpoint", "global"])
def test_same_window_larger_policy_cap_cannot_spend_another_authority_exit_floor(tmp_path, kind):
    clock = Clock()
    original = exit_floor(clock, global_limit=5 if kind == "global" else None)
    first = GlobalPPIBudget(tmp_path / "budget.sqlite", original, clock=clock.now)
    assert not use(first, "book", priority="OPENED_CRITICAL")["allowed"]
    replacement = dict(original, priority_reserves={}, configuration_fingerprint="c" * 64,
        endpoint_limits=dict(original["endpoint_limits"], book=10),
        global_limit=10 if kind == "global" else 50)
    second = GlobalPPIBudget(first.path, replacement, clock=clock.now)
    endpoint = "current" if kind == "global" else "book"
    assert all(not use(second, endpoint)["allowed"] for _ in range(5))
    assert all(use(first, "book", priority="EXIT_CRITICAL")["allowed"] for _ in range(5))


def test_wire_scope_preserves_transport_failure_type_and_releases_os_lock(wire, tmp_path, monkeypatch):
    clock, calls, behavior = wire
    attempts = []
    original_send = requests.adapters.HTTPAdapter.send
    def transient_wire(adapter, request, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise requests.exceptions.ConnectionError("connection reset")
        return original_send(adapter, request, **kwargs)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", transient_wire)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    guard = ReadOnlyTransportGuard(budget=budget).install()
    try:
        response = retry_read(lambda: requests.get("https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book"), pause=clock.advance)
        assert response.json() and len(attempts) == 2
        assert budget.metrics()["global"]["used"] == 2
        assert not budget._wire_busy()
    finally:
        guard.restore()


def live_wire_owner(path, value, seconds, started, release):
    clock = lambda: datetime.fromtimestamp(seconds.value, timezone.utc)
    budget = GlobalPPIBudget(path, value, clock=clock)
    lease = budget.acquire("current")
    with budget.wire_scope(lease["lease"]):
        budget.start(lease["lease"])
        started.put(True)
        if release.wait(5):
            budget.finish(lease["lease"])


@pytest.mark.parametrize("kill_owner", [False, True])
def test_process_wire_mutex_prevents_live_overlap_and_preserves_crash_lease(tmp_path, kill_owner):
    clock = Clock()
    value = exit_floor(clock)
    context = multiprocessing.get_context("fork")
    seconds = context.Value("d", clock.now().timestamp(), lock=False)
    shared_clock = lambda: datetime.fromtimestamp(seconds.value, timezone.utc)
    started, release = context.Queue(), context.Event()
    path = str(tmp_path / "budget.sqlite")
    owner = context.Process(target=live_wire_owner, args=(path, value, seconds, started, release))
    owner.start()
    try:
        assert started.get(timeout=3)
        second = GlobalPPIBudget(path, value, clock=shared_clock)
        if kill_owner:
            owner.kill()
            owner.join(3)
            assert second.acquire("book", priority="EXIT_CRITICAL")["reason"] == "PPI_SERIAL_BACKPRESSURE"
        seconds.value += 61
        if not kill_owner:
            assert second.acquire("book", priority="EXIT_CRITICAL")["reason"] == "PPI_SERIAL_BACKPRESSURE"
            release.set()
            owner.join(3)
            assert owner.exitcode == 0
        assert use(second, "book", priority="EXIT_CRITICAL")["allowed"]
    finally:
        # A killed child may die holding Event's internal synchronization;
        # never reuse that poisoned IPC primitive in the crash fixture.
        if not kill_owner:
            release.set()
        if owner.is_alive():
            owner.kill()
        owner.join(3)


@pytest.mark.parametrize("past_time_lease", [False, True])
def test_native_streaming_body_retains_actual_wire_ownership(monkeypatch, tmp_path, past_time_lease):
    clock = Clock()
    body_started, release = threading.Event(), threading.Event()
    calls, errors, completed, streams = [], [], [], []
    class Body:
        def __init__(self, blocked):
            self.blocked = blocked
        def stream(self, chunk_size, decode_content=True):
            streams.append(self.blocked)
            if self.blocked:
                body_started.set()
                assert release.wait(3), "offline body was not released"
            yield b'{"complete":true}'
        def close(self):
            pass
        def release_conn(self):
            pass
    def headers_only(adapter, request, **kwargs):
        calls.append(request.url)
        response = requests.Response()
        response.status_code, response.request, response.url = 200, request, request.url
        response.raw = Body(len(calls) == 1)
        return response
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", headers_only)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock), clock=clock.now)
    guard = ReadOnlyTransportGuard(budget=budget, consumer="SCANNER").install()
    def first():
        try:
            completed.append(requests.get("https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Current").json())
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=first)
    worker.start()
    try:
        assert body_started.wait(2)
        if past_time_lease:
            # read_timeout is inactivity: a body can remain active after60s.
            clock.advance(61)
        with guard.read_scope(priority="EXIT_CRITICAL"):
            with pytest.raises(BudgetBackpressure, match="SERIAL"):
                requests.get("https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book")
        assert worker.is_alive() and len(calls) == 1
        release.set()
        worker.join(2)
        assert not worker.is_alive() and not errors and completed == [{"complete": True}]
        with guard.read_scope(priority="EXIT_CRITICAL"):
            assert requests.get("https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book").json() == {"complete": True}
        assert len(calls) == 2 and streams == [True, False]
        assert budget.metrics()["global"]["used"] == 2
    finally:
        release.set()
        worker.join(2)
        guard.restore()


def test_explicit_unowned_http_streaming_is_denied_before_admission_or_wire(wire, tmp_path):
    clock, calls, behavior = wire
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    guard = ReadOnlyTransportGuard(budget=budget).install()
    try:
        with pytest.raises(ReadOnlyPolicyViolation, match="STREAMING_READ_NOT_OWNED"):
            requests.get("https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book", stream=True)
        prepared = requests.Request("GET", "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book").prepare()
        with pytest.raises(ReadOnlyPolicyViolation, match="STREAMING_READ_NOT_OWNED"):
            requests.Session().send(prepared, stream=True)
        assert not calls and budget.metrics()["global"]["requested"] == 0
    finally:
        guard.restore()


@pytest.mark.parametrize("status", [200, 429, 401, 403])
def test_native_body_failure_is_sanitized_and_preserves_global_http_semantics(wire, tmp_path, monkeypatch, status):
    clock, calls, behavior = wire
    closed = []
    class BrokenBody:
        def stream(self, chunk_size, decode_content=True):
            raise requests.exceptions.ChunkedEncodingError("RAW_PRIVATE_BODY_CANARY")
            yield b""
        def close(self):
            closed.append(True)
        def release_conn(self):
            pass
    original_send = requests.adapters.HTTPAdapter.send
    def broken_body(adapter, request, **kwargs):
        response = original_send(adapter, request, **kwargs)
        if urlsplit(request.url).path.lower().endswith("/intraday"):
            response._content, response._content_consumed = False, False
            response.raw = BrokenBody()
        return response
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", broken_body)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    reader = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=budget, consumer="SCALPING")
    try:
        reader.login_once()
        behavior["status"] = status
        with pytest.raises(Exception):
            reader.intraday("GGAL", "ACCIONES", "A-24HS")
        code = f"PPI_HTTP_{status}" if status >= 400 else "PPI_CHUNKEDENCODINGERROR"
        assert reader.last_read_error_code == code
        metrics = budget.metrics()
        assert metrics["global"]["used"] == 1 and closed
        assert "RAW_PRIVATE" not in json.dumps(metrics)
        if status >= 400:
            assert metrics["circuits"]["global"]["code"] == code
            before = len(calls)
            with pytest.raises(BudgetBackpressure):
                reader.intraday("GGAL", "ACCIONES", "A-24HS")
            assert len(calls) == before
    finally:
        reader.close()


@pytest.mark.parametrize("breaker", [False, True])
def test_fresh_native_cache_hit_publishes_increased_exit_demand_before_other_process_reads(wire, tmp_path, breaker):
    clock, calls, behavior = wire
    original = exit_floor(clock)
    original.update(priority_reserves={"EXIT_CRITICAL": {"book": 1}},
        open_positions_count=1, exit_demand={"book": 1})
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", original, clock=clock.now)
    reader = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=budget, consumer="EXIT_READER")
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    try:
        reader.login_once()
        assert scoped_book(reader, identity, "EXIT_CRITICAL")
        before = len(calls)
        budget.policy = dict(original, priority_reserves={"EXIT_CRITICAL": {"book": 5}},
            open_positions_count=5, exit_demand={"book": 5})
        if breaker:
            budget.report_error("intraday", "PPI_HTTP_429")
            with pytest.raises(BudgetBackpressure, match="GLOBAL_CIRCUIT"):
                scoped_book(reader, identity, "EXIT_CRITICAL")
            clock.advance(61)
        else:
            assert scoped_book(reader, identity, "EXIT_CRITICAL")
        assert len(calls) == before  # demand publication requires no duplicate wire
        other = GlobalPPIBudget(budget.path, dict(original, priority_reserves={},
            configuration_fingerprint="c" * 64, open_positions_count=0, exit_demand={}), clock=clock.now)
        lower = [use(other, "book") for _ in range(4)]
        assert all(not row["allowed"] for row in lower)
        if breaker:
            assert scoped_book(reader, identity, "EXIT_CRITICAL")
        for index in range(1, 5):
            assert scoped_book(reader, (f"EXIT{index}", *identity[1:]), "EXIT_CRITICAL")
        assert len([row for row in calls if urlsplit(row[1]).path.lower().endswith("/book")]) == 5 + breaker
        assert budget.metrics()["global"]["used"] == 5 + breaker
        assert budget.metrics()["real_orders_sent"] == 0
    finally:
        reader.close()


def test_accepted_fetch_completion_publishes_latest_valid_exit_metadata(tmp_path):
    clock = Clock()
    original = exit_floor(clock)
    original.update(priority_reserves={"EXIT_CRITICAL": {"book": 1}}, open_positions_count=1)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", original, clock=clock.now)
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    def fresh_fetch():
        budget.policy = dict(original, priority_reserves={"EXIT_CRITICAL": {"book": 5}}, open_positions_count=5)
        return book_payload(clock)
    assert budget.coalesced_book(identity, fresh_fetch, consumer="EXIT_READER", priority="EXIT_CRITICAL")
    other = GlobalPPIBudget(budget.path, dict(original, priority_reserves={},
        configuration_fingerprint="c" * 64), clock=clock.now)
    assert not use(other, "book")["allowed"]


@pytest.mark.parametrize("priority,consumer", [("OPENED_CRITICAL", "SCANNER"), ("EXIT_CRITICAL", "EXIT_READER")])
def test_429_preserves_semantics_and_durable_floor_across_restart(tmp_path, priority, consumer):
    clock = Clock()
    value = exit_floor(clock, common=1)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    lease = budget.acquire("book", priority=priority, consumer=consumer)
    assert lease["allowed"]
    budget.start(lease["lease"])
    budget.finish(lease["lease"], status_code=429)
    restarted = GlobalPPIBudget(budget.path, value, clock=clock.now)
    denied = restarted.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert denied["reason"] == "PPI_GLOBAL_CIRCUIT_OPEN"
    assert restarted.metrics()["window"]["reserve_hierarchy"][0:2] == ["EXIT_CRITICAL", "OPENED_CRITICAL"]
    assert restarted.metrics()["window"]["lower_priority_exit_borrowing"] == "FORBIDDEN"
    clock.advance(61)
    assert use(restarted, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]


def test_db_locked_is_bounded_and_no_wire_while_uncertain(tmp_path):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock), clock=clock.now)
    with sqlite3.connect(budget.path) as owner:
        owner.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        for priority in ("OPENED_CRITICAL", "EXIT_CRITICAL", "SCALPING_HOT"):
            with pytest.raises(BudgetBackpressure, match="STATE_UNAVAILABLE"):
                budget.acquire("book", priority=priority)
        assert time.monotonic() - started < 3
        owner.rollback()
    assert use(budget, "book", priority="EXIT_CRITICAL")["allowed"]
    assert budget.metrics()["global"]["used"] == 1


def _budget_contents(path):
    with closing(sqlite3.connect(path)) as c:
        return {table: list(c.execute(f"SELECT * FROM {table} ORDER BY 1,2"))
            for table in ("budget_state", "budget_requests", "budget_totals")}


def _confirmed_pre_wire_state_cancel(budget, lease, faults, deadline):
    """Only a known failure before wire may retry confirmed cancellation.

    Caller owns the lease and knows the sender was never invoked. Unknown
    failures, expired/invalid leases and any failure after start remain fatal.
    """
    while True:
        try:
            budget.finish(lease, error_code="PPI_BUDGET_STATE_UNAVAILABLE")
            faults.append("PREWIRE_CANCEL_CONFIRMED")
            return
        except BudgetBackpressure as error:
            if str(error) != "PPI_BUDGET_STATE_UNAVAILABLE" or time.monotonic() >= deadline:
                raise
            faults.append("PREWIRE_CANCEL_STATE_UNAVAILABLE")
            time.sleep(.005)


def test_initialized_restart_only_reads_journal_schema_and_keeps_promises(tmp_path, monkeypatch):
    clock = Clock()
    config = exit_floor(clock)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", config, clock=clock.now)
    assert not use(budget, "book", consumer="SCANNER", priority="OPENED_CRITICAL")["allowed"]
    assert use(budget, "current", consumer="SCANNER")["allowed"]
    before, contents, metrics = budget.path.read_bytes(), _budget_contents(budget.path), budget.metrics()
    writes = []
    connect = GlobalPPIBudget._connect
    def traced_connect(self, **kwargs):
        c = connect(self, **kwargs)
        def authorizer(action, first, second, *_):
            if (action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                    sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE}
                    or action == sqlite3.SQLITE_PRAGMA and first.lower() in {"journal_mode", "max_page_count"} and second is not None):
                writes.append((action, first, second))
            return sqlite3.SQLITE_OK
        c.set_authorizer(authorizer)
        return c
    monkeypatch.setattr(GlobalPPIBudget, "_connect", traced_connect)
    with closing(sqlite3.connect(budget.path)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE budget_state SET value=value WHERE key='schema'")
        restarted = GlobalPPIBudget(budget.path, config, clock=clock.now)
        assert restarted.metrics() == metrics
        assert writes == [], "initialized restart must not issue mode setters, DDL or state writes"
        assert budget.path.read_bytes() == before
        writer.rollback()
    assert _budget_contents(budget.path) == contents
    assert all(use(restarted, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"] for _ in range(5))
    assert restarted.metrics()["global"]["used"] == 6


def test_constructor_exclusive_writer_is_bounded_typed_and_recovers_exact_floor(tmp_path):
    clock = Clock()
    config = exit_floor(clock)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", config, clock=clock.now)
    assert not use(budget, "book", priority="OPENED_CRITICAL")["allowed"]
    assert use(budget, "current", consumer="SCANNER")["allowed"]
    before, contents, metrics = budget.path.read_bytes(), _budget_contents(budget.path), budget.metrics()
    with closing(sqlite3.connect(budget.path)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("UPDATE budget_state SET value=value WHERE key='schema'")
        started = time.monotonic()
        with pytest.raises(BudgetBackpressure, match="^PPI_BUDGET_STATE_UNAVAILABLE$"):
            GlobalPPIBudget(budget.path, config, clock=clock.now)
        assert time.monotonic() - started < 1
        assert budget.path.read_bytes() == before
        writer.rollback()
    restarted = GlobalPPIBudget(budget.path, config, clock=clock.now)
    assert _budget_contents(budget.path) == contents and restarted.metrics() == metrics
    assert all(not use(restarted, "book", priority="OPENED_CRITICAL")["allowed"] for _ in range(5))
    assert all(use(restarted, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"] for _ in range(5))
    assert restarted.metrics()["global"]["used"] == 6


def test_constructor_bootstrap_mutex_is_bounded_and_keeps_initialized_bytes(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    clock = Clock()
    config = exit_floor(clock)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", config, clock=clock.now)
    assert not use(budget, "book", priority="OPENED_CRITICAL")["allowed"]
    before, contents = budget.path.read_bytes(), _budget_contents(budget.path)
    with open(str(budget.path) + ".bootstrap.lock", "a") as owner, ThreadPoolExecutor(max_workers=1) as pool:
        fcntl.flock(owner, fcntl.LOCK_EX)
        try:
            started = time.monotonic()
            pending = pool.submit(GlobalPPIBudget, budget.path, config, clock=clock.now)
            with pytest.raises(BudgetBackpressure, match="^PPI_BUDGET_STATE_UNAVAILABLE$"):
                pending.result(timeout=1)
            assert time.monotonic() - started < 1
        finally:
            fcntl.flock(owner, fcntl.LOCK_UN)
    assert budget.path.read_bytes() == before and _budget_contents(budget.path) == contents
    assert all(use(budget, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"] for _ in range(5))


@pytest.mark.parametrize("foreign", [False, True])
def test_existing_wal_mode_is_rejected_without_conversion_or_state_reset(tmp_path, foreign):
    clock = Clock()
    config = exit_floor(clock)
    path = tmp_path / "budget.sqlite"
    if not foreign:
        budget = GlobalPPIBudget(path, config, clock=clock.now)
        assert not use(budget, "book", priority="OPENED_CRITICAL")["allowed"]
        assert use(budget, "current", consumer="SCANNER")["allowed"]
        contents = _budget_contents(path)
    with closing(sqlite3.connect(path)) as writer:
        if foreign:
            writer.execute("CREATE TABLE foreign_state(value TEXT)")
            writer.execute("INSERT INTO foreign_state VALUES('PAPER_FIXTURE_ONLY')")
            writer.commit()
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="JOURNAL_MODE|SEPARATE_DATABASE"):
        GlobalPPIBudget(path, config, clock=clock.now)
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as reader:
        assert reader.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        if foreign:
            assert list(reader.execute("SELECT * FROM foreign_state")) == [("PAPER_FIXTURE_ONLY",)]
    if not foreign:
        assert _budget_contents(path) == contents


def test_unfinished_bootstrap_keeps_existing_receipts_counters_and_promises(tmp_path):
    clock = Clock()
    config = exit_floor(clock)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", config, clock=clock.now)
    assert not use(budget, "book", priority="OPENED_CRITICAL")["allowed"]
    assert use(budget, "current", consumer="SCANNER")["allowed"]
    metrics = budget.metrics()
    with closing(sqlite3.connect(budget.path)) as interrupted:
        interrupted.execute("DELETE FROM budget_state WHERE key='schema'")
        interrupted.commit()
    contents = _budget_contents(budget.path)
    restarted = GlobalPPIBudget(budget.path, config, clock=clock.now)
    after = _budget_contents(budget.path)
    after["budget_state"] = [row for row in after["budget_state"] if row[0] != "schema"]
    assert after == contents and restarted.metrics() == metrics
    assert all(not use(restarted, "book", priority="OPENED_CRITICAL")["allowed"] for _ in range(5))
    assert all(use(restarted, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"] for _ in range(5))
    assert restarted.metrics()["global"]["used"] == 6


def _flapping_constructor_writer(path, ready, release, committed, proceed):
    with closing(sqlite3.connect(path, timeout=.5)) as writer:
        for index in range(len(ready)):
            writer.execute("BEGIN EXCLUSIVE")
            writer.execute("UPDATE budget_state SET value=value WHERE key='schema'")
            ready[index].set()
            assert release[index].wait(5)
            writer.commit()
            committed[index].set()
            assert proceed[index].wait(5)


def test_real_process_flapping_writer_startup_denials_preserve_exact_exit_floor(tmp_path):
    clock = Clock()
    config = exit_floor(clock)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", config, clock=clock.now)
    assert not use(budget, "book", priority="OPENED_CRITICAL")["allowed"]
    assert use(budget, "current", consumer="SCANNER")["allowed"]
    contents, metrics = _budget_contents(budget.path), budget.metrics()
    ctx = multiprocessing.get_context("spawn")
    ready, release, committed, proceed = ([ctx.Event() for _ in range(6)] for _ in range(4))
    writer = ctx.Process(target=_flapping_constructor_writer,
        args=(str(budget.path), ready, release, committed, proceed))
    writer.start()
    denials, recoveries = 0, 0
    try:
        for index in range(6):
            assert ready[index].wait(5)
            before = budget.path.read_bytes()
            started = time.monotonic()
            with pytest.raises(BudgetBackpressure, match="^PPI_BUDGET_STATE_UNAVAILABLE$"):
                GlobalPPIBudget(budget.path, config, clock=clock.now)
            assert time.monotonic() - started < 1
            denials += 1
            assert budget.path.read_bytes() == before
            release[index].set()
            assert committed[index].wait(5)
            restarted = GlobalPPIBudget(budget.path, config, clock=clock.now)
            assert _budget_contents(budget.path) == contents and restarted.metrics() == metrics
            recoveries += 1
            proceed[index].set()
    finally:
        for signal in [*release, *proceed]:
            signal.set()
        writer.join(10)
        if writer.is_alive():
            writer.terminate()
            writer.join(5)
    assert writer.exitcode == 0 and denials == recoveries == 6
    assert all(not use(restarted, "book", priority="OPENED_CRITICAL")["allowed"] for _ in range(5))
    assert all(use(restarted, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"] for _ in range(5))
    assert restarted.metrics()["global"]["used"] == 6


def test_constructor_io_uncertainty_has_only_sanitized_public_reason(tmp_path, monkeypatch):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock), clock=clock.now)
    before = budget.path.read_bytes()
    def unavailable(*_, **__):
        raise OSError("SENSITIVE_FIXTURE_ONLY_DO_NOT_EXPOSE")
    monkeypatch.setattr(sqlite3, "connect", unavailable)
    with pytest.raises(BudgetBackpressure) as caught:
        GlobalPPIBudget(budget.path, budget.policy, clock=clock.now)
    assert str(caught.value) == "PPI_BUDGET_STATE_UNAVAILABLE" and budget.path.read_bytes() == before


@pytest.mark.parametrize("emitted", [False, True])
def test_finish_sqlite_uncertainty_is_typed_and_preserves_claim_or_wire_debt(tmp_path, emitted):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock), clock=clock.now)
    pending = budget.acquire("current", consumer="SCANNER")
    assert pending["allowed"]
    if emitted:
        budget.start(pending["lease"])
    before, contents, metrics = budget.path.read_bytes(), _budget_contents(budget.path), budget.metrics()
    with closing(sqlite3.connect(budget.path)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        with pytest.raises(BudgetBackpressure, match="^PPI_BUDGET_STATE_UNAVAILABLE$"):
            budget.finish(pending["lease"])
        assert time.monotonic() - started < 1 and budget.path.read_bytes() == before
        writer.rollback()
    assert _budget_contents(budget.path) == contents and budget.metrics() == metrics
    assert budget.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["reason"] == "PPI_SERIAL_BACKPRESSURE"
    budget.finish(pending["lease"])
    assert len(_budget_contents(budget.path)["budget_requests"]) == int(emitted)
    assert all(not use(budget, "book", priority="OPENED_CRITICAL")["allowed"] for _ in range(5))
    assert all(use(budget, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"] for _ in range(5))
    assert budget.metrics()["global"]["used"] == 5 + emitted


def test_wire_scope_enter_sqlite_uncertainty_cannot_release_or_spend_claim(tmp_path):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock), clock=clock.now)
    pending = budget.acquire("current", consumer="SCANNER")
    before, contents = budget.path.read_bytes(), _budget_contents(budget.path)
    with closing(sqlite3.connect(budget.path)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        with pytest.raises(BudgetBackpressure, match="^PPI_BUDGET_STATE_UNAVAILABLE$"):
            with budget.wire_scope(pending["lease"]):
                pytest.fail("uncertain wire scope entered actual sender")
        assert budget.path.read_bytes() == before
        writer.rollback()
    assert _budget_contents(budget.path) == contents
    budget.finish(pending["lease"])
    assert budget.metrics()["global"]["used"] == 0
    assert all(not use(budget, "book", priority="OPENED_CRITICAL")["allowed"] for _ in range(5))
    assert all(use(budget, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"] for _ in range(5))


def test_actual_guarded_pre_wire_sqlite_fault_cleanup_recovers_all_five_exits(wire, tmp_path, monkeypatch):
    clock, calls, _ = wire
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock), clock=clock.now)
    reader = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=budget, consumer="EXIT_READER")
    actual_acquire, actual_start, actual_finish = budget.acquire, budget.start, budget.finish
    admissions, faults, owner = [], [], []
    def traced_acquire(*args, **kwargs):
        result = actual_acquire(*args, **kwargs)
        if result["allowed"]:
            admissions.append(result["lease"])
        return result
    def busy_start(lease):
        lock = sqlite3.connect(budget.path)
        lock.execute("BEGIN IMMEDIATE")
        lock.execute("UPDATE budget_state SET value=value WHERE key='schema'")
        owner.append(lock)
        actual_start(lease)
    def busy_guard_finish(lease, **kwargs):
        try:
            return actual_finish(lease, **kwargs)
        finally:
            for lock in owner:
                lock.rollback()
                lock.close()
            owner.clear()
    try:
        reader.login_once()
        before = len(calls)
        monkeypatch.setattr(budget, "acquire", traced_acquire)
        monkeypatch.setattr(budget, "start", busy_start)
        monkeypatch.setattr(budget, "finish", busy_guard_finish)
        with pytest.raises(BudgetBackpressure, match="^PPI_BUDGET_STATE_UNAVAILABLE$"):
            reader.current("GGAL", "ACCIONES", "A-24HS")
        assert len(calls) == before and reader.last_read_error_code == "PPI_BUDGET_STATE_UNAVAILABLE"
        rows = _budget_contents(budget.path)["budget_requests"]
        assert len(admissions) == len(rows) == 1 and rows[0][-1] == 0
        assert budget.metrics()["global"]["used"] == 0
        monkeypatch.setattr(budget, "start", actual_start)
        lock = sqlite3.connect(budget.path)
        lock.execute("BEGIN IMMEDIATE")
        def busy_cancel_once(lease, **kwargs):
            try:
                return actual_finish(lease, **kwargs)
            except BudgetBackpressure as error:
                assert str(error) == "PPI_BUDGET_STATE_UNAVAILABLE"
                lock.rollback()
                raise
        monkeypatch.setattr(budget, "finish", busy_cancel_once)
        try:
            _confirmed_pre_wire_state_cancel(budget, admissions[0], faults, time.monotonic() + 2)
        finally:
            lock.rollback()
            lock.close()
        assert faults == ["PREWIRE_CANCEL_STATE_UNAVAILABLE", "PREWIRE_CANCEL_CONFIRMED"]
        assert not _budget_contents(budget.path)["budget_requests"] and len(calls) == before
        monkeypatch.setattr(budget, "finish", actual_finish)
        assert all(not use(budget, "book", consumer="SCANNER", priority="OPENED_CRITICAL")["allowed"] for _ in range(5))
        for index in range(5):
            assert scoped_book(reader, (f"EXIT{index}", "ACCIONES", "BYMA", "ARS", "A-24HS"), "EXIT_CRITICAL")
        assert len(calls) == before + 5 and budget.metrics()["global"]["used"] == 5
        rows = _budget_contents(budget.path)["budget_requests"]
        assert len(rows) == 5 and all(row[2] == "book" and row[-1] == 1 for row in rows)
    finally:
        for lock in owner:
            lock.rollback()
            lock.close()
        reader.close()


def test_pre_wire_cleanup_never_hides_an_unknown_lease_failure(tmp_path):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock), clock=clock.now)
    faults = []
    with pytest.raises(BudgetBackpressure, match="^PPI_BUDGET_LEASE_INVALID$"):
        _confirmed_pre_wire_state_cancel(budget, "unknown", faults, time.monotonic() + 2)
    assert not faults and budget.metrics()["global"]["used"] == 0


def test_abandoned_lease_cannot_be_started_after_expiry_even_without_new_owner(tmp_path):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock, common=1), clock=clock.now)
    abandoned = budget.acquire("book", priority="OPENED_CRITICAL")
    assert abandoned["allowed"]
    clock.advance(61)
    with pytest.raises(BudgetBackpressure, match="LEASE_INVALID"):
        budget.start(abandoned["lease"])
    assert use(budget, "book", priority="EXIT_CRITICAL")["allowed"]


def test_expiry_between_admission_and_actual_send_is_fail_closed(tmp_path):
    clock = Clock()
    value = exit_floor(clock)
    value["expires_at"] = (clock.now() + timedelta(seconds=1)).isoformat()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    pending = budget.acquire("book", priority="EXIT_CRITICAL")
    clock.advance(2)
    with pytest.raises(BudgetBackpressure, match="EXPIRED"):
        budget.start(pending["lease"])
    assert budget.acquire("book", priority="EXIT_CRITICAL")["reason"] == "PPI_CAPACITY_EXPIRED_BACKPRESSURE"
    assert budget.metrics()["global"]["used"] == 0


def test_missing_policy_is_rejected_and_fresh_off_budget_is_original_baseline(tmp_path, wire):
    with pytest.raises(ValueError, match="POLICY_INVALID"):
        validate_policy(None)
    values = approved(wire)
    values[0]["mode"] = "OFF"
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    runtime = RuntimePPIBudget(store.path, controller(values), clock=wire[0].now)
    assert runtime.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL") == {"allowed": True, "lease": None}
    assert runtime.budget is None and not runtime.path.exists()


def test_actual_http_scanner_retry_cannot_evade_exit_floor(wire, tmp_path):
    clock, calls, behavior = wire
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock, global_limit=7), clock=clock.now)
    guard = ReadOnlyTransportGuard(budget=budget, consumer="SCANNER").install()
    try:
        behavior["mode"] = "nonjson"
        with guard.read_scope(priority="OPENED_CRITICAL"):
            with pytest.raises(requests.exceptions.JSONDecodeError):
                retry_read(lambda: requests.get("https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Current").json(),
                    retries=1, pause=clock.advance)
            for _ in range(3):
                with pytest.raises(BudgetBackpressure, match="RESERVE"):
                    requests.get("https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book")
        assert len(calls) == 2
        behavior["mode"] = "fresh"
        with guard.read_scope(priority="EXIT_CRITICAL"):
            assert requests.get("https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book").json()["date"]
        assert len(calls) == 3 and budget.metrics()["global"]["used"] == 3
    finally:
        guard.restore()


def test_window_metrics_have_full_scope_denial_reservation_and_borrowing(tmp_path):
    clock = Clock()
    value = exit_floor(clock, common=1)
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    assert use(budget, "book", consumer="SCANNER", priority="OPENED_CRITICAL")["allowed"]
    assert not use(budget, "book", consumer="SCALPING", priority="SCALPING_HOT")["allowed"]
    assert use(budget, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    metrics = budget.metrics()["window"]
    for row in metrics["by_scope"]:
        assert {"requested", "admitted", "used", "dropped", "reserved_total", "reserved_remaining",
            "borrowed_in", "borrowed_out", "priority", "consumer", "denial_reason", "open_positions_count", "exit_demand"} <= row.keys()
    exit_row = next(r for r in metrics["by_scope"] if r["endpoint"] == "book" and r["priority"] == "EXIT_CRITICAL")
    assert exit_row["reserved_total"] == 5 and exit_row["reserved_remaining"] == 4
    assert exit_row["open_positions_count"] == exit_row["exit_demand"] == 5
    opened_row = next(r for r in metrics["by_scope"] if r["endpoint"] == "book" and r["priority"] == "OPENED_CRITICAL")
    assert opened_row["borrowed_in"] == 1 and opened_row["reserved_total"] == 0
    assert metrics["by_endpoint"]["book"]["denial_reason"] == {"PPI_HIGH_PRIORITY_RESERVE_BACKPRESSURE": 1}
    clock.advance(31)
    assert budget.metrics()["window"]["by_endpoint"]["book"]["requested"] == 0


def test_only_higher_priority_may_borrow_lower_floor_and_reports_donor(tmp_path):
    clock = Clock()
    value = policy(clock, limits=dict(current=1, book=2, intraday=1),
        reserves={"EXIT_CRITICAL": {"book": 1}, "SCALPING_HOT": {"book": 1}})
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    assert not use(budget, "book", priority="DISCOVERY")["allowed"]
    assert use(budget, "book", priority="EXIT_CRITICAL")["allowed"]
    assert use(budget, "book", priority="EXIT_CRITICAL")["allowed"]
    rows = budget.metrics()["window"]["by_scope"]
    assert sum(r["borrowed_in"] for r in rows if r["priority"] == "EXIT_CRITICAL") == 1
    assert sum(r["borrowed_out"] for r in rows if r["priority"] == "SCALPING_HOT") == 1
    assert all(r["reserved_remaining"] == 0 for r in rows if r["endpoint"] == "book" and r["priority"] == "SCALPING_HOT")
    assert not use(budget, "book", priority="SCALPING_HOT")["allowed"]


@pytest.mark.parametrize("initial_fraction", [0, .6])
def test_reserved_remaining_uses_exact_wire_boundary_despite_rounded_borrow_counter(tmp_path, initial_fraction):
    clock = Clock()
    clock.advance(initial_fraction)
    value = policy(clock, limits=dict(current=1, book=2, intraday=1),
        reserves={"EXIT_CRITICAL": {"book": 1}, "SCALPING_HOT": {"book": 1}})
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    assert use(budget, "book", priority="EXIT_CRITICAL")["allowed"]
    assert use(budget, "book", priority="EXIT_CRITICAL")["allowed"]
    clock.advance(29)
    row = next(r for r in budget.metrics()["window"]["by_scope"] if r["endpoint"] == "book" and r["priority"] == "SCALPING_HOT")
    assert row["reserved_remaining"] == 0
    clock.advance(1)
    metrics = budget.metrics()["window"]
    row = next(r for r in metrics["by_scope"] if r["endpoint"] == "book" and r["priority"] == "SCALPING_HOT")
    assert metrics["counter_resolution_seconds"] == 1
    assert sum(r["borrowed_out"] for r in metrics["by_scope"] if r["endpoint"] == "book" and r["priority"] == "SCALPING_HOT") == 1
    assert row["reserved_remaining"] == 1
    assert metrics["active_envelopes"][0]["reserved_remaining"]["SCALPING_HOT"]["book"] == 1
    assert use(budget, "book", priority="SCALPING_HOT")["allowed"]


def test_donated_exhausted_endpoint_does_not_hold_phantom_global_reserve(tmp_path):
    clock = Clock()
    value = policy(clock, limits=dict(current=1, book=2, intraday=1),
        reserves={"EXIT_CRITICAL": {"book": 1}, "SCALPING_HOT": {"book": 1}})
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", value, clock=clock.now)
    assert use(budget, "book", priority="EXIT_CRITICAL")["allowed"]
    assert use(budget, "book", priority="EXIT_CRITICAL")["allowed"]
    assert use(budget, "current")["allowed"]
    assert use(budget, "intraday")["allowed"]
    assert budget.metrics()["global"]["used"] == 4
    assert budget.metrics()["window"]["by_endpoint"]["book"]["reserved_remaining"] == 0


def native_opened_store(tmp_path, clock, monkeypatch, count=5):
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    observer._support_schema(store)
    broker = PaperBroker(store, clock_fn=lambda: clock.now().isoformat())
    for index in range(count):
        symbol = "GGAL" if index == 0 else f"EXIT{index}"
        q = quote(symbol=symbol, at=clock.now().isoformat())
        store.add_quote(q)
        result = broker._open(q, D(".8"), {})
        assert result[0], result
        record = observer.financial_catalog.normalize_record(
            {"ticker": symbol, "type": "ACCIONES", "market": "BYMA", "currency": "Pesos"},
            "A-24HS", clock.now().isoformat(), "issue465-native-fixture")
        with store.connect() as connection:
            observer.financial_catalog.persist(connection, record)
    return store, broker


@pytest.mark.parametrize("scanner_first", [False, True])
def test_five_real_positions_stale_scanner_current_fresh_exit_book_independent(wire, tmp_path, monkeypatch, scanner_first):
    clock, calls, behavior = wire
    original_send = requests.adapters.HTTPAdapter.send
    def stale_current_send(adapter, request, **kwargs):
        response = original_send(adapter, request, **kwargs)
        if urlsplit(request.url).path.lower().endswith("/current"):
            payload = response.json()
            payload["date"] = (clock.now() - timedelta(minutes=10)).isoformat()
            response._content = json.dumps(payload).encode()
        elif urlsplit(request.url).path.lower().endswith("/book"):
            # The native broker retains its 10% participation guard. Depth
            # supports a full close here; other tests exercise shallow books.
            payload = response.json()
            payload["bids"][0]["quantity"] = 1000
            payload["offers"][0]["quantity"] = 1000
            response._content = json.dumps(payload).encode()
        return response
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", stale_current_send)
    store, broker = native_opened_store(tmp_path, clock, monkeypatch)
    monkeypatch.setattr(observer, "now_iso", lambda: clock.now().isoformat())
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock, common=int(scanner_first)), clock=clock.now)
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    if scanner_first:
        scanner = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=budget, consumer="SCANNER")
        try:
            scanner.login_once()
            q = observer._read_scanner_quote(scanner, store, "GGAL", "ACCIONES", "A-24HS",
                priority="OPENED_CRITICAL", identity=identity)
            assert q.time_error(clock.now(), require_trade=True) == "TRADE_STALE"
            assert q.time_error(clock.now()) == ""
            store.add_quote(q)
        finally:
            scanner.close()
    exit_reader = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=budget, consumer="EXIT_READER")
    try:
        exit_reader.login_once()
        assert collect_exit_books(exit_reader, store, PaperSessionPolicy(), clock.now().isoformat()) == 0
    finally:
        exit_reader.close()
    if not scanner_first:
        scanner = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=budget, consumer="SCANNER")
        try:
            scanner.login_once()
            q = observer._read_scanner_quote(scanner, store, "GGAL", "ACCIONES", "A-24HS",
                priority="OPENED_CRITICAL", identity=identity)
            assert q.time_error(clock.now(), require_trade=True) == "TRADE_STALE"
            assert q.time_error(clock.now()) == ""
        finally:
            scanner.close()
    assert sum("/Book?" in url for _, url in calls) == 5
    saved = store.latest_quote(store.open_positions()[0])
    assert saved.time_error(clock.now()) == ""
    assert broker._close(store.open_positions()[0], saved, "ISSUE465_EXIT_LIVENESS")
    assert budget.metrics()["window"]["by_endpoint"]["book"]["coalesced"] == 1
    with store.connect() as connection:
        assert connection.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM paper_positions WHERE status='CLOSED'").fetchone()[0] == 1


def test_exact_native_exit_caller_survives_all_five_opened_book_denials(wire, tmp_path, monkeypatch):
    clock, calls, _ = wire
    store, _ = native_opened_store(tmp_path, clock, monkeypatch)
    monkeypatch.setattr(observer, "now_iso", lambda: clock.now().isoformat())
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock), clock=clock.now)
    scanner = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=budget, consumer="SCANNER")
    try:
        scanner.login_once()
        for position in store.open_positions():
            key = tuple(position[k] for k in ("symbol", "asset_class", "market", "currency", "settlement"))
            with pytest.raises(BudgetBackpressure, match="RESERVE"):
                scoped_book(scanner, key, "OPENED_CRITICAL")
    finally:
        scanner.close()
    assert not any("/Book?" in url for _, url in calls)
    exit_reader = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=budget, consumer="EXIT_READER")
    try:
        exit_reader.login_once()
        assert collect_exit_books(exit_reader, store, PaperSessionPolicy(), clock.now().isoformat()) == 0
    finally:
        exit_reader.close()
    assert sum("/Book?" in url for _, url in calls) == 5


def test_approved_dynamic_reserves_real_durable_positions_without_paper_authority_change(wire, tmp_path, monkeypatch):
    values = approved(wire)
    clock = wire[0]
    store, _ = native_opened_store(tmp_path, clock, monkeypatch)
    runtime = RuntimePPIBudget(store.path, controller(values), clock=clock.now)
    current = runtime._current()
    assert current.policy["open_positions_count"] == 5
    assert current.policy["priority_reserves"]["EXIT_CRITICAL"]["book"] == 15
    assert not runtime.acquire("book", consumer="SCANNER", priority="OPENED_CRITICAL")["allowed"]
    assert use(runtime, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")["allowed"]
    with store.connect() as connection:
        assert tuple(connection.execute("SELECT mode,real_orders_sent FROM observer_state").fetchone()) == ("PRODUCTION_PAPER", 0)
        assert connection.execute("SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'").fetchone()[0] == 5


@pytest.mark.parametrize("mutation", ["stale", "future", "clock_missing", "empty", "crossed", "invalid_quantity"])
def test_malformed_stale_or_future_book_never_coalesces(tmp_path, mutation):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock), clock=clock.now)
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    payload = book_payload(clock)
    if mutation in {"stale", "future"}:
        payload["date"] = (clock.now() + timedelta(seconds=-6 if mutation == "stale" else 1)).isoformat()
    elif mutation == "clock_missing":
        del payload["date"]
    elif mutation == "empty":
        payload = {}
    elif mutation == "crossed":
        payload["bids"][0]["price"] = 102
    else:
        payload["bids"][0]["quantity"] = "NaN"
    calls = []
    def fetch():
        calls.append(1)
        return payload
    for _ in range(2):
        budget.coalesced_book(identity, fetch, consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert len(calls) == 2


def test_full_identity_native_book_cache_does_not_collapse_same_ticker(wire, tmp_path):
    clock, calls, _ = wire
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock), clock=clock.now)
    reader = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=budget, consumer="EXIT_READER")
    try:
        reader.login_once()
        ars = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
        usd = ("GGAL", "ACCIONES", "BYMA", "USD_MEP", "A-24HS")
        for identity in (ars, usd, ars, usd):
            assert scoped_book(reader, identity, "EXIT_CRITICAL")["date"]
        assert sum("/Book?" in url for _, url in calls) == 2
        with reader.read_scope(priority="EXIT_CRITICAL", identity=ars):
            with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
                reader.book("OTHER", "ACCIONES", "A-24HS")
    finally:
        reader.close()


def test_shared_book_preserves_provider_clock_has_five_second_ttl_and_no_secrets(tmp_path):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock), clock=clock.now)
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    calls = []
    def fetch():
        calls.append(1)
        return book_payload(clock) | {"account": "PRIVATE_CANARY", "accessToken": "PRIVATE_CANARY"}
    first = budget.coalesced_book(identity, fetch, consumer="EXIT_READER", priority="EXIT_CRITICAL")
    clock.advance(4)
    cached = budget.coalesced_book(identity, fetch, consumer="SCANNER", priority="OPENED_CRITICAL")
    assert cached["date"] == first["date"] and len(calls) == 1
    cached["bids"][0]["price"] = "0"
    assert budget.coalesced_book(identity, fetch, consumer="EXIT_READER", priority="EXIT_CRITICAL")["bids"][0]["price"] == "99"
    with sqlite3.connect(budget.path) as connection:
        assert "PRIVATE_CANARY" not in json.dumps(list(connection.execute("SELECT value FROM budget_state")))
    clock.advance(2)
    assert budget.coalesced_book(identity, fetch, consumer="EXIT_READER", priority="EXIT_CRITICAL")["date"] != first["date"]
    assert len(calls) == 2


def test_book_error_does_not_cache_or_rescue_previous_response(tmp_path):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock), clock=clock.now)
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    def error():
        raise RuntimeError("read failure")
    with pytest.raises(RuntimeError, match="read failure"):
        budget.coalesced_book(identity, error, consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert budget.coalesced_book(identity, lambda: book_payload(clock), consumer="EXIT_READER", priority="EXIT_CRITICAL")["date"]


def test_off_reader_repeats_original_wire_reads_without_coalescing(wire, tmp_path):
    values = approved(wire)
    values[0]["mode"] = "OFF"
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    runtime = RuntimePPIBudget(store.path, controller(values), clock=wire[0].now)
    before = len(wire[1])
    reader = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=runtime, consumer="EXIT_READER")
    try:
        reader.login_once()
        identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
        scoped_book(reader, identity, "EXIT_CRITICAL")
        scoped_book(reader, identity, "EXIT_CRITICAL")
        assert sum("/Book?" in url for _, url in wire[1][before:]) == 2
        assert runtime.budget is None and not runtime.path.exists()
    finally:
        reader.close()


def _budget_worker(path, config, at, consumer, priority, count, ready, start, output):
    clock = Clock(at)
    faults = []
    deadline = time.monotonic() + 5
    while True:
        try:
            budget = GlobalPPIBudget(path, config, clock=clock.now)
            break
        except BudgetBackpressure as error:
            if str(error) != "PPI_BUDGET_STATE_UNAVAILABLE" or time.monotonic() >= deadline:
                raise
            faults.append("CONSTRUCTOR_STATE_UNAVAILABLE")
            time.sleep(.005)
    ready.put(priority)
    assert start.wait(5)
    allowed = 0
    for _ in range(count):
        deadline = time.monotonic() + 5
        while True:
            try:
                row = budget.acquire("book", consumer=consumer, priority=priority)
            except BudgetBackpressure as error:
                assert str(error) == "PPI_BUDGET_STATE_UNAVAILABLE"
                faults.append("ACQUIRE_STATE_UNAVAILABLE")
                row = {"allowed": False, "reason": "PPI_BUDGET_STATE_UNAVAILABLE"}
            if row["allowed"]:
                started = False
                try:
                    with budget.wire_scope(row["lease"]):
                        budget.start(row["lease"])
                        started = True
                        # A finish failure after start remains fatal and its
                        # debt must survive. Only confirmed pre-wire STATE
                        # cancellation below permits an eventual EXIT retry.
                        budget.finish(row["lease"])
                except BudgetBackpressure as error:
                    if started or str(error) != "PPI_BUDGET_STATE_UNAVAILABLE":
                        raise
                    faults.append("START_OR_SCOPE_STATE_UNAVAILABLE")
                    _confirmed_pre_wire_state_cancel(budget, row["lease"], faults, deadline)
                    row = {"allowed": False, "reason": str(error)}
                else:
                    allowed += 1
                    break
            if priority != "EXIT_CRITICAL" or row["reason"] not in {"PPI_SERIAL_BACKPRESSURE", "PPI_BUDGET_STATE_UNAVAILABLE"} or time.monotonic() >= deadline:
                break
            time.sleep(.005)
    output.put((priority, allowed, faults))


def test_three_real_processes_exit_scanner_scalping_burst_cannot_steal_floor(tmp_path):
    clock = Clock()
    config = exit_floor(clock)
    path = str(tmp_path / "budget.sqlite")
    parent = GlobalPPIBudget(path, config, clock=clock.now)
    context = multiprocessing.get_context("fork")
    ready, start, output = context.Queue(), context.Event(), context.Queue()
    scopes = [("SCANNER", "OPENED_CRITICAL", 20), ("SCALPING", "SCALPING_HOT", 20), ("EXIT_READER", "EXIT_CRITICAL", 5)]
    processes = [context.Process(target=_budget_worker, args=(path, config, clock.now().isoformat(), consumer, priority, count, ready, start, output)) for consumer, priority, count in scopes]
    for process in processes:
        process.start()
    assert {ready.get(timeout=5) for _ in processes} == {priority for _, priority, _ in scopes}
    start.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    results = [output.get(timeout=2) for _ in processes]
    assert {priority: allowed for priority, allowed, _ in results} == {"OPENED_CRITICAL": 0, "SCALPING_HOT": 0, "EXIT_CRITICAL": 5}
    faults = [fault for _, _, values in results for fault in values]
    canceled = faults.count("PREWIRE_CANCEL_CONFIRMED")
    assert canceled == faults.count("START_OR_SCOPE_STATE_UNAVAILABLE")
    metrics = parent.metrics()
    assert metrics["global"]["used"] == 5 and metrics["global"]["allowed"] == 5 + canceled
    contents = _budget_contents(parent.path)
    assert len(contents["budget_requests"]) == 5 and all(row[-1] == 1 for row in contents["budget_requests"])
    envelopes = json.loads(dict(contents["budget_state"])["reservation_envelopes_v1"])
    assert len(envelopes) == 1 and envelopes[0]["priority_reserves"]["EXIT_CRITICAL"]["book"] == 5


def _single_flight_worker(path, config, at, ready, release, output, calls, owner):
    clock = Clock(at)
    budget = GlobalPPIBudget(path, config, clock=clock.now)
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    def fetch():
        with calls.get_lock():
            calls.value += 1
        ready.set()
        assert release.wait(5)
        return book_payload(clock)
    if not owner:
        assert ready.wait(5)
    try:
        value = budget.coalesced_book(identity, fetch, consumer="SCANNER" if owner else "EXIT_READER",
            priority="OPENED_CRITICAL" if owner else "EXIT_CRITICAL")
        output.put((owner, value["date"]))
    except BudgetBackpressure as error:
        output.put((owner, str(error)))


def test_shared_book_single_flight_has_no_duplicate_fetch_across_real_processes(tmp_path):
    import threading
    clock = Clock()
    config = exit_floor(clock, common=1)
    path = str(tmp_path / "budget.sqlite")
    GlobalPPIBudget(path, config, clock=clock.now)
    context = multiprocessing.get_context("fork")
    ready, release, output, calls = context.Event(), context.Event(), context.Queue(), context.Value("i", 0)
    owner = context.Process(target=_single_flight_worker, args=(path, config, clock.now().isoformat(), ready, release, output, calls, True))
    follower = context.Process(target=_single_flight_worker, args=(path, config, clock.now().isoformat(), ready, release, output, calls, False))
    owner.start()
    assert ready.wait(5)
    follower.start()
    timer = threading.Timer(.015, release.set)
    timer.start()
    try:
        for process in (owner, follower):
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        release.set()
        timer.cancel()
    results = dict(output.get(timeout=2) for _ in range(2))
    assert results[True] == results[False] == clock.now().isoformat()
    assert calls.value == 1


def test_book_cache_and_single_flight_are_cardinality_bounded(tmp_path):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock), clock=clock.now)
    for index in range(100):
        identity = (f"EXIT{index}", "ACCIONES", "BYMA", "ARS", "A-24HS")
        budget.coalesced_book(identity, lambda: book_payload(clock), consumer="EXIT_READER", priority="EXIT_CRITICAL")
    with sqlite3.connect(budget.path) as connection:
        values = {key: json.loads(value) for key, value in connection.execute("SELECT key,value FROM budget_state")}
    assert len(values["critical_books"]) == 64 and not values["critical_book_flights"]
    assert budget.path.stat().st_size < 1024 ** 2


def test_cache_cannot_bypass_expiry_circuit_or_clock_rollback(tmp_path):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock), clock=clock.now)
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    def forbidden():
        raise AssertionError("failure guard reached the wire")
    budget.coalesced_book(identity, lambda: book_payload(clock), consumer="EXIT_READER", priority="EXIT_CRITICAL")
    clock.advance(-1)
    with pytest.raises(BudgetBackpressure, match="CLOCK_ROLLBACK"):
        budget.coalesced_book(identity, forbidden, consumer="EXIT_READER", priority="EXIT_CRITICAL")
    clock.advance(1)
    budget.report_error("intraday", "PPI_HTTP_429")
    with pytest.raises(BudgetBackpressure, match="CIRCUIT_OPEN"):
        budget.coalesced_book(identity, forbidden, consumer="EXIT_READER", priority="EXIT_CRITICAL")
    budget.policy = dict(budget.policy, expires_at=clock.now().isoformat())
    with pytest.raises(BudgetBackpressure, match="EXPIRED"):
        budget.coalesced_book(identity, forbidden, consumer="EXIT_READER", priority="EXIT_CRITICAL")


def test_changed_policy_fingerprint_forces_new_native_book_probe(tmp_path):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock), clock=clock.now)
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    calls = []
    def fetch():
        calls.append(1)
        return book_payload(clock)
    budget.coalesced_book(identity, fetch, consumer="EXIT_READER", priority="EXIT_CRITICAL")
    budget.policy = dict(budget.policy, configuration_fingerprint="c" * 64)
    budget.coalesced_book(identity, fetch, consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert len(calls) == 2


def test_process_kill_during_book_flight_fails_closed_until_durable_lease_expires(tmp_path):
    clock = Clock()
    config = exit_floor(clock)
    path = str(tmp_path / "budget.sqlite")
    budget = GlobalPPIBudget(path, config, clock=clock.now)
    context = multiprocessing.get_context("fork")
    ready, release, output, calls = context.Event(), context.Event(), context.Queue(), context.Value("i", 0)
    owner = context.Process(target=_single_flight_worker, args=(path, config, clock.now().isoformat(), ready, release, output, calls, True))
    owner.start()
    assert ready.wait(5)
    owner.kill()
    owner.join(timeout=5)
    assert owner.exitcode is not None and owner.exitcode != 0
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    def forbidden():
        raise AssertionError("killed flight was reused before lease expiry")
    with pytest.raises(BudgetBackpressure, match="SINGLE_FLIGHT_BACKPRESSURE"):
        budget.coalesced_book(identity, forbidden, consumer="EXIT_READER", priority="EXIT_CRITICAL")
    clock.advance(61)
    assert budget.coalesced_book(identity, lambda: book_payload(clock), consumer="EXIT_READER", priority="EXIT_CRITICAL")["date"] == clock.now().isoformat()


def test_book_cache_db_lock_never_uses_uncertain_cached_bytes(tmp_path):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", exit_floor(clock), clock=clock.now)
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    budget.coalesced_book(identity, lambda: book_payload(clock), consumer="EXIT_READER", priority="EXIT_CRITICAL")
    with sqlite3.connect(budget.path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(BudgetBackpressure, match="STATE_UNAVAILABLE"):
            budget.coalesced_book(identity, lambda: pytest.fail("locked sidecar sent a request"), consumer="EXIT_READER", priority="EXIT_CRITICAL")
        writer.rollback()


@pytest.mark.parametrize("status", [429, 401, 403])
def test_native_sdk_current_error_code_keeps_exact_sanitized_http_reason(wire, tmp_path, status):
    clock, calls, behavior = wire
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    reader = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=budget, consumer="SCALPING")
    try:
        reader.login_once()
        behavior["status"] = status
        with pytest.raises(Exception):
            reader.intraday("GGAL", "ACCIONES", "A-24HS")
        assert reader.last_read_error_code == f"PPI_HTTP_{status}"
        assert "RAW_PRIVATE" not in reader.last_read_error_code
        before = len(calls)
        with pytest.raises(BudgetBackpressure):
            reader.intraday("GGAL", "ACCIONES", "A-24HS")
        assert reader.last_read_error_code == "PPI_GLOBAL_CIRCUIT_OPEN"
        assert len(calls) == before
        clock.advance(901)
        behavior["status"] = 200
        assert reader.intraday("GGAL", "ACCIONES", "A-24HS")
        assert reader.last_read_error_code is None
    finally:
        reader.close()
