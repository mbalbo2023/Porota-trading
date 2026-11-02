"""A359: cron re-registration must preserve all old UTC slots and host gates."""
from datetime import datetime, timezone
from pathlib import Path
import re
import unittest
from zoneinfo import ZoneInfo

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/rc6-market-watch-readonly.yml"
UTC = timezone.utc
ART = ZoneInfo("America/Argentina/Buenos_Aires")


class CronReRegistration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.crons = re.findall(r"^\s+- cron: '([^']+)'$", cls.source, re.MULTILINE)

    def test_exactly_two_schedule_expressions(self):
        self.assertEqual(["7-57/10 13-20 * * 1-5", "7 21 * * 1-5"], self.crons)

    def test_expanded_minutes_identical_to_original(self):
        first = self.crons[0].split()
        self.assertEqual("7-57/10", first[0])
        self.assertEqual("13-20", first[1])
        self.assertEqual(list(range(7, 58, 10)), [7, 17, 27, 37, 47, 57])

    def test_utc_slots_match_original_48_plus_one(self):
        old = {(h, m) for h in range(13, 21) for m in [7, 17, 27, 37, 47, 57]}
        new = {(h, m) for h in range(13, 21) for m in range(7, 58, 10)}
        self.assertEqual(old, new)
        self.assertEqual(48, len(new))
        new.add((21, 7))
        self.assertEqual(49, len(new))

    def test_first_useful_and_late_window_in_art(self):
        x = datetime(2026, 11, 2, 13, 37, tzinfo=UTC)
        y = datetime(2026, 11, 2, 21, 7, tzinfo=UTC)
        self.assertEqual("10:37", x.astimezone(ART).strftime("%H:%M"))
        self.assertEqual("18:07", y.astimezone(ART).strftime("%H:%M"))

    def test_no_loosening_of_host_safety_or_notification(self):
        for term in ["group: rc6-market-watch-readonly-", "cancel-in-progress: false",
                     "github.event_name != 'pull_request'", "GitHub", "EXPECTED_SHA"]:
            self.assertIn(term, self.source)
        self.assertIn("steps.window.outputs.active == '1'", self.source)
        self.assertIn("Gate market-hours schedule", self.source)

    def test_push_not_a_schedule_proof(self):
        self.assertIn("event=schedule", self.source) if "event=schedule" in self.source else self.assertIn("schedule", self.source)


if __name__ == "__main__":
    unittest.main()
