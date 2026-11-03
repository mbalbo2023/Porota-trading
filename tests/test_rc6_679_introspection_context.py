"""CLOSED EOD carry remains visible without hourly false CRITICAL alerts."""
from datetime import datetime, timedelta
import sqlite3

import ops_introspection_rc6 as introspection


def fixture(now):
    c=sqlite3.connect(':memory:')
    c.row_factory=sqlite3.Row
    c.executescript('''
        CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY,status TEXT,opened_at TEXT);
        CREATE TABLE paper_exit_intents(paper_id TEXT PRIMARY KEY,state TEXT,cause TEXT);
        CREATE TABLE paper_supervisor_state(id INTEGER PRIMARY KEY,state TEXT,heartbeat_at TEXT);
        CREATE TABLE paper_exit_reader_state(id INTEGER PRIMARY KEY,state TEXT,heartbeat_at TEXT);
    ''')
    opened=(now-timedelta(hours=7)).isoformat()
    c.executemany('INSERT INTO paper_positions VALUES(?,?,?)',
                  [('one','OPEN',opened),('two','OPEN',opened)])
    c.executemany('INSERT INTO paper_exit_intents VALUES(?,?,?)',
                  [('one','EXIT_PENDING_MARKET_CLOSED','EOD_PAPER'),
                   ('two','EXIT_PENDING_MARKET_CLOSED','EOD_PAPER')])
    c.execute('INSERT INTO paper_supervisor_state VALUES(1,?,?)',('RUNNING',now.isoformat()))
    c.execute('INSERT INTO paper_exit_reader_state VALUES(1,?,?)',('WAITING_MARKET',now.isoformat()))
    observer={'mode':'PRODUCTION_PAPER','real_orders_sent':0,
              'session_state':'MARKET_CLOSED','heartbeat_at':now.isoformat()}
    return c,observer


def test_exact_two_eod_carries_are_warning_with_raw_excess_preserved():
    now=datetime.fromisoformat('2026-10-20T23:00:00-03:00')
    c,observer=fixture(now)
    try:
        pending=introspection.pending_closed_eod_over_hold(c,now,observer)
        assert pending==2
        assert introspection.hold_excess_findings(2,pending)==(
            [],['open_over_max_hold_pending_market_closed=2'])
    finally:
        c.close()


def test_mixed_cause_or_dead_supervisor_keeps_actionable_critical():
    now=datetime.fromisoformat('2026-10-20T23:00:00-03:00')
    c,observer=fixture(now)
    try:
        c.execute("UPDATE paper_exit_intents SET cause='STOP_LOSS' WHERE paper_id='two'")
        assert introspection.hold_excess_findings(2,
            introspection.pending_closed_eod_over_hold(c,now,observer))==(
                ['open_over_max_hold=1'],['open_over_max_hold_pending_market_closed=1'])
        c.execute("UPDATE paper_supervisor_state SET state='STOPPED'")
        assert introspection.pending_closed_eod_over_hold(c,now,observer)==0
        assert introspection.hold_excess_findings(2,0)==(['open_over_max_hold=2'],[])
    finally:
        c.close()


def test_open_clock_never_downgrades_stale_market_closed_label():
    now=datetime.fromisoformat('2026-10-21T11:30:00-03:00')
    c,observer=fixture(now)
    try:
        assert introspection.pending_closed_eod_over_hold(c,now,observer)==0
    finally:
        c.close()


def test_future_or_stale_safety_heartbeat_never_downgrades_hold():
    now=datetime.fromisoformat('2026-10-20T23:00:00-03:00')
    c,observer=fixture(now)
    try:
        observer['heartbeat_at']=(now+timedelta(seconds=1)).isoformat()
        assert introspection.pending_closed_eod_over_hold(c,now,observer)==0
        observer['heartbeat_at']=now.isoformat()
        c.execute('UPDATE paper_exit_reader_state SET heartbeat_at=?',
                  ((now-timedelta(seconds=21)).isoformat(),))
        assert introspection.pending_closed_eod_over_hold(c,now,observer)==0
    finally:
        c.close()
