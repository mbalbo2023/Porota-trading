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
