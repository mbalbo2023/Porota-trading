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
