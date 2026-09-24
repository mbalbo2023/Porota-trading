import json,sqlite3
from rc6_baseline_replay_audit import build,recompute

def test_recompute_matches_formula():
    r=recompute({"momentum":"0.004","spread":"0.001","paper_threshold":"0.62"})
    assert r["status"]=="REPLAYED"
    assert r["score"]=="0.650"
    assert r["action"]=="BUY"

def test_replay_reads_only_and_matches(tmp_path):
    p=tmp_path/"r.db"; c=sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
    INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
    CREATE TABLE paper_decisions(id INTEGER PRIMARY KEY,decision_key TEXT,decided_at TEXT,symbol TEXT,action TEXT,score TEXT,reason TEXT,features_json TEXT);
    """)
    f=json.dumps({"momentum":"0.004","spread":"0.001","paper_threshold":"0.62"})
    c.execute("INSERT INTO paper_decisions VALUES(1,'d','2026-09-23T14:00:00+00:00','GGAL','BUY','0.650','ok',?)",(f,))
    c.commit();c.close()
    r=build(p)
    assert r["decisions_total"]==1 and r["replayable"]==1
    assert r["stored_action_matches"]==1 and r["stored_score_matches"]==1
    assert r["safety"]=={"mode":"PRODUCTION_PAPER","real_orders_sent":0}
