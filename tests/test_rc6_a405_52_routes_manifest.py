"""A405: prove the candidate-only 52-route replay cannot touch runtime.

This focused test lives in the A405 PR itself so the canonical candidate
sub-PR test selector has an actual changed test, not only a workflow file.
"""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/rc6-a405-52-routes-offhost.yml"
TARGET_BRANCH = "candidate/rc6-a279-a280-20261101"
SUITES = (
    "test_rc6_a228_52_route_closed_replay.py",
    "test_rc6_a222_byma_com19031_time_contract.py",
    "test_rc6_a200_byma_com19024_late_source.py",
    "test_rc6_eod_ready_idempotence.py",
    "test_rc6_postclose_schedule_stagger.py",
    "test_rc6_a199_caucion_cash_reserve.py",
    "test_rc6_byma_calendar_failclosed.py",
)


class A405WorkflowManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.document = yaml.safe_load(cls.source)
        # PyYAML 1.1 can parse the YAML key 'on' as boolean True.
        cls.events = cls.document.get("on", cls.document.get(True))

    def test_candidate_only_triggers_without_schedule_or_manual_host_dispatch(self):
        self.assertEqual(set(self.events), {"pull_request", "push"})
        for event in ("pull_request", "push"):
            self.assertEqual(self.events[event]["branches"], [TARGET_BRANCH])
        self.assertEqual(self.document["permissions"], {"contents": "read"})
        self.assertEqual(set(self.document["jobs"]), {"closed_replay"})

    def test_no_host_database_secrets_or_deploy_in_executable_steps(self):
        job = self.document["jobs"]["closed_replay"]
        self.assertLessEqual(job["timeout-minutes"], 12)
        for step in job["steps"]:
            command = str(step.get("run") or "").lower()
            for forbidden in ("ssh ", "sudo ", "docker ", "sqlite3.connect",
                              "gh api ", "curl ", "secrets.", "ppi_watch"):
                with self.subTest(step=step.get("name"), forbidden=forbidden):
                    self.assertNotIn(forbidden, command)
        self.assertNotIn("host_headers", self.document["jobs"])

    def test_exact_seven_existing_suites_and_52_route_denominator(self):
        from rc6_trader_dashboard.navigation import CANONICAL_PATHS

        self.assertEqual(len(CANONICAL_PATHS), 52)
        self.assertEqual(len(SUITES), 7)
        run = next(step["run"] for step in self.document["jobs"]["closed_replay"]["steps"]
                   if step.get("name") == "Verify denominator and run bounded synthetic regressions")
        for filename in SUITES:
            with self.subTest(filename=filename):
                self.assertTrue((ROOT / "tests" / filename).is_file())
                self.assertEqual(run.count("tests/" + filename), 1)
                self.assertIn("tests/" + filename, self.events["pull_request"]["paths"])
        self.assertIn("A405_CANONICAL_ROUTES=52", self.source)

    def test_junit_positive_requires_53_cases_and_no_skips(self):
        self.assertIn("--junitxml=evidence/a405-offhost-junit.xml", self.source)
        self.assertIn("cases < 53 or failures or errors or skipped", self.source)
        self.assertIn("A405_OFFHOST_REPLAY=GREEN", self.source)
        self.assertIn("requirements.lock.txt", self.source)
        self.assertIn("retention-days: 14", self.source)


if __name__ == "__main__":
    unittest.main()
