"""A205: read-only host book diagnostics, not a simulated deployment."""

from datetime import datetime, timedelta
import sqlite3

import pytest

from scripts.rc6_paper_deploy_preflight import inspect_book, UnsafeBook
import cd_spot_ledger as spot
from test_production_paper_v1634 import partial_spot


def pending_eod(broker, position):
    with broker.store.connect() as c:
        c.execute('''INSERT INTO paper_exit_intents VALUES(?,?,?,?,?,?,?)
          ON CONFLICT(paper_id) DO UPDATE SET state=excluded.state,cause=excluded.cause,
          blocked_reason=excluded.blocked_reason''',
                  (position['paper_id'], 'EXIT_PENDING_MARKET_CLOSED', 'EOD_PAPER',
                   position['opened_at'], 'OUTSIDE_PAPER_EXECUTION_WINDOW',
                   position['opened_at'], 0))


def fixture_book(partial_spot):
    broker, p, quote, sell = partial_spot()
    broker.store.state(mode='PRODUCTION_PAPER', process_state='WAITING_MARKET',
                       session_state='MARKET_CLOSED')
    now = (datetime.fromisoformat(quote.observed_at) + timedelta(days=1)).isoformat()
    return broker, p, sell, now


def test_readonly_spot_classifies_valid_eod_void_and_is_stable(partial_spot):
    broker, p, _, now = fixture_book(partial_spot)
    pending_eod(broker, p)
    a = inspect_book(broker.store.path, as_of=now, barrier={'status': 'ABSENT'})
    b = inspect_book(broker.store.path, as_of=now, barrier={'status': 'ABSENT'})
    assert a == b
    assert a['status'] == 'ADMIN_CLOSE_REQUIRED'
    assert a['read_only'] is True and a['host_mutations'] == 0
    assert a['exposures'] == {'spot': 1, 'caucion': 0, 'future': 0}
    assert a['plans']['spot'] == {'admin_void_eod_intact': 1, 'unsupported': 0}
    assert len(a['snapshot_sha256']) == 64
    assert p['paper_id'] not in str(a)  # public Actions never emit raw identities
    with broker.store.connect() as c:
        assert c.execute('SELECT status FROM paper_positions').fetchone()[0] == 'OPEN'


def test_new_authority_performs_atomic_spot_void_without_sale(partial_spot):
    broker, p, _, now = fixture_book(partial_spot)
    pending_eod(broker, p)
    assert inspect_book(broker.store.path, as_of=now)['status'] == 'ADMIN_CLOSE_REQUIRED'
    with broker.store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        spot.cancel_open_paper_position(c, p['paper_id'], now,
                                        spot.ADMIN_CANCEL_AUTHORITY_A205)
    assert inspect_book(broker.store.path, as_of=now)['status'] == 'FLAT_GREEN'
    with broker.store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0
        assert c.execute('SELECT authority FROM paper_admin_cancellations').fetchone()[0] == spot.ADMIN_CANCEL_AUTHORITY_A205
        assert spot.positions_at(c, now) == ([], [])
    assert broker._cash(as_of=now) == broker.initial_balances['ARS']
    with broker.store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError, match='no está abierta'):
            spot.cancel_open_paper_position(c, p['paper_id'], now,
                                            spot.ADMIN_CANCEL_AUTHORITY_A205)
        c.rollback()


def test_partially_sold_spot_requires_economic_exit(partial_spot):
    broker, p, sell, now = fixture_book(partial_spot)
    assert broker._close(p, sell(), 'PARTIAL')
    pending_eod(broker, p)
    report = inspect_book(broker.store.path, as_of=now)
    assert report['status'] == 'BLOCKED_RECONCILIATION'
    assert report['plans']['spot']['unsupported'] == 1
    assert 'SPOT_REQUIRES_VALID_EXIT_OR_LEDGER_REVIEW' in report['reasons']


def test_active_future_is_not_faked_closed(partial_spot):
    broker, p, _, now = fixture_book(partial_spot)
    pending_eod(broker, p)
    with broker.store.connect() as c:
        c.execute('DROP TABLE paper_future_positions')
        c.execute('CREATE TABLE paper_future_positions(status TEXT)')
        c.execute("INSERT INTO paper_future_positions VALUES('ACTIVE')")
    report = inspect_book(broker.store.path, as_of=now)
    assert report['status'] == 'BLOCKED_RECONCILIATION'
    assert report['exposures']['future'] == 1
    assert report['plans']['future']['needs_pit_fill_or_settlement'] == 1
    with broker.store.connect() as c:
        assert c.execute('SELECT status FROM paper_future_positions').fetchone()[0] == 'ACTIVE'


def test_missing_family_table_and_real_orders_are_red(partial_spot):
    broker, _, _, now = fixture_book(partial_spot)
    with broker.store.connect() as c:
        c.execute('DROP TABLE paper_cauciones')
    with pytest.raises(UnsafeBook, match='BOOK_SCHEMA_MISSING_CAUCION'):
        inspect_book(broker.store.path, as_of=now)
    with broker.store.connect() as c:
        c.execute('UPDATE observer_state SET real_orders_sent=1 WHERE id=1')
    with pytest.raises(UnsafeBook, match='BOOK_PAPER_ZERO_UNVERIFIED'):
        inspect_book(broker.store.path, as_of=now)


def test_uncertain_barrier_is_visible_not_silent(partial_spot):
    broker, _, _, now = fixture_book(partial_spot)
    info = inspect_book(broker.store.path, as_of=now, barrier={'status': 'INFLIGHT'})
    assert info['barrier_status'] == 'INFLIGHT'
    with pytest.raises(UnsafeBook, match='BOOK_BARRIER_UNKNOWN'):
        inspect_book(broker.store.path, as_of=now, barrier={'status': 'DESTROYED'})
