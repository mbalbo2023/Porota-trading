"""A671 OFFHOST WAL interleaving must be guarded across native consumers."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import pytest

from rc6_dynamic_universe.runtime import read_runtime
from rc6_audit_evidence.sqlite_snapshot import SnapshotError
from rc6_shadow_runtime.stages import enrich_pipeline
from rc6_shadow_runtime.source_reads import source_tick


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
    with pytest.raises(SnapshotError, match="SOURCE_SNAPSHOT_BUSY"):
        with source_tick(path) as capture:
            first = read_runtime(path, as_of=at, row_limit=5, query_budget_seconds=2)
            assert len(first["full_catalog"]) == 1
            assert first["safety"] == {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
                                        "real_routes": "NOT_CALLED"}
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO scalping_candidates(symbol,asset_class,market,currency,settlement,evaluated_at,action,score,reason,points) VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (*instrument, (as_of - timedelta(seconds=10)).isoformat(),
                            "BUY", 0.9, "synthetic candidate", 3))
            second = enrich_pipeline(path, {"engines": {"SCALPING": {"telemetry": [
                {"identity": instrument, "pipeline": {"SIGNAL_READY": True},
                 "entry_authority": False}]}}}, as_of=at)
            label = second["engines"]["SCALPING"]["telemetry"][0]["signal_shadow"]["status"]
            assert label != "OBSERVED_NATIVE_SIGNAL", "PIT_INTERLEAVING_CROSS_CONSUMER_READER_GAP"
    assert [r["consumer"] for r in capture.receipt["query_consumers"]] == ["runtime", "stages"]
    assert all(r["shared_primary_capture"] for r in capture.receipt["query_consumers"])
    assert capture.receipt["primary_captures"] == 1
    assert capture.receipt["source_unchanged"] is False


def test_unchanged_wal_reuses_verified_private_image_without_source_shm_writes(tmp_path: Path):
    path = tmp_path / "paper.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER)")
        db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
        db.execute("CREATE TABLE financial_instrument_catalog(ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,status TEXT,capability TEXT)")
        db.execute("CREATE TABLE paper_positions(paper_id INTEGER PRIMARY KEY,symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,status TEXT)")
    before = {suffix: (member.stat().st_size, member.stat().st_mtime_ns,
                       member.stat().st_ctime_ns)
              for suffix in ("", "-wal", "-shm")
              if (member := Path(str(path) + suffix)).exists()}
    with source_tick(path) as capture:
        output = read_runtime(path, as_of="2026-11-04T15:00:00+00:00",
                              row_limit=5, query_budget_seconds=2)
        assert output["safety"]["real_orders_sent"] == 0
        enrichment = enrich_pipeline(path, {"engines": {}}, as_of="2026-11-04T15:00:00+00:00")
        assert enrichment["engines"] == {}
    after = {suffix: (member.stat().st_size, member.stat().st_mtime_ns,
                      member.stat().st_ctime_ns)
             for suffix in ("", "-wal", "-shm")
             if (member := Path(str(path) + suffix)).exists()}
    assert before == after
    assert capture.receipt["primary_captures"] == 1
    assert capture.receipt["source_unchanged"] is True
    assert capture.receipt["private_image_unchanged"] is True
    assert capture.receipt["cleanup_complete"] is True
    assert [r["consumer"] for r in capture.receipt["query_consumers"]] == ["runtime", "stages"]
