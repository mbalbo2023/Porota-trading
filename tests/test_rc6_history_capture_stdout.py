"""A large host snapshot cannot run under an unknown PAPER admission state."""
from datetime import datetime
import json
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from scripts import rc6_history_capture_stdout as capture


def _state(tmp_path, monkeypatch, *, mode='PRODUCTION_PAPER', phase='MARKET_OPEN', orders=0):
    database = tmp_path / 'observer.sqlite'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE observer_state(id,mode,session_state,real_orders_sent)')
        connection.execute('INSERT INTO observer_state VALUES(1,?,?,?)', (mode, phase, orders))
    receipt = tmp_path / 'receipt.json'
    monkeypatch.setattr(capture, 'OBSERVER', database)
    monkeypatch.setattr(capture, 'RECEIPT', receipt)
    return receipt


def test_open_capture_needs_genuine_today_no_go(tmp_path, monkeypatch):
    receipt = _state(tmp_path, monkeypatch)
    today = datetime.now(ZoneInfo('America/Argentina/Buenos_Aires')).date().isoformat()
    with pytest.raises(RuntimeError, match='OPEN_ADMISSION_UNKNOWN'):
        capture._admission()
    receipt.write_text(json.dumps({'status':'NO_GO_PAPER_SCALPING',
                                   'trade_day_ar':'2020-01-01'}))
    with pytest.raises(RuntimeError, match='OPEN_ADMISSION_NOT_BLOCKED'):
        capture._admission()
    receipt.write_text(json.dumps({'status':'NO_GO_PAPER_SCALPING',
                                   'trade_day_ar':today}))
    capture._admission()


def test_paper_safety_precedes_receipt_or_copy(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch, orders=1)
    with pytest.raises(RuntimeError, match='PAPER_SAFETY_BLOCKED'):
        capture._admission()
