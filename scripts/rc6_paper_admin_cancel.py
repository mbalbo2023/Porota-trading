#!/usr/bin/env python3
"""One exact operator-authorized PAPER void, never a market sale.

Only a drained, stopped own runtime may call apply. A missing or contradictory
receipt leaves SQLite untouched. This tool never calls PPI or a broker.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from zoneinfo import ZoneInfo

from cd_spot_ledger import ADMIN_CANCEL_AUTHORITY, cancel_open_paper_position

DATABASE = Path('/opt/porota-trading/data/paper_v17/observer_v17.db')
RECEIPT_ROOT = Path('/opt/porota-trading/data/deploy')
AUTHORITY = ADMIN_CANCEL_AUTHORITY
OWNED = {'porota_production_observer', 'porota_production_dashboard'}
IDENTITY = ('SUPV', 'ACCIONES', 'BYMA', 'ARS', 'A-24HS', '9')


def verify_receipt(path, candidate):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('ADMIN_RECEIPT_PATH_INVALID')
    receipt = json.loads(path.read_text(encoding='utf-8'))
    if (receipt.get('candidate_sha') != candidate
            or receipt.get('status') != 'ADMIN_WRITE_AUTHORIZED'
            or receipt.get('installation_started') is not True
            or receipt.get('inflight') != 'ABSENT_OWNED_RUNTIME_STOPPED'
            or receipt.get('ppi_watch_accessed') is not False
            or type(receipt.get('real_orders_sent')) is not int
            or receipt['real_orders_sent'] != 0
            or set(receipt.get('stop_attempted') or []) != OWNED
            or set(receipt.get('stop_evidence') or {}) != OWNED
            or set(receipt.get('containers') or {}) != OWNED):
        raise ValueError('ADMIN_STOP_RECEIPT_INVALID')
    for name in OWNED:
        proof = receipt['stop_evidence'][name]
        expected = receipt['containers'][name]
        after = proof.get('after') or {}
        if (proof.get('classification') not in
                {'CLEAN_EXIT', 'INHERITED_OOM_FLAG_CLEAN_EXIT'}
                or after.get('Status') != 'exited'
                or type(after.get('ExitCode')) is not int or after['ExitCode'] != 0
                or after.get('OOMKilled') not in (False, True)
                or not expected.get('id') or not expected.get('image')):
            raise ValueError('ADMIN_RUNTIME_STOP_UNVERIFIED')
    return receipt


def _exact_open(c, *, stopped_host_verified=False):
    state = c.execute('''SELECT mode,real_orders_sent,process_state,session_state
      FROM observer_state WHERE id=1''').fetchone()
    if (not state or state['mode'] != 'PRODUCTION_PAPER'
            or type(state['real_orders_sent']) is not int
            or state['real_orders_sent'] != 0
            # Docker can stop the scanner without changing its last persisted
            # WAITING_MARKET state. That state is safe only after independent
            # inspection proves both owned containers are still stopped.
            or state['process_state'] not in
                ({'STOPPED', 'WAITING_MARKET'} if stopped_host_verified else {'STOPPED'})
            or state['session_state'] not in {'CLOSED', 'MARKET_CLOSED'}):
        raise ValueError('ADMIN_STOPPED_PAPER_ONLY')
    positions = c.execute("SELECT * FROM paper_positions WHERE status='OPEN'").fetchall()
    if len(positions) != 1:
        raise ValueError('ADMIN_EXACT_ONE_SPOT_REQUIRED')
    p = positions[0]
    if tuple(str(p[k]) for k in ('symbol', 'asset_class', 'market', 'currency',
                                 'settlement', 'quantity')) != IDENTITY:
        raise ValueError('ADMIN_SUPV_IDENTITY_OR_QUANTITY_CHANGED')
    opened = datetime.fromisoformat(p['opened_at'])
    if opened.tzinfo is None:
        raise ValueError('ADMIN_OPEN_CLOCK_UNVERIFIED')
    local = opened.astimezone(ZoneInfo('America/Argentina/Buenos_Aires'))
    if (local.date().isoformat(), local.hour, local.minute) != ('2026-10-19', 15, 51):
        raise ValueError('ADMIN_OPEN_TIME_CHANGED')
    intent = c.execute('SELECT state,cause FROM paper_exit_intents WHERE paper_id=?',
                       (p['paper_id'],)).fetchone()
    if (not intent or intent['state'] != 'EXIT_PENDING_MARKET_CLOSED'
            or intent['cause'] != 'EOD_PAPER'):
        raise ValueError('ADMIN_EOD_INTENT_CHANGED')
    for table, state in (('paper_future_positions', 'ACTIVE'),
                         ('paper_cauciones', 'OPEN')):
        if c.execute(f'SELECT 1 FROM {table} WHERE status=? LIMIT 1',
                     (state,)).fetchone():
            raise ValueError('ADMIN_OTHER_EXPOSURE_PRESENT')
    return p


def verify_host_stopped(receipt):
    """Receipt alone is insufficient if an owned container restarted."""
    for name in sorted(OWNED):
        result = subprocess.run(['docker', 'inspect', name], capture_output=True,
                                text=True, timeout=10, check=True)
        values = json.loads(result.stdout)
        if len(values) != 1:
            raise ValueError('ADMIN_RUNTIME_INSPECT_INVALID')
        value = values[0]
        expected = receipt['containers'][name]
        if (value['Id'] != expected['id'] or value['Image'] != expected['image']
                or value['State']['Status'] != 'exited'
                or value['State']['ExitCode'] != 0):
            raise ValueError('ADMIN_RUNTIME_CHANGED_AFTER_DRAIN')


def execute(db, receipt, candidate, *, apply=False):
    """Return only redacted counts/status; no price is manufactured or logged."""
    if not re.fullmatch(r'[0-9a-f]{40}', candidate):
        raise ValueError('ADMIN_CANDIDATE_INVALID')
    proof = verify_receipt(receipt, candidate)
    db = Path(db)
    if db.is_symlink() or not db.is_file():
        raise ValueError('ADMIN_DB_PATH_INVALID')
    stopped_host_verified = apply and db.resolve() == DATABASE
    if stopped_host_verified:
        verify_host_stopped(proof)
    mode = 'rw' if apply else 'ro'
    with closing(sqlite3.connect(db.as_uri() + f'?mode={mode}', uri=True,
                                 timeout=.2, isolation_level=None)) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA busy_timeout=200')
        c.execute('PRAGMA foreign_keys=ON')
        if not apply:
            c.execute('PRAGMA query_only=ON')
        deadline = time.monotonic() + 2
        c.set_progress_handler(lambda: int(time.monotonic() > deadline), 200)
        c.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
        try:
            p = _exact_open(c, stopped_host_verified=stopped_host_verified)
            if p['paper_id'] != proof.get('admin_cancel_paper_id'):
                raise ValueError('ADMIN_RECEIPT_POSITION_CHANGED')
            if not apply:
                return {'status': 'PLAN_ONLY', 'position_count': 1,
                        'paper_id': p['paper_id'], 'real_orders_sent': 0}
            # The table is part of the candidate schema. Creating it here is
            # idempotent and in the same rollback boundary as the void.
            c.execute('''CREATE TABLE IF NOT EXISTS paper_admin_cancellations(
              paper_id TEXT PRIMARY KEY REFERENCES paper_positions(paper_id),
              cancelled_at TEXT NOT NULL, authority TEXT NOT NULL,
              original_quantity TEXT NOT NULL, original_entry_price TEXT NOT NULL,
              original_entry_cost TEXT NOT NULL, reason TEXT NOT NULL)''')
            result = cancel_open_paper_position(
                c, p['paper_id'], datetime.now(timezone.utc).isoformat(), AUTHORITY)
            if c.execute("SELECT 1 FROM paper_positions WHERE status='OPEN'").fetchone():
                raise ValueError('ADMIN_EXPOSURE_REMAINS')
            if c.execute("SELECT 1 FROM paper_fills WHERE paper_id=? AND side='SELL_SIMULATED'",
                         (p['paper_id'],)).fetchone():
                raise ValueError('ADMIN_SYNTHETIC_SALE_FORBIDDEN')
            c.commit()
            return {'status': result['state'], 'paper_id': p['paper_id'],
                    'sell_simulated_created': False, 'real_orders_sent': 0}
        except BaseException:
            c.rollback()
            raise


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate-sha', required=True)
    parser.add_argument('--receipt', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    expected = RECEIPT_ROOT / ('quiescence-' + args.candidate_sha + '.json')
    if Path(args.receipt) != expected:
        parser.error('ADMIN_EXACT_RECEIPT_PATH_REQUIRED')
    result = execute(DATABASE, expected, args.candidate_sha, apply=args.apply)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
