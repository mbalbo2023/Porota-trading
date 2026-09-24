import json,sqlite3
from rc6_exit_target_counterfactual import build

def test_lower_target_precedes_factual_close_only_with_depth(tmp_path,monkeypatch):
    p=tmp_path/"x.db";c=sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
    INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
    CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY,symbol TEXT,asset_class TEXT,settlement TEXT,currency TEXT,market TEXT,status TEXT,quantity TEXT,entry_price TEXT,entry_cost TEXT,opened_at TEXT,closed_at TEXT,exit_price TEXT,exit_cost TEXT,net_pnl TEXT,close_reason TEXT,features_json TEXT);
    CREATE TABLE paper_exit_intents(paper_id TEXT PRIMARY KEY,due_at TEXT);
    CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,source TEXT,observed_at TEXT,symbol TEXT,asset_class TEXT,settlement TEXT,currency TEXT,market TEXT,bid TEXT,bid_size TEXT);
    """)
    f=json.dumps({"contract_cash_multiplier":"1"})
    c.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",("p","GGAL","ACCIONES","T1","ARS","BYMA","CLOSED","10","100","7.865","2026-09-23T14:00:00+00:00","2026-09-23T15:00:00+00:00","98","0.6","-28.465","STOP_PAPER",f))
    c.execute("INSERT INTO paper_exit_intents VALUES(?,?)",("p","2026-09-23T15:00:00+00:00"))
    c.execute("INSERT INTO market_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",(1,"PPI","2026-09-23T14:30:00+00:00","GGAL","ACCIONES","T1","ARS","BYMA","101","200"))
    c.execute("INSERT INTO market_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",(2,"PPI","2026-09-23T14:40:00+00:00","GGAL","ACCIONES","T1","ARS","BYMA","102","200"))
    c.commit();c.close()
    import rc6_exit_target_counterfactual as m
    monkeypatch.setattr(m,"fee_rates",lambda asset:(m.D("0.007865"),m.D("0.000605")))
    r=build(p)
    assert r["targets"]["0.005"]["changed_trades"]==1
    assert r["targets"]["0.05"]["changed_trades"]==0
    assert r["net_targets"]["0.005"]["changed_trades"]==1
    assert float(r["net_targets"]["0.005"]["changed"][0]["net_return"]) >= 0.005
    assert r["read_only"] is True
