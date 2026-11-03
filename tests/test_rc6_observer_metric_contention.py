"""Real SQLite writer contention must not turn optional metrics into a crash."""
import json
import sqlite3
import time

import pytest

from bf_production_paper_observer import _write_scanner_metrics


class Store:
    def __init__(self, path):
        self.path = path
        self.connections = []

    def connect(self):
        c = sqlite3.connect(self.path, timeout=20)
        self.connections.append(c)
        return c


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "metrics.db")
    with sqlite3.connect(result.path) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE symbol_cycle_metrics(id INTEGER PRIMARY KEY, cycle_started_at, symbol, latency_ms, state, detail)")
        c.execute("CREATE TABLE universe_cycle_metrics(id INTEGER PRIMARY KEY, started_at, finished_at, eligible_total, selected_count, successful_count, failed_count, duration_seconds, cursor_before, cursor_after, recommended_limit, detail)")
    return result


def values(stage):
    return (("2026-10-16T19:39:00Z", "PRIVATE_SYMBOL", 12, "VERDE", "PRIVATE_DETAIL")
            if stage == "SYMBOL" else
            ("2026-10-16T19:39:00Z", "2026-10-16T19:39:30Z", 20, 20, 19, 1, 30, 0, 20, 20, "PRIVATE_DETAIL"))


def assert_closed(store):
    for c in store.connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            c.execute("SELECT 1")


@pytest.mark.parametrize("stage,table", [("SYMBOL", "symbol_cycle_metrics"), ("UNIVERSE", "universe_cycle_metrics")])
def test_real_lock_defers_metric_then_next_stage_can_persist(store, stage, table, capsys):
    blocker = sqlite3.connect(store.path)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        # The original finally-path INSERT raises under this same lock.
        with sqlite3.connect(store.path, timeout=.01) as old:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                old.execute("INSERT INTO symbol_cycle_metrics(symbol) VALUES('old')")
        start = time.monotonic()
        assert _write_scanner_metrics(store, stage, values(stage)) is False
        assert time.monotonic() - start < 2, "optional metrics inherited the trading 20s lock wait"
        event = json.loads(capsys.readouterr().err)
        assert event["event"] == "SCANNER_METRICS_DEFERRED"
        assert event["stage"] == stage and event["persisted"] is False
        assert event["sqlite_errorcode"] & 0xff == sqlite3.SQLITE_BUSY
        assert "PRIVATE" not in json.dumps(event)
        assert_closed(store)
    finally:
        blocker.rollback()
        blocker.close()
    with sqlite3.connect(store.path) as c:
        assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert _write_scanner_metrics(store, stage, values(stage)) is True
    with sqlite3.connect(store.path) as c:
        assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
    assert_closed(store)


def test_unknown_sql_failure_propagates_and_closes_connection(store, capsys):
    with sqlite3.connect(store.path) as c:
        c.execute("DROP TABLE symbol_cycle_metrics")
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        _write_scanner_metrics(store, "SYMBOL", values("SYMBOL"))
    assert capsys.readouterr().err == ""
    assert_closed(store)


def test_cycle_metric_and_retention_are_atomic_on_integrity_error(store):
    with sqlite3.connect(store.path) as c:
        c.execute("INSERT INTO symbol_cycle_metrics(id) VALUES(1)")
        c.execute("CREATE TRIGGER no_retention BEFORE DELETE ON symbol_cycle_metrics BEGIN SELECT RAISE(ABORT, 'retention rejected'); END")
        c.executemany("INSERT INTO symbol_cycle_metrics(id) VALUES(?)", ((n,) for n in range(2, 10002)))
    with pytest.raises(sqlite3.IntegrityError, match="retention rejected"):
        _write_scanner_metrics(store, "UNIVERSE", values("UNIVERSE"))
    with sqlite3.connect(store.path) as c:
        assert c.execute("SELECT count(*) FROM universe_cycle_metrics").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM symbol_cycle_metrics").fetchone()[0] == 10001
    assert_closed(store)


def test_metrics_scope_cannot_accept_a_trading_stage(store):
    with pytest.raises(KeyError):
        _write_scanner_metrics(store, "PAPER_FILL", ())
    assert not store.connections


@pytest.mark.parametrize("code", [sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_BUSY | (2 << 8)])
def test_locked_and_extended_busy_use_primary_code_only(code, capsys):
    error = sqlite3.OperationalError("PRIVATE arbitrary lock text")
    error.sqlite_errorcode = code
    error.sqlite_errorname = "SQLITE_LOCKED" if code == sqlite3.SQLITE_LOCKED else "SQLITE_BUSY_SNAPSHOT"

    class LockedStore:
        def connect(self):
            raise error

    assert _write_scanner_metrics(LockedStore(), "SYMBOL", values("SYMBOL")) is False
    event = json.loads(capsys.readouterr().err)
    assert event["sqlite_errorcode"] == code
    assert "PRIVATE" not in json.dumps(event)


def test_lock_word_without_sqlite_busy_code_is_not_swallowed(capsys):
    class UnknownStore:
        def connect(self):
            raise sqlite3.OperationalError("locked but no authenticated SQLite code")

    with pytest.raises(sqlite3.OperationalError):
        _write_scanner_metrics(UnknownStore(), "SYMBOL", values("SYMBOL"))
    assert capsys.readouterr().err == ""
