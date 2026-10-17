"""Execute the actual workflow's GO program on an isolated SQLite store."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
import textwrap
import time

import pytest


def program():
    text = Path('.github/workflows/porota-deploy-v2-promote.yml').read_text()
    body = text.split('RUNTIME_CHECK="$(sudo -n timeout 1260 python3', 1)[1]
    return compile(textwrap.dedent(body.split("<<'PY'\n", 1)[1].split('\n          PY\n', 1)[0]),
                   'canonical-deploy-go.py', 'exec')


def run_go(tmp_path, monkeypatch, *, process='STARTING', counter=0, age=0, transition=None):
    path = tmp_path / 'paper.db'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE observer_state(id INTEGER,mode TEXT,real_orders_sent INTEGER,ppi_auth TEXT,process_state TEXT,session_state TEXT,last_market_data_at TEXT,heartbeat_at TEXT)')
        stamp = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
        connection.execute('INSERT INTO observer_state VALUES(1,?,?,?,?,?,?,?)',
            ('PRODUCTION_PAPER', counter, 'OK', process, 'CLOSED', None, stamp))
    elapsed = [0]
    def pause(seconds):
        elapsed[0] += seconds
        if transition:
            with sqlite3.connect(path) as connection:
                connection.execute('UPDATE observer_state SET process_state=?,ppi_auth=?,heartbeat_at=?',
                    (*transition, datetime.now(timezone.utc).isoformat()))
    monkeypatch.setattr(sys, 'argv', ['canonical-deploy-go.py', str(path)])
    monkeypatch.setattr(time, 'monotonic', lambda: elapsed[0])
    monkeypatch.setattr(time, 'sleep', pause)
    try:
        exec(program(), {})
    except SystemExit as error:
        return error.code, elapsed[0]
    return 0, elapsed[0]


def test_go_waits_for_completed_startup_before_accepting_baseline(tmp_path, monkeypatch, capsys):
    code, elapsed = run_go(tmp_path, monkeypatch, transition=('WAITING_MARKET', 'OK'))
    output = capsys.readouterr().out
    assert code == 0 and elapsed > 0
    assert 'GO_CHECK=GREEN|PRODUCTION_PAPER|0|OK|WAITING_MARKET|' in output


def test_stuck_starting_exhausts_existing_budget_without_go(tmp_path, monkeypatch, capsys):
    code, elapsed = run_go(tmp_path, monkeypatch)
    assert code != 0 and 900 <= elapsed <= 1200
    assert 'GO_CHECK=GREEN' not in capsys.readouterr().out


def test_startup_auth_failure_is_not_waited_out_as_healthy(tmp_path, monkeypatch, capsys):
    code, elapsed = run_go(tmp_path, monkeypatch, transition=('DEGRADED', 'ERROR'))
    assert code != 0 and elapsed <= 90
    assert 'GO_CHECK=GREEN' not in capsys.readouterr().out


def test_unknown_order_counter_cannot_pass_go(tmp_path, monkeypatch, capsys):
    code, _ = run_go(tmp_path, monkeypatch, process='WAITING_MARKET', counter=None)
    assert code != 0
    assert 'GO_CHECK=GREEN' not in capsys.readouterr().out


@pytest.mark.parametrize('age,expected', ((0, 0), (121, 1)))
def test_completed_startup_still_requires_fresh_heartbeat(tmp_path, monkeypatch, age, expected):
    code, _ = run_go(tmp_path, monkeypatch, process='WAITING_MARKET', age=age)
    assert code == expected
