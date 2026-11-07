"""A680 OFFHOST: one broker-wire receipt, not a second budget lease.

All requests are routed through an injected in-memory requests adapter. The
fixtures never open a socket, log in to PPI or exercise order endpoints.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import sys
from types import SimpleNamespace
from types import ModuleType
from urllib.parse import urlencode

import pytest

try:
    import requests
except ModuleNotFoundError:
    # This scratch executor has no requests wheel. Mimic only the two
    # monkeypatched methods needed by bd_ppi_readonly_guard; CI uses the real
    # pinned requests dependency and gets the exact same test assertions.
    requests = ModuleType("requests")
    sessions = ModuleType("requests.sessions")
    adapters = ModuleType("requests.adapters")

    class MinimalResponse:
        def __init__(self):
            self.status_code = 0
            self.url = ""
            self._content = b""
            self.headers = {}
            self.request = None

        @property
        def content(self):
            return self._content

        def json(self):
            return json.loads(self.content)

    class MinimalAdapter:
        def send(self, request, **kwargs):
            raise AssertionError("REAL_WIRE_FORBIDDEN_IN_OFFHOST_TEST")

    class MinimalSession:
        def request(self, method, url, **kwargs):
            params = kwargs.get("params") or {}
            request = SimpleNamespace(method=method.upper(),
                                      url=url + "?" + urlencode(params))
            return MinimalAdapter().send(request, **kwargs)

        def get(self, url, *, params=None, **kwargs):
            return self.request("GET", url, params=params, **kwargs)

        def close(self):
            return None

    sessions.Session = MinimalSession
    adapters.HTTPAdapter = MinimalAdapter
    requests.sessions = sessions
    requests.adapters = adapters
    requests.Session = MinimalSession
    requests.Response = MinimalResponse
    sys.modules["requests"] = requests
    sys.modules["requests.sessions"] = sessions
    sys.modules["requests.adapters"] = adapters

from bd_ppi_readonly_guard import (ProductionMarketReader,
                                   ReadOnlyPolicyViolation)


BASE = "https://clientapi.portfoliopersonal.com/api/1.0/MarketData"
FIVE = ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")


class FakeBudget:
    def __init__(self):
        self.calls = []
        self.in_wire_scope = False

    def acquire(self, endpoint, *, consumer, priority):
        self.calls.append(("acquire", endpoint, consumer, priority))
        return {"allowed": True, "lease": "synthetic-secret-lease"}

    @contextmanager
    def wire_scope(self, lease):
        self.in_wire_scope = True
        try:
            yield
        finally:
            self.in_wire_scope = False

    def start(self, lease):
        self.calls.append(("start", lease))

    def finish(self, lease, **kwargs):
        self.calls.append(("finish", lease, kwargs))

    def report_error(self, endpoint, code):
        self.calls.append(("report_error", endpoint, code))


class SDKMarket:
    def __init__(self, session, *, override_ticker=None, two_reads=False,
                 fabricates=False, changes_payload=False):
        self.session = session
        self.override_ticker = override_ticker
        self.two_reads = two_reads
        self.fabricates = fabricates
        self.changes_payload = changes_payload

    def _read(self, endpoint, ticker, type_, settlement):
        if self.fabricates:
            return {"date": "2026-11-04T17:01:00.000Z", "price": 100}
        params = {"Ticker": self.override_ticker or ticker,
                  "Type": type_, "Settlement": settlement}
        response = self.session.get(f"{BASE}/{endpoint}", params=params)
        result = response.json()
        if self.two_reads:
            result = self.session.get(f"{BASE}/{endpoint}", params=params).json()
        if self.changes_payload:
            result = {**result, "price": 999999}
        return result

    def current(self, ticker, type_, settlement):
        return self._read("Current", ticker, type_, settlement)

    def book(self, ticker, type_, settlement):
        return self._read("Book", ticker, type_, settlement)

    def intraday(self, ticker, type_, settlement):
        return self._read("Intraday", ticker, type_, settlement)


@pytest.fixture
def fake_wire(monkeypatch):
    expected = {
        "Current": {"date": "2026-11-04T17:01:00.000Z", "price": 100},
        "Book": {"date": "2026-11-04T17:01:00.000Z",
                 "bids": [{"position": 1, "price": 100, "quantity": 2}],
                 "offers": [{"position": 1, "price": 101, "quantity": 3}]},
        "Intraday": [{"date": "2026-11-04T17:01:00.000Z", "price": 100}],
    }
    calls = []
    budget = FakeBudget()
    budget.test_status = 200
    budget.test_content_override = None

    def fake_adapter_send(_adapter, request, **kwargs):
        assert budget.in_wire_scope
        endpoint = request.url.split("?", 1)[0].rsplit("/", 1)[-1]
        calls.append((request.method, endpoint, request.url))
        response = requests.Response()
        response.status_code = budget.test_status
        response.url = request.url
        response._content = (budget.test_content_override if budget.test_content_override is not None
                             else json.dumps(expected[endpoint], sort_keys=True).encode("utf-8"))
        response.headers["Content-Type"] = "application/json"
        response.request = request
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_adapter_send)
    reader = ProductionMarketReader("TEST_KEY_NOT_REAL", "TEST_SECRET_NOT_REAL",
                                    budget=budget, consumer="SCANNER")
    session = requests.Session()
    try:
        yield reader, session, budget, calls, expected
    finally:
        session.close()
        reader.close()


@pytest.mark.parametrize("endpoint", ["current", "book", "intraday"])
def test_single_guarded_native_receipt_is_exact_sdk_response(fake_wire, endpoint):
    reader, session, budget, calls, expected = fake_wire
    reader._ProductionMarketReader__client = SimpleNamespace(marketdata=SDKMarket(session))
    reader._ProductionMarketReader__authenticated = True

    observed = reader.capture_guarded_native(endpoint, FIVE, priority="EXIT_CRITICAL")
    assert observed["native_payload"] == expected[endpoint.title()]
    receipt = observed["guard_wire_receipt"]
    assert receipt["schema"] == "RC6_PPI_NATIVE_WIRE_RECEIPT_V1"
    assert receipt["endpoint"] == endpoint
    assert receipt["http_method"] == "GET"
    assert receipt["consumer"] == "SCANNER"
    assert receipt["priority"] == "EXIT_CRITICAL"
    assert receipt["claimed_five_key"] == list(FIVE)
    assert receipt["request_identity_3key"] == [FIVE[0], FIVE[1], FIVE[4]]
    assert receipt["identity_source"] == "CALLER_ONLY_UNVERIFIED"
    assert receipt["adapter_send_invoked"] is True
    assert receipt["adapter_response_completed"] is True
    assert receipt["http_status_code"] == 200
    assert receipt["raw_body_sha256"] != "0" * 64
    assert receipt["parsed_body_sha256"] == sha256(json.dumps(
        expected[endpoint.title()], sort_keys=True, ensure_ascii=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert len(receipt["guard_budget_receipt_sha256"]) == 64
    assert len(receipt["budget_lease_sha256"]) == 64
    assert "synthetic-secret-lease" not in json.dumps(receipt)
    assert "TEST_SECRET" not in json.dumps(receipt)
    assert "clientapi.portfoliopersonal.com" not in json.dumps(receipt)
    assert "?Ticker=" not in json.dumps(receipt)
    assert calls == [("GET", endpoint.title(), calls[0][2])]
    assert [entry[0] for entry in budget.calls] == ["acquire", "start", "finish"]


@pytest.mark.parametrize("scenario", ["fabricates", "two_reads", "changes_payload",
                                      "wrong_ticker"])
def test_missing_multiple_or_mismatched_wire_receipt_never_certifies(fake_wire, scenario):
    reader, session, budget, calls, _ = fake_wire
    options = {scenario: True} if scenario != "wrong_ticker" else {"override_ticker": "GGAL"}
    reader._ProductionMarketReader__client = SimpleNamespace(
        marketdata=SDKMarket(session, **options))
    reader._ProductionMarketReader__authenticated = True
    with pytest.raises(ReadOnlyPolicyViolation, match="PPI_NATIVE_WIRE_RECEIPT_"):
        reader.capture_guarded_native("current", FIVE, priority="HOT")
    assert all(item[0] == "GET" for item in calls)


@pytest.mark.parametrize("endpoint", ["search", "history", "market_configuration",
                                      "order", "login", "cancel"])
def test_nonbudgeted_or_order_methods_block_before_wire(fake_wire, endpoint):
    reader, session, budget, calls, _ = fake_wire
    reader._ProductionMarketReader__client = SimpleNamespace(marketdata=SDKMarket(session))
    reader._ProductionMarketReader__authenticated = True
    with pytest.raises(ReadOnlyPolicyViolation, match="PPI_NATIVE_WIRE_RECEIPT_ENDPOINT_DENIED"):
        reader.capture_guarded_native(endpoint, FIVE, priority="HOT")
    assert calls == [] and budget.calls == []


def test_http_error_must_not_be_certified_as_native_data(fake_wire):
    reader, session, budget, calls, _ = fake_wire
    reader._ProductionMarketReader__client = SimpleNamespace(marketdata=SDKMarket(session))
    reader._ProductionMarketReader__authenticated = True
    budget.test_status = 429
    with pytest.raises(ReadOnlyPolicyViolation, match="PPI_NATIVE_WIRE_RECEIPT_SOURCE_UNVERIFIED"):
        reader.capture_guarded_native("current", FIVE, priority="HOT")
    assert len(calls) == 1
    assert [entry[0] for entry in budget.calls] == ["acquire", "start", "finish"]
    assert budget.calls[-1][-1]["status_code"] == 429


def test_non_json_body_cannot_be_bound_to_native_sdk_result(fake_wire):
    reader, session, budget, calls, _ = fake_wire
    reader._ProductionMarketReader__client = SimpleNamespace(marketdata=SDKMarket(session))
    reader._ProductionMarketReader__authenticated = True
    budget.test_content_override = b"upstream error (fake, without credentials)"
    with pytest.raises(ValueError):
        reader.capture_guarded_native("current", FIVE, priority="HOT")
    assert len(calls) == 1
    assert [entry[0] for entry in budget.calls] == ["acquire", "start", "finish", "report_error"]


def test_oversized_wire_body_is_not_emitted_as_source_witness(fake_wire):
    reader, session, budget, calls, _ = fake_wire
    reader._ProductionMarketReader__client = SimpleNamespace(marketdata=SDKMarket(session))
    reader._ProductionMarketReader__authenticated = True
    budget.test_content_override = (b'{"date":"2026-11-04T17:01:00.000Z","padding":"' +
                                    b'X' * 1_048_576 + b'"}')
    with pytest.raises(ReadOnlyPolicyViolation, match="PPI_NATIVE_WIRE_RECEIPT_SOURCE_UNVERIFIED"):
        reader.capture_guarded_native("current", FIVE, priority="HOT")
    assert len(calls) == 1
    assert [entry[0] for entry in budget.calls] == ["acquire", "start", "finish"]


def test_deep_json_payload_not_certified_even_if_syntactically_valid(fake_wire):
    reader, session, budget, calls, _ = fake_wire
    reader._ProductionMarketReader__client = SimpleNamespace(marketdata=SDKMarket(session))
    reader._ProductionMarketReader__authenticated = True
    budget.test_content_override = b'[' * 40 + b'1' + b']' * 40
    with pytest.raises(ReadOnlyPolicyViolation, match="PPI_NATIVE_WIRE_RECEIPT_SDK_PAYLOAD_INVALID"):
        reader.capture_guarded_native("current", FIVE, priority="HOT")
    assert len(calls) == 1


def test_default_current_path_and_paper_read_only_surface_unchanged(fake_wire):
    reader, session, budget, calls, expected = fake_wire
    reader._ProductionMarketReader__client = SimpleNamespace(marketdata=SDKMarket(session))
    reader._ProductionMarketReader__authenticated = True
    # No optional native capture active: same original SDK return and exactly
    # one existing per-wire lease. The reader still cannot place any orders.
    assert reader.current("AL30", "BONOS", "INMEDIATA") == expected["Current"]
    assert len(calls) == 1
    assert [entry[0] for entry in budget.calls] == ["acquire", "start", "finish"]
    assert not any(hasattr(reader, name) for name in
                   ("send_order", "place_order", "cancel_order", "account"))
