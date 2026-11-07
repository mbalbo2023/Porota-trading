"""A820: isolated Actions diagnosis can never become a deploy/GO path."""

from pathlib import Path
import fnmatch
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/rc6-ws-tests-12-current-contract.yml"
BRANCH = "integration/rc6-a820-qa-diagnostic-20261107"


class TestA820DiagnosticWorkflow(unittest.TestCase):
    def _load(self):
        return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    def test_diagnostic_push_has_one_exact_safe_ref_and_disables_old_45min_job(self):
        doc = self._load()
        self.assertIn(BRANCH, doc["on"]["push"]["branches"])
        self.assertEqual(doc["permissions"], {"contents": "read"})
        self.assertNotIn("workflow_run", doc["on"])
        ref = f"refs/heads/{BRANCH}"
        for name in ("focal", "full-suite"):
            self.assertIn(ref, doc["jobs"][name]["if"])
            self.assertIn("!=", doc["jobs"][name]["if"])
        job = doc["jobs"]["diagnostic-shards"]
        self.assertIn("github.event_name == 'push'", job["if"])
        self.assertIn(f"github.ref == '{ref}'", job["if"])
        self.assertIn("github.repository", job["if"])
        self.assertLessEqual(int(job["timeout-minutes"]), 20)
        # A manual dispatch of this branch must not appear GREEN with all
        # three real jobs skipped; dispatch for other branches stays legacy.
        reject = doc["jobs"]["reject-manual-a820"]
        self.assertIn("github.event_name == 'workflow_dispatch'", reject["if"])
        self.assertIn(f"github.ref == '{ref}'", reject["if"])
        self.assertIn("exit 77", "\n".join(s.get("run", "") for s in reject["steps"]))

    def test_diagnostic_is_offhost_red_only_and_uploads_sanitized_receipts(self):
        doc = self._load()
        job = doc["jobs"]["diagnostic-shards"]
        steps = job["steps"]
        commands = "\n".join(step.get("run", "") for step in steps)
        import json
        content = json.dumps(job, sort_keys=True).lower()
        for banned in ("secrets.", "ssh ", "scp ", "sudo ", "docker ",
                       "rsync ", "curl ", "--maxfail", "--lf", "-x "):
            self.assertNotIn(banned, content, banned)
        self.assertIn("python -m scripts.rc6_ci_parallel_runner", commands)
        self.assertIn("--diagnostics-dir", commands)
        self.assertIn("--source-sha", commands)
        self.assertIn("--source-tree-sha", commands)
        checks = [step for step in steps if step.get("id") == "receipt"]
        self.assertEqual(len(checks), 1)
        self.assertIn("steps.diag.outcome == 'failure'", checks[0]["if"])
        self.assertIn("junit-sanitized.xml", checks[0]["run"])
        self.assertIn("receipt.json", checks[0]["run"])
        uploads = [step for step in steps
                   if step.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 1)
        self.assertIn("steps.receipt.outcome == 'success'", uploads[0]["if"])
        self.assertEqual(uploads[0]["with"]["if-no-files-found"], "error")
        self.assertIn("junit-sanitized.xml", uploads[0]["with"]["path"])
        self.assertIn("receipt.json", uploads[0]["with"]["path"])
        self.assertNotIn("continue-on-error", content)

    def test_exact_push_does_not_trigger_any_foreign_workflow_or_host_job(self):
        # All repository workflow files are part of the reviewed candidate.
        # Unknown unfiltered push workflows are not assumed harmless: reject.
        changed = {
            ".github/workflows/rc6-ws-tests-12-current-contract.yml",
            "scripts/rc6_ci_parallel_runner.py",
            "scripts/rc6_ci_shard_quorum.py",
            "tests/test_rc6_ci_junit_quorum.py",
            "tests/test_rc6_ci_parallel_runner.py",
            "tests/test_rc6_ci_shard_quorum.py",
            "tests/test_rc6_a820_qa_diagnostic_workflow.py",
        }
        triggered = []
        for path in sorted((ROOT / ".github/workflows").glob("*.y*ml")):
            doc = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
            events = (doc or {}).get("on", {})
            if not isinstance(events, dict) or "push" not in events:
                continue
            push = events["push"] or {}
            if not isinstance(push, dict):
                self.fail(f"unknown push config in {path.name}")
            branches = push.get("branches") or ["*"]
            if not any(fnmatch.fnmatch(BRANCH, p) for p in branches):
                continue
            excluded = push.get("branches-ignore") or []
            if any(fnmatch.fnmatch(BRANCH, p) for p in excluded):
                continue
            paths = push.get("paths") or ["*"]
            if not any(fnmatch.fnmatch(file, pattern)
                       for file in changed for pattern in paths):
                continue
            triggered.append(path.name)
        self.assertEqual(triggered, [WORKFLOW.name])


if __name__ == "__main__":
    unittest.main()
