"""RC6 postclose evidence bundle builder.

Pure SQL summarizer. It accepts an already-open SQLite connection and performs
SELECT/PRAGMA only. It never writes the PAPER DB and never sends orders.
"""
from __future__ import annotations
from datetime import date


def tables(c): return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}

def scalar(c,sql,args=(),default=0):
    r=c.execute(sql,args).fetchone(); return default if not r or r[0] is None else r[0]

def summarize(c, *, trading_day:str)->dict:
    day=str(trading_day)[:10]; date.fromisoformat(day)
    t=tables(c); out={'trading_day':day,'db_quick_check':scalar(c,'PRAGMA quick_check',default='UNKNOWN')}
    if 'observer_state' in t:
        r=c.execute('SELECT mode,process_state,session_state,ppi_auth,heartbeat_at,last_market_data_at,real_orders_sent FROM observer_state WHERE id=1').fetchone()
        if r: out['observer_state']=dict(zip(('mode','process_state','session_state','ppi_auth','heartbeat_at','last_market_data_at','real_orders_sent'),r))
    if 'paper_positions' in t:
        out['positions_open']=scalar(c,"SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'")
        out['positions_opened_day']=scalar(c,"SELECT COUNT(*) FROM paper_positions WHERE substr(opened_at,1,10)=?",(day,))
        out['positions_closed_day']=scalar(c,"SELECT COUNT(*) FROM paper_positions WHERE status='CLOSED' AND substr(closed_at,1,10)=?",(day,))
        out['net_pnl_closed_day']=scalar(c,"SELECT COALESCE(SUM(net_pnl),0) FROM paper_positions WHERE status='CLOSED' AND substr(closed_at,1,10)=?",(day,),0)
    if 'trade_gate_evaluations' in t:
        out['gate_evaluations_day']=scalar(c,"SELECT COUNT(*) FROM trade_gate_evaluations WHERE substr(evaluated_at,1,10)=?",(day,))
        out['gate_results']={str(r[0]):int(r[1]) for r in c.execute("SELECT final_result,COUNT(*) FROM trade_gate_evaluations WHERE substr(evaluated_at,1,10)=? GROUP BY final_result",(day,))}
    if 'market_snapshots' in t:
        out['market_snapshots_day']=scalar(c,"SELECT COUNT(*) FROM market_snapshots WHERE substr(observed_at,1,10)=?",(day,))
        out['last_market_snapshot']=scalar(c,"SELECT MAX(observed_at) FROM market_snapshots",default=None)
    if 'scalping_candidates' in t:
        out['scalping_evaluations_day']=scalar(c,"SELECT COUNT(*) FROM scalping_candidates WHERE substr(evaluated_at,1,10)=?",(day,))
        out['scalping_buy_candidates_day']=scalar(c,"SELECT COUNT(*) FROM scalping_candidates WHERE substr(evaluated_at,1,10)=? AND action='BUY_CANDIDATE'",(day,))
    if 'ppi_intraday_contract_state' in t:
        out['intraday_contract_states']={str(r[0]):int(r[1]) for r in c.execute('SELECT state,COUNT(*) FROM ppi_intraday_contract_state GROUP BY state')}
    return out

def assert_read_only(summary:dict):
    if int(summary.get('observer_state',{}).get('real_orders_sent',0))!=0: raise RuntimeError('REAL_ORDERS_SENT_NONZERO')
    if summary.get('db_quick_check')!='ok': raise RuntimeError('DB_QUICK_CHECK_NOT_OK')
    return True
