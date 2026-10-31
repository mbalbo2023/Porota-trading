import sqlite3
from pathlib import Path

import rc6_validation_operational_daily as m
from cd_spot_ledger import ADMIN_CANCEL_AUTHORITY, ADMIN_CANCEL_REASON
from cd_spot_ledger import ADMIN_CANCEL_AUTHORITY_A207, cancel_open_paper_position
from test_production_paper_v1634 import partial_spot
from test_rc6_paper_deploy_preflight import pending_eod


def _db(path: Path):
    c=sqlite3.connect(path)
    c.executescript('''
    CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER,process_state TEXT,session_state TEXT,heartbeat_at TEXT);
    INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0,'WAITING_MARKET','MARKET_CLOSED','2026-09-11T00:00:00+00:00');
    CREATE TABLE paper_events(id INTEGER PRIMARY KEY,event_at TEXT,event_type TEXT,paper_id TEXT);
    CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY,opened_at TEXT,closed_at TEXT,status TEXT,net_pnl TEXT,currency TEXT);
    CREATE TABLE paper_decisions(id INTEGER PRIMARY KEY,decided_at TEXT);
    CREATE TABLE paper_fills(id INTEGER PRIMARY KEY,filled_at TEXT);
    ''')
    c.execute("INSERT INTO paper_events VALUES(1,'2026-09-10T18:00:00+00:00','CLOSE','P1')")
    c.execute("INSERT INTO paper_positions VALUES('P1','2026-09-10T14:00:00+00:00','2026-09-10T18:00:00+00:00','CLOSED','123.45','ARS')")
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
    assert out['days'][0]['realized_by_currency']=={'ARS':'123.45'}
    m.assert_read_only_contract()


def test_daily_result_never_combines_ars_and_usd(tmp_path):
    p=tmp_path/'paper.db'; _db(p)
    with sqlite3.connect(p) as c:
        c.execute("INSERT INTO paper_positions VALUES('P2','2026-09-10T14:00:00+00:00','2026-09-10T18:00:00+00:00','CLOSED','-5','USD')")
    result=m.collect(db_path=str(p))['days'][0]
    assert result['realized_by_currency']=={'ARS':'123.45','USD':'-5'}
    assert 'realized_net_pnl' not in result


def test_fail_closed_if_real_orders_nonzero(tmp_path):
    p=tmp_path/'paper.db'; _db(p)
    c=sqlite3.connect(p); c.execute('UPDATE observer_state SET real_orders_sent=1 WHERE id=1'); c.commit(); c.close()
    try:
        m.collect(db_path=str(p))
    except m.OperationalDailyEvidenceError as exc:
        assert 'PAPER_SAFETY_INVARIANT_FAILED' in str(exc)
    else:
        raise AssertionError('expected fail closed')


def test_missing_real_order_counter_is_not_zero(tmp_path):
    p=tmp_path/'paper.db'; _db(p)
    with sqlite3.connect(p) as c:
        c.execute('UPDATE observer_state SET real_orders_sent=NULL WHERE id=1')
    try:
        m.collect(db_path=str(p))
    except m.OperationalDailyEvidenceError as exc:
        assert 'PAPER_SAFETY_INVARIANT_FAILED' in str(exc)
    else:
        raise AssertionError('missing counter must fail closed')


def test_admin_cancellation_is_visible_without_a_sale_or_realized_pnl(tmp_path):
    p=tmp_path/'paper.db'; _db(p)
    with sqlite3.connect(p) as c:
        c.execute('CREATE TABLE paper_admin_cancellations(paper_id TEXT,authority TEXT,reason TEXT,cancelled_at TEXT)')
        c.execute("INSERT INTO paper_positions VALUES('SUPV','2026-10-19T18:51:58+00:00',"
                  "'2026-10-19T23:55:00+00:00','CANCELLED_ADMIN',NULL,'ARS')")
        c.execute('INSERT INTO paper_admin_cancellations VALUES(?,?,?,?)',
                  ('SUPV',ADMIN_CANCEL_AUTHORITY,ADMIN_CANCEL_REASON,'2026-10-19T23:55:00+00:00'))
        c.execute("INSERT INTO paper_events VALUES(2,'2026-10-19T23:55:00+00:00',"
                  "'PAPER_ADMIN_CANCELLED_NO_MARKET','SUPV')")
    by_day={r['date_ar']:r for r in m.collect(db_path=str(p))['days']}
    assert by_day['2026-10-19']['opened']==1
    assert by_day['2026-10-19']['cancelled_admin']==1
    assert by_day['2026-10-19']['closed']==0
    assert by_day['2026-10-19']['realized_by_currency']=={}
    assert by_day['2026-10-19']['event_types']['PAPER_ADMIN_CANCELLED_NO_MARKET']==1
    assert by_day['2026-09-10']['closed']==1


def test_missing_admin_receipt_cannot_hide_a_missing_sale(tmp_path):
    p=tmp_path/'paper.db'; _db(p)
    with sqlite3.connect(p) as c:
        c.execute("INSERT INTO paper_positions VALUES('X','2026-10-19T18:00:00+00:00',"
                  "'2026-10-19T23:00:00+00:00','CANCELLED_ADMIN',NULL,'ARS')")
    try:m.collect(db_path=str(p))
    except m.OperationalDailyEvidenceError as exc:
        assert 'ADMIN_CANCEL_RECEIPT_UNVERIFIED' in str(exc)
    else:raise AssertionError('missing administrative receipt must fail closed')


def test_a207_daily_accepts_full_reversal_but_rejects_forged_original(partial_spot):
    broker, p, quote, _ = partial_spot()
    pending_eod(broker, p)
    when = '2026-10-30T20:30:00-03:00'
    with broker.store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        cancel_open_paper_position(c, p['paper_id'], when,
                                   ADMIN_CANCEL_AUTHORITY_A207)
    result = m.collect(db_path=str(broker.store.path))
    assert sum(item['cancelled_admin'] for item in result['days']) == 1
    assert all(not item['realized_by_currency'] for item in result['days'])
    with broker.store.connect() as c:
        c.execute('UPDATE paper_fills SET costs=? WHERE paper_id=?',
                  ('9999', p['paper_id']))
    try:
        m.collect(db_path=str(broker.store.path))
    except m.OperationalDailyEvidenceError as exc:
        assert str(exc) == 'ADMIN_CANCEL_RECEIPT_UNVERIFIED'
    else:
        raise AssertionError('Tampered A207 BUY must not pass the read-only dashboard')


def test_unknown_closed_state_still_fails_closed(tmp_path):
    p=tmp_path/'paper.db'; _db(p)
    with sqlite3.connect(p) as c:
        c.execute("INSERT INTO paper_positions VALUES('X','2026-10-19T18:00:00+00:00',"
                  "'2026-10-19T23:00:00+00:00','UNRECOGNIZED',NULL,'ARS')")
    try:m.collect(db_path=str(p))
    except m.OperationalDailyEvidenceError as exc:
        assert 'UNKNOWN_POSITION_STATE_WITH_CLOSED_AT' in str(exc)
    else:raise AssertionError('unknown ledger state must fail closed')
