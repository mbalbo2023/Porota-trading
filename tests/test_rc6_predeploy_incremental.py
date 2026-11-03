"""Exercise the actual native step's ordering and failure propagation without Docker."""

import json
import os
from pathlib import Path
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/porota-predeploy-v2.yml'


def _native_step():
    job = yaml.safe_load(WORKFLOW.read_text())['jobs']['artifact-gate']
    return next(step['run'] for step in job['steps']
                if step.get('name') == 'Exact image native sizing and generated catalog proof')


def _run_step(tmp_path, *, fail_session=False):
    fake = tmp_path / 'bin'
    fake.mkdir()
    docker = fake / 'docker'
    docker.write_text('''#!''' + sys.executable + '''
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
name = next((arg for arg in args if arg.startswith('porota-native-')), 'unknown')
label = name.removeprefix('porota-native-').removesuffix('-test')
events = Path(os.environ['RC6_EVENTS'])
def record(event):
    with events.open('a') as output:
        output.write(json.dumps([label, event, time.monotonic()]) + '\\n')
if args[0] == 'create':
    record('create')
    print(name)
elif args[0] == 'start':
    record('start')
    time.sleep({'session': .32, 'cadence-fresh': .22,
                'cadence-legacy': .22}.get(label, .01))
    record('end')
    if label == 'session' and os.getenv('RC6_FAIL_SESSION') == '1':
        sys.exit(7)
elif args[0] == 'inspect':
    print('7|true' if label == 'session' and
          os.getenv('RC6_FAIL_SESSION') == '1' else '0|false')
elif args[0] != 'rm':
    sys.exit(9)
''')
    docker.chmod(0o755)
    env = os.environ | {
        'PATH': str(fake) + os.pathsep + os.environ['PATH'],
        'RUNNER_TEMP': str(tmp_path), 'GITHUB_RUN_ID': 'test',
        'IMAGE': 'synthetic:exact', 'RC6_EVENTS': str(tmp_path/'events.jsonl'),
        'RC6_FAIL_SESSION': '1' if fail_session else '0',
    }
    result = subprocess.run(['bash', '-c', _native_step()], cwd=ROOT, env=env,
                            text=True, capture_output=True, timeout=10)
    events = [json.loads(line) for line in (tmp_path/'events.jsonl').read_text().splitlines()]
    return result, events


def test_session_overlaps_ordered_cadence_and_all_proofs_finish(tmp_path):
    result, events = _run_step(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    times = {(label, event): stamp for label, event, stamp in events}
    assert times['session', 'start'] < times['cadence-fresh', 'end']
    assert times['cadence-fresh', 'end'] <= times['cadence-legacy', 'start']
    assert times['session', 'end'] < times['cadence-legacy', 'end']
    assert 'POROTA_EXACT_IMAGE_NATIVE_SIZING=GREEN' in result.stdout


def test_session_failure_refuses_success_even_when_cadence_passes(tmp_path):
    result, events = _run_step(tmp_path, fail_session=True)
    assert result.returncode != 0
    assert any(row[:2] == ['cadence-legacy', 'end'] for row in events)
    assert 'POROTA_EXACT_IMAGE_NATIVE_SIZING=GREEN' not in result.stdout
