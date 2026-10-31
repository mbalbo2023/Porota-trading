"""Mocked pytest replay: global collection and truly concurrent exact shards."""

import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from scripts import rc6_ci_parallel_runner as runner
from scripts import rc6_ci_pytest_identity as identity
from scripts import rc6_ci_shard_quorum as shards


SHA = "a" * 40
TREE = "b" * 40
NODES = [f"tests/test_ci_stub.py::test_case[{i}]" for i in range(12)]
REPO = Path(__file__).resolve().parents[1]


class FakePytest:
    """Writes precise private manifests and JUnit as a stand-in for pytest."""

    def __init__(self, *, missing=False, skipped=False, fail_status=False,
                 mismatched_tree=False, extra_test=False, duplicate=False,
                 collection_rc=0, dirty_tree=False, require_parallel=True):
        self.missing = missing
        self.skipped = skipped
        self.fail_status = fail_status
        self.mismatched_tree = mismatched_tree
        self.extra_test = extra_test
        self.duplicate = duplicate
        self.collection_rc = collection_rc
        self.dirty_tree = dirty_tree
        self.calls = []
        self.barrier = threading.Barrier(2) if require_parallel else None

    def __call__(self, argv, *, cwd, env, timeout=3600):
        self.calls.append(argv)
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(argv, 0, SHA + "\n", "")
        if argv[:3] == ["git", "rev-parse", "HEAD^{tree}"]:
            return subprocess.CompletedProcess(argv, 0, TREE + "\n", "")
        if argv[:3] == ["git", "diff", "--quiet"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["git", "status", "--porcelain=v1"]:
            return subprocess.CompletedProcess(argv, 0,
                                               "?? untracked.py\n" if self.dirty_tree else "", "")
        if "--collect-only" in argv:
            assert argv[-1].startswith("--ignore=")
            root = Path(env["RC6_CI_MANIFEST_PATH"])
            if self.collection_rc == 0:
                tree = "c" * 40 if self.mismatched_tree else TREE
                root.write_text(json.dumps(shards.manifest(SHA, tree, NODES)), encoding="utf-8")
            return subprocess.CompletedProcess(argv, self.collection_rc, "not public", "")
        if "--" not in argv:
            raise AssertionError("Pytest shard must use -- end-of-options marker")
        cases = argv[argv.index("--") + 1:]
        if self.barrier:
            self.barrier.wait(timeout=5)
        is_one = NODES[0] in cases
        actual = cases[:-1] if self.missing and is_one else cases
        if self.duplicate and is_one:
            actual = [*actual, actual[0]]
        Path(env["RC6_CI_MANIFEST_PATH"]).write_text(
            json.dumps(shards.manifest(SHA, TREE, actual)) if len(actual) == len(set(actual))
            else json.dumps({"invalid": "duplicate"}), encoding="utf-8")
        xml_path = Path(next(value.split("=", 1)[1]
                             for value in argv if value.startswith("--junitxml=")))
        xml = ET.Element("testsuite", tests=str(len(actual)))
        for nodeid in actual:
            case = ET.SubElement(xml, "testcase", name="case")
            props = ET.SubElement(case, "properties")
            ET.SubElement(props, "property", name=identity.PROPERTY,
                          value=identity.nodeid_digest(nodeid))
            if self.skipped and is_one:
                ET.SubElement(case, "skipped", type="pytest.xfail")
        if self.extra_test and is_one:
            ET.SubElement(xml, "testcase", name="extra")
        xml_path.write_bytes(ET.tostring(xml, encoding="utf-8"))
        return subprocess.CompletedProcess(argv, 1 if self.fail_status and is_one else 0,
                                           "sensitive-log-not-public", "")


class TestParallelProofRunner(unittest.TestCase):
    def test_automatic_global_discovery_then_exact_parallel_shards(self):
        fake = FakePytest()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "combined.xml"
            with patch.object(runner, "_invoke", fake):
                receipt = runner.run(SHA, TREE, repo=REPO, shard_count=2,
                                     output_junit=output)
            self.assertEqual(receipt["status"], "GREEN_QUORUM_OFFLINE_ONLY")
            self.assertEqual((receipt["discovered"], receipt["executed"]), (12, 12))
            self.assertEqual(len(list(ET.parse(output).iter("testcase"))), 12)
            self.assertFalse(receipt["automatic_go"])
            self.assertFalse(receipt["ci_activated"])
            self.assertEqual(set(receipt["phase_elapsed_ms"]),
                             {"collect", "parallel_test", "quorum"})
            self.assertEqual(len([c for c in fake.calls if "--collect-only" in c]), 1)
            self.assertEqual(len([c for c in fake.calls if "--junitxml=" in " ".join(c)]), 2)
            self.assertEqual(len([c for c in fake.calls if c[:2] == ["git", "rev-parse"]]), 2)
            self.assertNotIn("sensitive-log-not-public", str(receipt))

    def test_synthetic_failures_or_missing_evidence_cannot_be_green(self):
        scenarios = [dict(missing=True), dict(skipped=True), dict(fail_status=True),
                     dict(mismatched_tree=True), dict(extra_test=True),
                     dict(duplicate=True), dict(collection_rc=2), dict(dirty_tree=True)]
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "combined.xml"
                with patch.object(runner, "_invoke", FakePytest(**scenario)):
                    with self.assertRaises(runner.ParallelRunnerRed):
                        runner.run(SHA, TREE, repo=REPO, shard_count=2,
                                   output_junit=output)
                self.assertFalse(output.exists())

    def test_wrong_sha_tree_shard_count_policy_or_symlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "combined.xml"
            with patch.object(runner, "_invoke", FakePytest(require_parallel=False)):
                for kwargs in (dict(candidate="f" * 40, tree=TREE, count=2),
                               dict(candidate=SHA, tree="f" * 40, count=2),
                               dict(candidate=SHA, tree=TREE, count=0),
                               dict(candidate=SHA, tree=TREE, count=13)):
                    with self.subTest(kwargs=kwargs):
                        with self.assertRaises(runner.ParallelRunnerRed):
                            runner.run(kwargs["candidate"], kwargs["tree"], repo=REPO,
                                       shard_count=kwargs["count"], output_junit=output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
