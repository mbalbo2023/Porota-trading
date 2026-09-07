import sqlite3
import fh_postclose_bundle_rc6 as m


def db():
    c=sqlite3.connect(':memory:')
    c.executescript('''
    CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,process_state TEXT,session_state TEXT,ppi_auth TEXT,heartbeat_at TEXT,last_market_data_at TEXT,real_orders_sent INTEGER);
    INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER','RUNNING','MARKET_CLOSED','OK','2026-09-07T20:10:00+00:00','2026-09-07T20:00:00+00:00',0);
    CREATE TABLE paper_positions(paper_id TEXT,status TEXT,opened_at TEXT,closed_at TEXT,net_pnl REAL);
    INSERT INTO paper_positions VALUES('p1','CLOSED','2026-09-07T14:00:00','2026-09-07T15:00:00',100),('p2','OPEN','2026-09-07T16:00:00',NULL,NULL);
    CREATE TABLE trade_gate_evaluations(evaluated_at TEXT,final_result TEXT);
    INSERT INTO trade_gate_evaluations VALUES('2026-09-07T14:00:00','BLOCKED'),('2026-09-07T14:01:00','OPENED_SIMULATED');
    CREATE TABLE scalping_candidates(evaluated_at TEXT,action TEXT);
    INSERT INTO scalping_candidates VALUES('2026-09-07T14:00:00','HOLD');
    ''')
    c.row_factory=sqlite3.Row; return c

def test_summary_collects_close_evidence_without_writes():
    c=db(); before=c.total_changes
    r=m.summarize(c,trading_day='2026-09-07'); after=c.total_changes
    assert before==after
    assert r['positions_open']==1 and r['positions_closed_day']==1
    assert r['net_pnl_closed_day']==100
    assert r['gate_results']=={'BLOCKED':1,'OPENED_SIMULATED':1}
    assert r['scalping_buy_candidates_day']==0
    assert r['observer_state']['real_orders_sent']==0
    assert m.assert_read_only(r)

def test_nonzero_real_orders_is_red():
    c=db(); c.execute('UPDATE observer_state SET real_orders_sent=1 WHERE id=1'); r=m.summarize(c,trading_day='2026-09-07')
    try: m.assert_read_only(r); assert False
    except RuntimeError as e: assert str(e)=='REAL_ORDERS_SENT_NONZERO'
