import json,sqlite3
from rc6_point_in_time_entry_features import build

def test_point_in_time_audit_never_uses_future_versions(tmp_path):
    p=tmp_path/"x.db";c=sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
    INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
    CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY,symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,status TEXT,opened_at TEXT,net_pnl TEXT);
    CREATE TABLE candle_series(series_id INTEGER PRIMARY KEY,identity_json TEXT);
    CREATE TABLE candle_versions(id INTEGER PRIMARY KEY,series_id INTEGER,body_json TEXT,known_at TEXT,bar_start TEXT,bar_end TEXT);
    CREATE TABLE production_history(symbol TEXT,instrument_type TEXT,settlement TEXT,payload_json TEXT,downloaded_at TEXT);
    """)
    c.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,?,?,?,?)",("p","GGAL","ACCIONES","BYMA","ARS","T1","CLOSED","2026-09-23T15:00:00+00:00","10"))
    ident=json.dumps({"symbol":"GGAL","asset_class":"ACCIONES","market":"BYMA","currency":"ARS","settlement":"T1","resolution":"5m"})
    c.execute("INSERT INTO candle_series VALUES(1,?)",(ident,))
    for i in range(22):
        hh=13+i//12;mm=(i%12)*5
        end=f"2026-09-23T{hh:02d}:{mm:02d}:00+00:00"
        b=json.dumps({"quality":"COMPLETE","synthetic":False,"open":100+i,"high":101+i,"low":99+i,"close":100+i,"volume":10})
        c.execute("INSERT INTO candle_versions VALUES(?,?,?,?,?,?)",(i+1,1,b,end,end,end))
    future=json.dumps({"quality":"COMPLETE","synthetic":False,"open":500,"high":501,"low":499,"close":500,"volume":10})
    c.execute("INSERT INTO candle_versions VALUES(99,1,?,?,?,?)",(future,"2026-09-23T16:00:00+00:00","2026-09-23T15:55:00+00:00","2026-09-23T16:00:00+00:00"))
    c.commit();c.close()
    r=build(p)
    assert r["read_only"] is True and r["coverage"]["closed_total"]==1
    assert int(r["rows"][0]["candle"]["n"])==22
