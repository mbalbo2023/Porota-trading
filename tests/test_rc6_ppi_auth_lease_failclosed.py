"""PPI AUTH/refresh allowlist, cooldown and fail-closed real-order paths.

Every HTTP operation in this suite terminates at an in-memory fake wire.
"""

from types import SimpleNamespace

import pytest
import requests

from bd_ppi_readonly_guard import (
    ProductionMarketReader,
    ReadOnlyPolicyViolation,
    ReadOnlyTransportGuard,
)
from ppi_synthetic_budget_fixtures import SyntheticWireLease
from ppi_synthetic_auth_fixtures import allow_fake_auth_only


BASE = "https://clientapi.portfoliopersonal.com/api/1.0/account/"
CURRENT = "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Current"


@pytest.mark.parametrize("endpoint", ["LoginApi", "RefreshToken"])
def test_auth_request_reaches_only_allowlisted_fake_wire(monkeypatch, endpoint):
    sends = []

    def fake_send(_adapter, request, **_kwargs):
        sends.append(request.url)
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"ok":true}'
        response.request = request
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    guard = ReadOnlyTransportGuard(budget=SyntheticWireLease()).install()
    try:
        assert requests.Session().post(BASE + endpoint).status_code == 200
        assert sends == [BASE + endpoint]
        assert guard.login_calls == (1 if endpoint == "LoginApi" else 0)
        assert guard.calls_allowed == 1
    finally:
        guard.restore()


def test_reader_reuses_single_sdk_login(monkeypatch):
    constructed = []

    class FakePPI:
        def __init__(self, **_kwargs):
            constructed.append("SDK_CONSTRUCTED")
            self.account = SimpleNamespace(login_api=lambda *_: constructed.append("SDK_LOGIN"))

    monkeypatch.setattr("ppi_client.ppi.PPI", FakePPI)
    reader = ProductionMarketReader("SYNTHETIC_KEY", "SYNTHETIC_SECRET",
                                    budget=SyntheticWireLease())
    try:
        reader.login_once()
        reader.login_once()
        assert constructed == ["SDK_CONSTRUCTED", "SDK_LOGIN"]
        assert reader.metrics["authenticated"] is True
        # Fake SDK did not make HTTP. Separate transport tests cover wire count.
        assert reader.metrics["login_calls"] == 0
    finally:
        reader.close()


def test_sdk_401_refresh_after_budgeted_get_is_only_allowlisted_post(monkeypatch):
    sends = []

    def fake_send(_adapter, request, **_kwargs):
        sends.append((request.method, request.url))
        response = requests.Response()
        response.status_code = 401 if request.method == "GET" else 200
        response._content = b'{"error":"SYNTHETIC_401"}'
        response.request = request
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    guard = ReadOnlyTransportGuard(budget=SyntheticWireLease()).install()
    try:
        # Models SDK's GET 401 => POST RefreshToken recursion. RefreshToken
        # is an independent, explicitly allowlisted auth route.
        response = requests.Session().get(CURRENT)
        assert response.status_code == 401
        assert requests.Session().post(BASE + "RefreshToken").status_code == 200
        with pytest.raises(ReadOnlyPolicyViolation):
            requests.Session().post(BASE + "Balance")
        assert sends == [("GET", CURRENT), ("POST", BASE + "RefreshToken")]
        assert guard.calls_allowed == 2
    finally:
        guard.restore()


def test_auth_configuration_cannot_grant_order_or_account_routes(monkeypatch):
    monkeypatch.setenv("PPI_AUTH_LEASE_SIGNED_JSON", '{"signature":"FAKE"}')
    monkeypatch.setenv("PPI_AUTH_LOGIN_QUOTA", "1000")
    fake_signed_object = SimpleNamespace(require=lambda *_args, **_kwargs: None)
    with pytest.raises(TypeError, match="auth_lease"):
        ReadOnlyTransportGuard(auth_lease=fake_signed_object)
    guard = ReadOnlyTransportGuard()
    assert guard.check("POST", BASE + "LoginApi")
    with pytest.raises(ReadOnlyPolicyViolation):
        guard.check("POST", BASE + "Balance")
    with pytest.raises(ReadOnlyPolicyViolation):
        guard.check("POST", BASE + "../Order/Confirm")


def test_fake_only_seam_rejects_native_http_adapter(monkeypatch):
    with pytest.raises(AssertionError, match="FAKE_HTTP_ADAPTER_REQUIRED"):
        allow_fake_auth_only(monkeypatch)


def test_paper_offline_ledger_remains_zero_with_login_allowlisted(tmp_path):
    from _version import EXECUTION, MODE, REAL_ORDER_CAPABILITY
    from be_paper_engine import PaperBroker, PaperStore

    assert (MODE, EXECUTION, REAL_ORDER_CAPABILITY) == (
        "PRODUCTION_PAPER", "SIMULATED", "BLOCKED")
    store = PaperStore(str(tmp_path / "no-live-auth-paper.sqlite"))
    broker = PaperBroker(store)
    store.state(process_state="RUNNING", real_orders_sent=0)
    assert broker is not None
    with store.connect() as connection:
        assert connection.execute(
            "SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0
    # This is a local fake DB assertion, NOT a physical runtime receipt.
    # A local allowlist check is not an order and does not alter PAPER state.
    assert ReadOnlyTransportGuard().check("POST", BASE + "LoginApi")
