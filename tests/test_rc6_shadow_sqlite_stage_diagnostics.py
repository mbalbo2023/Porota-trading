"""Real SQLite read budgets preserve fail-closed stage attribution."""
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from rc6_dynamic_universe import runtime


AT = datetime(2026, 10, 15, 14, 30, tzinfo=timezone.utc)


class ReadStages(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "paper.db"
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE observer_state(id INTEGER PRIMARY KEY, mode TEXT, real_orders_sent INTEGER)")
            c.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
            c.execute("CREATE TABLE financial_instrument_catalog(ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT, settlement TEXT, status TEXT, capability TEXT)")
            c.execute("CREATE TABLE paper_positions(paper_id INTEGER PRIMARY KEY, symbol TEXT, asset_class TEXT, market TEXT, currency TEXT, settlement TEXT, status TEXT)")
            c.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?)", [
                (f"S{i:05d}", "ACCIONES", "BYMA", "ARS", "A-24HS", "AVAILABLE", "READY_PAPER_SPOT")
                for i in range(3000)])

    def test_complete_read_records_rows_and_never_writes(self):
        trace = {}
        with sqlite3.connect(self.db) as witness:
            before = witness.execute("PRAGMA data_version").fetchone()[0]
            data = runtime.read_runtime(self.db, as_of=AT, row_limit=3000,
                                        query_budget_seconds=2, diagnostics=trace)
            after = witness.execute("PRAGMA data_version").fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(len(data["full_catalog"]), 3000)
        self.assertEqual(len(data["catalog"]), 3000)
        self.assertEqual(data["source_database_effect"], "READ_ONLY")
        self.assertEqual([stage["stage"] for stage in trace["stages"][:2]],
                         ["CATALOG_FULL", "OPEN_POSITIONS"])
        self.assertEqual(trace["stages"][0]["rows"], 3000)
        self.assertTrue(all("sql" not in stage and "path" not in stage
                            for stage in trace["stages"]))
        self.assertIn("SCAN financial_instrument_catalog", " ".join(trace["plans"]["CATALOG_FULL"]))
        self.assertGreater(trace["sql_remaining_ms"], 0)

    def test_wal_writer_does_not_mix_observation_generations(self):
        with sqlite3.connect(self.db) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY, symbol TEXT, asset_class TEXT, market TEXT, currency TEXT, settlement TEXT, last REAL, trade_at TEXT, book_at TEXT, observed_at TEXT, bid REAL, ask REAL, bid_size REAL, ask_size REAL, last_kind TEXT)")
            c.execute("INSERT INTO market_snapshots VALUES(1,'S00000','ACCIONES','BYMA','ARS','A-24HS',100,?,?,?,?,?,?,?,?)",
                      (AT.isoformat(), AT.isoformat(), AT.isoformat(), 99, 101, 10, 10, 'TRADE'))
        original_connect = sqlite3.connect
        db = self.db
        changed = []

        class ConcurrentRead(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if "FROM paper_positions" in sql and not changed:
                    changed.append(True)
                    with original_connect(db) as writer:
                        writer.execute("UPDATE market_snapshots SET last=200 WHERE id=1")
                return super().execute(sql, parameters)

        def connect(*args, **kwargs):
            if kwargs.get("uri"):
                kwargs["factory"] = ConcurrentRead
            return original_connect(*args, **kwargs)

        with patch.object(runtime.sqlite3, "connect", side_effect=connect):
            data = runtime.read_runtime(self.db, as_of=AT, row_limit=3000,
                                        query_budget_seconds=2)
        self.assertTrue(changed)
        self.assertEqual(data["observations"][0]["fields"]["price"], 100)
        with original_connect(self.db) as c:
            self.assertEqual(c.execute("SELECT last FROM market_snapshots").fetchone()[0], 200)

    def test_slow_timestamp_conversion_cannot_interrupt_later_sql(self):
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE ppi_intraday_points(symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,event_at TEXT,first_received_at TEXT,last_verified_at TEXT,price REAL,volume REAL,source TEXT)")
            c.execute("CREATE TABLE ppi_intraday_contract_state(symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,state TEXT,last_source_at TEXT,checked_at TEXT)")
            c.executemany("INSERT INTO ppi_intraday_contract_state VALUES(?,?,?,?,?,?,?,?)", [
                (f"S{i:05d}", "ACCIONES", "BYMA", "ARS", "A-24HS",
                 "CONFIRMED_INTERVAL_VOLUME", AT.isoformat(), AT.isoformat())
                for i in range(30)])
            # These contracts must have consumers to exercise conversion:
            # unrelated/empty intraday rows now correctly bypass their clocks.
            c.executemany("INSERT INTO ppi_intraday_points VALUES(?,?,?,?,?,?,?,?,?,?,?)", [
                (f"S{i:05d}", "ACCIONES", "BYMA", "ARS", "A-24HS",
                 AT.isoformat(), AT.isoformat(), AT.isoformat(), 100, 1, "FIXTURE")
                for i in range(30)])
        actual_stamp = runtime.stamp

        def slow_stamp(value):
            time.sleep(.005)
            return actual_stamp(value)

        trace = {}
        with patch.object(runtime, "stamp", side_effect=slow_stamp):
            data = runtime.read_runtime(self.db, as_of=AT, row_limit=3000,
                                        query_budget_seconds=.2, diagnostics=trace)
        self.assertEqual(len(data["observations"]), 30)
        self.assertGreater(trace["transform_elapsed_ms"], 200)
        self.assertGreater(trace["sql_remaining_ms"], 0)
        self.assertEqual(trace["stages"][-1]["stage"], "INTRADAY_POINTS")

    def test_shared_deadline_interruption_identifies_stage(self):
        ticks = iter([100.0, 100.0] + [103.0] * 10000)
        trace = {}
        with patch.object(runtime, "monotonic", side_effect=lambda: next(ticks)):
            with self.assertRaises(sqlite3.OperationalError) as caught:
                runtime.read_runtime(self.db, as_of=AT, row_limit=3000,
                                     query_budget_seconds=2, diagnostics=trace)
        exc = caught.exception
        self.assertEqual(exc.sqlite_errorname, "SQLITE_INTERRUPT")
        self.assertEqual(exc.rc6_stage, "CATALOG_FULL")
        self.assertTrue(exc.rc6_deadline_expired)
        self.assertEqual(trace["stages"][-1]["stage"], "CATALOG_FULL")
        self.assertTrue(trace["stages"][-1]["interrupted_by_deadline"])


if __name__ == "__main__":
    unittest.main()
