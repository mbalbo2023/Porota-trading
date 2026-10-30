"""A205: read-only host book diagnostics, not a simulated deployment."""

from datetime import datetime, timedelta
from pathlib import Path
import sqlite3

import pytest

from scripts.rc6_paper_deploy_preflight import inspect_book, UnsafeBook
from scripts import rc6_paper_admin_cancel as admin
import cd_spot_ledger as spot
from test_production_paper_v1634 import partial_spot
from test_rc6_paper_admin_cancel import _receipt


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
    assert len(a['exposures_sha256']) == 64
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
    with pytest.raises(UnsafeBook, match='BOOK_ACTIVE_SCHEMA_MISSING_FUTURE'):
        inspect_book(broker.store.path, as_of=now)
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


def batch_receipt(tmp_path, broker, p, now):
    info = inspect_book(broker.store.path, as_of=now)
    receipt = _receipt(tmp_path, p['paper_id'])
    import json
    proof = json.loads(receipt.read_text())
    proof.update(admin_policy='A205_EOD_SPOT_BATCH_V1',
                 a205_owner_approval='12345678',
                 a205_approval_candidate='a' * 40,
                 admin_cancel_paper_ids=[p['paper_id']],
                 a205_exposures_sha256=info['exposures_sha256'])
    receipt.write_text(json.dumps(proof))
    return receipt


def clone_spot_entry(broker, p):
    """Two synthetic, fully purchased positions to test atomic batch order."""
    second = p['paper_id'] + '-duplicate-fixture'
    with broker.store.connect() as c:
        for table in ('paper_positions', 'paper_fills', 'paper_exit_intents'):
            row = dict(c.execute('SELECT * FROM ' + table + ' WHERE paper_id=?',
                                 (p['paper_id'],)).fetchone())
            row['paper_id'] = second
            if table == 'paper_positions':
                row['symbol'] = 'GGAL'
            if table == 'paper_fills':
                row['id'] = None
            names = list(row)
            c.execute('INSERT INTO ' + table + ' (' + ','.join(names) + ') VALUES (' +
                      ','.join('?' for _ in names) + ')', list(row.values()))
    return second


def test_a205_closed_runtime_batch_plan_apply_and_retry_are_idempotent(partial_spot, tmp_path):
    broker, p, _, now = fixture_book(partial_spot)
    pending_eod(broker, p)
    broker.store.state(process_state='STOPPED')
    receipt = batch_receipt(tmp_path, broker, p, now)
    before = admin.execute_a205_batch(broker.store.path, receipt, 'a' * 40, at=now)
    assert before['status'] == 'PLAN_ONLY'
    with broker.store.connect() as c:
        assert c.execute('SELECT status FROM paper_positions').fetchone()[0] == 'OPEN'
    after = admin.execute_a205_batch(broker.store.path, receipt, 'a' * 40,
                                     apply=True, at=now)
    assert after['status'] == 'CANCELLED_ADMIN' and after['position_count'] == 1
    assert admin.execute_a205_batch(broker.store.path, receipt, 'a' * 40,
                                    apply=True, at=now)['status'] == 'ALREADY_CLOSED'
    assert broker._cash(as_of=now) == broker.initial_balances['ARS']
    with broker.store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM paper_admin_cancellations').fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM paper_events WHERE event_type='PAPER_ADMIN_CANCELLED_NO_MARKET'").fetchone()[0] == 1


def test_a205_two_spots_commit_together_and_rollback_together(partial_spot, tmp_path):
    broker, p, _, now = fixture_book(partial_spot)
    pending_eod(broker, p)
    broker.store.state(process_state='STOPPED')
    second = clone_spot_entry(broker, p)
    report = inspect_book(broker.store.path, as_of=now)
    assert report['plans']['spot']['admin_void_eod_intact'] == 2
    receipt = batch_receipt(tmp_path, broker, p, now)
    import json
    proof = json.loads(receipt.read_text())
    proof['admin_cancel_paper_ids'] = [p['paper_id'], second]
    receipt.write_text(json.dumps(proof))
    # The second identity changes after the snapshot: no partial cancellation.
    with broker.store.connect() as c:
        c.execute('UPDATE paper_fills SET quantity=? WHERE paper_id=?', ('8', second))
    with pytest.raises(ValueError, match='A205_EXPOSURE_SNAPSHOT_CHANGED'):
        admin.execute_a205_batch(broker.store.path, receipt, 'a' * 40, apply=True, at=now)
    with broker.store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'").fetchone()[0] == 2
        assert c.execute('SELECT COUNT(*) FROM paper_admin_cancellations').fetchone()[0] == 0
        c.execute('UPDATE paper_fills SET quantity=? WHERE paper_id=?', (p['quantity'],second))
    # Now the original snapshot is exact and both entries close atomically.
    result = admin.execute_a205_batch(broker.store.path, receipt, 'a' * 40,
                                      apply=True, at=now)
    assert result['status'] == 'CANCELLED_ADMIN' and result['position_count'] == 2
    with broker.store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM paper_admin_cancellations').fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'").fetchone()[0] == 0


def test_a205_batch_rejects_false_receipt_and_partial_spot(partial_spot, tmp_path):
    broker, p, sell, now = fixture_book(partial_spot)
    pending_eod(broker, p)
    broker.store.state(process_state='STOPPED')
    receipt = batch_receipt(tmp_path, broker, p, now)
    import json
    proof = json.loads(receipt.read_text())
    proof['a205_owner_approval'] = None
    receipt.write_text(json.dumps(proof))
    with pytest.raises(ValueError, match='A205_AUTHORITY_OR_PLAN_INVALID'):
        admin.execute_a205_batch(broker.store.path, receipt, 'a' * 40, apply=True, at=now)
    proof['a205_owner_approval'] = '12345678'
    receipt.write_text(json.dumps(proof))
    assert broker._close(p, sell(), 'PARTIAL')
    with pytest.raises(ValueError, match='A205_EXPOSURE_SNAPSHOT_CHANGED'):
        admin.execute_a205_batch(broker.store.path, receipt, 'a' * 40, apply=True, at=now)
    with broker.store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM paper_admin_cancellations').fetchone()[0] == 0


def test_a205_batch_never_voids_future_margin(partial_spot, tmp_path):
    broker, p, _, now = fixture_book(partial_spot)
    pending_eod(broker, p)
    broker.store.state(process_state='STOPPED')
    receipt = batch_receipt(tmp_path, broker, p, now)
    with broker.store.connect() as c:
        c.execute('DROP TABLE paper_future_positions')
        c.execute('CREATE TABLE paper_future_positions(status TEXT)')
        c.execute("INSERT INTO paper_future_positions VALUES('ACTIVE')")
    with pytest.raises(ValueError, match='BOOK_ACTIVE_SCHEMA_MISSING_FUTURE'):
        admin.execute_a205_batch(broker.store.path, receipt, 'a' * 40, apply=True, at=now)
    with broker.store.connect() as c:
        assert c.execute('SELECT status FROM paper_positions').fetchone()[0] == 'OPEN'


def test_a205_cli_dispatch_requires_exact_receipt_and_is_plan_only_by_default(
        partial_spot, tmp_path, monkeypatch, capsys):
    broker, p, _, now = fixture_book(partial_spot)
    pending_eod(broker, p)
    broker.store.state(process_state='STOPPED')
    old_receipt = batch_receipt(tmp_path, broker, p, now)
    exact = tmp_path / ('quiescence-' + 'a' * 40 + '.json')
    old_receipt.rename(exact)
    monkeypatch.setattr(admin, 'DATABASE', admin.Path(broker.store.path).resolve())
    monkeypatch.setattr(admin, 'RECEIPT_ROOT', tmp_path)
    assert admin.main(['--candidate-sha', 'a' * 40, '--receipt', str(exact),
                       '--a205-batch']) == 0
    assert 'PLAN_ONLY' in capsys.readouterr().out
    with broker.store.connect() as c:
        assert c.execute('SELECT status FROM paper_positions').fetchone()[0] == 'OPEN'


def test_a205_readonly_action_cannot_become_a_stealth_deploy():
    root = Path(__file__).resolve().parents[1]
    ro = (root / '.github/workflows/rc6-a205-paper-book-readonly.yml').read_text()
    deploy = (root / '.github/workflows/porota-deploy-v2-promote.yml').read_text()
    assert "rc6-a205-paper-book-ro-v1-*" in ro
    assert 'github.actor == \'mbalbo2023\'' in ro
    assert 'PYTHONDONTWRITEBYTECODE=1' in ro and 'python3 -B' in ro
    assert '--host-barrier' in ro and 'mode=ro' in (
        root / 'scripts/rc6_paper_deploy_preflight.py').read_text()
    assert 'BLOCKED_RECONCILIATION' in ro
    assert 'RC6_A205_PROMOTION_READY=NO' in ro
    assert 'rc6-unified-paper-deploy' in deploy
    assert 'RC6_A205_BOOK_OPEN_COUNTS=SPOT:' in deploy
    assert "if str(exc) != 'QUIESCENCE_OPEN_POSITION'" in deploy
    assert 'QUIESCENCE_OPEN_POSITION' in (
        root / 'scripts/rc6_deploy_quiescence.py').read_text()
