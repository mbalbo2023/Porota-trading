"""Synthetic SDK only. No socket, provider, host or independent authority."""
import sys
import types

import pytest

from bd_ppi_readonly_guard import ProductionMarketReader, ReadOnlyPolicyViolation
from ppi_synthetic_budget_fixtures import SyntheticWireLease


@pytest.fixture
def sdk(monkeypatch):
    state = {"calls": [], "login": lambda: None, "current": lambda: 42,
             "stream": lambda: None}
    class FakePPI:
        def __init__(self, **kwargs):
            state["calls"].append("construct")
            self.account = types.SimpleNamespace(login_api=lambda *a: state["login"]())
            self.marketdata = types.SimpleNamespace(current=lambda *a: state["current"]())
            self.realtime = types.SimpleNamespace(
                connect_to_market_data=lambda *a: state["stream"](),
                subscribe_to_element=lambda *a: state["stream"](),
                start_connections=lambda: state["stream"]())
    package = types.ModuleType("ppi_client")
    package.__path__ = []
    module = types.ModuleType("ppi_client.ppi")
    module.PPI = FakePPI
    monkeypatch.setitem(sys.modules, "ppi_client", package)
    monkeypatch.setitem(sys.modules, "ppi_client.ppi", module)
    return state


@pytest.mark.parametrize("during", ["login", "current"])
def test_reader_close_cannot_release_guard_while_sdk_call_is_inflight(sdk, during):
    reader = ProductionMarketReader("FAKE", "FAKE", budget=SyntheticWireLease())
    def inflight():
        with pytest.raises(ReadOnlyPolicyViolation):
            reader.close()
        with pytest.raises(ReadOnlyPolicyViolation):
            ProductionMarketReader("FAKE", "FAKE", budget=SyntheticWireLease())
        return 42
    sdk[during] = inflight
    try:
        reader.login_once()
        assert reader.current("X", "ACCIONES", "INMEDIATA") == 42
    finally:
        reader.close()


def test_closed_reader_and_saved_transport_do_not_reenter_sdk(sdk):
    reader = ProductionMarketReader("FAKE", "FAKE", budget=SyntheticWireLease())
    reader.login_once()
    transport = reader.market_only_sdk_transport(instrument_factory=lambda *a: a)
    reader.close()
    for call in (reader.login_once, lambda: reader.current("X", "ACCIONES", "INMEDIATA"),
                 lambda: transport.connect_market_data(None, None, None),
                 lambda: transport.subscribe_market_data("X", "ACCIONES", "INMEDIATA"),
                 transport.start_market_data):
        with pytest.raises(ReadOnlyPolicyViolation):
            call()


def test_missing_realtime_method_cannot_be_disguised_by_custody_proxy(sdk):
    from rc6_ppi_shared_producer import ProducerBlocked
    reader = ProductionMarketReader("FAKE", "FAKE", budget=SyntheticWireLease())
    try:
        reader.login_once()
        del reader._ProductionMarketReader__client.realtime.start_connections
        with pytest.raises(ProducerBlocked):
            reader.market_only_sdk_transport(instrument_factory=lambda *a: a)
    finally:
        reader.close()


@pytest.mark.parametrize("method", ["connect", "subscribe", "start"])
@pytest.mark.parametrize("raises", [False, True])
def test_uncertain_sdk_stream_invocation_retains_guard_even_on_exception(sdk, method, raises):
    reader = ProductionMarketReader("FAKE", "FAKE", budget=SyntheticWireLease())
    reader.login_once()
    transport = reader.market_only_sdk_transport(instrument_factory=lambda *a: a)
    def invoked():
        if raises:
            raise RuntimeError("synthetic stream start uncertainty")
    sdk["stream"] = invoked
    call = {"connect": lambda: transport.connect_market_data(None, None, None),
            "subscribe": lambda: transport.subscribe_market_data("X", "ACCIONES", "INMEDIATA"),
            "start": transport.start_market_data}[method]
    try:
        if raises:
            with pytest.raises(RuntimeError):
                call()
        else:
            call()
        with pytest.raises(ReadOnlyPolicyViolation):
            reader.close()
        with pytest.raises(ReadOnlyPolicyViolation):
            ProductionMarketReader("FAKE", "FAKE", budget=SyntheticWireLease())
    finally:
        # Synthetic-only fixture teardown; never a claimed SDK safe-stop.
        reader._ProductionMarketReader__stream_invoked = False
        reader.close()
