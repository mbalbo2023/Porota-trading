"""A651: OFFHOST proof that no skipped artifact gate can unlock product."""

import contextlib
import io
import os
import unittest
from unittest.mock import patch

from scripts import rc6_ci_release_required_guard as guard


SHA = "a" * 40


def evidence(**changes):
    valid = {
        "RC6_RELEASE_REPO": guard.REPO,
        "RC6_RELEASE_BASE_REF": guard.PRODUCT,
        "RC6_RELEASE_PR_DRAFT": "false",
        "RC6_RELEASE_HEAD_SHA": SHA,
        "RC6_RELEASE_CHECKOUT_SHA": SHA,
        "RC6_RELEASE_GATE_RESULT": "success",
        "RC6_RELEASE_ARTIFACT_SHA": SHA,
        "RC6_RELEASE_ARTIFACT_NAME": f"porota-predeploy-v2-{SHA}",
        "RC6_RELEASE_ARTIFACT_ID": "13272023016",
        "RC6_RELEASE_ARTIFACT_DIGEST": "b" * 64,
    }
    valid.update(changes)
    return valid


class TestRequiredReleaseEvidence(unittest.TestCase):
    def test_exact_non_draft_run_has_non_authoritative_artifact_evidence(self):
        receipt = guard.verify(evidence())
        self.assertEqual(receipt["candidate_sha"], SHA)
        self.assertEqual(receipt["artifact_id"], 13272023016)
        self.assertEqual(receipt["artifact_digest"], "b" * 64)
        self.assertEqual(receipt["status"],
                         "REQUIRED_EXACT_ARTIFACT_CI_EVIDENCE_PRESENT")
        for field in ("artifact_contents_verified_by_this_guard", "owner_go_verified",
                      "runtime_verified", "automatic_go", "auto_merge",
                      "auto_deploy", "auto_trade"):
            self.assertFalse(receipt[field], field)

    def test_skipped_failed_cancelled_and_incomplete_artifact_gate_are_red(self):
        for result in ("skipped", "failure", "cancelled", "neutral",
                       "timed_out", "action_required", "", "SUCCESS"):
            with self.subTest(result=result):
                with self.assertRaises(guard.RequiredReleaseEvidenceRed):
                    guard.verify(evidence(RC6_RELEASE_GATE_RESULT=result))

    def test_draft_never_piggybacks_on_prior_successful_artifact(self):
        for draft in ("true", "", "TRUE", "False", "1"):
            with self.subTest(draft=draft):
                with self.assertRaises(guard.RequiredReleaseEvidenceRed):
                    guard.verify(evidence(RC6_RELEASE_PR_DRAFT=draft))

    def test_checkout_and_producer_identity_must_match_current_pr_head(self):
        bad = (
            {"RC6_RELEASE_REPO": "fork/Porota-trading"},
            {"RC6_RELEASE_BASE_REF": "main"},
            {"RC6_RELEASE_HEAD_SHA": "c" * 40},
            {"RC6_RELEASE_HEAD_SHA": "A" * 40},
            {"RC6_RELEASE_HEAD_SHA": "a" * 39},
            {"RC6_RELEASE_CHECKOUT_SHA": "c" * 40},
            {"RC6_RELEASE_ARTIFACT_SHA": "c" * 40},
            {"RC6_RELEASE_ARTIFACT_NAME": "porota-predeploy-v2-" + "c" * 40},
        )
        for variant in bad:
            with self.subTest(variant=tuple(variant)):
                with self.assertRaises(guard.RequiredReleaseEvidenceRed):
                    guard.verify(evidence(**variant))

    def test_uploaded_artifact_must_have_id_and_digest_from_same_job(self):
        bad = (
            {"RC6_RELEASE_ARTIFACT_ID": ""},
            {"RC6_RELEASE_ARTIFACT_ID": "0"},
            {"RC6_RELEASE_ARTIFACT_ID": "01"},
            {"RC6_RELEASE_ARTIFACT_ID": "-1"},
            {"RC6_RELEASE_ARTIFACT_ID": "1" * 18},
            {"RC6_RELEASE_ARTIFACT_DIGEST": ""},
            {"RC6_RELEASE_ARTIFACT_DIGEST": "b" * 63},
            {"RC6_RELEASE_ARTIFACT_DIGEST": "z" * 64},
            {"RC6_RELEASE_ARTIFACT_DIGEST": "B" * 64},
        )
        for variant in bad:
            with self.subTest(variant=tuple(variant)):
                with self.assertRaises(guard.RequiredReleaseEvidenceRed):
                    guard.verify(evidence(**variant))

    def test_untrusted_values_or_extra_fields_never_escape_guard(self):
        for variant in (None, [], {**evidence(), "raw_log": "SECRET"},
                        {key: value for key, value in evidence().items()
                         if key != "RC6_RELEASE_ARTIFACT_DIGEST"},
                        evidence(RC6_RELEASE_ARTIFACT_ID=True)):
            with self.subTest(input_type=type(variant).__name__):
                with self.assertRaisesRegex(guard.RequiredReleaseEvidenceRed,
                                            "RC6_REQUIRED_RELEASE_EVIDENCE_RED"):
                    guard.verify(variant)

    def test_cli_is_sanitized_and_never_issues_go(self):
        output, error = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, evidence(), clear=True), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            self.assertEqual(guard.main(), 0)
        self.assertIn("RC6_REQUIRED_RELEASE_EVIDENCE=GREEN", output.getvalue())
        self.assertIn("automatic_go=NO", output.getvalue())
        self.assertEqual(error.getvalue(), "")

        output, error = io.StringIO(), io.StringIO()
        bad = evidence(RC6_RELEASE_ARTIFACT_ID="SECRET-CANNOT-ECHO")
        with patch.dict(os.environ, bad, clear=True), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            self.assertEqual(guard.main(), 2)
        self.assertIn("RC6_REQUIRED_RELEASE_EVIDENCE=RED", error.getvalue())
        self.assertNotIn("SECRET-CANNOT-ECHO", error.getvalue())
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
