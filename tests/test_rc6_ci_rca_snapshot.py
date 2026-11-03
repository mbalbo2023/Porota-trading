"""Offline Actions failure replay: UNKNOWN cause and no operational GO."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_ci_rca_snapshot as snapshot
from scripts import rc6_rca_learning as rca


SHA = "a" * 40


def evidence(**changes):
    statuses = {phase: "success" for phase in snapshot.PHASES}
    statuses.update(changes)
    return {"schema_version": 1, "run_id": 41208586626, "source_sha": SHA,
            "observed_at_utc": "2026-10-31T14:22:03Z", "outcomes": statuses}


class TestCIPhaseRCASnapshot(unittest.TestCase):
    def test_two_failed_phases_create_distinct_unknown_fingerprints_no_go(self):
        receipt = snapshot.snapshot(evidence(early_replay="failure",
                                             governed_suite="failure"))
        self.assertEqual(receipt["phase_failures"], 2)
        self.assertEqual(receipt["rca"]["distinct_signatures"], 2)
        self.assertEqual(receipt["coverage"], "FULL")
        self.assertEqual(receipt["rca"]["confirmed_rca_count"], 0)
        self.assertTrue(all(group["classification"] == "UNKNOWN" and
                            group["dimensions"]["error_code"] == "UNKNOWN"
                            for group in receipt["rca"]["groups"]))
        self.assertFalse(receipt["automatic_go"] or receipt["auto_merge"] or
                         receipt["auto_deploy"] or receipt["auto_trade"])

    def test_missing_skipped_cancelled_outcomes_never_invent_causes(self):
        receipt = snapshot.snapshot(evidence(early_replay="UNOBSERVED",
                                             governed_suite="skipped",
                                             native_proof="cancelled"))
        self.assertEqual(receipt["phase_failures"], 0)
        self.assertEqual(receipt["coverage"], "PARTIAL_NO_VERIFICADO")
        self.assertEqual(receipt["rca"]["distinct_signatures"], 0)
        self.assertFalse(receipt["root_cause_verified"])

    def test_invalid_statuses_logs_schema_and_ids_fail_closed(self):
        bad = [evidence(governed_suite=True), evidence(governed_suite=[]),
               evidence(governed_suite="UNKNOWN"), evidence(governed_suite="failed"),
               {**evidence(), "access_token": "never emit"},
               {**evidence(), "run_id": True},
               {**evidence(), "source_sha": "bad"},
               {**evidence(), "outcomes": {"unreviewed": "failure"}}]
        for payload in bad:
            with self.subTest(payload=type(payload).__name__):
                with self.assertRaises(rca.UnsafeRCAInput):
                    snapshot.snapshot(payload)

    def test_signature_independent_of_input_order_or_run_timestamp(self):
        one = evidence(governed_suite="failure")
        two = evidence(governed_suite="failure")
        two["outcomes"] = dict(reversed(list(two["outcomes"].items())))
        two["observed_at_utc"] = "2026-11-02T14:22:03Z"
        two["run_id"] += 1
        result_one = snapshot.snapshot(one)
        result_two = snapshot.snapshot(two)
        self.assertEqual(result_one["rca"]["groups"][0]["fingerprint"],
                         result_two["rca"]["groups"][0]["fingerprint"])

    def test_cli_env_is_allowlisted_and_local_artifact_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ci-rca.json"
            env = {"GITHUB_RUN_ID": "41208586626", "CANDIDATE_SHA": SHA,
                   "RC6_CI_OUTCOME_GOVERNED_SUITE": "failure",
                   "RC6_CI_OUTCOME_EARLY_REPLAY": "success"}
            output = io.StringIO()
            with patch.dict(os.environ, env), contextlib.redirect_stdout(output):
                self.assertEqual(snapshot.main(["--output", str(path)]), 0)
            self.assertIn("no_go=YES", output.getvalue())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["phase_failures"], 1)
            self.assertEqual(data["coverage"], "PARTIAL_NO_VERIFICADO")
            self.assertFalse(data["automatic_go"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_cli_rejects_symlink_target_without_overwrite_or_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, victim = root / "result.json", root / "victim.txt"
            victim.write_text("KEEP", encoding="utf-8")
            target.symlink_to(victim)
            env = {"GITHUB_RUN_ID": "41208586626", "CANDIDATE_SHA": SHA}
            stderr = io.StringIO()
            with patch.dict(os.environ, env), contextlib.redirect_stderr(stderr):
                self.assertEqual(snapshot.main(["--output", str(target)]), 2)
            self.assertEqual(victim.read_text(encoding="utf-8"), "KEEP")
            self.assertNotIn(str(target), stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
