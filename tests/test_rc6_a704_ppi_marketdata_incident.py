"""A704: OFFHOST, read-only RCA for the observer's 0/20 incident.

No broker, host, Docker, runtime, WAL or live SQLite database is accessed.
"""
import importlib.util
import json
import sqlite3
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "scripts/rc6_market_watch_readonly.py"
spec = importlib.util.spec_from_file_location("rc6_market_watch_readonly_a704", SOURCE)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)

START = "2026-11-05T13:34:00+00:00"
END = "2026-11-05T13:35:00+00:00"
CHECKED = "2026-11-05T13:35:10+00:00"


class A704PpiMarketDataIncident(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          CREATE TABLE api_health(component TEXT PRIMARY KEY, state TEXT,
            detail TEXT, checked_at TEXT, last_success_at TEXT, source TEXT);
          CREATE TABLE paper_notification_outbox(id INTEGER PRIMARY KEY,
            event_key TEXT, kind TEXT, created_at TEXT, state TEXT,
            attempts INTEGER, sent_at TEXT, body TEXT, last_error TEXT);
          CREATE TABLE universe_cycle_metrics(id INTEGER PRIMARY KEY,
            started_at TEXT, finished_at TEXT, selected_count INTEGER,
            successful_count INTEGER, failed_count INTEGER, detail TEXT);
          CREATE TABLE paper_events(id INTEGER PRIMARY KEY, event_at TEXT,
            event_type TEXT, detail TEXT);
          CREATE TABLE paper_exit_reader_state(id INTEGER PRIMARY KEY,
            heartbeat_at TEXT, state TEXT, detail TEXT);
          CREATE TABLE paper_supervisor_state(id INTEGER PRIMARY KEY,
            heartbeat_at TEXT, state TEXT, detail TEXT);
        """)
        self.db.executemany(
            "INSERT INTO api_health VALUES(?,?,?,?,?,?)",
            [("PPI_PRODUCTION_MARKETDATA", "ROJO", "0/20 private-info", CHECKED,
              "2026-11-04T19:59:57.981112+00:00", "PPI Producción"),
             ("PPI_PRODUCTION_AUTH", "VERDE", "credential SECRET", CHECKED,
              CHECKED, "PPI Producción")])
        self.db.execute("INSERT INTO universe_cycle_metrics VALUES(1,?,?,?,?,?,?)",
                        (START, END, 20, 0, 1, "all 20 sensitive tickers"))
        self.db.execute("INSERT INTO paper_notification_outbox VALUES(1,?,?,?,?,?,?,?,?)",
                        ("operational-health:PPI_PRODUCTION_MARKETDATA:BAD:abcdefgh12345678",
                         "OPERATIONAL_HEALTH_RED", CHECKED, "SENT", 1,
                         "2026-11-05T13:36:05+00:00",
                         "Bearer private-telegram-token", "credential SECRET"))
        self.db.execute("INSERT INTO paper_exit_reader_state VALUES(1,?,?,?)",
                        (CHECKED, "READY", "broker secret"))
        self.db.execute("INSERT INTO paper_supervisor_state VALUES(1,?,?,?)",
                        (CHECKED, "RUNNING", "sensitive position"))
        for _ in range(16):
            self.add_event("DATA_REJECTED", "GGAL: TRADE_STALE; API_KEY=SECRET")
        for _ in range(3):
            self.add_event("DATA_REJECTED", "AL30: BOOK_STALE; cookie=SECRET")
        self.add_event("DATA_ERROR", "YPFD: HTTP_403; bearer-secret")
        self.db.commit()

    def add_event(self, kind, detail, at="2026-11-05T13:34:30+00:00"):
        self.db.execute("INSERT INTO paper_events(event_at,event_type,detail) VALUES(?,?,?)",
                        (at, kind, detail))

    def read(self):
        self.db.execute("PRAGMA query_only=ON")
        tables = {row[0] for row in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        before = self.db.total_changes
        value = monitor._ppi_marketdata_incident(self.db, tables)
        self.assertEqual(before, self.db.total_changes)
        self.assertEqual(value["provider_requests"], 0)
        self.assertTrue(value["read_only"])
        return value

    def test_0_of_20_has_actual_cycle_window_and_no_identity_or_secret(self):
        value = self.read()
        self.assertEqual(value["marketdata"]["state"], "ROJO")
        self.assertEqual(value["marketdata"]["checked_at_utc"], CHECKED)
        self.assertEqual(value["auth"]["state"], "VERDE")
        self.assertEqual(value["cycle"]["status"], "MATCHED")
        self.assertEqual(value["cycle"]["selected_count"], 20)
        self.assertEqual(value["cycle"]["successful_count"], 0)
        self.assertEqual(value["rejections"]["status"], "RECORDED")
        self.assertEqual(value["rejections"]["event_counts"], {
            "TRADE_STALE": 16, "BOOK_STALE": 3, "DATA_ERROR": 1})
        self.assertEqual(value["telegram"]["status"], "RECORDED")
        self.assertEqual(value["telegram"]["delivery_delay_seconds"], 55)
        self.assertEqual(value["exit"]["reader"]["state"], "READY")
        self.assertEqual(value["exit"]["supervisor"]["state"], "RUNNING")
        serialized = json.dumps(value)
        for secret in ("GGAL", "AL30", "YPFD", "API_KEY", "cookie",
                       "SECRET", "Bearer", "private", "abcdefgh12345678"):
            self.assertNotIn(secret, serialized)
        self.assertNotIn("healthy", serialized.lower())

    def test_mismatched_metrics_clock_never_attributes_previous_cycle(self):
        self.db.execute("UPDATE universe_cycle_metrics SET started_at=?,finished_at=?",
                        ("2026-11-05T11:34:00+00:00",
                         "2026-11-05T11:35:00+00:00"))
        value = self.read()
        self.assertEqual(value["cycle"]["status"], "CLOCK_MISMATCH")
        self.assertEqual(value["rejections"]["status"], "NO_VERIFICADO")
        self.assertEqual(value["rejections"]["event_counts"], {})

    def test_missing_tables_and_partial_schema_fail_closed(self):
        missing = sqlite3.connect(":memory:")
        self.addCleanup(missing.close)
        missing.row_factory = sqlite3.Row
        missing.execute("PRAGMA query_only=ON")
        value = monitor._ppi_marketdata_incident(missing, set())
        self.assertEqual(value["marketdata"]["status"], "NO_VERIFICADO")
        self.assertEqual(value["cycle"]["status"], "NO_VERIFICADO")
        self.assertEqual(value["telegram"]["status"], "NO_VERIFICADO")
        self.assertEqual(value["exit"]["reader"]["status"], "NO_VERIFICADO")
        self.assertEqual(value["rejections"]["status"], "NO_VERIFICADO")
        self.db.execute("DROP TABLE api_health")
        self.db.execute("CREATE TABLE api_health(component TEXT)")
        value = self.read()
        self.assertEqual(value["marketdata"]["status"], "NO_VERIFICADO")
        self.assertEqual(value["auth"]["status"], "NO_VERIFICADO")

    def test_event_histogram_limit_does_not_claim_complete_sample(self):
        for _ in range(200):
            self.add_event("DATA_REJECTED", "GGAL: LAST_IS_NOT_A_TRADE token SECRET")
        value = self.read()
        self.assertEqual(value["rejections"]["status"], "PARTIAL_SAMPLE")
        self.assertTrue(value["rejections"]["possibly_truncated"])
        self.assertLessEqual(value["rejections"]["events_scanned"], 160)
        self.assertNotIn("GGAL", json.dumps(value))

    def test_invalid_values_are_rejected_without_echo(self):
        self.db.execute("UPDATE api_health SET state=?,checked_at=? WHERE component=?",
                        ("SECRET=abc", "bad time", "PPI_PRODUCTION_MARKETDATA"))
        self.db.execute("UPDATE paper_exit_reader_state SET state=?",
                        ("Bearer private",))
        self.db.execute("UPDATE paper_notification_outbox SET state=?,sent_at=?",
                        ("token secret", "not-a-timestamp"))
        value = self.read()
        self.assertEqual(value["marketdata"]["state"], "NO_VERIFICADO")
        self.assertEqual(value["marketdata"]["checked_at_utc"], None)
        self.assertEqual(value["cycle"]["status"], "NO_VERIFICADO")
        self.assertEqual(value["telegram"]["state"], "NO_VERIFICADO")
        self.assertEqual(value["exit"]["reader"]["state"], "NO_VERIFICADO")
        self.assertNotIn("secret", json.dumps(value).lower())


if __name__ == "__main__":
    unittest.main()
