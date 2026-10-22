"""Backup retirement never includes live stores and aborts on manifest drift."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from scripts import rc6_679_backup_suspension as retire


def setup_roots(tmp_path, monkeypatch):
    general = tmp_path / "porota-backups"
    artifact = tmp_path / "artifacts" / "backups"
    legacy = tmp_path / "data" / "backups"
    for root in (general, artifact, legacy):
        root.mkdir(parents=True)
    monkeypatch.setattr(retire, 'PATTERNS', tuple((root, rule) for root, (_, rule)
                                                in zip((general, artifact, legacy), retire.PATTERNS)))
    return general, artifact, legacy


def test_inventory_rejects_an_unclassified_database(tmp_path, monkeypatch):
    general, artifact, _ = setup_roots(tmp_path, monkeypatch)
    (general / 'porota-general-20261021T071616Z.tar.gz').write_bytes(b'backup')
    (artifact / 'observer_v17.db').write_bytes(b'LIVE')
    with pytest.raises(RuntimeError, match='UNEXPECTED_BACKUP_PATH_OR_TYPE'):
        retire.inventory()


def test_plan_drift_aborts_without_unlinking(tmp_path, monkeypatch):
    general, _, _ = setup_roots(tmp_path, monkeypatch)
    item = general / 'porota-general-20261021T071616Z.tar.gz'
    item.write_bytes(b'original')
    candidate = 'a' * 40
    monkeypatch.setattr(retire, 'guard', lambda *_args, **_kwargs: {'image_id': 'same'})
    monkeypatch.setattr(retire, 'open_files', lambda _paths: None)
    path = tmp_path / 'retire-plan.json'
    monkeypatch.setattr(sys, 'argv', ['retire', '--candidate-sha', candidate, '--plan-out', str(path)])
    retire.main()
    plan = json.loads(path.read_text())
    assert plan['files'][0]['path'] == str(item)
    item.write_bytes(b'changed')
    monkeypatch.setattr(sys, 'argv', ['retire', '--candidate-sha', candidate, '--apply-plan', str(path)])
    with pytest.raises(RuntimeError, match='BACKUP_PLAN_DRIFT'):
        retire.main()
    assert item.read_bytes() == b'changed'


def test_apply_guard_accepts_masked_inactive_timer_exit_three(tmp_path, monkeypatch):
    candidate = 'a' * 40
    state = tmp_path / 'data' / 'deploy'
    state.mkdir(parents=True)
    (state / 'CURRENT_STATE_V2.json').write_text(json.dumps({
        'mode': 'PRODUCTION_PAPER', 'real_orders_sent': 0,
        'real_order_routes': 'NOT_CALLED', 'candidate_sha': candidate,
        'validation_status': 'DEPLOYED_VALIDATION_PENDING', 'image_id': 'image'}))
    policy = tmp_path / 'ops' / 'policy'
    policy.mkdir(parents=True)
    (policy / 'rc6-disk-housekeeping-v1.json').write_text(
        json.dumps({'backups': {'suspended': True}}))
    monkeypatch.setattr(retire, 'ROOT', tmp_path)
    import rc6_host_general_backup_job as host
    monkeypatch.setattr(host, 'backups_suspended', lambda: True)
    def command(args, **_kwargs):
        if args[:2] == ('systemctl', 'is-enabled'):
            return SimpleNamespace(returncode=0, stdout='masked\n')
        if args[:2] == ('systemctl', 'is-active'):
            return SimpleNamespace(returncode=3, stdout='inactive\n')
        if args[:2] == ('docker', 'exec'):
            return SimpleNamespace(returncode=0, stdout='')
        if args[:2] == ('docker', 'inspect'):
            return SimpleNamespace(returncode=0, stdout='image\n')
        raise AssertionError(args)
    monkeypatch.setattr(retire.subprocess, 'run', command)
    assert retire.guard(candidate, apply=True)['candidate_sha'] == candidate
    # Retirement precedes the final marker; a completed marker cannot be
    # reused to delete another backup under this one-shot deploy sequence.
    old = json.loads((state / 'CURRENT_STATE_V2.json').read_text())
    old['validation_status'] = 'VALIDATED_RUNTIME'
    (state / 'CURRENT_STATE_V2.json').write_text(json.dumps(old))
    with pytest.raises(RuntimeError, match='CANDIDATE_SHA_CHANGED'):
        retire.guard(candidate, apply=True)


def test_exact_plan_removes_only_classified_files_and_persists_receipt(tmp_path, monkeypatch):
    general, artifact, _ = setup_roots(tmp_path, monkeypatch)
    backup = general / 'porota-general-20261021T071616Z.tar.gz'
    backup.write_bytes(b'backup')
    live = tmp_path / 'observer_v17.db'
    live.write_bytes(b'LIVE')
    deploy = tmp_path / 'data' / 'deploy'
    deploy.mkdir(parents=True)
    candidate = 'a' * 40
    monkeypatch.setattr(retire, 'ROOT', tmp_path)
    monkeypatch.setattr(retire, 'guard', lambda *_args, **_kwargs: {'image_id': 'same'})
    monkeypatch.setattr(retire, 'open_files', lambda _paths: None)
    plan = deploy / 'plan.json'
    monkeypatch.setattr(sys, 'argv', ['retire', '--candidate-sha', candidate, '--plan-out', str(plan)])
    retire.main()
    monkeypatch.setattr(sys, 'argv', ['retire', '--candidate-sha', candidate, '--apply-plan', str(plan)])
    retire.main()
    receipt = json.loads((deploy / f'backup-retire-{candidate}.json').read_text())
    assert (receipt['status'], receipt['count'], receipt['logical_bytes']) == ('DONE', 1, 6)
    assert not backup.exists() and live.read_bytes() == b'LIVE'
    assert retire.inventory() == []
