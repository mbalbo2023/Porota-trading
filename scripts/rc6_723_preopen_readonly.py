#!/usr/bin/env python3
"""Bounded, host-only RC6 preopen facts. No service, database or PPI Watch mutation."""
import json
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NOW = datetime.now(timezone.utc)
def cmd(argv, seconds=5, limit=240):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=seconds, check=False)
        return {'rc':r.returncode,'out':r.stdout.strip()[:limit]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'rc':999,'error':type(exc).__name__}

def age(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if dt.tzinfo is None: return None
        return round((NOW-dt.astimezone(timezone.utc)).total_seconds(),1)
    except (TypeError, ValueError):
        return None

local=NOW.astimezone(ZoneInfo('America/Argentina/Buenos_Aires'))
out={'schema':'RC6_723_PREOPEN_READONLY_V1','utc':NOW.isoformat(),
     'art':local.isoformat(),'writes':False,'market_open_evidence':'NOT_INFERRED'}
units=['porota-preopen-rc6.timer','porota-preopen-rc6.service',
       'porota-fast-functional-health-rc6.timer','porota-full-db-integrity-rc6.timer',
       'porota-market-watch-silence-rc6.timer','porota-market-watch-silence-rc6.service',
       'porota-a3-history-daily-rc6.timer','porota-a3-history-reconcile-rc6.timer',
       'porota-a3-history-weekend-rc6.timer','porota-iol-shadow-collector-rc6.timer',
       'porota-host-general-backup-rc6.timer','porota-contract-evidence-rc4.timer',
       'porota-functional-health-rc6.timer',
       'porota-a3-history-rc6@DAILY_INCREMENTAL.service',
       'porota-a3-history-rc6@RECONCILE.service',
       'porota-a3-history-rc6@WEEKEND_DEEP.service','porota-iol-shadow-collector-rc6.service']
out['units']={}
for unit in units:
    active=cmd(['systemctl','is-active',unit],3)
    enabled=cmd(['systemctl','is-enabled',unit],3)
    out['units'][unit]={'active':active.get('out') or active.get('error'),
                        'enabled':enabled.get('out') or enabled.get('error')}
for unit in ('porota-preopen-rc6.service','porota-market-watch-silence-rc6.service',
             'porota-a3-history-rc6@DAILY_INCREMENTAL.service',
             'porota-a3-history-rc6@RECONCILE.service',
             'porota-a3-history-rc6@WEEKEND_DEEP.service','porota-iol-shadow-collector-rc6.service'):
    s=cmd(['systemctl','show',unit,'--property=Result,ExecMainStatus,ActiveEnterTimestamp,InactiveEnterTimestamp','--no-pager'],3)
    out['units'][unit]['last']={k:v for k,v in (line.split('=',1) for line in s.get('out','').splitlines() if '=' in line)}
out['containers']={}
for name in ('porota_production_observer','porota_production_dashboard','porota_critical_approval_rc6'):
    s=cmd(['docker','inspect','-f','{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}|{{.Image}}',name],5)
    out['containers'][name]={'rc':s['rc'],'value':s.get('out') or s.get('error')}
db=Path('/opt/porota-trading/data/paper_v17/observer_v17.db')
try:
    c=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=.5)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA query_only=ON')
    c.execute('PRAGMA busy_timeout=300')
    deadline=time.monotonic()+3
    c.set_progress_handler(lambda:int(time.monotonic()>deadline),100)
    cols={r['name'] for r in c.execute('PRAGMA table_info(observer_state)')}
    selected=[x for x in ('mode','real_orders_sent','process_state','session_state','heartbeat_at','last_market_data_at','ppi_auth') if x in cols]
    row=c.execute('SELECT '+','.join(selected)+' FROM observer_state WHERE id=1').fetchone()
    out['observer']={k:row[k] for k in selected} if row else {'state':'ABSENT'}
    for key in ('heartbeat_at','last_market_data_at'):
        if key in out['observer']:out['observer'][key+'_age_seconds']=age(out['observer'][key])
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    out['open_paper']={}
    for name,status in [('paper_positions','OPEN'),('paper_future_positions','ACTIVE'),('paper_cauciones','OPEN')]:
        out['open_paper'][name]='AT_LEAST_2' if name in tables and len(c.execute('SELECT 1 FROM '+name+' WHERE status=? LIMIT 2',(status,)).fetchall())==2 else ('AT_MOST_1' if name in tables else 'TABLE_ABSENT')
    out['api_health']=[]
    if 'api_health' in tables:
        for row in c.execute("SELECT component,state,checked_at,last_success_at FROM api_health WHERE component IN ('PPI_AUTH','PAPER_ECONOMIC_GATE_SHADOW','PAPER_RISK_GATE','PAPER_MARKET_GATE','PPI_MARKET_DATA') ORDER BY component LIMIT 8"):
            item=dict(row); item['check_age_seconds']=age(item.get('checked_at'));out['api_health'].append(item)
    c.close()
except (OSError,sqlite3.Error,ValueError) as exc:
    out['observer_error']=type(exc).__name__
disk=shutil.disk_usage('/')
fs=os.statvfs('/')
out['root_capacity']={'free_bytes':disk.free,'inode_free_percent':round(100*fs.f_ffree/fs.f_files,2)}
state=Path('/opt/porota-trading/data/deploy/CURRENT_STATE_V2.json')
try:
    payload=json.loads(state.read_text(encoding='utf-8'))
    out['current_state']={k:payload.get(k) for k in
        ('validation_status','mode','real_orders_sent','real_order_routes','source_sha','candidate_sha','image_id')}
except (OSError,ValueError,TypeError) as exc:
    out['current_state']={'error':type(exc).__name__}
ppi=cmd(['systemctl','list-unit-files','--no-legend','--no-pager'],5,65536)
matches=[line.split()[0] for line in ppi.get('out','').splitlines()
         if 'ppi' in line.lower() and 'watch' in line.lower()]
out['ppi_watch_passive']={'enumeration_rc':ppi['rc'],'matching_units':matches[:4]}
legacy=[line.split()[0] for line in ppi.get('out','').splitlines()
        if any(tag in line.split()[0].lower() for tag in ('rc4','rc5','hf5'))]
out['legacy_units']={'enumeration_rc':ppi['rc'],'units':[
    {'name':unit,'active':cmd(['systemctl','is-active',unit],3).get('out'),
     'enabled':cmd(['systemctl','is-enabled',unit],3).get('out')}
    for unit in legacy[:24]],'truncated':len(legacy)>24}
next_preopen=cmd(['systemctl','show','porota-preopen-rc6.timer',
                  '--property=NextElapseUSecRealtime,LastTriggerUSec','--no-pager'],3)
out['preopen_schedule']={k:v for k,v in (line.split('=',1) for line in next_preopen.get('out','').splitlines() if '=' in line)}
action4=Path('/opt/porota-trading/data/paper_v17/artifacts/observer_v17.db/reports/rc6_action4_auditoria_latest.json')
try:
    stat=action4.stat()
    if stat.st_size>1048576:
        out['action4_report']={'state':'OVERSIZE','bytes':stat.st_size}
    else:
        report=json.loads(action4.read_text(encoding='utf-8'))
        out['action4_report']={'state':'PRESENT','bytes':stat.st_size,
          'status':report.get('status'),'generated_at_utc':report.get('generated_at_utc'),
          'scope':report.get('scope'),'age_seconds':age(report.get('generated_at_utc'))}
except (OSError,ValueError,TypeError) as exc:
    out['action4_report']={'state':'NO_VERIFICADO','error':type(exc).__name__}
print('RC6_723_PREOPEN_READONLY='+json.dumps(out,ensure_ascii=False,sort_keys=True,separators=(',',':')))
