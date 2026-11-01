"""Adversarial offline tests for opt-in pytest nodeid provenance plugin."""

import json
import os
from pathlib import Path
import subprocess
import sys
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
            # A real two-shard pilot provides RC6_CI_MANIFEST_PATH globally.
            # Simulate a genuinely missing target without inheriting and
            # accidentally replacing the runner's live executed.json proof.
            with patch.dict(os.environ, {"RC6_CI_IDENTITY_PROOF": "1"}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "PATH_REQUIRED"):
                    identity.pytest_collection_finish(SimpleNamespace(items=cases))

    def test_missing_path_adversarial_does_not_clobber_runner_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            runner_target=Path(directory)/'executed.json'
            runner_target.write_text('RUNNER_MANIFEST_UNTOUCHED',encoding='utf-8')
            with patch.dict(os.environ, environment(runner_target)):
                with patch.dict(os.environ,
                                {'RC6_CI_IDENTITY_PROOF':'1'},clear=True):
                    with self.assertRaisesRegex(RuntimeError,'PATH_REQUIRED'):
                        identity.pytest_collection_finish(
                            SimpleNamespace(items=items()))
            self.assertEqual(runner_target.read_text(encoding='utf-8'),
                             'RUNNER_MANIFEST_UNTOUCHED')

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

    def test_real_pytest_collection_labels_megabyte_payload_without_nodeid_leak(self):
        with tempfile.TemporaryDirectory(prefix='rc6-ci-oversized-') as directory:
            root=Path(directory)
            test_path=root/'test_large_parameter.py'
            test_path.write_text('import pytest\n'
                '@pytest.mark.parametrize("bad", ["{broken", "x"*(1024**2+1)], '
                'ids=["MALFORMED","OVERSIZED_1M"])\n'
                'def test_payload(bad):\n    assert isinstance(bad,str)\n',
                encoding='utf-8')
            target=root/'manifest.json'
            env=os.environ.copy()
            env.update(environment(target))
            env['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
            env.pop('PYTEST_ADDOPTS',None)
            env.pop('PYTEST_PLUGINS',None)
            result=subprocess.run(
                [sys.executable,'-m','pytest','-p','scripts.rc6_ci_pytest_identity',
                 '-p','no:cacheprovider','--collect-only','-q',str(test_path)],
                cwd=Path(__file__).resolve().parents[1],env=env,
                capture_output=True,text=True,timeout=30,check=False)
            self.assertEqual(result.returncode,0)
            proof=json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual(proof['discovered'],2)
            self.assertTrue(any('[OVERSIZED_1M]' in value for value in proof['nodeids']))
            self.assertTrue(all(len(name.encode('utf-8')) < 256 for name in proof['nodeids']))
            self.assertFalse(any('x'*256 in name for name in proof['nodeids']))


if __name__ == "__main__":
    unittest.main()
