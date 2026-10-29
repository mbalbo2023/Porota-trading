#!/usr/bin/env python3
"""A175 failed-Deploy runtime diagnostic; strictly read-only and never a GO.

Run only as a streamed, bounded GitHub Action under rc6-unified-paper-deploy.
Docker image/reference and disk ownership belong to the separate inventory.
No provider, account, order, start/stop, reset-failed or host file writes.
"""
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

ROOT=Path('/opt/porota-trading')
DEPLOY=ROOT/'data/deploy'
DB=ROOT/'data/paper_v17/observer_v17.db'
HEALTH=DB.parent/'artifacts'/DB.name/'runtime-health.json'
LOCK=Path('/run/lock/porota-rc6-deploy-v2.lock')
BARRIER=Path('/run/lock/porota-rc6-deploy-v2-attempt.json')
ART=ZoneInfo('America/Argentina/Buenos_Aires')
CANDIDATE='18ba517a846c6798f0d12f39072465fc02fa03b5'
TREE='66c67bff66dd9f036cd7aa8de5ba312e04e53be6'
DEPLOY_SHA='3c5210fef9d4f1d4c95b286b0dbc61b6654fe055'
IMAGE='sha256:9841cba8df3345fa22e39457645ba250431ad1f465ce4204d92f73f7816215b9'
FROZEN_SHA256='1067182c282f17440850e50c7f8d347d84cfbb90090d7e71066483beb19450e0'
FROZEN=DEPLOY/'porota-frozen-candidate.json'
CODE=re.compile(r'[A-Z][A-Z0-9_]{1,79}\Z')
ROLES=('scanner','dynamic_shadow','intraday_scalping','performance',
       'exit_reader','notifications','candles','caucion_cash_sweep')


def safe_json(path,limit):
    info=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or
            not 0<info.st_size<=limit):
        raise ValueError('EVIDENCE_PATH_UNSAFE')
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:raise ValueError('EVIDENCE_DUPLICATE_KEY')
            value[key]=item
        return value
    result=json.loads(path.read_bytes(),object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('EVIDENCE_NONFINITE')))
    if type(result) is not dict:raise ValueError('EVIDENCE_OBJECT_RED')
    return result


def frozen_manifest_hash():
    fd=os.open(FROZEN,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or
                not 0<info.st_size<=65536):
            raise ValueError('FROZEN_MANIFEST_PATH_UNSAFE')
        raw=os.read(fd,65537)
        if len(raw)!=info.st_size:
            raise ValueError('FROZEN_MANIFEST_SIZE_DRIFT')
        digest=hashlib.sha256(raw).hexdigest()
        if digest!=FROZEN_SHA256:
            raise ValueError('FROZEN_MANIFEST_HASH_DRIFT')
        return {'sha256':digest,'bytes':len(raw)}
    finally:os.close(fd)


def age(value,now,maximum):
    try:
        at=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if at.tzinfo is None:raise ValueError('NAIVE')
        seconds=(now-at.astimezone(timezone.utc)).total_seconds()
        if not 0<=seconds<=maximum:raise ValueError('STALE')
        return round(seconds,3)
    except (ValueError,TypeError,OverflowError) as exc:
        raise ValueError('RUNTIME_TIMESTAMP_STALE') from exc


def metadata(key):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://169.254.169.254/metadata/v1/'+key,timeout=1.5) as res:
        if res.status!=200:raise ValueError('DROPLET_METADATA_HTTP_RED')
        value=res.read(64).decode('ascii').strip()
    if not re.fullmatch('[0-9]{1,20}' if key=='id' else '[a-z0-9-]{2,20}',value):
        raise ValueError('DROPLET_METADATA_VALUE_RED')
    return value


def read_lock():
    if os.geteuid()!=0:raise ValueError('ROOT_REQUIRED')
    parent=LOCK.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=0 or
            parent.st_mode & stat.S_IWOTH and not parent.st_mode & stat.S_ISVTX):
        raise ValueError('MUTEX_PARENT_UNSAFE')
    fd=os.open(LOCK,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        held,path=os.fstat(fd),LOCK.lstat()
        if (not stat.S_ISREG(held.st_mode) or held.st_uid!=0 or
                stat.S_IMODE(held.st_mode)!=0o600 or held.st_nlink!=1 or
                (held.st_dev,held.st_ino)!=(path.st_dev,path.st_ino)):
            raise ValueError('MUTEX_IDENTITY_RED')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:BARRIER.lstat()
        except FileNotFoundError:pass
        else:raise ValueError('ATTEMPT_BARRIER_PRESENT')
        return fd
    except BaseException:
        os.close(fd);raise


def command(argv,timeout=3):
    allowed=(argv[:2]==['systemctl','show'] and len(argv)==4 and
             argv[2] in {'porota-preopen-rc6.service',
                         'porota-local-eod-history-rc6.service',
                         'porota-iol-oauth-health-rc6.service'} and
             argv[3].startswith('--property='))
    if not allowed:raise ValueError('MUTATING_COMMAND_FORBIDDEN')
    result=subprocess.run(argv,capture_output=True,text=True,timeout=timeout,check=False)
    if result.returncode or len(result.stdout)>4096:
        raise ValueError('SYSTEMD_READ_FAILED')
    return dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)


def service(name):
    fields=('ActiveState','Result','ExecMainStatus')
    row=command(['systemctl','show',name,'--property='+','.join(fields)])
    if set(row)!=set(fields):raise ValueError('SYSTEMD_FIELDS_RED')
    result=row['Result'].upper().replace('-','_')
    if result not in {'SUCCESS','EXIT_CODE','SIGNAL','CORE_DUMP','TIMEOUT'}:
        result='OTHER'
    return {'state':row['ActiveState'] if row['ActiveState'] in
            {'inactive','active','failed'} else 'UNKNOWN',
            'result':result,'last_exit':int(row['ExecMainStatus']) if
            re.fullmatch(r'[0-9]{1,3}',row['ExecMainStatus']) else None}


def marker(now):
    value=safe_json(DEPLOY/'CURRENT_STATE_V2.json',65536)
    if (value.get('candidate_sha')!=CANDIDATE or
            value.get('deploy_sha')!=DEPLOY_SHA or value.get('image_id')!=IMAGE or
            value.get('validation_status')!='DEPLOYED_VALIDATION_PENDING' or
            value.get('mode')!='PRODUCTION_PAPER' or
            type(value.get('real_orders_sent')) is not int or value['real_orders_sent']!=0 or
            value.get('real_order_routes')!='NOT_CALLED' or
            value.get('ppi_watch_untouched') is not True):
        raise ValueError('PENDING_MARKER_SAFETY_OR_IDENTITY_RED')
    seconds=age(value.get('recorded_at'),now,7*86400)
    if datetime.fromisoformat(value['recorded_at'].replace('Z','+00:00'))<=datetime(
            2026,10,29,0,48,30,tzinfo=timezone.utc):
        raise ValueError('PENDING_MARKER_OLDER_THAN_DEPLOY')
    return {'validation':'DEPLOYED_VALIDATION_PENDING','image_id':IMAGE,
            'age_s':seconds,'candidate_sha':CANDIDATE,'deploy_sha':DEPLOY_SHA}


def db_snapshot(now):
    with closing(sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True,timeout=.5)) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA busy_timeout=500')
        deadline=time.monotonic()+3
        db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        try:
            db.execute('BEGIN')
            o=db.execute('SELECT mode,real_orders_sent,ppi_auth,process_state,'
                'session_state,heartbeat_at FROM observer_state WHERE id=1').fetchone()
            if (o is None or o['mode']!='PRODUCTION_PAPER' or
                    type(o['real_orders_sent']) is not int or o['real_orders_sent']!=0):
                raise ValueError('OBSERVER_PAPER_ZERO_RED')
            books={}
            for table,status in (('paper_positions','OPEN'),('paper_cauciones','OPEN'),
                                 ('paper_future_positions','ACTIVE')):
                books[table]=db.execute('SELECT 1 FROM '+table+
                                        ' WHERE status=? LIMIT 1',(status,)).fetchone() is None
            if not all(books.values()):raise ValueError('PAPER_BOOK_NOT_FLAT')
            workers={}
            for name,table in (('scalping','intraday_scalping_worker_state'),
                    ('caucion','paper_caucion_cash_sweep_state'),
                    ('supervisor','paper_supervisor_state'),
                    ('exit_reader','paper_exit_reader_state')):
                row=db.execute('SELECT * FROM '+table+' WHERE id=1').fetchone()
                if row is None:raise ValueError('WORKER_ROW_MISSING')
                data=dict(row)
                if 'real_orders_sent' in data and (type(data['real_orders_sent']) is not int or
                        data['real_orders_sent']!=0):raise ValueError('WORKER_REAL_ORDER_RED')
                if name=='caucion' and json.loads(data.get('routes_json') or 'null')!=[]:
                    raise ValueError('CAUCION_REAL_ROUTE_RED')
                state=str(data.get('state') or '').upper()
                if not CODE.fullmatch(state):state='UNKNOWN'
                workers[name]={'state':state,'heartbeat_age_s':age(data.get('heartbeat_at'),now,300)}
            return {'mode':'PRODUCTION_PAPER','real_orders_sent':0,
                    'process_state':str(o['process_state']).upper(),
                    'session_state':str(o['session_state']).upper(),
                    'ppi_auth':str(o['ppi_auth']).upper(),
                    'observer_heartbeat_age_s':age(o['heartbeat_at'],now,300),
                    'three_books_flat':True,'workers':workers}
        finally:db.set_progress_handler(None,0)


def runtime_children(now):
    value=safe_json(HEALTH,131072)
    if (value.get('schema')!='rc6.runtime-child-health.v1' or
            value.get('source_sha')!=CANDIDATE or
            value.get('candidate_tree_sha')!=TREE or
            type(value.get('children')) is not dict):
        raise ValueError('CHILD_HEALTH_SOURCE_RED')
    result={}
    for name in ROLES:
        row=value['children'].get(name,{})
        if (type(row) is not dict or type(row.get('restarts')) is not int or
                row['restarts']<0):raise ValueError('CHILD_HEALTH_ROW_RED')
        result[name]={'running':row.get('state')=='RUNNING',
                      'pid_present':type(row.get('pid')) is int and row['pid']>0,
                      'restarts':row['restarts'],
                      'last_error_present':row.get('last_error') is not None}
    return {'recorded_age_s':age(value.get('recorded_at'),now,180),'children':result}


def guardian(now):
    value=safe_json(ROOT/'data/market/iol_oauth_guardian_state.json',8192)
    if value.get('schema')!='POROTA_RC6_IOL_OAUTH_GUARDIAN_STATE_V1':
        raise ValueError('IOL_GUARDIAN_SCHEMA_RED')
    state=str(value.get('state') or '').upper()
    if not CODE.fullmatch(state):state='UNKNOWN'
    checked=age(value.get('checked_at'),now,7*86400)
    return {'state':state,'checked_age_s':checked,
            'oauth_recent':state in {'AUTHENTICATED','ACTIVE_COLLECTOR_HEALTHY'} and checked<=1900,
            'service':service('porota-iol-oauth-health-rc6.service')}


def iol_cache(now):
    value=safe_json(ROOT/'data/market/iol_family_reference_latest.json',4*1024**2)
    if (value.get('cache_state') not in {
            'LIVE_FRESH','CACHE_FRESH','CACHE_STALE','SOURCE_UNAVAILABLE'} or
            value.get('continuation_state')!='CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL' or
            value.get('decision_effect')!='OBSERVE_ONLY' or
            value.get('real_money_authorized') is not False or
            value.get('fallback_order')!=['IOL_LIVE_BOUNDED_RETRY',
                'IOL_LAST_KNOWN_GOOD','PPI_PRIMARY','BYMA_PUBLIC_COMPLEMENTARY']):
        raise ValueError('IOL_CACHE_FAILSAFE_CONTRACT_RED')
    try:
        observed=age(value.get('last_known_good_at'),now,7*86400)
        refreshed=age(value.get('refreshed_at'),now,7*86400)
    except ValueError:
        observed=refreshed=None
    live=(value['cache_state']=='LIVE_FRESH' and observed is not None and
          refreshed is not None and observed<=240 and refreshed<=240)
    return {'stored_label':value['cache_state'],
            'last_known_good_age_s':observed,'refreshed_age_s':refreshed,
            'effective_state':'LIVE_FRESH' if live else 'AMBER_STALE_OR_UNAVAILABLE',
            'open_freshness_attested':False}


def dashboard():
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8000/health',timeout=3) as response:
        if response.status!=200 or len(response.read(4096))>=4096:
            raise ValueError('DASHBOARD_HEALTH_HTTP_RED')
    source=("import json,os,urllib.request; token=os.environ.get('DASHBOARD_ACCESS_TOKEN',''); "
            "assert token; req=urllib.request.Request('http://127.0.0.1:8000/api/dashboard/truth',"
            "headers={'Authorization':'Bearer '+token}); r=urllib.request.urlopen(req,timeout=8); "
            "body=r.read(1048577); assert r.status==200 and len(body)<=1048576; "
            "p=json.loads(body); print(json.dumps({'mode':p.get('mode'),'execution':p.get('execution'),"
            "'process_state':p.get('process_state'),'session_state':p.get('session_state'),"
            "'real_orders_sent':p.get('real_orders_sent'),'ready':p.get('readiness',{}).get('ready')},"
            "sort_keys=True,separators=(',',':')))" )
    result=subprocess.run(['docker','exec','-e','PYTHONDONTWRITEBYTECODE=1',
        'porota_production_dashboard','python','-B','-c',source],capture_output=True,
        text=True,timeout=12,check=False)
    if result.returncode or len(result.stdout)>1024:
        raise ValueError('DASHBOARD_TRUTH_HTTP_RED')
    row=json.loads(result.stdout)
    if (type(row) is not dict or row.get('mode')!='PRODUCTION_PAPER' or
            row.get('execution')!='SIMULATED' or
            type(row.get('real_orders_sent')) is not int or row['real_orders_sent']!=0):
        raise ValueError('DASHBOARD_TRUTH_SAFETY_RED')
    return {'http':200,**row}


def pulse():
    now=datetime.now(timezone.utc)
    receipt={'schema':'rc6.a175.pending-runtime-ro.v1','at_utc':now.isoformat(),
             'at_art':now.astimezone(ART).isoformat(),
             'candidate_sha':CANDIDATE,'candidate_tree':TREE,'deploy_sha':DEPLOY_SHA,
             'host_mutations':0,'provider_calls':0,'real_routes':'NOT_CALLED',
             'operational_go':False,'status':'RED'}
    fd=None
    try:
        receipt['droplet']={k:metadata(k) for k in ('id','region')}
        if receipt['droplet']!={'id':'594077619','region':'nyc1'}:
            raise ValueError('DROPLET_IDENTITY_RED')
        fd=read_lock()
        receipt['marker']=marker(now)
        receipt['frozen_manifest']=frozen_manifest_hash()
        receipt['db']=db_snapshot(now)
        receipt['children']=runtime_children(now)
        receipt['guardian']=guardian(now)
        receipt['iol_cache']=iol_cache(now)
        receipt['dashboard']=dashboard()
        receipt['services']={name:service(name) for name in (
            'porota-preopen-rc6.service','porota-local-eod-history-rc6.service')}
        db=receipt['db'];dash=receipt['dashboard'];children=receipt['children']['children']
        receipt['runtime_consistent']=(dash['process_state']==db['process_state'] and
            dash['session_state']==db['session_state'] and
            all(row['running'] and row['pid_present'] and not row['last_error_present']
                for row in children.values()))
        receipt['status']='DIAGNOSTIC_PENDING_NO_GO'
    except Exception as exc:
        value=str(exc).split(':',1)[0]
        receipt['error_code']=value if CODE.fullmatch(value) else type(exc).__name__.upper()
    finally:
        if fd is not None:os.close(fd)
    print('RC6_A175_PENDING_RUNTIME_RO='+json.dumps(receipt,sort_keys=True,
          separators=(',',':'),allow_nan=False))
    return 0 if receipt['status']=='DIAGNOSTIC_PENDING_NO_GO' else 2


if __name__=='__main__':
    signal.alarm(60)
    raise SystemExit(pulse())
