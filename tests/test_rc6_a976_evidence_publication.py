"""Prevent same-UID pytest files reaching a public Actions artifact."""
from pathlib import Path
import subprocess

import yaml
import pytest


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/rc6-ws-tests-12-current-contract.yml'


def test_publication_block_cannot_be_downgraded_to_green():
    job = yaml.safe_load(WORKFLOW.read_text())['jobs']['full-suite']
    assert 'continue-on-error' not in job
    assert all('continue-on-error' not in step for step in job['steps'])
    gate = next(step for step in job['steps']
                if step.get('id') == 'publication_custody')
    assert 'continue-on-error' not in gate
    assert gate.get('if') == 'always()'


def test_untrusted_pytest_files_have_no_artifact_sink():
    # A recursive upload, even after a sanitizer, exports arbitrary additions
    # made by tests/descendants sharing the Actions runner UID.
    steps = yaml.safe_load(WORKFLOW.read_text())['jobs']['full-suite']['steps']
    assert not [step for step in steps
                if str(step.get('uses', '')).startswith('actions/upload-artifact@')]


@pytest.mark.parametrize('job_name', ['focal', 'full-suite'])
def test_custody_blocks_before_checkout_dependency_hooks_or_pytest(job_name, tmp_path):
    """Moving the gate after candidate execution reopens public-log/token access."""
    job = yaml.safe_load(WORKFLOW.read_text())['jobs'][job_name]
    first = job['steps'][0]
    assert first.get('id') == 'execution_custody', 'custody must precede all actions/code'
    assert first.get('if') == 'always()'
    assert 'continue-on-error' not in job
    assert all('continue-on-error' not in step for step in job['steps'])
    assert 'uses' not in first, 'the HOLD must not load candidate or third-party code'
    # Execute the actual entry gate, not a recreated sanitizer. Environment
    # assertions cannot grant custody; hostile files must not be imported/read.
    marker = 'SYNTHETIC_SECRET_MUST_NOT_REACH_PUBLIC_LOG'
    (tmp_path / 'attestation.json').write_text('{"custody_verified":true}')
    (tmp_path / 'sitecustomize.py').write_text('raise RuntimeError(' + repr(marker) + ')')
    result = subprocess.run(
        ['bash', '--noprofile', '--norc', '-c', first['run']], cwd=tmp_path,
        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(tmp_path),
             'PUBLICATION_CUSTODY_VERIFIED': 'true', 'RC6_QA_CUSTODY_VERIFIED': 'true',
             'GITHUB_TOKEN': marker, 'ACTIONS_RUNTIME_TOKEN': marker},
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 79
    assert result.stderr == ''
    assert result.stdout == (
        'RC6_QA_EXECUTION=BLOQUEADO|reason=INDEPENDENT_CUSTODY_UNVERIFIED|automatic_go=false\n'
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ['attestation.json', 'sitecustomize.py']


def test_always_sanitizer_cannot_execute_candidate_after_custody_hold():
    # A976's always() sanitizer imports repo code even after a failed earlier
    # step. All post-HOLD exceptions must be either the inert publication HOLD
    # or explicitly dependent on successful execution custody.
    jobs = yaml.safe_load(WORKFLOW.read_text())['jobs']
    assert set(jobs) == {'focal', 'full-suite'}
    for job in jobs.values():
        for step in job['steps'][1:]:
            if step.get('id') == 'publication_custody':
                continue
            assert step.get('if') in (
                None, 'success()',
                "${{ always() && steps.execution_custody.outcome == 'success' }}",
            ), 'candidate code must not run on execution custody failure'


@pytest.mark.parametrize('job_name', ['focal', 'full-suite'])
def test_checkout_does_not_leave_credentials_for_candidate(job_name):
    steps = yaml.safe_load(WORKFLOW.read_text())['jobs'][job_name]['steps']
    checkouts = [step for step in steps
                 if str(step.get('uses', '')).startswith('actions/checkout@')]
    assert checkouts
    assert all(step.get('with', {}).get('persist-credentials') is False
               for step in checkouts)


@pytest.mark.parametrize('attack', ['extra_file', 'symlink_file', 'symlink_directory', 'forged_attestation'])
def test_missing_separate_publisher_custody_fails_closed(tmp_path, attack):
    steps = yaml.safe_load(WORKFLOW.read_text())['jobs']['full-suite']['steps']
    gates = [step for step in steps if step.get('id') == 'publication_custody']
    assert len(gates) == 1, 'same-UID publication must be explicitly BLOCKED'
    # Execute the real gate with a forged green claim and injected files.
    (tmp_path / 'attestation.json').write_text('{"automatic_go": true}')
    (tmp_path / 'secret.txt').write_text('SYNTHETIC_SECRET_DO_NOT_EXPORT')
    if attack == 'symlink_file':
        (tmp_path / 'receipt.json').symlink_to(tmp_path / 'secret.txt')
    elif attack == 'symlink_directory':
        (tmp_path / 'sanitized').symlink_to(tmp_path, target_is_directory=True)
    before = sorted(p.name for p in tmp_path.iterdir())
    result = subprocess.run(['bash', '-c', gates[0]['run']], cwd=tmp_path,
                            env={'PATH': '/usr/bin:/bin',
                                 'PUBLICATION_CUSTODY_VERIFIED': 'true'},
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 79
    assert 'SYNTHETIC_SECRET' not in result.stdout + result.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == before
