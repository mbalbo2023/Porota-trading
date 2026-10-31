"""Adversarial offline tests for opt-in pytest nodeid provenance plugin."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_ci_pytest_identity as identity


SHA = "a" * 40
TREE = "b" * 40


def items():
    return [SimpleNamespace(nodeid=f"tests/test_x.py::test_case[{i}]", user_properties=[])
            for i in range(4)]


def environment(path):
    return {"RC6_CI_IDENTITY_PROOF": "1", "RC6_CI_SOURCE_SHA": SHA,
            "RC6_CI_SOURCE_TREE_SHA": TREE, "RC6_CI_MANIFEST_PATH": str(path)}


class TestPytestNodeidIdentity(unittest.TestCase):
    def test_opt_in_attests_every_nodeid_and_writes_private_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "manifest.json"
            cases = items()
            with patch.dict(os.environ, environment(target)):
                identity.pytest_collection_modifyitems(None, None, cases)
                identity.pytest_collection_finish(SimpleNamespace(items=cases))
            value = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(value["nodeids"], sorted(case.nodeid for case in cases))
            self.assertEqual((value["source_sha"], value["source_tree_sha"]), (SHA, TREE))
            self.assertEqual(value["discovered"], len(cases))
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            for case in cases:
                self.assertEqual(case.user_properties,
                                 [(identity.PROPERTY, identity.nodeid_digest(case.nodeid))])

    def test_without_opt_in_does_not_change_pytest(self):
        cases = items()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "manifest.json"
            with patch.dict(os.environ, {"RC6_CI_IDENTITY_PROOF": "0",
                                        "RC6_CI_MANIFEST_PATH": str(target)}):
                identity.pytest_collection_modifyitems(None, None, cases)
                identity.pytest_collection_finish(SimpleNamespace(items=cases))
            self.assertFalse(target.exists())
            self.assertTrue(all(not case.user_properties for case in cases))

    def test_conflicting_or_duplicate_attestation_rejects(self):
        cases = items()
        cases[0].user_properties.append((identity.PROPERTY, "bad"))
        with patch.dict(os.environ, {"RC6_CI_IDENTITY_PROOF": "1"}):
            with self.assertRaisesRegex(RuntimeError, "CONFLICT"):
                identity.pytest_collection_modifyitems(None, None, cases)

        cases[0].user_properties = [(identity.PROPERTY,
                                    identity.nodeid_digest(cases[0].nodeid))]
        with patch.dict(os.environ, {"RC6_CI_IDENTITY_PROOF": "1"}):
            identity.pytest_collection_modifyitems(None, None, cases)
        self.assertEqual(len(cases[0].user_properties), 1)

    def test_duplicate_nodeids_bad_sha_missing_target_are_red(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "manifest.json"
            cases = items()
            cases[1].nodeid = cases[0].nodeid
            with patch.dict(os.environ, environment(target)):
                with self.assertRaises(ValueError):
                    identity.pytest_collection_finish(SimpleNamespace(items=cases))
            self.assertFalse(target.exists())
            cases = items()
            with patch.dict(os.environ, {**environment(target), "RC6_CI_SOURCE_SHA": "invalid"}):
                with self.assertRaises(ValueError):
                    identity.pytest_collection_finish(SimpleNamespace(items=cases))
            with patch.dict(os.environ, {"RC6_CI_IDENTITY_PROOF": "1"}):
                with self.assertRaisesRegex(RuntimeError, "PATH_REQUIRED"):
                    identity.pytest_collection_finish(SimpleNamespace(items=cases))

    def test_symlink_does_not_overwrite_victim(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "manifest.json"
            victim = Path(directory) / "victim.txt"
            victim.write_text("STABLE", encoding="utf-8")
            target.symlink_to(victim)
            with patch.dict(os.environ, environment(target)):
                with self.assertRaisesRegex(RuntimeError, "TARGET_UNSAFE"):
                    identity.pytest_collection_finish(SimpleNamespace(items=items()))
            self.assertEqual(victim.read_text(encoding="utf-8"), "STABLE")


if __name__ == "__main__":
    unittest.main()
