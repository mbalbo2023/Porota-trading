import json, sqlite3
from pathlib import Path
from rc6_trade_master_audit import build

def make_db(p: Path):
    c=sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
    INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
    CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY,source TEXT,strategy_version TEXT,symbol TEXT,asset_class TEXT,settlement TEXT,status TEXT,quantity TEXT,entry_price TEXT,entry_cost TEXT,stop_price TEXT,target_price TEXT,opened_at TEXT,closed_at TEXT,exit_price TEXT,exit_cost TEXT,gross_pnl TEXT,net_pnl TEXT,close_reason TEXT,features_json TEXT,max_favorable TEXT,max_adverse TEXT,currency TEXT,market TEXT,currency_source TEXT);
    CREATE TABLE paper_fills(id INTEGER PRIMARY KEY,paper_id TEXT,source TEXT,side TEXT,filled_at TEXT,quantity TEXT,price TEXT,costs TEXT,slippage TEXT);
    CREATE TABLE paper_learning_samples(paper_id TEXT PRIMARY KEY,source TEXT,strategy_version TEXT,feature_timestamp TEXT,features_json TEXT,label_timestamp TEXT,net_return_pct TEXT,outcome TEXT,duration_minutes INTEGER);
    CREATE TABLE paper_decisions(id INTEGER PRIMARY KEY,source TEXT,strategy_version TEXT,decision_key TEXT UNIQUE,decided_at TEXT,symbol TEXT,action TEXT,score TEXT,reason TEXT,features_json TEXT);
    CREATE TABLE trade_gate_evaluations(id INTEGER PRIMARY KEY,evaluated_at TEXT,decision_key TEXT UNIQUE,symbol TEXT,technical_gate TEXT,ai_gate TEXT,patrimonial_gate TEXT,final_result TEXT,reason TEXT,paper_id TEXT,detail_json TEXT);
    CREATE TABLE decision_evidence_snapshots(decision_key TEXT PRIMARY KEY,captured_at TEXT,schema_version TEXT,payload_sha256 TEXT,payload_json TEXT);
    CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,source TEXT,observed_at TEXT,symbol TEXT,asset_class TEXT,settlement TEXT,last TEXT,bid TEXT,ask TEXT,bid_size TEXT,ask_size TEXT,currency TEXT,market TEXT,book_at TEXT);
    """)
    features=json.dumps({"sma3":"101","sma8":"100","momentum":"0.01","spread":"0.002","samples":8,"paper_threshold":"0.62"})
    vals=[("p1","GGAL","100","103","250"),("p2","YPFD","100","98","-220")]
    for i,(pid,sym,entry,exitp,pnl) in enumerate(vals,1):
        opened=f"2026-09-0{i}T14:00:00+00:00"; closed=f"2026-09-0{i}T15:00:00+00:00"; dk=f"d{i}"
        c.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (pid,"X","v",sym,"ACCIONES","T1","CLOSED","1",entry,"1","98","105",opened,closed,exitp,"1",pnl,pnl,"STOP_PAPER",features,"0","0","ARS","BYMA","EXPLICIT"))
        c.execute("INSERT INTO paper_fills VALUES(?,?,?,?,?,?,?,?,?)",(i*2-1,pid,"X","BUY_SIMULATED",opened,"1",entry,"1","0"))
        c.execute("INSERT INTO paper_fills VALUES(?,?,?,?,?,?,?,?,?)",(i*2,pid,"X","SELL_SIMULATED",closed,"1",exitp,"1","0"))
        c.execute("INSERT INTO paper_learning_samples VALUES(?,?,?,?,?,?,?,?,?)",(pid,"X","v",opened,features,closed,pnl,"WIN" if float(pnl)>0 else "LOSS",60))
        c.execute("INSERT INTO paper_decisions VALUES(?,?,?,?,?,?,?,?,?,?)",(i,"X","v",dk,opened,sym,"BUY","0.70","ok",features))
        c.execute("INSERT INTO trade_gate_evaluations VALUES(?,?,?,?,?,?,?,?,?,?,?)",(i,opened,dk,sym,"APPROVE","NOT_USED","APPROVE","OPENED_SIMULATED","ok",pid,features))
        payload={"decision_key":dk,"decision":{"paper_id":pid,"symbol":sym,"final_result":"OPENED_SIMULATED"},"quote_used":{"bid":entry,"ask":entry},"inputs_used":{"iol":{"state":"READY"}}}
        raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))
        import hashlib
        c.execute("INSERT INTO decision_evidence_snapshots VALUES(?,?,?,?,?)",(dk,opened,"v",hashlib.sha256(raw.encode()).hexdigest(),raw))
        c.execute("INSERT INTO market_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(i,"PPI",opened,sym,"ACCIONES","T1",entry,str(float(entry)*1.01),str(float(entry)*1.02),"10","10","ARS","BYMA",opened))
    c.commit(); c.close()

def test_master_reconciles_closed_trades(tmp_path):
    p=tmp_path/"x.db"; make_db(p); r=build(p)
    assert r["read_only"] is True
    assert r["integrity"]["closed_total"]==2
    assert r["integrity"]["duplicate_paper_ids"]==0
    assert r["integrity"]["by_currency"]["ARS"]["wins"]==1
    assert r["integrity"]["by_currency"]["ARS"]["losses"]==1
    assert r["integrity"]["immutable_evidence_verified"]==2
    assert r["integrity"]["mfe_mae_measured"]==2
    assert all(t["iol"]["state"]=="READY" for t in r["trades"])
