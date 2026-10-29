#!/usr/bin/env python3
"""Strict read-only diagnosis of the 29-Oct A179 job-level timeout.

Never modifies files, services, Docker or the deploy mutex. The cancellation
is known from Actions 40916351644; host state must be established separately.
"""
import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
from pathlib import Path

ROOT=Path('/opt/porota-trading')
RUN='40916351644'
CANDIDATE='7d62d2f4ed353231da8566a360a1179e99bcc7eb'
DEPLOY='d961d19e55d78c68d902a14b41480344365cde1e'
TREE='31ced11ce047bfa092f5398c13aeb37b74afd629'
IMAGE='sha256:094b0f7485d835c43be4c8f519051beb21d0f4fbdfe817dd2eeb30ab341a06ba'
CRITICAL='sha256:586464c20b74ab3a7ba842df8dfafcd76f21eb4c7bed553f23f6f187924ff735'
MARKER_SHA='a9494750704f16669e76399577190e7110b2c8a3e57b903c08d9710074410a42'
QUIESCENCE_SHA='6539e51af11ed99d472fc692d4d0f48dcba20e2c95c2ff7b56b4732f0ce74887'
SCHEDULER_SHA='72c453b7a630440117c91e69bbc241ed1709e119f85c6a7c9309119d2e2d3ee2'
OWNED={
  'porota_production_observer':'c14f6dffaab2a053b6aaf1dea6d9f58daa21bb873ac82f604c415865619ca8fa',
  'porota_production_dashboard':'d96bb07800f08ac712aa200cf3427218e93e9bd488590d62b32418364f798c4f'
}


def require(check,reason):
    if not check: raise ValueError(reason)


def hashed_json(path,expected,limit=65536):
    require(path.is_file() and not path.is_symlink() and path.stat().st_nlink==1
            and 0<path.stat().st_size<=limit,'CANCEL_RECEIPT_UNSAFE:'+path.name)
    raw=path.read_bytes()
    require(hashlib.sha256(raw).hexdigest()==expected,
            'CANCEL_RECEIPT_DIGEST_DRIFT:'+path.name)
    return json.loads(raw)


def command(*args):
    return subprocess.check_output(args,text=True,timeout=20).strip()


def mutex_proof():
    barrier=Path('/run/lock/porota-rc6-deploy-v2-attempt.json')
    lock=Path('/run/lock/porota-rc6-deploy-v2.lock')
    require(barrier.is_file() and not barrier.is_symlink() and
            barrier.stat().st_uid==0 and barrier.stat().st_nlink==1 and
            stat.S_IMODE(barrier.stat().st_mode)==0o600 and
            0<barrier.stat().st_size<=1024,
            'CANCEL_BARRIER_UNKNOWN')
    state=json.loads(barrier.read_bytes())
    require(state.get('candidate')==CANDIDATE and state.get('run')==RUN
            and state.get('attempt')=='1' and state.get('phase')=='promotion'
            and state.get('status') in ('INFLIGHT','IDLE'),
            'CANCEL_BARRIER_NOT_EXACT_TARGET')
    require(lock.is_file() and not lock.is_symlink() and lock.stat().st_uid==0
            and stat.S_IMODE(lock.stat().st_mode)==0o600,'CANCEL_LOCK_UNSAFE')
    fd=os.open(lock,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            acquired=False
        else:
            acquired=True
            fcntl.flock(fd,fcntl.LOCK_UN)
    finally:
        os.close(fd)
    return {'status':state['status'],'uncontested_flock':acquired,
            'exact_run_id':RUN,'phase':state['phase']}


def matching_processes():
    # Command line contents must never be exported; only bounded PID count.
    patterns=(b'--mode\x00native-acceptance',b'rc6_native_soak.py',
              b'rc6_deploy_generation_gate.py',b'rc6_zero_known_error_runtime_audit.py')
    found=[]
    for child in Path('/proc').iterdir():
        if not child.name.isdigit():continue
        try:
            raw=(child/'cmdline').read_bytes()[:16384]
        except (OSError,PermissionError):continue
        if any(pattern in raw for pattern in patterns):
            found.append(int(child.name))
    return {'match_count':len(found),'candidate_processes_absent':not found}


def receipt_census():
    names=['native-generation-acceptance-a179-'+CANDIDATE[:12]+'-'+RUN+'.json',
           'native-ten-generation-soak-a179-'+CANDIDATE[:12]+'-'+RUN+'.json'] + [
           'zero-known-error-stability-a179-'+CANDIDATE[:12]+'-'+RUN+'-'+str(n)+'.json'
           for n in (1,2,3)]
    found={}
    for name in names:
        path=ROOT/'data/deploy'/name
        if not path.exists() and not path.is_symlink():
            found[name]={'present':False}
            continue
        require(path.is_file() and not path.is_symlink() and path.stat().st_nlink==1
                and 0<path.stat().st_size<=2*1024*1024,
                'CANCEL_GENERATION_RECEIPT_UNSAFE:'+name)
        raw=path.read_bytes()
        data=json.loads(raw)
        require(type(data) is dict,'CANCEL_GENERATION_RECEIPT_INVALID:'+name)
        found[name]={'present':True,'size':len(raw),
                     'sha256':hashlib.sha256(raw).hexdigest(),
                     'status':data.get('status'),'acceptance_mode':data.get('acceptance_mode')}
    return found


def native_cancel_recovery_certificate():
    """Independently recheck the native receipts, never Actions success."""
    prefix=CANDIDATE[:12]+'-'+RUN
    base=ROOT/'data/deploy'
    first=base/('native-generation-acceptance-a179-'+prefix+'.json')
    sustained=base/('native-ten-generation-soak-a179-'+prefix+'.json')
    def strict(path):
        require(path.is_file() and not path.is_symlink() and
                path.stat().st_nlink==1 and 0<path.stat().st_size<=2*1024*1024,
                'CANCEL_NATIVE_CERTIFICATE_UNSAFE:'+path.name)
        return json.loads(path.read_bytes())
    a,b=strict(first),strict(sustained)
    require(a.get('status')==b.get('status')=='GREEN' and
            a.get('acceptance_mode')==b.get('acceptance_mode')=='FRESH_GENERATIONS' and
            b.get('strict') is True and b.get('all_services_sampled_running') is True,
            'CANCEL_NATIVE_CERTIFICATE_STATUS_RED')
    rows_a,rows_b=a.get('generations'),b.get('generations')
    require(type(rows_a) is list and len(rows_a)>=2 and
            type(rows_b) is list and len(rows_b)>=10,
            'CANCEL_NATIVE_GENERATION_COUNT_RED')
    for rows in (rows_a,rows_b):
        require(all(type(row) is dict and row.get('source_sha')==CANDIDATE and
                    row.get('candidate_tree_sha')==TREE and
                    type(row.get('real_orders_sent')) is int and
                    row['real_orders_sent']==0 and
                    row.get('real_routes')=='NOT_CALLED' for row in rows),
                'CANCEL_NATIVE_GENERATION_SOURCE_OR_SAFETY_RED')
        require(len({row.get('generation_id') for row in rows})==len(rows) and
                all(type(row.get('sequence')) is int for row in rows) and
                all(b['sequence']>a['sequence'] for a,b in zip(rows,rows[1:])),
                'CANCEL_NATIVE_GENERATION_NOT_MONOTONIC')
    resources=b.get('resources')
    require(type(resources) is list and len(resources)>=10 and
            all(type(row) is dict and row.get('services_unchanged') is True and
                row.get('available_memory_bytes',0)>=128*1024**2 and
                row.get('free_disk_bytes',0)>=2*1024**3
                for row in resources),
            'CANCEL_NATIVE_RESOURCES_RED')
    stability=[]
    for n in (1,2,3):
        path=base/('zero-known-error-stability-a179-'+prefix+'-'+str(n)+'.json')
        row=strict(path)
        require(row.get('status')=='GREEN' and row.get('read_only') is True and
                row.get('real_routes')==[],
                'CANCEL_NATIVE_STABILITY_RECEIPT_RED:'+str(n))
        stability.append(hashlib.sha256(path.read_bytes()).hexdigest())
    return {'status':'GREEN_INDEPENDENT_NATIVE_RECEIPTS_ONLY',
            'source_sha':CANDIDATE,'source_tree':TREE,
            'native_acceptance_mode':'FRESH_GENERATIONS',
            'first_count':len(rows_a),'sustained_count':len(rows_b),
            'resource_samples':len(resources),'audits':3,
            'first_sha256':hashlib.sha256(first.read_bytes()).hexdigest(),
            'soak_sha256':hashlib.sha256(sustained.read_bytes()).hexdigest(),
            'audit_sha256':stability,
            'real_orders_sent':0,'ppi_watch_mutations':0}


def main():
    root=ROOT/'data/deploy'
    marker=hashed_json(root/'CURRENT_STATE_V2.json',MARKER_SHA)
    quiescence=hashed_json(root/('quiescence-'+CANDIDATE+'.json'),QUIESCENCE_SHA)
    scheduler=hashed_json(root/('scheduler-suspend-'+CANDIDATE+'.json'),SCHEDULER_SHA)
    require(marker.get('candidate_sha')==CANDIDATE
            and marker.get('deploy_sha')==DEPLOY
            and marker.get('validation_status')=='DEPLOYED_VALIDATION_PENDING'
            and marker.get('mode')=='PRODUCTION_PAPER'
            and marker.get('real_orders_sent')==0
            and marker.get('real_order_routes')=='NOT_CALLED',
            'CANCEL_MARKER_CHANGED')
    require(quiescence.get('status')=='FIX_FORWARD_VALIDATION_REQUIRED'
            and quiescence.get('real_orders_sent')==0
            and scheduler.get('status')=='SUSPENDED'
            and scheduler.get('candidate_sha')==CANDIDATE,
            'CANCEL_RUNTIME_QUARANTINE_LOST')
    with sqlite3.connect(
      (ROOT/'data/paper_v17/observer_v17.db').as_uri()+'?mode=ro',
      uri=True,timeout=2) as db:
        db.execute('PRAGMA query_only=ON')
        book=db.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
        require(book==('PRODUCTION_PAPER',0),'CANCEL_PAPER_ZERO_RED')
        for table,status in (('paper_positions','OPEN'),('paper_cauciones','OPEN'),
                             ('paper_future_positions','ACTIVE')):
            require(db.execute('SELECT 1 FROM '+table+' WHERE status=? LIMIT 1',
                               (status,)).fetchone() is None,
                    'CANCEL_PAPER_BOOK_NOT_FLAT')
    for name,expected in OWNED.items():
        row=json.loads(command('docker','inspect',name))[0]
        require(row['Id']==expected and row['Image']==IMAGE and
                row['State']['Status']=='running' and row['RestartCount']==0,
                'CANCEL_OWNED_RUNTIME_DRIFT:'+name)
    crit=json.loads(command('docker','inspect','porota_critical_approval_rc6'))[0]
    require(crit['Image']==CRITICAL and crit['State']['Status']=='running'
            and crit['State'].get('Health',{}).get('Status')=='healthy',
            'CANCEL_CRITICAL_RUNTIME_DRIFT')
    timer=subprocess.run(('systemctl','is-active','porota-preopen-rc6.timer'),
                         capture_output=True,text=True,timeout=15)
    require(timer.returncode==3 and timer.stdout.strip()=='inactive',
            'CANCEL_PREOPEN_ACTIVE_UNEXPECTED')
    return {'schema':'rc6.a179.cancelled-native-exact-ro.v1',
            'candidate':CANDIDATE,'run':RUN,'status':'READ_ONLY',
            'barrier':mutex_proof(), 'processes':matching_processes(),
            'contract_writers':command('docker','ps','--format','{{.Names}}',
                                      '--filter','name=porota-deploy-contract-').splitlines(),
            'partial_receipts':receipt_census(),
            'native_recovery':native_cancel_recovery_certificate(),
            'paper':'PRODUCTION_PAPER','real_orders_sent':0,
            'marker':'DEPLOYED_VALIDATION_PENDING',
            'host_mutations':0,'ppi_watch_mutations':0}


if __name__=='__main__':
    try:
        print('RC6_A179_CANCEL_RO='+json.dumps(main(),sort_keys=True))
    except Exception as exc:
        print('RC6_A179_CANCEL_RO_RED='+json.dumps(
            {'exception':type(exc).__name__,'reason':str(exc)[:220]},sort_keys=True))
        raise SystemExit(2)
