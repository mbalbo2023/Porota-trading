import inspect, sqlite3
from datetime import datetime, timezone
import rc6_fast_functional_health as fast
import rc6_full_db_integrity as full


def make_db(path, real_orders=0):
    c=sqlite3.connect(path)
    c.execute('CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,process_state TEXT,session_state TEXT,ppi_auth TEXT,real_orders_sent INTEGER,heartbeat_at TEXT,last_market_data_at TEXT)')
    c.execute('INSERT INTO observer_state VALUES(1,?,?,?,?,?,?,?)',('PRODUCTION_PAPER','RUNNING','MARKET_OPEN','OK',real_orders,'2026-09-07T18:00:00+00:00','2026-09-07T18:00:00+00:00'))
    c.commit(); c.close()


def test_fast_probe_is_green_and_declares_no_full_integrity(tmp_path,monkeypatch):
    p=str(tmp_path/'x.db'); make_db(p)
    monkeypatch.setattr(fast,'MODE','PRODUCTION_PAPER'); monkeypatch.setattr(fast,'EXECUTION','SIMULATED'); monkeypatch.setattr(fast,'REAL_ORDER_CAPABILITY','BLOCKED')
    r=fast.probe(p,now=datetime(2026,9,7,18,1,tzinfo=timezone.utc))
    assert r['status']=='GREEN' and r['full_integrity_check_performed'] is False
    assert r['checks']['real_orders_sent']


def test_fast_probe_source_contains_no_full_scan_pragma():
    src=inspect.getsource(fast.probe).lower()
    assert 'pragma quick_check' not in src
    assert 'pragma integrity_check' not in src


def test_fast_probe_red_on_real_orders(tmp_path,monkeypatch):
    p=str(tmp_path/'x.db'); make_db(p,1)
    monkeypatch.setattr(fast,'MODE','PRODUCTION_PAPER'); monkeypatch.setattr(fast,'EXECUTION','SIMULATED'); monkeypatch.setattr(fast,'REAL_ORDER_CAPABILITY','BLOCKED')
    r=fast.probe(p,now=datetime(2026,9,7,18,1,tzinfo=timezone.utc)); assert r['status']=='RED'


def test_full_probe_owns_quick_check(tmp_path):
    p=str(tmp_path/'x.db'); make_db(p)
    r=full.probe(p); assert r['quick_check']=='ok' and r['status']=='GREEN' and r['read_only']
    assert 'PRAGMA quick_check' in inspect.getsource(full.probe)
