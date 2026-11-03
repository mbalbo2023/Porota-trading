"""Adversaries for candidate-only pretransfer cleanup; Docker is simulated."""
import fcntl
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import rc6_pretransfer_image_cleanup as clean

IMG = 'sha256:' + 'a' * 64
STABLE = 'sha256:' + 'b' * 64
CID = 'c' * 64
TAG = 'porota-predeploy-v2:' + 'd' * 40
ALIAS = 'porota-trading-bot:17.0.0-rc6-candidate-' + 'd' * 40


class Docker:
    def __init__(self):
        self.images = {IMG: ([TAG, ALIAS], []), STABLE: (['porota-trading-bot:17.0.0-rc6'], [])}
        self.refs = {CID: STABLE}
        self.calls = []
        self.fail = None
        self.before_remove = None

    def __call__(self, *args):
        self.calls.append(args)
        assert '-f' not in args and 'prune' not in args and 'volume' not in args
        if self.fail and args[:len(self.fail)] == self.fail:
            raise clean.Rejected('SIMULATED_DOCKER_FAILURE')
        if args == ('image', 'ls', '-aq', '--no-trunc'):
            return '\n'.join(self.images)
        if args == ('ps', '-aq', '--no-trunc'):
            return '\n'.join(self.refs)
        if args[:2] == ('inspect', '--format'):
            return self.refs[args[-1]]
        if args[:2] == ('image', 'inspect'):
            identity = args[-1]; tags, digests = self.images[identity]
            return '|'.join(map(json.dumps, [identity, tags, digests]))
        if args[:2] == ('image', 'rm'):
            if self.before_remove: self.before_remove()
            image = next(key for key, (tags, _) in self.images.items() if args[-1] in tags)
            if image in self.refs.values():
                raise clean.Rejected('DOCKER_IMAGE_FAILED')
            self.images[image][0].remove(args[-1])
            if not self.images[image][0]: del self.images[image]
            return ''
        raise AssertionError(args)


@pytest.fixture
def host(monkeypatch):
    backend = Docker()
    monkeypatch.setattr(clean, 'docker', backend)
    monkeypatch.setattr(clean, 'safety', lambda: ['unchanged'])
    monkeypatch.setattr(clean.shutil, 'disk_usage', lambda _: SimpleNamespace(free=1234))
    return backend


def test_unused_multi_alias_candidate_removed_idempotently(host):
    first = clean.cleanup()
    assert first['status'] == 'GREEN' and first['removed_tags'] == [TAG, ALIAS]
    assert first['runtime_unchanged'] and first['disk_before'] == first['disk_after']
    assert STABLE in host.images and IMG not in host.images
    assert clean.cleanup()['removed_tags'] == []


@pytest.mark.parametrize('tags,digests', [
    ([TAG, 'porota-trading-bot:17.0.0-rc6'], []),
    ([TAG, 'external:keep'], []),
    ([TAG], ['registry.example/porota@sha256:'+'f'*64]),
    ([], []), ([TAG + 'bad'], []), (['ppi-watch:'+ 'd'*40], []),
])
def test_stable_external_digest_dangling_or_invalid_alias_preserved(host, tags, digests):
    host.images[IMG] = (tags, digests)
    assert clean.cleanup()['removed_tags'] == []
    assert IMG in host.images


def test_stopped_container_reference_preserves_candidate(host):
    host.refs['e'*64] = IMG
    assert clean.cleanup()['removed_tags'] == []
    assert ('ps', '-aq', '--no-trunc') in host.calls


@pytest.mark.parametrize('failure', [('ps',), ('inspect',), ('image','inspect')])
def test_unknown_inventory_blocks_before_any_mutation(host, failure):
    host.fail = failure
    with pytest.raises(clean.Rejected): clean.cleanup()
    assert not any(args[:2] == ('image', 'rm') for args in host.calls)


def test_new_owner_between_inventory_and_deletion_blocks(host, monkeypatch):
    original = clean.eligibility; count = 0
    def inspect(image):
        nonlocal count
        count += 1
        if count > 2: host.refs['e'*64] = IMG
        return original(image)
    monkeypatch.setattr(clean, 'eligibility', inspect)
    with pytest.raises(clean.Rejected, match='IMAGE_OWNERSHIP_CHANGED'): clean.cleanup()
    assert TAG in host.images[IMG][0]


def test_docker_last_instant_reference_race_is_never_forced(host):
    host.before_remove = lambda: host.refs.update({'e'*64: IMG})
    with pytest.raises(clean.Rejected): clean.cleanup()
    assert TAG in host.images[IMG][0]


def test_runtime_change_blocks_before_delete(host, monkeypatch):
    values = iter([['original'], ['changed']])
    monkeypatch.setattr(clean, 'safety', lambda: next(values))
    with pytest.raises(clean.Rejected, match='RUNTIME_CHANGED'): clean.cleanup()
    assert TAG in host.images[IMG][0]


def test_live_promoter_lock_blocks_cleanup(tmp_path, host, monkeypatch, capsys):
    lock = tmp_path / 'lock'; monkeypatch.setattr(clean, 'LOCK', lock)
    with lock.open('w') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert clean.main() == 2
    assert json.loads(capsys.readouterr().out)['reason'] == 'PROMOTION_STILL_RUNNING'
    assert host.calls == []


def test_lock_symlink_rejected(tmp_path, host, monkeypatch, capsys):
    target = tmp_path / 'target'; target.touch()
    lock = tmp_path / 'lock'; lock.symlink_to(target)
    monkeypatch.setattr(clean, 'LOCK', lock)
    assert clean.main() == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'RED'
    assert host.calls == []


def test_native_safety_read_only_rejects_real_and_stopped_runtime(tmp_path, monkeypatch):
    import sqlite3
    database = tmp_path / 'observer.db'
    connection = sqlite3.connect(database)
    connection.execute('CREATE TABLE observer_state(id INTEGER, mode TEXT, real_orders_sent INTEGER)')
    connection.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
    connection.commit(); connection.close()
    monkeypatch.setattr(clean, 'DB', database)
    running = True
    monkeypatch.setattr(clean, 'docker', lambda *args: '|'.join(map(json.dumps, [CID, STABLE, running, '2026-10-17T00:00:00Z', 0])))
    assert len(clean.safety()) == 3
    running = False
    with pytest.raises(clean.Rejected, match='RUNTIME_NOT_RUNNING'): clean.safety()
    running = True
    connection = sqlite3.connect(database)
    connection.execute('UPDATE observer_state SET real_orders_sent=1'); connection.commit(); connection.close()
    with pytest.raises(clean.Rejected, match='PAPER_SAFETY_UNVERIFIED'): clean.safety()


def test_exact_workflow_wires_checkout_helper_before_final_disk_gate():
    import subprocess
    import yaml
    workflow = Path(__file__).resolve().parents[1] / '.github/workflows/porota-deploy-v2-promote.yml'
    steps = yaml.safe_load(workflow.read_text())['jobs']['promote']['steps']
    block = next(step['run'] for step in steps if step.get('id') == 'diskguard')
    assert 'base64 -w0 scripts/rc6_pretransfer_image_cleanup.py' in block
    assert 'sudo -n timeout 120 python3 -c' in block
    assert block.index('sudo -n timeout 120 python3 -c') < block.index('RC6_DISK_PRETRANSFER_GATE=GREEN')
    assert 'docker system prune' not in block and 'docker image prune -af' not in block
    assert subprocess.run(['bash','-n'], input=block, capture_output=True, text=True).returncode == 0


def test_partial_removal_error_keeps_inventory_and_reclaimed_measurement(host, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(clean, 'LOCK', tmp_path/'absent')
    original = host.__call__
    def command(*args):
        if args == ('image','rm',ALIAS): raise clean.Rejected('SIMULATED_SECOND_TAG_FAILURE')
        return original(*args)
    monkeypatch.setattr(clean, 'docker', command)
    assert clean.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report['removed_tags'] == [TAG] and report['inventory']
    assert report['disk_before'] == report['disk_after'] and report['status'] == 'RED'


def test_new_external_alias_between_inspections_blocks(host, monkeypatch):
    original = clean.eligibility; count = 0
    def inspect(image):
        nonlocal count
        count += 1
        if count > 2: host.images[IMG][0].append('external:keep')
        return original(image)
    monkeypatch.setattr(clean, 'eligibility', inspect)
    with pytest.raises(clean.Rejected, match='IMAGE_OWNERSHIP_CHANGED'): clean.cleanup()
    assert TAG in host.images[IMG][0]


def test_same_repository_digest_does_not_make_unused_candidate_immortal(host):
    host.images[IMG] = ([TAG], ['porota-predeploy-v2@sha256:'+'f'*64])
    result = clean.cleanup()
    assert result['removed_tags'] == [TAG] and IMG not in host.images


def test_unpaired_canonical_repository_digest_still_protects_image(host):
    host.images[IMG] = ([TAG], ['porota-trading-bot@sha256:'+'f'*64])
    assert clean.cleanup()['removed_tags'] == []
    assert IMG in host.images
