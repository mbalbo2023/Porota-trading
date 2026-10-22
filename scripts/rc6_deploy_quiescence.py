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
          allow_market_open_suspend=False, allow_open_paper_carryover=False,
          allow_postclose_pending=False, allow_intraday_caucion=False,
          initial=False, stopped_receipt=None):
    if allow_intraday_caucion:
        local = datetime.now(ZoneInfo('America/Argentina/Buenos_Aires'))
        if (not allow_market_open_suspend or allow_postclose_pending or
                local.date().isoformat() != '2026-10-21' or not 13 <= local.hour < 17):
            raise ValueError('QUIESCENCE_INTRADAY_CAUCION_WINDOW_RED')
    if allow_postclose_pending:
        local = datetime.now(ZoneInfo('America/Argentina/Buenos_Aires'))
        if (not allow_market_open_suspend or not allow_open_paper_carryover or
                (stopped and not initial and stopped_receipt is None) or
                local.date().isoformat() != '2026-10-20' or
                not 17 <= local.hour < 23):
            raise ValueError('QUIESCENCE_POSTCLOSE_WINDOW_OR_STOP_RED')
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
        if allow_postclose_pending and (not stopped or
                (stopped_receipt or {}).get('postclose_live_closed') is True):
            # Postclose is a distinct authorized window. Demand a fresh
            # closed-session observer before stop, then its stopped ack or
            # exact clean Docker stop proof when the DB ack is stale.
            allowed_processes = {'WAITING_MARKET', 'STOPPED'} if stopped else {'WAITING_MARKET'}
            if stopped and not initial and process == 'WAITING_MARKET':
                exact_clean_stopped(stopped_receipt)
            if process not in allowed_processes or session not in {'CLOSED', 'MARKET_CLOSED'}:
                raise ValueError('QUIESCENCE_POSTCLOSE_STATE_REQUIRED')
        elif allow_market_open_suspend:
            # Operator-authorized intraday pause: retain the true market session.
            # A stopped observer must acknowledge SIGTERM as STOPPED; a stale
            # RUNNING row is never proof of quiescence.
            allowed_processes = ({'RUNNING', 'STOPPED'} if initial else {'STOPPED'}) if stopped else {'RUNNING'}
            if stopped and not initial and process == 'RUNNING' and stopped_receipt is not None:
                # The scanner can leave its last RUNNING row after its parent
                # and child have exited. Docker identity and clean-exit proofs
                # replace the missing DB acknowledgement; no writer may live.
                exact_clean_stopped(stopped_receipt)
                allowed_processes.add('RUNNING')
            # The scanner also writes the calendar phase OPEN while renewing
            # its read-only PPI session. The initial running admission must be
            # fresh RUNNING/MARKET_OPEN; after a clean SIGTERM, STOPPED/OPEN
            # is an exact transitional state, not evidence of market closure.
            allowed_sessions = ({'MARKET_OPEN', 'OPEN'} if stopped and
                                (process == 'STOPPED' or stopped_receipt is not None)
                                else {'MARKET_OPEN'})
            if session not in allowed_sessions or process not in allowed_processes:
                observed_process = process if process in {'RUNNING', 'STOPPED', 'DEGRADED', 'STARTING'} else 'UNKNOWN'
                observed_session = session if session in {'MARKET_OPEN', 'OPEN', 'MARKET_CLOSED', 'CLOSED', 'CHECKING'} else 'UNKNOWN'
                raise ValueError('QUIESCENCE_OPEN_SUSPEND_STATE_REQUIRED:' + observed_process + ':' + observed_session)
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
                if table == 'paper_positions' and allow_open_paper_carryover and allow_market_open_suspend:
                    continue
                if table == 'paper_cauciones' and allow_postclose_pending:
                    continue
                if table == 'paper_cauciones' and allow_intraday_caucion:
                    caucion = pending_caucion_snapshot()
                    if (caucion['open_count'] != 1 or
                            caucion['maturity_at'] != '2026-10-21T17:00:00-03:00'):
                        raise ValueError('QUIESCENCE_INTRADAY_CAUCION_CHANGED')
                    continue
                if table == 'paper_positions' and allow_admin_issue624:
                    admin_issue624_target(c)
                    continue
                raise ValueError('QUIESCENCE_OPEN_POSITION')
        return process


def admit_poststart_flat(pre_mode):
    """Recheck the live flat PAPER book after a promotion crosses market phase."""
    if pre_mode not in {'OPEN', 'CLOSED'}:
        raise ValueError('QUIESCENCE_PRESTART_MODE_UNKNOWN')
    path = ROOT / 'data/paper_v17/observer_v17.db'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)) as c:
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA busy_timeout=200')
        state = c.execute(
            'SELECT process_state,session_state FROM observer_state WHERE id=1').fetchone()
    if state == ('RUNNING', 'MARKET_OPEN'):
        actual, expected = 'OPEN', 'RUNNING'
    elif state and state[0] == 'WAITING_MARKET' and state[1] in {'CLOSED', 'MARKET_CLOSED'}:
        actual, expected = 'CLOSED', 'WAITING_MARKET'
    else:
        raise ValueError('QUIESCENCE_POSTSTART_SESSION_UNVERIFIED')
    # admit rereads committed mode, integer zero orders, a fresh heartbeat and
    # all three flat ledgers. A phase change between reads fails closed.
    if admit(allow_market_open_suspend=actual == 'OPEN') != expected:
        raise ValueError('QUIESCENCE_POSTSTART_BOOK_CHANGED')
    return actual


def spot_carryover_snapshot():
    """Bounded, read-only committed PAPER spot book after observer SIGTERM."""
    path = ROOT / 'data/paper_v17/observer_v17.db'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA busy_timeout=200')
        end = time.monotonic() + 2
        c.set_progress_handler(lambda: int(time.monotonic() > end), 1000)
        rows = c.execute("SELECT * FROM paper_positions WHERE status='OPEN' ORDER BY paper_id LIMIT 17").fetchall()
        if len(rows) > 16:
            raise ValueError('QUIESCENCE_CARRYOVER_POSITION_BOUND')
        ids = [row['paper_id'] for row in rows]
        if any(type(value) is not str or not value for value in ids) or len(set(ids)) != len(ids):
            raise ValueError('QUIESCENCE_CARRYOVER_IDENTITY_INVALID')
        payload = []
        identities = []
        for row in rows:
            identity = {key: row[key] for key in ('paper_id', 'symbol', 'quantity',
                                                   'entry_price', 'opened_at')}
            identities.append(identity)
            fills = c.execute("SELECT * FROM paper_fills WHERE paper_id=? ORDER BY id LIMIT 65",
                              (row['paper_id'],)).fetchall()
            if (len(fills) > 64 or any(fill['side'] == 'SELL_SIMULATED' for fill in fills)):
                raise ValueError('QUIESCENCE_CARRYOVER_FILLS_INVALID')
            intents = c.execute("SELECT * FROM paper_exit_intents WHERE paper_id=? LIMIT 2",
                                (row['paper_id'],)).fetchall()
            if len(intents) > 1:
                raise ValueError('QUIESCENCE_CARRYOVER_INTENTS_INVALID')
            payload.append({'position': dict(row), 'fills': [dict(x) for x in fills],
                            'intents': [dict(x) for x in intents]})
        raw = json.dumps(payload, sort_keys=True, default=str, separators=(',', ':')).encode()
        if len(raw) > 128 * 1024:
            raise ValueError('QUIESCENCE_CARRYOVER_SNAPSHOT_BOUND')
        return {'open_count': len(rows), 'paper_ids': ids, 'identities': identities,
                'sha256': hashlib.sha256(raw).hexdigest()}


def pending_caucion_snapshot():
    """Bind the one observed overnight PAPER caucion to its complete ledger row."""
    path = ROOT / 'data/paper_v17/observer_v17.db'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA busy_timeout=200')
        deadline = time.monotonic() + 2
        c.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        rows = c.execute("SELECT * FROM paper_cauciones WHERE status='OPEN' ORDER BY paper_id LIMIT 2").fetchall()
        if len(rows) != 1:
            raise ValueError('QUIESCENCE_PENDING_CAUCION_COUNT_CHANGED')
        row = dict(rows[0])
        raw = json.dumps(row, sort_keys=True, default=str, separators=(',', ':')).encode()
        if len(raw) > 16384:
            raise ValueError('QUIESCENCE_PENDING_CAUCION_BOUND')
        return {'open_count': 1, 'sha256': hashlib.sha256(raw).hexdigest(),
                'paper_id_sha256': hashlib.sha256(str(row['paper_id']).encode()).hexdigest(),
                'maturity_at': row['maturity_at']}


def verify_spot_carryover(receipt):
    expected = receipt.get('spot_carryover')
    if (not isinstance(expected, dict) or not isinstance(expected.get('paper_ids'), list)
            or not isinstance(expected.get('identities'), list)
            or len(expected['paper_ids']) != len(expected['identities'])):
        raise ValueError('QUIESCENCE_CARRYOVER_RECEIPT_MISSING')
    path = ROOT / 'data/paper_v17/observer_v17.db'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)) as c:
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA busy_timeout=200')
        mode, orders = c.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
        if mode != 'PRODUCTION_PAPER' or type(orders) is not int or orders != 0:
            raise ValueError('QUIESCENCE_CARRYOVER_PAPER_REQUIRED')
        c.row_factory = sqlite3.Row
        for paper_id, identity in zip(expected['paper_ids'], expected['identities']):
            row = c.execute('SELECT status,symbol,quantity,entry_price,opened_at '
                            'FROM paper_positions WHERE paper_id=?', (paper_id,)).fetchone()
            if not row or row['status'] not in {'OPEN', 'CLOSED'}:
                raise ValueError('QUIESCENCE_CARRYOVER_POSITION_LOST')
            if {key: row[key] if key != 'paper_id' else paper_id for key in identity} != identity:
                raise ValueError('QUIESCENCE_CARRYOVER_IDENTITY_CHANGED')
            if row['status'] == 'CLOSED' and not c.execute(
                    "SELECT 1 FROM paper_fills WHERE paper_id=? AND side='SELL_SIMULATED'",
                    (paper_id,)).fetchone():
                raise ValueError('QUIESCENCE_CARRYOVER_CLOSE_UNBACKED')
    return len(expected['paper_ids'])


def exact_clean_stopped(receipt):
    if (set(receipt.get('containers', {})) != set(OWNED) or
            set(receipt.get('stop_evidence', {})) != set(OWNED) or
            set(receipt.get('stop_attempted', [])) != set(OWNED)):
        raise ValueError('QUIESCENCE_STOP_PROOF_INCOMPLETE')
    for name in OWNED:
        expected = receipt['containers'][name]
        proof = receipt['stop_evidence'][name]
        value = inspect(name)
        if (value['Id'] != expected['id'] or value['Image'] != expected['image'] or
                lifecycle(value) != proof.get('after') or
                value['State']['Status'] != 'exited' or
                proof.get('classification') not in {'CLEAN_EXIT', 'INHERITED_OOM_FLAG_CLEAN_EXIT'}):
            raise ValueError('QUIESCENCE_STOP_PROOF_CHANGED')


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


def adopt_exact_pending_intraday_stop(candidate, folder, originals, *, postclose=False):
    """Continue the installed PAPER runtime after its exact clean stop."""
    prior_candidate = 'd96cb68d177199317ee23239553517e04ee0a255'
    prior_deploy = 'e86bdb6a9fe5ac21b93bc26e70af9844945e1407'
    image = 'sha256:5af3f01a09722faad9fc5cc49cc11b28494d75d1dbdb780425fdd240fd482a34'
    ids = {'porota_production_observer':
           'e0e770054854441ce32af64685ab7da6027735511e40e35beb40c0e2811c56eb',
           'porota_production_dashboard':
           'e89bb27820f93d655b9db410b4032878c22763529b81a7eac1783ec87702a570'}
    expected_book = 'e7fa20929b6aa4c26a534db6554fd95a22f1734c0fc91175d6f20f05067af13e'
    expected_caucion = 'bf6d2e347850e951c12141bd1d785868b6691da0cc878d17bfe429ac73c9c8a7'
    state_path = folder/'CURRENT_STATE_V2.json'
    prior_path = folder/('quiescence-'+prior_candidate+'.json')
    if (candidate == prior_candidate or state_path.is_symlink() or prior_path.is_symlink() or
            not state_path.is_file() or not prior_path.is_file() or
            max(state_path.stat().st_size, prior_path.stat().st_size) > 65536):
        raise ValueError('QUIESCENCE_PENDING_SOURCE_UNSAFE')
    current = json.loads(state_path.read_bytes())
    previous = json.loads(prior_path.read_bytes())
    if (current.get('candidate_sha') != prior_candidate or
            current.get('deploy_sha') != prior_deploy or
            current.get('image_id') != image or
            current.get('validation_status') != 'DEPLOYED_VALIDATION_PENDING' or
            current.get('mode') != 'PRODUCTION_PAPER' or
            type(current.get('real_orders_sent')) is not int or current['real_orders_sent'] != 0 or
            current.get('real_order_routes') != 'NOT_CALLED' or
            previous.get('candidate_sha') != prior_candidate or
            previous.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED' or
            previous.get('installation_started') is not True or
            previous.get('market_open_suspended') is not True or
            previous.get('open_paper_carryover') is not True or
            type(previous.get('real_orders_sent')) is not int or previous['real_orders_sent'] != 0 or
            previous.get('ppi_watch_accessed') is not False):
        raise ValueError('QUIESCENCE_PENDING_PROVENANCE_RED')
    failed_at = datetime.fromisoformat(previous['recorded_at'])
    if failed_at.tzinfo is None or datetime.fromisoformat(current['recorded_at']) > failed_at:
        raise ValueError('QUIESCENCE_PENDING_CLOCK_RED')
    proof = {}
    for name in OWNED:
        row = originals[name]
        after = lifecycle(row)
        finished = datetime.fromisoformat(after['FinishedAt'].replace('Z','+00:00'))
        if (row['Id'] != ids[name] or row['Image'] != image or
                after['Status'] != 'exited' or type(after['ExitCode']) is not int or
                after['ExitCode'] != 0 or after['OOMKilled'] is not False or
                finished.tzinfo is None or not failed_at <= finished <= datetime.now(timezone.utc)):
            raise ValueError('QUIESCENCE_PENDING_STOP_CHANGED')
        proof[name] = {'after': after, 'classification': 'CLEAN_EXIT'}
    stopped = {'containers': {name:{'id':ids[name], 'image':image} for name in OWNED},
               'stop_attempted': list(OWNED), 'stop_evidence': proof}
    exact_clean_stopped(stopped)
    process = admit(stopped=True, allow_market_open_suspend=True,
                    allow_open_paper_carryover=True, allow_postclose_pending=postclose,
                    stopped_receipt=stopped)
    carry = spot_carryover_snapshot()
    caucion = pending_caucion_snapshot() if postclose else None
    if (process not in {'STOPPED','RUNNING'} or carry.get('open_count') != 2 or
            carry.get('sha256') != expected_book or
            (postclose and (caucion.get('sha256') != expected_caucion or
                            caucion.get('maturity_at') != '2026-10-21T17:00:00-03:00'))):
        raise ValueError('QUIESCENCE_PENDING_BOOK_CHANGED')
    critical = critical_admission()
    return dict(stopped, candidate_sha=candidate, status='DRAINED',
                installation_started=False, inflight='ABSENT_OWNED_RUNTIME_STOPPED',
                real_orders_sent=0, ppi_watch_accessed=False,
                admin_cancel_paper_id=None, market_open_suspended=True,
                open_paper_carryover=True, spot_carryover=carry,
                paused_postclose=postclose, caucion_carryover=caucion,
                critical_admission=critical, fix_forward_from=prior_candidate,
                prior_receipt_sha256=hashlib.sha256(prior_path.read_bytes()).hexdigest())


def adopt_stopped_after_failed_install(candidate, folder, originals, *, admin_paper_id=None,
                                       allow_market_open_suspend=False,
                                       allow_open_paper_carryover=False,
                                       allow_postclose_pending=False,
                                       allow_intraday_caucion=False):
    """One exact fail-closed continuation; never start the prior image."""
    # CURRENT_STATE_V2 identifies the installed image, but a subsequent
    # failed pre-install COLD2 has its own newer stop receipt. Select the
    # latest receipt that proves ownership of the exact stopped containers.
    current_path = folder / 'CURRENT_STATE_V2.json'
    current_provenance = current_path.exists() or current_path.is_symlink()
    if (current_provenance and allow_market_open_suspend and allow_open_paper_carryover and
            current_path.is_file() and not current_path.is_symlink() and
            json.loads(current_path.read_bytes()).get('validation_status') == 'DEPLOYED_VALIDATION_PENDING'):
        return adopt_exact_pending_intraday_stop(candidate, folder, originals,
                                                 postclose=allow_postclose_pending)
    # A validated prior runtime may be stopped by a later failed installation.
    # Its exact postclose receipt owns the containers and both PAPER ledgers.
    # The DEPLOYED_VALIDATION_PENDING branch above has a separate identity gate.
    if current_provenance:
        if current_path.is_symlink() or not current_path.is_file() or current_path.stat().st_size > 65536:
            raise ValueError('QUIESCENCE_CURRENT_STATE_UNSAFE')
        current = json.loads(current_path.read_bytes())
        state_candidate = current.get('candidate_sha')
        if (not isinstance(state_candidate, str) or
                not re.fullmatch('[0-9a-f]{40}', state_candidate) or
                current.get('validation_status') not in {'DEPLOYED_VALIDATION_PENDING', 'VALIDATED_RUNTIME'} or
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
            if old_candidate == candidate or row.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED':
                continue
            if (row.get('installation_started') is not True or
                    set(row.get('containers', {})) != set(OWNED) or
                    any(row['containers'][name].get('id') != originals[name]['Id'] or
                        row['containers'][name].get('image') != originals[name]['Image']
                        for name in OWNED)):
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
    if allow_postclose_pending and (previous.get('paused_postclose') is not True or
            previous.get('postclose_live_closed') is not True or
            previous.get('open_paper_carryover') is not True):
        raise ValueError('QUIESCENCE_PRIOR_POSTCLOSE_PROOF_MISSING')
    if allow_intraday_caucion and (previous.get('intraday_caucion') is not True or
            previous.get('paused_postclose') is not False or
            previous.get('open_paper_carryover') is not False or
            previous.get('spot_carryover') is not None):
        raise ValueError('QUIESCENCE_PRIOR_INTRADAY_CAUCION_PROOF_MISSING')
    process = admit(stopped=True, allow_admin_issue624=bool(admin_paper_id),
                    allow_market_open_suspend=allow_market_open_suspend,
                    allow_open_paper_carryover=allow_open_paper_carryover,
                    allow_postclose_pending=allow_postclose_pending,
                    allow_intraday_caucion=allow_intraday_caucion,
                    stopped_receipt=previous)
    allowed = ({'RUNNING', 'STOPPED'} if allow_market_open_suspend else
               {'WAITING_MARKET', 'STOPPED'} if current_provenance or admin_paper_id or closed_admin_prior
               else {'STOPPED'})
    if process not in allowed:
        raise ValueError('QUIESCENCE_STOPPED_PROCESS_REQUIRED')
    carryover = spot_carryover_snapshot() if allow_open_paper_carryover else None
    if allow_open_paper_carryover and previous.get('spot_carryover') != carryover:
        raise ValueError('QUIESCENCE_PRIOR_CARRYOVER_CHANGED')
    caucion = pending_caucion_snapshot() if (allow_postclose_pending or allow_intraday_caucion) else None
    if (allow_postclose_pending or allow_intraday_caucion) and previous.get('caucion_carryover') != caucion:
        raise ValueError('QUIESCENCE_PRIOR_CAUCION_CHANGED')
    return {'candidate_sha': candidate,
            'spot_carryover': carryover,
            'open_paper_carryover': allow_open_paper_carryover,
            'paused_postclose': allow_postclose_pending,
            'intraday_caucion': allow_intraday_caucion,
            'postclose_live_closed': allow_postclose_pending,
            'caucion_carryover': caucion,
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
        allow_market_open_suspend=False, allow_open_paper_carryover=False,
        allow_postclose_pending=False, allow_intraday_caucion=False):
    if allow_open_paper_carryover and (not allow_market_open_suspend or allow_admin_issue624):
        raise ValueError('QUIESCENCE_CARRYOVER_MODE_INVALID')
    if allow_postclose_pending and (not allow_market_open_suspend or
                                  not allow_open_paper_carryover or
                                  action not in {'drain', 'install'}):
        raise ValueError('QUIESCENCE_POSTCLOSE_MODE_INVALID')
    if allow_intraday_caucion and (not allow_market_open_suspend or
                                    allow_open_paper_carryover or allow_postclose_pending or
                                    action not in {'drain', 'install'}):
        raise ValueError('QUIESCENCE_INTRADAY_CAUCION_MODE_INVALID')
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
            if receipt is not None:
                if (receipt.get('candidate_sha') != candidate or receipt.get('status') != 'DRAINED' or
                        receipt.get('installation_started') is not False or
                        receipt.get('market_open_suspended') != allow_market_open_suspend or
                        receipt.get('open_paper_carryover') != allow_open_paper_carryover or
                        receipt.get('paused_postclose', False) != allow_postclose_pending or
                        receipt.get('intraday_caucion', False) != allow_intraday_caucion):
                    raise ValueError('QUIESCENCE_EXISTING_RECEIPT')
                exact_clean_stopped(receipt)
                admit(stopped=True, allow_market_open_suspend=allow_market_open_suspend,
                      allow_open_paper_carryover=allow_open_paper_carryover,
                      allow_postclose_pending=allow_postclose_pending,
                      allow_intraday_caucion=allow_intraday_caucion,
                      stopped_receipt=receipt)
                if allow_open_paper_carryover and spot_carryover_snapshot() != receipt.get('spot_carryover'):
                    raise ValueError('QUIESCENCE_PAUSED_LEDGER_CHANGED')
                if allow_postclose_pending and pending_caucion_snapshot() != receipt.get('caucion_carryover'):
                    raise ValueError('QUIESCENCE_PAUSED_CAUCION_CHANGED')
                if allow_intraday_caucion and pending_caucion_snapshot() != receipt.get('caucion_carryover'):
                    raise ValueError('QUIESCENCE_INTRADAY_CAUCION_CHANGED')
                print(json.dumps({'status': 'DRAINED', 'candidate_sha': candidate,
                                  'prepaused_runtime_adopted': True, 'real_orders_sent': 0,
                                  'ppi_watch_accessed': False}), flush=True)
                return receipt
            originals = {name: inspect(name) for name in OWNED}
            stopped_owned = all(v['State']['Status'] == 'exited' for v in originals.values())
            # After an installed OPEN failure SQLite may still say RUNNING,
            # including a completely flat book. Select the exact previous
            # stop receipt before considering that stale live heartbeat.
            if stopped_owned and allow_market_open_suspend:
                admin_paper_id = None
                process = None
            else:
                process = admit(stopped=not allow_postclose_pending, allow_admin_issue624=allow_admin_issue624,
                                allow_market_open_suspend=allow_market_open_suspend,
                                allow_open_paper_carryover=allow_open_paper_carryover,
                                allow_postclose_pending=allow_postclose_pending,
                                allow_intraday_caucion=allow_intraday_caucion, initial=True)
                admin_paper_id = admin_target_on_host() if allow_admin_issue624 else None
            if allow_market_open_suspend and admin_paper_id:
                raise ValueError('QUIESCENCE_OPEN_ADMIN_CANCEL_FORBIDDEN')
            if (stopped_owned and (process in {'STOPPED', 'WAITING_MARKET'} or
                                   allow_market_open_suspend)):
                receipt = adopt_stopped_after_failed_install(
                    candidate, folder, originals, admin_paper_id=admin_paper_id,
                    allow_market_open_suspend=allow_market_open_suspend,
                    allow_open_paper_carryover=allow_open_paper_carryover,
                    allow_postclose_pending=allow_postclose_pending,
                    allow_intraday_caucion=allow_intraday_caucion)
                persist(path, receipt)
            else:
                required_process = ('WAITING_MARKET' if allow_postclose_pending else
                                    'RUNNING' if allow_market_open_suspend else 'WAITING_MARKET')
                if process != required_process or any(v['State']['Status'] != 'running' for v in originals.values()):
                    raise ValueError('QUIESCENCE_RUNTIME_NOT_RUNNING_OR_EXACT_PRIOR_STOP')
                admit(allow_admin_issue624=bool(admin_paper_id),
                      allow_market_open_suspend=allow_market_open_suspend,
                      allow_open_paper_carryover=allow_open_paper_carryover,
                      allow_postclose_pending=allow_postclose_pending,
                      allow_intraday_caucion=allow_intraday_caucion)
                caucion_before = pending_caucion_snapshot() if allow_intraday_caucion else None
                receipt = {'candidate_sha': candidate, 'status': 'DRAINING',
                           'installation_started': False, 'stop_attempted': [],
                           'stop_evidence': {},
                           'containers': {n: {'id': v['Id'], 'image': v['Image']} for n, v in originals.items()},
                    'real_orders_sent': 0, 'ppi_watch_accessed': False,
                    'admin_cancel_paper_id': admin_paper_id,
                    'market_open_suspended': allow_market_open_suspend,
                    'open_paper_carryover': allow_open_paper_carryover,
                    'paused_postclose': allow_postclose_pending,
                    'intraday_caucion': allow_intraday_caucion,
                    'postclose_live_closed': allow_postclose_pending}
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
                      allow_market_open_suspend=allow_market_open_suspend,
                      allow_open_paper_carryover=allow_open_paper_carryover,
                      allow_postclose_pending=allow_postclose_pending,
                      allow_intraday_caucion=allow_intraday_caucion,
                      stopped_receipt=receipt)
                if allow_intraday_caucion and pending_caucion_snapshot() != caucion_before:
                    raise ValueError('QUIESCENCE_INTRADAY_CAUCION_CHANGED_AFTER_STOP')
                if admin_paper_id and admin_target_on_host() != admin_paper_id:
                    raise ValueError('QUIESCENCE_ADMIN_TARGET_CHANGED_AFTER_STOP')
                receipt.update(status='ADMIN_DRAINED_PENDING' if admin_paper_id else 'DRAINED',
                               inflight='ABSENT_OWNED_RUNTIME_STOPPED',
                               spot_carryover=spot_carryover_snapshot() if allow_open_paper_carryover else None,
                               caucion_carryover=pending_caucion_snapshot() if (
                                   allow_postclose_pending or allow_intraday_caucion) else None)
                if allow_postclose_pending and receipt['spot_carryover']['open_count'] != 2:
                    raise ValueError('QUIESCENCE_POSTCLOSE_SPOT_COUNT_CHANGED')
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
            if bool(receipt.get('open_paper_carryover', False)) != allow_open_paper_carryover:
                raise ValueError('QUIESCENCE_INSTALL_CARRYOVER_MODE_CHANGED')
            if bool(receipt.get('paused_postclose', False)) != allow_postclose_pending:
                raise ValueError('QUIESCENCE_INSTALL_POSTCLOSE_MODE_CHANGED')
            if bool(receipt.get('intraday_caucion', False)) != allow_intraday_caucion:
                raise ValueError('QUIESCENCE_INSTALL_INTRADAY_CAUCION_MODE_CHANGED')
            admit(stopped=True, allow_market_open_suspend=allow_market_open_suspend,
                  allow_open_paper_carryover=allow_open_paper_carryover,
                  allow_postclose_pending=allow_postclose_pending,
                  allow_intraday_caucion=allow_intraday_caucion,
                  stopped_receipt=receipt)
            if allow_open_paper_carryover and spot_carryover_snapshot() != receipt.get('spot_carryover'):
                raise ValueError('QUIESCENCE_INSTALL_CARRYOVER_CHANGED')
            if allow_postclose_pending and pending_caucion_snapshot() != receipt.get('caucion_carryover'):
                raise ValueError('QUIESCENCE_INSTALL_CAUCION_CHANGED')
            if allow_intraday_caucion and pending_caucion_snapshot() != receipt.get('caucion_carryover'):
                raise ValueError('QUIESCENCE_INSTALL_INTRADAY_CAUCION_CHANGED')
            receipt.update(status='HOST_INSTALLATION_STARTED', installation_started=True)
        elif action == 'verify-carryover':
            if not receipt or receipt.get('installation_started') is not True or not (
                    receipt.get('open_paper_carryover') or receipt.get('intraday_caucion')):
                raise ValueError('QUIESCENCE_CARRYOVER_NOT_INSTALLED')
            receipt['carryover_verified_count'] = (verify_spot_carryover(receipt)
                                                   if receipt.get('open_paper_carryover') else 0)
            if receipt.get('paused_postclose') is True:
                if pending_caucion_snapshot() != receipt.get('caucion_carryover'):
                    raise ValueError('QUIESCENCE_CAUCION_CARRYOVER_CHANGED')
                receipt['caucion_verified_count'] = 1
            if receipt.get('intraday_caucion') is True:
                if pending_caucion_snapshot() != receipt.get('caucion_carryover'):
                    raise ValueError('QUIESCENCE_INTRADAY_CAUCION_CARRYOVER_CHANGED')
                receipt['caucion_verified_count'] = 1
            receipt['carryover_verified_at'] = datetime.now(timezone.utc).isoformat()
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
                                            'install', 'verify-carryover', 'recover'], required=True)
    parser.add_argument('--admin-cancel-issue624', action='store_true')
    parser.add_argument('--market-open-suspend', action='store_true')
    parser.add_argument('--carry-open-paper-spots', action='store_true')
    parser.add_argument('--paused-postclose', action='store_true')
    parser.add_argument('--intraday-caucion', action='store_true')
    parser.add_argument('--allow-validation-pending',action='store_true',
                        help='classify installed validation pending during failure cleanup; never validates runtime')
    args = parser.parse_args(argv)
    if args.allow_validation_pending and args.action!='recover':
        parser.error('--allow-validation-pending requires --action recover')
    if args.admin_cancel_issue624 and args.action not in {'drain', 'admin-begin', 'admin-commit'}:
        parser.error('--admin-cancel-issue624 only for controlled admin actions')
    if args.market_open_suspend and args.action not in {'drain', 'install'}:
        parser.error('--market-open-suspend only for drain and install')
    if args.carry_open_paper_spots and (not args.market_open_suspend or
                                        args.admin_cancel_issue624 or args.action not in {'drain', 'install'}):
        parser.error('--carry-open-paper-spots requires intraday drain/install without admin cancel')
    if args.paused_postclose and (not args.market_open_suspend or
                                  not args.carry_open_paper_spots or
                                  args.action not in {'drain','install'}):
        parser.error('--paused-postclose requires suspended carryover drain/install')
    if args.intraday_caucion and (not args.market_open_suspend or
                                  args.carry_open_paper_spots or args.paused_postclose or
                                  args.action not in {'drain','install'}):
        parser.error('--intraday-caucion requires intraday drain/install without spot carryover')
    options = {}
    if args.admin_cancel_issue624:
        options['allow_admin_issue624'] = True
    if args.market_open_suspend:
        options['allow_market_open_suspend'] = True
    if args.carry_open_paper_spots:
        options['allow_open_paper_carryover'] = True
    if args.paused_postclose:
        options['allow_postclose_pending'] = True
    if args.intraday_caucion:
        options['allow_intraday_caucion'] = True
    receipt = run(args.candidate_sha, args.action, **options)
    if receipt['status']=='RECOVERY_UNVERIFIED':return 2
    if receipt['status']=='FIX_FORWARD_VALIDATION_REQUIRED' and not args.allow_validation_pending:return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
