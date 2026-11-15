"""Prevent post-admission PreparedRequest drift from bypassing PPI AUTH HOLD.

Only a fake HTTP adapter is invoked. All budget callbacks are synthetic.
"""

from contextlib import contextmanager

import pytest
import requests

from bd_ppi_readonly_guard import ReadOnlyPolicyViolation, ReadOnlyTransportGuard


CURRENT = "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Current?ticker=SAFE"
LOGIN = "https://clientapi.portfoliopersonal.com/api/1.0/Account/LoginApi"
REFRESH = "https://clientapi.portfoliopersonal.com/api/1.0/Account/RefreshToken"


class MutableBudget:
    """Fake budget with the real acquire/start/finish/wire_scope contract."""

    def __init__(self, mutation=None, phase=None):
        self.mutation = mutation or (lambda: None)
        self.phase = phase
        self.events = []

    def _mutate(self, phase):
        if self.phase == phase:
            self.mutation()

    def acquire(self, endpoint, *, consumer, priority):
        assert endpoint == "current"
        self.events.append("acquire")
        self._mutate("acquire")
        return {"allowed": True, "lease": "FAKE_LEASE"}

    @contextmanager
    def wire_scope(self, lease):
        assert lease == "FAKE_LEASE"
        self.events.append("scope")
        self._mutate("scope")
        yield

    def start(self, lease):
        assert lease == "FAKE_LEASE"
        self.events.append("start")
        self._mutate("start")

    def finish(self, lease, *, status_code=None, error_code=None):
        assert lease == "FAKE_LEASE"
        self.events.append(("finish", status_code, error_code))


@pytest.mark.parametrize("phase,mutation", [
    ("acquire", "LOGIN"),
    ("start", "REFRESH"),
    ("scope", "QUERY"),
    ("acquire", "AUTH_HEADER"),
    ("start", "HOST"),
])
def test_mutated_prepared_request_is_rejected_before_adapter_and_budget_finished(
        monkeypatch, phase, mutation):
    sent = []

    def fake_send(_adapter, request, **_kwargs):
        sent.append((request.method, request.url))
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        response.request = request
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    prepared = requests.Request("GET", CURRENT).prepare()

    def alter():
        if mutation in {"LOGIN", "REFRESH"}:
            prepared.method = "POST"
            prepared.url = LOGIN if mutation == "LOGIN" else REFRESH
        elif mutation == "QUERY":
            prepared.url = CURRENT.replace("SAFE", "OTHER")
        elif mutation == "HOST":
            prepared.url = CURRENT.replace("clientapi.portfoliopersonal.com", "outside.invalid")
        else:
            prepared.headers["Authorization"] = "Bearer TEST_ONLY_SECRET"

    budget = MutableBudget(alter, phase)
    guard = ReadOnlyTransportGuard(budget=budget).install()
    try:
        with pytest.raises(ReadOnlyPolicyViolation, match="PPI_HTTP_PREPARED_REQUEST_DRIFT"):
            requests.Session().send(prepared)
        assert sent == []
        assert budget.events[:2] == ["acquire", "scope"]
        assert budget.events.count("start") == 1
        assert [event for event in budget.events if isinstance(event, tuple)
                and event[0] == "finish"] == [
                    ("finish", None, "PPI_HTTP_PREPARED_REQUEST_DRIFT")]
        assert guard.calls_allowed == 0 and guard.calls_blocked == 1
    finally:
        guard.restore()


def test_wire_receives_independent_pinned_request_even_if_original_drifts_at_adapter(
        monkeypatch):
    observed = []
    prepared = requests.Request("GET", CURRENT).prepare()

    def fake_send(_adapter, request, **_kwargs):
        # Simulates a concurrent holder of the ORIGINAL mutable request after
        # the final guard recheck; the wire object must remain independently safe.
        prepared.method = "POST"
        prepared.url = LOGIN
        prepared.headers["Authorization"] = "Bearer TEST_ONLY_SECRET"
        observed.append((request is prepared, request.method, request.url,
                         request.headers.get("Authorization")))
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        response.request = request
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    budget = MutableBudget()
    guard = ReadOnlyTransportGuard(budget=budget).install()
    try:
        response = requests.Session().send(prepared)
        assert response.status_code == 200
        assert observed == [(False, "GET", CURRENT, None)]
        assert budget.events[-1] == ("finish", 200, None)
    finally:
        guard.restore()


@pytest.mark.parametrize("unsafe_copy", ["SAME_OBJECT", "SHARED_HEADERS"])
def test_prepared_request_copy_must_not_alias_mutable_auth_identity(monkeypatch, unsafe_copy):
    sends = []

    def fake_send(_adapter, request, **_kwargs):
        sends.append((request.method, request.url, request.headers.get("Authorization")))
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        response.request = request
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    prepared = requests.Request("GET", CURRENT).prepare()
    native_copy = prepared.copy

    def unsafe_snapshot():
        if unsafe_copy == "SAME_OBJECT":
            return prepared
        cloned = native_copy()
        cloned.headers = prepared.headers
        return cloned

    monkeypatch.setattr(prepared, "copy", unsafe_snapshot)

    def mutate():
        if unsafe_copy == "SAME_OBJECT":
            prepared.method = "POST"
            prepared.url = LOGIN
        else:
            prepared.headers["Authorization"] = "Bearer TEST_ONLY_SECRET"

    budget = MutableBudget(mutate, "acquire")
    guard = ReadOnlyTransportGuard(budget=budget).install()
    try:
        with pytest.raises(ReadOnlyPolicyViolation, match="PPI_HTTP_PREPARED_REQUEST_COPY_UNSAFE"):
            requests.Session().send(prepared)
        assert sends == [] and budget.events == []
    finally:
        guard.restore()
