"""An operator may void an unfilled PAPER simulation without inventing a sale."""
from datetime import datetime, timedelta
import json
import sqlite3
import pytest

import cd_spot_ledger as ledger
from scripts import rc6_paper_admin_cancel as admin
from scripts import rc6_deploy_quiescence as quiescence
from test_production_paper_v1634 import partial_spot


AUTHORITY = 'ISSUE624_OPERATOR_REQUEST_20261019'


def _pending(broker, position):
    with broker.store.connect() as c:
        c.execute('''INSERT INTO paper_exit_intents VALUES(?,?,?,?,?,?,?)
          ON CONFLICT(paper_id) DO UPDATE SET state=excluded.state,cause=excluded.cause,
          blocked_reason=excluded.blocked_reason''',
                  (position['paper_id'], 'EXIT_PENDING_MARKET_CLOSED', 'EOD_PAPER',
                   position['opened_at'], 'OUTSIDE_PAPER_EXECUTION_WINDOW',
                   position['opened_at'], 0))


def _cancel(broker, position, when):
    with broker.store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        result = ledger.cancel_open_paper_position(c, position['paper_id'], when, AUTHORITY)
    return result


def test_admin_cancel_is_not_a_sale_or_a_learning_label(partial_spot):
    broker, position, quote, _ = partial_spot()
    _pending(broker, position)
    when = (datetime.fromisoformat(quote.observed_at) + timedelta(days=1)).isoformat()
    invested = broker._cash(as_of=quote.observed_at)
    result = _cancel(broker, position, when)
    assert result['state'] == 'CANCELLED_ADMIN'
    assert result['sell_simulated_created'] is False
    assert broker.store.open_positions() == []
    assert broker.store.recent_closed() == []
    assert broker.store.exit_positions() == ([], [])
    assert broker._cash(as_of=quote.observed_at) == invested
    assert broker._cash(as_of=when) == broker.initial_balances['ARS']
    with broker.store.connect() as c:
        p = c.execute('SELECT * FROM paper_positions WHERE paper_id=?',
                      (position['paper_id'],)).fetchone()
        assert p['status'] == 'CANCELLED_ADMIN'
        assert p['exit_price'] is None and p['net_pnl'] is None
        assert p['close_reason'] == ledger.ADMIN_CANCEL_REASON
        assert c.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0
        assert c.execute('SELECT COUNT(*) FROM paper_admin_cancellations').fetchone()[0] == 1
        assert c.execute('SELECT label_timestamp FROM paper_learning_samples').fetchone()[0] is None
        assert ledger.positions_at(c, quote.observed_at)[0][0]['paper_id'] == position['paper_id']
        assert ledger.positions_at(c, when) == ([], [])
        assert c.execute("SELECT COUNT(*) FROM paper_events WHERE event_type='PAPER_ADMIN_CANCELLED_NO_MARKET'").fetchone()[0] == 1
    with pytest.raises(ValueError, match='no está abierta'):
        _cancel(broker, position, when)


def test_admin_cancel_rejects_partial_sale_without_any_write(partial_spot):
    broker, position, quote, sell = partial_spot()
    assert broker._close(position, sell(), 'TEST_PARTIAL')
    _pending(broker, position)
    when = (datetime.fromisoformat(quote.observed_at) + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match='ventas parciales'):
        _cancel(broker, position, when)
    with broker.store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM paper_admin_cancellations').fetchone()[0] == 0
        assert c.execute('SELECT status FROM paper_positions').fetchone()[0] == 'OPEN'


def test_admin_cancel_missing_receipt_is_detected_not_hidden(partial_spot):
    broker, position, quote, _ = partial_spot()
    _pending(broker, position)
    when = (datetime.fromisoformat(quote.observed_at) + timedelta(days=1)).isoformat()
    _cancel(broker, position, when)
    with broker.store.connect() as c:
        c.execute('DELETE FROM paper_admin_cancellations')
    with broker.store.connect() as c:
        with pytest.raises(ValueError, match='sin autoridad/recibo'):
            ledger.positions_at(c)
    opened, invalid = broker.store.exit_positions()
    assert opened == [] and len(invalid) == 1
    assert invalid[0][1].startswith('SPOT_ADMIN_CANCEL_INVALID:')


def test_admin_cancel_requires_pending_eod_intent(partial_spot):
    broker, position, quote, _ = partial_spot()
    when = (datetime.fromisoformat(quote.observed_at) + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match='intención EOD'):
        _cancel(broker, position, when)
    with broker.store.connect() as c:
        assert c.execute('SELECT status FROM paper_positions').fetchone()[0] == 'OPEN'
        assert c.execute('SELECT COUNT(*) FROM paper_admin_cancellations').fetchone()[0] == 0


def _receipt(tmp_path, paper_id, candidate='a' * 40):
    path = tmp_path / 'quiescence.json'
    names = sorted(admin.OWNED)
    path.write_text(json.dumps({
        'candidate_sha': candidate, 'status': 'ADMIN_WRITE_AUTHORIZED',
        'admin_cancel_paper_id': paper_id,
        'installation_started': True, 'inflight': 'ABSENT_OWNED_RUNTIME_STOPPED',
        'ppi_watch_accessed': False, 'real_orders_sent': 0,
        'stop_attempted': names,
        'containers': {n: {'id': n, 'image': 'paper-image'} for n in names},
        'stop_evidence': {n: {'classification': 'CLEAN_EXIT', 'after': {
            'Status': 'exited', 'ExitCode': 0, 'OOMKilled': False}} for n in names},
    }))
    return path


def test_admin_cli_requires_stopped_runtime_and_receipt(partial_spot, tmp_path):
    broker, p, _, _ = partial_spot()
    # The generic fixture opens a different symbol/quantity; a real run must
    # reject it rather than silently choosing the first PAPER position.
    _pending(broker, p)
    receipt = _receipt(tmp_path, p['paper_id'])
    with pytest.raises(ValueError, match='ADMIN_STOPPED_PAPER_ONLY'):
        admin.execute(broker.store.path, receipt, 'a' * 40, apply=True)
    broker.store.state(process_state='STOPPED', session_state='MARKET_CLOSED')
    with pytest.raises(ValueError, match='ADMIN_SUPV_IDENTITY_OR_QUANTITY_CHANGED'):
        admin.execute(broker.store.path, receipt, 'a' * 40, apply=True)
    data = json.loads(receipt.read_text())
    data['installation_started'] = False
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='ADMIN_STOP_RECEIPT_INVALID'):
        admin.execute(broker.store.path, receipt, 'a' * 40, apply=True)
    with broker.store.connect() as c:
        assert c.execute('SELECT status FROM paper_positions').fetchone()[0] == 'OPEN'


def test_admin_cli_plan_then_apply_exact_identity(partial_spot, tmp_path):
    broker, p, _, _ = partial_spot(settlement='A-24HS')
    _pending(broker, p)
    broker.store.state(process_state='STOPPED', session_state='MARKET_CLOSED')
    with broker.store.connect() as c:
        timestamp = '2026-10-19T18:51:58+00:00'
        c.execute("UPDATE paper_positions SET symbol='SUPV',quantity='9',opened_at=? WHERE paper_id=?",
                  (timestamp, p['paper_id']))
        c.execute("UPDATE paper_fills SET quantity='9',filled_at=? WHERE paper_id=?",
                  (timestamp, p['paper_id']))
    receipt = _receipt(tmp_path, p['paper_id'])
    before = admin.execute(broker.store.path, receipt, 'a' * 40)
    assert before['status'] == 'PLAN_ONLY'
    with broker.store.connect() as c:
        assert c.execute('SELECT status FROM paper_positions').fetchone()[0] == 'OPEN'
    after = admin.execute(broker.store.path, receipt, 'a' * 40, apply=True)
    assert after['status'] == 'CANCELLED_ADMIN' and not after['sell_simulated_created']
    with broker.store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM paper_admin_cancellations').fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0


def test_exceptional_admission_is_exact_and_normal_gate_remains_closed(partial_spot, tmp_path, monkeypatch):
    broker, p, _, _ = partial_spot(settlement='A-24HS')
    _pending(broker, p)
    broker.store.state(process_state='STOPPED', session_state='MARKET_CLOSED')
    with broker.store.connect() as c:
        timestamp = '2026-10-19T18:51:58+00:00'
        c.execute("UPDATE paper_positions SET symbol='SUPV',quantity='9',opened_at=? WHERE paper_id=?",
                  (timestamp, p['paper_id']))
        c.execute("UPDATE paper_fills SET quantity='9',filled_at=? WHERE paper_id=?",
                  (timestamp, p['paper_id']))
    root = tmp_path / 'host'
    target = root / 'data/paper_v17/observer_v17.db'
    target.parent.mkdir(parents=True)
    with sqlite3.connect(broker.store.path) as source, sqlite3.connect(target) as destination:
        source.backup(destination)
    monkeypatch.setattr(quiescence, 'ROOT', root)
    with pytest.raises(ValueError, match='QUIESCENCE_OPEN_POSITION'):
        quiescence.admit(stopped=True)
    assert quiescence.admit(stopped=True, allow_admin_issue624=True) == 'STOPPED'
    assert quiescence.admin_target_on_host() == p['paper_id']
    with sqlite3.connect(target) as c:
        c.execute("UPDATE paper_positions SET quantity='8' WHERE paper_id=?", (p['paper_id'],))
    with pytest.raises(ValueError, match='QUIESCENCE_ADMIN_TARGET_CHANGED'):
        quiescence.admit(stopped=True, allow_admin_issue624=True)
