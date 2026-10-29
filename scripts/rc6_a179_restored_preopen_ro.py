#!/usr/bin/env python3
"""A179 exact read-only report of normal preopen after scheduler restoration.

Does not start timers, update receipts, access PPI Watch, or authorize orders.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path('/opt/porota-trading')
CANDIDATE = '7d62d2f4ed353231da8566a360a1179e99bcc7eb'
TREE = '31ced11ce047bfa092f5398c13aeb37b74afd629'
IMAGE = 'sha256:094b0f7485d835c43be4c8f519051beb21d0f4fbdfe817dd2eeb30ab341a06ba'
HELPER_DIGEST = '0f016652f884cd27bba0e29e3128da615266d09b8bf7a0a1f9a063d1386f0fc6'


def must(condition, reason):
    if not condition:
        raise ValueError(reason)


def main():
    marker = json.loads((ROOT/'data/deploy/CURRENT_STATE_V2.json').read_bytes())
    scheduler = json.loads((ROOT/'data/deploy'/('scheduler-suspend-'+CANDIDATE+'.json')).read_bytes())
    must(marker.get('candidate_sha') == CANDIDATE and
         marker.get('candidate_tree_sha',TREE) == TREE and
         marker.get('image_id') == IMAGE and
         marker.get('validation_status') == 'DEPLOYED_VALIDATION_PENDING' and
         marker.get('mode') == 'PRODUCTION_PAPER' and
         marker.get('real_orders_sent') == 0, 'A179_POSTRESTORE_NOT_EXACT_PENDING')
    must(scheduler.get('candidate_sha') == CANDIDATE and
         scheduler.get('status') == 'RESTORED' and
         len(scheduler.get('timers',[])) == 16, 'A179_POSTRESTORE_NOT_EXACT_SCHEDULER')
    with sqlite3.connect((ROOT/'data/paper_v17/observer_v17.db').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        book = db.execute('SELECT mode, real_orders_sent FROM observer_state WHERE id=1').fetchone()
    must(book == ('PRODUCTION_PAPER',0), 'A179_POSTRESTORE_NOT_PAPER0')
    proc = subprocess.run(['docker','inspect','porota_critical_approval_rc6'],
                          capture_output=True,check=True,text=True,timeout=30)
    crit = json.loads(proc.stdout)[0]
    must(crit['Image'] == IMAGE and crit['State']['Status']=='running',
         'A179_POSTRESTORE_CRITICAL_IMAGE_DRIFT')
    helper = (ROOT/'scripts/rc6_deploy_quiescence.py').read_bytes()
    must(hashlib.sha256(helper).hexdigest() == HELPER_DIGEST,
         'A179_POSTRESTORE_HELPER_DRIFT')
    env = dict(os.environ,RC6_A145_QUIESCENCE_GZIP_B64=base64.b64encode(
        gzip.compress(helper,mtime=0)).decode('ascii'),PYTHONDONTWRITEBYTECODE='1')
    rows = []
    for phase in ('T_MINUS_45','T_MINUS_10'):
        completed = subprocess.run([sys.executable,str(ROOT/'rc6_preopen.py'),
                                    '--phase',phase],capture_output=True,text=True,
                                   env=env,timeout=180,check=False)
        report = json.loads(completed.stdout)
        checks = report.get('checks') or {}
        reds = {name:{'state':value.get('state'),'reason':value.get('reason'),
                      'units':{u:item for u,item in (value.get('units') or {}).items()
                               if item.get('state')=='RED'}}
                for name,value in checks.items()
                if isinstance(value,dict) and value.get('state')=='RED'}
        rows.append({'phase':phase,'return_code':completed.returncode,
                     'report_status':report.get('status'),
                     'red_checks':report.get('red_checks'),
                     'red_details':reds,
                     'paper_admission_hold':report.get('paper_admission_hold'),
                     'operator_paper_paused':report.get('operator_paper_paused'),
                     'required_timer_state':checks.get('required_rc6_timers',{}).get('state'),
                     'stderr_type':completed.stderr[:300] if completed.stderr else None})
    print('RC6_A179_POSTRESTORE_READONLY='+json.dumps({
        'status':'DIAGNOSIS_ONLY_NOT_RUNTIME_GREEN','candidate':CANDIDATE,
        'marker':'DEPLOYED_VALIDATION_PENDING','scheduler':'RESTORED',
        'critical_image':'NEW_EXACT','paper':'PRODUCTION_PAPER',
        'real_orders_sent':0,'host_mutations':0,'ppi_watch_mutations':0,
        'phases':rows},sort_keys=True))


if __name__=='__main__':
    try: main()
    except Exception as exc:
        print('RC6_A179_POSTRESTORE_RED='+json.dumps({'type':type(exc).__name__,
               'reason':str(exc)[:250]},sort_keys=True));raise SystemExit(2)
