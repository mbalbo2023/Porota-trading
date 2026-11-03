"""A172 can stop only the exact running A171 failure while PAPER stays NO_GO."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import sqlite3

import pytest

from scripts import rc6_deploy_quiescence as q
from scripts import rc6_deploy_scheduler_suspend as scheduler
from rc6_paper_fixture_contracts import create_empty_family_parent_ledger


NEXT='f'*40
NOW=datetime(2026,10,28,22,0,tzinfo=timezone.utc)


class Clock(datetime):
    @classmethod
    def now(cls,tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.fixture
def running_a171(tmp_path,monkeypatch):
    root=tmp_path/'porota'
    folder=root/'data/deploy'
    folder.mkdir(parents=True)
    db=root/'data/paper_v17/observer_v17.db'
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER,process_state TEXT,session_state TEXT,heartbeat_at TEXT)')
        connection.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0,'WAITING_MARKET','MARKET_CLOSED',?)",(NOW.isoformat(),))
        for table in ('paper_positions','paper_cauciones','paper_future_positions'):
            connection.execute('CREATE TABLE '+table+'(status TEXT)')
        create_empty_family_parent_ledger(connection)
    monkeypatch.setattr(q,'ROOT',root)
    monkeypatch.setattr(q,'datetime',Clock)
    monkeypatch.setattr(q,'critical_admission',lambda:{'capability':'issues_only','status':'healthy'})
    operator=type('Operator',(),{'exact_a151_operator_preopen_pause':lambda:('e'*64,'A151-40686116017-1'),
                                 'operator_preopen_timer_disabled':lambda:True})
    monkeypatch.setattr(q,'candidate_operator_module',lambda **kw:operator)
    (root/'scripts').mkdir()
    (root/'scripts/rc6_deploy_quiescence.py').write_bytes(b'A171 exact installed source')
    monkeypatch.setattr(q,'A171_INSTALLED_QUIESCENCE_SHA256',hashlib.sha256(b'A171 exact installed source').hexdigest())
    states={name:{'Id':q.A171_RUNNING_IDS[name],'Image':q.A171_LOADED_IMAGE_ID,
                  'State':{'Status':'running','StartedAt':'2026-10-28T21:00:00Z',
                           'FinishedAt':'2026-10-27T20:00:00Z','ExitCode':0,'OOMKilled':False}}
            for name in q.OWNED}
    monkeypatch.setattr(q,'inspect',lambda name:json.loads(json.dumps(states[name])))
    frozen={'candidate_sha':q.A171_FAILED_CANDIDATE,
            'candidate_tree_sha':q.A171_CANDIDATE_TREE_SHA,
            'image_tar_sha256':'d'*64,'image_id':'sha256:'+'a'*64}
    raw=json.dumps(frozen,sort_keys=True).encode()
    (folder/'porota-frozen-candidate.json').write_bytes(raw)
    marker={'schema_version':2,'recorded_at':'2026-10-28T21:35:00+00:00',
            'candidate_sha':q.A171_FAILED_CANDIDATE,'deploy_sha':q.A171_DEPLOY_SHA,
            'image_id':q.A171_LOADED_IMAGE_ID,'validation_status':'DEPLOYED_VALIDATION_PENDING',
            'mode':'PRODUCTION_PAPER','real_orders_sent':0,'real_order_routes':'NOT_CALLED',
            'ppi_watch_untouched':True}
    (folder/'CURRENT_STATE_V2.json').write_text(json.dumps(marker))
    book={'source_sha':q.A171_FAILED_CANDIDATE,'real_orders_sent':0,
          'process_state':'WAITING_MARKET','session_state':'MARKET_CLOSED'}
    prior={'candidate_sha':q.A171_FAILED_CANDIDATE,
           'status':'FIX_FORWARD_VALIDATION_REQUIRED','installation_started':True,
           'a143_pending_adopted':True,
           'a169_origin_candidate':q.A169_PRIOR_CANDIDATE,
           'a169_origin_sha256':q.A169_PRIOR_RECEIPT_SHA256,
           'a170_origin_candidate':q.A170_FAILED_CANDIDATE,
           'a170_origin_sha256':q.A170_FAILED_RECEIPT_SHA256,
           'inflight':'ABSENT_OWNED_RUNTIME_STOPPED',
           'runtime_health':'NEW_A143_CANDIDATE_NOT_GO',
           'market_open_suspended':False,'open_paper_carryover':False,
           'paused_postclose':False,'intraday_caucion':False,'pending_caucion':False,
           'spot_carryover':None,'caucion_carryover':None,
           'real_orders_sent':0,'ppi_watch_accessed':False,
           'a143_previous_proof':{'operator_hold':{'admission':q.A151_HELD_OPEN_MODE,
                   'business_day_art':'2026-10-28','paper_execution_authorized':False,
                   'operator_receipt_sha256':'e'*64,
                   'operator_operation_id':'A151-40686116017-1'}},
           'candidate_image_intent':{'image_id':q.A171_LOADED_IMAGE_ID,
                   'manifest_sha256':hashlib.sha256(raw).hexdigest(),
                   'frozen_manifest_b64':base64.b64encode(raw).decode(),
                   'candidate_tree_sha':q.A171_CANDIDATE_TREE_SHA,
                   'image_tar_sha256':'d'*64,'recorded_at':'2026-10-28T21:30:00+00:00'},
           'candidate_runtime_after_failure':{'status':'running','flat_book':book,
                   'critical_admission':{'capability':'issues_only'},
                   'containers':{name:{'id':states[name]['Id'],'image':states[name]['Image'],
                                       'lifecycle':q.lifecycle(states[name])} for name in q.OWNED}}}
    prior_path=folder/('quiescence-'+q.A171_FAILED_CANDIDATE+'.json')
    prior_path.write_text(json.dumps(prior))
    monkeypatch.setattr(q,'A171_FAILED_RECEIPT_SHA256',hashlib.sha256(prior_path.read_bytes()).hexdigest())
    def cmd(args):
        if args[:3]==['docker','image','inspect']:
            return json.dumps([{'Id':q.A171_LOADED_IMAGE_ID}])
        if args[:2]==['docker','stop']:
            state=states[args[-1]]['State']
            state.update(Status='exited',FinishedAt='2026-10-28T22:00:00Z')
            return ''
        raise AssertionError(args)
    monkeypatch.setattr(q,'command',cmd)
    return folder,db,states,prior_path


def test_exact_a171_running_drain_install_never_restarts_parent(running_a171):
    folder,_,states,_=running_a171
    proof=q.a171_failed_running_proof(NEXT,require_installed_a171_source=True)
    assert proof['containers'][q.OWNED[0]]['id']==q.A171_RUNNING_IDS[q.OWNED[0]]
    receipt=q.run(NEXT,'drain',allow_a171_running_pending=True)
    assert receipt['status']=='DRAINED' and receipt['a171_origin_sha256']==q.A171_FAILED_RECEIPT_SHA256
    assert receipt['inflight']=='ABSENT_OWNED_RUNTIME_STOPPED'
    assert all(states[name]['State']['Status']=='exited' for name in q.OWNED)
    assert q.run(NEXT,'install',allow_a171_running_pending=True)['status']=='HOST_INSTALLATION_STARTED'
    held=q.run(NEXT,'recover')
    assert held['status']=='FIX_FORWARD_VALIDATION_REQUIRED'
    assert held['prior_a171_stopped_after_install'] is True
    assert all(states[name]['State']['Status']=='exited' for name in q.OWNED)


@pytest.mark.parametrize('change',('prior_receipt','docker_id','marker','source','tag','book','timer','paper'))
def test_a171_tamper_rejects_before_docker_stop(running_a171,monkeypatch,change):
    folder,db,states,prior_path=running_a171
    if change=='prior_receipt': prior_path.write_bytes(prior_path.read_bytes()+b' ')
    if change=='docker_id': states[q.OWNED[0]]['Id']='f'*64
    if change=='marker':
        path=folder/'CURRENT_STATE_V2.json'; row=json.loads(path.read_text())
        row['validation_status']='VALIDATED_RUNTIME'; path.write_text(json.dumps(row))
    if change=='source': (folder.parent.parent/'scripts/rc6_deploy_quiescence.py').write_text('tampered')
    if change=='tag':
        monkeypatch.setattr(q,'command',lambda args:json.dumps([{'Id':'sha256:'+'b'*64}]))
    if change=='book':
        with sqlite3.connect(db) as c:
            c.execute("UPDATE observer_state SET session_state='OPEN'")
    if change=='paper':
        with sqlite3.connect(db) as c:
            c.execute('UPDATE observer_state SET real_orders_sent=1')
    if change=='timer':
        monkeypatch.setattr(q,'candidate_operator_module',lambda **kw:
            type('Operator',(),{'exact_a151_operator_preopen_pause':lambda:('e'*64,'A151-40686116017-1'),
                                'operator_preopen_timer_disabled':lambda:False}))
    with pytest.raises(ValueError,match='QUIESCENCE_'):
        q.run(NEXT,'drain',allow_a171_running_pending=True)
    assert all(states[name]['State']['Status']=='running' for name in q.OWNED)


def test_preinstall_failure_keeps_a171_stopped(running_a171):
    _,_,states,_=running_a171
    q.run(NEXT,'drain',allow_a171_running_pending=True)
    receipt=q.run(NEXT,'recover')
    assert receipt['status']=='PRIOR_A171_UNVALIDATED_HELD_STOPPED'
    assert all(states[name]['State']['Status']=='exited' for name in q.OWNED)
