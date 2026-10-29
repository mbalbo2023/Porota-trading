"""Executed A175 -> A178 transition wiring, not merely YAML string assertions.

The old A175 image is deliberately validation-pending. Every failure path
must keep that image stopped, PAPER/0, and never call Docker start or PPI Watch.
"""
import base64
import hashlib
import json
from pathlib import Path
import sys

import pytest

from scripts import rc6_deploy_quiescence as q
from scripts import rc6_deploy_scheduler_suspend as s


CANDIDATE = 'd' * 40
POLICY = Path(__file__).resolve().parents[1] / 'ops/policy/host-control-plane-reconciliation-v2.json'


@pytest.fixture
def a175_held(tmp_path,monkeypatch):
    home=tmp_path/'porota'
    folder=home/'data/deploy'
    folder.mkdir(parents=True)
    monkeypatch.setattr(q,'ROOT',home)
    monkeypatch.setattr(s,'ROOT',folder)
    monkeypatch.setattr(q,'current_runtime_marker',lambda:{'candidate_sha':q.A175_FAILED_CANDIDATE})
    monkeypatch.setattr(s,'a143_staged_module',lambda:q)
    monkeypatch.setattr(s,'operator_preopen_hold_evidence',lambda:'a'*64)
    monkeypatch.setattr(s,'operator_preopen_timer_disabled',lambda:True)
    monkeypatch.setattr(s,'exact_a176_operator_no_go_continuation',
                        lambda:{'status':'NO_GO_PENDING_EXPLICIT_REARM','paper_execution_authorized':False})
    monkeypatch.setattr(s,'ppi_snapshot',lambda:{'ppi_watch':'PRESERVED_READ_ONLY'})
    # A clean Docker stop may leave the last flat WAITING_MARKET row intact.
    # `stopped=True` relaxes accepted rows; it does not rewrite their value.
    monkeypatch.setattr(q,'admit',lambda **opts:'WAITING_MARKET')
    states={name:{'Id':q.A175_RUNNING_IDS[name], 'Image':q.A175_LOADED_IMAGE_ID,
                  'State':{'Status':'running','StartedAt':'2026-10-29T10:00:00Z',
                           'FinishedAt':'2026-10-29T09:57:00Z',
                           'ExitCode':0,'OOMKilled':False}}
            for name in q.OWNED}
    monkeypatch.setattr(q,'inspect',lambda name:json.loads(json.dumps(states[name])))
    proof={'origin_candidate':q.A175_FAILED_CANDIDATE,
           'origin_sha256':q.A175_FAILED_RECEIPT_SHA256,
           'image_id':q.A175_LOADED_IMAGE_ID,
           'containers':{name:{'id':v['Id'],'image':v['Image'],
                               'lifecycle':q.lifecycle(v)} for name,v in states.items()},
           'flat_book':{'source_sha':q.A175_FAILED_CANDIDATE,'real_orders_sent':0,
                        'process_state':'WAITING_MARKET','session_state':'MARKET_CLOSED'},
           'failure_flat_book':{'source_sha':q.A175_FAILED_CANDIDATE,'real_orders_sent':0,
                                'process_state':'WAITING_MARKET','session_state':'MARKET_CLOSED'}}
    probes=[]
    def exact_proof(candidate,*args,**kwargs):
        assert candidate==CANDIDATE
        assert kwargs.get('require_installed_a175_source') is True
        assert callable(kwargs.get('operator_current_no_go_proof'))
        probes.append('A175_ORIGIN')
        return proof
    monkeypatch.setattr(q,'a175_failed_running_proof',exact_proof)
    operator=type('Operator',(),{
        'exact_a151_operator_preopen_pause':staticmethod(lambda:('a'*64,'A151-1-1')),
        'operator_preopen_timer_disabled':staticmethod(lambda:True),
        'exact_a176_operator_no_go_continuation':staticmethod(
            lambda:{'status':'NO_GO_PENDING_EXPLICIT_REARM','paper_execution_authorized':False})})
    monkeypatch.setattr(q,'candidate_operator_module',lambda **kw:operator)
    verified=[]
    def stopped(receipt):
        assert receipt.get('a175_pending_adopted') is True
        assert receipt.get('a175_origin_sha256')==q.A175_FAILED_RECEIPT_SHA256
        assert receipt.get('a175_previous_proof')==proof
        assert receipt.get('inflight')=='ABSENT_OWNED_RUNTIME_STOPPED'
        assert all(states[n]['State']['Status']=='exited' for n in q.OWNED)
        verified.append('A175_STOPPED')
        return proof['flat_book']
    monkeypatch.setattr(q,'a175_stopped_continuation',stopped)
    calls=[]
    def docker(args):
        calls.append(args)
        if args[:2]==['docker','stop']:
            state=states[args[-1]]['State']
            state.update(Status='exited',FinishedAt='2026-10-29T11:10:00Z')
            return ''
        raise AssertionError('UNEXPECTED_DOCKER_COMMAND:'+repr(args))
    monkeypatch.setattr(q,'command',docker)
    policy=POLICY.read_bytes()
    names=s.timer_policy(policy)
    prior={'candidate_sha':q.A175_FAILED_CANDIDATE,'status':'SUSPENDED',
           'policy_sha256':hashlib.sha256(policy).hexdigest(),
           'timers':names,'attempted':names,
           'preserved_iol_timer':s.IOL_OAUTH_GUARD_TIMER,
           'ppi_before':s.ppi_snapshot(),
           'operator_pause_timer':s.OPERATOR_PREOPEN_TIMER,
           'operator_pause_receipt_sha256':'a'*64,
           'paused_from':q.A174_FAILED_CANDIDATE,
           'a174_running_origin':True,
           'a174_origin_sha256':q.A174_FAILED_RECEIPT_SHA256,
           'rescue_failure_disposition':'A175_NEW_CANDIDATE_UNVALIDATED_HELD'}
    old_path=folder/('scheduler-suspend-'+q.A175_FAILED_CANDIDATE+'.json')
    old_path.write_text(json.dumps(prior))
    monkeypatch.setattr(q,'A175_SCHEDULER_RECEIPT_SHA256',
                        hashlib.sha256(old_path.read_bytes()).hexdigest())
    systemctl=[]
    def cmd(*parts):
        systemctl.append(parts)
        assert parts[0]=='systemctl'
        key,name=parts[1],parts[2]
        if key=='show':
            return name.removesuffix('.timer')+'.service',0
        if key=='is-enabled':
            return ('disabled',1) if name==s.OPERATOR_PREOPEN_TIMER else ('enabled',0)
        if key=='is-active':
            return ('active',0) if name==s.IOL_OAUTH_GUARD_TIMER else ('inactive',3)
        raise AssertionError(parts)
    monkeypatch.setattr(s,'command',cmd)
    return folder,policy,states,proof,calls,systemctl,probes,verified


def test_a175_scheduler_adopt_drain_install_failure_hold_is_executed(a175_held):
    folder,policy,states,proof,calls,systemctl,probes,verified=a175_held
    encoded=base64.b64encode(policy).decode('ascii')
    pause=s.run(CANDIDATE,'suspend',encoded)
    assert pause['a175_running_origin'] is True
    assert pause['a175_previous_proof']==proof
    assert pause['a175_origin_sha256']==q.A175_FAILED_RECEIPT_SHA256
    assert pause['status']=='SUSPENDED'
    assert s.require_a175_fix_forward_hold(CANDIDATE)=='A175_RUNNING_UNVALIDATED_HELD_BEFORE_DRAIN'
    receipt=q.run(CANDIDATE,'drain',allow_a175_running_pending=True)
    assert receipt['status']=='DRAINED'
    assert receipt['a175_pending_adopted'] is True
    assert receipt['market_open_suspended'] is False
    assert all(states[n]['State']['Status']=='exited' for n in q.OWNED)
    assert s.require_a175_fix_forward_hold(CANDIDATE)=='A175_PRIOR_UNVALIDATED_STOPPED_HELD'
    installed=q.run(CANDIDATE,'install',allow_a175_running_pending=True)
    assert installed['status']=='HOST_INSTALLATION_STARTED'
    failed=q.run(CANDIDATE,'recover')
    assert failed['status']=='FIX_FORWARD_VALIDATION_REQUIRED'
    assert failed['prior_a175_stopped_after_install'] is True
    assert failed['runtime_health']=='PRIOR_A175_PENDING_NOT_GO'
    held=s.run(CANDIDATE,'recover-a175-pending',encoded)
    assert held['rescue_failure_disposition']=='A175_PRIOR_UNVALIDATED_STOPPED_HELD'
    assert not any(row[:2]==['docker','start'] for row in calls)
    assert len([row for row in calls if row[:2]==['docker','stop']])==len(q.OWNED)
    assert all(row[1] in {'is-active','is-enabled','show'} for row in systemctl)
    assert len(probes)>=2 and len(verified)>=3
    assert (folder/('quiescence-'+CANDIDATE+'.json')).is_file()


def test_a175_preinstall_failure_never_restarts_failed_image(a175_held):
    _,_,states,_,calls,_,_,_=a175_held
    receipt=q.run(CANDIDATE,'drain',allow_a175_running_pending=True)
    assert receipt['status']=='DRAINED'
    held=q.run(CANDIDATE,'recover')
    assert held['status']=='PRIOR_A175_UNVALIDATED_HELD_STOPPED'
    assert all(states[n]['State']['Status']=='exited' for n in q.OWNED)
    assert not any(row[:2]==['docker','start'] for row in calls)


def test_a175_image_intent_and_new_runtime_failed_validation_are_fail_closed(
        a175_held,monkeypatch):
    _,_,states,_,calls,_,_,_=a175_held
    q.run(CANDIDATE,'drain',allow_a175_running_pending=True)
    q.run(CANDIDATE,'install',allow_a175_running_pending=True)
    frozen={'image_id':'sha256:'+'b'*64,'candidate_tree_sha':'c'*40}
    monkeypatch.setattr(q,'candidate_image_intent',lambda *args:frozen)
    intent=q.run(CANDIDATE,'image-intent',candidate_image_id=frozen['image_id'],
                 frozen_manifest='/tmp/fake-bound-frozen.json')
    assert intent['candidate_image_intent']==frozen
    # The old pair was not restarted. After a new image failed validation,
    # recovery must record the new runtime WITHOUT promoting it to GREEN.
    new_id='f'*64
    for state in states.values():
        state.update(Id=new_id,Image=frozen['image_id'])
        state['State']['Status']='running'
    runtime={'status':'running',
             'flat_book':{'source_sha':CANDIDATE,'real_orders_sent':0},
             'critical_admission':{'capability':'issues_only'},
             'containers':{name:{'id':value['Id'],'image':value['Image']}
                           for name,value in states.items()}}
    monkeypatch.setattr(q,'candidate_runtime_after_failed_install',lambda receipt:runtime)
    failed=q.run(CANDIDATE,'recover')
    assert failed['status']=='FIX_FORWARD_VALIDATION_REQUIRED'
    assert failed['runtime_health']=='NEW_A178_CANDIDATE_NOT_GO'
    assert failed['candidate_runtime_after_failure']==runtime
    assert not any(row[:2]==['docker','start'] for row in calls)


def test_a175_new_runtime_with_nonzero_real_orders_is_never_accepted(
        a175_held,monkeypatch):
    _,_,states,_,calls,_,_,_=a175_held
    q.run(CANDIDATE,'drain',allow_a175_running_pending=True)
    q.run(CANDIDATE,'install',allow_a175_running_pending=True)
    for state in states.values():
        state.update(Id='e'*64,Image='sha256:'+'b'*64)
        state['State']['Status']='running'
    monkeypatch.setattr(q,'candidate_runtime_after_failed_install',lambda _: {
        'status':'running','flat_book':{'source_sha':CANDIDATE,'real_orders_sent':1},
        'critical_admission':{'capability':'issues_only'}})
    failed=q.run(CANDIDATE,'recover')
    assert failed['status']=='RECOVERY_UNVERIFIED'
    assert not any(row[:2]==['docker','start'] for row in calls)


def test_a175_prior_scheduler_tamper_cannot_reach_docker(a175_held):
    folder,policy,_,_,calls,_,_,_=a175_held
    prior=folder/('scheduler-suspend-'+q.A175_FAILED_CANDIDATE+'.json')
    row=json.loads(prior.read_bytes())
    row['rescue_failure_disposition']='PRIOR_VALIDATED_INCORRECTLY'
    prior.write_text(json.dumps(row))
    with pytest.raises(ValueError,match='SCHEDULER_A175_FAILED_HOLD_CHANGED'):
        s.run(CANDIDATE,'suspend',base64.b64encode(policy).decode())
    assert not any(x[:2]==['docker','stop'] for x in calls)
    assert not (folder/('scheduler-suspend-'+CANDIDATE+'.json')).exists()


def test_a175_conflicting_modes_fail_before_any_docker_stop(a175_held):
    _,_,states,_,calls,_,_,_=a175_held
    with pytest.raises(ValueError,match='A175_PENDING_MODE_INVALID'):
        q.run(CANDIDATE,'drain',allow_a175_running_pending=True,
              allow_market_open_suspend=True)
    with pytest.raises(ValueError,match='A175_PENDING_MODE_INVALID'):
        q.run(CANDIDATE,'drain',allow_a175_running_pending=True,
              allow_a174_running_pending=True)
    assert all(states[n]['State']['Status']=='running' for n in q.OWNED)
    assert not calls


def test_a175_cli_flags_reach_executable_receivers(monkeypatch):
    observed=[]
    def fake_quiescence(candidate,action,**opts):
        observed.append(('quiescence',candidate,action,opts))
        return {'status':'DRAINED'}
    monkeypatch.setattr(q,'run',fake_quiescence)
    assert q.main(['--candidate-sha',CANDIDATE,'--action','drain',
                   '--adopt-a175-running-pending'])==0
    assert observed[-1]==('quiescence',CANDIDATE,'drain',
                          {'allow_a175_running_pending':True})
    with pytest.raises(SystemExit):
        q.main(['--candidate-sha',CANDIDATE,'--action','drain',
                '--adopt-a175-running-pending','--market-open-suspend'])
    def fake_scheduler(candidate,action,policy):
        observed.append(('scheduler',candidate,action,policy))
    monkeypatch.setattr(s,'run',fake_scheduler)
    monkeypatch.setattr(sys,'argv',['suspend.py','--candidate-sha',CANDIDATE,
                                  '--action','recover-a175-pending','--policy-b64',
                                  base64.b64encode(b'{}').decode()])
    s.main()
    assert observed[-1]==('scheduler',CANDIDATE,'recover-a175-pending',
                          base64.b64encode(b'{}').decode())
