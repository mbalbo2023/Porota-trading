"""A681: regression gates for a droplet also hosting Boca and other Docker apps.

All tests are OFFHOST and never invoke the Docker CLI or mutate runtime files.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from scripts import rc6_disk_housekeeping as housekeeping

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'ops/policy/rc6-disk-housekeeping-v1.json'
PROMOTE = ROOT / '.github/workflows/porota-deploy-v2-promote.yml'


@pytest.fixture
def host(monkeypatch):
    """A fully simulated runtime; any attempt to mutate causes the test to fail."""
    policy = json.loads(POLICY.read_text())
    calls = []
    monkeypatch.setattr(housekeeping, 'disk_free_bytes', lambda _: 4 * 1024**3)
    monkeypatch.setattr(housekeeping, 'inode_free_percent', lambda _: 90)
    monkeypatch.setattr(housekeeping, 'runtime_snapshot', lambda: {'paper': 0})
    monkeypatch.setattr(housekeeping, 'validate_runtime', lambda *_: None)
    monkeypatch.setattr(housekeeping, 'compare_runtime', lambda *_: None)
    monkeypatch.setattr(housekeeping, 'all_backup_plans', lambda _: {})
    monkeypatch.setattr(housekeeping, '_docker_observation', lambda: 'Boca + third app')
    for name in ('_delete_candidate', '_clean_apt', '_vacuum_journal'):
        monkeypatch.setattr(housekeeping, name,
                            lambda *_args, _name=name: calls.append(_name))
    return policy, calls


def test_cleanup_and_auto_never_attempt_global_housekeeping(host):
    policy, calls = host
    for mode in ('cleanup', 'auto'):
        result, code = housekeeping.execute(mode, policy)
        assert code == 0  # disk is above the 2 GiB post-cleanup reserve
        assert result['deleted'] == []
        if result['effective_mode'] == 'cleanup':
            assert result['actions'] == ['SHARED_HOST_GLOBAL_RECLAIM_DISABLED']
        assert not calls


def test_missing_or_relaxed_shared_host_policy_fails_closed(host):
    policy, calls = host
    for altered in ({}, {'enabled': False},
                    {'enabled': True, 'policy': 'PREFIX_ONLY'},
                    {**policy['shared_host'], 'hostwide_apt_cleanup_allowed': True}):
        changed = {**policy, 'shared_host': altered}
        with pytest.raises(RuntimeError, match='SHARED_HOST_CLEANUP_POLICY_NOT_PROVEN'):
            housekeeping.execute('cleanup', changed)
        assert calls == []


def test_direct_global_housekeeping_helpers_fail_closed_even_if_called_out_of_band():
    policy = json.loads(POLICY.read_text())
    row = housekeeping.FileCandidate('/tmp/rc6-boca-data', 1, 0, 'UNTRUSTED')
    for action in (lambda: housekeeping._delete_candidate(row),
                   lambda: housekeeping._clean_apt(policy),
                   lambda: housekeeping._vacuum_journal(policy)):
        with pytest.raises(RuntimeError, match='SHARED_HOST_GLOBAL_RECLAIM_FORBIDDEN'):
            action()


def test_audit_mode_remains_non_mutating_even_if_global_policy_unknown(host):
    policy, calls = host
    changed = {**policy, 'shared_host': {}}
    result, code = housekeeping.execute('audit', changed)
    assert code == 0 and result['effective_mode'] == 'audit'
    assert result['deleted'] == [] and calls == []


def test_shared_host_prefixes_are_not_exclusive_resource_proof(tmp_path):
    policy = json.loads(POLICY.read_text())
    other_system = tmp_path / 'rc6-boca-persist'
    third = tmp_path / 'porota-third-app'
    other_system.mkdir()
    third.mkdir()
    (other_system / 'foreign.db').write_bytes(b'Boca fixture')
    (third / 'foreign.db').write_bytes(b'Other Docker app')
    policy['tmp'].update(root=str(tmp_path), prefixes=['rc6', 'porota'], min_age_days=0)
    assert housekeeping.tmp_candidates(policy, now=10**12) == []
    assert (other_system / 'foreign.db').exists()
    assert (third / 'foreign.db').exists()


def test_active_deploy_candidate_cleanup_stays_bounded_and_auditable():
    steps = yaml.safe_load(PROMOTE.read_text())['jobs']['promote']['steps']
    source = PROMOTE.read_text()
    diskguard = next(step['run'] for step in steps if step.get('id') == 'diskguard')
    assert 'base64 -w0 scripts/rc6_pretransfer_image_cleanup.py' in diskguard
    assert 'RC6_PRECLEAN_OLD_CRITICAL_IMAGE_DEFERRED=' in diskguard
    assert 'sudo -n timeout 120 python3 -c' in diskguard
    assert 'RC6_SHARED_HOST_CANDIDATE_CLEANUP_RC' in source
    assert 'last-deploy-candidate-cleanup.json' in source
    assert '--mode audit --preserve-backups' in source
    assert 'RC6_FINAL_HOSTWIDE_MUTATION=SKIPPED' in source
    assert re.search(r'^\s*sudo -n docker (?:image|builder|buildx|system|volume|network) prune', source, re.M) is None
    assert re.search(r'^\s*sudo -n docker image rm "\$(?:CRIT_IMAGE|CRIT_IMAGE_BEFORE|FROZEN_IMAGE)"', source, re.M) is None
    for step in steps:
        if step.get('shell') == 'bash' and isinstance(step.get('run'), str):
            result = subprocess.run(['bash', '-n'], input=step['run'],
                                    capture_output=True, text=True)
            assert result.returncode == 0, (step.get('name'), result.stderr)


def test_legacy_build_cache_and_dated_manual_workflows_disabled():
    helper = (ROOT / 'ops/rc6_build_cache_safe_cleanup.sh').read_text()
    legacy = (ROOT / '.github/workflows/rc6-ws-ops-preopen-closure-20260930.yml').read_text()
    materialize = yaml.safe_load((ROOT / '.github/workflows/rc6-wave16-materialize-safe-housekeeping-20260909.yml').read_text())
    assert 'CLEANUP_RESULT=SKIPPED_UNPROVEN_EXCLUSIVE_OWNERSHIP' in helper
    assert re.search(r'^\s*sudo -n docker builder prune', helper, re.M) is None
    assert 'RC6_HISTORICAL_SHARED_HOST_CLEANUP=DISABLED_READ_ONLY' in legacy
    assert re.search(r'^\s*sudo -n docker (?:builder|image|buildx|system) prune', legacy, re.M) is None
    assert re.search(r'^\s*find /tmp .* -exec rm', legacy, re.M) is None
    assert materialize['jobs']['materialize']['if'] == '${{ false }}'

def test_a690_preclean_uses_private_owned_scratch_not_shared_fixed_paths():
    """Host-wide /tmp fixed names must never be redirected to or deleted."""
    src = PROMOTE.read_text(encoding='utf-8')
    assert '/tmp/porota-dynamic-deploy-disk-policy.json' not in src
    assert '/tmp/porota-predeploy-housekeeping.json' not in src
    assert 'mktemp -d -- /tmp/rc6-porota-preclean.XXXXXXXXXX' in src
    assert "trap cleanup_preclean_scratch EXIT" in src
    assert "stat -c '%a:%u'" in src
    assert "RC6_PRECLEAN_SCRATCH_ID=\"$(stat -c '%d:%i:%u:%a' -- \"$RC6_PRECLEAN_SCRATCH\")\"" in src
    assert "[ \"$(stat -c '%d:%i:%u:%a' -- \"$RC6_PRECLEAN_SCRATCH\")\" = \"$RC6_PRECLEAN_SCRATCH_ID\" ]" in src
    assert '--json-out "$TMP_HOUSEKEEPING"' in src
    assert 'rmdir -- "$RC6_PRECLEAN_SCRATCH"' in src
    assert 'rm -rf' not in src.split('RC6_PRECLEAN_SCRATCH="$(mktemp', 1)[1].split('\n          PRECLEAN_REMOTE\n', 1)[0]
    assert "RC6_DISK_PRECLEAN_VALIDATION=GREEN_PENDING_PRIVATE_SCRATCH_RELEASE" in src


@pytest.mark.parametrize('candidate_rc,stage_rc,expected_ok', [
    (0, 0, True), (2, 0, False), (0, 2, False), (2, 2, False),
])
def test_a690_failed_cleanup_must_never_emit_false_green(candidate_rc, stage_rc, expected_ok):
    """Execute only the pure bash gate tail; never touch Docker or the droplet."""
    import os
    src = PROMOTE.read_text(encoding='utf-8')
    start = src.index('          # A690: a successful disk reserve')
    end = src.index('          REMOTE', start)
    script = src[start:end]
    env = {**os.environ,
        'RC6_CLEANUP_RC': str(candidate_rc),
        'RC6_STAGE_CLEANUP_RC': str(stage_rc),
        'DISK_AFTER': '9000000000', 'FINAL_INODE_FREE_PERCENT': '90.0',
        'CANDIDATE_SHA': 'a' * 40, 'DEPLOY_SHA': 'b' * 40,
        'RUNTIME_IMAGE_ID': 'sha256:' + 'c' * 64,
        'EXPECTED_IMAGE_ID': 'sha256:' + 'c' * 64}
    out = subprocess.run(['bash', '-eu', '-o', 'pipefail'],
        input=script, capture_output=True, text=True, env=env)
    assert ('RC6_DEPLOY_V2=GREEN|' in out.stdout) is expected_ok
    assert ('RC6_FINAL_HOUSEKEEPING=GREEN|' in out.stdout) is expected_ok
    if expected_ok:
        assert out.returncode == 0
    else:
        assert out.returncode == 79
        assert 'RC6_FINAL_HOUSEKEEPING=DEFERRED_UNVERIFIED|' in out.stdout
        assert 'RC6_DEPLOY_V2=NO_GO_CLEANUP_DEFERRED|' in out.stdout
        assert 'RC6_SHARED_HOST_CLEANUP_DEFERRED_UNVERIFIED' in out.stdout


@pytest.mark.parametrize('invalidate_id,expected_rc', [(False, 0), (True, 65)])
def test_a690_preclean_trap_checks_original_inode_before_any_deletion(invalidate_id, expected_rc):
    """Real private tmp fixture: simulate same-mode path replacement via stale inode ID.

    On mismatch the trap must leave the directory untouched and report RED,
    not delete another app's possible same-UID replacement. No host/Docker.
    """
    import os
    src = PROMOTE.read_text(encoding='utf-8')
    start = src.index('          # A690: /tmp is shared')
    end = src.index('          printf \'%s\' "$POLICY_B64" | base64 -d > "$TMP_POLICY"', start)
    block = '\n'.join(line[10:] if line.startswith('          ') else line
                      for line in src[start:end].splitlines())
    if invalidate_id:
        block += '\nRC6_PRECLEAN_SCRATCH_ID=invalid\n'
    block += '\necho "SCRATCH_PATH=$RC6_PRECLEAN_SCRATCH"\nexit 0\n'
    result = subprocess.run(['bash', '-eu', '-o', 'pipefail'],
                            input=block, capture_output=True, text=True)
    assert result.returncode == expected_rc, result.stderr
    match = re.search(r'^SCRATCH_PATH=(/tmp/rc6-porota-preclean\.[A-Za-z0-9]{10})$',
                      result.stdout, re.M)
    assert match is not None
    directory = Path(match.group(1))
    if invalidate_id:
        assert 'RC6_DISK_PRECLEAN_REMOTE=DEFERRED_UNVERIFIED' in result.stdout
        assert directory.is_dir() and not directory.is_symlink()
        assert directory.stat().st_uid == os.getuid()
        # Test-specific orphan, created by THIS subprocess only; no broad rm.
        directory.rmdir()
    else:
        assert 'RC6_DISK_PRECLEAN_REMOTE=GREEN' in result.stdout
        assert not directory.exists()
