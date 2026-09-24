import json,sqlite3
from rc6_entry_context_audit import build
def test_context_uses_only_pre_entry_snapshots(tmp_path):
    p=tmp_path/"x.db";c=sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
    INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
    CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY,symbol TEXT,asset_class TEXT,settlement TEXT,currency TEXT,market TEXT,status TEXT,opened_at TEXT,net_pnl TEXT);
    CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,observed_at TEXT,trade_at TEXT,symbol TEXT,asset_class TEXT,settlement TEXT,currency TEXT,market TEXT,last_kind TEXT,last TEXT,bid TEXT,ask TEXT,bid_size TEXT,ask_size TEXT,book_at TEXT,source TEXT);
    """)
    c.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,?,?,?,?)",("p","GGAL","ACCIONES","T1","ARS","BYMA","CLOSED","2026-09-23T15:00:00+00:00","10"))
    for i,(at,last,bid,ask,bs,ass) in enumerate([("2026-09-23T14:00:00+00:00","100","99","101","20","10"),("2026-09-23T14:59:30+00:00","102","101","103","30","10"),("2026-09-23T15:01:00+00:00","500","499","501","1","100")],1):
      c.execute("INSERT INTO market_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(i,at,at,"GGAL","ACCIONES","T1","ARS","BYMA","TRADE",last,bid,ask,bs,ass,at,"PPI"))
    c.commit();c.close()
    r=build(p);row=r["rows"][0]
    assert r["read_only"] is True
    assert row["book"]["observed_at"]=="2026-09-23T14:59:30+00:00"
    assert row["asset_day_return"]!="3.9"
