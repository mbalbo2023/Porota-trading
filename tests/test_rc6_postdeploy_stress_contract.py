"""Hardware-stress governor adversaries; never touch Docker or live data."""
from datetime import datetime,timezone
import fcntl
import json
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from scripts import rc6_postdeploy_stress as harness
from scripts import rc6_postdeploy_stress_host as host

SHA='1'*40
IMAGE='sha256:'+'2'*64


@pytest.fixture
def installation(tmp_path,monkeypatch):
    monkeypatch.setattr(host,'ROOT',tmp_path)
    monkeypatch.setattr(host.os,'chown',lambda *args:None)
    monkeypatch.setattr(host.signal,'signal',lambda *args:None)
    (tmp_path/'data/deploy').mkdir(parents=True)
    (tmp_path/'data/paper_v17').mkdir()
    (tmp_path/'data/deploy/CURRENT_STATE_V2.json').write_text(json.dumps({
        'candidate_sha':SHA,'validation_status':'VALIDATED_RUNTIME','image_id':IMAGE}))
    db=tmp_path/'data/paper_v17/observer_v17.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE observer_state(id INTEGER,mode TEXT,real_orders_sent INTEGER,process_state TEXT,session_state TEXT,heartbeat_at TEXT)')
        c.execute('INSERT INTO observer_state VALUES(1,?,?,?,?,?)',('PRODUCTION_PAPER',0,'WAITING_MARKET','MARKET_CLOSED',datetime.now(timezone.utc).isoformat()))
        for table in ('paper_positions','paper_cauciones','paper_future_positions'):
            c.execute('CREATE TABLE '+table+'(status TEXT)')
    calls=[];states={n:'running' for n in host.OWNED}
    def command(args,**kwargs):
        calls.append(args)
        if args[:2]==['docker','inspect']:
            n=args[2]
            return json.dumps([{'Id':n+'_id','Image':IMAGE,'RestartCount':0,
                'State':{'Status':states.get(n,'exited'),'OOMKilled':False,'ExitCode':0}}])
        if args[:2]==['docker','stop']:states[args[-1]]='exited'
        if args[:2]==['docker','start']:
            # The stress mutex must remain exclusive until runtime recovery.
            with (tmp_path/'data/deploy/.postdeploy-stress.lock').open('a+') as stream:
                with pytest.raises(BlockingIOError):fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            states[args[-1]]='running'
            if args[-1]==host.OWNED[0]:
                with sqlite3.connect(db) as c:
                    c.execute('UPDATE observer_state SET heartbeat_at=?',
                              (datetime.now(timezone.utc).isoformat(),))
        return ''
    monkeypatch.setattr(host,'command',command)
    monkeypatch.setattr(host,'memory_available',lambda:1024**3)
    monkeypatch.setattr(host.shutil,'disk_usage',lambda path:SimpleNamespace(free=10*1024**3))
    monkeypatch.setattr(host,'urlopen',lambda *a,**k:SimpleContext())
    monkeypatch.setattr(host.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0))
    return tmp_path,db,calls,states


class SimpleContext:
    status=200
    def __enter__(self):return self
    def __exit__(self,*args):pass


@pytest.mark.parametrize('exit_code',[0,2])
def test_stress_red_keeps_red_and_restores_same_containers(installation,monkeypatch,exit_code):
    root,db,calls,states=installation
    def spawn(args,**kwargs):
        calls.append(args)
        work=next((root/'data/deploy/stress'/SHA).iterdir())
        (work/'stress.json').write_text(json.dumps({'status':'GREEN','smoke':False}))
        return SimpleNamespace(returncode=exit_code,poll=lambda:exit_code)
    monkeypatch.setattr(host.subprocess,'Popen',spawn)
    assert host.run(SHA,'123')==(0 if exit_code==0 else 2)
    report=json.loads((root/'data/deploy/stress'/SHA/'123/host-stress.json').read_text())
    assert report['status']==('GREEN' if exit_code==0 else 'RED')
    assert report['runtime_restore']=='GREEN' and all(s=='running' for s in states.values())
    docker=next(c for c in calls if c[:2]==['docker','run'])
    for pair in [('--network','none'),('--memory','512m'),('--memory-swap','512m'),('--cpus','.75')]:
        assert docker[docker.index(pair[0])+1]==pair[1]
    assert '--env-file' not in docker and '--read-only' in docker
    assert sum(c[:2]==['docker','stop'] for c in calls)==2
    assert sum(c[:2]==['docker','start'] for c in calls)==2
    assert not any('watch' in str(c).lower() for c in calls)


def test_python_temporary_files_use_isolated_bound_workspace_not_tmpfs(installation,monkeypatch):
    import os
    import sys
    root,db,calls,states=installation
    real_popen=subprocess.Popen
    def spawn(args,**kwargs):
        calls.append(args)
        work=root/'data/deploy/stress'/SHA/'130'
        (work/'stress.json').write_text(json.dumps({'status':'GREEN','smoke':False}))
        return SimpleNamespace(returncode=0,poll=lambda:0)
    monkeypatch.setattr(host.subprocess,'Popen',spawn)
    assert host.run(SHA,'130')==0
    docker=next(c for c in calls if c[:2]==['docker','run'])
    environment=[docker[i+1] for i,item in enumerate(docker[:-1]) if item=='--env']
    assert 'TMPDIR=/work' in environment
    work=root/'data/deploy/stress'/SHA/'130'
    assert 'type=bind,src='+str(work)+',dst=/work' in docker
    assert docker[docker.index('--tmpfs')+1]=='/tmp:rw,nosuid,nodev,size=512m'
    # Resolve the command's container mount in this test namespace and launch
    # real Python, proving tempfile accepts the existing writable directory.
    mapped=next(item.split('=',1)[1] for item in environment if item.startswith('TMPDIR='))
    host_path=work/Path(mapped).relative_to('/work')
    process=real_popen([sys.executable,'-B','-c','import tempfile;print(tempfile.gettempdir())'],
        env=dict(os.environ,TMPDIR=str(host_path)),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    output,error=process.communicate(timeout=5)
    assert process.returncode==0 and Path(output.strip()).resolve()==work.resolve()
    assert all(state=='running' for state in states.values())


def test_memory_barrier_never_starts_stress_and_restores_runtime(installation,monkeypatch):
    root,db,calls,states=installation
    monkeypatch.setattr(host,'memory_available',lambda:100*1024**2)
    monkeypatch.setattr(host.subprocess,'Popen',lambda *a,**k:pytest.fail('stress ignored memory gate'))
    assert host.run(SHA,'124')==2
    report=json.loads((root/'data/deploy/stress'/SHA/'124/host-stress.json').read_text())
    assert report['error_code']=='STRESS_MEMORY_ADMISSION_BLOCKED'
    assert report['runtime_restore']=='GREEN' and all(s=='running' for s in states.values())


def test_memory_reserve_during_stress_is_128mib_and_abort_restores(installation,monkeypatch):
    root,db,calls,states=installation
    memory=iter([1024**3,1024**3,100*1024**2])
    monkeypatch.setattr(host,'memory_available',lambda:next(memory))
    process=SimpleNamespace(returncode=0,poll=lambda:None,wait=lambda **kwargs:0)
    monkeypatch.setattr(host.subprocess,'Popen',lambda *args,**kwargs:process)
    assert host.run(SHA,'620')==2
    report=json.loads((root/'data/deploy/stress'/SHA/'620/host-stress.json').read_text())
    assert report['error_code']=='STRESS_RESOURCE_OR_TIME_ABORT'
    assert report['resource_samples'][0]['host_memory_available']==100*1024**2
    assert report['runtime_restore']=='GREEN' and all(states[n]=='running' for n in host.OWNED)


@pytest.mark.parametrize('change',['active_future','missing_ledger','unknown_orders','open_market'])
def test_financial_unknown_or_active_never_quiesces(installation,change):
    root,db,calls,states=installation
    with sqlite3.connect(db) as c:
        if change=='active_future':c.execute("INSERT INTO paper_future_positions VALUES('ACTIVE')")
        elif change=='missing_ledger':c.execute('DROP TABLE paper_cauciones')
        elif change=='unknown_orders':c.execute('UPDATE observer_state SET real_orders_sent=NULL')
        else:c.execute("UPDATE observer_state SET session_state='MARKET_OPEN'")
    with pytest.raises((ValueError,sqlite3.Error)):host.database_state(db)
    assert calls==[] and all(s=='running' for s in states.values())


def test_every_stress_scenario_path_is_present():
    assert len(harness.GROUPS)==8
    assert all((harness.ROOT/p).is_file() for paths in harness.GROUPS.values() for p in paths)


def test_post_stop_state_rechecks_financial_ledgers(installation):
    root,db,calls,states=installation
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='STOPPED'")
        c.execute("INSERT INTO paper_future_positions VALUES('ACTIVE')")
    with pytest.raises(ValueError,match='OPEN_FINANCIAL_POSITION'):
        host.database_state(db,require_waiting=False)


def test_stop_effect_before_client_timeout_still_restores_runtime(installation,monkeypatch):
    root,db,calls,states=installation
    original=host.command
    def timeout_after_stop(args,**kwargs):
        result=original(args,**kwargs)
        if args[:2]==['docker','stop']:raise TimeoutError('daemon stopped before client timeout')
        return result
    monkeypatch.setattr(host,'command',timeout_after_stop)
    assert host.run(SHA,'125')==2
    assert all(s=='running' for s in states.values())
    report=json.loads((root/'data/deploy/stress'/SHA/'125/host-stress.json').read_text())
    assert report['status']=='RED' and report['runtime_restore']=='GREEN'


def successful_stress(installation, monkeypatch):
    root,db,calls,states=installation
    def spawn(args,**kwargs):
        work=next((root/'data/deploy/stress'/SHA).iterdir())
        (work/'stress.json').write_text(json.dumps({'status':'GREEN','smoke':False}))
        return SimpleNamespace(returncode=0,poll=lambda:0)
    monkeypatch.setattr(host.subprocess,'Popen',spawn)
    monkeypatch.setattr(host.time,'sleep',lambda seconds:None)


@pytest.mark.parametrize('cleanup_failure',['nonzero','timeout'])
def test_failed_stress_container_removal_cannot_certify_green(installation,monkeypatch,cleanup_failure):
    root,db,calls,states=installation
    successful_stress(installation,monkeypatch)
    def remove(*args,**kwargs):
        if cleanup_failure=='timeout':raise subprocess.TimeoutExpired('docker rm',20)
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(host.subprocess,'run',remove)
    assert host.run(SHA,'126')==2
    report=json.loads((root/'data/deploy/stress'/SHA/'126/host-stress.json').read_text())
    assert report['status']=='RED' and report['stress_container_cleanup']=='RED'
    assert report['runtime_restore']=='GREEN' and all(s=='running' for s in states.values())


def test_same_identity_but_exited_observer_cannot_certify_restore(installation,monkeypatch):
    root,db,calls,states=installation
    successful_stress(installation,monkeypatch)
    original=host.command
    def no_process(args,**kwargs):
        result=original(args,**kwargs)
        if args[:2]==['docker','start'] and args[-1]==host.OWNED[0]:
            states[host.OWNED[0]]='exited'
        return result
    monkeypatch.setattr(host,'command',no_process)
    assert host.run(SHA,'127')==2
    report=json.loads((root/'data/deploy/stress'/SHA/'127/host-stress.json').read_text())
    assert report['status']=='RED' and report['runtime_restore']=='RED'
    assert states[host.OWNED[1]]=='running'


def test_running_processes_with_unchanged_heartbeat_cannot_certify_restore(installation,monkeypatch):
    root,db,calls,states=installation
    successful_stress(installation,monkeypatch)
    original=host.command
    with sqlite3.connect(db) as c:
        original_heartbeat=c.execute('SELECT heartbeat_at FROM observer_state').fetchone()[0]
    def frozen_heartbeat(args,**kwargs):
        result=original(args,**kwargs)
        if args[:2]==['docker','start']:
            with sqlite3.connect(db) as c:
                c.execute('UPDATE observer_state SET heartbeat_at=?',(original_heartbeat,))
        return result
    monkeypatch.setattr(host,'command',frozen_heartbeat)
    assert host.run(SHA,'128')==2
    report=json.loads((root/'data/deploy/stress'/SHA/'128/host-stress.json').read_text())
    assert report['status']=='RED' and report['runtime_restore']=='RED'


def test_restored_process_restart_must_not_disappear_behind_health(installation,monkeypatch):
    root,db,calls,states=installation
    successful_stress(installation,monkeypatch)
    original=host.command
    restarted=set()
    def auto_restart(args,**kwargs):
        result=original(args,**kwargs)
        if args[:2]==['docker','start']:restarted.add(args[-1])
        if args[:2]==['docker','inspect'] and args[-1] in restarted:
            value=json.loads(result);value[0]['RestartCount']=1
            result=json.dumps(value)
        return result
    monkeypatch.setattr(host,'command',auto_restart)
    assert host.run(SHA,'129')==2
    report=json.loads((root/'data/deploy/stress'/SHA/'129/host-stress.json').read_text())
    assert report['status']=='RED' and report['runtime_restore']=='RED'


def test_short_harness_requires_real_isolated_fills_and_eod(tmp_path,monkeypatch):
    monkeypatch.setenv('PAPER_HISTORICAL_CANDLE_SHADOW','OFF')
    monkeypatch.setenv('PAPER_SECTOR_CONCENTRATION_POLICY','OBSERVATION_ONLY')
    value=harness.session_replay(tmp_path,minutes=8)
    assert value['counts']['paper_fills']>=2
    assert value['exit_reasons'].get('EOD_PAPER',0)>=1
    assert len(value['dashboard_routes'])==52 and value['local_eod_repeated_imported']==0
    assert value['actual_market_acceptance'] is False and value['provider_requests']==0
