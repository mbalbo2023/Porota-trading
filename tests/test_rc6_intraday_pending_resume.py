"""The 20/10 stopped pending runtime and deploy-only timer pause stay fail closed."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import rc6_preopen as preopen
from scripts import rc6_deploy_quiescence as drain
from scripts import rc6_deploy_scheduler_suspend as scheduler


def test_preopen_accepts_only_exact_paused_deploy_receipt(tmp_path, monkeypatch):
    names = sorted(set(preopen.REQUIRED_TIMERS) |
                   {f'porota-extra-{i:02d}-rc6.timer' for i in range(14)})
    policy = {'units': {'systemd/'+name:{'role':'ACTIVE_TIMER','active':True}
                        for name in names}}
    policy_path=tmp_path/'ops/policy/host-control-plane-reconciliation-v2.json'
    policy_path.parent.mkdir(parents=True)
    raw=json.dumps(policy).encode()
    policy_path.write_bytes(raw)
    folder=tmp_path/'data/deploy'
    folder.mkdir(parents=True)
    candidate='a'*40
    receipt={'candidate_sha':candidate,'status':'SUSPENDED','timers':names,
             'attempted':names,'policy_sha256':hashlib.sha256(raw).hexdigest(),
             'ppi_before':[]}
    path=folder/('scheduler-suspend-'+candidate+'.json')
    path.write_text(json.dumps(receipt))
    monkeypatch.setattr(preopen,'ROOT',tmp_path)
    monkeypatch.setattr(scheduler,'ppi_snapshot',lambda:[])
    def command(args, timeout=20):
        if args[1]=='is-enabled': return 0,'enabled',''
        if args[1]=='show': return 0,args[2].replace('.timer','.service'),''
        return 3,'inactive',''
    monkeypatch.setattr(preopen,'cmd',command)
    assert preopen.required_timers(candidate)['state']=='GREEN'
    assert len(preopen.required_timers(candidate)['units'])==20
    assert preopen.required_timers()['state']=='RED'
    receipt['status']='RESTORED'
    path.write_text(json.dumps(receipt))
    assert preopen.required_timers(candidate)['state']=='RED'


def test_exact_pending_stop_adopts_two_positions_and_rejects_drift(tmp_path,monkeypatch):
    prior='d96cb68d177199317ee23239553517e04ee0a255'
    image='sha256:5af3f01a09722faad9fc5cc49cc11b28494d75d1dbdb780425fdd240fd482a34'
    ids={'porota_production_observer':'e0e770054854441ce32af64685ab7da6027735511e40e35beb40c0e2811c56eb',
         'porota_production_dashboard':'e89bb27820f93d655b9db410b4032878c22763529b81a7eac1783ec87702a570'}
    now=datetime.now(timezone.utc)
    folder=tmp_path/'data/deploy'
    folder.mkdir(parents=True)
    current={'candidate_sha':prior,'deploy_sha':'e86bdb6a9fe5ac21b93bc26e70af9844945e1407',
             'image_id':image,'validation_status':'DEPLOYED_VALIDATION_PENDING',
             'mode':'PRODUCTION_PAPER','real_orders_sent':0,'real_order_routes':'NOT_CALLED',
             'recorded_at':(now-timedelta(minutes=10)).isoformat()}
    failed={'candidate_sha':prior,'status':'FIX_FORWARD_VALIDATION_REQUIRED',
            'installation_started':True,'market_open_suspended':True,
            'open_paper_carryover':True,'real_orders_sent':0,'ppi_watch_accessed':False,
            'recorded_at':(now-timedelta(minutes=5)).isoformat()}
    (folder/'CURRENT_STATE_V2.json').write_text(json.dumps(current))
    (folder/('quiescence-'+prior+'.json')).write_text(json.dumps(failed))
    states={name:{'Id':ident,'Image':image,'State':{
        'Status':'exited','StartedAt':(now-timedelta(minutes=12)).isoformat(),
        'FinishedAt':(now-timedelta(minutes=1)).isoformat(),
        'ExitCode':0,'OOMKilled':False}} for name,ident in ids.items()}
    monkeypatch.setattr(drain,'inspect',lambda name:states[name])
    monkeypatch.setattr(drain,'admit',lambda **kwargs:'RUNNING')
    monkeypatch.setattr(drain,'critical_admission',lambda:{'status':'healthy'})
    book={'open_count':2,'sha256':'e7fa20929b6aa4c26a534db6554fd95a22f1734c0fc91175d6f20f05067af13e'}
    monkeypatch.setattr(drain,'spot_carryover_snapshot',lambda:book)
    receipt=drain.adopt_exact_pending_intraday_stop('b'*40,folder,states)
    assert receipt['status']=='DRAINED' and receipt['spot_carryover']==book
    assert receipt['fix_forward_from']==prior and receipt['real_orders_sent']==0
    monkeypatch.setattr(drain,'spot_carryover_snapshot',
                        lambda:dict(book,sha256='0'*64))
    with pytest.raises(ValueError,match='QUIESCENCE_PENDING_BOOK_CHANGED'):
        drain.adopt_exact_pending_intraday_stop('b'*40,folder,states)
    states['porota_production_observer']['State']['ExitCode']=137
    with pytest.raises(ValueError,match='QUIESCENCE_PENDING_STOP_CHANGED'):
        drain.adopt_exact_pending_intraday_stop('b'*40,folder,states)


def test_postclose_pending_requires_exact_overnight_caucion(tmp_path, monkeypatch):
    prior='d96cb68d177199317ee23239553517e04ee0a255'
    image='sha256:5af3f01a09722faad9fc5cc49cc11b28494d75d1dbdb780425fdd240fd482a34'
    now=datetime.now(timezone.utc)
    folder=tmp_path/'data/deploy'
    folder.mkdir(parents=True)
    (folder/'CURRENT_STATE_V2.json').write_text(json.dumps({
        'candidate_sha':prior,'deploy_sha':'e86bdb6a9fe5ac21b93bc26e70af9844945e1407',
        'image_id':image,'validation_status':'DEPLOYED_VALIDATION_PENDING',
        'mode':'PRODUCTION_PAPER','real_orders_sent':0,'real_order_routes':'NOT_CALLED',
        'recorded_at':(now-timedelta(minutes=10)).isoformat()}))
    (folder/('quiescence-'+prior+'.json')).write_text(json.dumps({
        'candidate_sha':prior,'status':'FIX_FORWARD_VALIDATION_REQUIRED',
        'installation_started':True,'market_open_suspended':True,
        'open_paper_carryover':True,'real_orders_sent':0,'ppi_watch_accessed':False,
        'recorded_at':(now-timedelta(minutes=5)).isoformat()}))
    ids={'porota_production_observer':'e0e770054854441ce32af64685ab7da6027735511e40e35beb40c0e2811c56eb',
         'porota_production_dashboard':'e89bb27820f93d655b9db410b4032878c22763529b81a7eac1783ec87702a570'}
    states={name:{'Id':ident,'Image':image,'State':{
        'Status':'exited','StartedAt':(now-timedelta(minutes=12)).isoformat(),
        'FinishedAt':(now-timedelta(minutes=1)).isoformat(),
        'ExitCode':0,'OOMKilled':False}} for name,ident in ids.items()}
    monkeypatch.setattr(drain,'inspect',lambda name:states[name])
    monkeypatch.setattr(drain,'admit',lambda **kw:'RUNNING')
    monkeypatch.setattr(drain,'critical_admission',lambda:{'status':'healthy'})
    monkeypatch.setattr(drain,'spot_carryover_snapshot',lambda:{
        'open_count':2,'sha256':'e7fa20929b6aa4c26a534db6554fd95a22f1734c0fc91175d6f20f05067af13e'})
    caucion={'open_count':1,'sha256':'bf6d2e347850e951c12141bd1d785868b6691da0cc878d17bfe429ac73c9c8a7',
             'maturity_at':'2026-10-21T17:00:00-03:00'}
    monkeypatch.setattr(drain,'pending_caucion_snapshot',lambda:caucion)
    receipt=drain.adopt_exact_pending_intraday_stop('b'*40,folder,states,postclose=True)
    assert receipt['caucion_carryover']==caucion and receipt['paused_postclose'] is True
    monkeypatch.setattr(drain,'pending_caucion_snapshot',lambda:dict(caucion,sha256='0'*64))
    with pytest.raises(ValueError,match='QUIESCENCE_PENDING_BOOK_CHANGED'):
        drain.adopt_exact_pending_intraday_stop('b'*40,folder,states,postclose=True)


def test_postclose_clock_rejects_outside_exact_day(monkeypatch):
    class NextDay(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime(2026,10,21,17,30,tzinfo=tz)
    monkeypatch.setattr(drain,'datetime',NextDay)
    with pytest.raises(ValueError,match='POSTCLOSE_WINDOW_OR_STOP_RED'):
        drain.admit(stopped=True,allow_market_open_suspend=True,
                    allow_open_paper_carryover=True,allow_postclose_pending=True,
                    stopped_receipt={})
