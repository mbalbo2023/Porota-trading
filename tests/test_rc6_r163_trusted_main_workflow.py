"""R206 static B/C workflow guards: publisher is separate from REST auditor."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
PUBLISHER = ROOT / ".github/workflows/rc6-r163-postrun-trusted-main.yml"
POSTCOMPLETION = ROOT / ".github/workflows/rc6-r206-trusted-postcompletion.yml"
SCRIPT = ROOT / "scripts/rc6_r163_trusted_postrun_byte_audit.py"
CHECKOUT_PIN = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
UPLOAD_PIN = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"


class TrustedMainWorkflowTest(unittest.TestCase):
    def test_b_trusted_main_producer_fresh_oci_and_public_only_upload(self):
        source = PUBLISHER.read_text(encoding="utf-8")
        self.assertIn("name: RC6 R206 Trusted Main Preupload Publisher (PROBE ONLY)", source)
        self.assertIn("workflow_run:", source)
        self.assertIn("types: [completed]", source)
        self.assertIn("RC6 R163 Offhost Guard79 Proof (PROBE ONLY)", source)
        for forbidden in (
            "workflow_dispatch:", "pull_request_target:",
            "repository_dispatch:", "sudo ", "ssh ", "secrets.",
            "git push", "gh workflow run", "docker system prune",
            "docker volume prune", "docker rm -f", "cancel-in-progress: true",
            "--mount type=bind", "docker.sock", "--secret", "--ssh",
            "rc6-unified-paper-deploy", "release_go=YES",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("actions: read", source)
        self.assertIn("contents: read", source)
        self.assertEqual(source.count("persist-credentials: false"), 2)
        self.assertIn(CHECKOUT_PIN, source)
        self.assertIn(UPLOAD_PIN, source)
        self.assertIn("ref: ${{ github.sha }}", source)
        self.assertIn("ref: ${{ github.event.workflow_run.head_sha }}", source)
        self.assertLess(source.index("Checkout trusted main only"),
                        source.index("Checkout candidate as Docker-only build context"))
        self.assertIn("path: .rc6-untrusted-source", source)
        self.assertIn("docker build --no-cache", source)
        self.assertIn("-t \"$RC6_PROBE_IMAGE\" .rc6-untrusted-source", source)
        self.assertIn("test \"$(id -u)\" != 1000", source)
        for required in (
            "--read-only", "--network none", "--cap-drop ALL",
            "--security-opt no-new-privileges", "--user 1000:1000",
            "--mount type=volume,target=/app/data",
            '--volumes-from "$source_cid:ro"', "python -m pytest",
            "rc6_postdeploy_stress.py --full-catalog-check",
            "rc6_postdeploy_stress.py --cadence-stage fresh",
            "rc6_postdeploy_stress.py --cadence-stage legacy",
            "RC6_R206_COLLECTOR=PHYSICAL_EROFS",
            "head -c 33554433", "head -c 262145",
            "owned_cid", "docker rm -v", "r206-upstream",
            "r206-seal", "RC6_R206_PUBLIC_DIR",
            "PROBE_PUBLISHED_PENDING_POSTRUN",
            "release_go=NO",
        ):
            self.assertIn(required, source)
        self.assertIn("path: ${{ env.RC6_R206_PUBLIC_DIR }}", source)
        self.assertNotIn("path: ${{ env.RC6_TRUSTED_STAGE }}", source)
        self.assertEqual(source.count("GITHUB_TOKEN: ${{ github.token }}"), 1)
        build_and_probe = source.split("- name: Build ephemeral OFFHOST", 1)[1].split(
            "- name: Trusted main seals", 1)[0]
        self.assertNotIn("GITHUB_TOKEN:", build_and_probe)

    def test_c_separate_completed_publisher_attempt_readonly(self):
        source = POSTCOMPLETION.read_text(encoding="utf-8")
        self.assertIn("RC6 R206 Trusted Postcompletion Public Byte Audit (READ ONLY)", source)
        self.assertIn("workflow_run:", source)
        self.assertIn("RC6 R206 Trusted Main Preupload Publisher (PROBE ONLY)", source)
        self.assertIn("types: [completed]", source)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", source)
        self.assertIn("ref: ${{ github.sha }}", source)
        self.assertIn(CHECKOUT_PIN, source)
        self.assertIn("persist-credentials: false", source)
        self.assertIn("r206-postcompletion", source)
        self.assertIn("--trusted-main-sha", source)
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", source)
        self.assertIn("actions: read", source)
        self.assertIn("contents: read", source)
        self.assertIn("release_go=NO", source)
        for forbidden in (
            "docker ", "sudo ", "ssh ", "secrets.", "git push",
            "workflow_dispatch:", "pull_request_target:",
            "repository_dispatch:", "actions/upload-artifact",
            "release_go=YES", "rc6-unified-paper-deploy",
        ):
            self.assertNotIn(forbidden, source)

    def test_b_and_c_pin_exact_same_trusted_source(self):
        expected = hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
        for path in (PUBLISHER, POSTCOMPLETION):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                match = re.search(r"RC6_TRUSTED_SOURCE_SHA256=([0-9a-f]{64})", source)
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), expected)
                self.assertIn("sha256sum --check --status", source)

    def test_every_b_and_c_inline_bash_is_syntax_valid(self):
        for path in (PUBLISHER, POSTCOMPLETION):
            source = path.read_text(encoding="utf-8")
            matches = re.findall(
                r"(?m)^ {8}run: \|\n((?:(?: {10}[^\n]*|[ ]*)\n)*)",
                source,
            )
            self.assertTrue(matches, path.name)
            for body in matches:
                normal = "\n".join(
                    line[10:] if line.startswith(" " * 10) else line
                    for line in body.splitlines())
                result = subprocess.run(
                    ["bash", "-n"], input=normal, text=True,
                    capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
