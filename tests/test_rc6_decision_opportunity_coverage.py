import hashlib,json,sqlite3
from rc6_decision_opportunity_coverage import build

def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))

def test_coverage_requires_valid_point_in_time_identity_book_and_future_depth(tmp_path):
    p=tmp_path/"x.db";c=sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
    INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
    CREATE TABLE paper_decisions(id INTEGER PRIMARY KEY,source TEXT,strategy_version TEXT,decision_key TEXT,decided_at TEXT,symbol TEXT,action TEXT,score TEXT,reason TEXT,features_json TEXT);
    CREATE TABLE decision_evidence_snapshots(decision_key TEXT PRIMARY KEY,captured_at TEXT,schema_version TEXT,payload_sha256 TEXT,payload_json TEXT);
    CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,observed_at TEXT,symbol TEXT,asset_class TEXT,settlement TEXT,currency TEXT,market TEXT,bid TEXT,ask TEXT,bid_size TEXT,ask_size TEXT);
    """)
    c.execute("INSERT INTO paper_decisions VALUES(1,'P','v','d','2026-09-23T14:00:00+00:00','GGAL','HOLD','0.6','x','{}')")
    q={"symbol":"GGAL","asset_class":"ACCIONES","settlement":"T1","currency":"ARS","market":"BYMA",
       "bid":"100","ask":"101","bid_size":"10","ask_size":"10","observed_at":"2026-09-23T14:00:00+00:00","book_at":"2026-09-23T14:00:00+00:00"}
    payload={"decision_key":"d","quote_used":q}
    raw=canonical(payload);h=hashlib.sha256(raw.encode()).hexdigest()
    c.execute("INSERT INTO decision_evidence_snapshots VALUES(?,?,?,?,?)",("d","2026-09-23T14:00:00+00:00","v",h,raw))
    c.execute("INSERT INTO market_snapshots VALUES(1,'2026-09-23T14:10:00+00:00','GGAL','ACCIONES','T1','ARS','BYMA','102','103','50','50')")
    c.commit();c.close()
    r=build(p)
    assert r["read_only"] and r["labels_created"] is False
    assert r["coverage"]["decisions_total"]==1
    assert r["coverage"]["label_path_ready"]==1
    assert r["coverage"]["actions_ready"]["HOLD"]==1
