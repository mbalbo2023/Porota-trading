import json,sqlite3
from rc6_candle_entry_diagnostic import build

def test_candle_point_in_time_deduplicates_and_excludes_future_known(tmp_path):
    p=tmp_path/"x.db";c=sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
    INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
    CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY,strategy_version TEXT,symbol TEXT,asset_class TEXT,settlement TEXT,currency TEXT,market TEXT,status TEXT,opened_at TEXT,net_pnl TEXT);
    CREATE TABLE paper_learning_samples(paper_id TEXT PRIMARY KEY,feature_timestamp TEXT);
    CREATE TABLE candle_series(series_id TEXT PRIMARY KEY,identity_json TEXT);
    CREATE TABLE candle_versions(series_id TEXT,bar_start TEXT,bar_end TEXT,known_at TEXT,body_json TEXT);
    """)
    c.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,?,?,?,?,?)",("p","v","GGAL","ACCIONES","T1","ARS","BYMA","CLOSED","2026-09-23T15:20:00+00:00","100"))
    c.execute("INSERT INTO paper_learning_samples VALUES(?,?)",("p","2026-09-23T15:20:00+00:00"))
    ident=json.dumps({"symbol":"GGAL","asset_class":"ACCIONES","market":"BYMA","currency":"ARS","settlement":"T1","resolution":"5m"})
    c.execute("INSERT INTO candle_series VALUES(?,?)",("s",ident))
    for i in range(15):
        start=f"2026-09-23T{14+i//12:02d}:{(i%12)*5:02d}:00+00:00"
        # use simple bar timestamps below as_of; exact spacing is not material to this unit test
        end=start
        body=json.dumps({"quality":"COMPLETE","synthetic":False,"close":100+i,"high":101+i,"low":99+i})
        c.execute("INSERT INTO candle_versions VALUES(?,?,?,?,?)",("s",start,end,"2026-09-23T15:00:00+00:00",body))
    # duplicate an existing bar with a later but still known revision
    c.execute("INSERT INTO candle_versions VALUES(?,?,?,?,?)",("s","2026-09-23T14:00:00+00:00","2026-09-23T14:00:00+00:00","2026-09-23T15:10:00+00:00",json.dumps({"quality":"COMPLETE","synthetic":False,"close":101,"high":102,"low":100})))
    # future-known revision must be excluded
    c.execute("INSERT INTO candle_versions VALUES(?,?,?,?,?)",("s","2026-09-23T14:00:00+00:00","2026-09-23T14:00:00+00:00","2026-09-23T16:00:00+00:00",json.dumps({"quality":"COMPLETE","synthetic":False,"close":999,"high":1000,"low":998})))
    c.commit();c.close()
    r=build(p)
    assert r["read_only"] is True
    assert r["coverage"]["closed_total"]==1
    assert r["coverage"]["momentum_ready"]==1
    assert r["rows"][0]["bars"]==15
