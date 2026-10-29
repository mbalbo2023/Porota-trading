#!/usr/bin/env python3
"""Exact A178 Deploy V2 postfailure validation and bounded native closure.

This source is streamed by GitHub Actions to the target. No host mutation
occurs in the ``preflight``, ``preopen`` or ``closure-readiness`` modes.
``native-acceptance`` writes only canonical audit and generation receipts
under the exclusive Deploy V2 host mutex; never restarts or runs stress.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path('/opt/porota-trading')
TZ = ZoneInfo('America/Argentina/Buenos_Aires')
CANDIDATE = '7d62d2f4ed353231da8566a360a1179e99bcc7eb'
TREE = '31ced11ce047bfa092f5398c13aeb37b74afd629'
DEPLOY = 'd961d19e55d78c68d902a14b41480344365cde1e'
IMAGE_ID = 'sha256:094b0f7485d835c43be4c8f519051beb21d0f4fbdfe817dd2eeb30ab341a06ba'
CRITICAL_IMAGE = 'sha256:586464c20b74ab3a7ba842df8dfafcd76f21eb4c7bed553f23f6f187924ff735'
MARKER_SHA256 = 'a9494750704f16669e76399577190e7110b2c8a3e57b903c08d9710074410a42'
QUIESCENCE_SHA256 = '6539e51af11ed99d472fc692d4d0f48dcba20e2c95c2ff7b56b4732f0ce74887'
SCHEDULER_SHA256 = '72c453b7a630440117c91e69bbc241ed1709e119f85c6a7c9309119d2e2d3ee2'
FROZEN_SHA256 = 'e9844093758fb205a0efec22d0d2ba0cf98e579aa15466ef58f11d6da81e0a34'
QUIESCENCE_SOURCE_SHA256 = '0f016652f884cd27bba0e29e3128da615266d09b8bf7a0a1f9a063d1386f0fc6'
OPERATOR_SEAL_SHA256 = '3218ccf5296306068d051b12588c4fac253a768ea3c81e0fc31c501adb34338c'
CONTAINERS = {
    'porota_production_observer': 'c14f6dffaab2a053b6aaf1dea6d9f58daa21bb873ac82f604c415865619ca8fa',
    'porota_production_dashboard': 'd96bb07800f08ac712aa200cf3427218e93e9bd488590d62b32418364f798c4f',
}
NATIVE_FIRST_PREFIX = 'native-generation-acceptance-a179-'+CANDIDATE[:12]+'-'
NATIVE_SUSTAINED_PREFIX = 'native-ten-generation-soak-a179-'+CANDIDATE[:12]+'-'
STABILITY_PREFIX = 'zero-known-error-stability-a179-'+CANDIDATE[:12]+'-'


def native_receipt_names():
    run=os.environ.get('RC6_A179_RUN_ID','')
    require(re.fullmatch(r'[1-9][0-9]{6,14}',run) is not None,
            'A179_NATIVE_RUN_ID_REQUIRED')
    return (NATIVE_FIRST_PREFIX+run+'.json',
            NATIVE_SUSTAINED_PREFIX+run+'.json',
            [STABILITY_PREFIX+run+'-'+str(n)+'.json' for n in (1,2,3)])


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def exact_json(path: Path, sha: str, limit=65536) -> dict:
    require(path.is_file() and not path.is_symlink() and
            path.stat().st_nlink == 1 and 0 < path.stat().st_size <= limit,
            'A179_RECEIPT_UNSAFE:' + path.name)
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == sha,
            'A179_RECEIPT_DIGEST_CHANGED:' + path.name)
    result = json.loads(raw)
    require(type(result) is dict, 'A179_RECEIPT_NOT_OBJECT:' + path.name)
    return result


def read_book(root=ROOT):
    path = root/'data/paper_v17/observer_v17.db'
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=2) as db:
        db.execute('PRAGMA query_only=ON')
        state = db.execute('SELECT mode,real_orders_sent,process_state,session_state FROM observer_state WHERE id=1').fetchone()
        outstanding = {}
        for table, status in (('paper_positions','OPEN'),('paper_cauciones','OPEN'),
                              ('paper_future_positions','ACTIVE')):
            outstanding[table] = bool(db.execute('SELECT 1 FROM '+table+' WHERE status=? LIMIT 1',
                                                 (status,)).fetchone())
    require(state and state[0] == 'PRODUCTION_PAPER' and
            type(state[1]) is int and state[1] == 0 and
            (state[2],state[3]) in {('RUNNING','MARKET_OPEN'),
                                  ('WAITING_MARKET','MARKET_CLOSED'),
                                  ('WAITING_MARKET','CLOSED')}, 'A179_PAPER_BOOK_UNSAFE')
    require(not any(outstanding.values()), 'A179_PAPER_LEDGER_NOT_FLAT')
    return state[2:]


def docker_inspect(name):
    raw = subprocess.check_output(['docker','inspect',name], timeout=15)
    data = json.loads(raw)
    require(type(data) is list and len(data) == 1, 'A179_DOCKER_RESPONSE_INVALID')
    return data[0]


def verify_origin(root=ROOT, *, critical_image=CRITICAL_IMAGE,
                  scheduler_status='SUSPENDED') -> dict:
    deploy_dir = root/'data/deploy'
    marker = exact_json(deploy_dir/'CURRENT_STATE_V2.json', MARKER_SHA256)
    q = exact_json(deploy_dir/('quiescence-'+CANDIDATE+'.json'), QUIESCENCE_SHA256)
    scheduler_path=deploy_dir/('scheduler-suspend-'+CANDIDATE+'.json')
    if scheduler_status=='SUSPENDED':
        suspend=exact_json(scheduler_path,SCHEDULER_SHA256)
    elif scheduler_status=='RESTORED':
        require(scheduler_path.is_file() and not scheduler_path.is_symlink() and
                scheduler_path.stat().st_nlink==1 and
                0<scheduler_path.stat().st_size<=65536,
                'A179_RESTORED_SCHEDULER_RECEIPT_UNSAFE')
        suspend=json.loads(scheduler_path.read_bytes())
    else:
        raise ValueError('A179_SCHEDULER_STATUS_NOT_ALLOWED')
    frozen = exact_json(deploy_dir/'porota-frozen-candidate.json', FROZEN_SHA256, 16384)
    require(marker.get('candidate_sha') == CANDIDATE and
            marker.get('deploy_sha') == DEPLOY and marker.get('image_id') == IMAGE_ID and
            marker.get('validation_status') == 'DEPLOYED_VALIDATION_PENDING' and
            marker.get('mode') == 'PRODUCTION_PAPER' and
            type(marker.get('real_orders_sent')) is int and marker['real_orders_sent'] == 0 and
            marker.get('real_order_routes') == 'NOT_CALLED' and
            marker.get('ppi_watch_untouched') is True, 'A179_MARKER_IDENTITY_CHANGED')
    require(q.get('candidate_sha') == CANDIDATE and
            q.get('status') == 'FIX_FORWARD_VALIDATION_REQUIRED' and
            q.get('installation_started') is True and
            q.get('runtime_health') == 'NEW_A178_CANDIDATE_NOT_GO' and
            q.get('a175_pending_adopted') is True and
            q.get('real_orders_sent') == 0 and
            q.get('ppi_watch_accessed') is False, 'A179_QUIESCENCE_ORIGIN_RED')
    require(suspend.get('candidate_sha') == CANDIDATE and
            suspend.get('status') == scheduler_status and
            len(suspend.get('timers',[])) == 16 and
            set(suspend.get('attempted',[])) == set(suspend['timers']),
            'A179_SCHEDULER_ORIGIN_RED')
    require(suspend.get('operator_pause_timer')=='porota-preopen-rc6.timer' and
            suspend.get('preserved_iol_timer')=='porota-iol-oauth-health-rc6.timer',
            'A179_SCHEDULER_NO_GO_CONTROL_MISSING')
    require(frozen.get('candidate_sha') == CANDIDATE and
            frozen.get('candidate_tree_sha') == TREE and
            frozen.get('build_once') is True and
            frozen.get('paper_mode_required') == 'PRODUCTION_PAPER' and
            frozen.get('real_orders_sent_required') == 0, 'A179_FROZEN_ORIGIN_RED')
    for name, expected in CONTAINERS.items():
        container = docker_inspect(name)
        require(container.get('Id') == expected and container.get('Image') == IMAGE_ID and
                container.get('State',{}).get('Status') == 'running' and
                container.get('RestartCount') == 0, 'A179_RUNTIME_IMAGE_CHANGED:'+name)
    critical = docker_inspect('porota_critical_approval_rc6')
    require(critical.get('Image') == critical_image and
            critical.get('State',{}).get('Status') == 'running' and
            critical.get('State',{}).get('Health',{}).get('Status') == 'healthy',
            'A179_CRITICAL_SERVICE_RED')
    phase = read_book(root)
    require(q.get('candidate_runtime_after_failure',{}).get('flat_book',{}).get('real_orders_sent') == 0 and
            q.get('candidate_runtime_after_failure',{}).get('critical_admission',{}).get('capability') == 'issues_only',
            'A179_PRIOR_PAPER_OR_CRITICAL_CHANGED')
    return {'candidate': CANDIDATE, 'deploy': DEPLOY, 'image': IMAGE_ID,
            'session': list(phase), 'marker':'PENDING', 'frozen_sha256':FROZEN_SHA256,
            'quiescence_sha256':QUIESCENCE_SHA256,
            'scheduler_sha256':hashlib.sha256(scheduler_path.read_bytes()).hexdigest(),
            'paper':'PRODUCTION_PAPER', 'real_orders_sent':0,
            'ppi_watch_mutations':0, 'host_mutations':0}


def staged_env(root=ROOT):
    source = root/'scripts/rc6_deploy_quiescence.py'
    require(source.is_file() and not source.is_symlink() and source.stat().st_size < 260000,
            'A179_HELPER_SOURCE_UNSAFE')
    raw = source.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == QUIESCENCE_SOURCE_SHA256,
            'A179_HELPER_SOURCE_CHANGED')
    encoded = base64.b64encode(gzip.compress(raw,mtime=0)).decode('ascii')
    require(0 < len(encoded) < 150000 and not os.environ.get('RC6_A145_QUIESCENCE_GZIP_B64'),
            'A179_HELPER_WIRE_BOUNDS_RED')
    return dict(os.environ, RC6_A145_QUIESCENCE_GZIP_B64=encoded,
                PYTHONDONTWRITEBYTECODE='1')


def verify_preopen(root=ROOT, *, critical_image=CRITICAL_IMAGE,
                   scheduler_status='SUSPENDED') -> dict:
    verify_origin(root,critical_image=critical_image,
                  scheduler_status=scheduler_status)
    sys.path.insert(0, str(root))
    from scripts.rc6_deploy_preopen_gate import strict_json, validate_report
    import ak_byma_calendar
    env = staged_env(root)
    results = {}
    for phase in ('T_MINUS_45','T_MINUS_10'):
        cmd=[sys.executable, str(root/'rc6_preopen.py'), '--phase',phase]
        if scheduler_status=='SUSPENDED':
            cmd.extend(('--deploy-scheduler-candidate',CANDIDATE))
        completed=subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180)
        require(completed.returncode == 0, 'A179_PREOPEN_PROCESS_RED:'+phase)
        report = strict_json(completed.stdout)
        accepted = validate_report(report,phase=phase,return_code=completed.returncode,
                                   now=datetime.now(TZ),
                                   is_operational=ak_byma_calendar.es_dia_habil_operativo)
        require(accepted.get('accepted') is True and
                accepted.get('preopen_status') == 'PAUSED_NO_GO' and
                accepted.get('execution_authorized') is False and
                report.get('checks',{}).get('required_rc6_timers',{}).get('state') == 'GREEN' and
                report.get('paper_admission_hold') == 'OPERATOR_NO_GO_PENDING_EXPLICIT_REARM' and
                report.get('operator_pause_receipt_sha256') == OPERATOR_SEAL_SHA256,
                'A179_PREOPEN_NOT_EXACT_PAUSED_GREEN:'+phase)
        results[phase] = {'status':'PAUSED_NO_GO','timers':'GREEN',
                          'paper_orders_authorized':False,
                          'operator_seal':OPERATOR_SEAL_SHA256}
    return {'schema':'rc6.a179.preopen-staged-exact-readonly.v1',
            'status':'GREEN_READ_ONLY', 'results':results,
            'candidate':CANDIDATE,'real_orders_sent':0,'host_mutations':0,
            'ppi_watch_mutations':0}


def verify_pending_restored_preopen(root=ROOT) -> dict:
    """The *only* allowed pre-marker RED is its own pending identity guard.

    Post-restore `rc6_preopen` necessarily refuses a non-VALIDATED marker for
    observer and dashboard. Require those two exact reds and no others;
    both phases are then independently rerun without a staged helper after
    VALIDATED_RUNTIME is durably written. No bypass of ordinary preopen.
    """
    verify_origin(root,critical_image=IMAGE_ID,scheduler_status='RESTORED')
    env=staged_env(root)
    expected={'observer_container','dashboard_container'}
    output={}
    for phase in ('T_MINUS_45','T_MINUS_10'):
        completed=subprocess.run([sys.executable,str(root/'rc6_preopen.py'),
                                  '--phase',phase],capture_output=True,text=True,
                                 env=env,timeout=180,check=False)
        require(completed.returncode==2,
                'A179_PENDING_PREOPEN_EXPECTED_EXACT_MARKER_RED:'+phase)
        report=json.loads(completed.stdout)
        checks=report.get('checks')
        require(report.get('status')=='RED' and
                type(report.get('red_checks')) is list and
                set(report['red_checks'])==expected and
                len(report['red_checks'])==2 and
                type(checks) is dict and
                set(checks)=={'release_identity','observer_container',
                              'dashboard_container','observer_db','disk',
                              'required_rc6_timers','byma_morning_watch',
                              'iol_reference_fallback','history_quarantine',
                              'foreign_market_policy'} and
                all(type(v) is dict and
                    (v.get('state')=='RED' and
                     v.get('reason')=='PREOPEN_VALIDATED_RUNTIME_IDENTITY_RED'
                     if key in expected
                     else v.get('state') in ('GREEN','AMBER'))
                    for key,v in checks.items()) and
                checks['required_rc6_timers'].get('state')=='GREEN' and
                report.get('operator_paper_paused') is True and
                report.get('paper_admission_hold')==
                'OPERATOR_NO_GO_PENDING_EXPLICIT_REARM' and
                report.get('operator_pause_receipt_sha256')==OPERATOR_SEAL_SHA256,
                'A179_PENDING_PREOPEN_HAS_ADDITIONAL_FAILURE:'+phase)
        output[phase]={'expected_pending_identity_red_only':sorted(expected),
                       'other_checks':'GREEN_OR_AMBER','timers':'GREEN',
                       'paper_admission':'NO_GO'}
    return {'status':'EXACT_EXPECTED_PENDING_MARKER_RED_ONLY',
            'candidate':CANDIDATE,'phases':output,'real_orders_sent':0,
            'host_mutations':0,'ppi_watch_mutations':0}


def command_stdout(*argv):
    return subprocess.check_output(argv, text=True, timeout=15).strip()


def systemctl_state(action, unit):
    done=subprocess.run(('systemctl',action,unit), capture_output=True,
                        text=True,timeout=15,check=False)
    require(done.returncode in (0,1,3), 'A179_SYSTEMCTL_QUERY_FAILED:'+unit)
    return done.stdout.strip()


def require_exact_mutex():
    raw=os.environ.get('RC6_DEPLOY_MUTEX_FD','')
    require(re.fullmatch(r'[1-9][0-9]*',raw) is not None and int(raw)>=3,
            'A179_DEPLOY_HOST_MUTEX_REQUIRED')
    try:
        target=os.readlink('/proc/self/fd/'+raw)
    except OSError as exc:
        raise ValueError('A179_DEPLOY_HOST_MUTEX_UNVERIFIABLE') from exc
    require(target=='/run/lock/porota-rc6-deploy-v2.lock',
            'A179_DEPLOY_HOST_MUTEX_IDENTITY_RED')


def verify_closure_readiness(root=ROOT, *, mutex_wrapped=False) -> dict:
    """Predict every remaining host-write prerequisite without touching it.

    Restore the installed artifact only if the prior attempt is definitely
    finished, the stable tag and approval unit agree, and the empty-backup
    guard is already satisfied. No docker run/systemctl start occurs here.
    """
    verify_origin(root)
    if mutex_wrapped:
        require_exact_mutex()
    else:
        require(not (Path('/run/lock')/'porota-rc6-deploy-v2-attempt.json').exists(),
                'A179_PREVIOUS_DEPLOY_MUTEX_BARRIER_PRESENT')
        require(not (Path('/tmp')/'porota-rc6-deploy-v2-attempt.json').exists(),
                'A179_OLD_DEPLOY_MUTEX_BARRIER_PRESENT')
    stable='porota-trading-bot:17.0.0-rc6'
    require(command_stdout('docker','image','inspect','-f','{{.Id}}',stable)==IMAGE_ID,
            'A179_STABLE_IMAGE_TAG_DRIFT')
    for service in ('porota-critical-github-proxy-rc6.service',
                    'porota-critical-approval-rc6.service'):
        require(systemctl_state('is-active',service)=='active',
                'A179_CRITICAL_SERVICE_INACTIVE:'+service)
    unit=command_stdout('systemctl','cat','porota-critical-approval-rc6.service')
    require(stable in unit and '/opt/porota-control-plane-rc6/current:/code:ro' in unit,
            'A179_CRITICAL_UNIT_UNEXPECTED')
    require(systemctl_state('is-enabled',
                            'porota-host-general-backup-rc6.timer')=='masked' and
            systemctl_state('is-active',
                            'porota-host-general-backup-rc6.timer')=='inactive',
            'A179_BACKUP_TIMER_UNEXPECTED')
    policy=json.loads((root/'ops/policy/rc6-disk-housekeeping-v1.json').read_bytes())
    require(policy.get('backups',{}).get('suspended') is True,
            'A179_BACKUP_POLICY_NOT_SUSPENDED')
    sys.path.insert(0,str(root))
    from scripts import rc6_679_backup_suspension as backup
    inventory=backup.inventory()
    require(inventory==[], 'A179_BACKUP_EMPTY_RETIRE_NOT_YET_SAFE')
    require(not (root/'data/deploy'/('backup-retire-'+CANDIDATE+'.json')).exists(),
            'A179_BACKUP_ALREADY_RETIRED')
    return {'schema':'rc6.a179.closure-readiness.v1','status':'GREEN_READ_ONLY',
            'candidate':CANDIDATE, 'current_marker':'DEPLOYED_VALIDATION_PENDING',
            'critical_old_image':CRITICAL_IMAGE, 'stable_image':IMAGE_ID,
            'backup_inventory_count':0,
            'mutex_barrier':'EXCLUSIVELY_HELD' if mutex_wrapped else 'ABSENT',
            'readiness':'NO_PREMATURE_RUNTIME_FINALIZATION',
            'real_orders_sent':0,'host_mutations':0,'ppi_watch_mutations':0}


def ppi_watch_snapshot():
    inventory=command_stdout('systemctl','list-unit-files','--no-legend')
    names=sorted(line.split()[0] for line in inventory.splitlines()
                 if line.split() and 'ppi' in line.split()[0].lower()
                 and 'watch' in line.split()[0].lower())
    return [(name,systemctl_state('is-enabled',name),
             systemctl_state('is-active',name)) for name in names]


def checked_process(args, seconds, reason):
    completed=subprocess.run(args, capture_output=True, text=True, timeout=seconds,
                             check=False)
    require(completed.returncode==0, reason+':RC='+str(completed.returncode))
    return completed


def safe_generated(path: Path, max_size=2*1024*1024) -> dict:
    require(path.is_file() and not path.is_symlink() and
            path.stat().st_nlink == 1 and 0 < path.stat().st_size <= max_size,
            'A179_NATIVE_RECEIPT_UNSAFE:'+path.name)
    value=json.loads(path.read_bytes())
    require(type(value) is dict and value.get('status')=='GREEN',
            'A179_NATIVE_RECEIPT_RED:'+path.name)
    return value


def receipt_census(root=ROOT) -> dict:
    verify_origin(root)
    deploy_dir=root/'data/deploy'
    names=['native-generation-acceptance.json',
           'native-ten-generation-soak.json'] + [
           'zero-known-error-stability-'+str(n)+'.json' for n in (1,2,3)]
    records={}
    for name in names:
        path=deploy_dir/name
        if not path.exists() and not path.is_symlink():
            records[name]={'present':False}
            continue
        require(path.is_file() and not path.is_symlink() and
                path.stat().st_nlink==1 and 0<path.stat().st_size<=2*1024*1024,
                'A179_EXISTING_NATIVE_RECEIPT_UNSAFE:'+name)
        data=json.loads(path.read_bytes())
        require(type(data) is dict,'A179_EXISTING_NATIVE_RECEIPT_INVALID:'+name)
        rows=data.get('generations') or data.get('idle_observations') or []
        require(type(rows) is list,'A179_EXISTING_NATIVE_RECEIPT_ROWS_INVALID:'+name)
        last=rows[-1] if rows else {}
        require(type(last) is dict,'A179_EXISTING_NATIVE_RECEIPT_ROW_INVALID:'+name)
        records[name]={'present':True,
                       'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                       'size':path.stat().st_size,
                       'status':data.get('status'),
                       'acceptance_mode':data.get('acceptance_mode'),
                       'source_sha':last.get('source_sha'),
                       'candidate_tree_sha':last.get('candidate_tree_sha')}
    return {'schema':'rc6.a179.native-receipt-census.v1',
            'status':'GREEN_READ_ONLY','candidate':CANDIDATE,
            'receipts':records,'host_mutations':0,'ppi_watch_mutations':0,
            'real_orders_sent':0}


def validate_native_pair(first, sustained):
    mode=first.get('acceptance_mode')
    require(first.get('status')==sustained.get('status')=='GREEN' and
            mode==sustained.get('acceptance_mode') and
            mode in ('FRESH_GENERATIONS','CLOSED_IDLE') and
            sustained.get('strict') is True and
            sustained.get('all_services_sampled_running') is True,
            'A179_NATIVE_PAIR_STATUS_RED')
    if mode=='CLOSED_IDLE':
        require(first.get('generations')==sustained.get('generations')==[] and
                first.get('source_freshness_renewed') is False and
                sustained.get('source_freshness_renewed') is False and
                len(first.get('idle_observations',[]))>=2 and
                len(sustained.get('idle_observations',[]))>=10 and
                first.get('idle_heartbeat_count',0)>=2 and
                sustained.get('idle_heartbeat_count',0)>=2,
                'A179_CLOSED_IDLE_CERTIFICATE_RED')
        rows=(first['idle_observations'][-1],sustained['idle_observations'][-1])
    else:
        require(len(first.get('generations',[]))>=2 and
                len(sustained.get('generations',[]))>=10,
                'A179_FRESH_NATIVE_GENERATIONS_RED')
        rows=(first['generations'][-1],sustained['generations'][-1])
    require(all(row.get('source_sha')==CANDIDATE and
                row.get('candidate_tree_sha')==TREE and
                type(row.get('real_orders_sent')) is int and
                row['real_orders_sent']==0 and
                row.get('real_routes')=='NOT_CALLED' for row in rows),
            'A179_NATIVE_SAFETY_OR_SOURCE_RED')
    return mode


def verify_unchanged_runtime():
    for name, expected in CONTAINERS.items():
        row=docker_inspect(name)
        require(row.get('Id')==expected and row.get('Image')==IMAGE_ID and
                row.get('State',{}).get('Status')=='running' and
                row.get('RestartCount')==0 and
                row.get('State',{}).get('OOMKilled') is False,
                'A179_NATIVE_RESTART_OR_IMAGE_DRIFT:'+name)
    read_book()


def run_native_acceptance(root=ROOT) -> dict:
    """Finish the canonical passive audit sequence; NO_GO stays sealed.

    Only existing production audit/SHADOW generation collectors are invoked.
    No time-of-day veto, no market requests, no orders, no stress, no restart.
    """
    verify_closure_readiness(root,mutex_wrapped=True)
    verify_preopen(root)
    ppi_before=ppi_watch_snapshot()
    deploy_dir=root/'data/deploy'
    first_name,sustained_name,stability_names=native_receipt_names()
    first_path=deploy_dir/first_name
    sustained_path=deploy_dir/sustained_name
    require(not first_path.exists() and not sustained_path.exists(),
            'A179_NATIVE_ALREADY_PARTIALLY_RUN')
    require(all(not (deploy_dir/name).exists() for name in stability_names),
            'A179_STABILITY_ALREADY_PARTIALLY_RUN')
    audit_dir='/app/data/deploy'
    baseline=deploy_dir/'zero-known-error-immediate.json'
    require(safe_generated(baseline).get('read_only') is True,
            'A179_IMMEDIATE_AUDIT_NOT_READ_ONLY')
    for n in (1,2,3):
        time.sleep(35)
        verify_unchanged_runtime()
        checked_process(('docker','exec','porota_production_dashboard','timeout','300',
                         'python','/app/scripts/rc6_zero_known_error_runtime_audit.py',
                         '--label','stability-'+str(n),'--baseline',
                         audit_dir+'/zero-known-error-immediate.json',
                         '--output',audit_dir+'/'+stability_names[n-1]),
                        320,'A179_STABILITY_AUDIT_RED:'+str(n))
        audit=safe_generated(deploy_dir/stability_names[n-1])
        require(audit.get('read_only') is True and
                audit.get('real_routes')==[],
                'A179_STABILITY_AUDIT_SAFETY_RED:'+str(n))
    # Once-per-minute bounded passive observation detects delayed exit-139.
    soak_start=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    for _ in range(5):
        time.sleep(60)
        verify_unchanged_runtime()
    signature=re.compile(r'Traceback|REAL_ORDER_CAPABILITY=(?:ENABLED|REAL)|real_orders_sent=[1-9][0-9]*')
    for name in CONTAINERS:
        logs=checked_process(('docker','logs','--since',soak_start,name),
                             30,'A179_RECENT_LOG_CHECK_FAILED')
        require(signature.search(logs.stdout+'\n'+logs.stderr) is None,
                'A179_RECENT_CRITICAL_LOG_SIGNATURE:'+name)
    checked_process(('docker','exec','porota_production_dashboard','timeout','195',
                     'python','/app/scripts/rc6_deploy_generation_gate.py',
                     '--expected-sha',CANDIDATE,'--expected-tree',TREE,
                     '--allow-closed-idle','--output',
                     '/app/data/deploy/'+first_name),
                    220,'A179_NATIVE_GENERATION_GATE_RED')
    checked_process(('python3',str(root/'scripts/rc6_native_soak.py'),
                     '--expected-sha',CANDIDATE,'--expected-tree',TREE,
                     '--allow-closed-idle','--output',str(sustained_path)),
                    1230,'A179_NATIVE_TEN_GENERATION_SOAK_RED')
    mode=validate_native_pair(safe_generated(first_path),
                              safe_generated(sustained_path))
    verify_origin(root)
    require(ppi_watch_snapshot()==ppi_before,'A179_PPI_WATCH_DRIFT')
    return {'schema':'rc6.a179.native-acceptance.v1','status':'GREEN',
            'candidate':CANDIDATE,'image':IMAGE_ID,'acceptance_mode':mode,
            'run_id':os.environ['RC6_A179_RUN_ID'],
            'first_receipt':first_name,'soak_receipt':sustained_name,
            'stability_receipts':stability_names,
            'stability_audits':3,'passive_exit139_minutes':5,
            'native_generation_gate':'GREEN','native_ten_generation_soak':'GREEN',
            'session_time_veto':False,'operator_no_go':'HELD',
            'real_orders_sent':0,'ppi_watch_mutations':0,
            'host_control_mutations':0}


def cleanup_failed_native_attempt(root=ROOT) -> dict:
    """Only retire a completed attempt barrier after re-proving NO_GO.

    The mutex wrapper owns the actual barrier unlink. An uncertain lost-child
    state is refused by that wrapper and remains blocked, not force-cleared.
    """
    require_exact_mutex()
    verify_origin(root)
    verify_preopen(root)
    return {'status':'RECOVERY_ATTEMPT_CLEANLY_ENDED','candidate':CANDIDATE,
            'marker':'DEPLOYED_VALIDATION_PENDING','real_orders_sent':0,
            'operator_no_go':'HELD','host_control_mutations':0,
            'ppi_watch_mutations':0}


def validate_native_proof(root=ROOT) -> dict:
    first_name,soak_name,stability_names=native_receipt_names()
    deploy_dir=root/'data/deploy'
    first=safe_generated(deploy_dir/first_name)
    soak=safe_generated(deploy_dir/soak_name)
    mode=validate_native_pair(first,soak)
    for name in stability_names:
        snapshot=safe_generated(deploy_dir/name)
        require(snapshot.get('read_only') is True and
                snapshot.get('real_routes')==[],
                'A179_STABILITY_RECEIPT_NOT_READ_ONLY:'+name)
    return {'mode':mode,'run_id':os.environ['RC6_A179_RUN_ID'],
            'first':first_name,'first_sha256':hashlib.sha256(
                (deploy_dir/first_name).read_bytes()).hexdigest(),
            'soak':soak_name,'soak_sha256':hashlib.sha256(
                (deploy_dir/soak_name).read_bytes()).hexdigest(),
            'stability_receipts':stability_names}


def release_native_attempt(root=ROOT) -> dict:
    """Complete the old successful validation's exact mutex attempt only."""
    require_exact_mutex()
    verify_origin(root)
    verify_preopen(root)
    proof=validate_native_proof(root)
    return {'status':'GREEN_NATIVE_ATTEMPT_RELEASED',
            'candidate':CANDIDATE, 'native_run_id':proof['run_id'],
            'native_mode':proof['mode'], 'marker':'DEPLOYED_VALIDATION_PENDING',
            'operator_no_go':'HELD','real_orders_sent':0,
            'ppi_watch_mutations':0}


def closure_preflight(root=ROOT) -> dict:
    """No host writes; prove exact prior *IDLE* barrier, never INFLIGHT."""
    import stat
    verify_origin(root)
    verify_preopen(root)
    proof=validate_native_proof(root)
    barrier=Path('/run/lock/porota-rc6-deploy-v2-attempt.json')
    require(barrier.is_file() and not barrier.is_symlink() and
            barrier.stat().st_uid==0 and barrier.stat().st_nlink==1 and
            stat.S_IMODE(barrier.stat().st_mode)==0o600 and
            0<barrier.stat().st_size<=1024,
            'A179_PRIOR_NATIVE_BARRIER_UNSAFE')
    state=json.loads(barrier.read_bytes())
    require(state=={'candidate':CANDIDATE,'run':proof['run_id'],
                    'attempt':'1','phase':'promotion','status':'IDLE'},
            'A179_PRIOR_NATIVE_BARRIER_NOT_CLEAN_IDLE')
    stale=command_stdout('docker','ps','--format','{{.Names}}',
                         '--filter','name=porota-deploy-contract-')
    require(not stale,'A179_ORPHAN_CONTRACT_WRITER_STILL_RUNNING')
    return {'schema':'rc6.a179.native-to-finalize-preflight.v1',
            'status':'GREEN_READ_ONLY','candidate':CANDIDATE,
            'native_run_id':proof['run_id'],'native_mode':proof['mode'],
            'prior_barrier':'IDLE_EXACT_ATTEMPT_1','operator_no_go':'HELD',
            'real_orders_sent':0,'host_mutations':0,'ppi_watch_mutations':0}


def restored_readiness(root=ROOT) -> dict:
    """New-attempt RO gate after A179 marked the prior attempt failed safe."""
    require(not Path('/run/lock/porota-rc6-deploy-v2-attempt.json').exists() and
            not Path('/tmp/porota-rc6-deploy-v2-attempt.json').exists(),
            'A179_POSTRESTORE_ATTEMPT_BARRIER_PRESENT')
    require(not command_stdout('docker','ps','--format','{{.Names}}',
                               '--filter','name=porota-deploy-contract-'),
            'A179_POSTRESTORE_ORPHAN_CONTRACT_RUNNING')
    transition=verify_pending_restored_preopen(root)
    proof=validate_native_proof(root)
    require(command_stdout('docker','image','inspect','-f','{{.Id}}',
                           'porota-trading-bot:17.0.0-rc6')==IMAGE_ID,
            'A179_POSTRESTORE_IMAGE_ALIAS_DRIFT')
    return {'status':'GREEN_READ_ONLY_PENDING_TRANSITION',
            'candidate':CANDIDATE,'native_run_id':proof['run_id'],
            'native_mode':proof['mode'],'transition':transition['status'],
            'critical_image':'NEW_EXACT','schedulers':'RESTORED',
            'operator_no_go':'HELD','real_orders_sent':0,
            'host_mutations':0,'ppi_watch_mutations':0}


def reconcile_critical_image(root=ROOT) -> dict:
    """Mirror the canonical critical-broker reimage, never changing its code."""
    require_exact_mutex()
    verify_origin(root)
    verify_preopen(root)
    proof=validate_native_proof(root)
    before=ppi_watch_snapshot()
    stable='porota-trading-bot:17.0.0-rc6'
    require(command_stdout('docker','image','inspect','-f','{{.Id}}',stable)==IMAGE_ID,
            'A179_CRITICAL_IMAGE_TAG_CHANGED')
    unit=command_stdout('systemctl','cat','porota-critical-approval-rc6.service')
    require(stable in unit and '/opt/porota-control-plane-rc6/current:/code:ro' in unit,
            'A179_CRITICAL_IMAGE_UNIT_CHANGED')
    for service in ('porota-critical-github-proxy-rc6.service',
                    'porota-critical-approval-rc6.service'):
        require(systemctl_state('is-active',service)=='active',
                'A179_CRITICAL_PRE_RESTART_SERVICE_RED:'+service)
    # The installed image is already immutable. This disposable compatibility
    # check is the same sandboxed command used by Deploy V2, not a build.
    checked_process(('docker','run','--rm','--pull','never','--read-only',
                     '--user','botuser','--cap-drop','ALL',
                     '--security-opt','no-new-privileges:true','--pids-limit','32',
                     '--memory','128m','--tmpfs','/tmp:rw,noexec,nosuid,size=16m',
                     '-e','PYTHONDONTWRITEBYTECODE=1','-e','PYTHONPATH=/code',
                     '-v','/opt/porota-control-plane-rc6/current:/code:ro',
                     '--entrypoint','python',stable,'-c',
                     'import fm_critical_approval_unix_runtime_rc6'),
                    120,'A179_CRITICAL_CANDIDATE_COMPATIBILITY_RED')
    checked_process(('systemctl','restart','porota-critical-approval-rc6.service'),
                    120,'A179_CRITICAL_APPROVAL_RESTART_RED')
    for _ in range(40):
        time.sleep(2)
        try:
            value=docker_inspect('porota_critical_approval_rc6')
        except (OSError,subprocess.CalledProcessError,ValueError):
            continue
        if (systemctl_state('is-active','porota-critical-approval-rc6.service')=='active'
                and value.get('Image')==IMAGE_ID
                and value.get('State',{}).get('Status')=='running'
                and value.get('State',{}).get('Health',{}).get('Status')=='healthy'
                and value.get('RestartCount')==0):
            break
    else:
        raise ValueError('A179_CRITICAL_CANDIDATE_DID_NOT_BECOME_HEALTHY')
    checked_process(('docker','exec','porota_critical_approval_rc6','python',
                     '-c','from fm_critical_approval_unix_runtime_rc6 import '
                          'UnixGithubIssuesClient; '
                          'h=UnixGithubIssuesClient().health(); '
                          'assert h.get("status")=="ok" and '
                          'h.get("capability")=="issues_only"'),
                    40,'A179_CRITICAL_GATEWAY_CAPABILITY_RED')
    verify_origin(root,critical_image=IMAGE_ID)
    require(ppi_watch_snapshot()==before,'A179_PPI_WATCH_CHANGED_DURING_CRITICAL')
    return {'status':'GREEN_CRITICAL_IMAGE_RECONCILED',
            'candidate':CANDIDATE,'image':IMAGE_ID,
            'native_run_id':proof['run_id'],
            'critical_capability':'issues_only','real_orders_sent':0,
            'operator_no_go':'HELD','ppi_watch_mutations':0}


POLICY_SHA256 = '2a06248cda503080e8b78ceb52211a210a6784d2423c3461da5e2a6053173688'


def candidate_policy(root=ROOT):
    path=root/'ops/policy/host-control-plane-reconciliation-v2.json'
    require(path.is_file() and not path.is_symlink() and
            hashlib.sha256(path.read_bytes()).hexdigest()==POLICY_SHA256,
            'A179_DEPLOY_SCHEDULER_POLICY_CHANGED')
    return base64.b64encode(path.read_bytes()).decode('ascii')


def restore_exact_schedulers(root=ROOT) -> dict:
    require_exact_mutex()
    verify_origin(root,critical_image=IMAGE_ID)
    verify_preopen(root,critical_image=IMAGE_ID)
    proof=validate_native_proof(root)
    before=ppi_watch_snapshot()
    sys.path.insert(0,str(root))
    from scripts import rc6_deploy_scheduler_suspend as scheduler
    result=scheduler.run(CANDIDATE,'restore',candidate_policy(root))
    require(result.get('status')=='RESTORED' and
            result.get('candidate_sha')==CANDIDATE,
            'A179_SCHEDULER_RESTORE_RECEIPT_RED')
    verify_origin(root,critical_image=IMAGE_ID,scheduler_status='RESTORED')
    # Once restored, use the DAILY active-timer path, not --deploy-scheduler-
    # candidate; the latter deliberately requires all fifteen timers paused.
    verify_pending_restored_preopen(root)
    require(ppi_watch_snapshot()==before,'A179_PPI_WATCH_CHANGED_DURING_SCHEDULERS')
    return {'status':'GREEN_SCHEDULERS_RESTORED','candidate':CANDIDATE,
            'restored_timer_count':16,'native_run_id':proof['run_id'],
            'preopen_timer':'INACTIVE_DISABLED_NO_GO',
            'real_orders_sent':0,'ppi_watch_mutations':0}


def verify_exact_restored_schedulers(root=ROOT) -> dict:
    require_exact_mutex()
    verify_origin(root,critical_image=IMAGE_ID,scheduler_status='RESTORED')
    proof=validate_native_proof(root)
    before=ppi_watch_snapshot()
    sys.path.insert(0,str(root))
    from scripts import rc6_deploy_scheduler_suspend as scheduler
    result=scheduler.run(CANDIDATE,'verify',candidate_policy(root))
    require(result.get('status')=='RESTORED','A179_SCHEDULER_VERIFY_RED')
    verify_pending_restored_preopen(root)
    require(ppi_watch_snapshot()==before,'A179_PPI_WATCH_CHANGED_DURING_VERIFY')
    return {'status':'GREEN_RESTORED_SCHEDULERS_INDEPENDENTLY_VERIFIED',
            'candidate':CANDIDATE,'native_run_id':proof['run_id'],
            'operator_no_go':'HELD','real_orders_sent':0,'ppi_watch_mutations':0}


def retire_empty_backups(root=ROOT) -> dict:
    """The operator-authorized zero-file receipt, not a general cleanup."""
    require_exact_mutex()
    verify_origin(root,critical_image=IMAGE_ID,scheduler_status='RESTORED')
    proof=validate_native_proof(root)
    before=ppi_watch_snapshot()
    sys.path.insert(0,str(root))
    from scripts import rc6_679_backup_suspension as backup
    require(backup.inventory()==[],'A179_BACKUP_INVENTORY_CHANGED')
    plan=root/'data/deploy'/('backup-retire-plan-'+CANDIDATE+'.json')
    receipt_path=root/'data/deploy'/('backup-retire-'+CANDIDATE+'.json')
    require(not plan.exists() and not plan.is_symlink() and
            not receipt_path.exists() and not receipt_path.is_symlink(),
            'A179_BACKUP_ALREADY_PLANNED_OR_RETIRED')
    checked_process(('python3',str(root/'scripts/rc6_679_backup_suspension.py'),
                     '--candidate-sha',CANDIDATE,'--plan-out',str(plan)),
                    90,'A179_BACKUP_EMPTY_PLAN_RED')
    checked_process(('python3',str(root/'scripts/rc6_679_backup_suspension.py'),
                     '--candidate-sha',CANDIDATE,'--apply-plan',str(plan),
                     '--require-empty'),90,'A179_BACKUP_EMPTY_APPLY_RED')
    receipt=safe_generated_allow_status(receipt_path,'DONE',size_limit=16384)
    require(receipt.get('schema')=='RC6_679_BACKUP_RETIRE_V1' and
            receipt.get('candidate_sha')==CANDIDATE and
            receipt.get('image_id')==IMAGE_ID and
            type(receipt.get('count')) is int and receipt['count']==0,
            'A179_BACKUP_EMPTY_RECEIPT_RED')
    verify_origin(root,critical_image=IMAGE_ID,scheduler_status='RESTORED')
    require(ppi_watch_snapshot()==before,'A179_PPI_WATCH_CHANGED_DURING_BACKUP')
    return {'status':'GREEN_EXACT_EMPTY_BACKUP_RECEIPT',
            'candidate':CANDIDATE,'count':0,'native_run_id':proof['run_id'],
            'operator_no_go':'HELD','real_orders_sent':0,
            'ppi_watch_mutations':0}


def safe_generated_allow_status(path, status, size_limit=2*1024*1024):
    require(path.is_file() and not path.is_symlink() and path.stat().st_nlink==1
            and 0<path.stat().st_size<=size_limit,
            'A179_RECEIPT_MISSING_OR_UNSAFE:'+path.name)
    value=json.loads(path.read_bytes())
    require(type(value) is dict and value.get('status')==status,
            'A179_RECEIPT_STATUS_RED:'+path.name)
    return value


LEGACY_NATIVE_DIGESTS = {
    'native-generation-acceptance.json':
        'e8a91fc827a839c55d7e2f546603107f0dcbe2ee48210aec84b4e181f6f5b320',
    'native-ten-generation-soak.json':
        '1b32904a9d0bf7f3fd3003dd3758ea8f3bcf2b2b198f1ba5dc2b16364d7131d0',
}


def escrow_and_publish_current_native(root, proof):
    """Preserve exact old GREEN bytes, then materialize the new SHA proof.

    Each replacement is atomic and idempotent against the exact before/after
    digests. A crash keeps the old evidence in its digest-named archive and
    leaves CURRENT_STATE_V2 PENDING until the final separate commit.
    """
    import stat
    import tempfile
    deploy_dir=root/'data/deploy'
    changes=[]
    for original, new in (('native-generation-acceptance.json',proof['first']),
                          ('native-ten-generation-soak.json',proof['soak'])):
        old_digest=LEGACY_NATIVE_DIGESTS[original]
        old_path=deploy_dir/original
        archive=deploy_dir/(original.removesuffix('.json')+
                            '.pre-a179-'+old_digest[:16]+'.json')
        source=deploy_dir/new
        require(old_path.is_file() and not old_path.is_symlink() and
                old_path.stat().st_nlink==1 and 0<old_path.stat().st_size<2*1024*1024,
                'A179_NATIVE_OLD_CANONICAL_UNSAFE:'+original)
        expected_new=proof['first_sha256'] if original.startswith(
            'native-generation') else proof['soak_sha256']
        source_raw=source.read_bytes()
        require(hashlib.sha256(source_raw).hexdigest()==expected_new,
                'A179_NATIVE_NEW_SCOPED_PROOF_CHANGED:'+new)
        old_raw=old_path.read_bytes()
        current_sha=hashlib.sha256(old_raw).hexdigest()
        require(current_sha in (old_digest,expected_new),
                'A179_NATIVE_CANONICAL_DRIFT:'+original)
        if archive.exists() or archive.is_symlink():
            exact_json(archive,old_digest,2*1024*1024)
        elif current_sha==old_digest:
            fd=os.open(archive,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
            with os.fdopen(fd,'wb') as handle:
                handle.write(old_raw)
                handle.flush()
                os.fsync(handle.fileno())
            require(hashlib.sha256(archive.read_bytes()).hexdigest()==old_digest,
                    'A179_NATIVE_ESCROW_DIGEST_RED:'+original)
        else:
            raise ValueError('A179_NATIVE_ESCROW_ABSENT_FOR_CURRENT_PROOF:'+original)
        if current_sha==old_digest:
            original_stat=old_path.stat()
            fd,temporary=tempfile.mkstemp(prefix='.a179-native-',suffix='.tmp',
                                          dir=deploy_dir)
            try:
                os.fchown(fd,original_stat.st_uid,original_stat.st_gid)
                os.fchmod(fd,stat.S_IMODE(original_stat.st_mode))
                with os.fdopen(fd,'wb') as out:
                    out.write(source_raw)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temporary,old_path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        require(hashlib.sha256(old_path.read_bytes()).hexdigest()==expected_new,
                'A179_NATIVE_CANONICAL_NEW_PROOF_MISMATCH:'+original)
        changes.append({'path':original,'old_sha256':old_digest,
                        'new_sha256':expected_new,'old_archive':archive.name,
                        'exact_new_source':new})
    manifest={'schema':'rc6.a179.native-proof-promotion.v1',
              'candidate':CANDIDATE,'candidate_tree':TREE,
              'native_run_id':proof['run_id'],
              'mode':proof['mode'],'receipts':changes,
              'real_orders_sent':0,'ppi_watch_mutations':0}
    target=deploy_dir/('native-proof-migration-a179-'+CANDIDATE+'.json')
    raw=(json.dumps(manifest,sort_keys=True)+'\n').encode()
    if target.exists() or target.is_symlink():
        require(target.is_file() and not target.is_symlink() and
                target.read_bytes()==raw,'A179_NATIVE_PROMOTION_MANIFEST_CHANGED')
    else:
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
        with os.fdopen(fd,'wb') as out:
            out.write(raw);out.flush();os.fsync(out.fileno())
    directory=os.open(deploy_dir,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)
    return hashlib.sha256(raw).hexdigest()


def finalize_exact_runtime(root=ROOT) -> dict:
    """V2 PENDING->VALIDATED after all independently recorded host gates."""
    import grp
    import tempfile
    require_exact_mutex()
    verify_origin(root,critical_image=IMAGE_ID,scheduler_status='RESTORED')
    verify_pending_restored_preopen(root)
    proof=validate_native_proof(root)
    deploy_dir=root/'data/deploy'
    latest=safe_generated(deploy_dir/proof['stability_receipts'][-1])
    endpoints=latest.get('http_status')
    require(type(endpoints) is dict and len(endpoints)>=1 and
            all(type(code) is int and code==200 for code in endpoints.values()),
            'A179_FINAL_DASHBOARD_HTTP_NOT_ALL_200')
    backup=safe_generated_allow_status(
        deploy_dir/('backup-retire-'+CANDIDATE+'.json'),'DONE',16384)
    require(backup.get('candidate_sha')==CANDIDATE and
            backup.get('image_id')==IMAGE_ID and
            type(backup.get('count')) is int and backup['count']==0,
            'A179_BACKUP_FINAL_RECEIPT_RED')
    from rc6_paper_admission_gate import admission_status, operator_pause_reason
    require(operator_pause_reason(datetime.now(TZ))==
            'OPERATOR_NO_GO_PENDING_EXPLICIT_REARM' and
            admission_status()[0] is False,
            'A179_PAPER_ADMISSION_REARMED_UNEXPECTEDLY')
    ppi_before=ppi_watch_snapshot()
    original=deploy_dir/'CURRENT_STATE_V2.json'
    state=exact_json(original,MARKER_SHA256)
    require(state.get('validation_status')=='DEPLOYED_VALIDATION_PENDING',
            'A179_FINAL_MARKER_NO_LONGER_PENDING')
    escrow_sha=escrow_and_publish_current_native(root,proof)
    state['dashboard_health_http']=200
    state['native_acceptance_mode']=proof['mode']
    state['native_acceptance_run_id']=proof['run_id']
    state['native_proof_promotion_sha256']=escrow_sha
    state['source_freshness_renewed']=(False if proof['mode']=='CLOSED_IDLE' else None)
    state['validation_status']='VALIDATED_RUNTIME'
    state['recorded_at']=datetime.now(timezone.utc).isoformat()
    fd,temporary=tempfile.mkstemp(prefix='.CURRENT_STATE_V2.',suffix='.tmp',
                                  dir=deploy_dir)
    try:
        os.fchown(fd,0,grp.getgrnam('porotaadmin').gr_gid)
        os.fchmod(fd,0o640)
        with os.fdopen(fd,'w',encoding='utf-8') as out:
            json.dump(state,out,indent=2,sort_keys=True)
            out.write('\n');out.flush();os.fsync(out.fileno())
        require(ppi_watch_snapshot()==ppi_before,
                'A179_PPI_WATCH_CHANGED_BEFORE_MARKER_WRITE')
        read_book(root)
        os.replace(temporary,original)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    require(json.loads(original.read_bytes()).get('validation_status')==
            'VALIDATED_RUNTIME','A179_FINAL_MARKER_DID_NOT_PERSIST')
    return {'status':'VALIDATED_RUNTIME','candidate':CANDIDATE,
            'deploy':DEPLOY,'image':IMAGE_ID,'native_mode':proof['mode'],
            'native_run_id':proof['run_id'],'old_receipts_archived':2,
            'backup_retired_count':0,'operator_no_go':'HELD',
            'real_orders_sent':0,'ppi_watch_mutations':0}


FINAL_FAILED_CLEANUP_RUN = '40921652908'


def exact_expected_idle_final_barrier():
    import stat
    path=Path('/run/lock/porota-rc6-deploy-v2-attempt.json')
    require(path.is_file() and not path.is_symlink() and
            path.stat().st_nlink==1 and path.stat().st_uid==0 and
            stat.S_IMODE(path.stat().st_mode)==0o600 and
            0<path.stat().st_size<=1024,
            'A179_POSTVALIDATED_BARRIER_UNSAFE')
    prior=json.loads(path.read_bytes())
    require(prior=={'candidate':CANDIDATE,'run':FINAL_FAILED_CLEANUP_RUN,
                    'attempt':'1','phase':'cleanup_final','status':'IDLE'},
            'A179_POSTVALIDATED_BARRIER_NOT_EXACT_IDLE')
    require(not command_stdout('docker','ps','--format','{{.Names}}',
                               '--filter','name=porota-deploy-contract-'),
            'A179_POSTVALIDATED_CONTRACT_PROCESS_ACTIVE')
    return prior


def verify_independent_final_state(root=ROOT, *, allow_exact_owned_mutex=False,
                                   allow_exact_idle_barrier=False) -> dict:
    """Independent RO closure; critically, NO STAGED preopen fallback."""
    sys.path.insert(0,str(root))
    deploy_dir=root/'data/deploy'
    marker_path=deploy_dir/'CURRENT_STATE_V2.json'
    require(marker_path.is_file() and not marker_path.is_symlink() and
            marker_path.stat().st_nlink==1 and marker_path.stat().st_size<65536,
            'A179_VALIDATED_MARKER_UNSAFE')
    marker=json.loads(marker_path.read_bytes())
    require(marker.get('validation_status')=='VALIDATED_RUNTIME' and
            marker.get('candidate_sha')==CANDIDATE and
            marker.get('deploy_sha')==DEPLOY and marker.get('image_id')==IMAGE_ID and
            marker.get('mode')=='PRODUCTION_PAPER' and
            type(marker.get('real_orders_sent')) is int and marker['real_orders_sent']==0 and
            marker.get('real_order_routes')=='NOT_CALLED' and
            marker.get('ppi_watch_untouched') is True,
            'A179_FINAL_MARKER_IDENTITY_OR_SAFETY_RED')
    proof=validate_native_proof(root)
    require(marker.get('native_acceptance_mode')==proof['mode'] and
            marker.get('native_acceptance_run_id')==proof['run_id'],
            'A179_FINAL_NATIVE_PROVENANCE_RED')
    for name in CONTAINERS:
        value=docker_inspect(name)
        require(value.get('Image')==IMAGE_ID and
                value.get('State',{}).get('Status')=='running' and
                value.get('RestartCount')==0,
                'A179_FINAL_OBSERVER_OR_DASHBOARD_DRIFT:'+name)
    crit=docker_inspect('porota_critical_approval_rc6')
    require(crit.get('Image')==IMAGE_ID and
            crit.get('State',{}).get('Status')=='running' and
            crit.get('State',{}).get('Health',{}).get('Status')=='healthy',
            'A179_FINAL_CRITICAL_DRIFT')
    require(safe_generated_allow_status(
        deploy_dir/('backup-retire-'+CANDIDATE+'.json'),'DONE',16384).get('count')==0,
        'A179_FINAL_BACKUP_RECEIPT_RED')
    require(safe_generated_allow_status(
        deploy_dir/('scheduler-suspend-'+CANDIDATE+'.json'),'RESTORED',65536)
            .get('candidate_sha')==CANDIDATE,'A179_FINAL_SCHEDULER_RECEIPT_RED')
    for name,sha in (('native-generation-acceptance.json',proof['first_sha256']),
                     ('native-ten-generation-soak.json',proof['soak_sha256'])):
        require(hashlib.sha256((deploy_dir/name).read_bytes()).hexdigest()==sha,
                'A179_FINAL_CANONICAL_PROOF_SOURCE_CHANGED:'+name)
    from rc6_paper_admission_gate import admission_status,operator_pause_reason
    require(operator_pause_reason(datetime.now(TZ))==
            'OPERATOR_NO_GO_PENDING_EXPLICIT_REARM' and
            admission_status()[0] is False,
            'A179_FINAL_PAPER_ADMISSION_WAS_REARMED')
    phase=read_book(root)
    require(not os.environ.get('RC6_A145_QUIESCENCE_GZIP_B64'),
            'A179_POSTFINAL_MUST_USE_INSTALLED_NOT_STAGED_HELPER')
    sys.path.insert(0,str(root))
    from scripts.rc6_deploy_preopen_gate import strict_json,validate_report
    import ak_byma_calendar
    for part in ('T_MINUS_45','T_MINUS_10'):
        finished=subprocess.run((sys.executable,str(root/'rc6_preopen.py'),
                                 '--phase',part),capture_output=True,text=True,
                                env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),
                                timeout=180,check=False)
        require(finished.returncode==0,'A179_POSTFINAL_NATIVE_PREOPEN_RED:'+part)
        report=strict_json(finished.stdout)
        decision=validate_report(report,phase=part,return_code=0,
                                 now=datetime.now(TZ),
                                 is_operational=ak_byma_calendar.es_dia_habil_operativo)
        require(decision.get('accepted') is True and
                decision.get('preopen_status')=='PAUSED_NO_GO' and
                decision.get('execution_authorized') is False and
                report.get('checks',{}).get('required_rc6_timers',{}).get('state')=='GREEN' and
                report.get('paper_admission_hold')==
                'OPERATOR_NO_GO_PENDING_EXPLICIT_REARM',
                'A179_POSTFINAL_NO_GO_TIMER_OR_AUTH_RED:'+part)
    require(not (allow_exact_owned_mutex and allow_exact_idle_barrier),
            'A179_FINAL_MUTEX_MODE_UNSAFE')
    if allow_exact_owned_mutex:
        require_exact_mutex()
    elif allow_exact_idle_barrier:
        exact_expected_idle_final_barrier()
    else:
        require(not (Path('/run/lock')/'porota-rc6-deploy-v2-attempt.json').exists(),
                'A179_FINAL_ATTEMPT_MUTEX_NOT_RELEASED')
    return {'schema':'rc6.a179.final-independent-readonly.v1',
            'status':'VALIDATED_RUNTIME','candidate':CANDIDATE,
            'deploy':DEPLOY,'image':IMAGE_ID,'native_mode':proof['mode'],
            'native_run_id':proof['run_id'],'session':list(phase),
            'preopen_phases':'BOTH_NATIVE_PAUSED_NO_GO_GREEN',
            'legacy_native_receipts':'ESCROW_PRESERVED',
            'real_orders_sent':0,'operator_no_go':'HELD',
            'host_mutations':0,'ppi_watch_mutations':0}


def validated_idle_preflight(root=ROOT) -> dict:
    """Read-only exact host proof while prior failed runner left an IDLE lock."""
    observed=verify_independent_final_state(root,allow_exact_idle_barrier=True)
    require(observed['status']=='VALIDATED_RUNTIME' and
            observed['operator_no_go']=='HELD' and
            observed['real_orders_sent']==0,
            'A179_POSTVALIDATED_READONLY_NOT_GREEN')
    return {'status':'GREEN_VALIDATED_RUNTIME_EXACT_IDLE_BARRIER',
            'candidate':CANDIDATE,'run':FINAL_FAILED_CLEANUP_RUN,
            'barrier':'IDLE_CLEANUP_FINAL','preopen':observed['preopen_phases'],
            'native_run_id':observed['native_run_id'],'operator_no_go':'HELD',
            'real_orders_sent':0,'host_mutations':0,'ppi_watch_mutations':0}


def release_validated_attempt(root=ROOT) -> dict:
    """Host mutex performs the only unlink after full native RO success."""
    require_exact_mutex()
    observed=verify_independent_final_state(root,allow_exact_owned_mutex=True)
    require(observed['status']=='VALIDATED_RUNTIME' and
            observed['real_orders_sent']==0 and
            observed['preopen_phases']=='BOTH_NATIVE_PAUSED_NO_GO_GREEN',
            'A179_POSTVALIDATED_RELEASE_NOT_SAFE')
    return {'status':'GREEN_VALIDATED_RUNTIME_BARRIER_RELEASED',
            'candidate':CANDIDATE,'run':FINAL_FAILED_CLEANUP_RUN,
            'native_run_id':observed['native_run_id'],
            'operator_no_go':'HELD','real_orders_sent':0,
            'host_mutations':0,'ppi_watch_mutations':0}


def cleanup_forward_attempt(root=ROOT) -> dict:
    """Prove a safely stopped write attempt; never change runtime or timers.

    The canonical mutex wrapper itself refuses to retire INFLIGHT attempts.
    A failed health check here leaves the lock barrier for manual RCA.
    """
    require_exact_mutex()
    exact_json(root/'data/deploy/CURRENT_STATE_V2.json', MARKER_SHA256)
    proof=validate_native_proof(root)
    for name in CONTAINERS:
        value=docker_inspect(name)
        require(value.get('Image')==IMAGE_ID and
                value.get('State',{}).get('Status')=='running' and
                value.get('RestartCount')==0,
                'A179_FAILED_CLEANUP_RUNTIME_UNKNOWN:'+name)
    critical=docker_inspect('porota_critical_approval_rc6')
    require(critical.get('Image') in (CRITICAL_IMAGE,IMAGE_ID) and
            critical.get('State',{}).get('Status')=='running' and
            critical.get('State',{}).get('Health',{}).get('Status')=='healthy',
            'A179_FAILED_CLEANUP_CRITICAL_UNKNOWN')
    sched=root/'data/deploy'/('scheduler-suspend-'+CANDIDATE+'.json')
    require(sched.is_file() and not sched.is_symlink() and
            sched.stat().st_nlink==1 and sched.stat().st_size<=65536,
            'A179_FAILED_CLEANUP_SCHEDULER_UNSAFE')
    snapshot=json.loads(sched.read_bytes())
    require(snapshot.get('candidate_sha')==CANDIDATE and
            snapshot.get('status') in ('SUSPENDED','RESTORED') and
            snapshot.get('operator_pause_timer')=='porota-preopen-rc6.timer',
            'A179_FAILED_CLEANUP_SCHEDULER_UNKNOWN')
    require(systemctl_state('is-enabled','porota-preopen-rc6.timer')=='disabled'
            and systemctl_state('is-active','porota-preopen-rc6.timer')=='inactive',
            'A179_FAILED_CLEANUP_NO_GO_TIMER_REARMED')
    sys.path.insert(0,str(root))
    from rc6_paper_admission_gate import admission_status,operator_pause_reason
    require(operator_pause_reason(datetime.now(TZ))==
            'OPERATOR_NO_GO_PENDING_EXPLICIT_REARM' and
            admission_status()[0] is False,
            'A179_FAILED_CLEANUP_PAPER_NO_GO_MISSING')
    read_book(root)
    return {'status':'SAFE_FAILED_ATTEMPT_RELEASE_ONLY',
            'candidate':CANDIDATE,'native_run_id':proof['run_id'],
            'marker':'DEPLOYED_VALIDATION_PENDING',
            'scheduler':snapshot['status'],'critical_image':critical['Image'],
            'real_orders_sent':0,'operator_no_go':'HELD',
            'host_mutations':0,'ppi_watch_mutations':0}


def finish_postfailure(root=ROOT) -> dict:
    """One exclusive V2 continuation: broker, timers, backup, then marker.

    The normal Deploy V2 has already done build-once, frozen transfer and
    installed two exact images. This operation NEVER repeats that work.
    Its stdout records each successful boundary before the next write.
    """
    require_exact_mutex()
    before=ppi_watch_snapshot()
    critical=reconcile_critical_image(root)
    print('RC6_A179_PHASE_CRITICAL=GREEN|image='+IMAGE_ID,flush=True)
    restored=restore_exact_schedulers(root)
    print('RC6_A179_PHASE_SCHEDULERS=GREEN|count='+str(
        restored['restored_timer_count'])+'|preopen=DISABLED',flush=True)
    independently=verify_exact_restored_schedulers(root)
    require(independently['status'].startswith('GREEN'),
            'A179_SCHEDULER_VERIFY_RED')
    print('RC6_A179_PHASE_SCHEDULER_VERIFY=GREEN|NO_GO',flush=True)
    backup=retire_empty_backups(root)
    require(backup['count']==0,'A179_UNEXPECTED_BACKUP_DELETION')
    print('RC6_A179_PHASE_EMPTY_BACKUP=GREEN|files_deleted=0',flush=True)
    require(ppi_watch_snapshot()==before,'A179_PPI_WATCH_CHANGED_DURING_CLOSURE')
    final=finalize_exact_runtime(root)
    print('RC6_A179_PHASE_FINAL_MARKER=VALIDATED_RUNTIME|NO_GO_HELD',flush=True)
    require(final['status']=='VALIDATED_RUNTIME' and
            final['real_orders_sent']==0 and critical['real_orders_sent']==0,
            'A179_FINAL_WRITE_DID_NOT_COMPLETE')
    return {'status':'VALIDATED_RUNTIME','candidate':CANDIDATE,
            'deploy':DEPLOY,'image':IMAGE_ID,
            'native_run_id':final['native_run_id'],
            'native_mode':final['native_mode'],
            'reused_frozen_artifact':True,'host_rebuilds':0,
            'backup_deleted_files':0,'operator_no_go':'HELD',
            'real_orders_sent':0,'ppi_watch_mutations':0}


def finish_from_restored(root=ROOT) -> dict:
    """Continue exactly the safely RESTORED but still PENDING installed image.

    This transition is needed after A179's independently certified postrestore
    preopen rejection. It does not restart healthy services, reconstruct an
    artifact, modify the DB, or rearm the operator PAPER gate.
    """
    require_exact_mutex()
    verify_origin(root,critical_image=IMAGE_ID,scheduler_status='RESTORED')
    proof=validate_native_proof(root)
    before=ppi_watch_snapshot()
    checked=verify_pending_restored_preopen(root)
    require(checked['status']=='EXACT_EXPECTED_PENDING_MARKER_RED_ONLY',
            'A179_POSTRESTORE_PENDING_GUARD_RED')
    print('RC6_A179_PHASE_TRANSITION=EXACT_EXPECTED_MARKER_RED_ONLY',flush=True)
    restored=verify_exact_restored_schedulers(root)
    print('RC6_A179_PHASE_SCHEDULERS=GREEN_RESTORED_VERIFIED|NO_GO',flush=True)
    backup=retire_empty_backups(root)
    require(backup['count']==0,'A179_UNEXPECTED_BACKUP_DELETION')
    print('RC6_A179_PHASE_EMPTY_BACKUP=GREEN|files_deleted=0',flush=True)
    require(ppi_watch_snapshot()==before,'A179_PPI_WATCH_CHANGED_BEFORE_FINAL')
    final=finalize_exact_runtime(root)
    print('RC6_A179_PHASE_FINAL_MARKER=VALIDATED_RUNTIME|NO_GO_HELD',flush=True)
    post=verify_independent_final_state(root,allow_exact_owned_mutex=True)
    require(post['status']=='VALIDATED_RUNTIME' and
            post['real_orders_sent']==0 and
            post['preopen_phases']=='BOTH_NATIVE_PAUSED_NO_GO_GREEN',
            'A179_NO_GO_NATIVE_POSTMARKER_RED')
    print('RC6_A179_PHASE_NATIVE_POSTMARKER=GREEN|BOTH_PREOPEN_PAUSED_NO_GO',flush=True)
    require(ppi_watch_snapshot()==before,'A179_PPI_WATCH_CHANGED_AFTER_FINAL')
    return {'status':'VALIDATED_RUNTIME','candidate':CANDIDATE,
            'deploy':DEPLOY,'image':IMAGE_ID,
            'native_run_id':proof['run_id'],'native_mode':proof['mode'],
            'postmarker_native_preopen':'BOTH_PAUSED_NO_GO_GREEN',
            'reused_frozen_artifact':True,'host_rebuilds':0,
            'critical_restarts':0,'backup_deleted_files':0,
            'operator_no_go':'HELD','real_orders_sent':0,'ppi_watch_mutations':0}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',required=True,
                        choices=('preflight','preopen','closure-readiness',
                                 'native-acceptance','cleanup-native-attempt',
                                 'receipt-census','release-native-attempt',
                                 'closure-preflight',
                                 'critical-reconcile','scheduler-restore',
                                 'scheduler-verify','backup-retire','finalize',
                                 'postfinal','cleanup-forward-attempt',
                                 'finish-postfailure','pending-restored-preopen',
                                 'finish-restored','restored-readiness',
                                 'validated-idle-preflight',
                                 'release-validated-attempt'))
    args=parser.parse_args(argv)
    try:
        modes={'preflight':verify_origin,'preopen':verify_preopen,
               'closure-readiness':verify_closure_readiness,
               'receipt-census':receipt_census,
               'native-acceptance':run_native_acceptance,
               'cleanup-native-attempt':cleanup_failed_native_attempt,
               'release-native-attempt':release_native_attempt,
               'closure-preflight':closure_preflight,
               'critical-reconcile':reconcile_critical_image,
               'scheduler-restore':restore_exact_schedulers,
               'scheduler-verify':verify_exact_restored_schedulers,
               'backup-retire':retire_empty_backups,
               'finalize':finalize_exact_runtime,
               'postfinal':verify_independent_final_state,
               'cleanup-forward-attempt':cleanup_forward_attempt,
               'finish-postfailure':finish_postfailure,
               'pending-restored-preopen':verify_pending_restored_preopen,
               'restored-readiness':restored_readiness,
               'validated-idle-preflight':validated_idle_preflight,
               'release-validated-attempt':release_validated_attempt,
               'finish-restored':finish_from_restored}
        result=modes[args.mode]()
        print('RC6_A179_RESUME_'+args.mode.upper()+'='+json.dumps(result,sort_keys=True))
        return 0
    except Exception as exc:
        print('RC6_A179_RESUME_RED='+json.dumps({'mode':args.mode,
              'exception':type(exc).__name__,'reason':str(exc)[:240]},sort_keys=True))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
