"""Replay failed Deploy #40755524824 before any SSH connection."""
import base64
import gzip
import hashlib
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/porota-deploy-v2-promote.yml'


def test_reproducible_bundle_mtime_zero_requires_gzip_no_name(tmp_path):
    """Both a path and redirected stdin cause GNU gzip exit 2 at mtime 0."""
    source = b'RC6 exact candidate staged A125 bytes\n' * 100
    staged = tmp_path / 'rc6_deploy_scheduler_suspend.py'
    staged.write_bytes(source)
    staged.touch()
    import os
    os.utime(staged, (0, 0))
    for command in (f'gzip -9 -c "{staged}" | base64 -w0',
                    f'gzip -9 -c < "{staged}" | base64 -w0'):
        old = subprocess.run(['bash', '-c', 'set -Eeuo pipefail; ' + command],
                             capture_output=True, text=True, check=False)
        assert old.returncode == 2 and 'timestamp out of range' in old.stderr
    fixed = subprocess.run(
        ['bash', '-c', f'set -Eeuo pipefail; gzip -9 -n -c < "{staged}" | base64 -w0'],
        capture_output=True, text=True, check=False)
    assert fixed.returncode == 0 and fixed.stderr == ''
    assert gzip.decompress(base64.b64decode(fixed.stdout)) == source
    assert hashlib.sha256(gzip.decompress(base64.b64decode(fixed.stdout))).digest() == \
           hashlib.sha256(staged.read_bytes()).digest()


def test_all_candidate_gzip_sources_ignore_reproducible_mtime():
    workflow = WORKFLOW.read_text()
    assert workflow.count('gzip -9 -n -c < ') == 7
    assert 'gzip -9 -c ' not in workflow
    assert 'gzip -9 -n -c < "$STAGE/scripts/rc6_deploy_scheduler_suspend.py"' in workflow


def test_failure_cleanup_stages_a151_operator_and_keeps_payload_cleanup():
    workflow = WORKFLOW.read_text()
    block = workflow.split('      - name: Failure-safe host cleanup', 1)[1].split(
        '      - name: Postdeploy full-session stress', 1)[0]
    assert 'RECOVERY_A125_ENV=' in block
    assert 'RC6_A125_CANDIDATE_SOURCE_GZIP_B64=' in block
    assert 'RC6_A125_CANDIDATE_SOURCE_SHA256=' in block
    assert '--phase cleanup_recover env $RECOVERY_A125_ENV python3 -' in block
    assert 'A151_HELD_STOPPED' in block
    assert block.count('cleanup_payload') >= 3
    assert '--phase cleanup_final timeout 150 python3 -' in block


def test_stopped_state_is_distinct_and_all_guarded_routes_keep_intraday_literal():
    workflow = WORKFLOW.read_text()
    classify = workflow.split('      - name: Classify current flat PAPER session', 1)[1].split(
        '      - name: Capture exact pre-suspend host fingerprint', 1)[0]
    assert "statuses=={'exited'} and state==('STOPPED','BOOT_CALENDAR')" in classify
    assert 'candidate.a169_prior_a143_stopped_proof(' in classify
    assert 'require_installed_a170_source=True)' in classify
    assert "print('A151_HELD_STOPPED')" in classify
    assert 'A151_HELD_STOPPED' in workflow.split('Require exact intraday owner authorization', 1)[1]
    assert "A151_HELD_STOPPED) FLAG='--adopt-a143-stopped-pending --a151-held-open'" in workflow
    assert "A151_HELD_STOPPED) QUIESCENCE_FLAG='--adopt-a143-stopped-pending --a151-held-open'" in workflow
    assert '[ "$RC6_QUIESCENCE_MODE" = A151_HELD_STOPPED ]' in workflow
    assert '[ "${RC6_QUIESCENCE_MODE:-}" = A151_HELD_STOPPED ]' in workflow
