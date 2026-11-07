"""No external provider: delayed data callbacks must respect live auth HOLD."""
import importlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bd_ppi_readonly_guard import ReadOnlyPolicyViolation


@pytest.mark.parametrize("kind", ["market", "order", "notification"])
def test_delayed_data_callback_cannot_publish_quote_order_or_notification(kind):
    import x_ppi_websocket as stream
    stream = importlib.reload(stream)
    messages = []
    client = stream.PPIRealTimeClient(None, notifier=SimpleNamespace(
        send_telegram=lambda message: messages.append(message)),
        instruments=[("SYNTHETIC", "ACCIONES", "INMEDIATA")])
    if kind == "market":
        callback = client._on_market_data
        event = {"Trade": True, "Ticker": "SYNTHETIC", "Type": "ACCIONES",
                 "Settlement": "INMEDIATA", "Date": datetime.now(timezone.utc).isoformat(),
                 "Price": 100}
    else:
        callback = client._on_account_data
        event = ({"OrderId": "SYNTHETIC", "Status": "FILLED", "QuantityExecuted": 7}
                 if kind == "order" else {"Title": "SYNTHETIC", "Message": "SYNTHETIC"})
    before = dict(stream._stats)
    with pytest.raises(ReadOnlyPolicyViolation, match="PPI_AUTHENTICATION_AUTHORITY_UNVERIFIED"):
        callback(event)
    assert stream.get_cached_price("SYNTHETIC", "ACCIONES", "INMEDIATA") is None
    assert stream.get_order_status_from_stream("SYNTHETIC") is None
    assert stream._tick_cache == {} and stream._order_cache == {}
    assert stream._stats == before
    assert stream.get_stream_status()["healthy"] is False
    assert messages == []


def test_disconnect_callbacks_can_still_revoke_cached_data_and_connected_status(monkeypatch):
    import x_ppi_websocket as stream
    stream = importlib.reload(stream)
    monkeypatch.setattr(stream, "_persist", lambda *a: None)
    stream._tick_cache[("SYNTHETIC", "ACCIONES", "INMEDIATA")] = {"price": 100}
    stream._stats.update(connected=True, account_connected=True)
    client = stream.PPIRealTimeClient(None)
    client._on_market_disconnect()
    client._on_account_disconnect()
    assert stream._tick_cache == {}
    assert stream.get_stream_status()["connected"] is False
    assert stream.get_stream_status()["account_connected"] is False
    assert stream.get_stream_status()["healthy"] is False
