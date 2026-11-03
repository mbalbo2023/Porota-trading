#!/usr/bin/env python3
"""One exact operator-authorized PAPER void, never a market sale.

Only a drained, stopped own runtime may call apply. A missing or contradictory
receipt leaves SQLite untouched. This tool never calls PPI or a broker.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from zoneinfo import ZoneInfo

from cd_spot_ledger import (ADMIN_CANCEL_AUTHORITY, ADMIN_CANCEL_AUTHORITY_A205,
                            ADMIN_CANCEL_AUTHORITY_A207,
                            cancel_open_paper_position, partition)

DATABASE = Path('/opt/porota-trading/data/paper_v17/observer_v17.db')
RECEIPT_ROOT = Path('/opt/porota-trading/data/deploy')
AUTHORITY = ADMIN_CANCEL_AUTHORITY
OWNED = {'porota_production_observer', 'porota_production_dashboard'}
IDENTITY = ('SUPV', 'ACCIONES', 'BYMA', 'ARS', 'A-24HS', '9')


def verify_receipt(path, candidate, *, allow_a207_resume=False):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('ADMIN_RECEIPT_PATH_INVALID')
    receipt = json.loads(path.read_text(encoding='utf-8'))
    status = receipt.get('status')
    resumed = (allow_a207_resume and
               status == 'FIX_FORWARD_VALIDATION_REQUIRED' and
               receipt.get('a207_resume_from') == 'ADMIN_WRITE_AUTHORIZED' and
               receipt.get('admin_policy') == 'A207_ALL_PAPER_REVERSAL_V1')
    if (receipt.get('candidate_sha') != candidate
            or (status != 'ADMIN_WRITE_AUTHORIZED' and not resumed)
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


def execute_a205_batch(db, receipt, candidate, *, apply=False, at=None):
    """A205: close *only* an exact, all-intact EOD spot batch atomically.

    A financed/unmatured caucion, a future margin position, any partial sale,
    a missing EOD intent or a changed exposure snapshot is NOT canceled here.
    The existing SUPV procedure and its 2026-10-19 authority are unchanged.
    """
    from scripts.rc6_paper_deploy_preflight import open_exposure_rows, _fingerprint
    if not re.fullmatch(r'[0-9a-f]{40}', candidate):
        raise ValueError('A205_CANDIDATE_INVALID')
    proof = verify_receipt(receipt, candidate)
    if (proof.get('admin_policy') != 'A205_EOD_SPOT_BATCH_V1'
            or proof.get('a205_owner_approval') is None
            or not re.fullmatch(r'[1-9][0-9]*', str(proof['a205_owner_approval']))
            or proof.get('a205_approval_candidate') != candidate
            or not isinstance(proof.get('admin_cancel_paper_ids'), list)
            or not 1 <= len(proof['admin_cancel_paper_ids']) <= 32
            or len(set(proof['admin_cancel_paper_ids'])) != len(proof['admin_cancel_paper_ids'])
            or not all(isinstance(value, str) and 0 < len(value) <= 128
                       for value in proof['admin_cancel_paper_ids'])
            or not re.fullmatch(r'[0-9a-f]{64}', proof.get('a205_exposures_sha256', ''))):
        raise ValueError('A205_AUTHORITY_OR_PLAN_INVALID')
    db = Path(db)
    if db.is_symlink() or not db.is_file():
        raise ValueError('A205_DB_PATH_UNSAFE')
    stopped_host_verified = apply and db.resolve() == DATABASE
    if stopped_host_verified:
        verify_host_stopped(proof)
    when = datetime.fromisoformat(at) if at is not None else datetime.now(timezone.utc)
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError('A205_CLOCK_UNVERIFIED')
    with closing(sqlite3.connect(db.as_uri() + ('?mode=rw' if apply else '?mode=ro'),
                                 uri=True, timeout=.2, isolation_level=None)) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA busy_timeout=200')
        c.execute('PRAGMA foreign_keys=ON')
        if not apply:
            c.execute('PRAGMA query_only=ON')
        c.set_progress_handler(lambda: int(time.monotonic() - begin > 3), 1000)
        begin = time.monotonic()
        c.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
        try:
            state = c.execute('''SELECT mode,real_orders_sent,process_state,session_state
                                 FROM observer_state WHERE id=1''').fetchone()
            if (not state or state['mode'] != 'PRODUCTION_PAPER'
                    or type(state['real_orders_sent']) is not int
                    or state['real_orders_sent'] != 0):
                raise ValueError('A205_PAPER_ZERO_REQUIRED')
            # An observer that wrote RUNNING before Docker was stopped can
            # retain that row. The independent exact Docker stop proof above
            # is mandatory on the actual host for the exceptional case.
            if proof.get('market_open_suspended') is True:
                allowed = (state['process_state'] in {'STOPPED', 'RUNNING'} and
                           state['session_state'] in {'MARKET_OPEN', 'OPEN'})
            else:
                allowed = (state['process_state'] in
                           ({'STOPPED', 'WAITING_MARKET'} if stopped_host_verified else {'STOPPED'})
                           and state['session_state'] in {'CLOSED', 'MARKET_CLOSED'})
            if not allowed:
                raise ValueError('A205_RUNTIME_NOT_QUIESCENT')
            counts, rows = open_exposure_rows(c)
            if counts['caucion'] or counts['future'] or counts['fund']:
                raise ValueError('A205_OTHER_FAMILY_NOT_ADMIN_VOIDABLE')
            targets = proof['admin_cancel_paper_ids']
            current = [row['paper_id'] for row in rows['spot']]
            if sorted(current) != sorted(targets):
                # Exactly one earlier successful transaction can be retried;
                # a partially canceled book or a different batch is RED.
                if current:
                    raise ValueError('A205_BATCH_CHANGED_OR_PARTIAL')
                for paper_id in targets:
                    p = c.execute('SELECT * FROM paper_positions WHERE paper_id=?',
                                  (paper_id,)).fetchone()
                    if (not p or p['status'] != 'CANCELLED_ADMIN' or
                            p['close_reason'] != 'ADMIN_CANCEL_NO_MARKET_EXECUTION'):
                        raise ValueError('A205_RETRY_NOT_FULLY_CLOSED')
                    receipt_row = c.execute('''SELECT authority FROM paper_admin_cancellations
                                               WHERE paper_id=?''', (paper_id,)).fetchone()
                    if not receipt_row or receipt_row['authority'] != ADMIN_CANCEL_AUTHORITY_A205:
                        raise ValueError('A205_RETRY_AUTHORITY_CHANGED')
                    if partition(c, p) != (None, []):
                        raise ValueError('A205_RETRY_LEDGER_INVALID')
                c.rollback()
                return {'status': 'ALREADY_CLOSED', 'position_count': len(targets),
                        'real_orders_sent': 0, 'sell_simulated_created': False}
            if _fingerprint(rows, {}) != proof['a205_exposures_sha256']:
                raise ValueError('A205_EXPOSURE_SNAPSHOT_CHANGED')
            # Eligibility checks run inside the exact same write transaction.
            from cd_spot_ledger import plan_open_paper_admin_cancel
            for paper_id in targets:
                plan_open_paper_admin_cancel(c, paper_id, when.isoformat())
            if not apply:
                c.rollback()
                return {'status': 'PLAN_ONLY', 'position_count': len(targets),
                        'real_orders_sent': 0, 'sell_simulated_created': False}
            for paper_id in targets:
                cancel_open_paper_position(c, paper_id, when.isoformat(),
                                           ADMIN_CANCEL_AUTHORITY_A205)
            after, _ = open_exposure_rows(c)
            if any(after.values()):
                raise ValueError('A205_BOOK_NOT_FLAT_AFTER_CANCEL')
            for paper_id in targets:
                p = c.execute('SELECT * FROM paper_positions WHERE paper_id=?',
                              (paper_id,)).fetchone()
                if partition(c, p) != (None, []):
                    raise ValueError('A205_SPOT_VOID_NOT_RECONCILED')
                if c.execute('''SELECT 1 FROM paper_fills WHERE paper_id=?
                                AND side='SELL_SIMULATED' LIMIT 1''', (paper_id,)).fetchone():
                    raise ValueError('A205_FAKE_SELL_FORBIDDEN')
            c.commit()
            return {'status': 'CANCELLED_ADMIN', 'position_count': len(targets),
                    'real_orders_sent': 0, 'sell_simulated_created': False}
        except BaseException:
            c.rollback()
            raise


def execute_a207_batch(db, receipt, candidate, *, apply=False, at=None):
    """One atomic administrative reversal across the three PAPER books.

    Never an exchange fill, sale or settlement. Spot requires an intact EOD
    pending intent; cauciones must be unliquidated; futures must have no
    settlement/variation events. All accounting changes commit together.
    """
    from scripts.rc6_paper_deploy_preflight import open_exposure_rows, _fingerprint
    from bt_caucion_paper import (ADMIN_CANCEL_AUTHORITY_A207 as CAUCION_AUTHORITY,
                                  cancel_open_caucion_admin, CaucionBook)
    from rc6_paper_family_lifecycle import (
        FUTURE_ADMIN_CANCEL_AUTHORITY_A207, cancel_open_future_admin,
        future_cash_effect)
    from cd_spot_ledger import plan_open_paper_admin_cancel
    if not re.fullmatch(r'[0-9a-f]{40}', candidate):
        raise ValueError('A207_CANDIDATE_INVALID')
    proof = verify_receipt(receipt, candidate, allow_a207_resume=True)
    targets = proof.get('a207_targets')
    if (proof.get('admin_policy') != 'A207_ALL_PAPER_REVERSAL_V1'
            or proof.get('a207_approval_candidate') != candidate
            or not re.fullmatch(r'[0-9a-f]{40}', proof.get('a207_approval_tree', ''))
            or not re.fullmatch(r'[1-9][0-9]*', str(proof.get('a207_owner_approval', '')))
            or not re.fullmatch(r'[0-9a-f]{64}', proof.get('a207_exposures_sha256', ''))
            or not isinstance(targets, dict) or set(targets) != {'spot','caucion','future'}
            or any(not isinstance(values, list) or len(values) > 32
                   or len(values) != len(set(values)) or any(
                       not isinstance(value, str) or not 0 < len(value) <= 128
                       for value in values) for values in targets.values())
            or not any(targets.values())):
        raise ValueError('A207_AUTHORITY_OR_TARGETS_INVALID')
    db = Path(db)
    if db.is_symlink() or not db.is_file():
        raise ValueError('A207_DB_PATH_UNSAFE')
    stopped_host_verified = apply and db.resolve() == DATABASE
    if stopped_host_verified:
        stat = Path(receipt).stat()
        if stat.st_uid != 0 or stat.st_mode & 0o077:
            raise ValueError('A207_ROOT_SEALED_RECEIPT_REQUIRED')
        verify_host_stopped(proof)
    when = datetime.fromisoformat(at) if at else datetime.now(timezone.utc)
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError('A207_CLOCK_UNVERIFIED')
    with closing(sqlite3.connect(db.as_uri() + ('?mode=rw' if apply else '?mode=ro'),
                                 uri=True, timeout=.2, isolation_level=None)) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA busy_timeout=200')
        c.execute('PRAGMA foreign_keys=ON')
        if not apply:
            c.execute('PRAGMA query_only=ON')
        begin = time.monotonic()
        c.set_progress_handler(lambda: int(time.monotonic() - begin > 5), 1000)
        c.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
        try:
            state = c.execute('''SELECT mode,real_orders_sent,process_state,session_state
                FROM observer_state WHERE id=1''').fetchone()
            if not state or state['mode'] != 'PRODUCTION_PAPER' or (
                    type(state['real_orders_sent']) is not int or state['real_orders_sent'] != 0):
                raise ValueError('A207_PAPER_ZERO_REQUIRED')
            if proof.get('market_open_suspended') is True:
                allowed = (state['process_state'] in {'STOPPED','RUNNING'} and
                           state['session_state'] in {'MARKET_OPEN','OPEN'})
            else:
                allowed = (state['process_state'] in
                           ({'STOPPED','WAITING_MARKET'} if stopped_host_verified else {'STOPPED'})
                           and state['session_state'] in {'CLOSED','MARKET_CLOSED'})
            if not allowed:
                raise ValueError('A207_RUNTIME_NOT_QUIESCENT')
            counts, rows = open_exposure_rows(c)
            if counts['fund']:
                raise ValueError('A207_FCI_NAV_OR_REDEMPTION_UNSUPPORTED')
            current = {name: sorted(row['paper_id'] if name != 'future' else row['lifecycle_id']
                                     for row in rows[name]) for name in targets}
            expected = {name: sorted(values) for name, values in targets.items()}
            if current != expected:
                if any(current.values()):
                    raise ValueError('A207_BATCH_CHANGED_OR_PARTIAL')
                _verify_a207_prior_cancel(c, expected, proof=proof, candidate=candidate)
                c.rollback()
                return {'status': 'ALREADY_CLOSED', 'counts': {k:len(v) for k,v in targets.items()},
                        'real_orders_sent': 0, 'real_routes_used': []}
            if _fingerprint(rows, {}) != proof['a207_exposures_sha256']:
                raise ValueError('A207_EXPOSURE_SNAPSHOT_CHANGED')
            for paper_id in targets['spot']:
                plan_open_paper_admin_cancel(c, paper_id, when.isoformat())
            for p in rows['caucion']:
                from bt_caucion_paper import validate_position
                validate_position(p)
                if p['settled_at'] is not None:
                    raise ValueError('A207_CAUCION_SETTLEMENT_NEEDS_RECONCILIATION')
            for p in rows['future']:
                if c.execute('''SELECT 1 FROM paper_future_marks WHERE lifecycle_id=?
                              AND is_settlement=1 LIMIT 1''', (p['lifecycle_id'],)).fetchone():
                    raise ValueError('A207_FUTURE_SETTLEMENT_NEEDS_RECONCILIATION')
            if not apply:
                c.rollback()
                return {'status': 'PLAN_ONLY', 'counts': {k:len(v) for k,v in targets.items()},
                        'real_orders_sent': 0, 'real_routes_used': []}
            before_caucion = {cc: CaucionBook(None).cash_effect(cc, when, connection=c)
                              for cc in {p['currency'] for p in rows['caucion']}}
            before_future = {cc: future_cash_effect(None, cc, when, connection=c)
                             for cc in {p['currency'] for p in rows['future']}}
            for paper_id in targets['spot']:
                cancel_open_paper_position(c, paper_id, when.isoformat(),
                                           ADMIN_CANCEL_AUTHORITY_A207)
            for paper_id in targets['caucion']:
                cancel_open_caucion_admin(c, paper_id, when.isoformat(), CAUCION_AUTHORITY)
            for lifecycle_id in targets['future']:
                cancel_open_future_admin(c, lifecycle_id, when.isoformat(),
                                         FUTURE_ADMIN_CANCEL_AUTHORITY_A207)
            after, _ = open_exposure_rows(c)
            if any(after.values()):
                raise ValueError('A207_BOOK_NOT_FLAT_AFTER_REVERSAL')
            _verify_a207_prior_cancel(c, expected)
            for currency, balance in before_caucion.items():
                changed = CaucionBook(None).cash_effect(currency, when, connection=c) - balance
                released = sum((Decimal(p['principal']) +
                                (Decimal(p['total_fees'])
                                 if p['fee_payment'] == 'UPFRONT' else 0)
                                for p in rows['caucion'] if p['currency'] == currency),
                               Decimal('0'))
                if changed != released:
                    raise ValueError('A207_CAUCION_CASH_NOT_RECONCILED')
            for currency, balance in before_future.items():
                changed = future_cash_effect(None, currency, when, connection=c) - balance
                released = sum((-Decimal(p['margin_reserved']) -
                                Decimal(p['entry_cost'])
                                for p in rows['future'] if p['currency'] == currency),
                               Decimal('0'))
                if changed != -released:
                    raise ValueError('A207_FUTURE_CASH_NOT_RECONCILED')
            c.execute('''CREATE TABLE IF NOT EXISTS paper_admin_reversal_batches(
                candidate_sha TEXT PRIMARY KEY, tree_sha TEXT NOT NULL,
                owner_comment_id TEXT NOT NULL, exposures_sha256 TEXT NOT NULL,
                targets_json TEXT NOT NULL, completed_at TEXT NOT NULL,
                audit_digest_sha256 TEXT NOT NULL)''')
            c.execute('''CREATE TRIGGER IF NOT EXISTS paper_admin_reversal_batches_no_update
                BEFORE UPDATE ON paper_admin_reversal_batches
                BEGIN SELECT RAISE(ABORT,'PAPER_ADMIN_BATCH_RECEIPT_IMMUTABLE'); END''')
            c.execute('''CREATE TRIGGER IF NOT EXISTS paper_admin_reversal_batches_no_delete
                BEFORE DELETE ON paper_admin_reversal_batches
                BEGIN SELECT RAISE(ABORT,'PAPER_ADMIN_BATCH_RECEIPT_IMMUTABLE'); END''')
            sealed = _a207_batch_seal(proof, candidate)
            c.execute('''INSERT INTO paper_admin_reversal_batches
                VALUES (?,?,?,?,?,?,?)''',
                (candidate, proof['a207_approval_tree'], str(proof['a207_owner_approval']),
                 proof['a207_exposures_sha256'], sealed['targets_json'],
                 when.isoformat(), sealed['digest']))
            c.commit()
            return {'status': 'CANCELLED_ADMIN', 'counts': {k:len(v) for k,v in targets.items()},
                    'real_orders_sent': 0, 'sell_simulated_created': False,
                    'real_routes_used': []}
        except BaseException:
            c.rollback()
            raise


def _a207_batch_seal(proof, candidate):
    targets_json = json.dumps({name: sorted(proof['a207_targets'][name])
                               for name in ('spot','caucion','future')},
                              sort_keys=True, separators=(',', ':'))
    raw = '|'.join((candidate, proof['a207_approval_tree'],
                    str(proof['a207_owner_approval']),
                    proof['a207_exposures_sha256'], targets_json))
    return {'targets_json': targets_json,
            'digest': hashlib.sha256(raw.encode()).hexdigest()}


def _verify_a207_prior_cancel(c, targets, *, proof=None, candidate=None):
    """Check complete prior commit before accepting an idempotent retry."""
    from bt_caucion_paper import (ADMIN_CANCEL_AUTHORITY_A207 as CAUCION_AUTHORITY,
                                  validate_position)
    from rc6_paper_family_lifecycle import FUTURE_ADMIN_CANCEL_AUTHORITY_A207
    for paper_id in targets['spot']:
        p = c.execute('SELECT * FROM paper_positions WHERE paper_id=?', (paper_id,)).fetchone()
        if (not p or p['status'] != 'CANCELLED_ADMIN'
                or p['close_reason'] != 'ADMIN_CANCEL_NO_MARKET_EXECUTION'
                or partition(c, p) != (None, [])):
            raise ValueError('A207_SPOT_PRIOR_CANCEL_INVALID')
        receipt_row = c.execute('SELECT authority FROM paper_admin_cancellations WHERE paper_id=?',
                          (paper_id,)).fetchone()
        if not receipt_row or receipt_row['authority'] != ADMIN_CANCEL_AUTHORITY_A207:
            raise ValueError('A207_SPOT_PRIOR_AUTHORITY_INVALID')
        if c.execute('''SELECT 1 FROM paper_fills WHERE paper_id=?
                        AND side='SELL_SIMULATED' LIMIT 1''', (paper_id,)).fetchone():
            raise ValueError('A207_SPOT_FAKE_SELL_FORBIDDEN')
    for paper_id in targets['caucion']:
        p = c.execute('SELECT * FROM paper_cauciones WHERE paper_id=?', (paper_id,)).fetchone()
        if not p or p['status'] != 'CANCELLED_ADMIN':
            raise ValueError('A207_CAUCION_PRIOR_CANCEL_INVALID')
        receipt_row = c.execute('SELECT * FROM paper_caucion_admin_cancellations WHERE paper_id=?',
                          (paper_id,)).fetchone()
        if not receipt_row or receipt_row['authority'] != CAUCION_AUTHORITY:
            raise ValueError('A207_CAUCION_PRIOR_AUTHORITY_INVALID')
        validate_position(p, cancel_receipt=receipt_row)
        events = c.execute('''SELECT event_type FROM paper_events WHERE paper_id=?
            AND event_type IN ('PAPER_CAUCION_ADMIN_CANCELLED_NO_EXECUTION',
                               'PAPER_CAUCION_MATURED')''', (paper_id,)).fetchall()
        if len(events) != 1 or events[0][0] != 'PAPER_CAUCION_ADMIN_CANCELLED_NO_EXECUTION':
            raise ValueError('A207_CAUCION_TERMINAL_EVENT_INVALID')
    for lifecycle_id in targets['future']:
        p = c.execute('SELECT * FROM paper_future_positions WHERE lifecycle_id=?',
                      (lifecycle_id,)).fetchone()
        parent = c.execute('SELECT * FROM paper_family_lifecycle WHERE lifecycle_id=?',
                           (lifecycle_id,)).fetchone()
        if (not p or p['status'] != 'CLOSED' or
                p['close_reason'] != 'ADMIN_CANCEL_NO_MARKET_EXECUTION' or
                not parent or parent['state'] != 'ADMIN_CANCEL' or
                Decimal(parent['ledger_total']) != 0):
            raise ValueError('A207_FUTURE_PRIOR_CANCEL_INVALID')
        metadata = json.loads(p['metadata_json'])
        if metadata.get('admin_cancel_authority') != FUTURE_ADMIN_CANCEL_AUTHORITY_A207:
            raise ValueError('A207_FUTURE_PRIOR_AUTHORITY_INVALID')
        e = c.execute('SELECT * FROM paper_family_lifecycle_events WHERE event_id=?',
                      (metadata.get('admin_cancel_event_id'),)).fetchone()
        if not e or e['lifecycle_id'] != lifecycle_id or e['to_state'] != 'ADMIN_CANCEL':
            raise ValueError('A207_FUTURE_PRIOR_EVENT_INVALID')
        events = c.execute('''SELECT amount,to_state FROM paper_family_lifecycle_events
            WHERE lifecycle_id=?''', (lifecycle_id,)).fetchall()
        if (sum((Decimal(e['amount']) for e in events), Decimal('0')) != 0
                or len([e for e in events if e['to_state']=='ADMIN_CANCEL']) != 1):
            raise ValueError('A207_FUTURE_PRIOR_CASH_NOT_NEUTRAL')
    if proof is not None:
        if not c.execute('''SELECT 1 FROM sqlite_master WHERE type='table'
                            AND name='paper_admin_reversal_batches' ''').fetchone():
            raise ValueError('A207_BATCH_SEALED_RECEIPT_MISSING')
        sealed = _a207_batch_seal(proof, candidate)
        row = c.execute('''SELECT * FROM paper_admin_reversal_batches
                           WHERE candidate_sha=?''', (candidate,)).fetchone()
        if (not row or row['tree_sha'] != proof['a207_approval_tree']
                or row['owner_comment_id'] != str(proof['a207_owner_approval'])
                or row['exposures_sha256'] != proof['a207_exposures_sha256']
                or row['targets_json'] != sealed['targets_json']
                or row['audit_digest_sha256'] != sealed['digest']):
            raise ValueError('A207_BATCH_SEALED_RECEIPT_MISMATCH')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate-sha', required=True)
    parser.add_argument('--receipt', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--a205-batch', action='store_true',
                        help='use separately authorized versioned EOD spot batch receipt')
    parser.add_argument('--a207-batch', action='store_true',
                        help='full bounded PAPER reversal with exact owner/preflight receipt')
    args = parser.parse_args(argv)
    expected = RECEIPT_ROOT / ('quiescence-' + args.candidate_sha + '.json')
    if Path(args.receipt) != expected:
        parser.error('ADMIN_EXACT_RECEIPT_PATH_REQUIRED')
    if args.a205_batch and args.a207_batch:
        parser.error('ADMIN_ONE_VERSIONED_POLICY_ONLY')
    handler = (execute_a207_batch if args.a207_batch else
               execute_a205_batch if args.a205_batch else execute)
    result = handler(DATABASE, expected, args.candidate_sha, apply=args.apply)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
