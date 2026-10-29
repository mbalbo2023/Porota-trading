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


def run_go(tmp_path, monkeypatch, *, process='STARTING', auth='OK', session='CLOSED',
           counter=0, age=0, detail='UNCLASSIFIED_RUNTIME_FAILURE',
           market_age=None, transition=None, timeline=()):
    path = tmp_path / 'paper.db'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE observer_state(id INTEGER,mode TEXT,real_orders_sent INTEGER,ppi_auth TEXT,process_state TEXT,session_state TEXT,last_market_data_at TEXT,heartbeat_at TEXT,detail TEXT)')
        stamp = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
        market = ((datetime.now(timezone.utc) - timedelta(seconds=market_age)).isoformat()
                  if market_age is not None else None)
        connection.execute('INSERT INTO observer_state VALUES(1,?,?,?,?,?,?,?,?)',
            ('PRODUCTION_PAPER', counter, auth, process, session, market, stamp, detail))
    elapsed = [0]
    def pause(seconds):
        elapsed[0] += seconds
        event = next((row for row in reversed(timeline) if elapsed[0] >= row[0]), None)
        if event:
            with sqlite3.connect(path) as connection:
                connection.execute(
                    'UPDATE observer_state SET ppi_auth=?,process_state=?,session_state=?,heartbeat_at=?',
                    (*event[1:], datetime.now(timezone.utc).isoformat()))
        elif transition:
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


def test_cooldown_boot_breadcrumb_and_login_transition_after_90_seconds(
        tmp_path, monkeypatch, capsys):
    """Execute the actual GO heredoc against the scanner's persisted states."""
    code, elapsed = run_go(
        tmp_path, monkeypatch, process='DEGRADED', auth='COOLDOWN',
        session='MARKET_CLOSED', timeline=(
            (85, 'NOT_ATTEMPTED', 'DEGRADED', 'BOOT_COMMAND'),
            (90, 'NOT_ATTEMPTED', 'DEGRADED', 'BOOT_CALENDAR'),
            (95, 'COOLDOWN', 'DEGRADED', 'MARKET_CLOSED'),
            (100, 'NOT_ATTEMPTED', 'DEGRADED', 'BOOT_COMMAND'),
            (105, 'NOT_ATTEMPTED', 'DEGRADED', 'BOOT_PPI_SECRET'),
            (110, 'NOT_ATTEMPTED', 'DEGRADED', 'BOOT_PPI_CLIENT'),
            (115, 'NOT_ATTEMPTED', 'DEGRADED', 'BOOT_PPI_LOGIN'),
            (120, 'NOT_ATTEMPTED', 'STARTING', 'BOOT_PPI_LOGIN'),
            (125, 'OK', 'STARTING', 'MARKET_CLOSED'),
            (145, 'OK', 'WAITING_MARKET', 'MARKET_CLOSED')))
    output = capsys.readouterr().out
    assert code == 0 and 145 <= elapsed <= 150
    assert 'GO_CHECK=GREEN|PRODUCTION_PAPER|0|OK|WAITING_MARKET|' in output


def test_boot_label_outside_exact_login_states_fails_closed(tmp_path, monkeypatch, capsys):
    code, elapsed = run_go(
        tmp_path, monkeypatch, process='DEGRADED', auth='COOLDOWN',
        session='MARKET_CLOSED', timeline=(
            (85, 'NOT_ATTEMPTED', 'DEGRADED', 'MARKET_CLOSED'),
            (145, 'OK', 'WAITING_MARKET', 'MARKET_CLOSED')))
    assert code != 0 and elapsed <= 90
    assert 'GO_CHECK=GREEN' not in capsys.readouterr().out


def test_late_explicit_login_error_stops_before_budget(tmp_path, monkeypatch, capsys):
    code, elapsed = run_go(
        tmp_path, monkeypatch, process='DEGRADED', auth='COOLDOWN',
        session='MARKET_CLOSED', timeline=(
            (85, 'NOT_ATTEMPTED', 'DEGRADED', 'BOOT_COMMAND'),
            (95, 'ERROR', 'DEGRADED', 'MARKET_CLOSED'),
            (145, 'OK', 'WAITING_MARKET', 'MARKET_CLOSED')))
    assert code != 0 and 95 <= elapsed < 145
    assert 'GO_CHECK=GREEN' not in capsys.readouterr().out


def test_bootstrap_pending_expires_bounded_budget(tmp_path, monkeypatch, capsys):
    code, elapsed = run_go(
        tmp_path, monkeypatch, process='DEGRADED', auth='COOLDOWN',
        session='MARKET_CLOSED', timeline=(
            (85, 'NOT_ATTEMPTED', 'DEGRADED', 'BOOT_COMMAND'),))
    assert code != 0 and 1195 <= elapsed <= 1200
    assert 'GO_CHECK=GREEN' not in capsys.readouterr().out


def test_bootstrap_pending_cannot_hide_nonzero_order_counter(tmp_path, monkeypatch, capsys):
    code, elapsed = run_go(
        tmp_path, monkeypatch, process='DEGRADED', auth='NOT_ATTEMPTED',
        session='BOOT_COMMAND', counter=1)
    assert code != 0 and elapsed <= 90
    assert 'GO_CHECK=GREEN' not in capsys.readouterr().out


def test_bootstrap_pending_requires_fresh_heartbeat(tmp_path, monkeypatch, capsys):
    code, elapsed = run_go(
        tmp_path, monkeypatch, process='DEGRADED', auth='NOT_ATTEMPTED',
        session='BOOT_PPI_LOGIN', age=121)
    assert code != 0 and elapsed <= 90
    assert 'GO_CHECK=GREEN' not in capsys.readouterr().out


def test_login_error_with_stale_boot_label_is_not_pending(tmp_path, monkeypatch, capsys):
    code, elapsed = run_go(
        tmp_path, monkeypatch, process='DEGRADED', auth='COOLDOWN',
        session='MARKET_CLOSED', timeline=(
            (85, 'NOT_ATTEMPTED', 'DEGRADED', 'BOOT_PPI_LOGIN'),
            (95, 'ERROR', 'DEGRADED', 'BOOT_PPI_LOGIN')))
    assert code != 0 and 95 <= elapsed < 1200
    assert 'GO_CHECK=GREEN' not in capsys.readouterr().out


@pytest.mark.parametrize('auth,detail', (
    ('ERROR', 'Login fallo: HTTPError'),
    ('COOLDOWN', 'PPI en espera de reintento; no se abre una tormenta de logins.'),
    ('ERROR', 'Login local bloqueado por interlock persistente.'),
))
def test_external_ppi_login_outage_allows_technical_deploy_only(
        tmp_path, monkeypatch, capsys, auth, detail):
    code, elapsed = run_go(tmp_path, monkeypatch, process='DEGRADED',
                           auth=auth, session='BOOT_PPI_LOGIN', detail=detail)
    output = capsys.readouterr().out
    assert code == 0 and elapsed == 0
    assert 'GO_CHECK=GREEN|' in output
    assert 'EXTERNAL_SOURCE_ALERT=AMBER|REASON=PPI_LOGIN_UNAVAILABLE' in output
    assert 'PAPER_READINESS=FAIL_CLOSED_PER_INSTRUMENT' in output


def test_source_stale_market_data_allows_technical_deploy_with_alarm(tmp_path, monkeypatch, capsys):
    code, elapsed = run_go(tmp_path, monkeypatch, process='RUNNING', auth='OK',
                           session='MARKET_OPEN', market_age=600)
    output = capsys.readouterr().out
    assert code == 0 and elapsed == 0
    assert 'EXTERNAL_SOURCE_ALERT=AMBER|REASON=PPI_MARKET_DATA_STALE' in output


def test_unclassified_degraded_error_remains_deploy_red(tmp_path, monkeypatch, capsys):
    code, elapsed = run_go(tmp_path, monkeypatch, process='DEGRADED', auth='ERROR',
                           detail='CAUCION_LEDGER_INVALID')
    assert code != 0 and elapsed <= 90
    assert 'GO_CHECK=GREEN' not in capsys.readouterr().out


def test_ppi_outage_cannot_hide_real_order_counter(tmp_path, monkeypatch, capsys):
    code, elapsed = run_go(tmp_path, monkeypatch, process='DEGRADED', auth='ERROR',
                           detail='Login fallo: HTTPError', counter=1)
    assert code != 0 and elapsed <= 90
    assert 'GO_CHECK=GREEN' not in capsys.readouterr().out
