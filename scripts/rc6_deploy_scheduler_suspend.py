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
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path('/opt/porota-trading/data/deploy')


def require_owned_running():
    """Never arm RC6 timers while a failed deploy leaves the observer stopped."""
    for name in ('porota_production_observer', 'porota_production_dashboard'):
        output, code = command('docker', 'inspect', '-f', '{{.State.Status}}', name)
        if code or output != 'running':
            raise ValueError('SCHEDULER_OWNED_RUNTIME_STOPPED')
    path = ROOT.parent / 'paper_v17/observer_v17.db'
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2) as db:
        db.execute('PRAGMA query_only=ON')
        if db.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone() != ('PRODUCTION_PAPER', 0):
            raise ValueError('SCHEDULER_PAPER_ZERO_REQUIRED')


def resolve_open_recovery():
    """Choose by current Docker state; a receipt alone does not prove liveness."""
    observed = []
    for name in ('porota_production_observer', 'porota_production_dashboard'):
        status, code = command('docker', 'inspect', '-f', '{{.State.Status}}', name)
        if code:
            raise ValueError('SCHEDULER_OPEN_RECOVERY_IDENTITY_UNKNOWN')
        observed.append(status)
    if observed == ['running', 'running']:
        return 'restore'
    if observed == ['exited', 'exited']:
        return 'hold-open-flat'
    raise ValueError('SCHEDULER_OPEN_RECOVERY_MIXED_OR_UNKNOWN')


def require_exact_stopped_open_flat(candidate):
    """Keep timers paused only under the prior clean OPEN PAPER stop proof."""
    sys.path.insert(0, '/opt/porota-trading')
    from scripts import rc6_deploy_quiescence as quiescence
    originals = {name: quiescence.inspect(name) for name in quiescence.OWNED}
    if any(value['State']['Status'] != 'exited' for value in originals.values()):
        raise ValueError('SCHEDULER_STOPPED_OPEN_IDENTITY_RED')
    path = quiescence.ROOT/'data/deploy'/('quiescence-' + candidate + '.json')
    if path.is_symlink() or (path.exists() and path.stat().st_size > 65536):
        raise ValueError('SCHEDULER_STOPPED_OPEN_RECEIPT_UNSAFE')
    own = json.loads(path.read_bytes()) if path.is_file() else None
    if own and own.get('installation_started') is True:
        if (own.get('candidate_sha') != candidate or
                own.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED' or
                own.get('inflight') != 'ABSENT_OWNED_RUNTIME_STOPPED' or
                set(own.get('containers', {})) != set(quiescence.OWNED)):
            raise ValueError('SCHEDULER_STOPPED_OPEN_INSTALL_RECEIPT_RED')
        receipt = own
        for name, value in originals.items():
            expected = receipt['containers'][name]
            if value['Id'] != expected.get('id') or value['Image'] != expected.get('image'):
                raise ValueError('SCHEDULER_STOPPED_OPEN_INSTALL_IDENTITY_RED')
    else:
        receipt = quiescence.adopt_stopped_after_failed_install(
            candidate, quiescence.ROOT/'data/deploy', originals,
            allow_market_open_suspend=True)
    quiescence.exact_clean_stopped(receipt)
    if (receipt.get('status') not in {'DRAINED', 'FIX_FORWARD_VALIDATION_REQUIRED'} or
            type(receipt.get('real_orders_sent')) is not int or
            receipt['real_orders_sent'] != 0 or
            receipt.get('ppi_watch_accessed') is not False or
            receipt.get('market_open_suspended') is not True or
            receipt.get('open_paper_carryover') is not False or
            receipt.get('spot_carryover') is not None or
            receipt.get('caucion_carryover') is not None or
            receipt.get('paused_postclose') is not False or
            receipt.get('intraday_caucion') is not False or
            quiescence.admit(stopped=True, allow_market_open_suspend=True,
                             stopped_receipt=receipt) not in ('RUNNING', 'STOPPED')):
        raise ValueError('SCHEDULER_STOPPED_OPEN_FLAT_RED')
    quiescence.critical_admission()


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
                or {'ppi', 'watch'} <= set(name.split('-'))
                or 'critical' in name.split('-') for name in names)):
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
            if (receipt.get('candidate_sha') != candidate or
                    receipt.get('policy_sha256') != digest or
                    receipt.get('timers') != names or
                    receipt.get('ppi_before') != before or
                    receipt.get('status') != 'SUSPENDED' or
                    set(receipt.get('attempted', [])) != set(names)):
                raise ValueError('SCHEDULER_RECEIPT_EXISTS')
            for name in names:
                if command('systemctl', 'is-active', name)[0] != 'inactive':
                    raise ValueError('SCHEDULER_PREPAUSE_TIMER_CHANGED:' + name)
                service, code = command('systemctl', 'show', name, '-p', 'Unit', '--value')
                if code or not service.endswith('.service') or command('systemctl', 'is-active', service)[0] in (
                        'active', 'activating', 'reloading', 'deactivating'):
                    raise ValueError('SCHEDULER_PREPAUSE_JOB_ACTIVE:' + name)
            print(json.dumps({'status': 'SUSPENDED', 'candidate_sha': candidate,
                              'timer_count': len(names), 'prepaused_timers_adopted': True,
                              'ppi_watch_mutated': False, 'ppi_watch_observed_read_only': True}, sort_keys=True))
            return receipt
        states = [(command('systemctl', 'is-active', name)[0],
                   command('systemctl', 'is-enabled', name)[0]) for name in names]
        if all(state == ('inactive', 'enabled') for state in states):
            # A failed installation can leave the exact owned timers paused
            # under a prior candidate receipt. Adopt only its latest durable
            # proof after checking every timer and already-running job.
            paths = list(ROOT.glob('scheduler-suspend-*.json'))
            if len(paths) > 128 or any(p.is_symlink() or p.stat().st_size > 65536 for p in paths):
                raise ValueError('SCHEDULER_PRIOR_RECEIPT_INVENTORY_UNSAFE')
            eligible = []
            for prior_path in paths:
                prior = json.loads(prior_path.read_text())
                if (prior.get('status') == 'SUSPENDED' and
                        prior.get('policy_sha256') == digest and
                        prior.get('timers') == names and
                        prior.get('ppi_before') == before and
                        set(prior.get('attempted', [])) == set(names) and
                        prior_path.name == 'scheduler-suspend-' + str(prior.get('candidate_sha')) + '.json'):
                    eligible.append((prior['recorded_at'], prior_path, prior))
            eligible.sort(key=lambda row: row[0])
            if not eligible or (len(eligible) > 1 and eligible[-1][0] == eligible[-2][0]):
                raise ValueError('SCHEDULER_PRIOR_PAUSE_UNVERIFIED')
            for name in names:
                service, code = command('systemctl', 'show', name, '-p', 'Unit', '--value')
                if code or not service.endswith('.service') or command('systemctl', 'is-active', service)[0] in (
                        'active', 'activating', 'reloading', 'deactivating'):
                    raise ValueError('SCHEDULER_PREPAUSE_JOB_ACTIVE:' + name)
            prior_path = eligible[-1][1]
            receipt = dict(eligible[-1][2], candidate_sha=candidate,
                           paused_from=eligible[-1][2]['candidate_sha'],
                           prior_receipt_sha256=hashlib.sha256(prior_path.read_bytes()).hexdigest(),
                           recorded_at=datetime.now(timezone.utc).isoformat())
            persist(path, receipt)
            print(json.dumps({'status':'SUSPENDED','candidate_sha':candidate,
                              'timer_count':len(names),'prepaused_timers_adopted':True,
                              'ppi_watch_mutated':False},sort_keys=True))
            return receipt
        for name, state in zip(names, states):
            if state != ('active', 'enabled'):
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
        if action == 'recover-open':
            action = resolve_open_recovery()
        if action == 'hold-open-flat':
            if (receipt.get('status') != 'SUSPENDED' or
                    set(receipt.get('attempted', [])) != set(names)):
                raise ValueError('SCHEDULER_OPEN_HOLD_RECEIPT_RED')
            for name in names:
                if (command('systemctl', 'is-active', name)[0] != 'inactive' or
                        command('systemctl', 'is-enabled', name)[0] != 'enabled'):
                    raise ValueError('SCHEDULER_OPEN_HOLD_TIMER_CHANGED:' + name)
                service, code = command('systemctl', 'show', name, '-p', 'Unit', '--value')
                if (code or not service.endswith('.service') or
                        command('systemctl', 'is-active', service)[0] in
                        ('active', 'activating', 'reloading', 'deactivating')):
                    raise ValueError('SCHEDULER_OPEN_HOLD_JOB_ACTIVE:' + name)
            require_exact_stopped_open_flat(candidate)
            if ppi_snapshot() != before:
                raise ValueError('SCHEDULER_PPI_WATCH_CHANGED')
            print(json.dumps({'status': 'STOPPED_OPEN_HELD', 'candidate_sha': candidate,
                              'timer_count': len(names), 'ppi_watch_accessed': False}, sort_keys=True))
            return receipt
        if action == 'restore':
            require_owned_running()
            for name in receipt['attempted']:
                _, code = command('systemctl', 'start', name)
                if code or command('systemctl', 'is-active', name)[0] != 'active':
                    raise ValueError('SCHEDULER_RESTORE_FAILED:' + name)
        elif action == 'verify':
            # The preceding restore action durably transitions the exact
            # receipt before this independent verification step runs.
            if receipt.get('status') != 'RESTORED':
                raise ValueError('SCHEDULER_RESTORE_RECEIPT_MISSING')
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
    parser.add_argument('--action', choices=['suspend', 'restore', 'verify',
                                           'hold-open-flat', 'recover-open'], required=True)
    parser.add_argument('--policy-b64', required=True)
    args = parser.parse_args()
    run(args.candidate_sha, args.action, args.policy_b64)


if __name__ == '__main__':
    main()
