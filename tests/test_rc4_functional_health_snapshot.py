from __future__ import annotations

from pathlib import Path
import sqlite3

import rc4_functional_health_snapshot as health


NOW="2026-09-03T16:30:00+00:00"


def _db(path: Path):
    c=sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE observer_state(
      id INTEGER PRIMARY KEY,mode TEXT,process_state TEXT,session_state TEXT,
      ppi_auth TEXT,heartbeat_at TEXT,last_market_data_at TEXT,real_orders_sent INTEGER,
      http_allowed INTEGER,http_blocked INTEGER,detail TEXT);
    CREATE TABLE paper_positions(
      paper_id TEXT PRIMARY KEY,symbol TEXT,asset_class TEXT,currency TEXT,market TEXT,
      settlement TEXT,status TEXT,opened_at TEXT);
    CREATE TABLE paper_position_marks(
      paper_id TEXT PRIMARY KEY,mark_price TEXT,book_at TEXT,marked_at TEXT);
    CREATE TABLE paper_exit_intents(paper_id TEXT PRIMARY KEY,state TEXT);
    CREATE TABLE paper_supervisor_state(id INTEGER PRIMARY KEY,state TEXT,heartbeat_at TEXT,detail TEXT);
    CREATE TABLE paper_exit_reader_state(id INTEGER PRIMARY KEY,state TEXT,heartbeat_at TEXT,detail TEXT);
    CREATE TABLE api_health(component TEXT PRIMARY KEY,state TEXT,checked_at TEXT,last_success_at TEXT,detail TEXT);
    CREATE TABLE source_sync(source TEXT PRIMARY KEY,status TEXT,last_attempt_at TEXT,last_success_at TEXT,items INTEGER,detail TEXT);
    CREATE TABLE production_history(symbol TEXT,instrument_type TEXT,settlement TEXT,date_from TEXT,date_to TEXT,downloaded_at TEXT,row_count INTEGER,payload_json TEXT);
    """)
    c.execute("INSERT INTO observer_state VALUES(1,?,?,?,?,?,?,?,?,?,?)",(
        "PRODUCTION_PAPER","RUNNING","MARKET_OPEN","OK",
        "2026-09-03T16:29:30+00:00","2026-09-03T16:29:50+00:00",0,100,0,"ok"))
    c.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,?,?,?)",(
        "P1","GGAL","ACCIONES","ARS","BYMA","A-24HS","OPEN","2026-09-03T15:00:00+00:00"))
    c.execute("INSERT INTO paper_position_marks VALUES(?,?,?,?)",(
        "P1","7000","2026-09-03T16:29:40+00:00","2026-09-03T16:29:42+00:00"))
    c.execute("INSERT INTO paper_exit_intents VALUES(?,?)",("P1","WATCHING"))
    c.executemany("INSERT INTO paper_supervisor_state VALUES(?,?,?,?)",[(1,"RUNNING","2026-09-03T16:29:50+00:00","ok")])
    c.executemany("INSERT INTO paper_exit_reader_state VALUES(?,?,?,?)",[(1,"READY","2026-09-03T16:29:50+00:00","ok")])
    c.execute("INSERT INTO api_health VALUES(?,?,?,?,?)",("PPI","VERDE","2026-09-03T16:29:00+00:00","2026-09-03T16:29:00+00:00","ok"))
    c.execute("INSERT INTO source_sync VALUES(?,?,?,?,?,?)",("PPI_PRODUCTION_HISTORY","VERDE","2026-09-03T16:00:00+00:00","2026-09-03T16:00:00+00:00",100,"ok"))
    c.execute("INSERT INTO production_history VALUES(?,?,?,?,?,?,?,?)",("GGAL","ACCIONES","A-24HS","2026-01-01","2026-09-03","2026-09-03T16:00:00+00:00",100,"[]"))
    c.commit(); c.close()


def test_healthy_snapshot_is_read_only_and_ok(tmp_path: Path):
    path=tmp_path/"observer.db"; _db(path)
    before=path.read_bytes()
    result=health.collect(str(path),now=NOW)
    after=path.read_bytes()
    assert before == after
    assert result["state"] == "OK"
    assert result["observer"]["real_orders_sent"] == 0
    assert result["positions"]["open"] == 1
    assert result["positions"]["stale_marks"] == 0
    assert result["positions"]["without_exit_intent"] == 0


def test_real_order_nonzero_is_critical(tmp_path: Path):
    path=tmp_path/"observer.db"; _db(path)
    c=sqlite3.connect(path); c.execute("UPDATE observer_state SET real_orders_sent=1"); c.commit(); c.close()
    result=health.collect(str(path),now=NOW)
    assert result["state"] == "CRITICAL"
    assert "REAL_ORDERS_NONZERO" in result["critical"]


def test_open_without_exit_intent_is_critical(tmp_path: Path):
    path=tmp_path/"observer.db"; _db(path)
    c=sqlite3.connect(path); c.execute("DELETE FROM paper_exit_intents"); c.commit(); c.close()
    result=health.collect(str(path),now=NOW)
    assert any(x.startswith("OPEN_POSITIONS_WITHOUT_EXIT_INTENT=") for x in result["critical"])


def test_stale_mark_is_warning_not_fake_green(tmp_path: Path):
    path=tmp_path/"observer.db"; _db(path)
    c=sqlite3.connect(path)
    c.execute("UPDATE paper_position_marks SET book_at='2026-09-03T15:00:00+00:00'")
    c.commit(); c.close()
    result=health.collect(str(path),now=NOW)
    assert result["positions"]["stale_marks"] == 1
    assert "OPEN_POSITIONS_WITH_STALE_MARK=1" in result["warnings"]
    assert result["state"] == "WARN"
