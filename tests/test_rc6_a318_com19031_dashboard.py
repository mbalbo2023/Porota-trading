"""Offhost COM19031 historical-dashboard read-only PIT timing regressions."""
from datetime import datetime
import sqlite3

from rc6_trader_dashboard.projection import Projection, Store


def _page(db, at):
    with Store(db, now=datetime.fromisoformat(at)) as store:
        page = Projection(store).history()
        assert page.state == 'AVAILABLE'
        return {row['settlement']: row for row in page.rows}


def test_dashboard_history_freshness_is_segment_specific_with_publication_floor(tmp_path):
    db=tmp_path/'history.db'
    with sqlite3.connect(db) as c:
        c.execute('''CREATE TABLE history_canonical_v2(
            symbol TEXT,instrument_type TEXT,market TEXT,currency TEXT,
            settlement TEXT,price_basis TEXT,adjustment_basis TEXT,date TEXT,
            observed_at TEXT,source TEXT,quality_state TEXT)''')
        c.executemany('INSERT INTO history_canonical_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?)',[
            ('GGAL','ACCIONES','BYMA','ARS',settlement,'NOMINAL','RAW',
             '2026-10-30','2026-10-30T21:00:00+00:00','PPI','FULL_OHLC')
            for settlement in ('A-24HS','CI')])
    before=_page(db,'2026-11-02T17:30:00-03:00')
    assert before['A-24HS']['expected_last_session']=='2026-10-30'
    assert before['A-24HS']['freshness']=='LAST_SESSION_PRESENT'
    assert before['CI']['expected_last_session']=='2026-11-02'
    assert before['CI']['freshness']=='STALE'
    closing=_page(db,'2026-11-02T18:14:00-03:00')
    assert closing['A-24HS']['freshness']=='LAST_SESSION_PRESENT'
    postclose=_page(db,'2026-11-02T18:15:00-03:00')
    assert postclose['A-24HS']['freshness']=='STALE'
    previous=_page(db,'2026-10-30T17:30:00-03:00')
    assert previous['A-24HS']['expected_last_session']=='2026-10-30'
