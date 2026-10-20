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
FAILED_ADMIN_CANDIDATE = '7ae4d088d8cf90f670f8675d220244638f35d7f6'
FAILED_COLD2_CANDIDATE = '48a981cf327a637c0c074879fcdcd17475de8111'
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


def admit(*, stopped=False, allow_admin_issue624=False,
          allow_market_open_suspend=False, initial=False):
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
        if allow_market_open_suspend:
            # Operator-authorized intraday pause: retain the true market session.
            # A stopped observer must acknowledge SIGTERM as STOPPED; a stale
            # RUNNING row is never proof of quiescence.
            allowed_processes = ({'RUNNING', 'STOPPED'} if initial else {'STOPPED'}) if stopped else {'RUNNING'}
            if session != 'MARKET_OPEN' or process not in allowed_processes:
                raise ValueError('QUIESCENCE_OPEN_SUSPEND_STATE_REQUIRED')
        else:
            allowed_processes = {'WAITING_MARKET', 'STOPPED'} if stopped else {'WAITING_MARKET'}
            if process not in allowed_processes or session not in {'CLOSED', 'MARKET_CLOSED'}:
                raise ValueError('QUIESCENCE_CLOSED_REQUIRED')
        if not stopped or (allow_market_open_suspend and initial and process == 'RUNNING'):
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


def critical_admission():
    """Verify the independent issues-only broker before adopting a stopped runtime.

    The critical container can retain its own image until promotion reconciles
    it. Its image is not proof of observer identity or health.
    """
    critical = inspect('porota_critical_approval_rc6')
    state = critical['State']
    if (state['Status'] != 'running' or
            state.get('Health', {}).get('Status') != 'healthy' or
            state['OOMKilled'] is not False or
            type(critical.get('RestartCount')) is not int or
            critical['RestartCount'] != 0):
        raise ValueError('QUIESCENCE_CRITICAL_UNHEALTHY:' + json.dumps({
            'status': state['Status'],
            'health': state.get('Health', {}).get('Status'),
            'oom': state['OOMKilled'],
            'restart_count': critical.get('RestartCount')}, sort_keys=True))
    result = command(['docker', 'exec', 'porota_critical_approval_rc6',
                      'python', '-c',
                      "from fm_critical_approval_unix_runtime_rc6 import UnixGithubIssuesClient; "
                      "h=UnixGithubIssuesClient().health(); "
                      "assert h.get('status')=='ok' and h.get('capability')=='issues_only'; "
                      "print('RC6_CRITICAL_ISSUES_ONLY=GREEN')"])
    if result.strip() != 'RC6_CRITICAL_ISSUES_ONLY=GREEN':
        raise ValueError('QUIESCENCE_CRITICAL_CAPABILITY_UNVERIFIED')
    return {'image': critical['Image'], 'status': 'healthy',
            'restart_count': 0, 'capability': 'issues_only'}


def adopt_stopped_after_failed_install(candidate, folder, originals, *, admin_paper_id=None,
                                       allow_market_open_suspend=False):
    """One exact fail-closed continuation; never start the prior image."""
    # CURRENT_STATE_V2 identifies the installed image, but a subsequent
    # failed pre-install COLD2 has its own newer stop receipt. Select the
    # latest receipt that proves ownership of the exact stopped containers.
    current_path = folder / 'CURRENT_STATE_V2.json'
    current_provenance = current_path.exists() or current_path.is_symlink()
    if current_provenance:
        if current_path.is_symlink() or not current_path.is_file() or current_path.stat().st_size > 65536:
            raise ValueError('QUIESCENCE_CURRENT_STATE_UNSAFE')
        current = json.loads(current_path.read_bytes())
        state_candidate = current.get('candidate_sha')
        if (not isinstance(state_candidate, str) or
                not re.fullmatch('[0-9a-f]{40}', state_candidate) or
                current.get('validation_status') != 'DEPLOYED_VALIDATION_PENDING' or
                current.get('mode') != 'PRODUCTION_PAPER' or
                type(current.get('real_orders_sent')) is not int or current['real_orders_sent'] != 0 or
                current.get('real_order_routes') != 'NOT_CALLED' or
                not all(v['Image'] == current.get('image_id') for v in originals.values())):
            raise ValueError('QUIESCENCE_CURRENT_PROVENANCE_RED')
        state_time = datetime.fromisoformat(current['recorded_at'])
        if state_time.tzinfo is None:
            raise ValueError('QUIESCENCE_CURRENT_CLOCK_UNVERIFIED')
        paths = list(folder.glob('quiescence-*.json'))
        if len(paths) > 128:
            raise ValueError('QUIESCENCE_RECEIPT_INVENTORY_BOUND')
        eligible = []
        for path in paths:
            old_candidate = path.name.removeprefix('quiescence-').removesuffix('.json')
            if (path.is_symlink() or not path.is_file() or path.stat().st_size > 65536 or
                    not re.fullmatch('[0-9a-f]{40}', old_candidate)):
                raise ValueError('QUIESCENCE_RECEIPT_INVENTORY_UNSAFE')
            row = json.loads(path.read_bytes())
            if row.get('candidate_sha') != old_candidate:
                raise ValueError('QUIESCENCE_RECEIPT_INVENTORY_PROVENANCE_RED')
            if old_candidate == candidate:
                continue
            stamp = datetime.fromisoformat(row['recorded_at'])
            if stamp.tzinfo is None:
                raise ValueError('QUIESCENCE_RECEIPT_CLOCK_UNVERIFIED')
            eligible.append((stamp, old_candidate))
        if not eligible:
            raise ValueError('QUIESCENCE_CURRENT_STOP_RECEIPT_MISSING')
        eligible.sort()
        if len(eligible) > 1 and eligible[-1][0] == eligible[-2][0]:
            raise ValueError('QUIESCENCE_CURRENT_STOP_RECEIPT_AMBIGUOUS')
        if eligible[-1][0] < state_time:
            raise ValueError('QUIESCENCE_CURRENT_STOP_RECEIPT_STALE')
        previous_candidate = eligible[-1][1]
    else:
        historical_admin = (folder / ('quiescence-' + FAILED_COLD2_CANDIDATE + '.json')).exists()
        previous_candidate = (FAILED_ADMIN_CANDIDATE if admin_paper_id else
                              FAILED_COLD2_CANDIDATE if historical_admin else
                              FAILED_INSTALL_CANDIDATE)
    if candidate in {FAILED_INSTALL_CANDIDATE, FAILED_ADMIN_CANDIDATE,
                     FAILED_COLD2_CANDIDATE}:
        raise ValueError('QUIESCENCE_NEW_CANDIDATE_REQUIRED')
    previous_path = folder / ('quiescence-' + previous_candidate + '.json')
    if previous_path.is_symlink() or not previous_path.is_file():
        raise ValueError('QUIESCENCE_PRIOR_RECEIPT_MISSING')
    raw = previous_path.read_bytes()
    if len(raw) > 64 * 1024:
        raise ValueError('QUIESCENCE_PRIOR_RECEIPT_BOUND')
    previous = json.loads(raw)
    closed_admin_prior = (admin_paper_id is None and
                          previous.get('admin_cancel_paper_id') is not None)
    if (previous.get('candidate_sha') != previous_candidate or
            previous.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED' or
            previous.get('installation_started') is not True or
            previous.get('inflight') != 'ABSENT_OWNED_RUNTIME_STOPPED' or
            (not closed_admin_prior and previous.get('admin_cancel_paper_id') != admin_paper_id) or
            type(previous.get('real_orders_sent')) is not int or previous['real_orders_sent'] != 0 or
            previous.get('ppi_watch_accessed') is not False or
            set(previous.get('stop_attempted', [])) != set(OWNED) or
            set(previous.get('stop_evidence', {})) != set(OWNED) or
            set(previous.get('containers', {})) != set(OWNED)):
        raise ValueError('QUIESCENCE_PRIOR_RECEIPT_INVALID')
    if closed_admin_prior:
        paper_id = previous.get('admin_cancel_paper_id')
        if (type(paper_id) is not str or not paper_id or
                previous.get('admin_cancel_status') != 'APPLIED_NO_MARKET_SALE'):
            raise ValueError('QUIESCENCE_PRIOR_ADMIN_CLOSE_UNVERIFIED')
        db = ROOT / 'data/paper_v17/observer_v17.db'
        with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=.2)) as c:
            c.execute('PRAGMA query_only=ON')
            c.execute('PRAGMA busy_timeout=200')
            row = c.execute('''SELECT p.status,p.close_reason,p.exit_price,p.net_pnl,
              a.authority FROM paper_positions p JOIN paper_admin_cancellations a
              USING(paper_id) WHERE p.paper_id=?''', (paper_id,)).fetchone()
            if (row != ('CANCELLED_ADMIN', ADMIN_CANCEL_REASON, None, None,
                        'ISSUE624_OPERATOR_REQUEST_20261019') or
                    c.execute("SELECT 1 FROM paper_fills WHERE paper_id=? AND side='SELL_SIMULATED'",
                              (paper_id,)).fetchone()):
                raise ValueError('QUIESCENCE_PRIOR_ADMIN_LEDGER_CHANGED')
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
    critical = critical_admission()
    if bool(previous.get('market_open_suspended', False)) != allow_market_open_suspend:
        raise ValueError('QUIESCENCE_PRIOR_SESSION_MODE_CHANGED')
    process = admit(stopped=True, allow_admin_issue624=bool(admin_paper_id),
                    allow_market_open_suspend=allow_market_open_suspend)
    if process not in ({'WAITING_MARKET', 'STOPPED'} if current_provenance or admin_paper_id or closed_admin_prior
                       else {'STOPPED'}):
        raise ValueError('QUIESCENCE_STOPPED_PROCESS_REQUIRED')
    return {'candidate_sha': candidate,
            'status': 'ADMIN_DRAINED_PENDING' if admin_paper_id else 'DRAINED',
            'installation_started': False, 'stop_attempted': list(OWNED),
            'stop_evidence': previous['stop_evidence'],
            'containers': previous['containers'], 'real_orders_sent': 0,
            'ppi_watch_accessed': False, 'inflight': 'ABSENT_OWNED_RUNTIME_STOPPED',
            'admin_cancel_paper_id': admin_paper_id,
            'market_open_suspended': allow_market_open_suspend,
            'critical_admission': critical,
            'fix_forward_from': previous_candidate,
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


def admin_resource_sample():
    """Read both host gates without allocating a second monitor process."""
    try:
        available = next(int(line.split()[1]) * 1024 for line in
            Path('/proc/meminfo').read_text().splitlines()
            if line.startswith('MemAvailable:'))
        pressure = next(float(part.split('=')[1]) for part in
            Path('/proc/pressure/io').read_text().splitlines()[0].split()
            if part.startswith('avg10='))
    except (OSError, StopIteration, IndexError, ValueError) as exc:
        raise ValueError('QUIESCENCE_ADMIN_RESOURCE_READ_FAILED') from exc
    return available, pressure


def wait_for_admin_resources(*, sample=None, clock=None, pause=None):
    """Let stop/transfer I/O settle, then demand two consecutive green samples.

    This never lowers the resource thresholds or writes the PAPER ledger. If
    pressure persists for 90 seconds, recovery starts the original runtime.
    """
    sample = sample or admin_resource_sample
    clock = clock or time.monotonic
    pause = pause or time.sleep
    deadline = clock() + 90
    green = 0
    while True:
        available, pressure = sample()
        admitted = available >= 128 * 1024 * 1024 and pressure <= 5
        green = green + 1 if admitted else 0
        print(json.dumps({'event': 'QUIESCENCE_ADMIN_RESOURCE_SAMPLE',
                          'mem_available_bytes': available, 'io_psi_avg10': pressure,
                          'consecutive_green': green, 'admitted': admitted},
                         sort_keys=True), flush=True)
        if green >= 2:
            return available, pressure
        remaining = deadline - clock()
        if remaining <= 0:
            raise ValueError('QUIESCENCE_ADMIN_RESOURCE_ADMISSION_RED:'
                             f'mem_available_bytes={available}:io_psi_avg10={pressure}')
        pause(min(5, remaining))


def run(candidate, action, *, allow_admin_issue624=False,
        allow_market_open_suspend=False):
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
            process = admit(stopped=True, allow_admin_issue624=allow_admin_issue624,
                            allow_market_open_suspend=allow_market_open_suspend, initial=True)
            admin_paper_id = admin_target_on_host() if allow_admin_issue624 else None
            if allow_market_open_suspend and admin_paper_id:
                raise ValueError('QUIESCENCE_OPEN_ADMIN_CANCEL_FORBIDDEN')
            originals = {name: inspect(name) for name in OWNED}
            if (process in {'STOPPED', 'WAITING_MARKET'}
                    and all(v['State']['Status'] == 'exited' for v in originals.values())):
                receipt = adopt_stopped_after_failed_install(
                    candidate, folder, originals, admin_paper_id=admin_paper_id,
                    allow_market_open_suspend=allow_market_open_suspend)
                persist(path, receipt)
            else:
                if process != ('RUNNING' if allow_market_open_suspend else 'WAITING_MARKET') or any(v['State']['Status'] != 'running' for v in originals.values()):
                    raise ValueError('QUIESCENCE_RUNTIME_NOT_RUNNING_OR_EXACT_PRIOR_STOP')
                admit(allow_admin_issue624=bool(admin_paper_id),
                      allow_market_open_suspend=allow_market_open_suspend)
                receipt = {'candidate_sha': candidate, 'status': 'DRAINING',
                           'installation_started': False, 'stop_attempted': [],
                           'stop_evidence': {},
                           'containers': {n: {'id': v['Id'], 'image': v['Image']} for n, v in originals.items()},
                    'real_orders_sent': 0, 'ppi_watch_accessed': False,
                    'admin_cancel_paper_id': admin_paper_id,
                    'market_open_suspended': allow_market_open_suspend}
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
                admit(stopped=True, allow_admin_issue624=bool(admin_paper_id),
                      allow_market_open_suspend=allow_market_open_suspend)
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
            wait_for_admin_resources()
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
            if bool(receipt.get('market_open_suspended', False)) != allow_market_open_suspend:
                raise ValueError('QUIESCENCE_INSTALL_SESSION_MODE_CHANGED')
            admit(stopped=True, allow_market_open_suspend=allow_market_open_suspend)
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
    parser.add_argument('--market-open-suspend', action='store_true')
    parser.add_argument('--allow-validation-pending',action='store_true',
                        help='classify installed validation pending during failure cleanup; never validates runtime')
    args = parser.parse_args(argv)
    if args.allow_validation_pending and args.action!='recover':
        parser.error('--allow-validation-pending requires --action recover')
    if args.admin_cancel_issue624 and args.action not in {'drain', 'admin-begin', 'admin-commit'}:
        parser.error('--admin-cancel-issue624 only for controlled admin actions')
    if args.market_open_suspend and args.action not in {'drain', 'install'}:
        parser.error('--market-open-suspend only for drain and install')
    receipt = run(args.candidate_sha, args.action,
                  allow_admin_issue624=args.admin_cancel_issue624,
                  allow_market_open_suspend=args.market_open_suspend)
    if receipt['status']=='RECOVERY_UNVERIFIED':return 2
    if receipt['status']=='FIX_FORWARD_VALIDATION_REQUIRED' and not args.allow_validation_pending:return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
