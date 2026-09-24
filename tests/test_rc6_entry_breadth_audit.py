import sqlite3
from rc6_entry_breadth_audit import build

def test_breadth_uses_only_same_day_past_trade_snapshots(tmp_path):
    p=tmp_path/"x.db";c=sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
    INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
    CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY,symbol TEXT,asset_class TEXT,currency TEXT,status TEXT,opened_at TEXT,net_pnl TEXT);
    CREATE TABLE paper_learning_samples(paper_id TEXT PRIMARY KEY,feature_timestamp TEXT);
    CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,symbol TEXT,asset_class TEXT,currency TEXT,last TEXT,last_kind TEXT,observed_at TEXT,trade_at TEXT);
    INSERT INTO paper_positions VALUES('p','GGAL','ACCIONES','ARS','CLOSED','2026-09-23T15:00:00+00:00','-10');
    INSERT INTO paper_learning_samples VALUES('p','2026-09-23T15:00:00+00:00');
    """)
    i=1
    for sym,a,b in [('A',100,90),('B',100,90),('C',100,90),('D',100,110)]:
        c.execute("INSERT INTO market_snapshots VALUES(?,?,?,?,?,?,?,?)",(i,sym,"ACCIONES","ARS",str(a),"TRADE","2026-09-23T14:00:00+00:00","2026-09-23T14:00:00+00:00"));i+=1
        c.execute("INSERT INTO market_snapshots VALUES(?,?,?,?,?,?,?,?)",(i,sym,"ACCIONES","ARS",str(b),"TRADE","2026-09-23T14:50:00+00:00","2026-09-23T14:50:00+00:00"));i+=1
    # future observation must be ignored
    c.execute("INSERT INTO market_snapshots VALUES(?,?,?,?,?,?,?,?)",(i,"D","ACCIONES","ARS","50","TRADE","2026-09-23T16:00:00+00:00","2026-09-23T16:00:00+00:00"))
    c.commit();c.close()
    r=build(p)
    assert r["coverage"]["covered"]==1
    assert r["rows"][0]["breadth"]["state"]=="BEARISH_BREADTH"
    assert r["rows"][0]["breadth"]["falling"]==3
