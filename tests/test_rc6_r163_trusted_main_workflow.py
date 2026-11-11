"""Stdlib static guard for the proposed main-only, read-only postrun trigger."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/rc6-r163-postrun-trusted-main.yml"
SCRIPT = ROOT / "scripts/rc6_r163_trusted_postrun_byte_audit.py"


class TrustedMainWorkflowTest(unittest.TestCase):
    def test_workflow_exists_only_as_a_new_default_main_candidate(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_run:", source)
        self.assertIn("types: [completed]", source)
        self.assertIn("RC6 R163 Offhost Guard79 Proof (PROBE ONLY)", source)
        for forbidden in ("workflow_dispatch:", "pull_request_target:",
                          "repository_dispatch:", "docker ", "sudo ", "ssh ",
                          "secrets.", "git push", "gh workflow run", "artifact-gate"):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("rc6-unified-paper-deploy", source)
        self.assertIn("actions: read", source)
        self.assertIn("contents: read", source)
        self.assertIn("persist-credentials: false", source)
        self.assertNotIn("github.event.workflow_run.head_sha }}", source.split("ref:", 1)[-1].split("\n", 1)[0])
        self.assertIn("ref: ${{ github.sha }}", source)
        self.assertIn("github.event.workflow_run.id", source)
        self.assertIn("github.event.workflow_run.run_attempt", source)
        self.assertIn("python3 -I scripts/rc6_r163_trusted_postrun_byte_audit.py", source)
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", source)
        self.assertIn("automatic_go=NO", source)

    def test_bootstrap_has_a_literal_reviewable_sha256_source_pin(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        match = re.search(r"RC6_TRUSTED_SOURCE_SHA256=([0-9a-f]{64})", source)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), hashlib.sha256(SCRIPT.read_bytes()).hexdigest())
        self.assertIn('sha256sum --check --status', source)

    def test_inline_bash_is_syntax_valid(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        match = re.search(r"(?ms)^\s{6}- name: Verify trusted main source and audit raw ZIP\n"
                          r".*?^ {8}run: \|\n(.*?)^ {6}- name:", source)
        self.assertIsNotNone(match)
        body = "\n".join(line[10:] if line.startswith(" " * 10) else line
                         for line in match.group(1).splitlines())
        result = subprocess.run(["bash", "-n"], input=body, text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
