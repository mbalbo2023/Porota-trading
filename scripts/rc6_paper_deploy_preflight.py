#!/usr/bin/env python3
"""A205: bounded, redacted, read-only live PAPER exposure preflight.

Run this *before* authorizing a production merge. A position is an actionable
preparation item, not an invitation to skip financial reconciliation. This
module never closes an exposure or modifies the Droplet.
"""

import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import types
import zlib

EXPOSURES = (
    ('spot', 'paper_positions', 'OPEN'),
    ('caucion', 'paper_cauciones', 'OPEN'),
    ('future', 'paper_future_positions', 'ACTIVE'),
)
MAX_OPEN_PER_FAMILY = 32
MAX_SUPPORT_ROWS = 256
DB = Path('/opt/porota-trading/data/paper_v17/observer_v17.db')


class UnsafeBook(ValueError):
    """Unsafe state; never continue a promotion after this classification."""


def _utc_time(value):
    result = datetime.fromisoformat(str(value))
    if result.tzinfo is None or result.utcoffset() is None:
        raise UnsafeBook('BOOK_CLOCK_UNVERIFIED')
    return result.astimezone(timezone.utc)


def _fingerprint(rows, state):
    # The raw identity, prices and amounts stay on the host. Only a digest
    # crosses the public Actions log boundary; an identical retry is stable.
    raw = json.dumps({'rows': rows, 'state': state}, sort_keys=True,
                     separators=(',', ':'), default=str).encode()
    if len(raw) > 256 * 1024:
        raise UnsafeBook('BOOK_SNAPSHOT_BOUND')
    return hashlib.sha256(raw).hexdigest()


def _load_signed_candidate_ledger():
    """Use the hash-pinned candidate's spot validator before it is installed.

    On the local runner no injection is required. On the host, the installed
    ledger may predate A205, so Actions sends exactly these candidate source
    bytes; there is no need to install or mutate a host file.
    """
    encoded = os.environ.get('RC6_A205_SPOT_LEDGER_GZIP_B64')
    if encoded is None:
        return
    expected = os.environ.get('RC6_A205_SPOT_LEDGER_SHA256', '')
    if not (0 < len(encoded) < 120000 and len(expected) == 64 and
            all(v in '0123456789abcdef' for v in expected)):
        raise UnsafeBook('BOOK_CANDIDATE_LEDGER_BOUNDS')
    packed = base64.b64decode(encoded, validate=True)
    if not 0 < len(packed) < 90000:
        raise UnsafeBook('BOOK_CANDIDATE_LEDGER_PACKED_BOUND')
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    source = inflater.decompress(packed, 150000)
    if (not 0 < len(source) < 150000 or not inflater.eof or
            inflater.unconsumed_tail or inflater.unused_data or
            hashlib.sha256(source).hexdigest() != expected):
        raise UnsafeBook('BOOK_CANDIDATE_LEDGER_HASH_RED')
    module = types.ModuleType('cd_spot_ledger')
    exec(compile(source, 'candidate/cd_spot_ledger.py', 'exec'), module.__dict__)
    if not callable(getattr(module, 'plan_open_paper_admin_cancel', None)):
        raise UnsafeBook('BOOK_CANDIDATE_LEDGER_CONTRACT_MISSING')
    sys.modules['cd_spot_ledger'] = module


def open_exposure_rows(c):
    """Snapshot three families on one caller-owned SQLite transaction."""
    counts, data = {}, {}
    for name, table, active_state in EXPOSURES:
        columns = {row['name'] for row in c.execute('PRAGMA table_info("' + table + '")')}
        if 'status' not in columns:
            raise UnsafeBook('BOOK_SCHEMA_MISSING_' + name.upper())
        count = c.execute('SELECT COUNT(*) FROM ' + table + ' WHERE status=?',
                          (active_state,)).fetchone()[0]
        if type(count) is not int or count < 0 or count > MAX_OPEN_PER_FAMILY:
            raise UnsafeBook('BOOK_EXPOSURE_BOUND_' + name.upper())
        rows = [dict(row) for row in c.execute('SELECT * FROM ' + table +
                ' WHERE status=? ORDER BY rowid LIMIT ?',
                (active_state, MAX_OPEN_PER_FAMILY))]
        if len(rows) != count:
            raise UnsafeBook('BOOK_COUNT_DRIFT_' + name.upper())
        required = ({'paper_id'} if name in {'spot', 'caucion'} else
                    {'lifecycle_id', 'margin_reserved', 'last_mark_price'} )
        if rows and not required <= set(rows[0]):
            raise UnsafeBook('BOOK_ACTIVE_SCHEMA_MISSING_' + name.upper())
        counts[name], data[name] = count, rows
    # A PAPER position may be partially sold without its own OPEN row
    # changing. Include related fills, intents, receivables, allocations and
    # settlement marks in the digest so an intervening ledger event cannot
    # pass the exact before/after idempotency check unnoticed.
    support = {}
    relations = (
        ('paper_fills', 'paper_id', [r['paper_id'] for r in data['spot']]),
        ('paper_spot_sales', 'paper_id', [r['paper_id'] for r in data['spot']]),
        ('paper_exit_intents', 'paper_id', [r['paper_id'] for r in data['spot']]),
        ('paper_sale_receivables', 'paper_id', [r['paper_id'] for r in data['spot']]),
        ('paper_admin_cancellations', 'paper_id', [r['paper_id'] for r in data['spot']]),
        ('paper_learning_samples', 'paper_id', [r['paper_id'] for r in data['spot']]),
        ('paper_events', 'paper_id', [r['paper_id'] for r in data['spot'] + data['caucion']]),
        ('paper_caucion_allocations', 'paper_id', [r['paper_id'] for r in data['caucion']]),
        ('paper_future_marks', 'lifecycle_id', [r['lifecycle_id'] for r in data['future']]),
        ('paper_family_lifecycle', 'lifecycle_id', [r['lifecycle_id'] for r in data['future']]),
        ('paper_family_lifecycle_events', 'lifecycle_id', [r['lifecycle_id'] for r in data['future']]),
    )
    for table, column, ids in relations:
        if not ids:
            continue
        columns = {row['name'] for row in c.execute('PRAGMA table_info("' + table + '")')}
        if column not in columns:
            raise UnsafeBook('BOOK_SUPPORT_SCHEMA_MISSING_' + table.upper())
        placeholders = ','.join('?' for _ in ids)
        rows = [dict(row) for row in c.execute(
            'SELECT * FROM ' + table + ' WHERE ' + column + ' IN (' + placeholders +
            ') ORDER BY rowid LIMIT ?', [*ids, MAX_SUPPORT_ROWS + 1])]
        if len(rows) > MAX_SUPPORT_ROWS:
            raise UnsafeBook('BOOK_SUPPORT_BOUND_' + table.upper())
        support[table] = rows
    data['support'] = support
    return counts, data


def inspect_book(path=DB, *, as_of=None, barrier=None):
    """Return non-sensitive counts, classifications and snapshot integrity."""
    from cd_spot_ledger import plan_open_paper_admin_cancel
    path = Path(path).absolute()
    if path.is_symlink() or not path.is_file():
        raise UnsafeBook('BOOK_DB_PATH_UNSAFE')
    now = _utc_time(as_of or datetime.now(timezone.utc).isoformat())
    start = time.monotonic()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True,
                                 timeout=.2)) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA busy_timeout=200')
        c.set_progress_handler(lambda: int(time.monotonic() - start > 3), 1000)
        c.execute('BEGIN')
        state_row = c.execute('SELECT mode,real_orders_sent,process_state,session_state '
                              'FROM observer_state WHERE id=1').fetchone()
        if (not state_row or state_row['mode'] != 'PRODUCTION_PAPER'
                or type(state_row['real_orders_sent']) is not int
                or state_row['real_orders_sent'] != 0):
            raise UnsafeBook('BOOK_PAPER_ZERO_UNVERIFIED')
        state = dict(state_row)
        counts, data = open_exposure_rows(c)
        plans = {}
        reasons = []
        for name, _, _ in EXPOSURES:
            count, rows = counts[name], data[name]
            if name == 'spot':
                planned = 0
                for row in rows:
                    try:
                        plan_open_paper_admin_cancel(c, row['paper_id'], now.isoformat())
                    except (ValueError, TypeError, KeyError, sqlite3.Error):
                        reasons.append('SPOT_REQUIRES_VALID_EXIT_OR_LEDGER_REVIEW')
                    else:
                        planned += 1
                plans[name] = {'admin_void_eod_intact': planned,
                               'unsupported': count - planned}
            elif name == 'caucion':
                # An unmatured funded caucion is not an early sale. A matured
                # position still requires an audited allocation/fee settlement;
                # neither fact authorizes a blind UPDATE here.
                mature = 0
                for row in rows:
                    try:
                        from bt_caucion_paper import validate_position
                        validate_position(row)
                        if _utc_time(row['maturity_at']) <= now:
                            mature += 1
                    except (ValueError, TypeError, KeyError):
                        reasons.append('CAUCION_CONTRACT_REVIEW_REQUIRED')
                plans[name] = {'matured_needs_settlement': mature,
                               'not_yet_matured_or_invalid': count - mature}
                if count:
                    reasons.append('CAUCION_AUDITED_SETTLEMENT_REQUIRED')
            else:
                # Futures carry margin and variation; a safe terminal fill or
                # exchange settlement must be verified before calling close_future.
                plans[name] = {'needs_pit_fill_or_settlement': count}
                if count:
                    reasons.append('FUTURE_MARK_AND_MARGIN_RECONCILIATION_REQUIRED')
        c.rollback()  # close the read-only snapshot; no journal change
    if barrier is not None:
        if not isinstance(barrier, dict) or barrier.get('status') not in {
                'ABSENT', 'IDLE', 'INFLIGHT', 'CONTRACT_ONLY'}:
            raise UnsafeBook('BOOK_BARRIER_UNKNOWN')
        barrier_status = barrier['status']
    else:
        barrier_status = 'NO_VERIFICADO'
    total = sum(counts.values())
    unsupported = (plans['spot']['unsupported'] or counts['future'] or
                   counts['caucion'])
    status = ('BLOCKED_RECONCILIATION' if unsupported else
              'ADMIN_CLOSE_REQUIRED' if total else 'FLAT_GREEN')
    return {
        'contract': 'RC6_A205_PAPER_DEPLOY_PREFLIGHT_V1',
        'read_only': True, 'host_mutations': 0,
        'required_mode': state['mode'], 'real_orders_sent': state['real_orders_sent'],
        'observer_process': state['process_state'],
        'observer_session': state['session_state'],
        'exposures': counts, 'plans': plans,
        'snapshot_sha256': _fingerprint(data, state),
        'exposures_sha256': _fingerprint(data, {}),
        'barrier_status': barrier_status,
        'status': status, 'reasons': sorted(set(reasons)),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', type=Path, default=DB)
    ap.add_argument('--as-of', help='ISO-8601 fixed time for tests, never required on host')
    ap.add_argument('--host-barrier', action='store_true',
                    help='Read but never alter root-owned deploy attempt barrier')
    args = ap.parse_args(argv)
    try:
        _load_signed_candidate_ledger()
        barrier = None
        if args.host_barrier:
            from scripts.rc6_deploy_host_mutex import _read_barrier, LOCK_DIR
            record = _read_barrier(LOCK_DIR)
            barrier = {'status': record['status'] if record else 'ABSENT'}
        result = inspect_book(args.db, as_of=args.as_of, barrier=barrier)
    except (UnsafeBook, sqlite3.Error, OSError, OverflowError, ImportError,
            ValueError, TypeError) as exc:
        code = str(exc) if isinstance(exc, UnsafeBook) else 'BOOK_PROBE_' + type(exc).__name__
        result = {'contract': 'RC6_A205_PAPER_DEPLOY_PREFLIGHT_V1',
                  'read_only': True, 'status': 'BLOCKED_PROBE', 'reason': code}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] in {'FLAT_GREEN', 'ADMIN_CLOSE_REQUIRED'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
