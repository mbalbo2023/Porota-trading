"""A685 OFFHOST fail-closed slot claim: no SDK, provider, host or live GitHub.

The race regression assumes the *existing* GitHub job concurrency mutex;
an artifact is never asserted to be an atomic lock by itself.
"""

from datetime import date, datetime, timezone
from pathlib import Path
import json
import tempfile
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from scripts.rc6_a685_slot_claim import (
    ART, CONTROL_REF, REPOSITORY, WORKFLOW_PATH, WORKFLOW_REF,
    Claim, Hold, _art_window, _write_claim, no_prior_attempt, prepare_claim,
)


DAY = date(2026, 11, 5)
NOW = datetime(2026, 11, 5, 14, 0, tzinfo=timezone.utc)


def run(run_id, number, *, event="schedule", status="completed",
        timestamp="2026-11-05T13:45:00Z"):
    return {"id": run_id, "run_number": number, "event": event,
            "created_at": timestamp, "head_branch": "main",
            "path": WORKFLOW_PATH + "@main", "status": status}


def artifact(name, *, expired=False):
    return {"name": name, "expired": expired}


def dataset(runs, artifacts=None):
    artifacts = artifacts or {}
    seen = []

    def get_json(url):
        seen.append(url)
        if "/artifacts?" in url:
            selected = int(url.split("/runs/")[1].split("/")[0])
            items = artifacts.get(selected, [])
            return {"total_count": len(items), "artifacts": items}
        return {"total_count": len(runs), "workflow_runs": runs}

    return get_json, seen


class SlotClaimTests(unittest.TestCase):
    def setUp(self):
        self.claim = Claim(DAY, "1045", 100, 20, "a" * 40)

    def assertHold(self, reason, fn):
        with self.assertRaises(Hold) as raised:
            fn()
        self.assertEqual(str(raised.exception), reason)

    def test_new_unique_slot_passes_with_self_visible(self):
        fetch, seen = dataset([run(100, 20)])
        no_prior_attempt(self.claim, fetch)
        self.assertEqual(len(seen), 1)
        self.assertIn("created=2026-11-05T03%3A00%3A00Z..2026-11-06T02%3A59%3A59Z", seen[0])

    def test_same_slot_schedule_then_manual_denied(self):
        runs = [run(100, 20, event="workflow_dispatch"), run(99, 19)]
        fetch, _ = dataset(runs, {99: [artifact("rc6-a670-claim-2026-11-05-1045")]})
        self.assertHold("A670_CLAIM_DUPLICATE_ART_SLOT", lambda: no_prior_attempt(self.claim, fetch))

    def test_different_slot_prior_capture_is_not_same_day_global_block(self):
        runs = [run(99, 19), run(100, 20)]
        fetch, _ = dataset(runs, {99: [artifact("rc6-a670-claim-2026-11-05-1120")]})
        no_prior_attempt(self.claim, fetch)

    def test_same_slot_newer_run_entered_mutex_first_denied(self):
        runs = [run(101, 21), run(100, 20)]
        fetch, _ = dataset(runs, {101: [artifact("rc6-a670-claim-2026-11-05-1045")]})
        self.assertHold("A670_CLAIM_DUPLICATE_ART_SLOT", lambda: no_prior_attempt(self.claim, fetch))

    def test_older_eligible_run_missing_claim_is_ambiguous_not_retry(self):
        runs = [run(100, 20), run(99, 19)]
        fetch, _ = dataset(runs)
        self.assertHold("A670_CLAIM_PRIOR_ATTEMPT_AMBIGUOUS", lambda: no_prior_attempt(self.claim, fetch))

    def test_claim_expired_blocks(self):
        runs = [run(100, 20), run(99, 19)]
        fetch, _ = dataset(runs, {99: [artifact("rc6-a670-claim-2026-11-05-1120", expired=True)]})
        self.assertHold("A670_CLAIM_ARTIFACT_LIST_AMBIGUOUS", lambda: no_prior_attempt(self.claim, fetch))

    def test_artifact_pagination_ambiguity_blocks(self):
        runs = [run(100, 20), run(99, 19)]

        def fetch(url):
            if "/artifacts?" in url:
                return {"total_count": 2, "artifacts": [artifact("rc6-a670-claim-2026-11-05-1120")]}
            return {"total_count": len(runs), "workflow_runs": runs}

        self.assertHold("A670_CLAIM_ARTIFACT_LIST_AMBIGUOUS", lambda: no_prior_attempt(self.claim, fetch))

    def test_run_pagination_ambiguity_blocks(self):
        fetch, _ = dataset([run(100, 20)])

        def truncated(url):
            result = fetch(url)
            result["total_count"] = 2
            return result

        self.assertHold("A670_CLAIM_RUN_HISTORY_AMBIGUOUS", lambda: no_prior_attempt(self.claim, truncated))

    def test_provider_api_429_or_transport_failure_is_not_a_go(self):
        # Real URL opener errors must be normalized to a static HOLD, not a
        # partially trusted empty-run response.
        from scripts.rc6_a685_slot_claim import _github_json
        self.assertHold("A670_CLAIM_ACTIONS_TOKEN_ABSENT", lambda: _github_json("unused", ""))
        with patch("scripts.rc6_a685_slot_claim.urlopen", side_effect=HTTPError(
                   "https://api.github.com", 429, "rate limited", None, None)):
            self.assertHold("A670_CLAIM_ACTIONS_API_UNAVAILABLE",
                            lambda: _github_json("https://api.github.com", "synthetic-test-token"))

    def test_rerun_must_hold_before_any_api(self):
        env = self._env()
        env["GITHUB_RUN_ATTEMPT"] = "2"
        self.assertHold("A670_CLAIM_RERUN_FORBIDDEN", lambda: prepare_claim(env, NOW))

    def test_noncanonical_workflow_and_wrong_sha_hold(self):
        env = self._env()
        env["GITHUB_WORKFLOW_REF"] = "other/repo/workflow@refs/heads/main"
        self.assertHold("A670_CLAIM_NONCANONICAL_CONTROL_PLANE", lambda: prepare_claim(env, NOW))
        env = self._env()
        env["GITHUB_SHA"] = "SPOOFED"
        self.assertHold("A670_CLAIM_SOURCE_SHA_INVALID", lambda: prepare_claim(env, NOW))

    def test_strict_art_date_clock_gate_and_midnight_window(self):
        env = self._env()
        env["RC6_A670_ART_DATE"] = "2026-11-04"
        self.assertHold("A670_CLAIM_ART_DATE_MISMATCH", lambda: prepare_claim(env, NOW))
        start, end = _art_window(DAY)
        self.assertEqual(start.isoformat(), "2026-11-05T03:00:00+00:00")
        self.assertEqual(end.isoformat(), "2026-11-06T02:59:59+00:00")

    def test_claim_is_private_sanitized_and_exclusive_create(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "claim.json"
            _write_claim(target, self.claim)
            data = json.loads(target.read_text())
            self.assertEqual(data["slot"], "1045")
            self.assertEqual(data["ppi_gets_by_claim"], 0)
            self.assertFalse(data["provider_payload_included"])
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                _write_claim(target, self.claim)

    def test_missing_or_untrusted_action_output_fails_closed(self):
        from scripts.rc6_a685_slot_claim import _append_claim_output
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "actions-output"
            self.assertHold("A670_CLAIM_OUTPUT_PATH_UNSAFE",
                            lambda: _append_claim_output(output, self.claim))
            output.write_text("")
            _append_claim_output(output, self.claim)
            self.assertEqual(output.read_text(), f"artifact_name={self.claim.name}\n")

    def test_serialized_race_claim_then_second_fails(self):
        # The GH job mutex protects these two phases. Claims do NOT lock.
        earlier = Claim(DAY, "1045", 99, 19, "a" * 40)
        fetch1, _ = dataset([run(99, 19)])
        no_prior_attempt(earlier, fetch1)
        fetch2, _ = dataset([run(100, 20), run(99, 19)],
                            {99: [artifact(earlier.name)]})
        self.assertHold("A670_CLAIM_DUPLICATE_ART_SLOT", lambda: no_prior_attempt(self.claim, fetch2))

    def test_prior_pr_event_without_host_job_does_not_claim(self):
        fetch, _ = dataset([run(100, 20), run(90, 10, event="pull_request")])
        no_prior_attempt(self.claim, fetch)

    def test_no_self_in_action_history_is_not_zero_get_authorization(self):
        fetch, _ = dataset([run(98, 18)])
        self.assertHold("A670_CLAIM_RUN_ID_NOT_VISIBLE",
                        lambda: no_prior_attempt(self.claim, fetch))

    def test_existing_workflow_has_mutex_and_upload_before_ssh(self):
        path = Path(".github/workflows/rc6-a669-ppi-current-book-20261105.yml")
        if not path.is_file():
            self.skipTest("Workflow static gate runs in complete GitHub checkout")
        source = path.read_text()
        mutex = source.index("group: rc6-unified-paper-deploy")
        guard = source.index("python3 -B scripts/rc6_a669_ppi_day_slot_guard.py")
        claim = source.index("python3 -B scripts/rc6_a685_slot_claim.py")
        upload = source.index("name: ${{ steps.a670_claim.outputs.artifact_name }}")
        host = source.index("Private RO host preflight")
        self.assertLess(mutex, guard)
        self.assertLess(guard, claim)
        self.assertLess(claim, upload)
        self.assertLess(upload, host)
        self.assertIn("actions: read", source)

    @staticmethod
    def _env():
        return {
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_REF": CONTROL_REF,
            "GITHUB_WORKFLOW_REF": WORKFLOW_REF,
            "GITHUB_EVENT_NAME": "schedule",
            "RC6_A669_SLOT": "1045",
            "RC6_A670_ART_DATE": DAY.isoformat(),
            "GITHUB_RUN_ID": "100",
            "GITHUB_RUN_NUMBER": "20",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": "a" * 40,
        }


if __name__ == "__main__":
    unittest.main()
