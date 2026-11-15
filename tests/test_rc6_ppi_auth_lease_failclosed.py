"""AUTH/refresh default HOLD, including prepared HTTP and fake SDK 401 retry.

No positive lease is shipped: a replaceable SQLite counter cannot prove
account-wide monotonic quota. All permitted test GETs hit an in-memory fake.
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
HOLD = "PPI_AUTHENTICATION_AUTHORITY_UNVERIFIED"


@pytest.mark.parametrize("endpoint", ["LoginApi", "RefreshToken"])
def test_auth_request_and_prepared_send_stop_before_fake_wire(monkeypatch, endpoint):
    sends = []

    def fake_send(_adapter, request, **_kwargs):
        sends.append(request.url)
        raise AssertionError("AUTH_ESCAPED_TO_FAKE_WIRE")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    guard = ReadOnlyTransportGuard(budget=SyntheticWireLease()).install()
    try:
        with pytest.raises(ReadOnlyPolicyViolation, match=HOLD):
            requests.Session().post(BASE + endpoint)
        prepared = requests.Request("POST", BASE + endpoint).prepare()
        with pytest.raises(ReadOnlyPolicyViolation, match=HOLD):
            requests.Session().send(prepared)
        assert sends == []
        assert guard.login_calls == 0
        assert guard.calls_allowed == 0
    finally:
        guard.restore()


def test_reader_refuses_auth_before_sdk_constructor(monkeypatch):
    constructed = []

    class FakePPI:
        def __init__(self, **_kwargs):
            constructed.append("SDK_CONSTRUCTED")
            self.account = SimpleNamespace(login_api=lambda *_: pytest.fail("SDK LoginApi sent"))

    monkeypatch.setattr("ppi_client.ppi.PPI", FakePPI)
    reader = ProductionMarketReader("SYNTHETIC_KEY", "SYNTHETIC_SECRET",
                                    budget=SyntheticWireLease())
    try:
        with pytest.raises(ReadOnlyPolicyViolation, match=HOLD):
            reader.login_once()
        assert constructed == []
        assert reader.metrics["authenticated"] is False
        assert reader.metrics["login_calls"] == 0
    finally:
        reader.close()


def test_sdk_401_refresh_after_budgeted_get_is_denied_before_fake_post(monkeypatch):
    sends = []

    def fake_send(_adapter, request, **_kwargs):
        sends.append((request.method, request.url))
        response = requests.Response()
        response.status_code = 401
        response._content = b'{"error":"SYNTHETIC_401"}'
        response.request = request
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    guard = ReadOnlyTransportGuard(budget=SyntheticWireLease()).install()
    try:
        # Models SDK's GET 401 => POST RefreshToken recursion; the GET lease
        # cannot authorize even one POST, regardless of retries/cooldown.
        response = requests.Session().get(CURRENT)
        assert response.status_code == 401
        with pytest.raises(ReadOnlyPolicyViolation, match=HOLD):
            requests.Session().post(BASE + "RefreshToken")
        with pytest.raises(ReadOnlyPolicyViolation, match=HOLD):
            requests.Session().post(BASE + "RefreshToken")
        assert sends == [("GET", CURRENT)]
        assert guard.calls_allowed == 1
    finally:
        guard.restore()


def test_runtime_auth_cannot_be_enabled_by_signed_looking_input_or_env(monkeypatch):
    monkeypatch.setenv("PPI_AUTH_LEASE_SIGNED_JSON", '{"signature":"FAKE"}')
    monkeypatch.setenv("PPI_AUTH_LOGIN_QUOTA", "1000")
    fake_signed_object = SimpleNamespace(require=lambda *_args, **_kwargs: None)
    with pytest.raises(TypeError, match="auth_lease"):
        ReadOnlyTransportGuard(auth_lease=fake_signed_object)
    with pytest.raises(ReadOnlyPolicyViolation, match=HOLD):
        ReadOnlyTransportGuard().check("POST", BASE + "LoginApi")


def test_fake_only_seam_rejects_native_http_adapter(monkeypatch):
    with pytest.raises(AssertionError, match="FAKE_HTTP_ADAPTER_REQUIRED"):
        allow_fake_auth_only(monkeypatch)


def test_paper_offline_ledger_survives_missing_ppi_auth_authority(tmp_path):
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
    with pytest.raises(ReadOnlyPolicyViolation, match=HOLD):
        ReadOnlyTransportGuard().check("POST", BASE + "LoginApi")
