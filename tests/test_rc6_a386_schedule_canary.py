"""Regression checks for A386 independent scheduled-event evidence."""

import importlib.util
import pathlib
import unittest
from datetime import datetime, timezone


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "rc6_a386_schedule_canary.py"
WORKFLOW = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows" / "rc6-a386-schedule-canary-readonly.yml"
SPEC = importlib.util.spec_from_file_location("rc6_a386_schedule_canary", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def context(event="schedule", expr=module.EXPECTED_CRON, ref="refs/heads/main", attempt="1"):
    return {
        "GITHUB_EVENT_NAME": event,
        "GITHUB_EVENT_SCHEDULE": expr,
        "GITHUB_REF": ref,
        "GITHUB_SHA": "a" * 40,
        "GITHUB_REPOSITORY": "mbalbo2023/Porota-trading",
        "GITHUB_RUN_ATTEMPT": attempt,
        "GITHUB_RUN_ID": "41512345678",
    }


class ScheduleCanaryTest(unittest.TestCase):
    NOW = datetime(2026, 11, 2, 19, 23, tzinfo=timezone.utc)

    def test_genuine_first_attempt_schedule_is_only_positive(self):
        receipt = module.derive_receipt(context(), self.NOW)
        self.assertEqual(receipt["classification"], "NATIVE_SCHEDULE_EVENT_SEEN")
        self.assertTrue(receipt["native_schedule_event"])
        self.assertEqual(receipt["market_watch_scheduler_recovered"], "NO_VERIFICADO")
        self.assertEqual(receipt["paper_runtime"], "NO_VERIFICADO")
        self.assertEqual(receipt["real_orders_sent"], "NO_VERIFICADO")

    def test_manual_push_and_pr_never_prove_schedule(self):
        for event in ("workflow_dispatch", "push", "pull_request"):
            with self.subTest(event=event):
                receipt = module.derive_receipt(context(event=event), self.NOW)
                self.assertEqual(receipt["classification"], "DIAGNOSTIC_EVENT_ONLY")
                self.assertFalse(receipt["native_schedule_event"])
                self.assertIsNone(receipt["event_schedule"])

    def test_schedule_ref_cron_and_attempt_fail_closed(self):
        cases = (
            {"expr": "23 * * * 1-5"},
            {"ref": "refs/heads/branch"},
            {"attempt": "2"},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                receipt = module.derive_receipt(context(**kwargs), self.NOW)
                self.assertEqual(receipt["classification"], "INVALID_NATIVE_SCHEDULE_CONTEXT")
                self.assertFalse(receipt["native_schedule_event"])

    def test_delayed_run_is_not_recast_as_absent(self):
        late = datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc)
        receipt = module.derive_receipt(context(), late)
        self.assertTrue(receipt["native_schedule_event"])
        self.assertFalse(receipt["observed_utc_in_nominal_window"])

    def test_reject_bad_repo_id_and_metadata(self):
        for key, value in (
            ("GITHUB_REPOSITORY", "wrong/repo"),
            ("GITHUB_SHA", "invalid"),
            ("GITHUB_RUN_ATTEMPT", "0"),
            ("GITHUB_EVENT_NAME", "invented"),
            ("GITHUB_RUN_ID", "1\nTOKEN"),
        ):
            with self.subTest(key=key):
                ctx = context()
                ctx[key] = value
                with self.assertRaises(ValueError):
                    module.derive_receipt(ctx, self.NOW)

    def test_workflow_is_isolated_and_safe(self):
        data = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("'23,53 13-21 * * 1-5'", data)
        self.assertIn("permissions:\n  contents: read", data)
        self.assertIn("workflow_dispatch:", data)
        self.assertIn("pull_request:", data)
        self.assertIn("upload-artifact@v4", data)
        for forbidden in ("secrets.", "sudo", "ssh ", "curl ", "sqlite", "PPI_WATCH", "DO_HOST", "github.rest", "repository_dispatch"):
            self.assertNotIn(forbidden, data)


if __name__ == "__main__":
    unittest.main()
