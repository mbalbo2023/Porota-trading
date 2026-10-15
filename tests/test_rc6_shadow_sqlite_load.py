"""A production-scale PAPER read stays bounded under an unrelated WAL writer."""
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from rc6_dynamic_universe.runtime import read_runtime


def test_full_day_under_concurrent_wal_updates():
    at = datetime(2026, 10, 15, 16, 55, tzinfo=timezone.utc)
    earlier = "2026-10-14T16:00:00+00:00"
    today = "2026-10-15T16:00:00+00:00"
    with tempfile.TemporaryDirectory() as folder:
        db = Path(folder) / "observer.db"
        with sqlite3.connect(db) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
                CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
                INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
                CREATE TABLE financial_instrument_catalog(ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,status TEXT,capability TEXT);
                CREATE TABLE paper_positions(paper_id INTEGER PRIMARY KEY,symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,status TEXT);
                CREATE TABLE ppi_intraday_points(symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,event_at TEXT,first_received_at TEXT,last_verified_at TEXT,price REAL,volume REAL,source TEXT);
                CREATE TABLE ppi_intraday_contract_state(symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,state TEXT,last_source_at TEXT,checked_at TEXT);
                CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,last REAL,trade_at TEXT,book_at TEXT,observed_at TEXT,bid REAL,ask REAL,bid_size REAL,ask_size REAL,last_kind TEXT);
                CREATE TABLE unrelated_heartbeat(id INTEGER PRIMARY KEY,seq INTEGER);
                INSERT INTO unrelated_heartbeat VALUES(1,0);
                CREATE INDEX idx_shadow_catalog_order ON financial_instrument_catalog(instrument_type,market,currency,ticker,settlement,status,capability);
                CREATE INDEX idx_shadow_intraday_event_jd ON ppi_intraday_points(julianday(event_at));
                CREATE INDEX idx_shadow_snapshot_observed_jd ON market_snapshots(julianday(observed_at));
            """)
            c.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?)",
                ((f"S{i:05d}", "ACCIONES", "BYMA", "ARS", "A-24HS",
                  "AVAILABLE", "READY_PAPER_SPOT") for i in range(14592)))
            c.executemany("INSERT INTO ppi_intraday_points VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ((f"S{i%2404:05d}", "ACCIONES", "BYMA", "ARS", "A-24HS",
                  today, today, today, 100, 1, "PPI_MARKETDATA_INTRADAY")
                 for i in range(11896)))
            c.executemany("INSERT INTO ppi_intraday_contract_state VALUES(?,?,?,?,?,?,?,?)",
                ((f"S{i:05d}", "ACCIONES", "BYMA", "ARS", "A-24HS",
                  "CONFIRMED_INTERVAL_VOLUME", today, today) for i in range(2404)))
            c.executemany("INSERT INTO market_snapshots VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ((f"S{i%2404:05d}", "ACCIONES", "BYMA", "ARS", "A-24HS",
                  100, earlier, earlier, earlier, 99, 101, 10, 10, "TRADE")
                 for i in range(233389)))
            c.executemany("INSERT INTO market_snapshots VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ((f"S{i%2404:05d}", "ACCIONES", "BYMA", "ARS", "A-24HS",
                  100, today, today, today, 99, 101, 10, 10, "TRADE")
                 for i in range(2244)))
            # A late historical arrival must not become a current observation.
            c.execute("INSERT INTO market_snapshots VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("S00000", "ACCIONES", "BYMA", "ARS", "A-24HS",
                 999, earlier, earlier, earlier, 99, 101, 10, 10, "TRADE"))
        stop = threading.Event()

        def writer():
            with sqlite3.connect(db, timeout=1) as c:
                while not stop.is_set():
                    c.execute("UPDATE unrelated_heartbeat SET seq=seq+1 WHERE id=1")
                    c.commit()
                    time.sleep(.002)

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            diagnostics = {}
            result = read_runtime(db, as_of=at, row_limit=20000,
                                  query_budget_seconds=2, diagnostics=diagnostics)
        finally:
            stop.set()
            thread.join(timeout=2)
        assert len(result["full_catalog"]) == 14592
        assert sum(o["endpoint"] == "intraday" for o in result["observations"]) == 11896
        assert sum(o["endpoint"] == "current" for o in result["observations"]) == 2244
        assert all(o["fields"]["price"] != 999 for o in result["observations"])
        assert result["source_database_effect"] == "READ_ONLY"
        assert result["safety"]["real_orders_sent"] == 0
        assert diagnostics["sql_remaining_ms"] > 0
        assert diagnostics["transform_elapsed_ms"] < 2000
        assert "idx_shadow_intraday_event_jd" in str(diagnostics["plans"]["INTRADAY_POINTS"])
        assert "idx_shadow_snapshot_observed_jd" in str(diagnostics["plans"]["MARKET_SNAPSHOTS"])
