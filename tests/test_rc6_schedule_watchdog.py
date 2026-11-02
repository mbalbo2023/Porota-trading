"""A358 deterministic tests; no GitHub network and no broker calls."""
import sys
from datetime import datetime, timezone
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rc6_schedule_watchdog as a358


def at(hour, minute):
    return datetime(2026, 11, 2, hour + 3, minute, tzinfo=timezone.utc)


def run(hour, minute, **kw):
    value = dict(event="schedule", run_attempt=1, head_branch="main",
                 status="completed", created_at=at(hour, minute).isoformat())
    value.update(kw)
    return value


class ScheduleWatchdogTest(unittest.TestCase):
    def test_expected_10_37_and_18_07(self):
        slots = a358.slots(at(10, 40).astimezone(a358.ART).date())
        self.assertEqual("10:37", slots[0].astimezone(a358.ART).strftime("%H:%M"))
        self.assertEqual("18:07", slots[-1].astimezone(a358.ART).strftime("%H:%M"))

    def test_weekend_closed_is_explicit(self):
        d = datetime(2026, 11, 1, 16, tzinfo=timezone.utc)
        self.assertEqual("NOT_APPLICABLE_CLOSED", a358.classify([], d)["status"])

    def test_two_mature_slots_silent(self):
        self.assertEqual("P1_SCHEDULE_SILENT", a358.classify([], at(11, 4))["status"])

    def test_not_premature_before_two_mature(self):
        self.assertEqual("NO_VERIFICADO", a358.classify([], at(10, 52))["status"])

    def test_manual_push_retry_do_not_count(self):
        noise = [run(10, 37, event="workflow_dispatch"), run(10, 47, event="push"),
                 run(10, 57, run_attempt=2)]
        result = a358.classify(noise, at(11, 12))
        self.assertEqual(0, result["genuine_runs_today"])
        self.assertEqual("P1_SCHEDULE_SILENT", result["status"])

    def test_genuine_recent_first_attempts(self):
        result = a358.classify([run(10, 37), run(10, 47), run(10, 57)], at(11, 4))
        self.assertEqual("RECENT_SCHEDULE_OBSERVED", result["status"])

    def test_run_from_other_day_not_counted(self):
        older = run(10, 37, created_at="2026-10-30T13:37:00Z")
        self.assertEqual(0, a358.classify([older], at(11, 5))["genuine_runs_today"])

    def test_workflow_disabled_is_p1(self):
        result = a358.classify([], at(10, 39), workflow_state="disabled_manually")
        self.assertEqual("P1_WORKFLOW_DISABLED", result["status"])

    def test_no_false_causal_proof_from_partial_runs(self):
        result = a358.classify([run(10, 37)], at(11, 15))
        self.assertEqual("P1_SCHEDULE_SILENT", result["status"])
        self.assertEqual(1, result["genuine_runs_today"])

    def test_bad_datetime_fails_closed(self):
        self.assertIsNone(a358.parse_clock("2026-11-02T12:01:00"))
        with self.assertRaises(ValueError):
            a358.classify([], datetime(2026, 11, 2, 12))

    def test_dedupe_existing_marker(self):
        receipt = {"date_art": "2026-11-02", "status": "P1_SCHEDULE_SILENT"}
        marker = "<!--RC6-A358:2026-11-02:P1_SCHEDULE_SILENT-->"
        sent = []
        state = a358.dedupe_issue("P1_SCHEDULE_SILENT", receipt, "mock_token",
                get=lambda *_: [{"body": marker}], post=lambda *a: sent.append(a))
        self.assertEqual("ALREADY_REPORTED", state)
        self.assertEqual([], sent)

    def test_no_notification_for_non_p1(self):
        self.assertEqual("NOT_REQUIRED", a358.dedupe_issue("NO_VERIFICADO", {}, "mock"))

    def test_dedupe_fails_if_unbounded(self):
        receipt = {"date_art": "2026-11-02"}
        with self.assertRaisesRegex(ValueError, "A358_UNBOUNDED_DEDUPE"):
            a358.dedupe_issue("P1_SCHEDULE_SILENT", receipt, "mock",
                              get=lambda *_: [{"body": "x"}] * 100,
                              post=lambda *_: self.fail("must not post"))

    def test_unknown_workflow_state_does_not_become_false_disabled(self):
        result = a358.classify([], at(10, 40), workflow_state="NO_VERIFICADO")
        self.assertEqual("NO_VERIFICADO", result["status"])

    def test_post_url_pinned(self):
        with self.assertRaisesRegex(ValueError, "A358_INVALID_POST_TARGET"):
            a358._post("https://example.org/", {}, "mock")


if __name__ == "__main__":
    unittest.main()
