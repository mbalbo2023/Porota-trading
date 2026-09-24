import json,sqlite3
from rc6_exit_grid_replay import build

def test_exit_grid_is_read_only_and_uses_persisted_rates(tmp_path):
    p=tmp_path/"x.db";c=sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
    INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
    CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY,strategy_version TEXT,symbol TEXT,asset_class TEXT,settlement TEXT,currency TEXT,market TEXT,status TEXT,quantity TEXT,entry_price TEXT,entry_cost TEXT,opened_at TEXT,closed_at TEXT,features_json TEXT);
    CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,source TEXT,observed_at TEXT,symbol TEXT,asset_class TEXT,settlement TEXT,currency TEXT,market TEXT,bid TEXT,bid_size TEXT);
    """)
    f=json.dumps({"contract_cash_multiplier":"1","economics":{"full_leg_rate":"0.007865","rebated_leg_rate":"0.000605"}})
    c.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
      ("p","paper-momentum-v17.0.0-rc6","GGAL","ACCIONES","T1","ARS","BYMA","CLOSED","10","100","7.87","2026-09-23T14:00:00+00:00","2026-09-23T15:00:00+00:00",f))
    c.execute("INSERT INTO market_snapshots VALUES(1,'PPI','2026-09-23T14:10:00+00:00','GGAL','ACCIONES','T1','ARS','BYMA','102','100')")
    c.commit();c.close()
    r=build(p)
    assert r["read_only"] is True and r["coverage"]["eligible"]==1
    x=r["grid"]["S0.02_T0.02"]["ARS"]
    assert x["n"]==1 and x["target_exits"]==1 and x["wins"]==1
