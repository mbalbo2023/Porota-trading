"""R1 PPI wire guard regression: no live provider, host, SQLite or order route.

The fake adapter represents the physical wire boundary. An absent budget or an
unverified lease MUST stop Current/Book/Intraday before reaching that boundary.
"""
from contextlib import contextmanager

import pytest
import requests

from bd_ppi_readonly_guard import (ReadOnlyPolicyViolation, ReadOnlyTransportGuard,
    _GET_PATHS, _BUDGETED_MARKET_GET_PATHS)
from rc6_ppi_global_budget import BudgetBackpressure


PPI_HOST = "https://clientapi.portfoliopersonal.com"
MARKET_PATH = "/api/1.0/marketdata/"


@pytest.fixture
def fake_wire(monkeypatch):
    sends = []

    def wire(_adapter, request, **_kwargs):
        sends.append((request.method, request.url))
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"ok":true}'
        response.request = request
        response.url = request.url
        return response

    # The transport's original_send becomes this in-memory adapter, so even
    # a regression cannot connect to an actual broker endpoint.
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", wire)
    return sends


class BudgetStub:
    def __init__(self, *, lease="test-lease-1", allowed=True, error=None):
        self.lease = lease
        self.allowed = allowed
        self.error = error
        self.acquires = []
        self.started = []
        self.finished = []
        self.wires = []

    def acquire(self, endpoint, *, consumer, priority):
        self.acquires.append((endpoint, consumer, priority))
        if self.error is not None:
            raise BudgetBackpressure(self.error)
        return {"allowed": self.allowed,
                "reason": "PPI_BUDGET_BACKPRESSURE" if not self.allowed else "ADMITTED",
                "lease": self.lease}

    def start(self, lease):
        self.started.append(lease)

    def finish(self, lease, *, status_code=None, error_code=None):
        self.finished.append((lease, status_code, error_code))

    @contextmanager
    def wire_scope(self, lease):
        self.wires.append(lease)
        yield


@pytest.mark.parametrize("endpoint", ("current", "book", "intraday"))
def test_r1_without_shared_budget_never_reaches_provider_adapter(fake_wire, endpoint):
    guard = ReadOnlyTransportGuard(budget=None, consumer="SCANNER").install()
    try:
        with pytest.raises(BudgetBackpressure, match="PPI_SHARED_BUDGET_UNAVAILABLE"):
            requests.get(PPI_HOST + MARKET_PATH + endpoint)
        assert fake_wire == []
        assert guard.calls_allowed == 0
    finally:
        guard.restore()


@pytest.mark.parametrize("endpoint", ("current", "book", "intraday"))
@pytest.mark.parametrize("lease", (None, "", 123))
def test_r1_unverified_lease_never_reaches_provider_adapter(fake_wire, endpoint, lease):
    budget = BudgetStub(lease=lease)
    guard = ReadOnlyTransportGuard(budget=budget, consumer="SCANNER").install()
    try:
        with pytest.raises(BudgetBackpressure, match="PPI_BUDGET_LEASE_UNVERIFIED"):
            requests.get(PPI_HOST + MARKET_PATH + endpoint)
        assert fake_wire == []
        assert guard.calls_allowed == 0
        assert len(budget.acquires) == 1
        assert budget.started == [] and budget.finished == []
    finally:
        guard.restore()


@pytest.mark.parametrize("endpoint", ("current", "book", "intraday"))
def test_r1_one_approved_lease_covers_exactly_one_fake_wire(fake_wire, endpoint):
    budget = BudgetStub()
    guard = ReadOnlyTransportGuard(budget=budget, consumer="SCANNER").install()
    try:
        result = requests.get(PPI_HOST + MARKET_PATH + endpoint)
        assert result.status_code == 200
        assert len(fake_wire) == 1
        assert budget.acquires == [(endpoint, "SCANNER", "DISCOVERY")]
        assert budget.started == ["test-lease-1"]
        assert budget.finished == [("test-lease-1", 200, None)]
        assert budget.wires == ["test-lease-1"]
        assert guard.calls_allowed == 1
    finally:
        guard.restore()


@pytest.mark.parametrize("allowed,error", ((False, None), (True, "PPI_SERIAL_BACKPRESSURE")))
def test_r1_backpressure_never_reaches_provider_adapter(fake_wire, allowed, error):
    budget = BudgetStub(allowed=allowed, error=error)
    guard = ReadOnlyTransportGuard(budget=budget, consumer="SCANNER").install()
    try:
        with pytest.raises(BudgetBackpressure):
            requests.get(PPI_HOST + MARKET_PATH + "book")
        assert fake_wire == []
        assert guard.calls_allowed == 0
    finally:
        guard.restore()


def test_r1_login_route_remains_readonly_auth_post_without_budget(fake_wire):
    guard = ReadOnlyTransportGuard(budget=None, consumer="SCANNER").install()
    try:
        response = requests.post(PPI_HOST + "/api/1.0/account/loginapi")
        assert response.status_code == 200
        assert fake_wire == [("POST", PPI_HOST + "/api/1.0/account/loginapi")]
        assert guard.login_calls == 1
    finally:
        guard.restore()


def test_r1_forbidden_real_order_route_remains_blocked(fake_wire):
    guard = ReadOnlyTransportGuard(budget=BudgetStub(), consumer="SCANNER").install()
    try:
        with pytest.raises(ReadOnlyPolicyViolation):
            requests.post(PPI_HOST + "/api/1.0/orders")
        assert fake_wire == []
    finally:
        guard.restore()


@pytest.mark.parametrize("path", sorted(_GET_PATHS - _BUDGETED_MARKET_GET_PATHS))
def test_r2_unmetered_catalog_history_configuration_get_never_reaches_adapter(fake_wire, path):
    budget = BudgetStub()
    guard = ReadOnlyTransportGuard(budget=budget, consumer="SCANNER").install()
    try:
        with pytest.raises(BudgetBackpressure, match="ENDPOINT_BUDGET_UNVERIFIED"):
            requests.get(PPI_HOST + path)
        assert fake_wire == []
        assert guard.calls_allowed == 0
        assert guard.calls_blocked == 1
        assert budget.acquires == []
    finally:
        guard.restore()


@pytest.mark.parametrize("path", (
    "/api/1.0/configuration/markets",
    "/api/1.0/marketdata/searchinstrument",
    "/api/1.0/marketdata/search",
    "/api/1.0/marketdata/bonds/estimate",
))
def test_r2_direct_adapter_cannot_bypass_unmetered_get(fake_wire, path):
    guard = ReadOnlyTransportGuard(budget=None, consumer="SCANNER").install()
    try:
        request = requests.Request("GET", PPI_HOST + path).prepare()
        with pytest.raises(BudgetBackpressure, match="ENDPOINT_BUDGET_UNVERIFIED"):
            requests.adapters.HTTPAdapter().send(request)
        assert fake_wire == []
        assert guard.calls_allowed == 0
    finally:
        guard.restore()
