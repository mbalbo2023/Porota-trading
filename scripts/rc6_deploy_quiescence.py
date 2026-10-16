#!/usr/bin/env python3
"""Durable CLOSED/PAPER admission and drain before candidate host writes.

Recovery restarts the original containers only before installation begins.
After that boundary the receipt explicitly requires fix-forward validation.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import time

ROOT = Path('/opt/porota-trading')
OWNED = ('porota_production_observer', 'porota_production_dashboard')


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=55)
    if result.returncode:
        raise RuntimeError('QUIESCENCE_COMMAND_FAILED:' + args[1])
    return result.stdout


def inspect(name):
    return json.loads(command(['docker', 'inspect', name]))[0]


def lifecycle(value):
    return {key: value.get('State', {}).get(key) for key in
            ('Status', 'StartedAt', 'FinishedAt', 'ExitCode', 'OOMKilled')}


def clean_stop(before, after):
    """Distinguish a retained historical OOM flag from this clean stop.

    A new/unknown OOM or any nonzero exit remains blocking. Docker may retain
    OOMKilled across starts; a fresh clean ExitCode alone does not erase its
    history. Accept that history only when the prior lifecycle was verified.
    """
    old, new = lifecycle(before), lifecycle(after)
    if before['Id'] != after['Id'] or before['Image'] != after['Image']:
        raise ValueError('QUIESCENCE_STOP_IDENTITY_CHANGED')
    if new['Status'] != 'exited': raise ValueError('QUIESCENCE_STOP_NOT_EXITED')
    if type(new['ExitCode']) is not int or new['ExitCode'] != 0:
        raise ValueError('QUIESCENCE_STOP_NONZERO_OR_UNKNOWN_EXIT')
    if new['OOMKilled'] is False: return 'CLEAN_EXIT'
    try:
        started = datetime.fromisoformat(old['StartedAt'].replace('Z', '+00:00'))
        previous_finish = datetime.fromisoformat(old['FinishedAt'].replace('Z', '+00:00'))
        finished = datetime.fromisoformat(new['FinishedAt'].replace('Z', '+00:00'))
        if (old['Status'] == 'running' and old['OOMKilled'] is True and new['OOMKilled'] is True
                and type(old['ExitCode']) is int and old['ExitCode'] == 0
                and new['StartedAt'] == old['StartedAt'] and previous_finish.year >= 2020
                and previous_finish < started <= finished <= datetime.now(timezone.utc)):
            return 'INHERITED_OOM_FLAG_CLEAN_EXIT'
    except (AttributeError, TypeError, ValueError): pass
    raise ValueError('QUIESCENCE_STOP_OOM_NEW_OR_UNVERIFIED')


def admit(*, stopped=False):
    path = ROOT / 'data/paper_v17/observer_v17.db'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)) as c:
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA busy_timeout=200')
        end = time.monotonic() + 2
        c.set_progress_handler(lambda: int(time.monotonic() > end), 1000)
        mode, orders, process, session, stamp = c.execute(
            'SELECT mode,real_orders_sent,process_state,session_state,heartbeat_at '
            'FROM observer_state WHERE id=1').fetchone()
        if mode != 'PRODUCTION_PAPER' or type(orders) is not int or orders != 0:
            raise ValueError('QUIESCENCE_PAPER_REQUIRED')
        allowed_processes = {'WAITING_MARKET', 'STOPPED'} if stopped else {'WAITING_MARKET'}
        if process not in allowed_processes or session not in {'CLOSED', 'MARKET_CLOSED'}:
            raise ValueError('QUIESCENCE_CLOSED_REQUIRED')
        if not stopped:
            heartbeat = datetime.fromisoformat(stamp)
            if heartbeat.tzinfo is None or not 0 <= (datetime.now(timezone.utc) - heartbeat).total_seconds() <= 180:
                raise ValueError('QUIESCENCE_HEARTBEAT_UNVERIFIED')
        for table, state in (('paper_positions', 'OPEN'), ('paper_cauciones', 'OPEN'),
                             ('paper_future_positions', 'ACTIVE')):
            # Missing schemas and unknown state fail rather than imply zero.
            if c.execute('SELECT 1 FROM ' + table + ' WHERE status=? LIMIT 1', (state,)).fetchone():
                raise ValueError('QUIESCENCE_OPEN_POSITION')


def persist(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.quiescence-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def run(candidate, action):
    if not re.fullmatch('[0-9a-f]{40}', candidate):
        raise ValueError('QUIESCENCE_CANDIDATE_INVALID')
    folder = ROOT / 'data/deploy'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ('quiescence-' + candidate + '.json')
    if folder.is_symlink() or path.is_symlink(): raise ValueError('QUIESCENCE_PATH_ALIAS')
    with (folder / '.deploy-quiescence.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = json.loads(path.read_text()) if path.exists() else None
        if action == 'drain':
            if receipt is not None: raise ValueError('QUIESCENCE_EXISTING_RECEIPT')
            admit()
            originals = {name: inspect(name) for name in OWNED}
            if any(v['State']['Status'] != 'running' for v in originals.values()):
                raise ValueError('QUIESCENCE_RUNTIME_NOT_RUNNING')
            receipt = {'candidate_sha': candidate, 'status': 'DRAINING',
                       'installation_started': False, 'stop_attempted': [],
                       'stop_evidence': {},
                       'containers': {n: {'id': v['Id'], 'image': v['Image']} for n, v in originals.items()},
                       'real_orders_sent': 0, 'ppi_watch_accessed': False}
            persist(path, receipt)
            for name in OWNED:
                # Durable intent precedes the command: a client timeout may
                # occur after Docker actually stopped the container.
                receipt['stop_attempted'].append(name); persist(path, receipt)
                command(['docker', 'stop', '--time', '45', name])
                value = inspect(name)
                receipt['stop_evidence'][name] = {'before': lifecycle(originals[name]), 'after': lifecycle(value)}
                persist(path, receipt)
                print(json.dumps({'container': name, 'stop_evidence': receipt['stop_evidence'][name]}), flush=True)
                classification = clean_stop(originals[name], value)
                receipt['stop_evidence'][name]['classification'] = classification
                persist(path, receipt)
            admit(stopped=True)
            receipt.update(status='DRAINED', inflight='ABSENT_OWNED_RUNTIME_STOPPED')
        elif action == 'install':
            if not receipt or receipt['status'] != 'DRAINED': raise ValueError('QUIESCENCE_NOT_DRAINED')
            for name, expected in receipt['containers'].items():
                value = inspect(name)
                if value['Id'] != expected['id'] or value['State']['Status'] != 'exited':
                    raise ValueError('QUIESCENCE_CHANGED_BEFORE_INSTALL')
            admit(stopped=True)
            receipt.update(status='HOST_INSTALLATION_STARTED', installation_started=True)
        elif action == 'recover':
            if not receipt: return {'status': 'NO_DRAIN_RECEIPT'}
            if receipt['installation_started']:
                receipt['status'] = 'FIX_FORWARD_VALIDATION_REQUIRED'
            else:
                failures = []
                for name in receipt['stop_attempted']:
                    try:
                        value = inspect(name); expected = receipt['containers'][name]
                        if value['Id'] != expected['id'] or value['Image'] != expected['image']:
                            raise ValueError('QUIESCENCE_RECOVERY_IDENTITY_CHANGED')
                        command(['docker', 'start', name])
                        if inspect(name)['State']['Status'] != 'running': raise ValueError('QUIESCENCE_RECOVERY_NOT_RUNNING')
                    except Exception as error: failures.append(type(error).__name__)
                receipt['status'] = 'ORIGINAL_RUNTIME_RESUMED' if not failures else 'RECOVERY_UNVERIFIED'
                receipt['recovery_errors'] = failures
        else: raise ValueError('QUIESCENCE_ACTION_INVALID')
        receipt['recorded_at'] = datetime.now(timezone.utc).isoformat()
        persist(path, receipt)
        print(json.dumps({'status': receipt['status'], 'candidate_sha': candidate,
                          'installation_started': receipt['installation_started'],
                          'real_orders_sent': 0, 'ppi_watch_accessed': False}), flush=True)
        return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate-sha', required=True)
    parser.add_argument('--action', choices=['drain', 'install', 'recover'], required=True)
    args = parser.parse_args()
    receipt = run(args.candidate_sha, args.action)
    raise SystemExit(2 if receipt['status'] in {'RECOVERY_UNVERIFIED', 'FIX_FORWARD_VALIDATION_REQUIRED'} else 0)
