"""A175 may adopt only the exact A174 running PAPER/NO_GO failure."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import sqlite3

import pytest

from scripts import rc6_deploy_quiescence as q
from scripts import rc6_deploy_scheduler_suspend as s
from rc6_paper_fixture_contracts import create_empty_family_parent_ledger

NEXT='f'*40
CLOCK=[datetime(2026,10,28,23,40,tzinfo=timezone.utc)]


class Clock(datetime):
    @classmethod
    def now(cls,tz=None):
        value=CLOCK[0]
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)


@pytest.fixture
def running_a174(tmp_path,monkeypatch):
    CLOCK[0]=datetime(2026,10,28,23,40,tzinfo=timezone.utc)
    root=tmp_path/'porota'
    folder=root/'data/deploy'; folder.mkdir(parents=True)
    db=root/'data/paper_v17/observer_v17.db'; db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER,process_state TEXT,session_state TEXT,heartbeat_at TEXT)')
        c.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0,'WAITING_MARKET','MARKET_CLOSED',?)",(CLOCK[0].isoformat(),))
        for table in ('paper_positions','paper_cauciones','paper_future_positions'):
            c.execute('CREATE TABLE '+table+'(status TEXT)')
        create_empty_family_parent_ledger(c)
    monkeypatch.setattr(q,'ROOT',root)
    monkeypatch.setattr(q,'datetime',Clock)
    monkeypatch.setattr(q,'critical_admission',lambda:{'capability':'issues_only','status':'healthy'})
    operator=type('Operator',(),{
        'exact_a151_operator_preopen_pause':lambda:('e'*64,'A151-40686116017-1'),
        'operator_preopen_timer_disabled':lambda:True})
    monkeypatch.setattr(q,'candidate_operator_module',lambda **kw:operator)
    (root/'scripts').mkdir()
    installed=b'exact A174 installed quiescence source'
    (root/'scripts/rc6_deploy_quiescence.py').write_bytes(installed)
    monkeypatch.setattr(q,'A174_INSTALLED_QUIESCENCE_SHA256',hashlib.sha256(installed).hexdigest())
    states={name:{'Id':q.A174_RUNNING_IDS[name],'Image':q.A174_LOADED_IMAGE_ID,
                  'State':{'Status':'running','StartedAt':'2026-10-28T23:14:28Z',
                           'FinishedAt':'2026-10-28T23:12:08Z','ExitCode':0,'OOMKilled':False}}
            for name in q.OWNED}
    monkeypatch.setattr(q,'inspect',lambda name:json.loads(json.dumps(states[name])))
    frozen={'candidate_sha':q.A174_FAILED_CANDIDATE,
            'candidate_tree_sha':q.A174_CANDIDATE_TREE_SHA,
            'image_tar_sha256':'d'*64,'image_id':'sha256:'+'a'*64}
    raw=json.dumps(frozen,sort_keys=True).encode()
    (folder/'porota-frozen-candidate.json').write_bytes(raw)
    monkeypatch.setattr(q,'A174_FROZEN_SHA256',hashlib.sha256(raw).hexdigest())
    marker={'schema_version':2,'recorded_at':'2026-10-28T23:20:00+00:00',
            'candidate_sha':q.A174_FAILED_CANDIDATE,'deploy_sha':q.A174_DEPLOY_SHA,
            'image_id':q.A174_LOADED_IMAGE_ID,'validation_status':'DEPLOYED_VALIDATION_PENDING',
            'mode':'PRODUCTION_PAPER','real_orders_sent':0,'real_order_routes':'NOT_CALLED',
            'ppi_watch_untouched':True}
    marker_path=folder/'CURRENT_STATE_V2.json'
    marker_path.write_text(json.dumps(marker))
    monkeypatch.setattr(q,'A174_MARKER_SHA256',hashlib.sha256(marker_path.read_bytes()).hexdigest())
    failure_book={'source_sha':q.A174_FAILED_CANDIDATE,'real_orders_sent':0,
                  'process_state':'DEGRADED','session_state':'CLOSED'}
    prior={'candidate_sha':q.A174_FAILED_CANDIDATE,
           'status':'FIX_FORWARD_VALIDATION_REQUIRED','installation_started':True,
           'a171_pending_adopted':True,
           'a171_origin_candidate':q.A171_FAILED_CANDIDATE,
           'a171_origin_sha256':q.A171_FAILED_RECEIPT_SHA256,
           'a171_previous_proof':{'origin_candidate':q.A171_FAILED_CANDIDATE,
                'origin_sha256':q.A171_FAILED_RECEIPT_SHA256,
                'operator_receipt_sha256':'e'*64,
                'operator_operation_id':'A151-40686116017-1'},
           'inflight':'ABSENT_OWNED_RUNTIME_STOPPED',
           'runtime_health':'NEW_A172_CANDIDATE_NOT_GO',
           'market_open_suspended':False,'open_paper_carryover':False,
           'paused_postclose':False,'intraday_caucion':False,'pending_caucion':False,
           'spot_carryover':None,'caucion_carryover':None,
           'real_orders_sent':0,'ppi_watch_accessed':False,
           'candidate_image_intent':{'image_id':q.A174_LOADED_IMAGE_ID,
                'manifest_sha256':hashlib.sha256(raw).hexdigest(),
                'frozen_manifest_b64':base64.b64encode(raw).decode(),
                'candidate_tree_sha':q.A174_CANDIDATE_TREE_SHA,
                'image_tar_sha256':'d'*64,'recorded_at':'2026-10-28T23:11:00+00:00'},
           'candidate_runtime_after_failure':{'status':'running','flat_book':failure_book,
                'critical_admission':{'capability':'issues_only'},
                'containers':{name:{'id':states[name]['Id'],'image':states[name]['Image'],
                                    'lifecycle':q.lifecycle(states[name])} for name in q.OWNED}}}
    prior_path=folder/('quiescence-'+q.A174_FAILED_CANDIDATE+'.json')
    prior_path.write_text(json.dumps(prior))
    monkeypatch.setattr(q,'A174_FAILED_RECEIPT_SHA256',hashlib.sha256(prior_path.read_bytes()).hexdigest())
    commands=[]
    def cmd(args):
        commands.append(args)
        if args[:3]==['docker','image','inspect']:
            return json.dumps([{'Id':q.A174_LOADED_IMAGE_ID}])
        if args[:2]==['docker','stop']:
            state=states[args[-1]]['State']
            state.update(Status='exited',FinishedAt='2026-10-28T23:40:00Z')
            return ''
        raise AssertionError(args)
    monkeypatch.setattr(q,'command',cmd)
    return folder,db,states,prior_path,marker_path,commands


def test_exact_a174_failure_and_current_recovered_book_drain_install(running_a174):
    folder,_,states,_,_,commands=running_a174
    proof=q.a174_failed_running_proof(NEXT,require_installed_a174_source=True)
    assert proof['failure_flat_book']=={'source_sha':q.A174_FAILED_CANDIDATE,
        'real_orders_sent':0,'process_state':'DEGRADED','session_state':'CLOSED'}
    assert proof['flat_book']=={'source_sha':q.A174_FAILED_CANDIDATE,
        'real_orders_sent':0,'process_state':'WAITING_MARKET','session_state':'MARKET_CLOSED'}
    receipt=q.run(NEXT,'drain',allow_a174_running_pending=True)
    assert receipt['status']=='DRAINED'
    assert receipt['inflight']=='ABSENT_OWNED_RUNTIME_STOPPED'
    assert receipt['a174_origin_sha256']==q.A174_FAILED_RECEIPT_SHA256
    assert receipt['a174_previous_proof']==proof
    assert all(states[name]['State']['Status']=='exited' for name in q.OWNED)
    assert q.run(NEXT,'install',allow_a174_running_pending=True)['status']=='HOST_INSTALLATION_STARTED'
    failed=q.run(NEXT,'recover')
    assert failed['status']=='FIX_FORWARD_VALIDATION_REQUIRED'
    assert failed['prior_a174_stopped_after_install'] is True
    assert not any(args[:2]==['docker','start'] for args in commands)
    assert (folder/('quiescence-'+NEXT+'.json')).exists()


@pytest.mark.parametrize('change',('receipt','docker_id','image','marker','source','tag',
                                   'book','paper','timer','future_position','heartbeat'))
def test_a174_tamper_rejects_before_any_docker_stop(running_a174,monkeypatch,change):
    folder,db,states,prior_path,marker_path,commands=running_a174
    if change=='receipt':prior_path.write_bytes(prior_path.read_bytes()+b' ')
    if change=='docker_id':states[q.OWNED[0]]['Id']='a'*64
    if change=='image':states[q.OWNED[0]]['Image']='sha256:'+'b'*64
    if change=='marker':
        row=json.loads(marker_path.read_text());row['validation_status']='VALIDATED_RUNTIME'
        marker_path.write_text(json.dumps(row))
    if change=='source':(folder.parent.parent/'scripts/rc6_deploy_quiescence.py').write_text('tampered')
    if change=='tag':
        monkeypatch.setattr(q,'command',lambda args:json.dumps([{'Id':'sha256:'+'b'*64}]))
    if change in {'book','paper','future_position','heartbeat'}:
        with sqlite3.connect(db) as c:
            if change=='book':c.execute("UPDATE observer_state SET session_state='OPEN'")
            if change=='paper':c.execute('UPDATE observer_state SET real_orders_sent=1')
            if change=='future_position':c.execute("INSERT INTO paper_future_positions VALUES('ACTIVE')")
            if change=='heartbeat':c.execute("UPDATE observer_state SET heartbeat_at='2026-10-27T00:00:00+00:00'")
    if change=='timer':
        monkeypatch.setattr(q,'candidate_operator_module',lambda **kw:
            type('Operator',(),{
                'exact_a151_operator_preopen_pause':lambda:('e'*64,'A151-40686116017-1'),
                'operator_preopen_timer_disabled':lambda:False}))
    with pytest.raises(ValueError,match='QUIESCENCE_'):
        q.run(NEXT,'drain',allow_a174_running_pending=True)
    assert not any(args[:2]==['docker','stop'] for args in commands)
    assert all(states[name]['State']['Status']=='running' for name in q.OWNED)


def test_a174_clock_boundary_requires_fresh_daily_no_go(running_a174):
    _,db,_,_,_,_=running_a174
    CLOCK[0]=datetime(2026,10,29,2,59,tzinfo=timezone.utc) # 23:59 ART on 28th
    with sqlite3.connect(db) as c:
        c.execute('UPDATE observer_state SET heartbeat_at=?',(CLOCK[0].isoformat(),))
    assert q.a174_failed_running_proof(NEXT)['flat_book']['real_orders_sent']==0
    CLOCK[0]=datetime(2026,10,29,3,0,tzinfo=timezone.utc) # 00:00 ART on 29th
    with pytest.raises(ValueError,match='QUIESCENCE_A174_A151_DATED_HOLD_EXPIRED'):
        q.a174_failed_running_proof(NEXT)


def test_a174_preinstall_failure_keeps_failed_pair_stopped(running_a174):
    _,_,states,_,_,commands=running_a174
    q.run(NEXT,'drain',allow_a174_running_pending=True)
    held=q.run(NEXT,'recover')
    assert held['status']=='PRIOR_A174_UNVALIDATED_HELD_STOPPED'
    assert all(states[name]['State']['Status']=='exited' for name in q.OWNED)
    assert not any(args[:2]==['docker','start'] for args in commands)


def test_scheduler_a174_exact_held_chain_and_zero_mutation(running_a174,monkeypatch,tmp_path):
    folder,_,states,_,_,commands=running_a174
    monkeypatch.setattr(s,'ROOT',folder)
    monkeypatch.setattr(s,'a143_staged_module',lambda:q)
    monkeypatch.setattr(s,'exact_a151_operator_preopen_pause',
        lambda:('e'*64,'A151-40686116017-1'))
    monkeypatch.setattr(s,'operator_preopen_timer_disabled',lambda:True)
    monkeypatch.setattr(s,'operator_preopen_hold_evidence',lambda:'h'*64)
    monkeypatch.setattr(s,'ppi_snapshot',lambda:[['ppi-watch.timer','enabled','active']])
    names=sorted(['porota-preopen-rc6.timer',s.IOL_OAUTH_GUARD_TIMER]+[
        'porota-test%d-rc6.timer'%i for i in range(14)])
    digest='d'*64
    prior={'candidate_sha':q.A174_FAILED_CANDIDATE,'status':'SUSPENDED',
           'policy_sha256':digest,'timers':names,'preserved_iol_timer':s.IOL_OAUTH_GUARD_TIMER,
           'ppi_before':s.ppi_snapshot(),'attempted':names,
           'operator_pause_timer':s.OPERATOR_PREOPEN_TIMER,
           'operator_pause_receipt_sha256':'h'*64,
           'paused_from':q.A171_FAILED_CANDIDATE,
           'a171_running_origin':True,'a171_origin_sha256':q.A171_FAILED_RECEIPT_SHA256,
           'rescue_failure_disposition':'A172_NEW_CANDIDATE_UNVALIDATED_HELD'}
    path=folder/('scheduler-suspend-'+q.A174_FAILED_CANDIDATE+'.json')
    path.write_text(json.dumps(prior))
    monkeypatch.setattr(q,'A174_SCHEDULER_RECEIPT_SHA256',hashlib.sha256(path.read_bytes()).hexdigest())
    calls=[]
    def systemctl(*args):
        calls.append(args)
        assert args[0]=='systemctl' and args[1] in {'is-active','is-enabled','show'}
        name=args[2]
        if args[1]=='show':return name.removesuffix('.timer')+'.service',0
        if args[1]=='is-enabled':return ('disabled' if name==s.OPERATOR_PREOPEN_TIMER else 'enabled'),0
        if name.endswith('.service'):return 'inactive',3
        return ('active' if name==s.IOL_OAUTH_GUARD_TIMER else 'inactive'),0
    monkeypatch.setattr(s,'command',systemctl)
    states_seen=[(('active','enabled') if n==s.IOL_OAUTH_GUARD_TIMER else
                  ('inactive','disabled') if n==s.OPERATOR_PREOPEN_TIMER else
                  ('inactive','enabled')) for n in names]
    next_path=folder/('scheduler-suspend-'+NEXT+'.json')
    result=s.adopt_a174_running_hold(NEXT,next_path,names,digest,s.ppi_snapshot(),states_seen)
    assert result['a174_running_origin'] is True
    assert result['a174_origin_sha256']==q.A174_FAILED_RECEIPT_SHA256
    assert result['prior_receipt_sha256']==q.A174_SCHEDULER_RECEIPT_SHA256
    assert result['paused_from']==q.A174_FAILED_CANDIDATE
    assert result['a174_previous_proof']['flat_book']['real_orders_sent']==0
    assert s.require_a174_fix_forward_hold(NEXT)=='A174_RUNNING_UNVALIDATED_HELD_BEFORE_DRAIN'
    assert all(args[1] in {'is-active','is-enabled','show'} for args in calls)
    assert not any(args[:2]==['docker','stop'] for args in commands)
    prior['rescue_failure_disposition']='UNEXPECTED'
    path.write_text(json.dumps(prior))
    with pytest.raises(ValueError,match='SCHEDULER_A174_FAILED_HOLD_CHANGED|SCHEDULER_A174_CANDIDATE_HOLD_CHANGED'):
        s.adopt_a174_running_hold(NEXT,tmp_path/'untouched.json',names,digest,s.ppi_snapshot(),states_seen)
    assert not (tmp_path/'untouched.json').exists()
