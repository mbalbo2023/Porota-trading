#!/usr/bin/env python3
"""P0-5 Sunday Readiness — fail-closed host truth aggregator for RC6.

Read-only by design. It never sends orders, never edits config, never restarts
services and never performs a network order test.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from zoneinfo import ZoneInfo

TZ=ZoneInfo('America/Argentina/Buenos_Aires')
ROOT=Path(os.getenv('POROTA_REPO','/opt/porota-trading'))
OBS=Path(os.getenv('PAPER_V17_DB_PATH','/opt/porota-trading/data/paper_v17/observer_v17.db'))
HIST=Path(os.getenv('HIST_DB_PATH','/opt/porota-trading/data/market_history.db'))
MIN_FREE_BYTES=int(os.getenv('RC6_MIN_FREE_BYTES',str(8*1024**3)))
EXPECTED_BASE='852b812d610860b57b579212978443f7450e8855'


def cmd(args,timeout=25):
    try:
        p=subprocess.run(args,cwd=ROOT,text=True,capture_output=True,timeout=timeout,check=False)
        return {'rc':p.returncode,'out':p.stdout.strip(),'err':p.stderr.strip()}
    except Exception as exc:
        return {'rc':999,'out':'','err':f'{type(exc).__name__}:{exc}'}


def db_check(path):
    if not path.exists(): return {'state':'RED','detail':'MISSING'}
    try:
        c=sqlite3.connect(f'file:{path}?mode=ro',uri=True,timeout=30)
        c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON')
        qc=c.execute('PRAGMA quick_check').fetchone()[0]
        state={}
        if path==OBS:
            row=c.execute('SELECT mode,process_state,session_state,ppi_auth,real_orders_sent,heartbeat_at FROM observer_state WHERE id=1').fetchone()
            state=dict(row) if row else {}
        c.close()
        return {'state':'GREEN' if qc=='ok' else 'RED','quick_check':qc,'bytes':path.stat().st_size,'observer_state':state}
    except Exception as exc:
        return {'state':'RED','detail':f'{type(exc).__name__}:{exc}'}


def active_rc4_units():
    r=cmd(['systemctl','list-units','--all','--no-legend','--no-pager'])
    lines=[x for x in r['out'].splitlines() if 'rc4' in x.lower() and ('running' in x.lower() or 'active' in x.lower())]
    return {'state':'GREEN' if r['rc']==0 and not lines else 'RED','lines':lines,'rc':r['rc']}


def timers():
    r=cmd(['systemctl','list-timers','--all','--no-pager','--no-legend'])
    text=r['out']
    suspicious=[x for x in text.splitlines() if 'rc4' in x.lower()]
    return {'state':'GREEN' if r['rc']==0 and not suspicious else 'RED','rc':r['rc'],'rc4_lines':suspicious,'sample':text.splitlines()[:80]}


def containers():
    result={}
    for name in ('porota_production_observer','porota_production_dashboard'):
        r=cmd(['docker','inspect','-f','{{.State.Running}}|{{.Config.Image}}|{{.RestartCount}}|{{.HostConfig.ReadonlyRootfs}}',name])
        parts=r['out'].split('|') if r['rc']==0 else []
        ok=len(parts)>=4 and parts[0]=='true' and parts[2]=='0'
        if name.endswith('observer'):
            ok=ok and parts[3]=='true'
        result[name]={'state':'GREEN' if ok else 'RED','raw':r['out'],'rc':r['rc']}
    return result


def git_truth():
    head=cmd(['git','rev-parse','HEAD'])
    branch=cmd(['git','branch','--show-current'])
    base=cmd(['git','merge-base','--is-ancestor',EXPECTED_BASE,'HEAD'])
    return {'state':'GREEN' if head['rc']==0 and branch['rc']==0 and base['rc']==0 else 'RED',
            'head':head['out'],'branch':branch['out'],'rc5_ancestor':base['rc']==0}


def disk():
    usage=shutil.disk_usage('/')
    return {'state':'GREEN' if usage.free>=MIN_FREE_BYTES else 'RED','total':usage.total,'used':usage.used,'free':usage.free,'minimum':MIN_FREE_BYTES}


def calendar_gate():
    # Import project calendar locally; no web dependency in Sunday readiness.
    code="""from datetime import date\nimport ak_byma_calendar as c\nprint('1' if c.es_dia_habil_operativo(date(2026,9,7)) else '0')\n"""
    r=cmd([sys.executable,'-c',code])
    return {'state':'GREEN' if r['rc']==0 and r['out'].strip()=='1' else 'RED','monday_2026_09_07_operational':r['out'].strip()=='1','detail':r['err']}


def policy_gate():
    # Evaluate actual generated PAPER defaults without starting containers.
    code="""import porota_mode_manager as m\ns=m.paper_settings({})\nkeys=['PAPER_ECONOMIC_GATE_MODE','PAPER_EXPECTANCY_POLICY','PAPER_MARKET_REGIME_POLICY','PAPER_SECTOR_CONCENTRATION_POLICY']\nprint('|'.join(f'{k}={s.get(k)}' for k in keys))\n"""
    r=cmd([sys.executable,'-c',code])
    raw=r['out']
    ok=(r['rc']==0 and 'PAPER_ECONOMIC_GATE_MODE=SHADOW' in raw and
        'PAPER_EXPECTANCY_POLICY=BINDING' not in raw and
        'PAPER_MARKET_REGIME_POLICY=BINDING' not in raw and
        'PAPER_SECTOR_CONCENTRATION_POLICY=BINDING' not in raw)
    return {'state':'GREEN' if ok else 'RED','raw':raw,'rc':r['rc'],'detail':r['err']}


def main():
    now=datetime.now(TZ).isoformat(timespec='seconds')
    obs=db_check(OBS);hist=db_check(HIST)
    order_value=(obs.get('observer_state') or {}).get('real_orders_sent')
    order_gate={'state':'GREEN' if str(order_value)=='0' else 'RED','value':order_value}
    checks={
      'git':git_truth(),
      'containers':containers(),
      'observer_db':obs,
      'history_db':hist,
      'disk':disk(),
      'active_rc4_units':active_rc4_units(),
      'timers':timers(),
      'calendar':calendar_gate(),
      'learning_policies':policy_gate(),
      'real_orders_sent':order_gate,
    }
    reds=[]
    def walk(prefix,obj):
        if isinstance(obj,dict):
            if obj.get('state')=='RED': reds.append(prefix)
            for k,v in obj.items():
                if isinstance(v,dict): walk(prefix+'.'+k if prefix else k,v)
    walk('',checks)
    result={'schema':'POROTA_RC6_SUNDAY_READINESS_V1','generated_at_ar':now,
            'read_only':True,'network_order_test_performed':False,
            'checks':checks,'red_checks':sorted(set(x.strip('.') for x in reds))}
    result['status']='GREEN' if not result['red_checks'] else 'RED'
    print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    return 0 if result['status']=='GREEN' else 2

if __name__=='__main__':
    raise SystemExit(main())
