#!/usr/bin/env python3
"""Exact A178 Deploy V2 postfailure validation, initially READ_ONLY.

This source is streamed by GitHub Actions to the target. No host mutation
occurs in the ``preflight`` or ``preopen`` modes. Future write modes require
an independent reviewed patch and the exclusive RC6 host mutex.
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


def verify_origin(root=ROOT) -> dict:
    deploy_dir = root/'data/deploy'
    marker = exact_json(deploy_dir/'CURRENT_STATE_V2.json', MARKER_SHA256)
    q = exact_json(deploy_dir/('quiescence-'+CANDIDATE+'.json'), QUIESCENCE_SHA256)
    suspend = exact_json(deploy_dir/('scheduler-suspend-'+CANDIDATE+'.json'), SCHEDULER_SHA256)
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
            suspend.get('status') == 'SUSPENDED' and
            len(suspend.get('timers',[])) == 16 and
            set(suspend.get('attempted',[])) == set(suspend['timers']),
            'A179_SCHEDULER_ORIGIN_RED')
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
    require(critical.get('Image') == CRITICAL_IMAGE and
            critical.get('State',{}).get('Status') == 'running' and
            critical.get('State',{}).get('Health',{}).get('Status') == 'healthy',
            'A179_CRITICAL_SERVICE_RED')
    phase = read_book(root)
    require(q.get('candidate_runtime_after_failure',{}).get('flat_book',{}).get('real_orders_sent') == 0 and
            q.get('candidate_runtime_after_failure',{}).get('critical_admission',{}).get('capability') == 'issues_only',
            'A179_PRIOR_PAPER_OR_CRITICAL_CHANGED')
    return {'candidate': CANDIDATE, 'deploy': DEPLOY, 'image': IMAGE_ID,
            'session': list(phase), 'marker':'PENDING', 'frozen_sha256':FROZEN_SHA256,
            'quiescence_sha256':QUIESCENCE_SHA256, 'scheduler_sha256':SCHEDULER_SHA256,
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


def verify_preopen(root=ROOT) -> dict:
    verify_origin(root)
    sys.path.insert(0, str(root))
    from scripts.rc6_deploy_preopen_gate import strict_json, validate_report
    import ak_byma_calendar
    env = staged_env(root)
    results = {}
    for phase in ('T_MINUS_45','T_MINUS_10'):
        cmd=[sys.executable, str(root/'rc6_preopen.py'), '--phase',phase,
             '--deploy-scheduler-candidate',CANDIDATE]
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


def command_stdout(*argv):
    return subprocess.check_output(argv, text=True, timeout=15).strip()


def systemctl_state(action, unit):
    done=subprocess.run(('systemctl',action,unit), capture_output=True,
                        text=True,timeout=15,check=False)
    require(done.returncode in (0,1,3), 'A179_SYSTEMCTL_QUERY_FAILED:'+unit)
    return done.stdout.strip()


def verify_closure_readiness(root=ROOT) -> dict:
    """Predict every remaining host-write prerequisite without touching it.

    Restore the installed artifact only if the prior attempt is definitely
    finished, the stable tag and approval unit agree, and the empty-backup
    guard is already satisfied. No docker run/systemctl start occurs here.
    """
    verify_origin(root)
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
            'backup_inventory_count':0, 'mutex_barrier':'ABSENT',
            'readiness':'NO_PREMATURE_RUNTIME_FINALIZATION',
            'real_orders_sent':0,'host_mutations':0,'ppi_watch_mutations':0}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',required=True,
                        choices=('preflight','preopen','closure-readiness'))
    args=parser.parse_args(argv)
    try:
        result=(verify_origin() if args.mode=='preflight' else
                verify_preopen() if args.mode=='preopen' else
                verify_closure_readiness())
        print('RC6_A179_RESUME_'+args.mode.upper()+'='+json.dumps(result,sort_keys=True))
        return 0
    except Exception as exc:
        print('RC6_A179_RESUME_RED='+json.dumps({'mode':args.mode,
              'exception':type(exc).__name__,'reason':str(exc)[:240]},sort_keys=True))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
