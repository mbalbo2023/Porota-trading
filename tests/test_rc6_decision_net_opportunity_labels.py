import hashlib,json,sqlite3
from rc6_decision_net_opportunity_labels import build

def canon(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))

def test_labels_hold_and_buy_with_executable_path(tmp_path,monkeypatch):
    p=tmp_path/"x.db";c=sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
    INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
    CREATE TABLE paper_decisions(id INTEGER PRIMARY KEY,decision_key TEXT,decided_at TEXT,symbol TEXT,action TEXT,score TEXT,reason TEXT,features_json TEXT);
    CREATE TABLE decision_evidence_snapshots(decision_key TEXT PRIMARY KEY,captured_at TEXT,schema_version TEXT,payload_sha256 TEXT,payload_json TEXT);
    CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,observed_at TEXT,symbol TEXT,asset_class TEXT,settlement TEXT,currency TEXT,market TEXT,bid TEXT,bid_size TEXT);
    """)
    q={"symbol":"GGAL","asset_class":"ACCIONES","settlement":"T1","currency":"ARS","market":"BYMA",
       "bid":"99","ask":"100","bid_size":"100","ask_size":"100","observed_at":"2026-09-23T14:00:00+00:00","book_at":"2026-09-23T14:00:00+00:00"}
    for i,action in enumerate(("HOLD","BUY"),1):
        dk=f"d{i}";payload={"decision_key":dk,"quote_used":q};raw=canon(payload);h=hashlib.sha256(raw.encode()).hexdigest()
        c.execute("INSERT INTO paper_decisions VALUES(?,?,?,?,?,?,?,?)",(i,dk,"2026-09-23T14:00:00+00:00","GGAL",action,"0.6","x",json.dumps({"momentum":"0.01","spread":"0.01"})))
        c.execute("INSERT INTO decision_evidence_snapshots VALUES(?,?,?,?,?)",(dk,"2026-09-23T14:00:00+00:00","v",h,raw))
    c.execute("INSERT INTO market_snapshots VALUES(1,'2026-09-23T14:30:00+00:00','GGAL','ACCIONES','T1','ARS','BYMA','103','100')")
    c.commit();c.close()
    import rc6_decision_net_opportunity_labels as m
    monkeypatch.setattr(m,"fee_rates",lambda a:(m.D("0.001"),m.D("0.0001")))
    monkeypatch.setattr(m,"session_cutoff",lambda q,at:(at+m.timedelta(minutes=120),""))
    r=build(p)
    assert r["coverage"]["labeled_rows"]==2
    assert r["coverage"]["by_action"]=={"HOLD":1,"BUY":1}
    assert all(x["opp_0.0025"]==1 for x in r["rows"])
    assert r["read_only"] and r["factual_fills_created"] is False
