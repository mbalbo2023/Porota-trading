"""Exercise automatic collection and truthful summaries with real pytest runs."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class CodeTestRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "ops/policy").mkdir(parents=True)
        (self.root / "ops/policy/test-policy.yaml").write_text("""test_governance:
  automatic_discovery_required: true
  manual_test_file_allowlist_as_primary_ci: false
productive_scope:
  roots: ['.']
  exclusions: []
  retired_contracts: ops/policy/retired-release-tests.json
""")
        (self.root / "ops/policy/retired-release-tests.json").write_text("[]\n")
        (self.root / "pytest.ini").write_text("[pytest]\n")
        self.identity = {"source_sha": "a" * 40, "source_tree": "b" * 40, "image_id": "sha256:" + "c" * 64}

    def run_suite(self):
        self.assertIsNotNone(importlib.util.find_spec("scripts.porota_ci"), "The single code-test runner is missing")
        module = importlib.import_module("scripts.porota_ci")
        return module.run_code_tests(self.root, self.root / "receipt.json", self.identity)

    def test_cgroup_event_is_measured_without_false_oom_inference(self):
        module = importlib.import_module("scripts.porota_ci")
        output = self.root / "receipt.json"
        with patch.object(module, "_cgroup_oom_kills", side_effect=[10, 11]), \
             patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess(
                 args=["synthetic_pytest"], returncode=-9)):
            result = module.run_code_tests(self.root, output, self.identity)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["process_exit_code"], -9)
        self.assertEqual(result["cgroup_oom_kill_delta"], 1)
        self.assertEqual(result["cgroup_oom_status"], "OOM_KILL_EVENT_OBSERVED")
        self.assertNotIn("PASS", result["status"])

    def test_progress_checkpoint_names_only_module_and_remains_red(self):
        module = importlib.import_module("scripts.porota_ci")
        source = self.root / "test_example.py"
        source.write_text("def test_example():\\n    assert True\\n")
        plugin = module._Receipt(self.root, self.root / "receipt.json", self.identity)
        plugin.collected = 7
        plugin.pytest_runtest_logstart(
            "test_example.py::test_example[PRIVATE_PARAMETER]", ("test_example.py", 1, "test_example"))
        report = json.loads((self.root / "receipt.json").read_text())
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["runner_phase"], "PYTEST_IN_PROGRESS")
        self.assertEqual(report["active_test_module"], "test_example.py")
        self.assertNotIn("PRIVATE_PARAMETER", json.dumps(report))

    def test_governed_interrupted_shard_keeps_sanitized_fail_progress(self):
        module = importlib.import_module("scripts.porota_ci")
        from scripts.rc6_ci_parallel_runner import ParallelRunnerRed
        output = self.root / "receipt.json"

        def interrupted(*args, **kwargs):
            report = kwargs["progress"]
            report("COLLECTION", -1, 0)
            report("SHARD_RUNNING", 3, 2)
            snapshot = json.loads(output.read_text())
            self.assertEqual(snapshot["status"], "FAIL")
            self.assertEqual(snapshot["runner_phase"], "GOVERNED_SHARD_IN_PROGRESS")
            self.assertEqual(snapshot["progress_shard_index"], 3)
            raise ParallelRunnerRed("SUBPROCESS_INVALID")

        with patch("scripts.rc6_ci_parallel_runner.run", side_effect=interrupted):
            result = module._governed_serial_shard_tests(
                self.root, output, self.identity, {})
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["runner_phase"], "GOVERNED_SHARD_QUORUM_REJECTED")
        self.assertEqual(result["progress_stage"], "SHARD_RUNNING")
        self.assertEqual(result["progress_completed_shards"], 2)
        self.assertNotIn("PRIVATE_PARAMETER", json.dumps(result))
        self.assertEqual(json.loads(output.read_text()), result)

    def test_progress_receipt_rejects_untrusted_text_without_losing_previous(self):
        module = importlib.import_module("scripts.porota_ci")
        output = self.root / "receipt.json"
        before = module._failclosed_start_receipt(
            output, self.identity, "PYTEST_PRECHECK_PENDING")
        with self.assertRaisesRegex(ValueError, "CI_PROGRESS_METADATA_INVALID"):
            module._failclosed_start_receipt(
                output, self.identity, "GOVERNED_SHARD_IN_PROGRESS",
                progress={"stage": "PRIVATE_PARAMETER", "index": 0, "completed": 0})
        self.assertEqual(json.loads(output.read_text()), before)


    def test_governed_nonzero_carries_only_sanitized_source_module(self):
        module = importlib.import_module("scripts.porota_ci")
        from scripts.rc6_ci_parallel_runner import ParallelRunnerRed
        output = self.root / "receipt.json"

        def failed(*args, **kwargs):
            kwargs["progress"]("SHARD_RUNNING", 0, 0)
            raise ParallelRunnerRed(
                "SHARD_PYTEST_NONZERO",
                failure_module="tests/test_rc6_ci_parallel_runner.py")

        with patch("scripts.rc6_ci_parallel_runner.run", side_effect=failed):
            receipt = module._governed_serial_shard_tests(
                self.root, output, self.identity, {})
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(receipt["validation_failure_phase"],
                         "SHARD_PYTEST_NONZERO")
        self.assertEqual(receipt["failed_test_module"],
                         "tests/test_rc6_ci_parallel_runner.py")
        self.assertNotIn("PRIVATE_PARAMETER", json.dumps(receipt))
        self.assertEqual(json.loads(output.read_text()), receipt)

    def test_governed_wrapper_rejects_pilot_without_complete_retired_quorum(self):
        module = importlib.import_module("scripts.porota_ci")
        output = self.root / "receipt.json"
        proof = {"status": "GREEN_QUORUM_OFFLINE_ONLY",
                 "source_sha": self.identity["source_sha"],
                 "source_tree_sha": self.identity["source_tree"],
                 "shards": 16, "execution_workers": 1,
                 "ci_activated": False, "automatic_go": False,
                 "runtime_authority": False, "discovered": 16, "executed": 15,
                 "globally_discovered": 17, "retired": ["test_retired.py::test_old"],
                 "retired_testcases": 1,
                 "collection_sha256": "a" * 64}
        with patch("scripts.rc6_ci_parallel_runner.run", return_value=proof):
            result = module._governed_serial_shard_tests(
                self.root, output, self.identity,
                {"test_retired.py::test_old": {"reason": "fixture"}})
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["runner_phase"], "GOVERNED_SHARD_QUORUM_REJECTED")

    def test_governed_wrapper_only_promotes_exact_serial_quorum_to_code_pass(self):
        module = importlib.import_module("scripts.porota_ci")
        output = self.root / "receipt.json"
        proof = {"status": "GREEN_QUORUM_OFFLINE_ONLY",
                 "source_sha": self.identity["source_sha"],
                 "source_tree_sha": self.identity["source_tree"],
                 "shards": 16, "execution_workers": 1,
                 "ci_activated": False, "automatic_go": False,
                 "runtime_authority": False, "discovered": 16, "executed": 16,
                 "globally_discovered": 16, "retired": [],
                 "retired_testcases": 0,
                 "collection_sha256": "a" * 64}
        with patch("scripts.rc6_ci_parallel_runner.run", return_value=proof) as pilot:
            result = module._governed_serial_shard_tests(
                self.root, output, self.identity, {})
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["runner_mode"], "GOVERNED_EXACT_SERIAL_SHARDS")
        self.assertEqual((result["collected"], result["executed"]), (16, 16))
        self.assertFalse(result["automatic_deploy"])
        self.assertEqual(pilot.call_args.kwargs["max_workers"], 1)
        self.assertEqual(pilot.call_args.kwargs["shard_count"], 16)

    def test_governed_quorum_rejects_wrong_parametrized_retirement_count(self):
        module = importlib.import_module("scripts.porota_ci")
        output = self.root / "receipt.json"
        proof = {"status": "GREEN_QUORUM_OFFLINE_ONLY",
                 "source_sha": self.identity["source_sha"],
                 "source_tree_sha": self.identity["source_tree"],
                 "shards": 16, "execution_workers": 1,
                 "ci_activated": False, "automatic_go": False,
                 "runtime_authority": False, "discovered": 16, "executed": 16,
                 "globally_discovered": 19,
                 "retired": ["test_retired.py::test_old"], "retired_testcases": 1,
                 "collection_sha256": "a" * 64}
        with patch("scripts.rc6_ci_parallel_runner.run", return_value=proof):
            receipt = module._governed_serial_shard_tests(
                self.root, output, self.identity,
                {"test_retired.py::test_old": {"reason": "fixture"}})
        self.assertEqual(receipt["status"], "FAIL")

    def test_missing_pytest_final_report_is_sanitized_fail_not_missing_file(self):
        module = importlib.import_module("scripts.porota_ci")
        output = self.root / "receipt.json"
        # Models subprocess exiting before pytest_sessionfinish; raw stderr is
        # never read or published, and the exit result never becomes PASS.
        with patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess(
                args=["synthetic_pytest"], returncode=17)):
            result = module.run_code_tests(self.root, output, self.identity)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["runner_phase"], "PYTEST_FINAL_RECEIPT_ABSENT")
        self.assertEqual(result["process_exit_class"], "NO_REPORT_NONZERO_EXIT")
        self.assertEqual(result["process_exit_code"], 17)
        self.assertEqual(result["failure_ids"], ["CI_RUNNER_NO_COMPLETION_RECEIPT"])
        self.assertEqual(json.loads(output.read_text()), result)

    def test_policy_failure_still_leaves_red_receipt(self):
        module = importlib.import_module("scripts.porota_ci")
        (self.root / "ops/policy/test-policy.yaml").write_text(
            "test_governance:\n  automatic_discovery_required: false\n"
            "  manual_test_file_allowlist_as_primary_ci: false\n"
            "productive_scope:\n  roots: ['.']\n  exclusions: []\n")
        output = self.root / "receipt.json"
        with self.assertRaises(ValueError):
            module.run_code_tests(self.root, output, self.identity)
        evidence = json.loads(output.read_text())
        self.assertEqual(evidence["status"], "FAIL")
        self.assertEqual(evidence["runner_phase"], "PYTEST_PRECHECK_PENDING")
        self.assertEqual(evidence["failure_ids"], ["CI_RUNNER_NO_COMPLETION_RECEIPT"])

    def test_image_identity_precheck_writes_fail_receipt_before_test_runner(self):
        module = importlib.import_module("scripts.porota_ci")
        output = self.root / "image-preflight.json"
        args = ["porota_ci.py", "--output", str(output),
                "--source-sha", self.identity["source_sha"],
                "--source-tree", self.identity["source_tree"],
                "--image-id", self.identity["image_id"]]
        with patch.object(sys, "argv", args), patch.dict(os.environ, {
                "POROTA_BUILD_SHA": "0" * 40,
                "POROTA_CANDIDATE_TREE_SHA": self.identity["source_tree"]}):
            with self.assertRaises(ValueError):
                module.main()
        result = json.loads(output.read_text())
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["runner_phase"], "SOURCE_PRECHECK_PENDING")
        self.assertEqual(result["failure_ids"], ["CI_RUNNER_NO_COMPLETION_RECEIPT"])

    def test_missing_report_after_zero_exit_never_becomes_success(self):
        module = importlib.import_module("scripts.porota_ci")
        output = self.root / "receipt.json"
        with patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess(
                args=["synthetic_pytest"], returncode=0)):
            result = module.run_code_tests(self.root, output, self.identity)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["process_exit_class"], "NO_REPORT_ZERO_EXIT")
        self.assertEqual(result["executed"], 0)
        self.assertEqual(result["errors"], 1)

    def test_newly_added_test_is_automatically_executed(self):
        (self.root / "test_example.py").write_text("def test_example():\n    assert 2 + 2 == 4\n")
        (self.root / "test_new.py").write_text("def test_new():\n    assert 'paper'.upper() == 'PAPER'\n")
        report = self.run_suite()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual((report["collected"], report["executed"]), (2, 2))

    def test_failed_application_test_blocks_release(self):
        (self.root / "test_broken.py").write_text("def test_application():\n    assert False\n")
        report = self.run_suite()
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["failed"], 1)

    def subprocess_suite(self):
        source = Path(__file__).resolve().parents[1]
        program = '''import json,sys
from scripts.porota_ci import run_code_tests
report=run_code_tests(sys.argv[1],sys.argv[2],json.loads(sys.argv[3]))
print(json.dumps(report,sort_keys=True))
raise SystemExit(0 if report['status']=='PASS' else 1)
'''
        result = subprocess.run(
            [sys.executable, '-B', '-c', program, str(self.root), str(self.root/'receipt.json'),
             json.dumps(self.identity)], cwd=self.root,
            env={**os.environ, 'PYTHONPATH': str(source), 'PYTHONDONTWRITEBYTECODE': '1'},
            capture_output=True, text=True, timeout=30)
        return result, json.loads((self.root/'receipt.json').read_text())

    def test_real_failure_reports_source_line_without_assertion_or_parameter_values(self):
        secret = 'POROTA_SYNTHETIC_SECRET_7d3ab8'
        (self.root/'test_private.py').write_text(
            'import pytest\n'
            f'@pytest.mark.parametrize("value", ["{secret}"], ids=["{secret}"])\n'
            'def test_application(value):\n'
            f'    assert False, "{secret}"\n')
        result, report = self.subprocess_suite()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(report['failure_ids'], ['test_private.py::test_application'])
        self.assertEqual(report['failure_locations'], [{
            'nodeid': 'test_private.py::test_application', 'phase': 'call',
            'path': 'test_private.py', 'lineno': 4, 'exception_type': 'AssertionError'}])
        for output in (result.stdout, result.stderr, json.dumps(report)):
            self.assertNotIn(secret, output)
            self.assertNotIn(str(self.root), output)
        self.assertEqual(json.loads(result.stdout), report)
        self.assertEqual(result.stderr, '')

    def test_setup_teardown_and_collection_locations_remain_sanitized(self):
        secret = 'POROTA_SYNTHETIC_SECRET_c91e4f'
        cases = (
            ('setup', 'ValueError', 4, 'import pytest\n@pytest.fixture(autouse=True)\n'
             f'def broken():\n    raise ValueError("{secret}")\n'
             'def test_application():\n    pass\n'),
            ('teardown', 'RuntimeError', 5, 'import pytest\n@pytest.fixture(autouse=True)\n'
             f'def broken():\n    yield\n    raise RuntimeError("{secret}")\n'
             'def test_application():\n    pass\n'),
            ('collect', 'ImportError', 1, f'raise ImportError("{secret}")\n'),
            ('collect', 'SyntaxError', 1, 'def broken(:\n    pass\n'),
        )
        for phase, exception, line, code in cases:
            with self.subTest(phase=phase):
                (self.root/'test_error.py').write_text(code)
                result, report = self.subprocess_suite()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(report['status'], 'FAIL')
                self.assertEqual(report['failure_locations'], [{
                    'nodeid': ('test_error.py' if phase == 'collect' else
                               'test_error.py::test_application'),
                    'phase': phase, 'path': 'test_error.py', 'lineno': line,
                    'exception_type': exception}])
                self.assertNotIn(secret, result.stdout + result.stderr + json.dumps(report))

    def test_location_rejects_foreign_paths_and_unsafe_exception_identifiers(self):
        module = importlib.import_module('scripts.porota_ci')
        path = self.root/'test_example.py'
        path.write_text('def test_example():\n    pass\n')
        location = module._failure_location(
            self.root, 'test_example.py::test_example[private-value]', 'call', path, 2,
            'ValueError: private-message')
        self.assertEqual(location, {'nodeid': 'test_example.py::test_example', 'phase': 'call',
                                    'path': 'test_example.py', 'lineno': 2})
        with tempfile.TemporaryDirectory() as external:
            outside = Path(external)/'outside.py'
            outside.write_text('raise ValueError("private-value")\n')
            alias = self.root/'alias.py'
            alias.symlink_to(outside)
            for foreign in (outside, alias):
                self.assertIsNone(module._failure_location(
                    self.root, 'test_example.py::test_example', 'call', foreign, 1, 'ValueError'))

    def test_skipped_test_is_not_reported_as_pass(self):
        (self.root / "test_skip.py").write_text("import pytest\ndef test_application():\n    pytest.skip('missing dependency')\n")
        report = self.run_suite()
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["skipped"], 1)

    def test_retirement_is_exact_and_does_not_remove_application_neighbors(self):
        (self.root / "test_old.py").write_text("def test_old_workflow():\n    assert False\ndef test_application():\n    assert True\n")
        (self.root / "test_new.py").write_text("def test_semantic_guard():\n    assert True\n")
        retirement = [{"nodeid": "test_old.py::test_old_workflow", "reason": "Retired workflow contract",
                       "successor": "test_new.py", "owner": "CI"}]
        (self.root / "ops/policy/retired-release-tests.json").write_text(json.dumps(retirement))
        report = self.run_suite()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["executed"], 2)
        self.assertEqual(report["retired"], ["test_old.py::test_old_workflow"])

    def test_nonexistent_retirement_cannot_silently_exclude_new_tests(self):
        (self.root / "test_new.py").write_text("def test_application():\n    assert True\n")
        (self.root / "ops/policy/retired-release-tests.json").write_text(json.dumps([
            {"nodeid": "test_new.py::test_nonexistent", "reason": "Old contract", "successor": "test_new.py", "owner": "CI"}]))
        report = self.run_suite()
        self.assertEqual(report["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
