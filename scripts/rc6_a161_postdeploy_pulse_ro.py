#!/usr/bin/env python3
"""One strictly read-only RC6 postdeploy pulse under the existing host mutex.

The runner takes two independent pulses >=190 seconds apart. This script
never starts a timer, calls an IOL provider, writes the host, or emits
positions, symbols, account details, raw URLs or secrets.
"""
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
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
ARTIFACTS=DB.parent/'artifacts'/DB.name
MARKER=DEPLOY/'CURRENT_STATE_V2.json'
LOCK=Path('/run/lock/porota-rc6-deploy-v2.lock')
BARRIER=Path('/run/lock/porota-rc6-deploy-v2-attempt.json')
ART=ZoneInfo('America/Argentina/Buenos_Aires')
SHA=re.compile(r'[0-9a-f]{40}\Z')
HASH=re.compile(r'[0-9a-f]{64}\Z')
CODE=re.compile(r'[A-Z][A-Z0-9_]{1,39}\Z')
TIMER=re.compile(r'porota-[a-z0-9-]+\.timer\Z')
EXPECTED={'id':'594077619','region':'nyc1'}
ROLES=('scanner','dynamic_shadow','intraday_scalping','performance',
       'exit_reader','notifications','candles','caucion_cash_sweep')


def fail_code(exc):
    value=str(exc).split(':',1)[0]
    return value if CODE.fullmatch(value) else type(exc).__name__.upper()


def metadata(name):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://169.254.169.254/metadata/v1/'+name,timeout=1.5) as response:
        if response.status!=200:
            raise ValueError('METADATA_HTTP_RED')
        value=response.read(64).decode('ascii').strip()
    if not re.fullmatch('[0-9]{1,20}' if name=='id' else '[a-z0-9-]{2,20}',value):
        raise ValueError('METADATA_VALUE_RED')
    return value


def lock_existing():
    if os.geteuid()!=0:
        raise ValueError('ROOT_REQUIRED')
    parent=LOCK.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=0 or
            parent.st_mode & stat.S_IWOTH and not parent.st_mode & stat.S_ISVTX):
        raise ValueError('MUTEX_PARENT_UNSAFE')
    fd=os.open(LOCK,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        observed,path=os.fstat(fd),LOCK.lstat()
        if (not stat.S_ISREG(observed.st_mode) or observed.st_uid!=0 or
                stat.S_IMODE(observed.st_mode)!=0o600 or observed.st_nlink!=1 or
                (observed.st_dev,observed.st_ino)!=(path.st_dev,path.st_ino)):
            raise ValueError('MUTEX_IDENTITY_RED')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:BARRIER.lstat()
        except FileNotFoundError:pass
        else:raise ValueError('BARRIER_PRIOR_ATTEMPT_REVIEW_REQUIRED')
        return fd
    except BaseException:
        os.close(fd)
        raise


def strict_json(raw):
    def pairs(values):
        result={}
        for key,value in values:
            if key in result:raise ValueError('JSON_DUPLICATE_KEY_RED')
            result[key]=value
        return result
    value=json.loads(raw,object_pairs_hook=pairs,
                     parse_constant=lambda _ : (_ for _ in ()).throw(
                         ValueError('JSON_NONFINITE_RED')))
    if type(value) is not dict:raise ValueError('JSON_OBJECT_REQUIRED')
    return value


def exact_json(path,limit,*,with_digest=False):
    info=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or
            not 0<info.st_size<=limit):
        raise ValueError('JSON_PATH_UNSAFE')
    raw=path.read_bytes()
    value=strict_json(raw)
    return (value,hashlib.sha256(raw).hexdigest()) if with_digest else value


def envelope(path,limit):
    value=exact_json(path,limit)
    if set(value)!={'payload','digest'} or type(value['payload']) is not dict or not (
            isinstance(value['digest'],str) and HASH.fullmatch(value['digest'])):
        raise ValueError('SHADOW_ENVELOPE_RED')
    encoder=json.JSONEncoder(sort_keys=True,separators=(',',':'),default=str,
                             allow_nan=False)
    digest=hashlib.sha256()
    for part in encoder.iterencode(value['payload']):
        digest.update(part.encode('ascii'))
    if digest.hexdigest()!=value['digest']:
        raise ValueError('SHADOW_DIGEST_RED')
    return value['payload']


def canonical_digest(payload):
    encoder=json.JSONEncoder(sort_keys=True,separators=(',',':'),default=str,
                             allow_nan=False)
    digest=hashlib.sha256()
    for part in encoder.iterencode(payload):digest.update(part.encode('ascii'))
    return digest.hexdigest()


def compressed_envelope(path,limit=4*1024**2):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>limit:
        raise ValueError('SHADOW_CUT_PATH_RED')
    with gzip.open(path,'rb') as stream:
        raw=stream.read(limit+1)
    if len(raw)>limit:raise ValueError('SHADOW_CUT_BOUND_RED')
    value=strict_json(raw)
    if (type(value) is not dict or set(value)!={'payload','digest'} or
            type(value['payload']) is not dict or
            value['digest']!=canonical_digest(value['payload'])):
        raise ValueError('SHADOW_CUT_DIGEST_RED')
    return value['payload']


def timestamp(value,now,maximum):
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        age=(now-parsed.astimezone(timezone.utc)).total_seconds()
        if parsed.tzinfo is not None and 0<=age<=maximum:
            return parsed.astimezone(timezone.utc).isoformat(),round(age,3)
    except (ValueError,TypeError,AttributeError,OverflowError):
        pass
    raise ValueError('TIMESTAMP_STALE_OR_INVALID')


def command(args,seconds=1.0):
    result=subprocess.run(args,capture_output=True,text=True,timeout=seconds,
                          check=False)
    if result.returncode or len(result.stdout)>65536:
        raise ValueError('READ_ONLY_COMMAND_RED')
    return result.stdout.strip()


def systemd(name,properties):
    raw=command(['systemctl','show',name,'--property='+','.join(properties)],.9)
    value=dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
    if set(value)!=set(properties):
        raise ValueError('SYSTEMD_FIELDS_RED')
    return value


def timers(policy,allow_historical_eod=False,now=None):
    units=policy.get('units')
    if type(units) is not dict:raise ValueError('TIMER_POLICY_RED')
    names=sorted(Path(path).name for path,row in units.items() if type(row) is dict
                 and row.get('role')=='ACTIVE_TIMER' and row.get('active') is True)
    if len(names)!=16 or len(set(names))!=16 or any(not TIMER.fullmatch(n) for n in names):
        raise ValueError('TIMER_POLICY_RED')
    expected_iol='porota-iol-oauth-health-rc6.timer'
    expected_hold='porota-preopen-rc6.timer'
    if not {expected_iol,expected_hold}<=set(names):
        raise ValueError('TIMER_POLICY_RED')
    states={}
    eod_historical=False
    eod_invocation=None
    eod_next=None
    for name in names:
        row=systemd(name,('ActiveState','UnitFileState','Unit'))
        service=name.removesuffix('.timer')+'.service'
        if row['Unit']!=service:
            raise ValueError('TIMER_SERVICE_IDENTITY_RED')
        service_row=systemd(service,('ActiveState','Result','ExecMainStatus'))
        expected=('inactive','disabled') if name==expected_hold else ('active','enabled')
        observed=(row['ActiveState'],row['UnitFileState'])
        result=service_row['Result']
        # reset-failed closes an adjudicated historical failure but may leave
        # ExecMainStatus for introspection. Current ActiveState/Result decide
        # service health; retain the last exit number only as provenance.
        failed=(service_row['ActiveState'] not in {'active','inactive'} or
                result!='success')
        if allow_historical_eod and name=='porota-local-eod-history-rc6.timer' and failed:
            detail=systemd(service,('ActiveState','Result','ExecMainStatus',
                                    'ExecMainStartTimestamp','ExecMainExitTimestamp',
                                    'InvocationID'))
            timer_schedule=systemd(name,('NextElapseUSecRealtime',))
            try:
                scheduled=datetime.strptime(timer_schedule['NextElapseUSecRealtime'],
                          '%a %Y-%m-%d %H:%M:%S UTC').replace(tzinfo=timezone.utc)
            except ValueError:
                scheduled=None
            local=scheduled.astimezone(ART) if scheduled else None
            window=(now is not None and local is not None and
                now.astimezone(ART).date().isoformat()=='2026-10-28' and
                local.date()==now.astimezone(ART).date() and
                17*60+15<=local.hour*60+local.minute<=22*60+5 and
                scheduled>now and
                10*60+30<=now.astimezone(ART).hour*60+now.astimezone(ART).minute<17*60)
            eod_historical=(detail['ActiveState']=='failed' and
                detail['Result']=='exit-code' and detail['ExecMainStatus']=='2' and
                detail['ExecMainStartTimestamp']=='Tue 2026-10-27 22:10:13 UTC' and
                detail['ExecMainExitTimestamp']=='Tue 2026-10-27 22:10:13 UTC' and
                isinstance(detail['InvocationID'],str) and
                re.fullmatch('[0-9a-f]{32}',detail['InvocationID']) is not None and
                window and observed==('active','enabled'))
            if eod_historical:
                eod_invocation=detail['InvocationID']
                eod_next=scheduled.isoformat()
        states[name]={'state':row['ActiveState'] if row['ActiveState'] in
                      {'active','inactive'} else 'UNKNOWN',
                      'enabled':row['UnitFileState'] if row['UnitFileState'] in
                      {'enabled','disabled'} else 'UNKNOWN',
                      'service_failure':bool(failed),
                      'service_result':result.upper().replace('-','_') if
                      CODE.fullmatch(result.upper().replace('-','_')) else 'UNKNOWN',
                      'last_exec_status_historical':int(service_row['ExecMainStatus']) if
                      re.fullmatch(r'[0-9]{1,3}',service_row['ExecMainStatus']) else None,
                      'migration_expected':observed==expected}
    failures=sorted(name for name,row in states.items() if
                    not row['migration_expected'] or row['service_failure'])
    admissible=(eod_historical and failures==['porota-local-eod-history-rc6.timer'])
    return {'count':16,'active_enabled':15,'operator_preopen_disabled':True,
            'iol_guard_active':states[expected_iol]['migration_expected'],
            'all_healthy':not failures,'failed_units':failures,
            'historical_eod_a162_only':admissible,
            'historical_cause_run_id':40722096423 if admissible else None,
            'historical_cause_artifact_id':12830702310 if admissible else None,
            'historical_cause_artifact_sha256':(
                '7f0d8950527fbaef8fd6a0e2ce619c16a3efee385fa98a4f4e4808d329379f83'
                if admissible else None),
            'historical_eod_invocation_id':eod_invocation,
            'eod_next_elapse_utc':eod_next,
            'states':states}


def ppi_digest():
    raw=command(['systemctl','list-unit-files','--no-legend'],2)
    names=sorted(line.split()[0] for line in raw.splitlines() if line.split() and
                 {'ppi','watch'}<=set(re.split(r'[-_.@]',line.split()[0].lower())))
    rows=[]
    for name in names:
        if len(name)>256 or not re.fullmatch(r'[A-Za-z0-9_.@-]+',name):
            raise ValueError('PPI_UNIT_NAME_RED')
        row=systemd(name,('ActiveState','UnitFileState'))
        rows.append([name,row['ActiveState'],row['UnitFileState']])
    return {'state':'STATE_READ_ONLY' if rows else 'NOT_PRESENT',
            'unit_count':len(rows),
            'snapshot_sha256':hashlib.sha256(
                json.dumps(rows,sort_keys=True).encode()).hexdigest()}


def containers(image):
    result={}
    for name in ('porota_production_observer','porota_production_dashboard',
                 'porota_critical_approval_rc6'):
        raw=command(['docker','inspect','--format',
                     '{{.Image}}|{{.Id}}|{{.State.Status}}|{{.State.OOMKilled}}|'
                     '{{.RestartCount}}|{{if .State.Health}}{{.State.Health.Status}}'
                     '{{else}}none{{end}}',name],4)
        values=raw.split('|')
        if (len(values)!=6 or values[0]!=image or not HASH.fullmatch(values[1]) or
                values[2]!='running' or values[3]!='false' or
                not re.fullmatch(r'[0-9]{1,8}',values[4]) or
                (name=='porota_critical_approval_rc6' and values[5]!='healthy')):
            raise ValueError('CONTAINER_IDENTITY_OR_HEALTH_RED')
        result[name]={'id_sha256':hashlib.sha256(values[1].encode()).hexdigest(),
                      'restarts':int(values[4]),'running':True,
                      'image_matches_marker':True,'oom_killed':False,
                      'health':values[5] if name=='porota_critical_approval_rc6' else None}
    return result


def critical():
    script=("from fm_critical_approval_unix_runtime_rc6 import "
            "UnixGithubIssuesClient; h=UnixGithubIssuesClient().health(); "
            "assert h.get('status')=='ok' and h.get('capability')=='issues_only'; "
            "print('ISSUES_ONLY_GREEN')")
    output=command(['docker','exec','-e','PYTHONDONTWRITEBYTECODE=1',
                    'porota_critical_approval_rc6','python','-B','-c',script],7)
    if output!='ISSUES_ONLY_GREEN':raise ValueError('CRITICAL_CAPABILITY_RED')
    return 'ISSUES_ONLY_HEALTHY'


def dashboard():
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8000/health',timeout=3) as response:
        if response.status!=200 or len(response.read(4096))>=4096:
            raise ValueError('DASHBOARD_HTTP_RED')
    # The token stays inside the dashboard process. Only an allowlisted,
    # aggregate projection crosses the docker-exec boundary.
    script=("import json,os,urllib.request; "
            "token=os.environ.get('DASHBOARD_ACCESS_TOKEN',''); "
            "assert token, 'DASHBOARD_AUTH_MISSING'; "
            "request=urllib.request.Request('http://127.0.0.1:8000/api/dashboard/truth',"
            "headers={'Authorization':'Bearer '+token}); "
            "response=urllib.request.urlopen(request,timeout=60); "
            "raw=response.read(1048577); "
            "assert response.status==200 and len(raw)<=1048576, 'DASHBOARD_TRUTH_BOUND'; "
            "truth=json.loads(raw); "
            "readiness=truth['readiness']; "
            "families=readiness['families']; "
            "assert isinstance(families,list) and len(families)<=32, 'DASHBOARD_FAMILIES_BOUND'; "
            "summary={'mode':truth.get('mode'),'execution':truth.get('execution'),"
            "'process_state':truth.get('process_state'),"
            "'session_state':truth.get('session_state'),"
            "'real_orders_sent':truth.get('real_orders_sent'),"
            "'market_open':truth.get('market_open'),"
            "'scalping_mode':truth.get('scalping',{}).get('mode'),"
            "'history_governs_readiness':truth.get('history',{}).get('governs_readiness'),"
            "'readiness_source':readiness.get('source'),"
            "'ready':readiness.get('ready'),"
            "'families':{r.get('family'):r.get('runtime_ready') for r in families}}; "
            "print(json.dumps(summary,sort_keys=True,separators=(',',':')))" )
    raw=command(['docker','exec','-e','PYTHONDONTWRITEBYTECODE=1',
                 'porota_production_dashboard','python','-B','-c',script],65)
    try: truth=strict_json(raw.encode('utf-8'))
    except (ValueError,UnicodeError) as exc:
        raise ValueError('DASHBOARD_TRUTH_AGGREGATE_RED') from exc
    if (type(truth) is not dict or truth.get('mode')!='PRODUCTION_PAPER' or
            truth.get('execution')!='SIMULATED' or
            type(truth.get('real_orders_sent')) is not int or
            truth['real_orders_sent']!=0 or
            truth.get('history_governs_readiness') is not False or
            truth.get('readiness_source')!='candidate_identity_v2' or
            truth.get('scalping_mode')!='ACTIVE_PAPER' or
            type(truth.get('ready')) is not int or truth['ready']<0 or
            type(truth.get('families')) is not dict or
            len(truth['families'])>32 or
            any(type(value) is not int or value<0 for value in
                truth['families'].values())):
        raise ValueError('DASHBOARD_TRUTH_SAFETY_RED')
    return truth


def paper_interlock(now,candidate):
    # Exercise the installed admission reader with the historical immutable
    # receipt. This does not publish a new preopen gate or open any route.
    sys.path.insert(0,str(ROOT))
    from rc6_paper_admission_gate import (
        HOST_RECEIPT, admission_status, operator_pause_reason)
    local=now.astimezone(ART)
    expected=operator_pause_reason(local)
    allowed,reason=admission_status(
        now=local,candidate_sha=candidate,receipt_path=HOST_RECEIPT)
    if (allowed is not False or not expected or reason!=expected or
            not CODE.fullmatch(reason)):
        raise ValueError('PAPER_NO_GO_INTERLOCK_RED')
    return {'allow_new_openings':False,'reason':reason}


def sqlite_snapshot(now,integrity,phase='OPEN'):
    info=DB.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
        raise ValueError('DB_PATH_UNSAFE')
    with closing(sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True,timeout=.5)) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA busy_timeout=500')
        if integrity=='check':
            deadline=time.monotonic()+20
            db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            try: rows=db.execute('PRAGMA quick_check').fetchall()
            except sqlite3.Error as exc:
                raise ValueError('DB_QUICK_CHECK_BOUNDED_RED') from exc
            finally: db.set_progress_handler(None,0)
            if len(rows)!=1 or rows[0][0]!='ok':
                raise ValueError('DB_QUICK_CHECK_RED')
        deadline=time.monotonic()+2
        db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        try:
            o=db.execute('SELECT mode,real_orders_sent,ppi_auth,heartbeat_at,process_state,'
                         'session_state FROM observer_state WHERE id=1').fetchone()
            if (o is None or o['mode']!='PRODUCTION_PAPER' or
                    type(o['real_orders_sent']) is not int or o['real_orders_sent']!=0):
                raise ValueError('OBSERVER_PAPER_ZERO_RED')
            expected=('RUNNING','MARKET_OPEN') if phase=='OPEN' else (
                'WAITING_MARKET','MARKET_CLOSED')
            if ((o['process_state'],o['session_state'])!=expected or
                    o['ppi_auth']!='OK'):
                raise ValueError('OBSERVER_PHASE_RED')
            open_counts={}
            for table,state in (('paper_positions','OPEN'),
                                ('paper_cauciones','OPEN'),
                                ('paper_future_positions','ACTIVE')):
                row=db.execute('SELECT 1 FROM '+table+' WHERE status=? LIMIT 1',(state,)).fetchone()
                open_counts[table]=bool(row)
            if any(open_counts.values()):raise ValueError('PAPER_THREE_BOOKS_NOT_FLAT')
            scalping=db.execute('SELECT state,heartbeat_at,real_orders_sent,failed '
                                'FROM intraday_scalping_worker_state WHERE id=1').fetchone()
            caucion=db.execute('SELECT state,heartbeat_at,real_orders_sent,routes_json '
                               'FROM paper_caucion_cash_sweep_state WHERE id=1').fetchone()
            supervisor=db.execute('SELECT state,heartbeat_at FROM paper_supervisor_state '
                                  'WHERE id=1').fetchone()
            exit_reader=db.execute('SELECT state,heartbeat_at FROM paper_exit_reader_state '
                                   'WHERE id=1').fetchone()
            if any(row is None for row in (scalping,caucion,supervisor,exit_reader)):
                raise ValueError('WORKER_ROW_MISSING')
            scalping_states={'RUNNING'} if phase=='OPEN' else {'WAITING_MARKET'}
            caucion_states={'WAITING_WINDOW','WAITING_CALENDAR','HOLD',
                            'PLACED_SIMULATED'}
            if (scalping['state'] not in scalping_states or
                    caucion['state'] not in caucion_states or
                    supervisor['state']!='RUNNING' or
                    exit_reader['state'] not in (
                        {'READY'} if phase=='OPEN' else {'WAITING_MARKET'}) or
                    any(type(row['real_orders_sent']) is not int or row['real_orders_sent']!=0
                        for row in (scalping,caucion)) or
                    json.loads(caucion['routes_json'] or '[]')!=[]):
                raise ValueError('WORKER_PAPER_SAFETY_RED')
            paper_at,paper_age=timestamp(o['heartbeat_at'],now,300)
            scalp_at,scalp_age=timestamp(scalping['heartbeat_at'],now,300)
            caucion_at,caucion_age=timestamp(caucion['heartbeat_at'],now,300)
            supervisor_at,supervisor_age=timestamp(supervisor['heartbeat_at'],now,20)
            exit_reader_at,exit_reader_age=timestamp(exit_reader['heartbeat_at'],now,20)
            aliases={'ON':'OBLIGACIONES','OBLIGACIONES_NEGOCIABLES':'OBLIGACIONES',
                     'ETF':'ETFS','CAUCION':'CAUCIONES','FONDOS_COMUNES':'FCI',
                     'ACCION':'ACCIONES','CEDEAR':'CEDEARS'}
            by_family={}
            for family,ready in db.execute(
                'SELECT upper(instrument_type),SUM(CASE WHEN can_simulate=1 '
                "AND upper(status)='AVAILABLE' THEN 1 ELSE 0 END) "
                'FROM candidate_identity_v2 GROUP BY upper(instrument_type) '
                'ORDER BY upper(instrument_type)'):
                normalized=aliases.get(family,family)
                by_family[normalized]=by_family.get(normalized,0)+int(ready or 0)
            if len(by_family)>32 or any(value<0 for value in by_family.values()):
                raise ValueError('READINESS_AGGREGATE_RED')
            if type(scalping['failed']) is not int or scalping['failed']!=0:
                raise ValueError('SCALPING_FAILED_COUNTER_RED')
        finally:
            db.set_progress_handler(None,0)
    return {'mode':'PRODUCTION_PAPER','real_orders_sent':0,'flat_books':3,
            'observer_state':o['process_state'],'session_state':o['session_state'],
            'ppi_auth':'OK',
            'observer_heartbeat_at':paper_at,'observer_heartbeat_age_s':paper_age,
            'scalping':{'state':scalping['state'],'heartbeat_at':scalp_at,
                        'heartbeat_age_s':scalp_age,'failed_cumulative':0,
                        'real_orders_sent':0},
            'caucion':{'state':caucion['state'],'heartbeat_at':caucion_at,
                      'heartbeat_age_s':caucion_age,'real_orders_sent':0,'routes':[]},
            'exit_supervisor':{'state':'RUNNING','heartbeat_at':supervisor_at,
                               'heartbeat_age_s':supervisor_age},
            'exit_reader':{'state':exit_reader['state'],'heartbeat_at':exit_reader_at,
                           'heartbeat_age_s':exit_reader_age},
            'readiness_by_family':by_family,
            'integrity':'PRAGMA_QUICK_CHECK_OK' if integrity=='check' else 'IN_FIRST_PULSE'}


def runtime_health(now,candidate,tree):
    health=exact_json(ARTIFACTS/'runtime-health.json',131072)
    at,age=timestamp(health.get('recorded_at'),now,120)
    if (health.get('schema')!='rc6.runtime-child-health.v1' or
            health.get('source_sha')!=candidate or
            health.get('candidate_tree_sha')!=tree):
        raise ValueError('RUNTIME_HEALTH_SOURCE_RED')
    children=health.get('children')
    if type(children) is not dict:raise ValueError('CHILDREN_HEALTH_RED')
    child={}
    for name in ROLES:
        row=children.get(name,{})
        if (type(row) is not dict or row.get('state')!='RUNNING' or
                type(row.get('pid')) is not int or row['pid']<=0 or
                type(row.get('restarts')) is not int or row['restarts']<0 or
                row.get('last_error') is not None):
            raise ValueError('CHILD_HEALTH_RED')
        child[name]={'running':True,'pid_present':True,'restarts':row['restarts']}
    return {'recorded_at':at,'age_s':age,'source_sha':candidate,
            'candidate_tree_sha':tree,'children':child}


def shadow(now,phase='OPEN'):
    root=ARTIFACTS/'dynamic-shadow'
    for attempt in range(3):
        pointer=envelope(root/'CURRENT.json',4096)
        cut_name=pointer.get('file')
        if not isinstance(cut_name,str) or not re.fullmatch(
                r'cut-[0-9a-f]{64}\.json\.gz',cut_name):
            raise ValueError('SHADOW_CUT_NAME_RED')
        cut=compressed_envelope(root/cut_name)
        live=envelope(root/'status.json',65536)
        again=envelope(root/'CURRENT.json',4096)
        manifest=cut.get('manifest') if type(cut) is dict else None
        status=cut.get('status') if type(cut) is dict else None
        report=cut.get('report') if type(cut) is dict else None
        original_report=({k:v for k,v in report.items() if k not in
                         ('generation_id','source_watermark','configuration_fingerprint')}
                         if type(report) is dict else {})
        original_status=({k:v for k,v in status.items() if k not in
                         ('generation_id','source_watermark')}
                         if type(status) is dict else {})
        if (type(manifest) is dict and type(status) is dict and
                type(report) is dict and pointer==again and
                cut_name=='cut-'+str(pointer.get('generation_id'))+'.json.gz' and
                manifest.get('generation_id')==pointer.get('generation_id') and
                manifest.get('sequence')==pointer.get('sequence') and
                manifest.get('schema')=='rc6.dashboard-generation.v1' and
                status.get('report_digest')==canonical_digest(original_report) and
                pointer.get('generation_id')==canonical_digest(
                    {'report':original_report,'status':original_status}) and
                all(cut[role].get('source_watermark')==
                    manifest.get('source_watermark') and
                    cut[role].get('configuration_fingerprint')==
                    manifest.get('configuration_fingerprint')
                    for role in ('status','report')) and
                canonical_digest(manifest)==pointer.get('manifest_sha256') and
                all(type(manifest.get('files',{}).get(role)) is dict and
                    canonical_digest(cut[role])==manifest['files'][role].get('payload_digest') and
                    cut[role].get('generation_id')==pointer['generation_id'] and
                    cut[role].get('as_of')==manifest.get('source_watermark',{}).get('as_of')
                    for role in ('status','report')) and
                all(live.get(key)==status.get(key) for key in
                    ('as_of','status','report_digest'))):
            break
        if attempt==2:raise ValueError('SHADOW_COMMITTED_GENERATION_RED')
        time.sleep(.05)  # Only a short observed publication race.
    if (not isinstance(pointer.get('generation_id'),str) or
            not HASH.fullmatch(pointer['generation_id']) or
            type(pointer.get('sequence')) is not int or pointer['sequence']<=0 or
            status.get('status')!='SHADOW_OBSERVING' or
            status.get('mode')!='SHADOW' or status.get('phase')!=phase or
            type(status.get('provider_requests')) is not int or
            status['provider_requests']!=0 or
            type(status.get('real_orders_sent')) is not int or
            status['real_orders_sent']!=0 or
            status.get('real_routes')!='NOT_CALLED' or
            type(report.get('real_orders_sent')) is not int or
            report['real_orders_sent']!=0 or report.get('real_routes')!='NOT_CALLED'):
        raise ValueError('SHADOW_PHASE_SAFETY_RED')
    if phase=='CLOSED':
        # Active postclose inputs can keep publishing fresh, authenticated
        # CLOSED cuts. Only stable inputs eventually enter idle after +1h.
        try:
            fresh_as_of,fresh_age=timestamp(status.get('as_of'),now,180)
        except ValueError:
            fresh_as_of=fresh_age=None
        if fresh_as_of is not None:
            return {'generation_id':pointer['generation_id'],
                    'sequence':pointer['sequence'],'as_of':fresh_as_of,
                    'age_s':fresh_age,'phase':'CLOSED',
                    'status':'SHADOW_CLOSED_ADVANCING','digest_verified':True,
                    'real_orders_sent':0,'provider_requests':0}
        idle=envelope(root/'idle.json',8192)
        if (idle.get('schema')!='rc6.shadow-closed-idle.v1' or
                idle.get('state')!='SHADOW_IDLE_MARKET_CLOSED' or
                idle.get('source_freshness_renewed') is not False or
                idle.get('last_generation_id')!=pointer['generation_id'] or
                idle.get('last_source_as_of')!=status.get('as_of') or
                type(idle.get('real_orders_sent')) is not int or
                idle['real_orders_sent']!=0 or idle.get('real_routes')!='NOT_CALLED'):
            raise ValueError('SHADOW_IDLE_PROOF_RED')
        beat,age=timestamp(idle.get('heartbeat_at'),now,90)
        if datetime.fromisoformat(beat)<datetime.fromisoformat(
                status['as_of'].replace('Z','+00:00')):
            raise ValueError('SHADOW_IDLE_BEFORE_CUT_RED')
        return {'generation_id':pointer['generation_id'],'sequence':pointer['sequence'],
                'source_as_of':status['as_of'],'idle_heartbeat_at':beat,
                'idle_heartbeat_age_s':age,'phase':'CLOSED',
                'status':'SHADOW_IDLE_MARKET_CLOSED','digest_verified':True,
                'source_freshness_renewed':False,'real_orders_sent':0,'provider_requests':0}
    as_of,age=timestamp(status.get('as_of'),now,180)
    return {'generation_id':pointer['generation_id'],'sequence':pointer['sequence'],
            'as_of':as_of,'age_s':age,'phase':'OPEN','status':'SHADOW_OBSERVING',
            'digest_verified':True,'real_orders_sent':0,'provider_requests':0}


def iol(now,phase='OPEN'):
    guardian=exact_json(ROOT/'data/market/iol_oauth_guardian_state.json',8192)
    cache=exact_json(ROOT/'data/market/iol_family_reference_latest.json',4*1024**2)
    if (guardian.get('schema')!='POROTA_RC6_IOL_OAUTH_GUARDIAN_STATE_V1' or
            guardian.get('state') not in {'AUTHENTICATED','ACTIVE_COLLECTOR_HEALTHY'} or
            cache.get('cache_state') not in (
                {'LIVE_FRESH'} if phase=='OPEN' else
                {'LIVE_FRESH','CACHE_FRESH','CACHE_STALE'}) or
            cache.get('continuation_state')!='CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL' or
            cache.get('source')!='IOL_MCP' or
            cache.get('decision_effect')!='OBSERVE_ONLY' or
            cache.get('real_money_authorized') is not False or
            cache.get('fallback_order')!=['IOL_LIVE_BOUNDED_RETRY',
                'IOL_LAST_KNOWN_GOOD','PPI_PRIMARY','BYMA_PUBLIC_COMPLEMENTARY'] or
            type(cache.get('section_states')) is not dict or
            not cache['section_states'] or
            type(cache.get('live_sections')) is not list or
            phase=='OPEN' and not any(section in cache['section_states'] and
                    cache['section_states'][section]=='LIVE_FRESH'
                    for section in cache['live_sections'])):
        raise ValueError('IOL_OAUTH_OR_CACHE_STATE_RED')
    # The guardian timer is 30 minutes, not a 240-second timer. Its OAuth
    # receipt is independent of the collector's <=240s live market cache.
    checked,checked_age=timestamp(guardian.get('checked_at'),now,1900)
    maximum=240 if phase=='OPEN' else 7*86400
    observed,observed_age=timestamp(cache.get('last_known_good_at'),now,maximum)
    refreshed,refresh_age=timestamp(cache.get('refreshed_at'),now,maximum)
    return {'guardian_state':guardian['state'],'guardian_checked_at':checked,
            'guardian_age_s':checked_age,'cache_state':cache['cache_state'],
            'cache_observed_at':observed,'cache_observed_age_s':observed_age,
            'cache_refreshed_at':refreshed,'cache_refreshed_age_s':refresh_age,
            'guardian_cadence_s':1800,'collector_freshness_limit_s':maximum}


def pulse(candidate,tree,deploy,integrity,phase='OPEN',frozen_sha=None,frozen_image=None):
    now=datetime.now(timezone.utc)
    receipt={'schema':'rc6.a161.postdeploy-ro.v1','at_utc':now.isoformat(),
             'at_art':now.astimezone(ART).isoformat(),
             'candidate_sha':candidate,'candidate_tree':tree,'deploy_sha':deploy,
             'audit_phase':phase,
             'host_mutations':0,'iol_provider_requests':0,'real_orders_sent':0,
             'operational_go':False}
    fd=None
    try:
        if (not all(SHA.fullmatch(value) for value in (candidate,tree,deploy)) or
                integrity not in {'check','reuse'} or phase not in {'OPEN','CLOSED'}):
            raise ValueError('EXACT_IDENTITY_INPUT_RED')
        receipt['droplet']={key:metadata(key) for key in ('id','region')}
        if receipt['droplet']!=EXPECTED:raise ValueError('DROPLET_IDENTITY_MISMATCH')
        if (not isinstance(frozen_sha,str) or not HASH.fullmatch(frozen_sha) or
                not isinstance(frozen_image,str) or
                not re.fullmatch(r'sha256:[0-9a-f]{64}',frozen_image)):
            raise ValueError('FROZEN_ARTIFACT_IDENTITY_INPUT_RED')
        fd=lock_existing()
        receipt['mutex']='EXISTING_ROOT_FLOCK_NO_BARRIER_WRITE'
        marker=exact_json(MARKER,65536)
        frozen,observed_frozen_sha=exact_json(
            DEPLOY/'porota-frozen-candidate.json',16384,with_digest=True)
        marker_at,_=timestamp(marker.get('recorded_at'),now,7*86400)
        if (marker.get('candidate_sha')!=candidate or marker.get('deploy_sha')!=deploy or
                datetime.fromisoformat(marker_at)<=datetime.fromisoformat(
                    '2026-10-27T22:10:13+00:00') or
                marker.get('validation_status')!='VALIDATED_RUNTIME' or
                marker.get('mode')!='PRODUCTION_PAPER' or
                type(marker.get('real_orders_sent')) is not int or
                marker['real_orders_sent']!=0 or
                marker.get('real_order_routes')!='NOT_CALLED' or
                marker.get('ppi_watch_untouched') is not True or
                observed_frozen_sha!=frozen_sha or
                frozen.get('candidate_sha')!=candidate or
                frozen.get('candidate_tree_sha')!=tree or
                frozen.get('image_id')!=frozen_image or
                frozen.get('paper_mode_required')!='PRODUCTION_PAPER' or
                frozen.get('real_order_capability_required')!='BLOCKED' or
                type(frozen.get('real_orders_sent_required')) is not int or
                frozen['real_orders_sent_required']!=0 or
                frozen.get('build_once') is not True):
            raise ValueError('FINAL_MARKER_IDENTITY_OR_SAFETY_RED')
        image=marker['image_id']
        if not isinstance(image,str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',image):
            raise ValueError('FINAL_IMAGE_ID_RED')
        receipt['release']={'validation':'VALIDATED_RUNTIME','image_id':image,
                            'frozen_manifest_sha256':observed_frozen_sha,
                            'frozen_preload_image_id':frozen_image,
                            'native_acceptance_mode':marker.get('native_acceptance_mode'),
                            'validated_at_utc':marker_at}
        receipt['containers']=containers(image)
        receipt['critical']=critical()
        receipt['paper_interlock']=paper_interlock(now,candidate)
        receipt['dashboard']=dashboard()
        receipt['dashboard_http']=200
        receipt['runtime']=runtime_health(now,candidate,tree)
        receipt['paper']=sqlite_snapshot(now,integrity,phase)
        display=receipt['dashboard']
        paper=receipt['paper']
        if (display['process_state']!=paper['observer_state'] or
                display['session_state']!=paper['session_state'] or
                display['market_open'] is not (phase=='OPEN') or
                display['ready']!=sum(paper['readiness_by_family'].values()) or
                any(display['families'].get(family,0)!=
                    paper['readiness_by_family'].get(family,0) for family in
                    set(display['families'])|set(paper['readiness_by_family']))):
            raise ValueError('DASHBOARD_DATABASE_TRUTH_MISMATCH')
        receipt['shadow']=shadow(now,phase)
        receipt['iol']=iol(now,phase)
        # The candidate policy is installed as the exact source pinned by the
        # validated marker and manifest. Read it, never call scheduler actions.
        policy=exact_json(ROOT/'ops/policy/host-control-plane-reconciliation-v2.json',65536)
        receipt['timers']=timers(policy,allow_historical_eod=phase=='OPEN',now=now)
        if phase=='OPEN' and not (receipt['timers']['all_healthy'] or
                receipt['timers']['historical_eod_a162_only']):
            raise ValueError('TIMERS_OR_SERVICE_RED')
        receipt['ppi_watch']=ppi_digest()
        usage=shutil.disk_usage('/')
        receipt['disk_free_bytes']=usage.free
        if usage.free<2*1024**3:raise ValueError('POSTDEPLOY_DISK_RESERVE_RED')
        disk_policy=exact_json(ROOT/'ops/policy/rc6-disk-housekeeping-v1.json',8192)
        inode_floor=float(disk_policy['disk']['min_inode_free_percent'])
        vfs=os.statvfs('/')
        inodes=100*vfs.f_ffree/vfs.f_files if vfs.f_files else 0
        receipt['disk_inode_free_percent']=round(inodes,6)
        receipt['disk_inode_floor_percent']=inode_floor
        if not 0<inode_floor<=100 or inodes<inode_floor:
            raise ValueError('POSTDEPLOY_INODE_RESERVE_RED')
        receipt['status']=('OPEN_PROVISIONAL' if phase=='OPEN' else
                           'CLOSED_PROVISIONAL' if receipt['timers']['all_healthy'] else
                           'CLOSED_DIAGNOSTIC_RED')
    except Exception as exc:
        receipt['status']='RED'
        receipt['error_code']=fail_code(exc)
    finally:
        if fd is not None:os.close(fd)
    print('RC6_A161_POSTDEPLOY_RO='+json.dumps(receipt,sort_keys=True,
          separators=(',',':'),allow_nan=False))
    return 0 if receipt['status'] in {
        'OPEN_PROVISIONAL','CLOSED_PROVISIONAL','CLOSED_DIAGNOSTIC_RED'} else 2


if __name__=='__main__':
    signal.alarm(150)
    if len(sys.argv)!=8:raise SystemExit('EXPECTED_CANDIDATE_TREE_DEPLOY_INTEGRITY_PHASE_FROZEN_SHA_IMAGE')
    raise SystemExit(pulse(*sys.argv[1:]))
