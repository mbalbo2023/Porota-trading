import sqlite3
from pathlib import Path

import rc6_validation_operational_daily as m


def _db(path: Path):
    c=sqlite3.connect(path)
    c.executescript('''
    CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER,process_state TEXT,session_state TEXT,heartbeat_at TEXT);
    INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0,'WAITING_MARKET','MARKET_CLOSED','2026-09-11T00:00:00+00:00');
    CREATE TABLE paper_events(id INTEGER PRIMARY KEY,event_at TEXT,event_type TEXT,paper_id TEXT);
    CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY,opened_at TEXT,closed_at TEXT,status TEXT,net_pnl TEXT);
    CREATE TABLE paper_decisions(id INTEGER PRIMARY KEY,decided_at TEXT);
    CREATE TABLE paper_fills(id INTEGER PRIMARY KEY,filled_at TEXT);
    ''')
    c.execute("INSERT INTO paper_events VALUES(1,'2026-09-10T18:00:00+00:00','CLOSE','P1')")
    c.execute("INSERT INTO paper_positions VALUES('P1','2026-09-10T14:00:00+00:00','2026-09-10T18:00:00+00:00','CLOSED','123.45')")
    c.execute("INSERT INTO paper_decisions VALUES(1,'2026-09-10T13:00:00+00:00')")
    c.execute("INSERT INTO paper_fills VALUES(1,'2026-09-10T18:00:00+00:00')")
    c.commit(); c.close()


def test_collect_is_read_only_and_groups_argentina_day(tmp_path):
    p=tmp_path/'paper.db'; _db(p)
    out=m.collect(db_path=str(p),limit_days=10)
    assert out['mode']=='PRODUCTION_PAPER' and out['real_orders_sent']==0
    assert out['days'][0]['date_ar']=='2026-09-10'
    assert out['days'][0]['closed']==1
    assert out['days'][0]['fills']==1
    assert out['days'][0]['decisions']==1
    assert out['days'][0]['realized_net_pnl']=='123.45'
    m.assert_read_only_contract()


def test_fail_closed_if_real_orders_nonzero(tmp_path):
    p=tmp_path/'paper.db'; _db(p)
    c=sqlite3.connect(p); c.execute('UPDATE observer_state SET real_orders_sent=1 WHERE id=1'); c.commit(); c.close()
    try:
        m.collect(db_path=str(p))
    except m.OperationalDailyEvidenceError as exc:
        assert 'PAPER_SAFETY_INVARIANT_FAILED' in str(exc)
    else:
        raise AssertionError('expected fail closed')
