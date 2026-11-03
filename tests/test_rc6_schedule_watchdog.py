"""A358 deterministic tests; no GitHub network and no broker calls."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rc6_schedule_watchdog as a358


def at(hour, minute):
    return datetime(2026, 11, 2, hour + 3, minute, tzinfo=timezone.utc)


def run(hour, minute, **kw):
    value = dict(id=hour * 100 + minute, event="schedule", run_attempt=1, head_branch="main",
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

    def test_real_monday_late_20_12_does_not_erase_46_mature_slots(self):
        """Actual #41543540643 was a genuine but off-session event."""
        late = dict(id=41543540643, event="schedule", run_attempt=1,
                    head_branch="main", created_at="2026-11-02T23:12:06Z")
        now = datetime(2026, 11, 3, 2, 13, tzinfo=timezone.utc)
        result = a358.classify([late], now, workflow_state="active")
        self.assertEqual("P1_SCHEDULE_SILENT", result["status"])
        self.assertEqual("TWO_MATURE_SLOTS_WITHOUT_RUN", result["reason"])
        self.assertEqual("MATURE_RUN_COUNT_LOWER_BOUND", result["detection_basis"])
        self.assertEqual(46, result["mature_expected_slots"])
        self.assertEqual(1, result["genuine_runs_today"])
        self.assertEqual(0, result["genuine_in_session"])
        self.assertEqual(1, result["off_session_runs"])
        self.assertEqual(45, result["missing_mature_run_lower_bound"])
        self.assertEqual(0, result["overdue_slots_after_last_run"])

    def test_two_healthy_in_session_runs_do_not_create_false_p1(self):
        result = a358.classify([run(10, 37), run(10, 47)], at(11, 4))
        self.assertEqual("RECENT_SCHEDULE_OBSERVED", result["status"])
        self.assertEqual(2, result["mature_expected_slots"])
        self.assertEqual(2, result["genuine_in_session"])
        self.assertEqual(0, result["missing_mature_run_lower_bound"])
        self.assertEqual(0, result["off_session_runs"])

    def test_two_missed_mature_slots_cannot_be_reset_by_late_run(self):
        result = a358.classify([run(10, 37), run(10, 47),
                                 run(20, 12)], datetime(2026, 11, 3, 2, 13,
                                                       tzinfo=timezone.utc))
        self.assertEqual("P1_SCHEDULE_SILENT", result["status"])
        self.assertEqual(46 - 3, result["missing_mature_run_lower_bound"])
        self.assertEqual(1, result["off_session_runs"])

    def test_all_genuine_runs_outside_business_window_still_p1(self):
        # Native schedules can fire before the 10:37 useful window and long
        # after 18:07. They remain real events but not evidence of 46 slots.
        samples = [run(10, 7), run(10, 17), run(10, 27), run(20, 12)]
        now = datetime(2026, 11, 3, 2, 13, tzinfo=timezone.utc)
        result = a358.classify(samples, now)
        self.assertEqual("P1_SCHEDULE_SILENT", result["status"])
        self.assertEqual("TWO_MATURE_SLOTS_WITHOUT_RUN", result["reason"])
        self.assertEqual("MATURE_RUN_COUNT_LOWER_BOUND", result["detection_basis"])
        self.assertEqual(0, result["genuine_in_session"])
        self.assertEqual(4, result["off_session_runs"])
        self.assertEqual(42, result["missing_mature_run_lower_bound"])

    def test_same_run_id_does_not_double_count_and_conflict_fails_closed(self):
        first = run(10, 37, id=11001)
        duplicate = dict(first)
        result = a358.classify([first, duplicate], at(11, 15))
        self.assertEqual(1, result["genuine_runs_today"])
        self.assertEqual(2, result["missing_mature_run_lower_bound"])
        self.assertEqual("P1_SCHEDULE_SILENT", result["status"])

        conflicting = run(10, 47, id=11001)
        result = a358.classify([first, conflicting], at(11, 15))
        self.assertEqual("NO_VERIFICADO", result["status"])
        self.assertEqual("GENUINE_RUN_ID_CONFLICT", result["reason"])

    def test_genuine_run_missing_id_fails_closed(self):
        candidate = run(10, 37)
        candidate.pop("id")
        result = a358.classify([candidate], at(11, 15))
        self.assertEqual("NO_VERIFICADO", result["status"])
        self.assertEqual("GENUINE_RUN_ID_MISSING_OR_INVALID", result["reason"])

    def test_api_first_page_completeness_is_explicit(self):
        now = datetime(2026, 11, 3, 2, 13, tzinfo=timezone.utc)
        old = run(10, 37, created_at="2026-10-30T13:37:00Z")
        fresh = run(20, 12, created_at="2026-11-02T23:12:06Z")
        self.assertTrue(a358.api_page_covers_art_day([fresh, old], now))
        full_day = [dict(fresh, id=i + 1) for i in range(100)]
        self.assertFalse(a358.api_page_covers_art_day(full_day, now))
        self.assertTrue(a358.api_page_covers_art_day(full_day[:-1] + [old], now))
        self.assertFalse(a358.api_page_covers_art_day([old, fresh], now))
        self.assertFalse(a358.api_page_covers_art_day([dict(fresh, created_at="bad")], now))

        inconclusive = a358.classify([fresh], now, api_day_covered=False)
        self.assertEqual("NO_VERIFICADO", inconclusive["status"])
        self.assertEqual("SCHEDULE_API_DAY_INCOMPLETE", inconclusive["reason"])
        self.assertEqual(45, inconclusive["missing_mature_run_lower_bound"])

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

    def test_dedupe_many_small_pages_recognizes_existing_marker(self):
        """The current #732 volume overflows one 100-comment response."""
        receipt = {"date_art": "2026-11-02", "status": "P1_SCHEDULE_SILENT"}
        marker = "<!--RC6-A358:2026-11-02:P1_SCHEDULE_SILENT-->"
        pages = []

        def get(url, _token):
            pages.append(url)
            self.assertIn("since=2026-11-02T03:00:00Z", url)
            self.assertIn("per_page=5", url)
            page = int(url.rsplit("page=", 1)[1])
            if page < 19:
                return [{"body": "sanitized fixture " + str(i)} for i in range(5)]
            return [{"body": marker}]

        result = a358.dedupe_issue("P1_SCHEDULE_SILENT", receipt, "mock",
                                   get=get, post=lambda *_: self.fail("duplicate"))
        self.assertEqual("ALREADY_REPORTED", result)
        self.assertEqual(19, len(pages))

    def test_dedupe_posts_once_only_after_complete_scan(self):
        receipt = {"date_art": "2026-11-02", "status": "P1_SCHEDULE_SILENT",
                   "observed_at_utc": "2026-11-02T23:11:54Z",
                   "workflow_state": "active", "genuine_runs_today": 0,
                   "overdue_slots_after_last_run": 2}
        pages, posted = [], []

        def get(url, _token):
            pages.append(int(url.rsplit("page=", 1)[1]))
            return ([{"body": "fixture"}] * 5) if len(pages) < 3 else []

        def post(url, data, _token):
            posted.append((url, data))
            return 201

        result = a358.dedupe_issue("P1_SCHEDULE_SILENT", receipt, "mock",
                                   get=get, post=post)
        self.assertEqual("ISSUE_COMMENT_CREATED", result)
        self.assertEqual([1, 2, 3], pages)
        self.assertEqual(1, len(posted))
        self.assertIn("<!--RC6-A358:2026-11-02:P1_SCHEDULE_SILENT-->",
                      posted[0][1]["body"])

    def test_no_notification_for_non_p1(self):
        self.assertEqual("NOT_REQUIRED", a358.dedupe_issue("NO_VERIFICADO", {}, "mock"))

    def test_dedupe_fails_if_unbounded(self):
        receipt = {"date_art": "2026-11-02"}
        with self.assertRaisesRegex(ValueError, "A358_UNBOUNDED_DEDUPE"):
            a358.dedupe_issue("P1_SCHEDULE_SILENT", receipt, "mock",
                              get=lambda *_: [{"body": "x"}] * a358.DEDUPE_PAGE_SIZE,
                              post=lambda *_: self.fail("must not post"))

    def test_dedupe_incomplete_api_scan_never_posts(self):
        calls = []

        def get(_url, _token):
            calls.append(1)
            if len(calls) == 2:
                raise ValueError("A358_API_RESPONSE_TOO_LARGE")
            return [{"body": "fixture"}] * a358.DEDUPE_PAGE_SIZE

        with self.assertRaisesRegex(ValueError, "A358_API_RESPONSE_TOO_LARGE"):
            a358.dedupe_issue("P1_SCHEDULE_SILENT", {"date_art": "2026-11-02"}, "mock",
                              get=get, post=lambda *_: self.fail("must not post"))

    def test_dedupe_malformed_page_and_art_date_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "A358_DATE_ART_INVALID"):
            a358.dedupe_issue("P1_SCHEDULE_SILENT", {"date_art": "2026-11-02T00:00:00Z"},
                              "mock", get=lambda *_: self.fail("bad date not queried"))
        with self.assertRaisesRegex(ValueError, "A358_COMMENTS_SHAPE_INVALID"):
            a358.dedupe_issue("P1_SCHEDULE_SILENT", {"date_art": "2026-11-02"},
                              "mock", get=lambda *_: [{"metadata": "body missing"}],
                              post=lambda *_: self.fail("must not post"))

    def test_notification_error_class_is_sanitized_preserves_p1_reason(self):
        p1 = {"schema": "rc6.a358.schedule-watch.v1", "status": "P1_SCHEDULE_SILENT",
              "reason": "TWO_MATURE_SLOTS_WITHOUT_RUN", "date_art": "2026-11-02"}
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "receipt.json")
            with patch.dict(os.environ, {"GH_TOKEN": "mock_token_123456"}):
                with patch.object(a358, "_get", side_effect=[
                    {"state": "active"}, {"workflow_runs": []}]):
                    with patch.object(a358, "classify", return_value=p1):
                        with patch.object(a358, "dedupe_issue", side_effect=
                                          a358.A358SafeError("A358_API_RESPONSE_TOO_LARGE")):
                            code = a358.main(["--output", target, "--notify"])
            self.assertEqual(3, code)
            result = json.loads(Path(target).read_text(encoding="utf-8"))
            self.assertEqual("P1_SCHEDULE_SILENT", result["status"])
            self.assertEqual("TWO_MATURE_SLOTS_WITHOUT_RUN", result["reason"])
            self.assertEqual("NO_VERIFICADO", result["notification"])
            self.assertEqual("A358_API_RESPONSE_TOO_LARGE",
                             result["notification_error_class"])
            self.assertNotIn("mock_token_123456", json.dumps(result))

    def test_unknown_exception_text_is_never_emitted(self):
        self.assertEqual("A358_VALUE_ERROR",
                         a358.safe_error_class(ValueError("private body/token/url")))

    def test_unknown_workflow_state_does_not_become_false_disabled(self):
        result = a358.classify([], at(10, 40), workflow_state="NO_VERIFICADO")
        self.assertEqual("NO_VERIFICADO", result["status"])

    def test_post_url_pinned(self):
        with self.assertRaisesRegex(ValueError, "A358_INVALID_POST_TARGET"):
            a358._post("https://example.org/", {}, "mock")


if __name__ == "__main__":
    unittest.main()


