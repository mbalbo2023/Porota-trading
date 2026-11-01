"""Adverse fixtures for legacy manual promotion source and CI authority."""
from pathlib import Path
import unittest

from scripts.rc6_legacy_promotion_guard import (
    PromotionBlocked, application_ci_proof, source_package,
)


SHA = "a" * 40
TREE = ("100644 blob " + "b" * 40 + "\trequirements.txt\n"
        + "100644 blob " + "c" * 40 + "\tentrypoint.py\n")
RUN_ID = 123


def valid():
    runs = {"workflow_runs": [{"id": RUN_ID, "head_sha": SHA,
             "head_branch": "testing", "event": "push", "status": "completed",
             "conclusion": "success"}]}
    jobs = {"total_count": 2, "jobs": [
        {"name": "Suite de tests", "run_id": RUN_ID, "conclusion": "success",
         "steps": [{"name": "Ejecutar la suite", "conclusion": "success"},
                   {"name": "Cobertura minima en los modulos que tocan dinero",
                    "conclusion": "success"}]},
        {"name": "Build y arranque", "run_id": RUN_ID, "conclusion": "success",
         "steps": [{"name": "Build", "conclusion": "success"},
                   {"name": "Levantar el stack", "conclusion": "success"},
                   {"name": "El panel responde", "conclusion": "success"}]},
    ]}
    return runs, jobs


class LegacyPromotionGuardTests(unittest.TestCase):
    def test_exact_product_package_is_required_for_both_branches(self):
        for branch in ("testing", "develop"):
            source_package(branch, SHA, TREE)
        for incomplete in ("", TREE.splitlines()[0] + "\n",
                           TREE.replace("100644 blob", "120000 blob", 1)):
            with self.subTest(incomplete=incomplete), self.assertRaisesRegex(
                    PromotionBlocked, "PROMOTION_APPLICATION_PACKAGE_REQUIRED"):
                source_package("testing", SHA, incomplete)
        for branch, sha in (("main", SHA), ("testing", "d" * 39)):
            with self.assertRaisesRegex(PromotionBlocked, "PROMOTION_SOURCE_IDENTITY_INVALID"):
                source_package(branch, sha, TREE)

    def test_product_push_ci_and_both_executed_jobs_required(self):
        runs, jobs = valid()
        self.assertEqual(application_ci_proof(runs, jobs, "testing", SHA), RUN_ID)
        runs["workflow_runs"][0]["head_branch"] = "develop"
        self.assertEqual(application_ci_proof(runs, jobs, "develop", SHA), RUN_ID)

    def test_control_plane_success_cannot_be_product_proof(self):
        runs, jobs = valid()
        jobs["jobs"][0]["name"] = "Repository tests / application when present"
        with self.assertRaisesRegex(PromotionBlocked, "PROMOTION_APPLICATION_CI_NOT_EXECUTED"):
            application_ci_proof(runs, jobs, "testing", SHA)

    def test_wrong_branch_sha_event_or_skipped_run_is_red(self):
        for key, value in (("head_branch", "develop"), ("head_sha", "f" * 40),
                           ("event", "pull_request"), ("status", "queued"),
                           ("conclusion", "skipped")):
            runs, jobs = valid()
            runs["workflow_runs"][0][key] = value
            reason = "PROMOTION_LATEST_EXACT_CI_RED" if key == "conclusion" else "PROMOTION_EXACT_PUSH_CI_MISSING"
            with self.subTest(key=key), self.assertRaisesRegex(PromotionBlocked, reason):
                application_ci_proof(runs, jobs, "testing", SHA)

    def test_old_success_cannot_mask_newer_failure_for_same_source_sha(self):
        runs, jobs = valid()
        newer = dict(runs["workflow_runs"][0], id=RUN_ID + 1, conclusion="failure")
        runs["workflow_runs"].append(newer)
        with self.assertRaisesRegex(PromotionBlocked, "PROMOTION_LATEST_EXACT_CI_RED"):
            application_ci_proof(runs, jobs, "testing", SHA)

    def test_skipped_suite_coverage_build_or_health_is_red(self):
        for job_index, step_name in ((0, "Ejecutar la suite"),
                                      (0, "Cobertura minima en los modulos que tocan dinero"),
                                      (1, "Build"), (1, "Levantar el stack"),
                                      (1, "El panel responde")):
            runs, jobs = valid()
            step = next(s for s in jobs["jobs"][job_index]["steps"] if s["name"] == step_name)
            step["conclusion"] = "skipped"
            with self.subTest(step=step_name), self.assertRaisesRegex(
                    PromotionBlocked, "PROMOTION_APPLICATION_CI_NOT_EXECUTED"):
                application_ci_proof(runs, jobs, "testing", SHA)

    def test_incomplete_jobs_or_other_run_cannot_borrow_success(self):
        for change in ("count", "run_id", "job_conclusion"):
            runs, jobs = valid()
            if change == "count":
                jobs["total_count"] = 3
            elif change == "run_id":
                jobs["jobs"][0]["run_id"] = 122
            else:
                jobs["jobs"][1]["conclusion"] = "skipped"
            with self.subTest(change=change), self.assertRaises(PromotionBlocked):
                application_ci_proof(runs, jobs, "testing", SHA)

    def test_manual_workflow_pins_source_in_both_jobs_and_escapes_inputs(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/"
                    "promote-to-production.yml").read_text()
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertNotIn("  push:", workflow)
        self.assertIn("source_sha: ${{ steps.source.outputs.sha }}", workflow)
        self.assertIn("SOURCE_SHA: ${{ needs.verify.outputs.source_sha }}", workflow)
        self.assertIn('git merge --no-ff "$SOURCE_SHA"', workflow)
        self.assertIn('git rev-parse "refs/remotes/origin/$SOURCE_BRANCH"', workflow)
        self.assertIn("PROMOTION_REASON: ${{ inputs.motivo }}", workflow)
        self.assertIn("CONFIRMATION: ${{ inputs.confirmacion }}", workflow)
        self.assertNotIn("github.event.workflow_run.head_sha", workflow)
        self.assertNotIn("gh run list", workflow)


if __name__ == "__main__":
    unittest.main()
