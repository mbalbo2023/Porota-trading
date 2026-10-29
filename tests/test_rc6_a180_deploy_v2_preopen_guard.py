"""A180 regression: both canonical preopen phases require frozen A176 source.

Execute the Python guard embedded in the real GitHub workflow against a
synthetic installed-host manifest and PENDING runtime marker. No host writes.
"""

import base64
import gzip
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import textwrap
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/porota-deploy-v2-promote.yml'
CANDIDATE = 'a' * 40
HELPER = b'# exact frozen A176 helper\nVALUE = 42\n'
STAGED_START = (
    'A176_PREOPEN_SOURCE_B64="$(sudo -n python3 - "$REPO" '
    '"$CANDIDATE_SHA" <<\'PY\'\n'
)


def guard_program():
    workflow = WORKFLOW.read_text(encoding='utf-8')
    assert workflow.count(STAGED_START) == 1
    block = workflow.split(STAGED_START, 1)[1].split('\n          PY\n', 1)[0]
    return textwrap.dedent(block), workflow


def fixture_host(root, *, source=HELPER, marker='DEPLOYED_VALIDATION_PENDING'):
    helper = root / 'scripts/rc6_deploy_quiescence.py'
    helper.parent.mkdir(parents=True)
    helper.write_bytes(source)
    deploy = root / 'data/deploy'
    deploy.mkdir(parents=True)
    row = {
        'path': 'scripts/rc6_deploy_quiescence.py',
        'bytes': len(source),
        'sha256': hashlib.sha256(source).hexdigest(),
    }
    manifest = {
        'schema_version': 1, 'status': 'GREEN', 'bundle_sha256': 'b' * 64,
        'file_count': 1, 'files': [row],
    }
    state = {
        'candidate_sha': CANDIDATE, 'validation_status': marker,
        'mode': 'PRODUCTION_PAPER', 'real_orders_sent': 0,
        'real_order_routes': 'NOT_CALLED',
    }
    (deploy / 'porota-deploy-bundle-v2-manifest.json').write_text(
        json.dumps(manifest), encoding='utf-8')
    (deploy / 'CURRENT_STATE_V2.json').write_text(
        json.dumps(state), encoding='utf-8')
    return helper, manifest, state


def execute_guard(root):
    code, _ = guard_program()
    return subprocess.run(
        [sys.executable, '-B', '-c', code, str(root), CANDIDATE],
        capture_output=True, text=True, timeout=10, check=False)


class DeployV2A180PreopenGuardTests(unittest.TestCase):
    def test_job_budget_covers_each_step_timeout(self):
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))
        job = workflow['jobs']['promote']
        job_budget = job['timeout-minutes']
        step_budgets = [step.get('timeout-minutes', 0) for step in job['steps']]
        self.assertGreater(job_budget, max(step_budgets))
        self.assertLessEqual(job_budget, 360)

    def test_preopen_frozen_source_matches_installed_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture_host(root)
            result = execute_guard(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                gzip.decompress(base64.b64decode(result.stdout.strip())), HELPER)
            self.assertFalse(result.stderr)

    def test_both_phases_inject_same_attested_source_under_sudo(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture_host(root)
            _, workflow = guard_program()
            self.assertEqual(
                workflow.count('          for phase in T_MINUS_45 T_MINUS_10; do'), 1)
            phase = workflow.split(
                '          for phase in T_MINUS_45 T_MINUS_10; do', 1)[1].split(
                    '          done', 1)[0]
            self.assertIn(
                'sudo -n env RC6_A145_QUIESCENCE_GZIP_B64="$A176_PREOPEN_SOURCE_B64" '
                'timeout 300 python3 "$REPO/rc6_preopen.py" --phase "$phase"',
                phase)
            self.assertIn('RC6_PREOPEN_${phase}=RESULT_ACCEPTED', phase)
            self.assertIn('RC6_PREOPEN_A176_FROZEN_SOURCE=GREEN', workflow)
            self.assertEqual(execute_guard(root).returncode, 0)

    def test_missing_tampered_or_wrong_phase_host_evidence_fails_closed(self):
        scenarios = (
            'missing_helper', 'symlink_helper', 'wrong_sha', 'wrong_bytes',
            'missing_entry', 'duplicate_entry', 'manifest_red',
            'missing_manifest', 'wrong_candidate', 'orders_nonzero',
            'marker_validated', 'marker_missing',
        )
        for tamper in scenarios:
            with self.subTest(tamper=tamper), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                helper, manifest, state = fixture_host(root)
                deploy = root / 'data/deploy'
                manifest_file = deploy / 'porota-deploy-bundle-v2-manifest.json'
                marker_file = deploy / 'CURRENT_STATE_V2.json'
                if tamper == 'missing_helper':
                    helper.unlink()
                elif tamper == 'symlink_helper':
                    helper.unlink()
                    helper.symlink_to(marker_file)
                elif tamper == 'wrong_sha':
                    manifest['files'][0]['sha256'] = 'f' * 64
                elif tamper == 'wrong_bytes':
                    manifest['files'][0]['bytes'] += 1
                elif tamper == 'missing_entry':
                    manifest['files'] = []
                    manifest['file_count'] = 0
                elif tamper == 'duplicate_entry':
                    manifest['files'].append(dict(manifest['files'][0]))
                    manifest['file_count'] += 1
                elif tamper == 'manifest_red':
                    manifest['status'] = 'RED'
                elif tamper == 'missing_manifest':
                    manifest_file.unlink()
                elif tamper == 'wrong_candidate':
                    state['candidate_sha'] = 'b' * 40
                elif tamper == 'orders_nonzero':
                    state['real_orders_sent'] = 1
                elif tamper == 'marker_validated':
                    state['validation_status'] = 'VALIDATED_RUNTIME'
                elif tamper == 'marker_missing':
                    marker_file.unlink()
                if tamper != 'missing_manifest':
                    manifest_file.write_text(json.dumps(manifest), encoding='utf-8')
                if tamper != 'marker_missing':
                    marker_file.write_text(json.dumps(state), encoding='utf-8')
                result = execute_guard(root)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(result.stdout.strip())
                self.assertIn('RC6_PREOPEN_A176_', result.stderr)

    def test_preopen_rejects_oversized_env_before_entering_phase(self):
        # Incompressible bytes exceed the per-argument Linux execve limit.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = random.Random(180).randbytes(105000)
            fixture_host(root, source=source)
            result = execute_guard(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('RC6_PREOPEN_A176_ENV_ARGUMENT_BOUND', result.stderr)
            self.assertFalse(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()
