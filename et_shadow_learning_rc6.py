"""Read-only SHADOW counterfactual learning metrics for `/validacion`.

Consumes the existing gate ledger and realized PAPER positions. It does not
change a policy, does not write SQLite and does not authorize real money.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from cg_paper_workspace import database_path
from es_shadow_binding_contract_rc6 import summarize_counterfactuals, current_learning_contracts

TZ=ZoneInfo('America/Argentina/Buenos_Aires')
POLICY_KEYS=('ECONOMIC_GATE','EXPECTANCY','MARKET_REGIME','SECTOR_CONCENTRATION')


def _connect(path):
    c=sqlite3.connect(f'file:{path}?mode=ro',uri=True,timeout=20)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA query_only=ON')
    return c


def _date_ar(value):
    try:
        d=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if d.tzinfo is None: d=d.replace(tzinfo=TZ)
        return d.astimezone(TZ).date().isoformat()
    except Exception:
        return None


def _policy_would_block(detail, key):
    if key=='ECONOMIC_GATE':
        economics=detail.get('economics')
        if isinstance(economics,dict) and 'passed' in economics:
            return not bool(economics.get('passed'))
        return None
    evaluation=detail.get('policy_evaluation') or detail.get('rc4_policy_evaluation')
    if not isinstance(evaluation,dict): return None
    gates=evaluation.get('gates')
    if not isinstance(gates,dict): return None
    lookup={'EXPECTANCY':'expectancy','MARKET_REGIME':'regime','SECTOR_CONCENTRATION':'sector_concentration'}[key]
    gate=gates.get(lookup)
    if not isinstance(gate,dict): return None
    value=gate.get('would_block')
    return bool(value) if value is not None else None


def collect(path=None, limit=25000):
    path=Path(path or database_path())
    base={k:{'rows':[],'first_observed':None,'sessions':0} for k in POLICY_KEYS}
    result={'state':'GRAY','path':str(path),'read_only':True,'real_money_authorized':False,
            'target_sessions':20,'target_note':'~20 ruedas / 4 semanas es objetivo observacional, no promoción automática',
            'policies':{},'detail':''}
    if not path.exists():
        result['detail']='DB_MISSING'; return result
    try:
        with _connect(path) as c:
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'trade_gate_evaluations' not in tables:
                result['detail']='GATE_LEDGER_MISSING'; return result
            positions={}
            if 'paper_positions' in tables:
                for r in c.execute('SELECT paper_id,status,net_pnl,opened_at,closed_at,symbol,asset_class,currency FROM paper_positions'):
                    positions[str(r['paper_id'])]=dict(r)
            gates=c.execute('''SELECT evaluated_at,symbol,final_result,paper_id,detail_json
                FROM trade_gate_evaluations ORDER BY id DESC LIMIT ?''',(int(limit),)).fetchall()
        for r in gates:
            try: detail=json.loads(r['detail_json'] or '{}')
            except Exception: continue
            pos=positions.get(str(r['paper_id'])) if r['paper_id'] else None
            realized=None
            if pos and str(pos.get('status')).upper()=='CLOSED' and pos.get('net_pnl') not in (None,''):
                try: realized=float(pos['net_pnl'])
                except Exception: realized=None
            day=_date_ar(r['evaluated_at'])
            for key in POLICY_KEYS:
                block=_policy_would_block(detail,key)
                if block is None: continue
                base[key]['rows'].append({'would_block':block,'realized_net_pnl':realized,
                                          'symbol':r['symbol'],'day':day,'paper_id':r['paper_id'],
                                          'final_result':r['final_result']})
        contracts={c.key:c for c in current_learning_contracts()}
        for key in POLICY_KEYS:
            data=base[key]
            days=sorted({r['day'] for r in data['rows'] if r.get('day')})
            metrics=summarize_counterfactuals(data['rows'])
            contract=contracts[key]
            state='SHADOW' if metrics['evaluated'] else 'COLLECTING_EVIDENCE'
            result['policies'][key]={
                'stage':state,'authority':contract.authority,'can_block_paper':False,
                'automatic_promotion':False,'first_observed':days[0] if days else None,
                'sessions':len(days),'metrics':metrics,
                'target_sessions':20,'eligible_for_binding_decision':False,
                'eligibility_reason':'NO_AUTO_PROMOTION; requires sufficient evidence + tests + explicit authorization + versioned release',
            }
        result['state']='GREEN'
        result['detail']='COUNTERFACTUAL_OBSERVATION_ONLY'
        return result
    except Exception as exc:
        result['state']='RED'; result['detail']=f'{type(exc).__name__}:{str(exc)[:240]}'
        return result
