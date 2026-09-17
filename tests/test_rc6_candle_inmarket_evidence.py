import json
import tempfile
import unittest
from pathlib import Path

from scripts.rc6_candle_inmarket_evidence import atomic_write_json, build_summary


class CandleInmarketEvidenceTests(unittest.TestCase):
    def test_green_requires_all_four_invariants(self):
        summary = build_summary(
            baseline_cursor=118740,
            current_cursor=118741,
            worker_state="RUNNING",
            heartbeat_at="2026-09-17T14:30:01+00:00",
            quick_check="ok",
            dirty_bars=0,
            observed_at="2026-09-17T14:30:02+00:00",
        )
        self.assertEqual(summary["status"], "GREEN")
        self.assertTrue(all(summary["checks"].values()))
        self.assertEqual(summary["data_mutation"], "NONE")

    def test_dirty_bars_prevents_green_even_when_other_checks_pass(self):
        summary = build_summary(
            baseline_cursor=118740,
            current_cursor=118741,
            worker_state="RUNNING",
            heartbeat_at="x",
            quick_check="ok",
            dirty_bars=1,
            observed_at="x",
        )
        self.assertEqual(summary["status"], "RED")
        self.assertFalse(summary["checks"]["dirty_bars_zero"])

    def test_atomic_file_has_complete_non_secret_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "status.json"
            payload = build_summary(
                baseline_cursor=1, current_cursor=2, worker_state="RUNNING",
                heartbeat_at="x", quick_check="ok", dirty_bars=0, observed_at="x",
            )
            atomic_write_json(target, payload)
            stored = json.loads(target.read_text())
            self.assertEqual(stored["status"], "GREEN")
            self.assertNotIn("token", json.dumps(stored).lower())


if __name__ == "__main__":
    unittest.main()
