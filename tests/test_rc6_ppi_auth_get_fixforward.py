"""Restore PPI read-only observations through an in-memory HTTP wire.

No provider calls, secrets or order routes are exercised here.
"""

from types import SimpleNamespace

import pytest
import requests

from bd_ppi_readonly_guard import (
    ProductionMarketReader,
    ReadOnlyPolicyViolation,
    ReadOnlyTransportGuard,
)


BASE = "https://clientapi.portfoliopersonal.com/api/1.0/"


@pytest.fixture
def fake_wire(monkeypatch):
    requests_seen = []

    def send(_adapter, request, **_kwargs):
        requests_seen.append((request.method, request.url))
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"status":"ok"}'
        response.headers["Content-Type"] = "application/json"
        response.request = request
        response.url = request.url
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    return requests_seen


def test_production_reader_reaches_one_login_wire_and_reuses_session(monkeypatch, fake_wire):
    class FakePPI:
        def __init__(self, *, sandbox):
            assert sandbox is False
            self.account = SimpleNamespace(login_api=self.login_api)

        @staticmethod
        def login_api(key, secret):
            assert (key, secret) == ("FAKE_KEY", "FAKE_SECRET")
            return requests.Session().post(BASE + "Account/LoginApi").json()

    monkeypatch.setattr("ppi_client.ppi.PPI", FakePPI)
    reader = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=None)
    try:
        reader.login_once()
        reader.login_once()  # reusing an authenticated reader cannot cause a new POST
        assert reader.metrics["authenticated"] is True
        assert reader.metrics["login_calls"] == 1
        assert fake_wire == [("POST", BASE + "Account/LoginApi")]
    finally:
        reader.close()


@pytest.mark.parametrize("path", [
    "Configuration/Markets",
    "MarketData/SearchInstrument",
    "MarketData/Search",
])
def test_legacy_allowlisted_catalog_and_history_get_reach_fake_wire_without_market_lease(fake_wire, path):
    guard = ReadOnlyTransportGuard(budget=None).install()
    try:
        response = requests.Session().get(BASE + path)
        assert response.status_code == 200
        assert fake_wire == [("GET", BASE + path)]
        assert guard.calls_allowed == 1
    finally:
        guard.restore()


def test_refresh_uses_only_account_whitelist_and_order_routes_stay_blocked(fake_wire):
    guard = ReadOnlyTransportGuard(budget=None).install()
    try:
        assert requests.Session().post(BASE + "Account/RefreshToken").status_code == 200
        for path in ("Order/Confirm", "Orders", "Account/Balance"):
            with pytest.raises(ReadOnlyPolicyViolation):
                requests.Session().post(BASE + path)
        assert fake_wire == [("POST", BASE + "Account/RefreshToken")]
        assert guard.calls_allowed == 1
        assert guard.calls_blocked == 3
    finally:
        guard.restore()


def test_direct_prepared_login_cannot_evade_nine_hundred_second_cooldown(fake_wire):
    clock = [1000.0]
    guard = ReadOnlyTransportGuard(budget=None, clock=lambda: clock[0]).install()
    try:
        assert requests.Session().post(BASE + "Account/LoginApi").status_code == 200
        prepared = requests.Request("POST", BASE + "Account/LoginApi").prepare()
        with pytest.raises(ReadOnlyPolicyViolation, match="cooldown"):
            requests.Session().send(prepared)
        assert fake_wire == [("POST", BASE + "Account/LoginApi")]
        assert guard.login_calls == 1
        clock[0] += 900
        assert requests.Session().send(prepared).status_code == 200
        assert len(fake_wire) == 2
        assert guard.login_calls == 2
    finally:
        guard.restore()


def test_streaming_login_rejection_does_not_consume_cooldown_without_wire(fake_wire):
    guard = ReadOnlyTransportGuard(budget=None).install()
    prepared = requests.Request("POST", BASE + "Account/LoginApi").prepare()
    try:
        with pytest.raises(ReadOnlyPolicyViolation, match="PPI_STREAMING_READ_NOT_OWNED"):
            requests.Session().send(prepared, stream=True)
        assert fake_wire == []
        assert guard.login_calls == 0
        assert requests.Session().send(prepared).status_code == 200
        assert guard.login_calls == 1
        assert len(fake_wire) == 1
    finally:
        guard.restore()
