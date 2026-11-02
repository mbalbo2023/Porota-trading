"""A360 regression: an UNKNOWN market calendar cannot mask missing GitHub cron.

Only synthetic GitHub API responses, never host/DB/broker/side effects.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rc6_market_watch_readonly as watch


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class ScheduleDueClockTests(unittest.TestCase):
    def test_not_due_before_first_cron_plus_grace(self):
        self.assertFalse(watch.schedule_watch_due_by_clock(utc("2026-11-02T13:33:00Z")))
        self.assertFalse(watch.schedule_watch_due_by_clock(utc("2026-11-02T13:48:59Z")))
        self.assertTrue(watch.schedule_watch_due_by_clock(utc("2026-11-02T13:49:00Z")))

    def test_due_when_real_open_but_audited_calendar_unknown(self):
        now=utc("2026-11-02T15:27:42Z")
        context={"generated_at": now.isoformat(),
                 "market_clock_window": True,
                 "market_open_observed": True,
                 "expected_session":{"state":"UNKNOWN","reason":"AUDITED_CALENDAR_UNAVAILABLE",
                                     "operational_day":None},
                 "findings":[]}
        watch.add_schedule_health(context,"mbalbo2023/Porota-trading",
                                  get=lambda _:{"workflow_runs":[]})
        self.assertEqual("SCHEDULE_SILENT",context["schedule_health"]["state"])
        self.assertIs(context["schedule_health"]["silence"],True)
        self.assertIn("SCHEDULE_SILENCE_TWO_INTERVALS",
                      [x["code"] for x in context["findings"]])
        self.assertEqual("P1",context["severity"])

    def test_calendar_closed_does_not_veto_scheduled_technical_watch(self):
        now=utc("2026-11-02T15:27:42Z")
        context={"generated_at":now.isoformat(),
                 "expected_session":{"state":"CLOSED","operational_day":False},
                 "findings":[]}
        watch.add_schedule_health(context,"mbalbo2023/Porota-trading",
                                  get=lambda _:{"workflow_runs":[]})
        self.assertEqual("SCHEDULE_SILENT",context["schedule_health"]["state"])

    def test_weekend_and_post_gate_are_not_expected(self):
        for value in ("2026-11-01T15:27:42Z","2026-11-02T21:16:00Z",
                      "2026-11-03T02:00:00Z"):
            self.assertFalse(watch.schedule_watch_due_by_clock(utc(value)),value)

    def test_old_gate_after_com19031_review_window(self):
        self.assertTrue(watch.schedule_watch_due_by_clock(utc("2027-01-04T19:00:00Z")))
        self.assertFalse(watch.schedule_watch_due_by_clock(utc("2027-01-04T20:16:00Z")))

    def test_no_invalid_clock_promoted_to_open(self):
        self.assertFalse(watch.schedule_watch_due_by_clock(None))


if __name__ == "__main__":
    unittest.main()
