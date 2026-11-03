"""A heartbeat reflects completed scanner work, never a stalled call."""
from datetime import datetime, timezone
from unittest.mock import patch

from bf_production_paper_observer import _pulse_observed_progress


class Store:
    def __init__(self):
        self.calls = []

    def state(self, **fields):
        self.calls.append(fields)


def test_progress_pulse_is_throttled_and_preserves_paper_zero():
    store = Store()
    when = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc).isoformat()
    with patch("bf_production_paper_observer.now_iso", return_value=when):
        assert _pulse_observed_progress(store, 100, monotonic_now=124.9) == 100
        assert store.calls == []
        assert _pulse_observed_progress(store, 100, monotonic_now=125) == 125
    assert store.calls == [{"heartbeat_at": when, "real_orders_sent": 0}]


def test_no_progress_means_no_new_heartbeat():
    store = Store()
    # A hung individual provider read never calls the progress hook.
    assert _pulse_observed_progress(store, 150, monotonic_now=151) == 150
    assert not store.calls
