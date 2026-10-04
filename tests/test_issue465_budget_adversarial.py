"""#465 F-01: actual SQLite, SDK fake wire and canonical caller regressions.

No network, productive store, broker credential, or real-order route is used.
The exact auditor reproduction intentionally fails at #463's frozen head.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
import multiprocessing
import sqlite3
import time
from urllib.parse import urlsplit

import pytest
import requests

from bd_ppi_readonly_guard import ProductionMarketReader, ReadOnlyTransportGuard, retry_read
from be_paper_engine import D, PaperBroker, PaperStore
from bq_exit_policy import PaperSessionPolicy
import bf_production_paper_observer as observer
from bv_paper_runtime import collect_exit_books
from rc6_ppi_global_budget import BudgetBackpressure, GlobalPPIBudget, RuntimePPIBudget, PRIORITIES, validate_policy
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


def _budget_worker(path, config, at, consumer, priority, count, start, output):
    clock = Clock(at)
    budget = GlobalPPIBudget(path, config, clock=clock.now)
    start.wait(5)
    allowed = 0
    for _ in range(count):
        deadline = time.monotonic() + 5
        while True:
            try:
                row = use(budget, "book", consumer=consumer, priority=priority)
            except BudgetBackpressure:
                row = {"allowed": False, "reason": "PPI_BUDGET_STATE_UNAVAILABLE"}
            if row["allowed"]:
                allowed += 1
                break
            if priority != "EXIT_CRITICAL" or row["reason"] not in {"PPI_SERIAL_BACKPRESSURE", "PPI_BUDGET_STATE_UNAVAILABLE"} or time.monotonic() >= deadline:
                break
            time.sleep(.005)
    output.put((priority, allowed))


def test_three_real_processes_exit_scanner_scalping_burst_cannot_steal_floor(tmp_path):
    clock = Clock()
    config = exit_floor(clock)
    path = str(tmp_path / "budget.sqlite")
    parent = GlobalPPIBudget(path, config, clock=clock.now)
    context = multiprocessing.get_context("fork")
    start, output = context.Event(), context.Queue()
    scopes = [("SCANNER", "OPENED_CRITICAL", 20), ("SCALPING", "SCALPING_HOT", 20), ("EXIT_READER", "EXIT_CRITICAL", 5)]
    processes = [context.Process(target=_budget_worker, args=(path, config, clock.now().isoformat(), consumer, priority, count, start, output)) for consumer, priority, count in scopes]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    assert dict(output.get(timeout=2) for _ in processes) == {"OPENED_CRITICAL": 0, "SCALPING_HOT": 0, "EXIT_CRITICAL": 5}
    assert parent.metrics()["global"]["used"] == 5


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
