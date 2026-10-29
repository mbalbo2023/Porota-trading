"""A180: regress canonical postfinal normal preopen and ambiguous finalizer RO.

Actual embedded workflow Python is exercised offline. No network, Droplet,
GitHub mutations, Docker daemon, or market traffic is used by these tests.
"""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/porota-deploy-v2-promote.yml'
CANDIDATE = 'a' * 40
DEPLOY = 'b' * 40
TREE = 'c' * 40
IMAGE = 'sha256:' + 'd' * 64


def workflow_steps():
    steps = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))['jobs']['promote']['steps']
    return steps, {step.get('id', step['name']): step for step in steps}


def embedded_python(start, end, source):
    assert source.count(start) == 1, 'ambiguous workflow Python extraction: ' + start
    return textwrap.dedent(source.split(start, 1)[1].split(end, 1)[0])


def postfinal_parser():
    steps, by_name = workflow_steps()
    source = by_name['postfinal_unstaged']['run']
    start = "printf '%s\\n' \"$POSTFINAL_OUTPUT\" | python3 -c '\n"
    end = "\n' \"$CANDIDATE_SHA\" \"$DEPLOY_SHA\" \"$CANDIDATE_TREE\""
    return embedded_python(start, end, source)


def marker_guard():
    _, by_name = workflow_steps()
    source = by_name['postfinal_unstaged']['run']
    start = 'python3 - "$REPO" "$CANDIDATE_SHA" "$DEPLOY_SHA" "$CANDIDATE_TREE" <<\'PY\'\n'
    return embedded_python(start, '\nPY\n', source)


def finalizer_classifier():
    _, by_name = workflow_steps()
    source = by_name['Failure-safe host cleanup']['run']
    start = '"sudo -n python3 - \'$CANDIDATE_SHA\' \'$DEPLOY_SHA\' \'$GITHUB_RUN_ID\' \'$GITHUB_RUN_ATTEMPT\'" <<\'PY\'\n'
    return embedded_python(start, '\nPY\n', source)


def valid_scoped_receipts():
    result = {
        'schema': 'RC6_POSTFINAL_UNSTAGED_V1', 'candidate_sha': CANDIDATE,
        'deploy_sha': DEPLOY, 'candidate_tree_sha': TREE,
        'validation_status': 'VALIDATED_RUNTIME',
        'mode': 'PRODUCTION_PAPER', 'real_orders_sent': 0,
        'real_order_routes': 'NOT_CALLED', 'ppi_watch_untouched': True,
        'staged_helper_used': False, 'host_mutations': 0,
        'phases': ['T_MINUS_45', 'T_MINUS_10'],
    }
    rows = []
    for phase in result['phases']:
        scoped = {
            'schema': 'POROTA_DEPLOY_PREOPEN_RESULT_V1', 'accepted': True,
            'phase': phase, 'preopen_status': 'PAUSED_NO_GO',
            'execution_authorized': False,
        }
        rows.append('RC6_POSTFINAL_PHASE_' + phase + '=' + json.dumps(scoped))
    rows.append('RC6_POSTFINAL_UNSTAGED_V1=' + json.dumps(result))
    return rows


def run_parser(rows):
    return subprocess.run(
        [sys.executable, '-B', '-c', postfinal_parser(), CANDIDATE, DEPLOY, TREE],
        input='\n'.join(rows) + '\n', text=True, capture_output=True,
        timeout=10, check=False)


def host_fixture(root, *, marker='VALIDATED_RUNTIME', orders=0,
                 db_orders=0, frozen_tree=TREE):
    deploy = root / 'data/deploy'
    deploy.mkdir(parents=True)
    state = {
        'candidate_sha': CANDIDATE, 'deploy_sha': DEPLOY,
        'validation_status': marker, 'mode': 'PRODUCTION_PAPER',
        'real_orders_sent': orders, 'real_order_routes': 'NOT_CALLED',
        'ppi_watch_untouched': True, 'image_id': IMAGE,
    }
    (deploy / 'CURRENT_STATE_V2.json').write_text(json.dumps(state), encoding='utf-8')
    (deploy / 'porota-frozen-candidate.json').write_text(json.dumps({
        'candidate_sha': CANDIDATE, 'candidate_tree_sha': frozen_tree,
    }), encoding='utf-8')
    db = root / 'data/paper_v17/observer_v17.db'
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE observer_state (id INTEGER, mode TEXT, real_orders_sent INTEGER)')
        conn.execute('INSERT INTO observer_state VALUES (1,?,?)', ('PRODUCTION_PAPER', db_orders))
    fake_bin = root / 'fake_bin'
    fake_bin.mkdir()
    fake_docker = fake_bin / 'docker'
    fake_docker.write_text(
        '#!' + sys.executable + '\n'
        'import json,sys\n'
        'assert sys.argv[1] == "inspect"\n'
        'print(json.dumps([{"Image":' + repr(IMAGE) + ',"State":{"Status":"running",'
        '"Health":{"Status":"healthy"}}}]))\n',
        encoding='utf-8')
    fake_docker.chmod(0o755)
    env = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ.get('PATH', ''))
    return env, state


class DeployV2A180PostfinalTests(unittest.TestCase):
    def test_workflow_orders_immutable_finalizer_normal_preopen_then_cleanup(self):
        steps, by_name = workflow_steps()
        ids = [step.get('id') for step in steps]
        self.assertLess(ids.index('finalize_runtime'), ids.index('postfinal_unstaged'))
        self.assertLess(ids.index('postfinal_unstaged'),
                        next(i for i, step in enumerate(steps)
                             if step['name'] == 'Failure-safe host cleanup'))
        postfinal = by_name['postfinal_unstaged']
        self.assertEqual(postfinal['if'], 'success()')
        self.assertGreaterEqual(postfinal['timeout-minutes'], 13)
        self.assertIn('--phase classify bash -s', postfinal['run'])
        self.assertNotIn('--deploy-scheduler-candidate',
                         postfinal['run'].split('for phase in T_MINUS_45 T_MINUS_10; do', 1)[1])
        self.assertEqual(postfinal['run'].count('env -u RC6_A145_QUIESCENCE_GZIP_B64'), 2)
        self.assertIn('"$REPO/rc6_preopen.py" --phase "$phase"', postfinal['run'])
        self.assertIn('"$REPO/scripts/rc6_deploy_preopen_gate.py"', postfinal['run'])
        cleanup = by_name['Failure-safe host cleanup']['run']
        self.assertLess(cleanup.index('RC6_FINALIZER_MARKER_CLASS='),
                        cleanup.index('POROTA_DEPLOY_FAILURE_CLEANUP=START'))
        self.assertIn('NO_PENDING_RECOVERY|NO_HOST_MUTATION', cleanup)
        self.assertNotIn('steps.postfinal_unstaged.outcome',
                         by_name['Failure-safe host cleanup']['if'])

    def test_final_receipts_select_exact_prefix_among_unrelated_lines(self):
        rows = ["RC6_DEPLOY_HOST_MUTEX=HELD", "scheduler: RESTORED"]
        rows.extend(valid_scoped_receipts())
        rows.append('diagnostic: extra logger line')
        result = run_parser(rows)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('RC6_POSTFINAL_UNSTAGED=GREEN|PAPER=0', result.stdout)

    def test_missing_duplicate_and_tampered_receipts_fail_closed(self):
        for scenario in ('missing_phase', 'duplicate_phase', 'foreign_sha',
                         'paper_nonzero', 'phase_not_accepted',
                         'phase_authorized', 'oversized'):
            with self.subTest(scenario=scenario):
                rows = valid_scoped_receipts()
                if scenario == 'missing_phase':
                    rows.pop(0)
                elif scenario == 'duplicate_phase':
                    rows.insert(0, rows[0])
                elif scenario == 'foreign_sha':
                    rows[-1] = rows[-1].replace(CANDIDATE, 'f' * 40)
                elif scenario == 'paper_nonzero':
                    payload = json.loads(rows[-1].split('=', 1)[1])
                    payload['real_orders_sent'] = 1
                    rows[-1] = 'RC6_POSTFINAL_UNSTAGED_V1=' + json.dumps(payload)
                elif scenario == 'phase_not_accepted':
                    payload = json.loads(rows[0].split('=', 1)[1])
                    payload['accepted'] = False
                    rows[0] = 'RC6_POSTFINAL_PHASE_T_MINUS_45=' + json.dumps(payload)
                elif scenario == 'phase_authorized':
                    payload = json.loads(rows[0].split('=', 1)[1])
                    payload['execution_authorized'] = True
                    rows[0] = 'RC6_POSTFINAL_PHASE_T_MINUS_45=' + json.dumps(payload)
                elif scenario == 'oversized':
                    rows.insert(0, 'diagnostic:' + 'x' * 270000)
                result = run_parser(rows)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('RC6_POSTFINAL_UNSTAGED=GREEN', result.stdout)

    def test_host_marker_identity_paper_zero_and_runtime(self):
        for scenario in ('good', 'pending', 'wrong_tree', 'nonzero_marker', 'nonzero_db'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                env, _ = host_fixture(
                    root,
                    marker='DEPLOYED_VALIDATION_PENDING' if scenario == 'pending'
                    else 'VALIDATED_RUNTIME',
                    orders=1 if scenario == 'nonzero_marker' else 0,
                    db_orders=1 if scenario == 'nonzero_db' else 0,
                    frozen_tree='f' * 40 if scenario == 'wrong_tree' else TREE,
                )
                result = subprocess.run(
                    [sys.executable, '-B', '-c', marker_guard(), str(root),
                     CANDIDATE, DEPLOY, TREE], env=env, capture_output=True,
                    text=True, timeout=10, check=False)
                if scenario == 'good':
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('RC6_POSTFINAL_MARKER=VALIDATED_RUNTIME|PAPER=0',
                                  result.stdout)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(result.stdout.strip())

    def test_ambiguous_finalize_classifier_is_read_only_under_existing_mutex(self):
        code = finalizer_classifier()
        self.assertIn('mutex.open_lock(mutex.LOCK_DIR)', code)
        self.assertIn('mutex._read_barrier(mutex.LOCK_DIR)', code)
        self.assertIn("barrier['status'] != 'IDLE'", code)
        self.assertIn('status == \'VALIDATED_RUNTIME\'', code)
        self.assertIn("elif status != 'DEPLOYED_VALIDATION_PENDING'", code)
        self.assertNotIn('mutex._write_barrier', code)
        self.assertNotIn('os.replace(', code)
        self.assertNotIn('os.unlink(', code)


if __name__ == '__main__':
    unittest.main()
