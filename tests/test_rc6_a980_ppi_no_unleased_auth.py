"""Live SDK boundaries for canonical/legacy argv paths, never real startup."""
import importlib
import sys
import types

import pytest
import requests

from bd_ppi_readonly_guard import ProductionMarketReader, ReadOnlyPolicyViolation, ReadOnlyTransportGuard


@pytest.mark.parametrize("argv", [
    ["bf_production_paper_observer.py"],
    ["bv_paper_runtime.py", "--exit-reader"],
])
def test_canonical_sdk_login_requires_authority_before_sdk_construction(monkeypatch, argv):
    calls = []
    package = types.ModuleType("ppi_client")
    package.__path__ = []
    module = types.ModuleType("ppi_client.ppi")
    class FakePPI:
        def __init__(self, **kwargs):
            calls.append("SDK_CONSTRUCTOR")
            self.account = types.SimpleNamespace(login_api=lambda *a: calls.append("LOGIN"))
    module.PPI = FakePPI
    monkeypatch.setitem(sys.modules, "ppi_client", package)
    monkeypatch.setitem(sys.modules, "ppi_client.ppi", module)
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr("rc6_ppi_global_budget.budget_from_environment", lambda: None)
    reader = ProductionMarketReader("SYNTHETIC", "SYNTHETIC")
    try:
        with pytest.raises(ReadOnlyPolicyViolation):
            reader.login_once()
        assert calls == []
    finally:
        reader.close()


@pytest.mark.parametrize("path", ["LoginApi", "RefreshToken"])
def test_existing_http_guard_denies_sdk_internal_unleased_auth(path):
    # Exercise the real policy, no HTTP adapter or external call.
    with pytest.raises(ReadOnlyPolicyViolation):
        ReadOnlyTransportGuard().check("POST", "https://clientapi.portfoliopersonal.com/api/1.0/Account/" + path)


@pytest.mark.parametrize("enabled", [None, "true"])
def test_legacy_default_or_enabled_stream_cannot_start_threads_without_authority(monkeypatch, enabled):
    for name in ("PPI_STREAM_ENABLED", "PPI_WEBSOCKET_ENABLED", "PPI_STREAM_ACCOUNT_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    if enabled is not None:
        monkeypatch.setenv("PPI_STREAM_ENABLED", enabled)
    import x_ppi_websocket as stream
    stream = importlib.reload(stream)
    client = stream.PPIRealTimeClient(types.SimpleNamespace(is_authenticated=lambda: True))
    calls = []
    class ForbiddenThread:
        def __init__(self, **kwargs):
            calls.append("THREAD_CREATED")
        def start(self):
            calls.append("THREAD_STARTED")
    monkeypatch.setattr(stream.threading, "Thread", ForbiddenThread)
    monkeypatch.setattr(sys, "argv", ["entrypoint.py"])
    with pytest.raises(ReadOnlyPolicyViolation):
        client.start()
    assert calls == []


def test_legacy_auth_gate_precedes_mutable_login_state_and_transport():
    from test_rc6_legacy_ppi_wire_budget import legacy_module
    module, _ = legacy_module(lambda: None)
    class Unleased:
        @property
        def _auth_blocked_until(self):
            raise AssertionError("Unleased auth must stop before login state or SDK")
    with pytest.raises(ReadOnlyPolicyViolation):
        module.ResilientPPIClient._login(Unleased())
    adapter = module._BudgetedPPIAdapter(types.SimpleNamespace(
        send=lambda *a, **kw: pytest.fail("unleased SDK auth reached transport")))
    request = requests.Request("POST", "https://clientapi.portfoliopersonal.com/api/1.0/account/loginapi").prepare()
    with pytest.raises(ReadOnlyPolicyViolation):
        adapter.send(request)


def test_previously_bound_synthetic_stream_cannot_bypass_current_auth_hold(monkeypatch):
    calls = []
    reader = ProductionMarketReader("SYNTHETIC", "SYNTHETIC", budget=object())
    # Test-only fake authenticated object: models a previously bound transport,
    # never imports or authenticates the actual SDK.
    reader._ProductionMarketReader__authenticated = True
    reader._ProductionMarketReader__client = types.SimpleNamespace(
        marketdata=object(), realtime=types.SimpleNamespace(
            connect_to_market_data=lambda *a: calls.append("CONNECT"),
            subscribe_to_element=lambda *a: calls.append("SUBSCRIBE"),
            start_connections=lambda: calls.append("START")))
    transport = reader.market_only_sdk_transport(instrument_factory=lambda *a: a)
    try:
        with pytest.raises(ReadOnlyPolicyViolation):
            transport.connect_market_data(None, None, None)
        assert calls == []
    finally:
        reader._ProductionMarketReader__stream_invoked = False  # fake-only teardown
        reader.close()


@pytest.mark.parametrize("callback_name", ["_on_account_connect", "_on_market_connect"])
def test_delayed_prebound_callback_cannot_subscribe_after_auth_hold(callback_name):
    import x_ppi_websocket as stream
    calls = []
    client = stream.PPIRealTimeClient(types.SimpleNamespace(
        account_number="SYNTHETIC", client=types.SimpleNamespace(
            realtime=types.SimpleNamespace(
                subscribe_to_account_data=lambda *a: calls.append("SDK_ACCOUNT_SUBSCRIBE")))))
    # A saved callback can arrive even though normal start was denied. The
    # callback itself must reject it before its broad exception handler.
    callback = getattr(client, callback_name)
    before = (stream._stats["connected"], stream._stats["account_connected"])
    from bd_ppi_readonly_guard import require_ppi_authentication_authority
    with pytest.raises(ReadOnlyPolicyViolation):
        require_ppi_authentication_authority()
    with pytest.raises(ReadOnlyPolicyViolation):
        callback()
    assert calls == []
    assert (stream._stats["connected"], stream._stats["account_connected"]) == before


def test_offline_paper_ledger_and_explicit_stream_off_need_no_live_auth(tmp_path, monkeypatch):
    from be_paper_engine import PaperStore, PaperBroker
    from _version import MODE, EXECUTION, REAL_ORDER_CAPABILITY
    assert (MODE, EXECUTION, REAL_ORDER_CAPABILITY) == ("PRODUCTION_PAPER", "SIMULATED", "BLOCKED")
    store = PaperStore(str(tmp_path / "offline-paper.sqlite"))
    broker = PaperBroker(store)
    store.state(process_state="RUNNING", real_orders_sent=0)
    assert broker is not None
    with store.connect() as connection:
        assert connection.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0
    monkeypatch.setenv("PPI_STREAM_ENABLED", "false")
    import x_ppi_websocket as stream
    stream = importlib.reload(stream)
    assert stream.PPIRealTimeClient(None).start() is None
