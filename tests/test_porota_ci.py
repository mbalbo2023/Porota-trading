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
