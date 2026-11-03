"""A contended PAPER heartbeat must recover without hiding sustained failure."""
import sqlite3

import pytest

from be_paper_engine import PaperStore
from cf_intraday_scalping import _heartbeat, init_schema


AT = "2026-10-14T21:00:00+00:00"


def test_scalping_heartbeat_survives_writer_lock_and_recovers(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    init_schema(store)
    with store.connect() as connection:
        connection.execute("""INSERT OR REPLACE INTO observer_state
            (id,mode,process_state,session_state,ppi_auth,real_orders_sent)
            VALUES(1,'PRODUCTION_PAPER','WAITING_MARKET','MARKET_CLOSED','OK',0)""")

    locker = sqlite3.connect(store.path, timeout=1)
    try:
        locker.execute("BEGIN IMMEDIATE")
        assert _heartbeat(store, at=AT, state="WAITING_MARKET", cursor=0) is False
        with store.connect() as connection:
            assert connection.execute(
                "SELECT heartbeat_at FROM intraday_scalping_worker_state WHERE id=1"
            ).fetchone() is None
    finally:
        locker.rollback()
        locker.close()

    assert _heartbeat(store, at=AT, state="WAITING_MARKET", cursor=0) is True
    with store.connect() as connection:
        row = connection.execute("""SELECT state,real_orders_sent
            FROM intraday_scalping_worker_state WHERE id=1""").fetchone()
    assert tuple(row) == ("WAITING_MARKET", 0)


def test_scalping_heartbeat_does_not_hide_non_contention_sqlite_error(tmp_path):
    class MissingSchema:
        path = str(tmp_path / "empty.db")

    sqlite3.connect(MissingSchema.path).close()
    with pytest.raises(RuntimeError, match="PAPER_SQLITE_WAL_REQUIRED"):
        _heartbeat(MissingSchema(), at=AT, state="WAITING_MARKET", cursor=0)
