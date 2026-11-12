"""Negative CI fixtures: a skipped or absent suite never makes main green."""
from __future__ import annotations

import copy
from contextlib import redirect_stdout
import io
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import yaml

import scripts.rc6_main_ci_scope as scope
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

    def test_no_tests_is_red_even_when_discovery_finds_zero(self):
        (self.root / "tests").mkdir()
        with self.assertRaisesRegex(ScopeError, "CI_CONTROL_TESTS_EMPTY"):
            run_control_tests(self.root)

    def test_all_skipped_is_red(self):
        self._test("import unittest\nclass T(unittest.TestCase):\n"
                   " @unittest.skip('fixture')\n def test_skip(self): pass\n")
        with self.assertRaisesRegex(
                ScopeError, "CI_CONTROL_TESTS_INCOMPLETE_OR_FAILED"):
            run_control_tests(self.root)

    def test_failure_is_red_and_real_pass_has_count(self):
        self._test("import unittest\nclass T(unittest.TestCase):\n"
                   " def test_fail(self): self.fail('fixture')\n", "test_fixture_fail.py")
        with self.assertRaisesRegex(
                ScopeError, "CI_CONTROL_TESTS_INCOMPLETE_OR_FAILED"):
            run_control_tests(self.root)
        (self.root / "tests/test_fixture_fail.py").unlink()
        self._test("import unittest\nclass T(unittest.TestCase):\n"
                   " def test_pass(self): self.assertTrue(True)\n", "test_fixture_pass.py")
        self.assertEqual(run_control_tests(self.root), 1)

    def test_pytest_function_is_counted_in_addition_to_unittest_case(self):
        self._test("import unittest\nclass T(unittest.TestCase):\n"
                   " def test_case(self): self.assertTrue(True)\n"
                   "def test_pytest_function(): assert True\n")
        self.assertEqual(run_control_tests(self.root), 2)

    def test_unittest_subtests_increase_junit_denominator_without_false_red(self):
        self._test("import unittest\nclass T(unittest.TestCase):\n"
                   " def test_subtests(self):\n"
                   "  for i in range(3):\n"
                   "   with self.subTest(i=i): self.assertTrue(True)\n")
        self.assertGreater(run_control_tests(self.root), 1)

    def test_workflow_requires_control_tests_and_app_only_steps_are_explicit(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text()
        self.assertIn("python -m scripts.rc6_main_ci_scope test-control", workflow)
        self.assertIn("python -m scripts.rc6_main_ci_scope validate-control-deps", workflow)
        self.assertIn("cache-dependency-path: .github/requirements-control-ci.txt", workflow)
        self.assertIn("python -m pip install --disable-pip-version-check -r .github/requirements-control-ci.txt", workflow)
        self.assertIn("if: steps.detect.outputs.scope == 'CONTROL_PLANE_ONLY'", workflow)
        self.assertIn("APPLICATION_CHECKS=NOT_APPLICABLE|reason=CONTROL_PLANE_ONLY", workflow)
        self.assertIn("if: steps.detect.outputs.scope == 'APPLICATION'", workflow)
        self.assertNotIn("Application package not installed yet", workflow)

    def test_control_dependency_guard_requires_all_three_exact_pins(self):
        dependency_file = self.root / ".github/requirements-control-ci.txt"
        with self.assertRaisesRegex(ScopeError, "CI_CONTROL_DEPS_MISSING"):
            scope.validate_control_deps(self.root)
        bad_files = (
            "pytest==9.1.1\nPyYAML==6.0.3\n",
            "pytest>=9.1.1\nPyYAML==6.0.3\ncryptography==50.0.2\n",
            "pytest==9.1.1\nPyYAML==6.0.3\ncryptography==50.0.2\n--trusted-host=example.net\n",
            "pytest==9.1.1\nPyYAML==6.0.3\ncryptography==50.0.1\n",
        )
        for value in bad_files:
            with self.subTest(value=value):
                dependency_file.write_text(value, encoding="utf-8")
                with self.assertRaisesRegex(ScopeError, "CI_CONTROL_DEPS_MISMATCH"):
                    scope.validate_control_deps(self.root)
        dependency_file.write_text(
            "pytest==9.1.1\nPyYAML==6.0.3\ncryptography==50.0.2\n",
            encoding="utf-8")
        self.assertEqual(scope.validate_control_deps(self.root), 3)
        allowed_text = dependency_file.read_text(encoding="utf-8")
        dependency_file.write_text(allowed_text + ("x" * 513), encoding="utf-8")
        with self.assertRaisesRegex(ScopeError, "CI_CONTROL_DEPS_MISMATCH"):
            scope.validate_control_deps(self.root)
        external = self.root / "pins-external.txt"
        external.write_text(allowed_text, encoding="utf-8")
        dependency_file.unlink()
        dependency_file.symlink_to(external)
        with self.assertRaisesRegex(ScopeError, "CI_CONTROL_DEPS_MISMATCH"):
            scope.validate_control_deps(self.root)

    def test_control_cache_is_exact_pip_lock_and_never_skips_mandatory_tests(self):
        project_root = Path(__file__).resolve().parents[1]
        lockfile = project_root / ".github/requirements-control-ci.txt"
        self.assertEqual(lockfile.read_text(encoding="utf-8").splitlines(), [
            "pytest==9.1.1", "PyYAML==6.0.3", "cryptography==50.0.2",
        ])
        workflow = yaml.load((project_root / ".github/workflows/ci.yml").read_text(),
                             Loader=yaml.BaseLoader)
        control_steps = workflow["jobs"]["application"]["steps"]
        by_name = {step["name"]: step for step in control_steps}
        setup = by_name["Setup control-plane Python 3.11"]
        self.assertEqual(setup["with"]["cache"], "pip")
        self.assertEqual(setup["with"]["cache-dependency-path"],
                         ".github/requirements-control-ci.txt")
        self.assertEqual(by_name["Install pinned control-plane test dependencies"]["run"],
                         "python -m pip install --disable-pip-version-check -r .github/requirements-control-ci.txt")
        for name in ("Validate pinned control-plane dependencies",
                     "Install pinned control-plane test dependencies",
                     "Run mandatory control-plane tests"):
            self.assertEqual(by_name[name]["if"],
                             "steps.detect.outputs.scope == 'CONTROL_PLANE_ONLY'")


class DiffAwareScopeTests(unittest.TestCase):
    REPO = "mbalbo2023/Porota-trading"

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="rc6-ci-diff-")
        self.addCleanup(self.scratch.cleanup)
        self.repo = Path(self.scratch.name)
        self._git("init", "-q", "-b", "main")
        (self.repo / "docs").mkdir()
        (self.repo / "docs/guide.md").write_text("old\n", encoding="utf-8")
        (self.repo / "engine.py").write_text("paper_only = True\n", encoding="utf-8")
        self._commit("baseline")
        self.base = self._git("rev-parse", "HEAD").strip()

    def _git(self, *args):
        return subprocess.check_output(
            ["git", "-c", "user.name=RC6 Test", "-c",
             "user.email=rc6@example.invalid", *args],
            cwd=self.repo, text=True,
        )

    def _commit(self, title):
        self._git("add", "-A")
        self._git("commit", "-qm", title)
        return self._git("rev-parse", "HEAD").strip()

    def _event(self, head):
        return {
            "repository": {"full_name": self.REPO},
            "pull_request": {
                "number": 1087,
                "base": {"sha": self.base, "ref": "main",
                         "repo": {"full_name": self.REPO}},
                "head": {"sha": head,
                         "repo": {"full_name": self.REPO}},
            },
            # Untrusted authored metadata must not substitute for Git's tree diff.
            "files": [{"filename": "docs/guide.md", "status": "modified"}],
        }

    def test_changed_markdown_under_docs_is_docs_only(self):
        self.assertEqual(
            scope.classify_changed_paths([("M", "docs/user-guide.md")]),
            "DOCS_ONLY",
        )

    def test_dashboard_view_only_is_advisory_dashboard_scope(self):
        self.assertEqual(
            scope.classify_changed_paths([
                ("M", "rc6_trader_dashboard/views_analytics.py"),
                ("M", "da_dashboard_ux_hf6.py"),
            ]),
            "DASHBOARD_ONLY",
        )

    def test_sensitive_or_ambiguous_paths_must_run_full(self):
        dangerous = (
            "docs/governance/release-policy.md",
            "docs/ci/verify.py",
            ".github/workflows/ci.yml",
            "scripts/rc6_main_ci_scope.py",
            "tests/test_rc6_main_ci_scope.py",
            "AGENTS.md",
            "ops/policy/porota-policy.yaml",
            "rc6_trader_dashboard/shell.py",
            "be_paper_engine.py",
            "cg_paper_workspace.py",
            "Dockerfile",
            "requirements.lock.txt",
            "docs/../src/sneaky.md",
            "docs//duplicate.md",
            "docs/escaped\\path.md",
            "docs/line\nbreak.md",
            "/docs/absolute.md",
            "docs/.hidden.md",
            "unknown/module.py",
        )
        for path in dangerous:
            with self.subTest(path=path):
                self.assertEqual(
                    scope.classify_changed_paths([("M", path)]),
                    "FULL_REQUIRED",
                )

    def test_renames_deletions_and_new_files_never_skip_full(self):
        for status in ("R100", "D", "A", "C100", "T", "U", "X", "?"):
            with self.subTest(status=status):
                self.assertEqual(
                    scope.classify_changed_paths([(status, "docs/guide.md")]),
                    "FULL_REQUIRED",
                )

    def test_mixed_scope_empty_and_bounded_diff_fail_closed(self):
        self.assertEqual(scope.classify_changed_paths([]), "FULL_REQUIRED")
        self.assertEqual(scope.classify_changed_paths([
            ("M", "docs/guide.md"),
            ("M", "rc6_trader_dashboard/views_analytics.py"),
        ]), "FULL_REQUIRED")
        self.assertEqual(scope.classify_changed_paths(
            [("M", "docs/guide.md")] * 513,
        ), "FULL_REQUIRED")

    def test_authenticated_git_diff_takes_paths_from_git_not_pr_payload(self):
        (self.repo / "docs/guide.md").write_text("new\n", encoding="utf-8")
        doc_head = self._commit("docs change")
        self.assertEqual(scope.classify_event_diff(
            self.repo, self._event(doc_head), event_name="pull_request",
            repository=self.REPO, github_sha=doc_head,
        ), "DOCS_ONLY")
        (self.repo / "engine.py").write_text("paper_only = False\n", encoding="utf-8")
        unsafe_head = self._commit("engine change")
        self.assertEqual(scope.classify_event_diff(
            self.repo, self._event(unsafe_head), event_name="pull_request",
            repository=self.REPO, github_sha=unsafe_head,
        ), "FULL_REQUIRED")

    def test_event_identity_git_sha_and_fork_must_be_unambiguous(self):
        (self.repo / "docs/guide.md").write_text("new\n", encoding="utf-8")
        head = self._commit("docs change")
        event = self._event(head)
        def check(event_value, *, event_name="pull_request", repository=None,
                  github_sha=None):
            return scope.classify_event_diff(
                self.repo, event_value, event_name=event_name,
                repository=repository if repository is not None else self.REPO,
                github_sha=github_sha if github_sha is not None else head,
            )

        self.assertEqual(check(event), "DOCS_ONLY")
        variants = (
            ("wrong repository", ("repository", "full_name"), "somebody/repo"),
            ("forked head", ("pull_request", "head", "repo", "full_name"), "fork/repo"),
            ("wrong base", ("pull_request", "base", "ref"), "security-bypass"),
            ("invalid base sha", ("pull_request", "base", "sha"), "0" * 40),
            ("missing head", ("pull_request", "head", "sha"), "BAD;echo hacked"),
            ("nonnumeric pr number", ("pull_request", "number"), "1087"),
            ("negative pr number", ("pull_request", "number"), -3),
        )
        for description, path, value in variants:
            with self.subTest(description=description):
                altered = copy.deepcopy(event)
                node = altered
                for part in path[:-1]:
                    node = node[part]
                node[path[-1]] = value
                self.assertEqual(check(altered), "FULL_REQUIRED")
        self.assertEqual(check(event, repository="attacker/repo"), "FULL_REQUIRED")
        self.assertEqual(check(event, github_sha="not-a-sha"), "FULL_REQUIRED")
        self.assertEqual(check(event, event_name="workflow_dispatch"), "FULL_REQUIRED")

    def test_push_requires_same_after_sha_and_available_base(self):
        (self.repo / "docs/guide.md").write_text("new\n", encoding="utf-8")
        head = self._commit("docs change")
        event = {
            "repository": {"full_name": self.REPO},
            "ref": "refs/heads/main", "before": self.base,
            "after": head, "forced": False, "deleted": False,
        }
        check = lambda event_value, sha: scope.classify_event_diff(
            self.repo, event_value, event_name="push",
            repository=self.REPO, github_sha=sha)
        self.assertEqual(check(event, head), "DOCS_ONLY")
        self.assertEqual(check(event, self.base), "FULL_REQUIRED")
        event["forced"] = True
        self.assertEqual(check(event, head), "FULL_REQUIRED")
        event["forced"] = False
        event["before"] = "f" * 40
        self.assertEqual(check(event, head), "FULL_REQUIRED")

    def test_git_detects_rename_deletion_and_addition_as_full(self):
        target = self.repo / "docs/guide.md"
        target.rename(self.repo / "docs/renamed.md")
        renamed = self._commit("rename docs")
        self.assertEqual(scope.classify_event_diff(
            self.repo, self._event(renamed), event_name="pull_request",
            repository=self.REPO, github_sha=renamed), "FULL_REQUIRED")

    def test_git_detects_deletion_and_addition_as_full(self):
        (self.repo / "docs/guide.md").unlink()
        deleted = self._commit("delete docs")
        self.assertEqual(scope.classify_event_diff(
            self.repo, self._event(deleted), event_name="pull_request",
            repository=self.REPO, github_sha=deleted), "FULL_REQUIRED")

    def test_git_detects_addition_as_full(self):
        (self.repo / "docs/new.md").write_text("hello\n", encoding="utf-8")
        added = self._commit("add docs")
        self.assertEqual(scope.classify_event_diff(
            self.repo, self._event(added), event_name="pull_request",
            repository=self.REPO, github_sha=added), "FULL_REQUIRED")

    def test_diff_cli_is_fail_closed_on_missing_or_malformed_payload(self):
        def call_cli():
            with io.StringIO() as output, redirect_stdout(output):
                self.assertEqual(scope.main(["classify-diff"]), 0)
                return output.getvalue().strip()
        with mock.patch.dict(os.environ, {"GITHUB_EVENT_PATH": ""}):
            self.assertEqual(call_cli(), "FULL_REQUIRED")
        self._git("checkout", "-qb", "feature", self.base)
        (self.repo / "docs/guide.md").write_text("new\n", encoding="utf-8")
        head = self._commit("docs change")
        self._git("checkout", "main")
        self._git("merge", "--no-ff", "-qm", "synthetic PR merge", "feature")
        merge_sha = self._git("rev-parse", "HEAD").strip()
        event_path = self.repo / "event.json"
        event_path.write_text(json.dumps(self._event(head)), encoding="utf-8")
        with mock.patch.dict(os.environ, {
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REPOSITORY": self.REPO,
            "GITHUB_SHA": merge_sha,
        }):
            with mock.patch.object(Path, "cwd", return_value=self.repo):
                self.assertEqual(call_cli(), "DOCS_ONLY")
                with mock.patch.dict(os.environ, {"GITHUB_SHA": head}):
                    self.assertEqual(call_cli(), "FULL_REQUIRED")
                tampered = self._event(self.base)
                event_path.write_text(json.dumps(tampered), encoding="utf-8")
                self.assertEqual(call_cli(), "FULL_REQUIRED")
                event_path.write_text(json.dumps(self._event(head)), encoding="utf-8")
        event_path.write_text("bad json", encoding="utf-8")
        with mock.patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event_path)}):
            with mock.patch.object(Path, "cwd", return_value=self.repo):
                self.assertEqual(call_cli(), "FULL_REQUIRED")

    def test_workflow_keeps_security_and_tests_with_doc_only_image_optimization(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("fetch-depth: 2", workflow)
        self.assertIn('git show "$trusted_base:scripts/rc6_main_ci_scope.py"', workflow)
        self.assertIn('plan="FULL_REQUIRED"', workflow)
        self.assertIn("GITHUB_EVENT_PATH: ${{ github.event_path }}", workflow)
        self.assertIn("GITHUB_REPOSITORY: ${{ github.repository }}", workflow)
        self.assertIn("GITHUB_SHA: ${{ github.sha }}", workflow)
        self.assertIn("CI_PLAN=$plan", workflow)
        self.assertIn("python -m scripts.rc6_main_ci_scope test-control", workflow)
        self.assertIn("run: pytest -q", workflow)
        self.assertIn("echo 'APPLICATION_IMAGE_CHECKS=NOT_APPLICABLE|reason=AUTHENTICATED_DOCS_ONLY'", workflow)
        self.assertIn("steps.delta.outputs.plan != 'DOCS_ONLY'", workflow)
        # Security scan, Compose syntax, and control-plane test must not be
        # conditional on the reduced diff plan.
        self.assertIn("- name: Validate repository safety", workflow)
        self.assertIn("- name: Validate Docker Compose syntax", workflow)
        parsed = yaml.load(workflow, Loader=yaml.BaseLoader)
        infra = {step["name"]: step for step in
                 parsed["jobs"]["infrastructure"]["steps"]}
        application = {step["name"]: step for step in
                       parsed["jobs"]["application"]["steps"]}
        for name in ("Validate repository safety", "Validate Docker Compose syntax"):
            self.assertNotIn("if", infra[name])
        self.assertEqual(application["Run mandatory control-plane tests"]["if"],
                         "steps.detect.outputs.scope == 'CONTROL_PLANE_ONLY'")
        self.assertEqual(application["Run application tests"]["if"],
                         "steps.detect.outputs.scope == 'APPLICATION'")
        for name in ("Build application image", "Start application stack",
                     "Wait for application health", "Verify no restart loop",
                     "Verify SQLite WAL when database module is available"):
            self.assertIn("steps.delta.outputs.plan != 'DOCS_ONLY'",
                          application[name]["if"])
        for step in application.values():
            if step.get("shell") == "bash":
                parsed_bash = subprocess.run(
                    ["bash", "-n", "-c", step["run"]],
                    capture_output=True, text=True, check=False)
                self.assertEqual(parsed_bash.returncode, 0, step["name"])


if __name__ == "__main__":
    unittest.main()
