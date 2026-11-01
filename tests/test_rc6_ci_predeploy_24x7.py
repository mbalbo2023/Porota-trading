"""A229: technical predeploy must not depend on real-market calendars."""

import ast
from pathlib import Path
import re
import textwrap
import unittest

import yaml


REPO = Path(__file__).resolve().parents[1]


class TestPredeployTwentyFourSeven(unittest.TestCase):
    def test_predeploy_trigger_unconditional_on_market_hours(self):
        workflow = (REPO / ".github/workflows/porota-predeploy-v2.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", workflow)
        self.assertIn("Governed automatic test discovery and execution", workflow)
        self.assertIn("--junitxml=/tmp/porota-governed-tests.xml", workflow)
        self.assertIn("PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", workflow)
        # Virtual 405-minute native sessions are allowed; these forbidden
        # identifiers represent real-market or calendar-based technical vetoes.
        gates = ("market_open", "market_closed", "is_market_hour", "trading_hours",
                 "holiday_calendar", "is_weekend", "is_business_day",
                 "weekday()", "isoweekday()", "datetime.now(", "date.today(")
        for gate in gates:
            with self.subTest(gate=gate):
                self.assertNotIn(gate, workflow.lower())

    def test_offline_ci_uses_no_calendar_based_gate(self):
        for script in sorted((REPO / "scripts").glob("rc6_ci_*.py")):
            source = script.read_text(encoding="utf-8").lower()
            # No runtime environment-dependent market/holiday condition.
            with self.subTest(script=script.name):
                self.assertIsNone(re.search(
                    r"\b(?:market_open|is_weekend|is_business_day|holiday_calendar)\b",
                    source))

    def test_parallel_pilot_is_exact_pr776_or_explicit_opt_in_and_sequential_mandatory(self):
        workflow = yaml.safe_load((REPO / ".github/workflows/porota-predeploy-v2.yml").read_text())
        job = workflow["jobs"]["artifact-gate"]
        steps = job["steps"]
        names = [step["name"] for step in steps]
        proof_step = names.index("Optional exact-nodeid parallel proof pilot (non-authoritative)")
        governed = names.index("Governed automatic test discovery and execution")
        summary = names.index("Governed test evidence summary")
        self.assertLess(governed, summary)
        self.assertLess(summary, proof_step)
        self.assertNotIn("if", steps[governed])
        condition=steps[proof_step]["if"]
        for required in (
                "vars.RC6_CI_PARALLEL_PILOT == '1'",
                "github.event.pull_request.number == 776",
                "github.event.pull_request.head.repo.full_name == 'mbalbo2023/Porota-trading'",
                "github.event.pull_request.head.ref == 'candidate/rc6-a224-consolidated-after-775-20261031'",
                "github.event.pull_request.base.repo.full_name == 'mbalbo2023/Porota-trading'",
                "github.event.pull_request.base.ref == 'deploy/rc6-pr69-isolated-20260915'"):
            self.assertIn(required,condition)
        self.assertNotIn("RC6_CI_PARALLEL_PILOT", job.get("env", {}))
        self.assertIn("--shards 2", steps[proof_step]["run"])
        self.assertIn("sequential['discovered'] == sequential['executed']", steps[proof_step]["run"])
        self.assertIn("select_parallel_pilot",steps[proof_step]["run"])
        self.assertIn("pilot['source_tree_sha'] == os.environ['CANDIDATE_TREE_SHA']",
                      steps[proof_step]["run"])
        self.assertIn('RC6_CI_PARALLEL_OPERATOR_OPT_IN',steps[proof_step]['env'])
        self.assertIn("automatic_go", steps[proof_step]["run"])
        snippets = re.findall(r"python - <<'PY'\n(.*?)\nPY",
                              steps[proof_step]['run'],flags=re.S)
        self.assertEqual(len(snippets),2)
        for snippet in snippets:
            ast.parse(textwrap.dedent(snippet))

    def test_pr_lifecycle_exact_candidate_checked_before_all_tests(self):
        workflow = yaml.safe_load((REPO / ".github/workflows/porota-predeploy-v2.yml").read_text())
        steps = workflow["jobs"]["artifact-gate"]["steps"]
        names = [step["name"] for step in steps]
        self.assertLess(names.index("Verify exact PR provenance before tests"),
                        names.index("Fast host-transition incident replay"))
        gate = steps[names.index("Verify exact PR provenance before tests")]["run"]
        for fragment in ("GITHUB_EVENT_PATH", "CANDIDATE_SHA",
                         "git', 'rev-parse', 'HEAD", "verify(event, expected_sha=candidate)",
                         "owner_go_verified", "automatic_go"):
            self.assertIn(fragment, gate)

    def test_diagnostic_rca_is_always_observed_without_logs_or_go(self):
        workflow = yaml.safe_load((REPO / ".github/workflows/porota-predeploy-v2.yml").read_text())
        steps = workflow["jobs"]["artifact-gate"]["steps"]
        names = [step["name"] for step in steps]
        diagnostic = steps[names.index("Sanitized RCA phase learning (diagnostic only)")]
        self.assertEqual(diagnostic["if"],
                         "${{ always() && steps.rc6_checkout.outcome == 'success' }}")
        self.assertIn("rc6_ci_rca_snapshot", diagnostic["run"])
        self.assertIn("rc6-rca-signatures-v1.json", diagnostic["run"])
        self.assertEqual(set(diagnostic["env"]),
                         {"RC6_CI_OUTCOME_" + name.upper() for name in (
                             "pr_provenance", "early_replay", "governed_suite", "parallel_pilot",
                             "build_once", "native_proof", "artifact_gate")})
        primary_upload = steps[names.index("Upload predeploy evidence")]
        diagnostic_upload = steps[names.index("Upload sanitized RCA diagnostic (never promotion input)")]
        self.assertNotIn("/tmp/porota-rca-learning.json", primary_upload["with"]["path"])
        self.assertEqual(diagnostic_upload["with"]["path"], "/tmp/porota-rca-learning.json")
        self.assertLess(names.index("Upload predeploy evidence"),
                        names.index("Sanitized RCA phase learning (diagnostic only)"))
        self.assertNotIn("deploy", diagnostic["run"].lower())


if __name__ == "__main__":
    unittest.main()
