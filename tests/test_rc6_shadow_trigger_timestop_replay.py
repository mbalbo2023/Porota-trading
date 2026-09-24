import hashlib,json,sqlite3
from datetime import timedelta
from rc6_shadow_trigger_timestop_replay import build

def canon(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))

def test_trigger_selects_low_trend_candidate_and_stays_readonly(tmp_path,monkeypatch):
    p=tmp_path/"x.db";c=sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
    INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
    CREATE TABLE paper_decisions(id INTEGER PRIMARY KEY,decision_key TEXT,decided_at TEXT,symbol TEXT,action TEXT,score TEXT,features_json TEXT);
    CREATE TABLE decision_evidence_snapshots(decision_key TEXT PRIMARY KEY,captured_at TEXT,payload_sha256 TEXT,payload_json TEXT);
    CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,observed_at TEXT,symbol TEXT,asset_class TEXT,settlement TEXT,currency TEXT,market TEXT,bid TEXT,bid_size TEXT);
    """)
    for i,(sym,tr,cm,score) in enumerate((("A","-0.2","0.01","0.50"),("B","-0.3","-0.01","0.50")),1):
        feat={"spread":"0.001","historical_candle_shadow":{"history":{"trend_50":tr},"candles_5m":{"momentum_3v15":cm}}}
        dk=f"d{i}";at="2026-09-21T14:00:00+00:00"
        c.execute("INSERT INTO paper_decisions VALUES(?,?,?,?,?,?,?)",(i,dk,at,sym,"HOLD",score,json.dumps(feat)))
        q={"symbol":sym,"asset_class":"ACCIONES","settlement":"T1","currency":"ARS","market":"BYMA","ask":"100","ask_size":"10","observed_at":at}
        raw=canon({"decision_key":dk,"quote_used":q});h=hashlib.sha256(raw.encode()).hexdigest()
        c.execute("INSERT INTO decision_evidence_snapshots VALUES(?,?,?,?)",(dk,at,h,raw))
    c.execute("INSERT INTO market_snapshots VALUES(1,'2026-09-21T14:10:00+00:00','A','ACCIONES','T1','ARS','BYMA','101','10')")
    c.execute("INSERT INTO market_snapshots VALUES(2,'2026-09-21T14:10:00+00:00','B','ACCIONES','T1','ARS','BYMA','90','10')")
    c.commit();c.close()
    import rc6_shadow_trigger_timestop_replay as m
    monkeypatch.setattr(m,"rates",lambda a:(m.D("0"),m.D("0")))
    monkeypatch.setattr(m,"session_cutoff",lambda q,at,time_stop:at+timedelta(minutes=time_stop))
    r=build(p)
    x=r["results"]["CANDLE_MOM_POS__TARGET_0.0025__TSTOP_30__CAP_5"]
    assert x["all"]["n"]==1 and x["all"]["wins"]==1
    assert r["read_only"] and r["factual_writes"] is False
