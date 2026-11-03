"""COM19031 read-only candle-integrity coverage, virtual clocks only."""
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from zoneinfo import ZoneInfo

from scripts.rc6_source_cycle_dispatch import phase_status


ROOT = Path(__file__).resolve().parents[1]
ART = ZoneInfo("America/Argentina/Buenos_Aires")


class CandleLateTimerContracts(unittest.TestCase):
    def test_bounded_semantic_audits_cover_t1_late_hour_without_backfill(self):
        timer = (ROOT / "systemd/porota-candle-integrity-rc6.timer").read_text()
        service = (ROOT / "systemd/porota-candle-integrity-rc6.service").read_text()
        self.assertIn("17:05,35:00 America/Argentina/Buenos_Aires", timer)
        self.assertIn("18:05:00 America/Argentina/Buenos_Aires", timer)
        self.assertIn("23:45:00", timer)
        self.assertIn("Persistent=false", timer)
        self.assertNotIn("OnUnitActiveSec", timer)
        self.assertIn("--check-phase business_day", service)
        self.assertIn("rc6_candle_integrity.py", service)
        # No provider/client/ingest call in the timer/service.
        self.assertNotIn("rc6_history_", service)
        self.assertNotIn("rc6_iol_shadow_collect", service)

    def test_real_systemd_calendar_has_1735_and_1805_on_monday(self):
        if not shutil.which("systemd-analyze"):
            self.skipTest("systemd-analyze unavailable on offhost runner")
        timer = (ROOT / "systemd/porota-candle-integrity-rc6.timer").read_text()
        expressions = [line.split("=", 1)[1] for line in timer.splitlines()
                       if line.startswith("OnCalendar=")]
        events = []
        for expression in expressions:
            p = subprocess.run(["systemd-analyze", "calendar",
                                "--base-time=2026-11-01 00:00 UTC",
                                "--iterations=60", expression],
                               capture_output=True, text=True, check=True)
            events.extend(datetime.fromisoformat(s + "+00:00")
                          for s in re.findall(
                              r"(?m)^\s+(?:Next elapse|Iteration #\d+): \w{3} "
                              r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC$", p.stdout))
        monday = {x.astimezone(ART).strftime("%H:%M") for x in events
                  if x.astimezone(ART).date().isoformat() == "2026-11-02"}
        self.assertIn("17:05", monday)
        self.assertIn("17:35", monday)
        self.assertIn("18:05", monday)
        self.assertIn("23:45", monday)
        self.assertNotIn("17:15", monday)
        self.assertNotIn("18:15", monday)

    def test_business_day_execcondition_blocks_holiday_and_closed_weekend(self):
        monday = datetime(2026, 11, 2, 17, 35, tzinfo=ART)
        self.assertTrue(phase_status(monday, "business_day")[0])
        self.assertTrue(phase_status(monday.replace(hour=18, minute=5), "business_day")[0])
        self.assertFalse(phase_status(datetime(2026, 11, 9, 17, 35, tzinfo=ART),
                                      "business_day")[0])
        self.assertFalse(phase_status(datetime(2026, 11, 10, 17, 35, tzinfo=ART),
                                      "business_day")[0])
        self.assertFalse(phase_status(datetime(2026, 11, 1, 17, 35, tzinfo=ART),
                                      "business_day")[0])


if __name__ == "__main__":
    unittest.main()
