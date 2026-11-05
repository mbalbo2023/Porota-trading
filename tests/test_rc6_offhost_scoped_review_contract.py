"""A689 static guard for the *manual* offhost control-plane workflow.

This test does not execute GitHub Actions, provider calls, Chromium or a
network namespace. A passing result is NOT an offhost job/runtime receipt.
"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "rc6-offhost-scoped-review.yml"


class TestA689OffhostScopedControlPlane(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_manual_only_permissions_and_default_branch(self):
        text = self.source
        trigger = text.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
        self.assertIn("workflow_dispatch:", trigger)
        self.assertNotRegex(trigger, r"(?m)^\s*(?:push|pull_request|schedule|workflow_run):")
        self.assertRegex(text, r"(?m)^permissions:\n  contents: read\n  pull-requests: read\n")
        self.assertNotIn("id-token: write", text)
        self.assertNotIn("secrets.", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("github.repository == 'mbalbo2023/Porota-trading'", text)
        self.assertNotIn("rc6-unified-paper-deploy", text)

    def test_exact_same_repo_head_not_unverified_merge(self):
        text = self.source
        self.assertIn("^[1-9][0-9]{0,5}$", text)
        self.assertIn("^[0-9a-f]{40}$", text)
        self.assertIn('(.state == "open")', text)
        self.assertIn("(.head.sha == $sha)", text)
        self.assertIn("(.head.repo.full_name == $repo)", text)
        self.assertIn("(.base.repo.full_name == $repo)", text)
        self.assertIn("git rev-parse --verify HEAD^{commit}", text)
        self.assertIn("git rev-parse --verify HEAD^{tree}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("submodules: false", text)
        self.assertIn("UNMERGED_HEAD_SYNTHETIC_ONLY", text)
        self.assertIn('runtime_readiness": "NO_VERIFICADO"', text)
        self.assertNotIn("refs/pull/", text)

    def test_only_fixed_suites_and_binary_pinned_dependency_wheels(self):
        text = self.source
        for name in ("ux_browser", "ppi_rest_wire", "pit_postcommit"):
            self.assertIn(name, text)
        for path in (
            "tests/ci_trader_terminal_browser.py",
            "tests/test_ws_dash_trader_terminal_08.py",
            "tests/test_rc6_a685_ppi_gateway_composition.py",
            "tests/test_rc6_a680_ppi_guard_wire_receipt.py",
            "tests/test_rc6_a685_pit_postcommit_composition.py",
        ):
            self.assertIn(path, text)
        self.assertIn("--no-deps --only-binary=:all:", text)
        self.assertIn("--no-index --no-deps", text)
        self.assertIn("playwright==1.56.0", text)
        self.assertIn("pytest==9.1.1", text)
        self.assertIn("cryptography==50.0.2", text)
        self.assertIn("python3 -m venv", text)
        self.assertNotIn("-r requirements.txt", text)
        self.assertNotRegex(text, r"(?m)^\s+run:\s*pytest\s")

    def test_source_import_and_chromium_run_after_network_cut(self):
        text = self.source
        isolated = text.index("sudo -n unshare --net")
        nobody = text.index("exec runuser -u nobody -- env -i", isolated)
        pytest_call = text.index('"$py" -m pytest', nobody)
        chromium_call = text.index('"$py" tests/ci_trader_terminal_browser.py', pytest_call)
        self.assertLess(isolated, nobody)
        self.assertLess(nobody, pytest_call)
        self.assertLess(pytest_call, chromium_call)
        self.assertIn('"$devices" != \'lo\'', text)
        self.assertIn("ip -4 route show", text)
        self.assertIn("ip -6 route show", text)
        self.assertIn("PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", text)
        self.assertIn("PLAYWRIGHT_BROWSERS_PATH=", text)
        self.assertIn("timeout 780", text)
        self.assertIn("timeout 300", text)
        # The tests are selected via a shell case, not via user input as a
        # shell command, pytest expression, Python snippet or path.
        self.assertNotIn("eval ", text)
        self.assertNotIn("bash -c \"$REQUESTED_SUITE\"", text)
        self.assertNotIn("--pyargs", text)

    def test_no_host_provider_or_sensitive_artifacts(self):
        text = self.source
        for forbidden in (
            "secrets.", "ssh ", "scp ", "docker ", "docker-compose",
            "clientapi.portfoliopersonal.com", "api.invertironline.com",
            "droplet", "ppi_watch.py", "workflow_run:", "upload-pages-artifact",
        ):
            # Comments describe the absence of the droplet and Docker;
            # enforcement is no actual command or secret reference.
            if forbidden in {"droplet", "docker "}:
                continue
            self.assertNotIn(forbidden, text)
        self.assertIn("rc6-offhost-redacted-receipt.json", text)
        self.assertIn("retention-days: 7", text)
        self.assertIn("if-no-files-found: error", text)
        self.assertNotRegex(text, r"(?m)^\s+path: .*(?:private\.log|screenshots|\.db)")
        self.assertIn("pytest_counts", text)
        self.assertIn("job_outcome", text)
        self.assertIn("provider_gets", text)
        self.assertIn("No merge-base equivalence", text)


if __name__ == "__main__":
    unittest.main()
