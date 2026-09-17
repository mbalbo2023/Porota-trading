import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.rc6_candle_inmarket_evidence import (
    atomic_write_json, build_summary, collect_inputs, is_fresh,
)


class CandleInmarketEvidenceTests(unittest.TestCase):
    def summary(self, **overrides):
        values = dict(
            baseline_cursor=118740, current_cursor=118741, worker_state="RUNNING",
            heartbeat_at="2026-09-17T14:30:01+00:00", quick_check="ok",
            dirty_due_bars=0, dirty_open_bars=1, invalid_dirty_timestamp_count=0,
            observed_at="2026-09-17T14:30:02+00:00",
        )
        values.update(overrides)
        return build_summary(**values)

    def test_green_requires_cursor_running_integrity_and_no_overdue_dirty_bars(self):
        summary = self.summary()
        self.assertEqual(summary["status"], "GREEN")
        self.assertTrue(summary["checks"]["dirty_due_bars_zero"])
        self.assertEqual(summary["dirty_open_bars"], 1)

    def test_overdue_dirty_bar_prevents_green(self):
        summary = self.summary(dirty_due_bars=1)
        self.assertEqual(summary["status"], "RED")
        self.assertFalse(summary["checks"]["dirty_due_bars_zero"])

    def test_freshness_is_not_latched(self):
        summary = self.summary()
        now = datetime.fromisoformat(summary["observed_at"]) + timedelta(seconds=901)
        self.assertFalse(is_fresh(summary, now=now))
        self.assertTrue(is_fresh(summary, now=datetime.fromisoformat(summary["observed_at"])))

    def test_collect_uses_real_candle_dirty_schema_and_excludes_open_bar(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            c = sqlite3.connect(db)
            c.executescript("""
                CREATE TABLE candle_worker_state(
                  id INTEGER PRIMARY KEY, cursor INTEGER, heartbeat_at TEXT, state TEXT, detail TEXT);
                CREATE TABLE candle_dirty(series_id TEXT, bar_start TEXT, bar_end TEXT);
                INSERT INTO candle_worker_state VALUES(1,118741,'2026-09-17T14:30:00+00:00','RUNNING','ok');
                INSERT INTO candle_dirty VALUES('x','2026-09-17T14:29:00+00:00','2026-09-17T14:30:00+00:00');
                INSERT INTO candle_dirty VALUES('x','2026-09-17T14:30:00+00:00','2026-09-17T14:31:00+00:00');
            """)
            c.commit(); c.close()
            collected = collect_inputs(db_path=str(db), observed_at="2026-09-17T14:30:30+00:00")
            self.assertEqual(collected["dirty_due_bars"], 1)
            self.assertEqual(collected["dirty_open_bars"], 1)

    def test_atomic_file_has_complete_non_secret_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "status.json"
            atomic_write_json(target, self.summary())
            stored = json.loads(target.read_text())
            self.assertEqual(stored["status"], "GREEN")
            self.assertNotIn("token", json.dumps(stored).lower())


if __name__ == "__main__":
    unittest.main()
