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
import urllib.request
from pathlib import Path

DB = Path('/opt/porota-trading/data/paper_v17/observer_v17.db')
OWNED = {'porota_production_observer', 'porota_production_dashboard',
         'porota_critical_approval_rc6'}
EXPECTED_DROPLET_ID = '594077619'
EXPECTED_REGION = 'nyc1'


class FingerprintRejected(RuntimeError):
    pass


def command(*args, allowed=(0,)):
    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
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


def fingerprint(policy_raw, *, opener=None):
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if (metadata('id', opener) != EXPECTED_DROPLET_ID or
            metadata('region', opener) != EXPECTED_REGION):
        raise FingerprintRejected('DROPLET_IDENTITY_CHANGED')
    with closing(sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True, timeout=.5)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA busy_timeout=500')
        row = db.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
        if row != ('PRODUCTION_PAPER', 0) or type(row[1]) is not int:
            raise FingerprintRejected('PAPER_SAFETY_RED')
        positions = tuple(db.execute('SELECT COUNT(*) FROM ' + table + ' WHERE status=?',
                                     (status,)).fetchone()[0]
                          for table, status in (('paper_positions', 'OPEN'),
                                                ('paper_cauciones', 'OPEN'),
                                                ('paper_future_positions', 'ACTIVE')))
        if positions != (0, 0, 0):
            raise FingerprintRejected('PAPER_POSITION_NOT_FLAT')
    ids = command('docker', 'ps', '-aq', '--no-trunc').splitlines()
    if len(ids) > 128 or any(re.fullmatch(r'[a-f0-9]{12,64}', cid) is None for cid in ids):
        raise FingerprintRejected('CONTAINER_LIST_INVALID')
    containers = []
    for cid in ids:
        raw = command('docker', 'inspect', '--format',
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
        active = command('systemctl', 'is-active', name, allowed=(0, 3))
        enabled = command('systemctl', 'is-enabled', name, allowed=(0, 1, 3))
        service = command('systemctl', 'show', name, '-p', 'Unit', '--value')
        if (active not in {'active', 'inactive', 'failed'} or
                enabled not in {'enabled', 'disabled', 'masked'} or
                re.fullmatch(r'porota-[a-z0-9-]+\.service', service) is None):
            raise FingerprintRejected('TIMER_STATE_UNKNOWN')
        service_state = command('systemctl', 'is-active', service, allowed=(0, 3))
        if service_state not in {'inactive', 'failed'}:
            raise FingerprintRejected('TIMER_SERVICE_WRITER_ACTIVE')
        timers.append((name, active, enabled, service, service_state))
    units = command('systemctl', 'list-unit-files', '--no-legend').splitlines()
    ppi = []
    for line in units:
        tokens = line.split()
        if tokens and {'ppi', 'watch'} <= set(re.split(r'[-_.@]', tokens[0].lower())):
            name = tokens[0]
            ppi.append((name, command('systemctl', 'is-active', name, allowed=(0, 3)),
                        command('systemctl', 'is-enabled', name, allowed=(0, 1, 3))))
    state = {'droplet_id': EXPECTED_DROPLET_ID, 'region': EXPECTED_REGION,
             'mode': row[0], 'real_orders_sent': row[1],
             'positions': positions, 'containers': sorted(containers),
             'timers': timers, 'ppi_watch': sorted(ppi)}
    digest = hashlib.sha256(json.dumps(state, sort_keys=True,
                                       separators=(',', ':')).encode()).hexdigest()
    return digest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy-b64', required=True)
    parser.add_argument('--expected-fingerprint')
    args = parser.parse_args(argv)
    try:
        raw = base64.b64decode(args.policy_b64, validate=True)
        if not 0 < len(raw) < 100_000:
            raise FingerprintRejected('POLICY_SIZE_RED')
        value = fingerprint(raw)
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
