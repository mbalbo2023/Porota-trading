import json
from pathlib import Path
import sqlite3

import et_shadow_learning_rc6 as learning


def make_db(path: Path):
    c=sqlite3.connect(path)
    c.executescript('''
    CREATE TABLE trade_gate_evaluations(
      id INTEGER PRIMARY KEY,evaluated_at TEXT,decision_key TEXT,symbol TEXT,
      technical_gate TEXT,ai_gate TEXT,patrimonial_gate TEXT,final_result TEXT,
      reason TEXT,paper_id TEXT,detail_json TEXT);
    CREATE TABLE paper_positions(
      paper_id TEXT PRIMARY KEY,status TEXT,net_pnl TEXT,opened_at TEXT,closed_at TEXT,
      symbol TEXT,asset_class TEXT,currency TEXT);
    ''')
    c.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,?,?,?)",
              ('p1','CLOSED','-100','2026-09-04T11:00:00-03:00','2026-09-04T12:00:00-03:00','GGAL','ACCIONES','ARS'))
    c.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,?,?,?)",
              ('p2','CLOSED','200','2026-09-05T11:00:00-03:00','2026-09-05T12:00:00-03:00','YPFD','ACCIONES','ARS'))
    d1={'economics':{'passed':False},'policy_evaluation':{'gates':{
        'expectancy':{'would_block':True},'regime':{'would_block':False},'sector_concentration':{'would_block':True}}}}
    d2={'economics':{'passed':False},'policy_evaluation':{'gates':{
        'expectancy':{'would_block':False},'regime':{'would_block':True},'sector_concentration':{'would_block':False}}}}
    c.execute("INSERT INTO trade_gate_evaluations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (1,'2026-09-04T11:00:00-03:00','d1','GGAL','APPROVE','OFF','APPROVE','OPENED_SIMULATED','', 'p1',json.dumps(d1)))
    c.execute("INSERT INTO trade_gate_evaluations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (2,'2026-09-05T11:00:00-03:00','d2','YPFD','APPROVE','OFF','APPROVE','OPENED_SIMULATED','', 'p2',json.dumps(d2)))
    c.commit();c.close()


def test_counterfactual_collector_is_read_only_and_per_policy(tmp_path):
    p=tmp_path/'observer.db';make_db(p)
    before=p.read_bytes()
    r=learning.collect(p)
    assert r['state']=='GREEN'
    assert r['read_only'] is True
    assert r['real_money_authorized'] is False
    assert r['policies']['ECONOMIC_GATE']['sessions']==2
    m=r['policies']['ECONOMIC_GATE']['metrics']
    assert m['would_block']==2
    assert m['losses_avoided']==1
    assert m['gains_removed']==1
    assert m['actual_paper_pnl']==100
    assert m['counterfactual_pnl_if_gate_bound']==0
    assert r['policies']['ECONOMIC_GATE']['can_block_paper'] is False
    assert p.read_bytes()==before


def test_missing_db_is_gray_not_invented(tmp_path):
    r=learning.collect(tmp_path/'missing.db')
    assert r['state']=='GRAY'
    assert r['detail']=='DB_MISSING'
