"""Replay 20/10 attached-client timeout: the Docker writer survived 60s kill."""
import json
import sqlite3

import pytest

from scripts import rc6_contract_runner_lifecycle as lifecycle


CANDIDATE='a'*40
IMAGE='sha256:'+'b'*64
ORPHAN_ID='502599734bdce0c41e98bec56f465b156b480f2d005e6c75716804f2e404ce7e'
NAME='porota-deploy-contract-'+CANDIDATE


@pytest.fixture
def host(tmp_path,monkeypatch):
    db=tmp_path/'observer.db'
    c=sqlite3.connect(db)
    c.execute('CREATE TABLE observer_state(id INTEGER,mode TEXT,real_orders_sent INTEGER)')
    c.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
    c.commit();c.close()
    state=tmp_path/'CURRENT_STATE_V2.json'
    state.write_text(json.dumps({'candidate_sha':CANDIDATE,
        'validation_status':'DEPLOYED_VALIDATION_PENDING','image_id':IMAGE,
        'mode':'PRODUCTION_PAPER','real_orders_sent':0}))
    monkeypatch.setattr(lifecycle,'DB',db)
    monkeypatch.setattr(lifecycle,'CURRENT',state)
    records={
        'porota_production_observer':{'Id':'1'*64,'Image':IMAGE,'State':{'Running':False}},
        'porota_production_dashboard':{'Id':'2'*64,'Image':IMAGE,'State':{'Running':True}},
        NAME:{'Id':ORPHAN_ID,'Name':'/'+NAME,'Image':IMAGE,'Path':'python',
              'Args':['ck_contract_evidence_runner_hf6.py'],
              'HostConfig':{'AutoRemove':True,'ReadonlyRootfs':True},
              'State':{'Running':True}},
    }
    calls=[]
    def docker(*args,allow_missing=False):
        calls.append(args)
        if args[0]=='inspect':
            row=records.get(args[1])
            return json.dumps([row]) if row else None
        if args[:3]==('stop','--timeout','5'):
            assert args[3]==ORPHAN_ID
            records.pop(NAME)
            return ORPHAN_ID
        raise AssertionError(args)
    monkeypatch.setattr(lifecycle,'docker',docker)
    return records,calls,state,db


def test_exact_orphan_stopped_before_observer_restart(host):
    records,calls,_,_=host
    result=lifecycle.stop(CANDIDATE)
    assert result['writer']=='STOPPED_EXACT' and result['writer_id']==ORPHAN_ID
    assert NAME not in records and records['porota_production_observer']['State']['Running'] is False
    assert ('stop','--timeout','5',ORPHAN_ID) in calls
    assert lifecycle.stop(CANDIDATE)['writer']=='ABSENT'


@pytest.mark.parametrize('field,value',[
    ('Image','sha256:'+'c'*64),('Path','bash'),('Args',['other.py']),
    ('HostConfig',{'AutoRemove':False,'ReadonlyRootfs':True}),
])
def test_unknown_writer_never_stopped(host,field,value):
    records,calls,_,_=host
    records[NAME][field]=value
    with pytest.raises(RuntimeError,match='CONTRACT_WRITER_IDENTITY_MISMATCH'):
        lifecycle.stop(CANDIDATE)
    assert not any(x[0]=='stop' for x in calls)


def test_paper_or_provisional_identity_change_rejected_before_stop(host):
    _,calls,state,db=host
    payload=json.loads(state.read_text());payload['validation_status']='VALIDATED_RUNTIME'
    state.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError,match='CONTRACT_STATE_MISMATCH'):
        lifecycle.stop(CANDIDATE)
    payload['validation_status']='DEPLOYED_VALIDATION_PENDING';state.write_text(json.dumps(payload))
    c=sqlite3.connect(db);c.execute('UPDATE observer_state SET real_orders_sent=1');c.commit();c.close()
    with pytest.raises(RuntimeError,match='CONTRACT_PAPER_UNVERIFIED'):
        lifecycle.stop(CANDIDATE)
    assert not any(x[0]=='stop' for x in calls)


@pytest.mark.parametrize('inventory,expected', [
    ('porota_production_observer|'+'1'*64, 'ABSENT'),
    (NAME+'|'+ORPHAN_ID, 'CONTRACT_WRITER_INVENTORY_UNVERIFIED'),
    ('BROKEN_INVENTORY', 'CONTRACT_WRITER_INVENTORY_UNVERIFIED'),
])
def test_auto_removed_writer_requires_second_docker_inventory(host, monkeypatch, inventory, expected):
    records, calls, _, _ = host
    records.pop(NAME)
    original = lifecycle.docker
    def inspect_failure(*args, allow_missing=False):
        if args == ('inspect', NAME):
            raise RuntimeError('CONTRACT_DOCKER_INSPECT_FAILED')
        if args == ('ps', '-a', '--no-trunc', '--format', '{{.Names}}|{{.ID}}'):
            return inventory
        return original(*args, allow_missing=allow_missing)
    monkeypatch.setattr(lifecycle, 'docker', inspect_failure)
    if expected == 'ABSENT':
        assert lifecycle.stop(CANDIDATE)['writer'] == 'ABSENT'
    else:
        with pytest.raises(RuntimeError, match=expected): lifecycle.stop(CANDIDATE)
    assert not any(x[0] == 'stop' for x in calls)
