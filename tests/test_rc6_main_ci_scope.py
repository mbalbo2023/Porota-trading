"""Negative CI fixtures: a skipped or absent suite never makes main green."""
from __future__ import annotations

from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest

from scripts.rc6_main_ci_scope import ScopeError, classify, run_control_tests


class MainCiScopeTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="rc6-ci-scope-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        (self.root / "AGENTS.md").touch()
        workflow = self.root / ".github/workflows/checkpoint-attestation.yml"
        workflow.parent.mkdir(parents=True)
        workflow.touch()

    def _test(self, body: str, name="test_fixture.py"):
        folder = self.root / "tests"
        folder.mkdir(exist_ok=True)
        (folder / name).write_text(body)

    def test_complete_application_and_control_tree_have_distinct_scopes(self):
        self.assertEqual(classify(self.root), "CONTROL_PLANE_ONLY")
        (self.root / "requirements.txt").touch()
        (self.root / "entrypoint.py").touch()
        self.assertEqual(classify(self.root), "APPLICATION")
        with self.assertRaisesRegex(ScopeError, "CI_CONTROL_TESTS_WRONG_SCOPE"):
            run_control_tests(self.root)

    def test_partial_application_package_is_red(self):
        (self.root / "requirements.txt").touch()
        with self.assertRaisesRegex(ScopeError, "CI_APPLICATION_PACKAGE_PARTIAL"):
            classify(self.root)

    def test_unknown_tree_cannot_be_labeled_control_plane(self):
        (self.root / "AGENTS.md").unlink()
        with self.assertRaisesRegex(ScopeError, "CI_REPOSITORY_SCOPE_UNVERIFIED"):
            classify(self.root)

    def test_no_tests_is_red_even_when_discovery_returns_success(self):
        (self.root / "tests").mkdir()
        with self.assertRaisesRegex(ScopeError, "CI_CONTROL_TESTS_EMPTY"):
            run_control_tests(self.root)

    def test_all_skipped_is_red(self):
        self._test("import unittest\nclass T(unittest.TestCase):\n"
                   " @unittest.skip('fixture')\n def test_skip(self): pass\n")
        with redirect_stderr(io.StringIO()), self.assertRaisesRegex(
                ScopeError, "CI_CONTROL_TESTS_INCOMPLETE_OR_FAILED"):
            run_control_tests(self.root)

    def test_failure_is_red_and_real_pass_has_count(self):
        self._test("import unittest\nclass T(unittest.TestCase):\n"
                   " def test_fail(self): self.fail('fixture')\n", "test_fixture_fail.py")
        with redirect_stderr(io.StringIO()), self.assertRaisesRegex(
                ScopeError, "CI_CONTROL_TESTS_INCOMPLETE_OR_FAILED"):
            run_control_tests(self.root)
        (self.root / "tests/test_fixture_fail.py").unlink()
        self._test("import unittest\nclass T(unittest.TestCase):\n"
                   " def test_pass(self): self.assertTrue(True)\n", "test_fixture_pass.py")
        with redirect_stderr(io.StringIO()):
            self.assertEqual(run_control_tests(self.root), 1)

    def test_workflow_requires_control_tests_and_app_only_steps_are_explicit(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text()
        self.assertIn("python3 -m scripts.rc6_main_ci_scope test-control", workflow)
        self.assertIn("if: steps.detect.outputs.scope == 'CONTROL_PLANE_ONLY'", workflow)
        self.assertIn("APPLICATION_CHECKS=NOT_APPLICABLE|reason=CONTROL_PLANE_ONLY", workflow)
        self.assertIn("if: steps.detect.outputs.scope == 'APPLICATION'", workflow)
        self.assertNotIn("Application package not installed yet", workflow)


if __name__ == "__main__":
    unittest.main()
