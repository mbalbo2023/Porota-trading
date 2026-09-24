import hashlib,json,sqlite3
from rc6_decision_feature_coverage import build

def canon(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))

def test_feature_coverage_is_action_and_day_specific(tmp_path):
    p=tmp_path/"x.db";c=sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
    INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
    CREATE TABLE paper_decisions(decision_key TEXT PRIMARY KEY,decided_at TEXT,action TEXT,features_json TEXT);
    CREATE TABLE decision_evidence_snapshots(decision_key TEXT PRIMARY KEY,payload_sha256 TEXT,payload_json TEXT);
    CREATE TABLE trade_gate_evaluations(id INTEGER PRIMARY KEY,evaluated_at TEXT,decision_key TEXT,detail_json TEXT);
    """)
    feat={"momentum":"0.01","spread":"0.001","samples":8,"candidate":{"action":"BUY"},"hard_safety":{"x":True},
          "historical_candle_shadow":{"history":{"trend_20":"0.1","trend_50":"-0.1","avg_range":"0.02"},
          "candles_5m":{"momentum_3v15":"0.01","avg_range_5m":"0.005"}}}
    c.execute("INSERT INTO paper_decisions VALUES(?,?,?,?)",("d","2026-09-23T14:00:00+00:00","HOLD",json.dumps(feat)))
    q={"symbol":"GGAL","asset_class":"ACCIONES","settlement":"T1","currency":"ARS","market":"BYMA","bid":"99","ask":"100","bid_size":"10","ask_size":"10","observed_at":"2026-09-23T14:00:00+00:00"}
    payload={"quote_used":q,"inputs_used":{"iol":{"state":"READY"}}};raw=canon(payload)
    c.execute("INSERT INTO decision_evidence_snapshots VALUES(?,?,?)",("d",hashlib.sha256(raw.encode()).hexdigest(),raw))
    c.commit();c.close()
    r=build(p);h=r["by_day"]["2026-09-23"]["HOLD"]
    assert h["counts"]["CANDIDATE"]==1
    assert h["counts"]["HIST_TREND50"]==1
    assert h["counts"]["IOL"]==1
    assert h["counts"]["QUOTE_COMPLETE"]==1
    assert r["read_only"] is True
