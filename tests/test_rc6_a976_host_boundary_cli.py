"""OFFHOST CLI must reject bypassed hold before CI builds any artifact."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
PROMOTE = ROOT / '.github/workflows/porota-deploy-v2-promote.yml'


def invoke(path):
    return subprocess.run(
        [sys.executable, '-m', 'scripts.rc6_deploy_legacy_host_guard',
         '--workflow', str(path)], cwd=ROOT, capture_output=True,
        text=True, timeout=5, check=False)


def test_cli_reports_hold_not_rootless_permission():
    result = invoke(PROMOTE)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), 'missing executable OFFHOST hold receipt'
    receipt = json.loads(result.stdout)
    assert receipt['status'] == 'BLOQUEADO'
    assert receipt['prehost_exit'] == 79
    assert receipt['rootless_certified'] is False
    assert receipt['host_contact_authorized'] is False


@pytest.mark.parametrize('mutation', ['exit_zero', 'remote_always', 'second_job'])
def test_cli_rejects_real_yaml_bypass(tmp_path, mutation):
    workflow = yaml.safe_load(PROMOTE.read_text())
    steps = workflow['jobs']['promote']['steps']
    if mutation == 'exit_zero':
        steps[1]['run'] = 'exit 0\n'
    elif mutation == 'remote_always':
        next(s for s in steps if s.get('id') == 'host_reconcile')['if'] = '${{ always() }}'
    else:
        workflow['jobs']['bypass'] = deepcopy(workflow['jobs']['promote'])
    path = tmp_path / 'mutated.yml'
    path.write_text(yaml.safe_dump(workflow))
    result = invoke(path)
    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        'status': 'BLOQUEADO', 'guard_validated': False,
        'host_contact_authorized': False,
        'reason': 'LEGACY_HOST_BOUNDARY_REJECTED',
    }
    assert result.stderr == ''


@pytest.mark.parametrize('contents', ['[', 'null', '[]', 'jobs: secret-canary'])
def test_cli_invalid_input_is_red_and_does_not_echo_contents(tmp_path, contents):
    path = tmp_path / 'invalid.yml'
    path.write_text(contents)
    result = invoke(path)
    assert result.returncode == 2
    assert 'secret-canary' not in result.stdout + result.stderr
    assert json.loads(result.stdout)['host_contact_authorized'] is False


def test_cli_missing_file_is_red(tmp_path):
    result = invoke(tmp_path / 'missing.yml')
    assert result.returncode == 2
    assert json.loads(result.stdout)['guard_validated'] is False


def test_predeploy_fast_replay_executes_hold_guard_before_tests():
    workflow = yaml.safe_load((ROOT / '.github/workflows/porota-predeploy-v2.yml').read_text())
    steps = workflow['jobs']['artifact-gate']['steps']
    run = next(s['run'] for s in steps if s.get('id') == 'rc6_early_replay')
    command = 'python -m scripts.rc6_deploy_legacy_host_guard --workflow .github/workflows/porota-deploy-v2-promote.yml'
    assert command in run, 'host boundary guard not wired into fast replay'
    assert run.index(command) < run.index('python -m pytest')
    result = subprocess.run(['bash', '-Eeuo', 'pipefail', '-c', command],
                            cwd=ROOT, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['host_contact_authorized'] is False
