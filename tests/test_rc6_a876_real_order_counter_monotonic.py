"""A876: a PAPER heartbeat may never erase a recorded real-order warning.

All databases are local synthetic fixtures. These tests do not send orders or
establish the identity of any Droplet/production SQLite or WAL file.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest

from be_paper_engine import PaperStore
from bf_production_paper_observer import _pulse_observed_progress


REGRESSION = "PAPER_REAL_ORDERS_COUNTER_REGRESSION"
INVALID = "PAPER_REAL_ORDERS_COUNTER_INVALID"


def _store(tmp_path):
    return PaperStore(str(tmp_path / "observer.db"))


def _read_state(store):
    with closing(store.connect()) as connection:
        row = connection.execute(
            "SELECT real_orders_sent,heartbeat_at FROM observer_state WHERE id=1"
        ).fetchone()
        return tuple(row)


def _unsafe_positive_fixture(store):
    with closing(store.connect()) as connection:
        with connection:
            connection.execute(
                "UPDATE observer_state SET real_orders_sent=1,heartbeat_at='old' WHERE id=1"
            )


def test_real_observer_heartbeat_cannot_erase_positive_counter(tmp_path):
    """Removing the atomic guard must turn an observed 1 into a false 0."""
    store = _store(tmp_path)
    _unsafe_positive_fixture(store)

    with pytest.raises(RuntimeError, match=REGRESSION):
        _pulse_observed_progress(store, 0, monotonic_now=30)

    assert _read_state(store) == (1, "old")


def test_state_regression_rejects_entire_heartbeat_write(tmp_path):
    store = _store(tmp_path)
    _unsafe_positive_fixture(store)

    with pytest.raises(RuntimeError, match=REGRESSION):
        store.state(heartbeat_at="forged", real_orders_sent=0)

    assert _read_state(store) == (1, "old")


@pytest.mark.parametrize("invalid", [-1, False, 0.0, "0"])
def test_invalid_counter_types_and_negative_values_fail_closed(tmp_path, invalid):
    store = _store(tmp_path)

    with pytest.raises(ValueError, match=INVALID):
        store.state(heartbeat_at="forged", real_orders_sent=invalid)

    assert _read_state(store) == (0, None)


def test_zero_heartbeat_and_positive_alarm_are_preserved(tmp_path):
    store = _store(tmp_path)

    store.state(heartbeat_at="ok", real_orders_sent=0)
    assert _read_state(store) == (0, "ok")
    store.state(real_orders_sent=1)
    assert _read_state(store) == (1, "ok")


def test_guard_survives_a_new_store_instance(tmp_path):
    store = _store(tmp_path)
    _unsafe_positive_fixture(store)

    restarted = PaperStore(store.path)
    with pytest.raises(RuntimeError, match=REGRESSION):
        restarted.state(heartbeat_at="after-restart", real_orders_sent=0)

    assert _read_state(restarted) == (1, "old")


def test_concurrent_positive_and_heartbeat_never_end_at_zero(tmp_path):
    """Two real SQLite connections contend; either order must retain a 1."""
    store = _store(tmp_path)
    start = Barrier(2)

    def update(value):
        start.wait(timeout=3)
        try:
            store.state(real_orders_sent=value)
        except RuntimeError as exc:
            assert REGRESSION in str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        positive = pool.submit(update, 1)
        heartbeat = pool.submit(update, 0)
        positive.result(timeout=5)
        heartbeat.result(timeout=5)

    assert _read_state(store)[0] == 1
