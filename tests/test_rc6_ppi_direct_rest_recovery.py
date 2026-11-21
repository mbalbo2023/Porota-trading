"""P0 direct PPI REST recovery under a single synthetic account wire.

No real PPI credentials/socket. This tests the original REST reader's guard,
not the WebSocket gateway. There is no assumed numeric provider quota.
"""

import pytest
import requests
from types import SimpleNamespace

from bd_ppi_readonly_guard import (
    ProductionMarketReader, ReadOnlyPolicyViolation, ReadOnlyTransportGuard,
)
from be_paper_engine import PaperStore
from rc6_ppi_global_budget import (
    BudgetBackpressure, PaperDirectReadBudget, budget_from_environment,
)


BOOK_URL = "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book"


@pytest.fixture
def paper(tmp_path):
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    return store


@pytest.fixture
def fake_wire(monkeypatch):
    calls = []
    status = [200]

    def fake_send(_adapter, request, **kwargs):
        calls.append((request.method, request.url))
        response = requests.Response()
        response.url = request.url
        response.status_code = status[0]
        response._content = b'{"price":100,"date":"2026-11-18T15:00:00Z"}'
        response.request = request
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    return calls, status


def guarded_get(budget):
    guard = ReadOnlyTransportGuard(budget=budget, consumer="SCANNER").install()
    try:
        with requests.Session() as session:
            return session.get(BOOK_URL).status_code
    finally:
        guard.restore()


def test_direct_rest_get_restored_without_fictitious_two_per_hour(paper, fake_wire):
    calls, _ = fake_wire
    budget = PaperDirectReadBudget(paper.path)
    assert [guarded_get(budget) for _ in range(4)] == [200] * 4
    assert len(calls) == 4
    assert all(method == "GET" for method, _ in calls)
    assert not hasattr(budget, "provider_quota_per_hour")


def test_actual_429_closes_wire_across_separate_budget_owners(paper, fake_wire):
    calls, status = fake_wire
    budget = PaperDirectReadBudget(paper.path)
    status[0] = 429
    assert guarded_get(budget) == 429
    assert len(calls) == 1
    # A separate process would reopen the same private lock/breaker file.
    other = PaperDirectReadBudget(paper.path)
    with pytest.raises(BudgetBackpressure, match="PPI_DIRECT_PROVIDER_429"):
        guarded_get(other)
    assert len(calls) == 1


def test_exclusive_wire_lock_blocks_second_owner_before_network(paper, fake_wire):
    calls, _ = fake_wire
    one = PaperDirectReadBudget(paper.path)
    two = PaperDirectReadBudget(paper.path)
    first = one.acquire("book", consumer="SCANNER", priority="DISCOVERY")
    second = two.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert first["allowed"] and second["allowed"]
    with one.wire_scope(first["lease"]):
        one.start(first["lease"])
        with pytest.raises(BudgetBackpressure, match="PPI_DIRECT_SINGLE_WIRE_BUSY"):
            with two.wire_scope(second["lease"]):
                two.start(second["lease"])
        one.finish(first["lease"], status_code=200)
    assert calls == []


def test_bad_paper_mode_or_nonzero_real_orders_blocks_before_wire(paper, fake_wire):
    calls, _ = fake_wire
    # PaperStore.state deliberately refuses normal mode changes. Corrupt the
    # synthetic temp DB directly to prove wire fail-closed on mode drift.
    with paper.connect() as c:
        c.execute("UPDATE observer_state SET mode='SIMULATION' WHERE id=1")
    with pytest.raises(BudgetBackpressure, match="PPI_DIRECT_PAPER_STATE_UNVERIFIED"):
        guarded_get(PaperDirectReadBudget(paper.path))
    with paper.connect() as c:
        c.execute("UPDATE observer_state SET mode='PRODUCTION_PAPER',real_orders_sent=1 WHERE id=1")
    with pytest.raises(BudgetBackpressure, match="PPI_DIRECT_PAPER_STATE_UNVERIFIED"):
        guarded_get(PaperDirectReadBudget(paper.path))
    assert calls == []


def test_recovery_requires_explicit_runtime_flag(paper, monkeypatch):
    monkeypatch.delenv("POROTA_PPI_DIRECT_REST_RECOVERY", raising=False)
    assert not isinstance(budget_from_environment(database=paper.path), PaperDirectReadBudget)
    monkeypatch.setenv("POROTA_PPI_DIRECT_REST_RECOVERY", "1")
    assert isinstance(budget_from_environment(database=paper.path), PaperDirectReadBudget)


def test_market_only_allowed_no_order_mutation_route(paper, fake_wire):
    calls, _ = fake_wire
    guard = ReadOnlyTransportGuard(budget=PaperDirectReadBudget(paper.path)).install()
    try:
        with requests.Session() as session:
            with pytest.raises(Exception, match="Ruta bloqueada"):
                session.post("https://clientapi.portfoliopersonal.com/api/1.0/Orders/New")
    finally:
        guard.restore()
    assert calls == []


def test_existing_truncated_breaker_cannot_reset_observed_hold(paper, fake_wire):
    calls, _ = fake_wire
    budget = PaperDirectReadBudget(paper.path)
    assert guarded_get(budget) == 200  # creates state under exclusive custody
    budget.path.write_bytes(b"")  # model a crashed/incomplete previous write
    with pytest.raises(BudgetBackpressure, match="PPI_DIRECT_BREAKER_STATE_UNVERIFIED"):
        guarded_get(PaperDirectReadBudget(paper.path))
    assert len(calls) == 1


def test_paper_counter_change_after_acquire_denies_at_wire_start(paper, fake_wire):
    calls, _ = fake_wire
    budget = PaperDirectReadBudget(paper.path)
    lease = budget.acquire("book", consumer="EXIT_READER",
                           priority="EXIT_CRITICAL")["lease"]
    with pytest.raises(BudgetBackpressure, match="PPI_DIRECT_PAPER_STATE_UNVERIFIED"):
        with budget.wire_scope(lease):
            paper.state(real_orders_sent=1)
            budget.start(lease)
    assert calls == []


def test_closed_reader_remains_closed_under_legacy_sdk_injection():
    """A missing fixture flag must not make a truly closed reader reusable."""
    reader = ProductionMarketReader.__new__(ProductionMarketReader)
    reader._ProductionMarketReader__authenticated = True
    reader._ProductionMarketReader__client = SimpleNamespace(marketdata=object())
    reader._ProductionMarketReader__closed = True
    with pytest.raises(ReadOnlyPolicyViolation, match="PPI_READER_CLOSED"):
        reader.market_configuration()


def test_all_allowlisted_ppi_get_routes_have_one_budget_endpoint():
    """Regression: Search/History/Configuration cannot bypass EXIT wire custody."""
    from bd_ppi_readonly_guard import _GET_PATHS, _ppi_get_budget_endpoint
    from rc6_ppi_global_budget import PAPER_DIRECT_GET_ENDPOINTS, ENDPOINTS

    mapped = {_ppi_get_budget_endpoint("GET", route) for route in _GET_PATHS}
    assert None not in mapped
    assert mapped == PAPER_DIRECT_GET_ENDPOINTS
    assert set(ENDPOINTS) == {"current", "book", "intraday"}
    for route in _GET_PATHS:
        assert _ppi_get_budget_endpoint("POST", route) is None
    assert _ppi_get_budget_endpoint("GET", "/api/1.0/orders/new") is None


def test_search_configuration_and_native_get_share_one_paper_wire(paper, fake_wire):
    """Three unrelated SDK GETs must all enter the same PAPER lease/breaker."""
    calls, _ = fake_wire
    urls = (
        "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/SearchInstrument?Ticker=GGAL",
        "https://clientapi.portfoliopersonal.com/api/1.0/Configuration/Markets",
        BOOK_URL,
    )
    guard = ReadOnlyTransportGuard(
        budget=PaperDirectReadBudget(paper.path), consumer="SCANNER").install()
    try:
        with requests.Session() as session:
            assert [session.get(url).status_code for url in urls] == [200, 200, 200]
    finally:
        guard.restore()
    assert [url for method, url in calls if method == "GET"] == list(urls)


def test_auxiliary_429_stops_following_native_exit_without_second_send(paper, fake_wire):
    """Observed 429 on search also blocks later book; no independent GET pool."""
    calls, code = fake_wire
    code[0] = 429
    guard = ReadOnlyTransportGuard(
        budget=PaperDirectReadBudget(paper.path), consumer="SCANNER").install()
    try:
        with requests.Session() as session:
            assert session.get(
                "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Search"
            ).status_code == 429
            code[0] = 200
            with pytest.raises(BudgetBackpressure, match="PPI_DIRECT_PROVIDER_429"):
                session.get(BOOK_URL)
    finally:
        guard.restore()
    assert len(calls) == 1


def test_auxiliary_get_without_existing_budget_fails_before_wire(fake_wire):
    calls, _ = fake_wire
    guard = ReadOnlyTransportGuard(budget=None, consumer="SCANNER").install()
    try:
        with requests.Session() as session:
            with pytest.raises(BudgetBackpressure, match="PPI_SHARED_BUDGET_UNAVAILABLE"):
                session.get(
                    "https://clientapi.portfoliopersonal.com/api/1.0/Configuration/Markets")
    finally:
        guard.restore()
    assert calls == []
