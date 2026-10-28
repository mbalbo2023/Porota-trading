"""A145 OAuth-only timer exception leaves the A138 PAPER hold fail-closed."""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from datetime import datetime, timezone

import pytest

from scripts import rc6_deploy_scheduler_suspend as scheduler
from scripts.porota_host_policy_v2 import validate_policy
from scripts.porota_apply_host_control_plane_v2 import build_plan
from scripts import porota_apply_host_control_plane_v2 as host


def policy16():
    names = ([f"porota-test-timer-{i:02d}-rc6.timer" for i in range(14)] +
             [scheduler.OPERATOR_PREOPEN_TIMER, scheduler.IOL_OAUTH_GUARD_TIMER])
    units = {"systemd/" + name: {"role": "ACTIVE_TIMER", "active": True,
                                 "enabled": True, "install": True,
                                 "reason": "test recurring RC6 workload"}
             for name in names}
    units["systemd/" + scheduler.IOL_OAUTH_GUARD_TIMER]["suspend_during_deploy"] = False
    raw = json.dumps({"units": units}).encode()
    return raw, sorted(names)


@pytest.fixture
def hold16(tmp_path, monkeypatch):
    raw, names = policy16()
    root = tmp_path / "deploy"
    root.mkdir()
    states = {name: ["inactive", "disabled" if name == scheduler.OPERATOR_PREOPEN_TIMER
                     else "enabled"] for name in names}
    jobs = {name.removesuffix('.timer') + '.service': "inactive" for name in names}
    starts = []
    def cmd(*args):
        assert args[0] == "systemctl"
        if args[1] == 'list-unit-files':
            return '', 0
        if args[1] == 'is-active':
            return (states[args[2]][0] if args[2] in states else jobs[args[2]]), 0
        if args[1] == 'is-enabled':
            return states[args[2]][1], 0
        if args[1] == 'show':
            return args[2].removesuffix('.timer') + '.service', 0
        if args[1] == 'start':
            assert args[2] in states
            states[args[2]][0] = 'active'
            starts.append(args[2])
            return '', 0
        raise AssertionError(f"unexpected systemd mutation: {args}")
    monkeypatch.setattr(scheduler, 'ROOT', root)
    monkeypatch.setattr(scheduler, 'command', cmd)
    monkeypatch.setattr(scheduler, 'operator_preopen_hold_evidence',lambda: 'e'*64)
    monkeypatch.setattr(scheduler, 'operator_preopen_timer_disabled', lambda: True)
    monkeypatch.setattr(scheduler, 'a143_staged_pending_proof',
                        lambda: {"source_sha":scheduler.A143_PENDING_CANDIDATE,
                                 "flat_book":{"real_orders_sent":0}})
    monkeypatch.setattr(scheduler, 'require_owned_running', lambda: None)
    monkeypatch.setattr(scheduler, 'require_a143_fix_forward_hold',
                        lambda candidate:'A143_PRIOR_UNVALIDATED_STOPPED_HELD')
    a138 = {"candidate_sha":scheduler.FAILED_A138_SUSPENSION_CANDIDATE,
            "status":"SUSPENDED", "policy_sha256":scheduler.A138_ORIGINAL_TIMER_POLICY_SHA256,
            "timers":names, "attempted":names, "ppi_before":[],
            "operator_pause_timer":scheduler.OPERATOR_PREOPEN_TIMER,
            "operator_pause_receipt_sha256":"e"*64}
    a138_path = root / ("scheduler-suspend-" + scheduler.FAILED_A138_SUSPENSION_CANDIDATE + ".json")
    a138_path.write_text(json.dumps(a138))
    prior = dict(a138, candidate_sha=scheduler.A143_PENDING_CANDIDATE,
                 paused_from=scheduler.FAILED_A138_SUSPENSION_CANDIDATE,
                 prior_receipt_sha256=hashlib.sha256(a138_path.read_bytes()).hexdigest(),
                 adopted_a138_operator_hold=True,
                 rescue_failure_disposition="RESCUE_CANDIDATE_UNVALIDATED_HELD")
    prior_path = root / ("scheduler-suspend-" + scheduler.A143_PENDING_CANDIDATE + ".json")
    prior_path.write_text(json.dumps(prior))
    return raw, names, states, starts, prior_path


def test_iol_policy_exception_only_exact_oauth_unit():
    raw, names = policy16()
    assert len(scheduler.timer_policy(raw)) == 16
    assert scheduler.preserved_iol_guard(raw) == scheduler.IOL_OAUTH_GUARD_TIMER
    policy = json.loads(raw)
    other = "systemd/" + next(n for n in names if n != scheduler.IOL_OAUTH_GUARD_TIMER)
    policy['units'][other]['suspend_during_deploy'] = False
    with pytest.raises(ValueError,match='CARVEOUT'):
        scheduler.timer_policy(json.dumps(policy).encode())
    assert validate_policy({"units":[{"source_path":p} for p in policy['units']]},policy)['status'] == 'FAILED'
    with pytest.raises(ValueError,match='CARVEOUT'):
        build_plan(policy)


def test_a145_exact_old16_hold_migrates_once_to_15_plus_guard(hold16):
    raw,names,states,starts,_ = hold16
    target='f'*40
    result=scheduler.run(target,'suspend',base64.b64encode(raw).decode())
    assert result['status']=='SUSPENDED'
    assert result['iol_guard_migration']=='OLD16_PAUSED_TO_15_PLUS_IOL_RUNNING'
    assert result['preserved_iol_timer']==scheduler.IOL_OAUTH_GUARD_TIMER
    assert starts==[scheduler.IOL_OAUTH_GUARD_TIMER]
    assert states[scheduler.OPERATOR_PREOPEN_TIMER]==['inactive','disabled']
    assert all(states[name]==['inactive','enabled'] for name in names if name not in
               {scheduler.OPERATOR_PREOPEN_TIMER,scheduler.IOL_OAUTH_GUARD_TIMER})
    # Subsequent Deploy V2 steps must not start a duplicate OAuth timer.
    scheduler.run(target,'suspend',base64.b64encode(raw).decode())
    scheduler.run(target,'recover-a143-pending',base64.b64encode(raw).decode())
    assert starts==[scheduler.IOL_OAUTH_GUARD_TIMER]
    assert scheduler.run(target,'restore',base64.b64encode(raw).decode())['status']=='RESTORED'
    assert starts.count(scheduler.IOL_OAUTH_GUARD_TIMER)==1
    assert states[scheduler.OPERATOR_PREOPEN_TIMER]==['inactive','disabled']


@pytest.mark.parametrize('mutation', [
    'wrong_policy_digest','wrong_prior_source','wrong_prior_status',
    'wrong_prior_hash','wrong_a138_policy','wrong_a125_hash',
    'preopen_active','iol_already_running',
])
def test_a145_bad_legacy_chain_or_active_job_cannot_start_guardian(hold16,mutation):
    raw,names,states,starts,prior_path = hold16
    root = prior_path.parent
    value=json.loads(prior_path.read_text())
    if mutation=='wrong_policy_digest':
        value['policy_sha256']='0'*64
    elif mutation=='wrong_prior_source':
        value['candidate_sha']='a'*40
    elif mutation=='wrong_prior_status':
        value['status']='RESTORED'
    elif mutation=='wrong_prior_hash':
        value['prior_receipt_sha256']='0'*64
    elif mutation=='wrong_a125_hash':
        value['operator_pause_receipt_sha256']='0'*64
    elif mutation=='wrong_a138_policy':
        a138_path=root/("scheduler-suspend-"+scheduler.FAILED_A138_SUSPENSION_CANDIDATE+'.json')
        a138=json.loads(a138_path.read_text());a138['policy_sha256']='0'*64
        a138_path.write_text(json.dumps(a138))
    elif mutation=='preopen_active':
        states[scheduler.OPERATOR_PREOPEN_TIMER][0]='active'
    elif mutation=='iol_already_running':
        states[scheduler.IOL_OAUTH_GUARD_TIMER][0]='active'
    prior_path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        scheduler.run('f'*40,'suspend',base64.b64encode(raw).decode())
    assert not starts


@pytest.mark.parametrize('error', ['ACTIVE_WORKER','WRONG_SERVICE_TARGET'])
def test_a145_iol_carveout_fails_before_start_for_running_job_or_wrong_unit(
        hold16, monkeypatch, error):
    raw,names,_,starts,_ = hold16
    previous=scheduler.command
    def changed(*args):
        if error=='ACTIVE_WORKER' and args[1]=='is-active' and args[2]==(
                'porota-test-timer-00-rc6.service'):
            return 'active',0
        if error=='WRONG_SERVICE_TARGET' and args[1]=='show' and args[2]==scheduler.IOL_OAUTH_GUARD_TIMER:
            return 'porota-untrusted.service',0
        return previous(*args)
    monkeypatch.setattr(scheduler,'command',changed)
    with pytest.raises(ValueError):
        scheduler.run('f'*40,'suspend',base64.b64encode(raw).decode())
    assert not starts


@pytest.mark.parametrize('iol_running', [True,False])
def test_host_reconciler_requires_preserved_oauth_timer_already_running(
        tmp_path, monkeypatch, iol_running):
    root = tmp_path/'repo'
    systemd = tmp_path/'systemd'
    systemd.mkdir()
    names = [scheduler.IOL_OAUTH_GUARD_TIMER,'porota-runtime-evidence-rc6.timer']
    for name in names:
        path = root/'systemd'/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('[Unit]\nDescription=Fake test timer\n')
    policy = {'units': {
        'systemd/'+name: {'role':'ACTIVE_TIMER','install':True,'enabled':True,
                          'active':True,'reason':'test',
                          **({'suspend_during_deploy':False}
                             if name == scheduler.IOL_OAUTH_GUARD_TIMER else {})}
        for name in names}}
    changed=[]
    def command(*args,check=True):
        action=args[1]
        if action=='is-enabled':
            return subprocess.CompletedProcess(args,0,stdout='enabled')
        if action=='is-active':
            running = args[2] == scheduler.IOL_OAUTH_GUARD_TIMER and iol_running
            return subprocess.CompletedProcess(args,0,stdout='active' if running else 'inactive')
        if action in {'unmask','enable','daemon-reload'}:
            changed.append(args)
            return subprocess.CompletedProcess(args,0,stdout='')
        raise AssertionError(f'Unexpected command {args}')
    monkeypatch.setattr(host,'_run',command)
    if iol_running:
        result=host.apply(root,policy,systemd,keep_active_timers_paused=True)
        assert result['status']=='GREEN'
        assert ('systemctl','enable',scheduler.IOL_OAUTH_GUARD_TIMER) not in changed
        assert ('systemctl','enable','porota-runtime-evidence-rc6.timer') in changed
    else:
        with pytest.raises(RuntimeError,match='HOST_IOL_GUARD_CARVEOUT_NOT_ADOPTED'):
            host.apply(root,policy,systemd,keep_active_timers_paused=True)
        assert ('systemctl','enable',scheduler.IOL_OAUTH_GUARD_TIMER) not in changed


def test_a125_boundary_2359_vs_0000_uses_art_and_never_rearms(monkeypatch):
    calls=[]
    def evidence(**kwargs):
        calls.append(kwargs)
        return 'e'*64
    monkeypatch.setattr(scheduler,'exact_operator_preopen_pause',evidence)
    original=scheduler.datetime
    try:
        class BeforeMidnight(datetime):
            @classmethod
            def now(cls,tz=None):
                return datetime(2026,10,28,2,59,tzinfo=timezone.utc).astimezone(tz)
        monkeypatch.setattr(scheduler,'datetime',BeforeMidnight)
        assert scheduler.operator_preopen_hold_evidence()=='e'*64
        assert calls[-1]=={}  # Still Oct 27 23:59 ART, A125 current-day.
        class AfterMidnight(datetime):
            @classmethod
            def now(cls,tz=None):
                return datetime(2026,10,28,3,0,tzinfo=timezone.utc).astimezone(tz)
        monkeypatch.setattr(scheduler,'datetime',AfterMidnight)
        assert scheduler.operator_preopen_hold_evidence()=='e'*64
        assert calls[-1]=={'historical_hold_only':True}
    finally:
        monkeypatch.setattr(scheduler,'datetime',original)
