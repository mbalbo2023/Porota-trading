"""A848 offhost collection-only Action: negative orchestration contracts."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

import yaml

from scripts import rc6_ci_parallel_runner as runner
from scripts import rc6_ci_shard_quorum as shards
from scripts import rc6_ci_pytest_identity as identity


REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github/workflows/rc6-a848-collect-only-diagnostic.yml"
BRANCH = "integration/rc6-a848-collect-only-20261107"
SOURCE_SHA = "35bfe0888b32ead66a63c94502bc0a3292b27e16"
SOURCE_TREE = "9b48a25db351f9691a8164207fcfc2d6233ebcf9"


class A848OffhostCollectOnlyWorkflowTests(unittest.TestCase):
    def _workflow(self) -> dict:
        self.assertTrue(WORKFLOW.is_file(), "A848 isolated workflow is not implemented")
        data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        return data

    def _collection_code(self) -> str:
        job = self._workflow()["jobs"]["collect-only"]
        steps = job["steps"]
        matches = [step for step in steps
                   if step.get("name") == "Collect exact source node IDs privately"]
        self.assertEqual(len(matches), 1)
        script = matches[0]["run"]
        self.assertEqual(script.count("python - <<'PY'"), 1)
        self.assertEqual(script.count("\nPY\n"), 1)
        body = script.split("python - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        ast.parse(body)
        return body

    def _isolated_collector(self) -> tuple[dict, dict]:
        """Exercise actual inline Python contract, replacing only pytest subprocess."""
        code = self._collection_code()
        parsed = ast.parse(code)
        definitions = []
        for node in parsed.body:
            if isinstance(node, ast.Try):
                break  # Run the genuine collect() function, not its CLI print/exit.
            definitions.append(node)
        setup = ast.Module(
            body=definitions,
            type_ignores=[])
        namespace = {"__name__": "rc6_a848_offhost_fixture"}
        exec(compile(setup, "<a848-workflow-collector>", "exec"), namespace)
        nodeids = [f"tests/test_synthetic_{i:02d}.py::test_case_{j}"
                   for i in range(8) for j in range(2)]
        manifest = shards.manifest(SOURCE_SHA, SOURCE_TREE, nodeids)
        groups = shards.partition(manifest, 4)
        encoded = json.dumps(
            [identity.nodeid_digest(node) for node in groups[1]],
            sort_keys=True, separators=(",", ":")).encode("ascii")
        namespace["EXPECTED_CASES"] = len(nodeids)
        namespace["EXPECTED_COLLECTION_SHA256"] = manifest["collection_sha256"]
        namespace["EXPECTED_SHARD1_COUNT"] = len(groups[1])
        namespace["EXPECTED_SHARD1_SHA256"] = hashlib.sha256(encoded).hexdigest()
        return namespace, manifest

    def test_one_job_only_on_exact_a848_push_without_dispatch(self) -> None:
        workflow = self._workflow()
        triggers = workflow.get("on", workflow.get(True))
        self.assertEqual(set(triggers), {"push"})
        self.assertEqual(triggers["push"]["branches"], [BRANCH])
        self.assertEqual(set(workflow["jobs"]), {"collect-only"})
        job = workflow["jobs"]["collect-only"]
        event_filter = job["if"]
        for guard in ("github.event_name == 'push'", "github.repository == 'mbalbo2023/Porota-trading'",
                      "github.ref == 'refs/heads/" + BRANCH + "'"):
            self.assertIn(guard, event_filter)
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(job["permissions"], {"contents": "read"})
        self.assertEqual(job["timeout-minutes"], 6)

    def test_new_paths_trigger_only_one_of_all_existing_actions(self) -> None:
        """A branch push must not wake a second legacy/predeploy workflow."""
        changed = [
            ".github/workflows/rc6-a848-collect-only-diagnostic.yml",
            "tests/test_rc6_a848_collect_only_workflow.py",
        ]
        matches = []
        all_flows = sorted((REPO / ".github/workflows").glob("*.yml"))
        self.assertGreaterEqual(len(all_flows), 168)
        for path in all_flows:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            triggers = data.get("on", data.get(True, {}))
            if not isinstance(triggers, dict) or "push" not in triggers:
                continue
            push = triggers["push"] or {}
            if not isinstance(push, dict):
                push = {}
            branches = push.get("branches", ["*"])
            branches = [branches] if isinstance(branches, str) else branches
            ignored_branches = push.get("branches-ignore", [])
            ignored_branches = ([ignored_branches] if isinstance(ignored_branches, str)
                                else ignored_branches)
            if (not any(fnmatch.fnmatchcase(BRANCH, b) for b in branches) or
                    any(fnmatch.fnmatchcase(BRANCH, b) for b in ignored_branches)):
                continue
            paths = push.get("paths", ["*"])
            paths = [paths] if isinstance(paths, str) else paths
            ignored = push.get("paths-ignore", [])
            ignored = [ignored] if isinstance(ignored, str) else ignored
            if not any(fnmatch.fnmatchcase(file, pattern)
                       for file in changed for pattern in paths):
                continue
            if all(any(fnmatch.fnmatchcase(file, pattern) for pattern in ignored)
                   for file in changed):
                continue
            matches.append(path.relative_to(REPO).as_posix())
        self.assertEqual(matches, [
            ".github/workflows/rc6-a848-collect-only-diagnostic.yml"])

    def test_checkout_is_historically_pinned_but_event_sha_remains_separate(self) -> None:
        steps = self._workflow()["jobs"]["collect-only"]["steps"]
        checkouts = [step for step in steps if step.get("uses", "").startswith("actions/checkout@")]
        self.assertEqual(len(checkouts), 1)
        self.assertEqual(checkouts[0]["with"], {"ref": SOURCE_SHA, "persist-credentials": False})
        rendered = "\n".join(str(step.get("run", "")) for step in steps)
        for token in (SOURCE_SHA, SOURCE_TREE, "$GITHUB_SHA", "GITHUB_REF", "git rev-parse HEAD"):
            self.assertIn(token, rendered)
        code = self._collection_code()
        self.assertIn("event_sha", code)
        self.assertIn("source_sha", code)
        self.assertNotIn("RC6_CI_SOURCE_SHA\"] = event_sha", code)

    def test_only_collect_with_strict_60s_timeout_and_existing_helpers(self) -> None:
        steps = self._workflow()["jobs"]["collect-only"]["steps"]
        self.assertTrue(any("requirements.lock.txt" in str(s.get("run", "")) for s in steps))
        code = self._collection_code()
        for token in ("rc6_ci_parallel_runner", "rc6_ci_shard_quorum", "rc6_ci_pytest_identity",
                      "--collect-only", "timeout=60", "7438", "1860",
                      "49b6a886908707e8a1f5afe7b904cb374523094e499e3d320c85784d7ef9dd6d",
                      "df71467995a879b1da2c1c2c882e293b1d827c5014deccf153d3a7086c53ce21"):
            self.assertIn(token, code)
        for forbidden in ("--junitxml", "pytest -q", "--diagnostics-dir"):
            self.assertNotIn(forbidden, code)

    def test_no_release_artifact_host_secret_or_false_success(self) -> None:
        workflow = self._workflow()
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("workflow_dispatch", text)
        self.assertNotRegex(text, re.compile(r"(?i)(secrets\.|ssh |sudo |docker |digitalocean|deploy-v2|upload-artifact|self-hosted)"))
        self.assertNotIn("exit 0", text)
        code = self._collection_code()
        for item in ("automatic_go", "runtime_authority", "host_written", "test_bodies_executed",
                     "nodeid_digest", "status", "raise SystemExit(77)"):
            self.assertIn(item, code)
        self.assertNotIn('print(nodeid)', code)

    def test_actual_inline_collector_returns_only_hashed_receipt(self) -> None:
        namespace, manifest = self._isolated_collector()
        with tempfile.TemporaryDirectory(prefix="a848-unit-") as root:
            def synthetic_pytest(command, *, cwd, env, timeout):
                self.assertEqual(timeout, 60)
                self.assertIn("--collect-only", command)
                self.assertNotIn("--junitxml", " ".join(command))
                Path(env["RC6_CI_MANIFEST_PATH"]).write_text(
                    json.dumps(manifest), encoding="utf-8")
                return mock.Mock(returncode=0, stdout="", stderr="")

            env = {"GITHUB_SHA": "a" * 40,
                   "GITHUB_REF": "refs/heads/" + BRANCH,
                   "GITHUB_REPOSITORY": "mbalbo2023/Porota-trading",
                   "RUNNER_TEMP": root}
            with (mock.patch.dict(os.environ, env),
                  mock.patch.object(runner, "_verify_checkout") as verify,
                  mock.patch.object(runner, "_test_args", return_value=["."]),
                  mock.patch.object(runner, "_invoke", side_effect=synthetic_pytest)):
                proof = namespace["collect"]()
            verify.assert_called_once()
        self.assertEqual(proof["status"], "RED_COLLECTION_ONLY_OFFHOST")
        self.assertEqual(proof["source_sha"], SOURCE_SHA)
        self.assertEqual(proof["event_sha"], "a" * 40)
        self.assertEqual(sum(s["count"] for s in proof["shards"]), 16)
        self.assertFalse(proof["automatic_go"])
        self.assertFalse(proof["runtime_authority"])
        self.assertFalse(proof["test_bodies_executed"])
        serialized = json.dumps(proof)
        self.assertNotIn("tests/test_synthetic_", serialized)
        for shard in proof["shards"]:
            self.assertRegex(shard["first_node_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(shard["last_node_sha256"], r"^[0-9a-f]{64}$")

    def test_inline_collector_rejects_drift_and_event_spoof_before_receipt(self) -> None:
        namespace, manifest = self._isolated_collector()
        with tempfile.TemporaryDirectory(prefix="a848-drift-") as root:
            def synthetic_pytest(command, *, cwd, env, timeout):
                Path(env["RC6_CI_MANIFEST_PATH"]).write_text(
                    json.dumps(manifest), encoding="utf-8")
                return mock.Mock(returncode=0, stdout="", stderr="")

            env = {"GITHUB_SHA": "b" * 40,
                   "GITHUB_REF": "refs/heads/" + BRANCH,
                   "GITHUB_REPOSITORY": "mbalbo2023/Porota-trading",
                   "RUNNER_TEMP": root}
            with (mock.patch.dict(os.environ, env),
                  mock.patch.object(runner, "_verify_checkout"),
                  mock.patch.object(runner, "_test_args", return_value=["."]),
                  mock.patch.object(runner, "_invoke", side_effect=synthetic_pytest)):
                namespace["EXPECTED_COLLECTION_SHA256"] = "0" * 64
                with self.assertRaisesRegex(ValueError, "exact collection drift"):
                    namespace["collect"]()
                namespace["EXPECTED_COLLECTION_SHA256"] = manifest["collection_sha256"]
                namespace["EXPECTED_SHARD1_SHA256"] = "1" * 64
                with self.assertRaisesRegex(ValueError, "shard1 membership drift"):
                    namespace["collect"]()
            with (mock.patch.dict(os.environ, {**env, "GITHUB_SHA": SOURCE_SHA}),
                  mock.patch.object(runner, "_verify_checkout") as verify):
                with self.assertRaisesRegex(ValueError, "invalid diagnostic event"):
                    namespace["collect"]()
                verify.assert_not_called()

    def test_inline_collector_timeout_propagates_without_fake_receipt(self) -> None:
        namespace, _ = self._isolated_collector()
        with tempfile.TemporaryDirectory(prefix="a848-timeout-") as root:
            env = {"GITHUB_SHA": "c" * 40,
                   "GITHUB_REF": "refs/heads/" + BRANCH,
                   "GITHUB_REPOSITORY": "mbalbo2023/Porota-trading",
                   "RUNNER_TEMP": root}
            with (mock.patch.dict(os.environ, env),
                  mock.patch.object(runner, "_verify_checkout"),
                  mock.patch.object(runner, "_test_args", return_value=["."]),
                  mock.patch.object(runner, "_invoke",
                                    side_effect=runner.ParallelRunnerRed(
                                        diagnostic={"parent_exited_before_timeout": False}))):
                with self.assertRaises(runner.ParallelRunnerRed):
                    namespace["collect"]()


if __name__ == "__main__":
    unittest.main()
