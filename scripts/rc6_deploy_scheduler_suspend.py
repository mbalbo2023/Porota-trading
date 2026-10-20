#!/usr/bin/env python3
"""Temporarily pause only versioned RC6 timers for one intraday Deploy V2.

The receipt is written before each systemd mutation. Restore is idempotent on
failure; the deployed host control plane normally restores the timers.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

ROOT = Path('/opt/porota-trading/data/deploy')


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    return result.stdout.strip(), result.returncode


def ppi_snapshot():
    output, code = command('systemctl', 'list-unit-files', '--no-legend')
    if code:
        raise ValueError('SCHEDULER_PPI_ENUMERATION_FAILED')
    names = sorted(line.split()[0] for line in output.splitlines()
                   if {'ppi', 'watch'} <= set(re.split(r'[-_.@]', line.split()[0].lower())))
    return [[name, command('systemctl', 'is-enabled', name)[0],
             command('systemctl', 'is-active', name)[0]] for name in names]


def timer_policy(raw):
    policy = json.loads(raw)
    rows = policy['units']
    names = sorted(Path(path).name for path, spec in rows.items()
                   if spec['role'] == 'ACTIVE_TIMER' and spec['active'] is True)
    if (not names or len(names) != len(set(names)) or
            any(not re.fullmatch(r'porota-[a-z0-9-]+\.timer', name)
                for name in names)):
        raise ValueError('SCHEDULER_POLICY_UNSAFE')
    return names


def persist(path, payload):
    fd, temp = tempfile.mkstemp(prefix='.scheduler-suspend-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(payload, stream, sort_keys=True, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def run(candidate, action, policy_b64):
    if not re.fullmatch(r'[0-9a-f]{40}', candidate):
        raise ValueError('SCHEDULER_CANDIDATE_INVALID')
    raw = base64.b64decode(policy_b64, validate=True)
    if len(raw) > 100_000:
        raise ValueError('SCHEDULER_POLICY_BOUND')
    names = timer_policy(raw)
    digest = hashlib.sha256(raw).hexdigest()
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / ('scheduler-suspend-' + candidate + '.json')
    if ROOT.is_symlink() or path.is_symlink():
        raise ValueError('SCHEDULER_RECEIPT_ALIAS')
    before = ppi_snapshot()
    receipt = json.loads(path.read_text()) if path.exists() else None
    if action == 'suspend':
        if receipt is not None:
            raise ValueError('SCHEDULER_RECEIPT_EXISTS')
        for name in names:
            active, _ = command('systemctl', 'is-active', name)
            enabled, _ = command('systemctl', 'is-enabled', name)
            if (active, enabled) != ('active', 'enabled'):
                raise ValueError('SCHEDULER_EXPECTED_TIMER_NOT_ACTIVE:' + name)
        receipt = {'candidate_sha': candidate, 'policy_sha256': digest,
                   'timers': names, 'ppi_before': before, 'attempted': [],
                   'status': 'SUSPENDING', 'recorded_at': datetime.now(timezone.utc).isoformat()}
        persist(path, receipt)
        for name in names:
            receipt['attempted'].append(name)
            persist(path, receipt)
            _, code = command('systemctl', 'stop', name)
            if code or command('systemctl', 'is-active', name)[0] != 'inactive':
                raise ValueError('SCHEDULER_STOP_FAILED:' + name)
        receipt['status'] = 'SUSPENDED'
        persist(path, receipt)
        # Timer stops prevent new invocations; allow already running jobs to
        # finish naturally. Do not kill work outside the observer containers.
        deadline = time.monotonic() + 120
        while True:
            active = []
            for name in names:
                service, code = command('systemctl', 'show', name, '-p', 'Unit', '--value')
                if code or not service.endswith('.service'):
                    raise ValueError('SCHEDULER_SERVICE_IDENTITY_UNKNOWN:' + name)
                status, _ = command('systemctl', 'is-active', service)
                if status in ('active', 'activating', 'reloading', 'deactivating'):
                    active.append(service)
            if not active:
                break
            if time.monotonic() >= deadline:
                raise ValueError('SCHEDULER_JOB_STILL_ACTIVE:' + ','.join(active))
            time.sleep(5)
    else:
        if receipt is None:
            if action == 'restore':
                return {'status': 'NO_SUSPENSION_RECEIPT'}
            raise ValueError('SCHEDULER_RECEIPT_MISSING')
        if (receipt.get('candidate_sha') != candidate or
                receipt.get('policy_sha256') != digest or
                receipt.get('timers') != names or
                receipt.get('ppi_before') != before or
                not set(receipt.get('attempted', [])) <= set(names)):
            raise ValueError('SCHEDULER_RECEIPT_IDENTITY_CHANGED')
        if action == 'restore':
            for name in receipt['attempted']:
                _, code = command('systemctl', 'start', name)
                if code or command('systemctl', 'is-active', name)[0] != 'active':
                    raise ValueError('SCHEDULER_RESTORE_FAILED:' + name)
        elif action == 'verify':
            if receipt.get('status') != 'SUSPENDED':
                raise ValueError('SCHEDULER_NOT_SUSPENDED')
            for name in names:
                if command('systemctl', 'is-active', name)[0] != 'active':
                    raise ValueError('SCHEDULER_NOT_RESTORED:' + name)
        else:
            raise ValueError('SCHEDULER_ACTION_INVALID')
        receipt['status'] = 'RESTORED'
        persist(path, receipt)
    if ppi_snapshot() != before:
        raise ValueError('SCHEDULER_PPI_WATCH_CHANGED')
    print(json.dumps({'status': receipt['status'], 'candidate_sha': candidate,
                      'timer_count': len(names), 'ppi_watch_accessed': False}, sort_keys=True))
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate-sha', required=True)
    parser.add_argument('--action', choices=['suspend', 'restore', 'verify'], required=True)
    parser.add_argument('--policy-b64', required=True)
    args = parser.parse_args()
    run(args.candidate_sha, args.action, args.policy_b64)


if __name__ == '__main__':
    main()
