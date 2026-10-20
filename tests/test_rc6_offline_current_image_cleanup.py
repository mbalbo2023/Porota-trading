"""Exercise the stopped-runtime cleanup against an in-memory Docker inventory."""
import fcntl
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import rc6_offline_current_image_cleanup as clean


CURRENT = 'sha256:' + 'c' * 64
STALE = 'sha256:' + 'd' * 64
OLD_SHA = '1' * 40
NEW_SHA = '2' * 40
PREDEPLOY = 'porota-predeploy-v2:' + OLD_SHA
CANDIDATE = 'porota-trading-bot:17.0.0-rc6-candidate-' + OLD_SHA


class Docker:
    def __init__(self):
        self.images = {
            CURRENT: ['porota-predeploy-v2:' + NEW_SHA],
            STALE: [clean.STABLE, PREDEPLOY, CANDIDATE],
        }
        self.refs = {CURRENT}
        self.calls = []
        self.before_remove = None

    def __call__(self, *args):
        self.calls.append(args)
        assert '--force' not in args and 'prune' not in args and 'volume' not in args
        if args == ('ps', '-aq', '--no-trunc'):
            return '\n'.join('a'*64 if ref == CURRENT else 'b'*64 for ref in self.refs)
        if args[0] == 'inspect':
            return json.dumps([{'Image':CURRENT if args[1]=='a'*64 else STALE}])
        if args[:2] == ('image', 'inspect'):
            identity = next((ident for ident, tags in self.images.items()
                             if args[2] == ident or args[2] in tags), None)
            if identity is None: raise ValueError('IMAGE_ABSENT')
            return json.dumps([{'Id':identity,'RepoTags':self.images[identity],
                                'RepoDigests':['porota-trading-bot@sha256:'+'f'*64]
                                if identity == STALE else []}])
        if args[0] == 'tag':
            assert args == ('tag', CURRENT, clean.STABLE)
            self.images[STALE].remove(clean.STABLE)
            self.images[CURRENT].append(clean.STABLE)
            return ''
        if args[:2] == ('image', 'rm'):
            if self.before_remove: self.before_remove()
            assert STALE not in self.refs
            self.images[STALE].remove(args[2])
            if not self.images[STALE]: del self.images[STALE]
            return ''
        raise AssertionError(args)


@pytest.fixture
def host(tmp_path, monkeypatch):
    backend = Docker()
    root = tmp_path/'porota'
    (root/'data/deploy').mkdir(parents=True)
    (root/'data/deploy/.deploy-quiescence.lock').touch()
    monkeypatch.setattr(clean, 'ROOT', root)
    monkeypatch.setattr(clean, 'docker', backend)
    monkeypatch.setattr(clean, 'admission', lambda: {
        'current_image':CURRENT,'candidate_sha':NEW_SHA,'ppi':{},'owned':{},'critical':()})
    monkeypatch.setattr(clean.shutil, 'disk_usage', lambda _: SimpleNamespace(free=1234))
    return backend


def test_retags_current_and_deletes_only_unreferenced_old_aliases(host):
    result = clean.cleanup()
    assert result['status'] == 'GREEN'
    assert result['removed_tags'] == [PREDEPLOY, CANDIDATE]
    assert STALE not in host.images
    assert clean.STABLE in host.images[CURRENT]
    assert clean.cleanup()['stable'] == 'ALREADY_CURRENT'


@pytest.mark.parametrize('alien', ['external:keep', 'ppi-watch:latest'])
def test_foreign_tag_rejects_before_pointer_change(host, alien):
    host.images[STALE].append(alien)
    with pytest.raises(ValueError, match='STALE_NOT_EXCLUSIVE'):
        clean.cleanup()
    assert not any(args[0] == 'tag' for args in host.calls)


def test_container_reference_rejects_before_pointer_change(host):
    host.refs.add(STALE)
    with pytest.raises(ValueError, match='STALE_NOT_EXCLUSIVE'):
        clean.cleanup()
    assert not any(args[0] == 'tag' for args in host.calls)


def test_changed_admission_rejects_before_pointer_change(host, monkeypatch):
    count = 0
    def admission():
        nonlocal count
        count += 1
        return {'current_image':CURRENT,'candidate_sha':NEW_SHA,'ppi':count,'owned':{},'critical':()}
    monkeypatch.setattr(clean, 'admission', admission)
    with pytest.raises(ValueError, match='ADMISSION_CHANGED'):
        clean.cleanup()
    assert not any(args[0] == 'tag' for args in host.calls)


def test_new_reference_before_remove_cannot_be_forced(host):
    host.before_remove = lambda: host.refs.add(STALE)
    with pytest.raises(AssertionError): clean.cleanup()
    assert CANDIDATE in host.images[STALE]


def test_lock_contention_and_symlink_reject_before_docker(host, tmp_path, monkeypatch):
    lock = clean.ROOT/'data/deploy/.deploy-quiescence.lock'
    with lock.open('rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError): clean.cleanup()
    lock.unlink()
    lock.symlink_to(tmp_path/'target')
    with pytest.raises(ValueError, match='LOCK_UNSAFE'): clean.cleanup()
    assert not host.calls


def test_workflow_wires_current_identity_helper():
    import subprocess
    import yaml
    workflow = Path(__file__).resolve().parents[1]/'.github/workflows/porota-deploy-v2-promote.yml'
    step = next(s['run'] for s in yaml.safe_load(workflow.read_text())['jobs']['promote']['steps']
                if s.get('id') == 'diskguard')
    assert '< scripts/rc6_offline_current_image_cleanup.py' in step
    assert '< scripts/rc6_offline_failed_image_cleanup.py' not in step
    assert subprocess.run(['bash','-n'], input=step, text=True, capture_output=True).returncode == 0
