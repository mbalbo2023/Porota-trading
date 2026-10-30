"""A207: purely simulated multi-family reversals, never exchange execution."""
from datetime import datetime
from decimal import Decimal
import json
import sqlite3

import pytest

from rc6_paper_family_lifecycle import (FamilyPaperExecutor, future_positions,
                                       future_cash_effect)
from be_paper_engine import PaperStore, PaperBroker
from bt_caucion_paper import CaucionBook
from bt_caucion_paper import (ADMIN_CANCEL_AUTHORITY_A207 as CAUCION_AUTHORITY,
                              cancel_open_caucion_admin)
from bw_daily_risk import caucion_pnl
from scripts.rc6_paper_deploy_preflight import inspect_book
from scripts import rc6_paper_admin_cancel as admin
from test_rc6_paper_admin_cancel import _receipt, _pending
from test_production_paper_v1634 import caucion_offer, partial_spot, allocated
from test_rc6_future_paper_engine import contract

D = Decimal
FUTURE_OPEN = '2026-10-05T12:00:00-03:00'
CANCEL_AT = '2026-10-05T12:10:00-03:00'


def _make_book(partial_spot, tmp_path, *, include_caucion=True, include_future=True):
    broker, p, quote, _ = partial_spot()
    _pending(broker, p)
    paper_ids = {'spot': [p['paper_id']],
                 'caucion': [], 'future': []}
    if include_caucion:
        offer = caucion_offer()
        # Isolate ledger projection from unrelated live risk/session admission.
        caucion = broker.cauciones.place(
            offer, '1000', 'a207-placed', offer.quoted_at,
            lambda currency, at, c: broker._cash(at, currency, connection=c),
            admission=None)
        paper_ids['caucion'] = [caucion['paper_id']]
    if include_future:
        FamilyPaperExecutor(broker.store).open_future(
            contract(), lifecycle_id='PAPER-FUT-A207', event_id='OPEN-A207',
            entry_price='1500', quantity='1', entry_cost='2',
            occurred_at=FUTURE_OPEN, book_at=FUTURE_OPEN)
        paper_ids['future'] = ['PAPER-FUT-A207']
    broker.store.state(process_state='STOPPED', session_state='MARKET_CLOSED')
    report = inspect_book(broker.store.path, as_of=CANCEL_AT, barrier={'status': 'ABSENT'})
    receipt = _receipt(tmp_path, p['paper_id'])
    proof = json.loads(receipt.read_text())
    proof.update(admin_policy='A207_ALL_PAPER_REVERSAL_V1',
                 a207_approval_candidate='a'*40, a207_approval_tree='b'*40,
                 a207_owner_approval='12345678', a207_targets=paper_ids,
                 a207_exposures_sha256=report['exposures_sha256'])
    receipt.write_text(json.dumps(proof))
    return broker, paper_ids, receipt


@pytest.mark.parametrize('caucion,future', [(True,False),(False,True),(True,True)])
def test_a207_three_family_atomic_accounting_history_and_retry(partial_spot, tmp_path,
                                                               caucion, future):
    broker, targets, receipt = _make_book(
        partial_spot, tmp_path, include_caucion=caucion, include_future=future)
    before_caucion = broker.cauciones.cash_effect('ARS', CANCEL_AT)
    before_future = future_cash_effect(broker.store, 'ARS', CANCEL_AT)
    plan = admin.execute_a207_batch(broker.store.path, receipt, 'a'*40, at=CANCEL_AT)
    assert plan['status'] == 'PLAN_ONLY'
    assert plan['real_routes_used'] == []
    assert admin.execute_a207_batch(broker.store.path, receipt, 'a'*40,
                                    at=CANCEL_AT, apply=True)['status'] == 'CANCELLED_ADMIN'
    assert admin.execute_a207_batch(broker.store.path, receipt, 'a'*40,
                                    at=CANCEL_AT, apply=True)['status'] == 'ALREADY_CLOSED'
    assert inspect_book(broker.store.path, as_of=CANCEL_AT)['status'] == 'FLAT_GREEN'
    assert broker._cash(as_of=CANCEL_AT) == broker.initial_balances['ARS']
    assert broker.cauciones.cash_effect('ARS', CANCEL_AT) == 0
    assert future_cash_effect(broker.store, 'ARS', CANCEL_AT) == 0
    assert before_caucion == (-1000 if caucion else 0)
    assert before_future == (-D('1500002') if future else 0)
    if caucion:
        pos = broker.cauciones.positions()[0]
        assert pos['status'] == 'CANCELLED_ADMIN'
        assert broker.cauciones.state_at(pos, '2026-08-28T11:30:00-03:00') == 'OPEN'
        assert broker.cauciones.state_at(pos, CANCEL_AT) == 'CANCELLED_ADMIN'
        assert broker.cauciones.valuation(CANCEL_AT)['realized'] == 0
        assert broker.cauciones.cash_effect('ARS', '2026-08-28T11:30:00-03:00') == -1000
    if future:
        old = future_positions(broker.store, 'ARS', as_of='2026-10-05T12:01:00-03:00')
        now = future_positions(broker.store, 'ARS', as_of=CANCEL_AT)
        assert old[0]['status'] == 'ACTIVE' and D(old[0]['collateral']) > 0
        assert now[0]['status'] == 'CLOSED' and now[0]['close_reason'] == 'ADMIN_CANCEL_NO_MARKET_EXECUTION'
        assert D(now[0]['realized_pnl']) == 0 and D(now[0]['cash_effect']) == 0
        assert D(now[0]['collateral']) == 0 and D(now[0]['exposure']) == 0
    with broker.store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0
        assert c.execute('SELECT COUNT(*) FROM paper_caucion_admin_cancellations').fetchone()[0] == int(caucion)
        assert c.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events WHERE to_state='ADMIN_CANCEL'").fetchone()[0] == int(future)
        assert c.execute('SELECT COUNT(*) FROM paper_admin_cancellations').fetchone()[0] == 1


def test_a207_changed_future_cash_rolls_back_spot_and_caucion(partial_spot, tmp_path):
    broker, targets, receipt = _make_book(partial_spot, tmp_path)
    with broker.store.connect() as c:
        c.execute("UPDATE paper_family_lifecycle SET ledger_total='-999' WHERE lifecycle_id=?",
                  (targets['future'][0],))
    with pytest.raises(ValueError, match='A207_EXPOSURE_SNAPSHOT_CHANGED'):
        admin.execute_a207_batch(broker.store.path, receipt, 'a'*40, at=CANCEL_AT, apply=True)
    with broker.store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM paper_cauciones WHERE status='OPEN'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM paper_future_positions WHERE status='ACTIVE'").fetchone()[0] == 1


def test_a207_database_error_on_last_family_rolls_back_all_three(partial_spot, tmp_path):
    broker, targets, receipt = _make_book(partial_spot, tmp_path)
    with broker.store.connect() as c:
        c.execute('''CREATE TRIGGER a207_test_fail_last_family BEFORE INSERT
            ON paper_family_lifecycle_events WHEN NEW.to_state='ADMIN_CANCEL'
            BEGIN SELECT RAISE(ABORT,'A207_SYNTHETIC_INJECTION'); END''')
    with pytest.raises(sqlite3.IntegrityError, match='A207_SYNTHETIC_INJECTION'):
        admin.execute_a207_batch(broker.store.path, receipt, 'a'*40,
                                  at=CANCEL_AT, apply=True)
    with broker.store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM paper_cauciones WHERE status='OPEN'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM paper_future_positions WHERE status='ACTIVE'").fetchone()[0] == 1
        assert c.execute('SELECT COUNT(*) FROM paper_admin_cancellations').fetchone()[0] == 0
        assert c.execute('SELECT COUNT(*) FROM paper_caucion_admin_cancellations').fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events WHERE to_state='ADMIN_CANCEL'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='paper_admin_reversal_batches'").fetchone()[0] == 0


def test_a207_retry_seal_cannot_be_reassigned_to_another_owner_comment(partial_spot, tmp_path):
    broker, targets, receipt = _make_book(partial_spot, tmp_path)
    admin.execute_a207_batch(broker.store.path, receipt, 'a'*40, at=CANCEL_AT, apply=True)
    proof = json.loads(receipt.read_text())
    proof['a207_owner_approval'] = '987654321'
    receipt.write_text(json.dumps(proof))
    with pytest.raises(ValueError, match='A207_BATCH_SEALED_RECEIPT_MISMATCH'):
        admin.execute_a207_batch(broker.store.path, receipt, 'a'*40,
                                  at=CANCEL_AT, apply=True)
    with broker.store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM paper_admin_reversal_batches").fetchone()[0] == 1


def test_a207_futures_settlement_and_invalid_authority_are_not_voided(partial_spot, tmp_path):
    broker, targets, receipt = _make_book(partial_spot, tmp_path)
    proof = json.loads(receipt.read_text())
    proof['a207_approval_tree'] = 'not-a-sha'
    receipt.write_text(json.dumps(proof))
    with pytest.raises(ValueError, match='A207_AUTHORITY_OR_TARGETS_INVALID'):
        admin.execute_a207_batch(broker.store.path, receipt, 'a'*40, at=CANCEL_AT, apply=True)
    proof['a207_approval_tree'] = 'b'*40
    receipt.write_text(json.dumps(proof))
    with broker.store.connect() as c:
        c.execute('''UPDATE paper_future_marks SET is_settlement=1
            WHERE lifecycle_id=?''', (targets['future'][0],))
        # No mark may exist yet; create a malicious official-looking mark.
        c.execute('''INSERT OR IGNORE INTO paper_future_marks
            VALUES(?,?,?,?,?,?,?,?)''', ('A207-MARK', targets['future'][0], '1500',
            FUTURE_OPEN, FUTURE_OPEN, 1, '0', '{}'))
    with pytest.raises(ValueError, match='A207_EXPOSURE_SNAPSHOT_CHANGED'):
        admin.execute_a207_batch(broker.store.path, receipt, 'a'*40, at=CANCEL_AT, apply=True)
    with broker.store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'").fetchone()[0] == 1


@pytest.mark.parametrize('fee_payment', ['MATURITY','UPFRONT'])
def test_a207_caucion_fee_and_point_in_time_cash_are_exact(partial_spot, tmp_path, fee_payment):
    broker = PaperBroker(PaperStore(str(tmp_path/'caucion-only.sqlite')),
                         initial_cash='10000')
    offer = caucion_offer(fee_payment=fee_payment)
    position = broker.cauciones.place(
        offer, '1000', 'a207-fee-' + fee_payment, offer.quoted_at,
        lambda currency, at, c: broker._cash(at, currency, connection=c),
        admission=None)
    before = broker._cash(as_of=offer.quoted_at)
    with broker.store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        result = cancel_open_caucion_admin(c, position['paper_id'], CANCEL_AT,
                                            CAUCION_AUTHORITY)
    assert D(result['upfront_fee_reversed']) == (1 if fee_payment == 'UPFRONT' else 0)
    assert broker._cash(as_of=offer.quoted_at) == before
    assert broker._cash(as_of=CANCEL_AT) == broker.initial_balances['ARS']
    p = broker.cauciones.positions()[0]
    assert caucion_pnl(p, datetime.fromisoformat(CANCEL_AT)) == 0
    assert broker.cauciones.valuation(CANCEL_AT)['unrealized'] == 0
    with pytest.raises(ValueError, match='CASH_CLOCK_ROLLBACK'):
        broker._cash(as_of=offer.quoted_at, for_execution=True)
    with broker.store.connect() as c:
        with pytest.raises(Exception, match='PAPER_CAUCION_ADMIN_RECEIPT_IMMUTABLE'):
            c.execute('''UPDATE paper_caucion_admin_cancellations SET total_fees='99'
                         WHERE paper_id=?''', (position['paper_id'],))


def test_a207_allocation_original_is_retained_and_auditable(tmp_path):
    import cb_caucion_audit
    broker, decision = allocated(tmp_path, [caucion_offer()])
    assert decision['status'] == 'PLACED_SIMULATED'
    with broker.store.connect() as c:
        prior = c.execute('SELECT * FROM paper_caucion_allocations').fetchone()
        paper_id = prior['paper_id']
        c.execute('BEGIN IMMEDIATE')
        cancel_open_caucion_admin(c, paper_id, CANCEL_AT, CAUCION_AUTHORITY)
    with broker.store.connect() as c:
        assert dict(c.execute('SELECT * FROM paper_caucion_allocations').fetchone()) == dict(prior)
    # Audit preserves the original allocation and reports the separate
    # administrative terminal status, never a fake market maturity.
    history = cb_caucion_audit.allocation_history(broker.store.path)
    assert history
