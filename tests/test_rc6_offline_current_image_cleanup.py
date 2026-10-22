"""Exercise the stopped-runtime cleanup against an in-memory Docker inventory."""
import fcntl
import json
import sqlite3
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


def test_intraday_validated_state_requires_exact_stopped_receipt(tmp_path, monkeypatch):
    from scripts import rc6_deploy_quiescence as drain
    root = tmp_path/'porota'
    folder = root/'data/deploy'
    folder.mkdir(parents=True)
    (folder/'CURRENT_STATE_V2.json').write_text(json.dumps({
        'candidate_sha':NEW_SHA, 'image_id':CURRENT, 'validation_status':'VALIDATED_RUNTIME',
        'mode':'PRODUCTION_PAPER', 'real_orders_sent':0, 'real_order_routes':'NOT_CALLED'}))
    monkeypatch.setattr(clean, 'ROOT', root)
    owned = {name:{'Id':name, 'Image':CURRENT,
                   'State':{'Status':'exited','ExitCode':0}} for name in clean.OWNED}
    owned[clean.CRITICAL] = {'Id':'critical','Image':CURRENT,'RestartCount':0,
        'State':{'Status':'running','Health':{'Status':'healthy'},'OOMKilled':False}}
    monkeypatch.setattr(clean, 'inspect', lambda name:owned[name])
    monkeypatch.setattr(clean, 'docker', lambda *args:'RC6_CRITICAL_ISSUES_ONLY=GREEN')
    monkeypatch.setattr(clean, 'ppi_units', lambda:{})
    carry = {'open_count':4,'sha256':'e'*64}
    (folder/('quiescence-'+OLD_SHA+'.json')).write_text(json.dumps({
        'candidate_sha':OLD_SHA, 'recorded_at':'2026-10-20T16:00:00+00:00',
        'status':'FIX_FORWARD_VALIDATION_REQUIRED','installation_started':True,
        'market_open_suspended':True,'open_paper_carryover':True,
        'containers':{name:{'id':owned[name]['Id'],'image':CURRENT}
                      for name in clean.OWNED}, 'spot_carryover':carry}))
    monkeypatch.setattr(drain, 'exact_clean_stopped', lambda receipt:None)
    monkeypatch.setattr(drain, 'admit', lambda **kwargs:'RUNNING')
    monkeypatch.setattr(drain, 'spot_carryover_snapshot', lambda:carry)
    result = clean.admission()
    assert result['intraday_proof'] == (OLD_SHA, 'e'*64)
    monkeypatch.setattr(drain, 'spot_carryover_snapshot',
                        lambda:dict(carry,sha256='f'*64))
    with pytest.raises(ValueError, match='OFFLINE_CURRENT_CARRYOVER_RED'):
        clean.admission()


def test_closed_flat_validated_state_requires_stopped_paper_zero(tmp_path, monkeypatch):
    from scripts import rc6_deploy_quiescence as drain
    root=tmp_path/'porota';folder=root/'data/deploy';folder.mkdir(parents=True)
    (folder/'CURRENT_STATE_V2.json').write_text(json.dumps({
        'candidate_sha':NEW_SHA,'image_id':CURRENT,'validation_status':'VALIDATED_RUNTIME',
        'mode':'PRODUCTION_PAPER','real_orders_sent':0,'real_order_routes':'NOT_CALLED',
        'recorded_at':'2026-10-22T02:33:14+00:00'}))
    db=root/'data/paper_v17/observer_v17.db';db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE observer_state(id INTEGER,mode TEXT,real_orders_sent INTEGER,process_state TEXT,session_state TEXT)')
        c.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0,'STOPPED','MARKET_CLOSED')")
        for table in ('paper_positions','paper_cauciones','paper_future_positions'):
            c.execute(f'CREATE TABLE {table}(status TEXT)')
    monkeypatch.setattr(clean,'ROOT',root)
    owned={name:{'Id':name,'Image':CURRENT,'State':{'Status':'exited','ExitCode':0}}
           for name in clean.OWNED}
    owned[clean.CRITICAL]={'Id':'critical','Image':CURRENT,'RestartCount':0,
        'State':{'Status':'running','Health':{'Status':'healthy'},'OOMKilled':False}}
    monkeypatch.setattr(clean,'inspect',lambda name:owned[name])
    monkeypatch.setattr(clean,'docker',lambda *args:'RC6_CRITICAL_ISSUES_ONLY=GREEN')
    monkeypatch.setattr(clean,'ppi_units',lambda:{})
    prior={'candidate_sha':OLD_SHA,'recorded_at':'2026-10-22T12:41:17+00:00',
           'status':'FIX_FORWARD_VALIDATION_REQUIRED','installation_started':True,
           'market_open_suspended':False,'open_paper_carryover':False,
           'paused_postclose':False,'intraday_caucion':False,
           'spot_carryover':None,'caucion_carryover':None,
           'containers':{name:{'id':owned[name]['Id'],'image':CURRENT} for name in clean.OWNED}}
    path=folder/('quiescence-'+OLD_SHA+'.json');path.write_text(json.dumps(prior))
    monkeypatch.setattr(drain,'exact_clean_stopped',lambda receipt:None)
    monkeypatch.setattr(drain,'admit',lambda **kwargs:'STOPPED')
    result=clean.admission()
    assert result['flat_stop_proof']==(OLD_SHA,prior['recorded_at'])
    assert result['intraday_proof'] is None
    with sqlite3.connect(db) as c:c.execute("INSERT INTO paper_positions VALUES('OPEN')")
    with pytest.raises(ValueError,match='OFFLINE_CURRENT_FLAT_POSITION_OPEN'):
        clean.admission()
    with sqlite3.connect(db) as c:c.execute('DELETE FROM paper_positions')
    monkeypatch.setattr(drain,'admit',lambda **kwargs:'RUNNING')
    with pytest.raises(ValueError,match='OFFLINE_CURRENT_FLAT_STOP_RED'):
        clean.admission()
    prior['market_open_suspended']=True;path.write_text(json.dumps(prior))
    with pytest.raises(ValueError,match='OFFLINE_CURRENT_EXACT_FAILURE_RECEIPT_MISSING'):
        clean.admission()
