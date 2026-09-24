import hashlib,json,sqlite3
from rc6_decision_unit_opportunity import build

def canon(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))

def test_labels_use_ask_future_bid_and_keep_future_out_of_features(tmp_path,monkeypatch):
    p=tmp_path/"x.db";c=sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
    INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
    CREATE TABLE paper_decisions(id INTEGER PRIMARY KEY,decision_key TEXT,decided_at TEXT,symbol TEXT,action TEXT,score TEXT,features_json TEXT);
    CREATE TABLE decision_evidence_snapshots(decision_key TEXT PRIMARY KEY,captured_at TEXT,payload_sha256 TEXT,payload_json TEXT);
    CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,observed_at TEXT,symbol TEXT,asset_class TEXT,settlement TEXT,currency TEXT,market TEXT,bid TEXT,bid_size TEXT);
    """)
    feat={"momentum":"0.001","spread":"0.001","samples":8,"candidate":{"action":"BUY"},"hard_safety":{"x":True},
          "historical_candle_shadow":{"state":"READY","candles_5m":{"momentum_3v15":"-0.01","avg_range_5m":"0.02"},"shadow_score_delta":"0.01"}}
    c.execute("INSERT INTO paper_decisions VALUES(1,'d','2026-09-23T14:00:00+00:00','GGAL','HOLD','0.61',?)",(json.dumps(feat),))
    q={"symbol":"GGAL","asset_class":"ACCIONES","settlement":"T1","currency":"ARS","market":"BYMA",
       "bid":"100","ask":"100","bid_size":"10","ask_size":"10","observed_at":"2026-09-23T14:00:00+00:00"}
    payload={"decision_key":"d","quote_used":q};raw=canon(payload);h=hashlib.sha256(raw.encode()).hexdigest()
    c.execute("INSERT INTO decision_evidence_snapshots VALUES(?,?,?,?)",("d","2026-09-23T14:00:00+00:00",h,raw))
    c.execute("INSERT INTO market_snapshots VALUES(1,'2026-09-23T14:10:00+00:00','GGAL','ACCIONES','T1','ARS','BYMA','102','20')")
    c.commit();c.close()
    import rc6_decision_unit_opportunity as m
    monkeypatch.setattr(m,"rates",lambda asset:(m.D("0"),m.D("0")))
    r=build(p)
    assert r["coverage"]["labeled_rows"]==1
    row=r["rows"][0]
    assert row["action"]=="HOLD"
    assert row["labels"]["hit_net_0.01_before_stop"]==1
    assert row["features"]["candle_avg_range_5m"]=="0.02"
    assert r["hypothetical_portfolio_trades"] is False
