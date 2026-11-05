"""A671 FAIL-FIRST, OFFHOST only: separate WAL readers can mix commits.

This test intentionally fails on the current product candidate. It must not be
merged or called GREEN until a single immutable per-tick source snapshot is
verified before publication and all consuming readers share it. The fixture
has no provider/client/host access and no trading authority.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from rc6_dynamic_universe.runtime import read_runtime
from rc6_shadow_runtime.stages import enrich_pipeline


def test_all_native_consumers_must_share_first_wal_commit(tmp_path: Path):
    as_of = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)
    instrument = ("AL30", "BONOS", "BYMA", "ARS", "A-24HS")
    path = tmp_path / "paper.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER)")
        db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
        db.execute("CREATE TABLE financial_instrument_catalog(ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,status TEXT,capability TEXT)")
        db.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?)",
                   (instrument[0], instrument[1], instrument[2], instrument[3],
                    instrument[4], "AVAILABLE", "READY_PAPER_SPOT"))
        db.execute("CREATE TABLE paper_positions(paper_id INTEGER PRIMARY KEY,symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,status TEXT)")
        db.execute("CREATE TABLE scalping_candidates(id INTEGER PRIMARY KEY,symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,evaluated_at TEXT,action TEXT,score REAL,reason TEXT,points INTEGER)")
        db.execute("CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,last REAL,trade_at TEXT,book_at TEXT,observed_at TEXT,bid REAL,ask REAL,bid_size REAL,ask_size REAL,last_kind TEXT)")

    at = as_of.isoformat()
    first = read_runtime(path, as_of=at, row_limit=5, query_budget_seconds=2)
    assert len(first["full_catalog"]) == 1
    assert first["safety"] == {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
                                "real_routes": "NOT_CALLED"}

    # A native writer COMMITs after the first SHADOW consumer has finished,
    # but with an event timestamp ten seconds BEFORE the shared as_of value.
    # A common read snapshot would be unable to see this later commit.
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO scalping_candidates(symbol,asset_class,market,currency,settlement,evaluated_at,action,score,reason,points) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (*instrument, (as_of - timedelta(seconds=10)).isoformat(),
                    "BUY", 0.9, "synthetic candidate", 3))

    second = enrich_pipeline(path, {"engines": {"SCALPING": {"telemetry": [
        {"identity": instrument, "pipeline": {"SIGNAL_READY": True},
         "entry_authority": False}]}}}, as_of=at)
    label = second["engines"]["SCALPING"]["telemetry"][0]["signal_shadow"]["status"]
    assert label != "OBSERVED_NATIVE_SIGNAL", "PIT_INTERLEAVING_CROSS_CONSUMER_READER_GAP"
