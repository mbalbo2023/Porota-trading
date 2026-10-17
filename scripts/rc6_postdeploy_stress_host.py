#!/usr/bin/env python3
"""Postdeploy-only RC6 stress governor. Invoked through canonical Actions.

Quiesces only the two named RC6 containers after CLOSED/PAPER admission,
executes the exact installed image without credentials/production mounts,
then restores and verifies those same containers even on a test failure.
"""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT=Path('/opt/porota-trading')
sys.path.insert(0,str(ROOT))
from scripts.rc6_deploy_quiescence import clean_stop,lifecycle

OWNED=('porota_production_observer','porota_production_dashboard')
RESERVE=2*1024**3
MEMORY=512*1024**2


def command(args, *, timeout=30):
    value=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=timeout)
    if value.returncode:raise RuntimeError('STRESS_COMMAND_FAILED:'+Path(args[0]).name)
    return value.stdout


def memory_available():
    values={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()}
    return values['MemAvailable']


def database_state(path, *, require_waiting=True):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.2)) as c:
        c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('PRAGMA busy_timeout=200')
        end=time.monotonic()+2;c.set_progress_handler(lambda:int(time.monotonic()>end),1000)
        row=dict(c.execute('SELECT mode,real_orders_sent,process_state,session_state,heartbeat_at FROM observer_state WHERE id=1').fetchone())
        if row['mode']!='PRODUCTION_PAPER' or type(row['real_orders_sent']) is not int or row['real_orders_sent']!=0:
            raise ValueError('STRESS_PAPER_REQUIRED')
        if require_waiting:
            stamp=datetime.fromisoformat(row['heartbeat_at'])
            if (row['session_state'] not in {'CLOSED','MARKET_CLOSED'} or row['process_state']!='WAITING_MARKET'
                    or stamp.tzinfo is None or not 0<=(datetime.now(timezone.utc)-stamp).total_seconds()<=180):
                raise ValueError('STRESS_CLOSED_QUIESCENT_HEARTBEAT_REQUIRED')
        for table,status in (('paper_positions','OPEN'),('paper_cauciones','OPEN'),('paper_future_positions','ACTIVE')):
            if not c.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?',(table,)).fetchone():
                raise ValueError('STRESS_LEDGER_UNVERIFIED')
            if c.execute('SELECT 1 FROM '+table+' WHERE status=? LIMIT 1',(status,)).fetchone():
                raise ValueError('STRESS_OPEN_FINANCIAL_POSITION')
        return row


def restored_state(before,current,image):
    if current['Id']!=before['Id'] or current['Image']!=image:
        raise ValueError('STRESS_RESTORE_IDENTITY_MISMATCH')
    if current['State']['Status']!='running':
        raise ValueError('STRESS_RESTORE_PROCESS_NOT_RUNNING')
    previous=before.get('RestartCount'); observed=current.get('RestartCount')
    if type(previous) is not int or type(observed) is not int or observed!=previous:
        raise ValueError('STRESS_RESTORE_RESTART_COUNT_CHANGED_OR_UNKNOWN')
    return dict(lifecycle(current),RestartCount=observed)


def run(candidate, run_id):
    if not re.fullmatch('[0-9a-f]{40}',candidate) or not re.fullmatch('[0-9]+',run_id):
        raise ValueError('STRESS_IDENTITY_INVALID')
    state=json.loads((ROOT/'data/deploy/CURRENT_STATE_V2.json').read_text())
    if state.get('candidate_sha')!=candidate or state.get('validation_status')!='VALIDATED_RUNTIME':
        raise ValueError('STRESS_REQUIRES_VALIDATED_CURRENT_DEPLOY')
    image=state['image_id']
    if not re.fullmatch('sha256:[0-9a-f]{64}',image):raise ValueError('STRESS_IMAGE_INVALID')
    work=ROOT/'data/deploy/stress'/candidate/run_id
    if any(p.is_symlink() for p in (work,*work.parents)):raise ValueError('STRESS_PATH_ALIAS')
    work.mkdir(parents=True,exist_ok=False,mode=0o700);os.chown(work,1000,1000)
    report={'schema':'rc6.postdeploy-stress-host.v1','candidate_sha':candidate,'image_id':image,
            'github_run_id':run_id,'status':'ADMISSION','started_at':datetime.now(timezone.utc).isoformat(),
            'owned_containers':list(OWNED),'ppi_watch_accessed':False,'runtime_restore':'NOT_NEEDED',
            'production_data_mounted':False,'network':'none','memory_limit_bytes':MEMORY,
            'stress_container_cleanup':'NOT_NEEDED',
            'resource_samples':[]}
    stopped=[];name='porota_stress_'+run_id;test_created=False
    def interrupted(signum,frame):raise RuntimeError('STRESS_INTERRUPTED')
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
    lock=ROOT/'data/deploy/.postdeploy-stress.lock'
    stream=lock.open('a+')
    try:
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        admitted=database_state(ROOT/'data/paper_v17/observer_v17.db')
        report['heartbeat_before_quiesce']=admitted['heartbeat_at']
        if shutil.disk_usage(ROOT).free < RESERVE+768*1024**2:raise ValueError('STRESS_DISK_ADMISSION_BLOCKED')
        before={};before_details={}
        for container in OWNED:
            value=json.loads(command(['docker','inspect',container]))[0]
            if value['Image']!=image or value['State']['Status']!='running':raise ValueError('STRESS_RUNTIME_IMAGE_MISMATCH')
            before[container]=value['Id']
            before_details[container]=value
        report['original_container_ids']=before
        report['stop_evidence']={}
        # Graceful stop drains the scanner/exit owner; only after all its
        # processes stop is absence of evaluation/fill in flight proven.
        for container in OWNED:
            stopped.append(container)
            command(['docker','stop','--time','45',container],timeout=55)
            value=json.loads(command(['docker','inspect',container]))[0]
            report['stop_evidence'][container]={'before':lifecycle(before_details[container]),'after':lifecycle(value)}
            report['stop_evidence'][container]['classification']=clean_stop(before_details[container],value)
        database_state(ROOT/'data/paper_v17/observer_v17.db',require_waiting=False)
        report['inflight_evaluation_or_fill']='ABSENT_OWNED_RUNTIME_STOPPED'
        report['memory_available_after_quiesce']=memory_available()
        if memory_available()<MEMORY+128*1024**2:raise ValueError('STRESS_MEMORY_ADMISSION_BLOCKED')
        args=['docker','run','--name',name,'--network','none','--read-only',
              '--cap-drop','ALL','--security-opt','no-new-privileges','--user','1000:1000',
              '--memory','512m','--memory-swap','512m','--cpus','.75','--pids-limit','96',
              '--tmpfs','/tmp:rw,nosuid,nodev,size=512m',
              '--mount','type=bind,src='+str(work)+',dst=/work',
              '--env','RC6_STRESS_EXECUTION_CONTEXT=DROPLET_ISOLATED_CONTAINER',
              '--env','PYTHONDONTWRITEBYTECODE=1',image,'python','-B','/app/scripts/rc6_postdeploy_stress.py',
              '--output','/work']
        with (work/'process.log').open('w') as log:
            process=subprocess.Popen(args,stdout=log,stderr=log,text=True);test_created=True
            deadline=time.monotonic()+1800;minimum_free=RESERVE
            while process.poll() is None:
                available=memory_available();free=shutil.disk_usage(ROOT).free
                report['resource_samples'].append({'elapsed_seconds':round(1800-(deadline-time.monotonic()),1),
                                                   'host_memory_available':available,'disk_free':free})
                if free<minimum_free or available<32*1024**2 or time.monotonic()>deadline:
                    command(['docker','stop','--time','5',name],timeout=12)
                    process.wait(timeout=15)
                    raise ValueError('STRESS_RESOURCE_OR_TIME_ABORT')
                time.sleep(2)
            report['process_exit_code']=process.returncode
        value=json.loads(command(['docker','inspect',name]))[0]
        report['stress_container_id']=value['Id'];report['oom_killed']=value['State']['OOMKilled']
        if process.returncode or value['State']['OOMKilled']:raise ValueError('STRESS_CONTAINER_RED')
        stress=json.loads((work/'stress.json').read_text())
        if stress.get('status')!='GREEN' or stress.get('smoke') is not False:raise ValueError('STRESS_REPORT_NOT_FULL_GREEN')
        report['status']='GREEN'
    except BaseException as error:
        report.update(status='RED',error_class=type(error).__name__,error_code=str(error)[:200])
    finally:
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        signal.signal(signal.SIGINT,signal.SIG_IGN)
        failures=[]
        if test_created:
            try:
                removed=subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
                report['stress_container_cleanup_exit_code']=removed.returncode
                if removed.returncode:raise RuntimeError('STRESS_CONTAINER_REMOVE_FAILED')
                report['stress_container_cleanup']='GREEN'
            except Exception as error:
                # Cleanup failure must remain RED without suppressing recovery.
                report.update(status='RED',stress_container_cleanup='RED',
                              cleanup_error_class=type(error).__name__)
        # Restart the SAME containers, never recreate from a tag or build.
        for container in stopped:
            try:
                command(['docker','start',container],timeout=35)
                current=json.loads(command(['docker','inspect',container]))[0]
                restored_state(before_details[container],current,image)
            except Exception as error:failures.append(type(error).__name__)
        for _ in range(24):
            try:
                # A healthy dashboard and a still-fresh old heartbeat do not
                # prove the observer resumed. Recheck both process states.
                restored={}
                if stopped:
                    for container in OWNED:
                        current=json.loads(command(['docker','inspect',container]))[0]
                        restored[container]=restored_state(before_details[container],current,image)
                row=database_state(ROOT/'data/paper_v17/observer_v17.db')
                if stopped and (datetime.fromisoformat(row['heartbeat_at'])
                                <=datetime.fromisoformat(report['heartbeat_before_quiesce'])):
                    raise ValueError('STRESS_RESTORE_HEARTBEAT_NOT_ADVANCED')
                with urlopen('http://127.0.0.1:8000/health',timeout=3) as response:
                    if response.status!=200:raise ValueError('health')
                report['heartbeat_after_restore']=row['heartbeat_at']
                if stopped:report['restored_lifecycle']=restored
                break
            except Exception:
                time.sleep(5)
        else:failures.append('RUNTIME_HEALTH_UNVERIFIED')
        report['runtime_restore']='GREEN' if not failures else 'RED'
        if failures:report.update(status='RED',restore_failures=failures)
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        (work/'host-stress.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k not in {'resource_samples','original_container_ids'}}),flush=True)
        stream.close()
    return 0 if report['status']=='GREEN' and report['runtime_restore']=='GREEN' else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--candidate-sha',required=True)
    parser.add_argument('--run-id',required=True);args=parser.parse_args()
    raise SystemExit(run(args.candidate_sha,args.run_id))
