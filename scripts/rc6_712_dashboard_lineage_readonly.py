#!/usr/bin/env python3
"""Bounded, non-sensitive dashboard producer census in the installed image."""
import collections
from datetime import datetime, timezone
import json
import sqlite3
import time

PATH='/app/data/paper_v17/observer_v17.db'
c=sqlite3.connect('file:'+PATH+'?mode=ro',uri=True,timeout=1)
c.row_factory=sqlite3.Row
c.execute('PRAGMA query_only=ON')
c.execute('PRAGMA busy_timeout=500')
deadline=time.monotonic()+4
c.set_progress_handler(lambda:int(time.monotonic()>deadline),100)
def rows(sql,args=()):
    return [dict(x) for x in c.execute(sql,args).fetchall()]
def table(name):
    return bool(rows('SELECT 1 FROM sqlite_master WHERE type=? AND name=?',('table',name)))
state=rows('SELECT mode,real_orders_sent,process_state,session_state,heartbeat_at FROM observer_state WHERE id=1')[0]
assert state['mode']=='PRODUCTION_PAPER' and state['real_orders_sent']==0
out={'schema':'RC6_712_DASHBOARD_LINEAGE_READONLY_V1','at_utc':datetime.now(timezone.utc).isoformat(),
     'paper':state,'tables':{}}
if table('paper_decisions'):
    decisions=rows('SELECT decision_key,action,decided_at FROM paper_decisions ORDER BY id DESC LIMIT 10')
    counter=collections.Counter()
    for decision in decisions:
        gate=rows('SELECT technical_gate,patrimonial_gate,final_result,detail_json FROM trade_gate_evaluations WHERE decision_key=? LIMIT 1',(decision['decision_key'],)) if table('trade_gate_evaluations') else []
        counter['decision:'+str(decision['action'])]+=1
        counter['gate_matched' if gate else 'gate_missing']+=1
        if gate:
            g=gate[0]
            counter['technical:'+str(g['technical_gate'])]+=1
            counter['risk:'+str(g['patrimonial_gate'])]+=1
            counter['final:'+str(g['final_result'])]+=1
            try: economics=json.loads(g['detail_json'] or '{}').get('economics')
            except (TypeError,ValueError): economics=None
            counter['economics:'+str(economics.get('state') if isinstance(economics,dict) else 'ABSENT')]+=1
    out['tables']['decisions_last_10']={'count':len(decisions),'summary':dict(counter),
        'latest_at':decisions[0]['decided_at'] if decisions else None}
if table('paper_daily_risk'):
    out['tables']['daily_risk']=rows('SELECT currency,state,evaluated_at FROM paper_daily_risk WHERE day=? ORDER BY currency LIMIT 8',(datetime.now(timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('America/Argentina/Buenos_Aires')).date().isoformat(),))
if table('paper_valuation_quality'):
    out['tables']['valuation_quality']=rows('SELECT currency,state,measured_at,stale_positions FROM paper_valuation_quality ORDER BY currency LIMIT 8')
if table('paper_equity_by_currency'):
    out['tables']['equity_latest']=rows('SELECT currency,measured_at FROM paper_equity_by_currency ORDER BY id DESC LIMIT 8')
if table('api_health'):
    out['tables']['source_health']=rows('SELECT component,state,checked_at,last_success_at FROM api_health ORDER BY component LIMIT 25')
if table('source_sync'):
    out['tables']['iol_sync']=rows("SELECT source,status,last_attempt_at,last_success_at FROM source_sync WHERE source LIKE 'IOL%' ORDER BY last_attempt_at DESC LIMIT 3")
c.close()
from rc6_trader_dashboard.projection import Projection, Store
with Store(PATH) as store:
    projection=Projection(store)
    shadow=projection.shadow
    report=shadow.get('report') or {}
    funnel=report.get('operational_funnel') or {}
    groups=funnel.get('by_currency_channel') or []
    status=shadow.get('status') or {}
    out['shadow']={'state':shadow.get('state'),'reason':shadow.get('reason'),
                   'error_class':shadow.get('error_class'),
                   'fresh':projection.shadow_fresh,
                   'liveness':projection.shadow_liveness,
                   'report_as_of':report.get('as_of'),
                   'status_state':status.get('state'),
                   'status_as_of':status.get('as_of'),
                   'report_keys':sorted(report),
                   'funnel_keys':sorted(funnel) if isinstance(funnel,dict) else [],
                   'groups':[{'currency':g.get('currency'),'channel':g.get('channel'),
                              'stages':g.get('stages')} for g in groups[:16] if isinstance(g,dict)],
                   'engines':{str(name):{'keys':sorted(plan),
                                         'telemetry_count':len(plan.get('telemetry') or []),
                                         'capacity_status':(plan.get('capacity') or {}).get('status'),
                                         'capacity_reason_codes':(plan.get('capacity') or {}).get('reason_codes')}
                              for name,plan in (report.get('engines') or {}).items() if isinstance(plan,dict)},
                   'funnel_counts':{k:v for k,v in (report.get('operational_funnel') or {}).get('counts',{}).items()
                                    if k in ('ELIGIBLE','TRADEABLE','HOT') and type(v) is int}}
print('RC6_712_DASHBOARD_LINEAGE='+json.dumps(out,ensure_ascii=False,sort_keys=True,separators=(',',':')))
