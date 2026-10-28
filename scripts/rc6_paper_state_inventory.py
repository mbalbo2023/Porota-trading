#!/usr/bin/env python3
"""P0-4 RC6 PAPER-state inventory — read-only, fail-closed evidence.

No DELETE/UPDATE/VACUUM/RESET is possible from this program.  It opens SQLite
with mode=ro + query_only and produces one JSON document for Sunday Readiness.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

TZ=ZoneInfo('America/Argentina/Buenos_Aires')


def conn_ro(path: Path):
    c=sqlite3.connect(f'file:{path}?mode=ro',uri=True,timeout=30)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA query_only=ON')
    return c


def scalar(c, sql, args=(), default=None):
    try:
        row=c.execute(sql,args).fetchone()
        return row[0] if row else default
    except sqlite3.Error:
        return default


def rows(c, sql, args=()):
    try:return [dict(r) for r in c.execute(sql,args).fetchall()]
    except sqlite3.Error:return []


def table_exists(c,name):
    return bool(scalar(c,"SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,),0))


def table_count(c,name):
    if not table_exists(c,name): return None
    return int(scalar(c,f'SELECT COUNT(*) FROM "{name}"',default=0) or 0)


def dbstat(c):
    try:
        return [dict(r) for r in c.execute('''SELECT name, SUM(pgsize) bytes, COUNT(*) pages
          FROM dbstat GROUP BY name ORDER BY bytes DESC LIMIT 30''').fetchall()]
    except sqlite3.Error as exc:
        return [{"state":"UNAVAILABLE","detail":type(exc).__name__}]


def inspect_observer(path: Path):
    out={"path":str(path),"exists":path.exists()}
    if not path.exists():
        out["state"]="MISSING"; return out
    with conn_ro(path) as c:
        qc=scalar(c,'PRAGMA quick_check',default='ERROR')
        state=rows(c,'SELECT * FROM observer_state WHERE id=1')
        out.update({
            "state":"GREEN" if qc=='ok' else "RED",
            "quick_check":qc,
            "bytes":path.stat().st_size,
            "observer_state":state[0] if state else {},
            "open_positions":table_count(c,'paper_positions') and int(scalar(c,"SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'",default=0) or 0),
            "closed_positions":int(scalar(c,"SELECT COUNT(*) FROM paper_positions WHERE status='CLOSED'",default=0) or 0) if table_exists(c,'paper_positions') else None,
            "decisions":table_count(c,'paper_decisions'),
            "gate_evaluations":table_count(c,'trade_gate_evaluations'),
            "market_snapshots":table_count(c,'market_snapshots'),
            "learning_samples":table_count(c,'paper_learning_samples'),
            "pending_outbox":int(scalar(c,"SELECT COUNT(*) FROM paper_notification_outbox WHERE status='PENDING'",default=0) or 0) if table_exists(c,'paper_notification_outbox') else None,
            "pending_exit_intents":int(scalar(c,"SELECT COUNT(*) FROM paper_exit_intents WHERE state NOT IN ('CLOSED','CANCELLED')",default=0) or 0) if table_exists(c,'paper_exit_intents') else None,
            "snapshot_first":scalar(c,'SELECT MIN(observed_at) FROM market_snapshots') if table_exists(c,'market_snapshots') else None,
            "snapshot_last":scalar(c,'SELECT MAX(observed_at) FROM market_snapshots') if table_exists(c,'market_snapshots') else None,
            "position_first":scalar(c,'SELECT MIN(opened_at) FROM paper_positions') if table_exists(c,'paper_positions') else None,
            "position_last":scalar(c,'SELECT MAX(COALESCE(closed_at,opened_at)) FROM paper_positions') if table_exists(c,'paper_positions') else None,
            "dbstat_top":dbstat(c),
        })
        if table_exists(c,'trade_gate_evaluations'):
            out['recent_gates']=rows(c,'''SELECT evaluated_at,symbol,final_result,reason,paper_id,detail_json
              FROM trade_gate_evaluations ORDER BY id DESC LIMIT 30''')
        if table_exists(c,'paper_positions'):
            out['open_position_rows']=rows(c,'''SELECT paper_id,symbol,asset_class,settlement,status,quantity,
              entry_price,opened_at,currency,market FROM paper_positions WHERE status='OPEN' ORDER BY opened_at''')
    return out


def inspect_history(path: Path):
    out={"path":str(path),"exists":path.exists()}
    if not path.exists(): out['state']='MISSING'; return out
    with conn_ro(path) as c:
        qc=scalar(c,'PRAGMA quick_check',default='ERROR')
        out.update({"state":"GREEN" if qc=='ok' else "RED","quick_check":qc,
                    "bytes":path.stat().st_size,"dbstat_top":dbstat(c)})
        tables=rows(c,"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        out['tables']={r['name']:table_count(c,r['name']) for r in tables}
        # Discover freshness without assuming one exact schema.
        candidates=[]
        for r in tables:
            name=r['name']
            cols={x['name'] for x in rows(c,f'PRAGMA table_info("{name}")')}
            for col in ('trading_day_ar','observed_at','timestamp','date','downloaded_at'):
                if col in cols:
                    candidates.append({"table":name,"column":col,
                                       "min":scalar(c,f'SELECT MIN("{col}") FROM "{name}"'),
                                       "max":scalar(c,f'SELECT MAX("{col}") FROM "{name}"')})
                    break
        out['freshness_candidates']=candidates
    return out


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--observer',default='/opt/porota-trading/data/paper_v17/observer_v17.db')
    ap.add_argument('--history',default='/opt/porota-trading/data/market_history.db')
    ap.add_argument('--pretty',action='store_true')
    args=ap.parse_args()
    result={
      "schema":"POROTA_RC6_P0_4_PAPER_STATE_V1",
      "generated_at_ar":datetime.now(TZ).isoformat(timespec='seconds'),
      "read_only":True,
      "mutations_available":False,
      "observer":inspect_observer(Path(args.observer)),
      "history":inspect_history(Path(args.history)),
    }
    invariant=(result['observer'].get('observer_state') or {}).get('real_orders_sent')
    result['real_orders_sent']=invariant
    result['gate']='GREEN' if result['observer'].get('quick_check')=='ok' and result['history'].get('quick_check')=='ok' and int(invariant or 0)==0 else 'RED'
    print(json.dumps(result,ensure_ascii=False,indent=2 if args.pretty else None,default=str))
    return 0 if result['gate']=='GREEN' else 2

if __name__=='__main__':
    raise SystemExit(main())
