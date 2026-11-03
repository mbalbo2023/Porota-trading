#!/usr/bin/env python3
"""Read-only fingerprint for retiring only an unchanged pre-suspend attempt."""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
import hashlib
import json
import re
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

DB = Path('/opt/porota-trading/data/paper_v17/observer_v17.db')
OWNED = {'porota_production_observer', 'porota_production_dashboard',
         'porota_critical_approval_rc6'}
EXPECTED_DROPLET_ID = '594077619'
EXPECTED_REGION = 'nyc1'
HOST_PROBE_BUDGET_SECONDS = 30
SQL_BUDGET_SECONDS = 2


class FingerprintRejected(RuntimeError):
    pass


def command(*args, allowed=(0,), timeout=5):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode not in allowed or len(result.stdout) > 1024 * 1024:
        raise FingerprintRejected('HOST_INVENTORY_FAILED')
    return result.stdout.strip()


def metadata(key, opener):
    with opener.open('http://169.254.169.254/metadata/v1/' + key,
                     timeout=3) as response:
        if response.status != 200:
            raise FingerprintRejected('DROPLET_METADATA_RED')
        raw = response.read(65)
    if not raw or len(raw) > 64:
        raise FingerprintRejected('DROPLET_METADATA_RED')
    return raw.decode('ascii').strip()


def timer_names(raw):
    policy = json.loads(raw)
    names = sorted(Path(path).name for path, spec in policy['units'].items()
                   if spec['role'] == 'ACTIVE_TIMER' and spec['active'] is True)
    if len(names) != 16 or len(set(names)) != 16 or any(
            re.fullmatch(r'porota-[a-z0-9-]+\.timer', name) is None or
            'ppi' in name.split('-') and 'watch' in name.split('-')
            for name in names):
        raise FingerprintRejected('TIMER_POLICY_UNSAFE')
    return names


def _a207_exposure_digest(db):
    """Read full bounded PAPER position/support bytes, never log identifiers."""
    collections = {}
    identifiers = {'spot':[], 'caucion':[], 'future':[]}
    for family, table, state, identity in (
            ('spot','paper_positions','OPEN','paper_id'),
            ('caucion','paper_cauciones','OPEN','paper_id'),
            ('future','paper_future_positions','ACTIVE','lifecycle_id')):
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute('SELECT * FROM ' + table +
                    ' WHERE status=? ORDER BY rowid LIMIT 33', (state,))]
        if len(rows) > 32 or any(identity not in row for row in rows):
            raise FingerprintRejected('A207_EXPOSURE_SCHEMA_OR_BOUND')
        identifiers[family] = [r[identity] for r in rows]
        collections[family] = rows
    if not any(identifiers.values()):
        raise FingerprintRejected('A207_OPEN_EXPOSURE_NO_LONGER_PRESENT')
    for table, key, values in (
            ('paper_fills','paper_id',identifiers['spot']),
            ('paper_spot_sales','paper_id',identifiers['spot']),
            ('paper_exit_intents','paper_id',identifiers['spot']),
            ('paper_sale_receivables','paper_id',identifiers['spot']),
            ('paper_admin_cancellations','paper_id',identifiers['spot']),
            ('paper_learning_samples','paper_id',identifiers['spot']),
            ('paper_events','paper_id',identifiers['spot']+identifiers['caucion']),
            ('paper_caucion_allocations','paper_id',identifiers['caucion']),
            ('paper_future_marks','lifecycle_id',identifiers['future']),
            ('paper_family_lifecycle','lifecycle_id',identifiers['future']),
            ('paper_family_lifecycle_events','lifecycle_id',identifiers['future'])):
        if not values:
            continue
        query = ('SELECT * FROM ' + table + ' WHERE ' + key + ' IN (' +
                 ','.join('?' for _ in values) + ') ORDER BY rowid LIMIT 257')
        rows = [dict(row) for row in db.execute(query, values)]
        if len(rows) > 256:
            raise FingerprintRejected('A207_EXPOSURE_SUPPORT_BOUND')
        collections[table] = rows
    raw = json.dumps(collections, sort_keys=True, separators=(',', ':'),
                     default=str).encode()
    if len(raw) > 256 * 1024:
        raise FingerprintRejected('A207_EXPOSURE_SNAPSHOT_BOUND')
    return hashlib.sha256(raw).hexdigest()


def fingerprint(policy_raw, *, opener=None, a207_authorized_open=False):
    host_deadline = time.monotonic() + HOST_PROBE_BUDGET_SECONDS
    def bounded_command(*args, allowed=(0,)):
        remaining = host_deadline - time.monotonic()
        if remaining <= 0:
            raise FingerprintRejected('HOST_PROBE_BUDGET_EXCEEDED')
        return command(*args, allowed=allowed, timeout=min(5, remaining))

    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if (metadata('id', opener) != EXPECTED_DROPLET_ID or
            metadata('region', opener) != EXPECTED_REGION):
        raise FingerprintRejected('DROPLET_IDENTITY_CHANGED')
    with closing(sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True, timeout=.5)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA busy_timeout=200')
        sql_deadline = min(host_deadline, time.monotonic() + SQL_BUDGET_SECONDS)
        db.set_progress_handler(lambda: int(time.monotonic() >= sql_deadline), 1000)
        row = db.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
        if row != ('PRODUCTION_PAPER', 0) or type(row[1]) is not int:
            raise FingerprintRejected('PAPER_SAFETY_RED')
        columns = {item[1] for item in db.execute('PRAGMA table_info(paper_family_lifecycle)')}
        if not {'family', 'state'} <= columns:
            raise FingerprintRejected('FCI_LIFECYCLE_SCHEMA_UNVERIFIED')
        if db.execute("SELECT 1 FROM paper_family_lifecycle WHERE family='FCI' AND state!='REDEEMED' LIMIT 1").fetchone():
            raise FingerprintRejected('FCI_PAPER_NAV_LIABILITY_NOT_FLAT')
        positions = tuple(int(db.execute('SELECT 1 FROM ' + table +
                                         ' WHERE status=? LIMIT 1',
                                         (status,)).fetchone() is not None)
                          for table, status in (('paper_positions', 'OPEN'),
                                                ('paper_cauciones', 'OPEN'),
                                                ('paper_future_positions', 'ACTIVE')))
        a207_book = None
        if positions != (0, 0, 0):
            if not a207_authorized_open:
                raise FingerprintRejected('PAPER_POSITION_NOT_FLAT')
            a207_book = _a207_exposure_digest(db)
        elif a207_authorized_open:
            raise FingerprintRejected('A207_OPEN_EXPOSURE_NO_LONGER_PRESENT')
    ids = bounded_command('docker', 'ps', '-aq', '--no-trunc').splitlines()
    if len(ids) > 128 or any(re.fullmatch(r'[a-f0-9]{12,64}', cid) is None for cid in ids):
        raise FingerprintRejected('CONTAINER_LIST_INVALID')
    containers = []
    for cid in ids:
        raw = bounded_command('docker', 'inspect', '--format',
                      '{{json .Id}}|{{json .Name}}|{{json .Image}}|{{json .State.Status}}|{{json .RestartCount}}|{{json .State.StartedAt}}',
                      cid)
        fields = raw.split('|')
        if len(fields) != 6:
            raise FingerprintRejected('CONTAINER_RECORD_INVALID')
        values = tuple(json.loads(field) for field in fields)
        if (values[0] != cid or not isinstance(values[1], str) or
                re.fullmatch(r'sha256:[a-f0-9]{64}', values[2]) is None or
                values[3] not in {'running', 'exited', 'created', 'restarting', 'paused'} or
                type(values[4]) is not int or not isinstance(values[5], str)):
            raise FingerprintRejected('CONTAINER_RECORD_INVALID')
        containers.append(values)
    if not OWNED <= {row[1].lstrip('/') for row in containers}:
        raise FingerprintRejected('OWNED_CONTAINER_MISSING')
    timers = []
    for name in timer_names(policy_raw):
        active = bounded_command('systemctl', 'is-active', name, allowed=(0, 3))
        enabled = bounded_command('systemctl', 'is-enabled', name, allowed=(0, 1, 3))
        service = bounded_command('systemctl', 'show', name, '-p', 'Unit', '--value')
        if (active not in {'active', 'inactive', 'failed'} or
                enabled not in {'enabled', 'disabled', 'masked'} or
                re.fullmatch(r'porota-[a-z0-9-]+\.service', service) is None):
            raise FingerprintRejected('TIMER_STATE_UNKNOWN')
        service_state = bounded_command('systemctl', 'is-active', service,
                                        allowed=(0, 3))
        if service_state not in {'active', 'inactive', 'failed',
                                 'activating', 'deactivating'}:
            raise FingerprintRejected('TIMER_SERVICE_STATE_UNKNOWN')
        timers.append((name, active, enabled, service, service_state))
    units = bounded_command('systemctl', 'list-unit-files', '--no-legend').splitlines()
    ppi = []
    for line in units:
        tokens = line.split()
        if tokens and {'ppi', 'watch'} <= set(re.split(r'[-_.@]', tokens[0].lower())):
            name = tokens[0]
            ppi.append((name, bounded_command('systemctl', 'is-active', name,
                                               allowed=(0, 3)),
                        bounded_command('systemctl', 'is-enabled', name,
                                        allowed=(0, 1, 3))))
    state = {'droplet_id': EXPECTED_DROPLET_ID, 'region': EXPECTED_REGION,
             'mode': row[0], 'real_orders_sent': row[1],
             'positions': positions, 'a207_exposure_digest': a207_book,
             'containers': sorted(containers),
             'timers': timers, 'ppi_watch': sorted(ppi)}
    digest = hashlib.sha256(json.dumps(state, sort_keys=True,
                                       separators=(',', ':')).encode()).hexdigest()
    return digest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy-b64', required=True)
    parser.add_argument('--expected-fingerprint')
    parser.add_argument('--a207-authorized-open', action='store_true')
    args = parser.parse_args(argv)
    try:
        raw = base64.b64decode(args.policy_b64, validate=True)
        if not 0 < len(raw) < 100_000:
            raise FingerprintRejected('POLICY_SIZE_RED')
        value = fingerprint(raw, a207_authorized_open=args.a207_authorized_open)
        if args.expected_fingerprint is not None:
            if not re.fullmatch('[0-9a-f]{64}', args.expected_fingerprint) or value != args.expected_fingerprint:
                raise FingerprintRejected('HOST_FINGERPRINT_CHANGED')
    except (FingerprintRejected, OSError, ValueError, TypeError, KeyError,
            sqlite3.Error, subprocess.TimeoutExpired, UnicodeError) as exc:
        print('RC6_PRE_SUSPEND_FINGERPRINT=RED|' + (
            str(exc) if isinstance(exc, FingerprintRejected) else type(exc).__name__))
        return 2
    print('RC6_PRE_SUSPEND_FINGERPRINT=GREEN|' + value)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
