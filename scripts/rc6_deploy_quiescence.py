#!/usr/bin/env python3
"""Durable CLOSED/PAPER admission and drain before candidate host writes.

Recovery restarts the original containers only before installation begins.
After that boundary the receipt explicitly requires fix-forward validation.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import time
from zoneinfo import ZoneInfo

ROOT = Path('/opt/porota-trading')
OWNED = ('porota_production_observer', 'porota_production_dashboard')
FAILED_INSTALL_CANDIDATE = '38b04252f5e25d43bf40c2fa332a9d01d3eb8fb1'
ADMIN_CANCEL_REASON = 'ADMIN_CANCEL_NO_MARKET_EXECUTION'


def admin_issue624_target(c):
    """The one observed SUPV PAPER EOD position, never a general bypass."""
    opened = c.execute("SELECT * FROM paper_positions WHERE status='OPEN'").fetchall()
    if not opened:
        return None
    if len(opened) != 1:
        raise ValueError('QUIESCENCE_ADMIN_EXACT_ONE_REQUIRED')
    p = opened[0]
    opened_at = datetime.fromisoformat(p['opened_at'])
    if opened_at.tzinfo is None:
        raise ValueError('QUIESCENCE_ADMIN_CLOCK_UNVERIFIED')
    local = opened_at.astimezone(ZoneInfo('America/Argentina/Buenos_Aires'))
    identity = tuple(str(p[k]) for k in ('symbol', 'asset_class', 'market',
                                          'currency', 'settlement', 'quantity'))
    intent = c.execute('SELECT state,cause FROM paper_exit_intents WHERE paper_id=?',
                       (p['paper_id'],)).fetchone()
    if (identity != ('SUPV', 'ACCIONES', 'BYMA', 'ARS', 'A-24HS', '9')
            or local.date().isoformat() != '2026-10-19'
            or (local.hour, local.minute) != (15, 51)
            or not intent or intent['cause'] != 'EOD_PAPER'
            or intent['state'] != 'EXIT_PENDING_MARKET_CLOSED'
            or c.execute("SELECT 1 FROM paper_fills WHERE paper_id=? AND side='SELL_SIMULATED'",
                         (p['paper_id'],)).fetchone()):
        raise ValueError('QUIESCENCE_ADMIN_TARGET_CHANGED')
    return p['paper_id']


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


def admit(*, stopped=False, allow_admin_issue624=False):
    path = ROOT / 'data/paper_v17/observer_v17.db'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)) as c:
        c.row_factory = sqlite3.Row
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
                if table == 'paper_positions' and allow_admin_issue624:
                    admin_issue624_target(c)
                    continue
                raise ValueError('QUIESCENCE_OPEN_POSITION')
        return process


def admin_target_on_host():
    path = ROOT / 'data/paper_v17/observer_v17.db'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA busy_timeout=200')
        return admin_issue624_target(c)


def adopt_stopped_after_failed_install(candidate, folder, originals):
    """One exact fail-closed continuation; never start the prior image."""
    if candidate == FAILED_INSTALL_CANDIDATE:
        raise ValueError('QUIESCENCE_NEW_CANDIDATE_REQUIRED')
    previous_path = folder / ('quiescence-' + FAILED_INSTALL_CANDIDATE + '.json')
    if previous_path.is_symlink() or not previous_path.is_file():
        raise ValueError('QUIESCENCE_PRIOR_RECEIPT_MISSING')
    raw = previous_path.read_bytes()
    if len(raw) > 64 * 1024:
        raise ValueError('QUIESCENCE_PRIOR_RECEIPT_BOUND')
    previous = json.loads(raw)
    if (previous.get('candidate_sha') != FAILED_INSTALL_CANDIDATE or
            previous.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED' or
            previous.get('installation_started') is not True or
            previous.get('inflight') != 'ABSENT_OWNED_RUNTIME_STOPPED' or
            type(previous.get('real_orders_sent')) is not int or previous['real_orders_sent'] != 0 or
            previous.get('ppi_watch_accessed') is not False or
            set(previous.get('stop_attempted', [])) != set(OWNED) or
            set(previous.get('stop_evidence', {})) != set(OWNED) or
            set(previous.get('containers', {})) != set(OWNED)):
        raise ValueError('QUIESCENCE_PRIOR_RECEIPT_INVALID')
    for name in OWNED:
        value = originals[name]
        old = previous['containers'][name]
        proof = previous['stop_evidence'][name]
        state = lifecycle(value)
        if (value['Id'] != old['id'] or value['Image'] != old['image'] or
                state['Status'] != 'exited' or type(state['ExitCode']) is not int or
                state['ExitCode'] != 0 or
                proof.get('after') != state or
                proof.get('classification') not in {'CLEAN_EXIT','INHERITED_OOM_FLAG_CLEAN_EXIT'}):
            raise ValueError('QUIESCENCE_PRIOR_STOP_IDENTITY_CHANGED')
    critical = inspect('porota_critical_approval_rc6')
    if (critical['State']['Status'] != 'running' or
            critical['State'].get('Health', {}).get('Status') != 'healthy' or
            critical['State']['OOMKilled'] is not False or
            critical['Image'] != originals[OWNED[0]]['Image']):
        raise ValueError('QUIESCENCE_CRITICAL_UNHEALTHY')
    if admit(stopped=True) != 'STOPPED':
        raise ValueError('QUIESCENCE_STOPPED_PROCESS_REQUIRED')
    return {'candidate_sha': candidate, 'status': 'DRAINED',
            'installation_started': False, 'stop_attempted': list(OWNED),
            'stop_evidence': previous['stop_evidence'],
            'containers': previous['containers'], 'real_orders_sent': 0,
            'ppi_watch_accessed': False, 'inflight': 'ABSENT_OWNED_RUNTIME_STOPPED',
            'fix_forward_from': FAILED_INSTALL_CANDIDATE,
            'prior_receipt_sha256': hashlib.sha256(raw).hexdigest()}


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


def run(candidate, action, *, allow_admin_issue624=False):
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
            process = admit(stopped=True, allow_admin_issue624=allow_admin_issue624)
            admin_paper_id = admin_target_on_host() if allow_admin_issue624 else None
            if process == 'WAITING_MARKET':
                admit(allow_admin_issue624=bool(admin_paper_id))
            originals = {name: inspect(name) for name in OWNED}
            if (process == 'STOPPED' and not admin_paper_id
                    and all(v['State']['Status'] == 'exited' for v in originals.values())):
                receipt = adopt_stopped_after_failed_install(candidate, folder, originals)
                persist(path, receipt)
            else:
                if process != 'WAITING_MARKET' or any(v['State']['Status'] != 'running' for v in originals.values()):
                    raise ValueError('QUIESCENCE_RUNTIME_NOT_RUNNING_OR_EXACT_PRIOR_STOP')
                receipt = {'candidate_sha': candidate, 'status': 'DRAINING',
                           'installation_started': False, 'stop_attempted': [],
                           'stop_evidence': {},
                           'containers': {n: {'id': v['Id'], 'image': v['Image']} for n, v in originals.items()},
                    'real_orders_sent': 0, 'ppi_watch_accessed': False,
                    'admin_cancel_paper_id': admin_paper_id}
                persist(path, receipt)
                for name in OWNED:
                    # Durable intent precedes stop: timeout may follow daemon effect.
                    receipt['stop_attempted'].append(name); persist(path, receipt)
                    command(['docker', 'stop', '--time', '45', name])
                    value = inspect(name)
                    receipt['stop_evidence'][name] = {'before': lifecycle(originals[name]), 'after': lifecycle(value)}
                    persist(path, receipt)
                    print(json.dumps({'container': name, 'stop_evidence': receipt['stop_evidence'][name]}), flush=True)
                    classification = clean_stop(originals[name], value)
                    receipt['stop_evidence'][name]['classification'] = classification
                    persist(path, receipt)
                admit(stopped=True, allow_admin_issue624=bool(admin_paper_id))
                if admin_paper_id and admin_target_on_host() != admin_paper_id:
                    raise ValueError('QUIESCENCE_ADMIN_TARGET_CHANGED_AFTER_STOP')
                receipt.update(status='ADMIN_DRAINED_PENDING' if admin_paper_id else 'DRAINED',
                               inflight='ABSENT_OWNED_RUNTIME_STOPPED')
        elif action == 'admin-begin':
            if (not allow_admin_issue624 or not receipt
                    or receipt['status'] != 'ADMIN_DRAINED_PENDING'
                    or not receipt.get('admin_cancel_paper_id')):
                raise ValueError('QUIESCENCE_ADMIN_NOT_DRAINED')
            for name in OWNED:
                value = inspect(name)
                expected = receipt['containers'][name]
                if (value['Id'] != expected['id'] or value['Image'] != expected['image']
                        or value['State']['Status'] != 'exited'):
                    raise ValueError('QUIESCENCE_ADMIN_RUNTIME_CHANGED')
            admit(stopped=True, allow_admin_issue624=True)
            if admin_target_on_host() != receipt['admin_cancel_paper_id']:
                raise ValueError('QUIESCENCE_ADMIN_TARGET_CHANGED')
            # A failed capacity check leaves the old runtime recoverable.
            available = next(int(line.split()[1]) * 1024 for line in
                Path('/proc/meminfo').read_text().splitlines()
                if line.startswith('MemAvailable:'))
            pressure = next(float(part.split('=')[1]) for part in
                Path('/proc/pressure/io').read_text().splitlines()[0].split()
                if part.startswith('avg10='))
            if available < 128 * 1024 * 1024 or pressure > 5:
                raise ValueError('QUIESCENCE_ADMIN_RESOURCE_ADMISSION_RED')
            receipt.update(status='ADMIN_WRITE_AUTHORIZED', installation_started=True)
        elif action == 'admin-commit':
            if (not allow_admin_issue624 or not receipt
                    or receipt['status'] != 'ADMIN_WRITE_AUTHORIZED'
                    or receipt['installation_started'] is not True):
                raise ValueError('QUIESCENCE_ADMIN_WRITE_NOT_AUTHORIZED')
            admit(stopped=True)
            db = ROOT / 'data/paper_v17/observer_v17.db'
            with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=.2)) as c:
                c.execute('PRAGMA query_only=ON')
                c.execute('PRAGMA busy_timeout=200')
                row = c.execute('''SELECT p.status,p.close_reason,p.exit_price,p.net_pnl,
                  a.authority FROM paper_positions p JOIN paper_admin_cancellations a
                  USING(paper_id) WHERE p.paper_id=?''',
                  (receipt['admin_cancel_paper_id'],)).fetchone()
                if (row != ('CANCELLED_ADMIN', ADMIN_CANCEL_REASON, None, None,
                            'ISSUE624_OPERATOR_REQUEST_20261019')
                        or c.execute("SELECT 1 FROM paper_fills WHERE paper_id=? AND side='SELL_SIMULATED'",
                                     (receipt['admin_cancel_paper_id'],)).fetchone()):
                    raise ValueError('QUIESCENCE_ADMIN_LEDGER_NOT_RECONCILED')
            receipt.update(status='DRAINED', admin_cancel_status='APPLIED_NO_MARKET_SALE')
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


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate-sha', required=True)
    parser.add_argument('--action', choices=['drain', 'admin-begin', 'admin-commit',
                                            'install', 'recover'], required=True)
    parser.add_argument('--admin-cancel-issue624', action='store_true')
    parser.add_argument('--allow-validation-pending',action='store_true',
                        help='classify installed validation pending during failure cleanup; never validates runtime')
    args = parser.parse_args(argv)
    if args.allow_validation_pending and args.action!='recover':
        parser.error('--allow-validation-pending requires --action recover')
    if args.admin_cancel_issue624 and args.action not in {'drain', 'admin-begin', 'admin-commit'}:
        parser.error('--admin-cancel-issue624 only for controlled admin actions')
    if args.admin_cancel_issue624:
        receipt = run(args.candidate_sha, args.action, allow_admin_issue624=True)
    else:
        receipt = run(args.candidate_sha, args.action)
    if receipt['status']=='RECOVERY_UNVERIFIED':return 2
    if receipt['status']=='FIX_FORWARD_VALIDATION_REQUIRED' and not args.allow_validation_pending:return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
