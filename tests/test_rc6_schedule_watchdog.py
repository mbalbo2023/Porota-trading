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
        self.assertEqual(1, result["after_last_declared_slot_count"])
        self.assertEqual(7506, result["latest_creation_after_last_slot_lower_bound_seconds"])
        self.assertTrue(result["multi_hour_delayed_creation_proven"])
        self.assertEqual("NO_VERIFICADO", result["market_watch_runtime_recovered"])

    def test_both_actual_nov02_delayed_schedule_events_remain_p1(self):
        """Actual GitHub created_at, not a synthetic provider delay claim."""
        created = [
            dict(id=41543540643, event="schedule", run_attempt=1,
                 head_branch="main", created_at="2026-11-02T23:12:06Z"),
            dict(id=41568900395, event="schedule", run_attempt=1,
                 head_branch="main", created_at="2026-11-03T02:53:48Z"),
        ]
        result = a358.classify(created, datetime(2026, 11, 3, 2, 56,
                                                 tzinfo=timezone.utc),
                               workflow_state="active")
        self.assertEqual("P1_SCHEDULE_SILENT", result["status"])
        self.assertEqual("MATURE_RUN_COUNT_LOWER_BOUND", result["detection_basis"])
        self.assertEqual(46, result["mature_expected_slots"])
        self.assertEqual(2, result["genuine_runs_today"])
        self.assertEqual(0, result["genuine_in_session"])
        self.assertEqual(2, result["off_session_runs"])
        self.assertEqual(44, result["missing_mature_run_lower_bound"])
        self.assertEqual(2, result["after_last_declared_slot_count"])
        self.assertEqual(20808, result["latest_creation_after_last_slot_lower_bound_seconds"])
        self.assertTrue(result["multi_hour_delayed_creation_proven"])
        self.assertEqual("NO_VERIFICADO", result["market_watch_runtime_recovered"])

    def test_early_or_after_host_gate_runs_never_count_as_in_session_recovery(self):
        early = a358.classify([run(10, 7), run(10, 17)], at(10, 45))
        self.assertEqual("NO_VERIFICADO", early["status"])
        self.assertEqual("INSUFFICIENT_IN_SESSION_SCHEDULE_EVIDENCE", early["reason"])
        self.assertEqual(0, early["genuine_in_session"])
        self.assertEqual(2, early["off_session_runs"])

        # Market Watch stops at 18:15. The 18:07 cron's 12m grace does
        # NOT authorize host evidence from a genuine run created at 18:16.
        too_late_for_host = a358.classify([run(18, 16)], at(18, 17))
        self.assertEqual(0, too_late_for_host["genuine_in_session"])
        self.assertEqual(1, too_late_for_host["off_session_runs"])
        self.assertEqual(540, too_late_for_host[
            "latest_creation_after_last_slot_lower_bound_seconds"])
        self.assertFalse(too_late_for_host["multi_hour_delayed_creation_proven"])

    def test_host_time_gate_matches_existing_bounded_t1_extension(self):
        for day, expected in (
            ("2026-10-30", "17:15"),
            ("2026-11-02", "18:15"),
            ("2026-12-31", "18:15"),
            ("2027-01-01", "17:15"),
        ):
            with self.subTest(day=day):
                value = datetime.fromisoformat(day).date()
                self.assertEqual(expected, a358.host_window_close(value)
                                 .astimezone(a358.ART).strftime("%H:%M"))

    def test_two_healthy_in_session_runs_do_not_create_false_p1(self):
        result = a358.classify([run(10, 37), run(10, 47)], at(11, 4))
        self.assertEqual("RECENT_SCHEDULE_OBSERVED", result["status"])
        self.assertEqual(2, result["mature_expected_slots"])
        self.assertEqual(2, result["genuine_in_session"])
        self.assertEqual(0, result["missing_mature_run_lower_bound"])
        self.assertEqual(0, result["off_session_runs"])
        self.assertEqual(2, result["distinct_mature_slot_compatible_count"])
        self.assertTrue(result["latest_two_mature_slots_compatible"])

    def test_two_early_same_slot_runs_cannot_claim_schedule_cadence(self):
        # A reviewer found the pre-A499 false GREEN: at 10:55 ART, one
        # nominal slot is mature, but runs at 10:38/10:39 used to claim two.
        result = a358.classify([run(10, 38), run(10, 39)], at(10, 55))
        self.assertEqual("NO_VERIFICADO", result["status"])
        self.assertEqual(1, result["mature_expected_slots"])
        self.assertEqual(2, result["genuine_in_session"])
        self.assertEqual(1, result["distinct_mature_slot_compatible_count"])
        self.assertFalse(result["latest_two_mature_slots_compatible"])

    def test_two_burst_runs_near_second_slot_cannot_claim_schedule_cadence(self):
        # At 11:00 ART, 10:37/10:47 are both mature. These two one-minute-
        # apart runs are compatible with 10:47 only, NOT two distinct slots.
        for sample in ((run(10, 47), run(10, 48)),
                       (run(10, 46), run(10, 47))):
            with self.subTest(sample=sample):
                result = a358.classify(list(sample), at(11, 0))
                self.assertEqual("NO_VERIFICADO", result["status"])
                self.assertEqual("DISTINCT_MATURE_SLOT_EVIDENCE_INSUFFICIENT",
                                 result["reason"])
                self.assertEqual(2, result["mature_expected_slots"])
                self.assertEqual(1, result["distinct_mature_slot_compatible_count"])
                self.assertFalse(result["latest_two_mature_slots_compatible"])

    def test_two_separate_slot_compatible_arrivals_are_observed(self):
        result = a358.classify([run(10, 38), run(10, 48)], at(11, 4))
        self.assertEqual("RECENT_SCHEDULE_OBSERVED", result["status"])
        self.assertEqual(2, result["distinct_mature_slot_compatible_count"])
        self.assertTrue(result["latest_two_mature_slots_compatible"])

    def test_second_slot_does_not_accept_creation_after_half_cadence(self):
        late_first = run(10, 42, created_at=at(10, 42).replace(second=1).isoformat())
        result = a358.classify([late_first, run(10, 48)], at(11, 4))
        self.assertEqual("NO_VERIFICADO", result["status"])
        self.assertEqual(1, result["distinct_mature_slot_compatible_count"])

    def test_last_two_mature_slots_must_be_consecutive_and_both_compatible(self):
        # 10:57 is missing, though last pair of raw created_at events was
        # separated by exactly 20 min and would pass the old gaps[-1] gate.
        result = a358.classify([run(10, 38), run(10, 48), run(11, 8)], at(11, 24))
        self.assertEqual("NO_VERIFICADO", result["status"])
        self.assertEqual(4, result["mature_expected_slots"])
        self.assertEqual(3, result["distinct_mature_slot_compatible_count"])
        self.assertFalse(result["latest_two_mature_slots_compatible"])

    def test_late_prior_art_day_receipt_cannot_masquerade_as_today_cadence(self):
        # 02-Nov 23:53 ART is 03-Nov 02:53 UTC, but still belongs to
        # previous ART day: even paired with Tue 10:48 it must not be fresh.
        old = run(10, 38, id=41568900395,
                  created_at="2026-11-03T02:53:48Z")
        fresh = run(10, 48, id=42222333,
                    created_at="2026-11-03T13:48:00Z")
        now = datetime(2026, 11, 3, 14, 4, tzinfo=timezone.utc)
        result = a358.classify([old, fresh], now)
        self.assertEqual("NO_VERIFICADO", result["status"])
        self.assertEqual(1, result["genuine_runs_today"])
        self.assertEqual(1, result["distinct_mature_slot_compatible_count"])
        self.assertFalse(result["latest_two_mature_slots_compatible"])

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
                    {"state": "active"}, {"workflow_runs": []},
                    {"state": "active"}, {"workflow_runs": [], "total_count": 0}]):
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


def at5(hour, minute):
    return datetime(2026, 11, 5, hour + 3, minute, tzinfo=timezone.utc)


def a670_run(hour, minute, *, event="schedule", run_id=670001, **kw):
    item = dict(id=run_id, event=event, run_attempt=1, head_branch="main",
                workflow_id=a358.A670_WORKFLOW_ID,
                path=a358.A670_WORKFLOW_PATH,
                status="completed", created_at=at5(hour, minute).isoformat())
    item.update(kw)
    return item


class A670ScheduleDeliveryTest(unittest.TestCase):
    """Fake GitHub timestamps and artifacts; never exercises broker or host."""

    def classify(self, runs, hour, minute, **kwargs):
        return a358.classify_a670(runs, at5(hour, minute), **kwargs)

    def test_before_slot_and_before_deadline_are_not_false_missing(self):
        early = self.classify([], 10, 44)
        self.assertEqual("NOT_DUE", early["slots"][0]["status"])
        self.assertEqual("NO_P1_MATURE_MISSING", early["schedule_alert"])
        due = self.classify([], 11, 15)
        self.assertEqual("AWAITING_SCHEDULE", due["slots"][0]["status"])
        self.assertEqual("NO_P1_MATURE_MISSING", due["schedule_alert"])

    def test_genuine_no_trigger_after_exact_30min_deadline(self):
        result = self.classify([], 11, 16)
        self.assertEqual("NO_TRIGGER", result["slots"][0]["status"])
        self.assertEqual("P1_A670_SCHEDULE_DELIVERY", result["schedule_alert"])
        self.assertEqual("NOT_DUE", result["slots"][1]["status"])
        self.assertEqual(False, result["host_access"])
        self.assertEqual(0, result["provider_requests_by_watchdog"])

    def test_schedule_late_within_30_minutes_not_a_provider_receipt(self):
        result = self.classify([a670_run(10, 52)], 11, 16)
        self.assertEqual("LATE_WITHIN_30M", result["slots"][0]["status"])
        self.assertEqual(7 * 60, result["slots"][0]["delivery_lag_seconds"])
        self.assertFalse(result["slots"][0]["artifact_present"])

    def test_exact_artifact_presence_is_metadata_only(self):
        run_id = 670002
        artifact = {"name": f"rc6-a669-ppi-current-book-{run_id}", "expired": False}
        result = self.classify([a670_run(10, 55, run_id=run_id)], 11, 16,
                               artifacts_by_run={run_id: [artifact]})
        self.assertEqual("RECEIPT_PRESENT", result["slots"][0]["status"])
        self.assertTrue(result["slots"][0]["artifact_present"])
        self.assertFalse(result["slots"][0]["artifact_content_verified"])
        self.assertEqual("NO_VERIFICADO", result["paper_readiness"])

    def test_post_deadline_schedule_is_too_late_not_a_good_capture(self):
        run_id = 670003
        result = self.classify([a670_run(11, 18, run_id=run_id)], 11, 19,
                               artifacts_by_run={run_id: [
                                   {"name": f"rc6-a669-ppi-current-book-{run_id}",
                                    "expired": False}]})
        first = result["slots"][0]
        self.assertEqual("TOO_LATE", first["status"])
        self.assertEqual(33 * 60, first["delivery_lag_seconds"])
        self.assertTrue(first["artifact_present"])
        self.assertEqual("TIMESTAMP_COMPATIBLE_NOT_CAUSAL", first["cron_attribution"])
        self.assertEqual("P1_A670_SCHEDULE_DELIVERY", result["schedule_alert"])

    def test_manual_with_artifact_never_fills_missed_schedule(self):
        run_id = 42043657835
        manual = a670_run(11, 18, event="workflow_dispatch", run_id=run_id)
        result = self.classify([manual], 11, 19, artifacts_by_run={run_id: [
            {"name": f"rc6-a669-ppi-current-book-{run_id}", "expired": False}]})
        self.assertEqual(1, result["manual_runs_today"])
        self.assertEqual(1, result["manual_artifact_count"])
        self.assertEqual("NO_TRIGGER", result["slots"][0]["status"])
        self.assertEqual("P1_A670_SCHEDULE_DELIVERY", result["schedule_alert"])

    def test_pr_and_second_slot_do_not_rewrite_first_slot(self):
        pr = a670_run(10, 45, event="pull_request", run_id=670007,
                      head_branch="feature/sample")
        second = a670_run(11, 21, run_id=670008)
        result = self.classify([pr, second], 11, 51)
        self.assertEqual(1, result["pull_request_runs_today"])
        self.assertEqual("NO_TRIGGER", result["slots"][0]["status"])
        self.assertEqual("LATE_WITHIN_30M", result["slots"][1]["status"])

    def test_wrong_or_expired_artifact_cannot_claim_receipt(self):
        rid = 670009
        artifacts = [{"name": "other-family", "expired": False},
                     {"name": f"rc6-a669-ppi-current-book-{rid}", "expired": True}]
        result = self.classify([a670_run(10, 48, run_id=rid)], 11, 16,
                               artifacts_by_run={rid: artifacts})
        self.assertEqual("LATE_WITHIN_30M", result["slots"][0]["status"])
        self.assertFalse(result["slots"][0]["artifact_present"])

    def test_incomplete_api_must_not_certify_missing_or_provider_failure(self):
        result = self.classify([], 11, 16, api_day_covered=False)
        self.assertEqual("NO_VERIFICADO", result["slots"][0]["status"])
        self.assertEqual("NO_VERIFICADO", result["schedule_alert"])

    def test_duplicate_run_id_fails_closed(self):
        duplicate = [a670_run(10, 46), a670_run(10, 48)]
        result = self.classify(duplicate, 11, 16)
        self.assertEqual("NO_VERIFICADO", result["schedule_alert"])
        self.assertEqual("A670_RUN_ID_INVALID_OR_DUPLICATE", result["reason"])

    def test_fetch_uses_only_github_readonly_metadata(self):
        observed = at5(11, 19)
        calls = []

        def get(url, token):
            calls.append(url)
            self.assertEqual("fixture", token)
            if url.endswith("/workflows/407536423"):
                return {"state": "active"}
            if "/runs?created=" in url:
                return {"workflow_runs": [a670_run(11, 18, event="workflow_dispatch")],
                        "total_count": 1}
            if "/artifacts?per_page=" in url:
                return {"artifacts": [], "total_count": 0}
            self.fail("Unexpected endpoint")

        with patch.object(a358, "_get", side_effect=get):
            result = a358.fetch_a670_delivery("fixture", observed)
        self.assertEqual("P1_A670_SCHEDULE_DELIVERY", result["schedule_alert"])
        self.assertEqual(3, len(calls))
        self.assertTrue(all("api.github.com/repos/mbalbo2023/Porota-trading/actions/" in x
                            for x in calls))


if __name__ == "__main__":
    unittest.main()



