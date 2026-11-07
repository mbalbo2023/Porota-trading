"""A shared-host preclean must leave every non-owned receipt untouched."""
import os
from pathlib import Path
import shlex
import subprocess

from scripts import rc6_deploy_quiescence as quiescence

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/porota-deploy-v2-promote.yml'


def test_canonical_preclean_clears_retained_receipt_as_unprivileged_user(
        tmp_path, record_testsuite_property):
    # Legacy nodeid retained. A690 removed the old sudo rm of a shared /tmp
    # receipt: only a newly allocated private 0700 scratch may be removed.
    shared = tmp_path / 'shared-sticky-directory'
    shared.mkdir(mode=0o1777)
    shared.chmod(0o1777)
    assert shared.stat().st_mode & 0o7777 == 0o1777
    foreign_receipt = shared / 'other-application-receipt.json'
    foreign_receipt.write_text('FOREIGN_MUST_SURVIVE')
    foreign_receipt.chmod(0o444)
    original = foreign_receipt.read_bytes()

    workflow = WORKFLOW.read_text(encoding='utf-8')
    start = workflow.index('          # A690: /tmp is shared')
    end = workflow.index('          printf \'%s\' "$POLICY_B64" | base64 -d > "$TMP_POLICY"', start)
    script = '\n'.join(line[10:] for line in workflow[start:end].splitlines())
    template = '/tmp/rc6-porota-preclean.XXXXXXXXXX'
    script = script.replace(template,
                            shlex.quote(str(shared / 'rc6-porota-preclean.XXXXXXXXXX')))
    assert '/tmp/porota-predeploy-housekeeping.json' not in workflow
    assert 'sudo ' not in script and 'docker ' not in script
    assert 'rm -rf' not in script
    assert 'trap cleanup_preclean_scratch EXIT' in script
    record_testsuite_property('RC6_PRECLEAN_PRIVILEGE_BOUNDARY', 'PRIVATE_SCRATCH_NO_SUDO')

    result = subprocess.run(['/bin/bash', '-Eeuo', 'pipefail', '-c', script],
                            cwd=shared, env=dict(os.environ),
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr + result.stdout
    assert 'RC6_DISK_PRECLEAN_REMOTE=GREEN' in result.stdout
    assert foreign_receipt.read_bytes() == original
    assert list(shared.glob('rc6-porota-preclean.*')) == []


def test_pretransfer_failure_uses_readonly_accounting_before_any_timer_suspend():
    text = WORKFLOW.read_text()
    start = text.index('- name: Dynamic pre-transfer disk guard and conditional safe cleanup')
    end = text.index('- name: Transfer frozen artifact', start)
    assert 'id: diskguard' in text[start:end]
    start = text.index('- name: Account pre-suspend failure without recovery or timer writes')
    end = text.index('timeout-minutes:', start)
    assert "steps.diskguard.outcome == 'failure'" in text[start:end]
    assert "steps.diskguard.outcome == 'cancelled'" in text[start:end]
    assert "steps.suspend.outcome == 'skipped'" in text[start:end]
    full = text[text.index('- name: Failure-safe host cleanup'):]
    full = full.split('timeout-minutes:', 1)[0]
    assert "steps.diskguard.outcome == 'failure'" not in full


def test_cleanup_before_any_drain_cannot_restart_a_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(quiescence, 'ROOT', tmp_path)
    def forbidden(*args, **kwargs): raise AssertionError('NO_RUNTIME_MUTATION_PERMITTED')
    monkeypatch.setattr(quiescence, 'command', forbidden)
    result = quiescence.run('a'*40, 'recover')
    assert result == {'status': 'NO_DRAIN_RECEIPT'}
