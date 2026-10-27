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
from zoneinfo import ZoneInfo

ROOT = Path('/opt/porota-trading/data/deploy')
OPERATOR_PAUSE_DAY = '2026-10-27'
OPERATOR_PREOPEN_TIMER = 'porota-preopen-rc6.timer'
OPERATOR_PAUSE_RUN = 'A125-40501523106-1'
OPERATOR_PAUSE_SOURCE = '3fd020f408275a490e911e06f60a1ddc89153fe5'
OPERATOR_PAUSE_PRODUCT = '2bd9dbf576b253b8f83aab159c784d7fa7d06d89'
OPERATOR_PAUSE_IMAGE = 'sha256:586464c20b74ab3a7ba842df8dfafcd76f21eb4c7bed553f23f6f187924ff735'


def _operator_pause_json(path, limit):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError('SCHEDULER_OPERATOR_PAUSE_PATH_UNSAFE')
    raw = path.read_bytes()
    def unique_pairs(items):
        output = {}
        for key, value in items:
            if key in output:
                raise ValueError('SCHEDULER_OPERATOR_PAUSE_DUPLICATE_KEY')
            output[key] = value
        return output
    def invalid_constant(_):
        raise ValueError('SCHEDULER_OPERATOR_PAUSE_NONFINITE')
    return json.loads(raw, object_pairs_hook=unique_pairs,
                      parse_constant=invalid_constant), hashlib.sha256(raw).hexdigest()


def exact_operator_preopen_pause(*, require_original_marker=False):
    """Prove the sole A125 operator pause, without changing systemd or PPI.

    This is not permission to resume PAPER. It only explains the one disabled
    timer on the exact 2026-10-27 ART trading day. The original A92 marker is
    checked before the install, not after Deploy V2 writes its new marker.
    """
    if ROOT.is_symlink() or not ROOT.is_dir():
        raise ValueError('SCHEDULER_OPERATOR_PAUSE_ROOT_UNSAFE')
    now_utc = datetime.now(timezone.utc)
    day = now_utc.astimezone(
        ZoneInfo('America/Argentina/Buenos_Aires')).date().isoformat()
    if day != OPERATOR_PAUSE_DAY:
        raise ValueError('SCHEDULER_OPERATOR_PAUSE_DAY_EXPIRED')
    receipt, receipt_sha = _operator_pause_json(
        ROOT/'operator-preopen-pause-20261027.json', 65536)
    gate, gate_sha = _operator_pause_json(ROOT/'paper_scalping_preopen_gate_v1.json', 4096)
    try:
        issued = datetime.fromisoformat(receipt['issued_at_utc'])
        completed = datetime.fromisoformat(receipt['completed_at_utc'])
        times_valid = (issued.tzinfo is not None and completed.tzinfo is not None and
                       0 <= (completed-issued).total_seconds() <= 300 and
                       0 <= (now_utc-completed).total_seconds() and
                       completed.astimezone(ZoneInfo('America/Argentina/Buenos_Aires'))
                       .date().isoformat() == OPERATOR_PAUSE_DAY)
    except (KeyError, TypeError, ValueError, AttributeError):
        times_valid = False
    if (type(receipt) is not dict or type(gate) is not dict or
            not times_valid or
            any(type(receipt.get(k)) is not dict for k in
                ('timer_before','timer_after','service_before','service_after',
                 'containers_before','containers_after','paper_before','paper_after')) or
            receipt.get('schema') != 'rc6.operator-preopen-pause.v1' or
            receipt.get('status') != 'PAUSED_NO_GO' or
            receipt.get('operation_id') != OPERATOR_PAUSE_RUN or
            receipt.get('trade_day_ar') != OPERATOR_PAUSE_DAY or
            receipt.get('source_sha') != OPERATOR_PAUSE_SOURCE or
            receipt.get('product_sha') != OPERATOR_PAUSE_PRODUCT or
            receipt.get('image_id') != OPERATOR_PAUSE_IMAGE or
            receipt.get('gate_after_sha256') != gate_sha or
            receipt.get('timer_before') != {'enabled':'enabled','active':'active'} or
            receipt.get('timer_after') != {'enabled':'disabled','active':'inactive'} or
            receipt.get('service_before') != receipt.get('service_after') or
            receipt.get('service_after', {}).get('active') not in {'failed','inactive'} or
            receipt.get('service_after', {}).get('enabled') != 'static' or
            receipt.get('containers_before') != receipt.get('containers_after') or
            receipt.get('paper_before', {}).get('mode') != 'PRODUCTION_PAPER' or
            receipt.get('paper_before', {}).get('real_orders_sent') != 0 or
            receipt.get('paper_after', {}).get('mode') != 'PRODUCTION_PAPER' or
            receipt.get('paper_after', {}).get('real_orders_sent') != 0 or
            receipt.get('ppi_watch_accessed') is not False or
            receipt.get('provider_requests') != 0 or
            receipt.get('admission_allowed') is not False or
            gate.get('schema') != 'RC6_PAPER_PREOPEN_GATE_V1' or
            gate.get('status') != 'NO_GO_PAPER_SCALPING' or
            gate.get('trade_day_ar') != OPERATOR_PAUSE_DAY or
            gate.get('candidate_sha') != OPERATOR_PAUSE_SOURCE or
            gate.get('phase') != 'OPERATOR_PAUSE' or
            receipt.get('gate_after') != {k:gate.get(k) for k in
                  ('status','trade_day_ar','candidate_sha','phase')}):
        raise ValueError('SCHEDULER_OPERATOR_PAUSE_EVIDENCE_RED')
    if require_original_marker:
        marker, marker_sha = _operator_pause_json(ROOT/'CURRENT_STATE_V2.json', 65536)
        if (marker_sha != receipt.get('marker_sha256') or
                marker.get('candidate_sha') != OPERATOR_PAUSE_SOURCE or
                marker.get('deploy_sha') != OPERATOR_PAUSE_PRODUCT or
                marker.get('image_id') != OPERATOR_PAUSE_IMAGE or
                marker.get('validation_status') != 'VALIDATED_RUNTIME' or
                marker.get('mode') != 'PRODUCTION_PAPER' or
                type(marker.get('real_orders_sent')) is not int or
                marker['real_orders_sent'] != 0 or
                marker.get('real_order_routes') != 'NOT_CALLED'):
            raise ValueError('SCHEDULER_OPERATOR_PAUSE_MARKER_RED')
    return receipt_sha


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


def resolve_closed_recovery():
    """A stopped CLOSED/flat install must keep its timers paused."""
    observed = []
    for name in ('porota_production_observer', 'porota_production_dashboard'):
        status, code = command('docker', 'inspect', '-f', '{{.State.Status}}', name)
        if code:
            raise ValueError('SCHEDULER_CLOSED_RECOVERY_IDENTITY_UNKNOWN')
        observed.append(status)
    if observed == ['running', 'running']:
        return 'restore'
    if observed == ['exited', 'exited']:
        return 'hold-closed-flat'
    raise ValueError('SCHEDULER_CLOSED_RECOVERY_MIXED_OR_UNKNOWN')


def require_rescue_degraded_hold(candidate):
    """Leave timers paused only with the exact A92 incident and PAPER book."""
    sys.path.insert(0, '/opt/porota-trading')
    from scripts import rc6_deploy_quiescence as q
    path = q.ROOT/'data/deploy'/('quiescence-' + candidate + '.json')
    if path.is_symlink() or (path.exists() and
                            (not path.is_file() or path.stat().st_size > 65536)):
        raise ValueError('SCHEDULER_RESCUE_RECEIPT_UNSAFE')
    if not path.exists():
        # Scheduler suspension can succeed before the first quiescence
        # persist. Keep it paused only if the exact original A92 image is
        # still running, with the exact A92 PAPER/0 flat book and broker.
        # The clock or child-health phase may have moved after suspension;
        # holding timers is safe, but this never authorizes a new drain.
        observed = {name: q.inspect(name) for name in q.OWNED}
        if (all(value['State']['Status'] == 'exited' and
                value['Image'] == q.RESCUE_IMAGE_ID and
                re.fullmatch('[0-9a-f]{64}',value['Id'])
                for value in observed.values())):
            # A138 died after the original A92 pair was cleanly stopped but
            # before the new runtime existed.  With no new candidate receipt,
            # leave its already-paused timers alone only after the exact old
            # receipt proves both stops and the PAPER/0 book.  No restart.
            prior_sha='dd22a1b0761cc4f231813edb6b502d1dd21bb8de'
            prior_path=q.ROOT/'data/deploy'/('quiescence-'+prior_sha+'.json')
            if (prior_path.is_symlink() or not prior_path.is_file() or
                    prior_path.stat().st_size > 65536):
                raise ValueError('SCHEDULER_A139_PRIOR_RECEIPT_UNSAFE')
            prior=json.loads(prior_path.read_bytes())
            if (prior.get('candidate_sha') != prior_sha or
                    prior.get('status') not in {'HOST_INSTALLATION_STARTED',
                                                'FIX_FORWARD_VALIDATION_REQUIRED'} or
                    prior.get('installation_started') is not True):
                raise ValueError('SCHEDULER_A139_PRIOR_RECEIPT_RED')
            q.exact_rescue_receipt(prior)
            q.exact_clean_stopped(prior)
            if q.admit(stopped=True,allow_degraded_closed_rescue=True,
                       stopped_receipt=prior) != 'STOPPED':
                raise ValueError('SCHEDULER_A139_STOPPED_BOOK_CHANGED')
            q.critical_admission()
            return 'RESCUE_A92_STOPPED_A138_UNVALIDATED_HELD'
        if any(value['State']['Status'] != 'running' or
               value['Image'] != q.RESCUE_IMAGE_ID or
               not re.fullmatch('[0-9a-f]{64}', value['Id'])
               for value in observed.values()):
            raise ValueError('SCHEDULER_RESCUE_NO_DRAIN_RUNTIME_CHANGED')
        book = q.rescue_flat_book()
        if (book['source_sha'] != q.RESCUE_SOURCE_SHA or
                (book['process_state'],book['session_state']) not in
                {('DEGRADED','BOOT_CALENDAR'),('STOPPED','CLOSED')}):
            raise ValueError('SCHEDULER_RESCUE_NO_DRAIN_BOOK_CHANGED')
        q.critical_admission()
        return 'RESCUE_NO_DRAIN_A92_UNVALIDATED_HELD'
    receipt = json.loads(path.read_bytes())
    if (receipt.get('candidate_sha') != candidate or
            receipt.get('degraded_closed_rescue') is not True or
            receipt.get('rescue_pre_state') not in (
                ['DEGRADED', 'BOOT_CALENDAR'], ['STOPPED', 'CLOSED']) or
            receipt.get('rescue_source_sha') != q.RESCUE_SOURCE_SHA or
            receipt.get('rescue_image_id') != q.RESCUE_IMAGE_ID or
            type(receipt.get('real_orders_sent')) is not int or
            receipt['real_orders_sent'] != 0 or
            receipt.get('ppi_watch_accessed') is not False or
            set(receipt.get('containers', {})) != set(q.OWNED)):
        raise ValueError('SCHEDULER_RESCUE_RECEIPT_CHANGED')
    q.exact_rescue_receipt(receipt)
    observed = {name: q.inspect(name) for name in q.OWNED}
    candidate_runtime = receipt.get('candidate_runtime_after_failure')
    if candidate_runtime is not None:
        intent = receipt.get('candidate_image_intent')
        if (receipt.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED' or
                receipt.get('installation_started') is not True or
                not isinstance(intent, dict) or not isinstance(candidate_runtime, dict) or
                candidate_runtime.get('status') not in {'exited', 'running'} or
                set(candidate_runtime.get('containers', {})) != set(q.OWNED) or
                candidate_runtime.get('flat_book') != q.rescue_flat_book() or
                candidate_runtime.get('critical_admission') != q.critical_admission()):
            raise ValueError('SCHEDULER_RESCUE_CANDIDATE_PROVENANCE_RED')
        for name, value in observed.items():
            expected = candidate_runtime['containers'][name]
            if (value['Id'] != expected.get('id') or
                    value['Image'] != expected.get('image') or
                    value['Image'] != intent.get('image_id') or
                    q.lifecycle(value) != expected.get('lifecycle') or
                    value['State']['Status'] != candidate_runtime['status']):
                raise ValueError('SCHEDULER_RESCUE_CANDIDATE_IDENTITY_CHANGED')
        return 'RESCUE_CANDIDATE_UNVALIDATED_HELD'
    if any(observed[name]['Id'] != receipt['containers'][name]['id'] or
           observed[name]['Image'] != q.RESCUE_IMAGE_ID for name in q.OWNED):
        raise ValueError('SCHEDULER_RESCUE_RUNTIME_IDENTITY_CHANGED')
    states = {value['State']['Status'] for value in observed.values()}
    if (states == {'running'} and
            receipt.get('status') == 'ORIGINAL_RUNTIME_DEGRADED_RESUMED' and
            receipt.get('installation_started') is False):
        if q.admit(allow_degraded_closed_rescue=True) not in {'DEGRADED', 'STOPPED'}:
            raise ValueError('SCHEDULER_RESCUE_RUNNING_BOOK_CHANGED')
    elif (states == {'exited'} and
          receipt.get('status') == 'FIX_FORWARD_VALIDATION_REQUIRED' and
          receipt.get('installation_started') is True):
        q.exact_clean_stopped(receipt)
        if q.admit(stopped=True, allow_degraded_closed_rescue=True,
                   stopped_receipt=receipt) not in {'DEGRADED', 'STOPPED'}:
            raise ValueError('SCHEDULER_RESCUE_STOPPED_BOOK_CHANGED')
    else:
        raise ValueError('SCHEDULER_RESCUE_RUNTIME_UNVERIFIED')
    q.critical_admission()
    return 'RESCUE_DEGRADED_HELD'


def require_adopted_rescue_hold(candidate):
    """Never rearm the prior failed candidate while it awaits fix-forward."""
    sys.path.insert(0, '/opt/porota-trading')
    from scripts import rc6_deploy_quiescence as q
    folder = q.ROOT/'data/deploy'
    path = folder/('quiescence-' + candidate + '.json')
    if path.is_symlink() or (path.exists() and
                            (not path.is_file() or path.stat().st_size > 65536)):
        raise ValueError('SCHEDULER_ADOPTED_RECEIPT_UNSAFE')
    observed = {name:q.inspect(name) for name in q.OWNED}
    if not path.exists():
        q.adopt_failed_rescue_candidate_stopped(candidate,folder,observed)
        return 'ADOPTED_RESCUE_NO_DRAIN_HELD'
    receipt=json.loads(path.read_bytes())
    if (receipt.get('candidate_sha') != candidate or
            receipt.get('adopted_failed_rescue') is not True or
            receipt.get('real_orders_sent') != 0 or
            receipt.get('ppi_watch_accessed') is not False or
            set(receipt.get('containers',{})) != set(q.OWNED) or
            any(observed[name]['Id'] != receipt['containers'][name].get('id') or
                observed[name]['Image'] != receipt['containers'][name].get('image')
                for name in q.OWNED)):
        raise ValueError('SCHEDULER_ADOPTED_RUNTIME_IDENTITY_CHANGED')
    book=q.rescue_flat_book()
    if (book['source_sha'] != receipt.get('adopted_from_candidate') or
            q.critical_admission() != receipt.get('critical_admission')):
        raise ValueError('SCHEDULER_ADOPTED_BOOK_OR_CRITICAL_RED')
    if ((receipt.get('status') == 'PRIOR_FAILED_CANDIDATE_HELD_STOPPED' and
            receipt.get('installation_started') is False) or
            (receipt.get('status') == 'FIX_FORWARD_VALIDATION_REQUIRED' and
             receipt.get('installation_started') is True)) and (
            all(v['State']['Status']=='exited' for v in observed.values()) and
            book == receipt.get('adopted_book') and
            all(q.lifecycle(observed[name]) == receipt['stop_evidence'][name].get('after')
                and receipt['stop_evidence'][name].get('classification') ==
                'CANDIDATE_CLEAN_EXIT' for name in q.OWNED)):
        return 'ADOPTED_RESCUE_PRIOR_STOPPED_HELD'
    raise ValueError('SCHEDULER_ADOPTED_AFTER_INSTALL_UNVERIFIED')


def require_exact_stopped_closed_flat(candidate):
    """Prove the installed failed candidate's exact clean stop and flat PAPER ledger."""
    sys.path.insert(0, '/opt/porota-trading')
    from scripts import rc6_deploy_quiescence as quiescence
    originals = {name: quiescence.inspect(name) for name in quiescence.OWNED}
    if any(value['State']['Status'] != 'exited' for value in originals.values()):
        raise ValueError('SCHEDULER_STOPPED_CLOSED_IDENTITY_RED')
    path = quiescence.ROOT/'data/deploy'/('quiescence-' + candidate + '.json')
    if (path.is_symlink() or not path.is_file() or path.stat().st_size > 65536):
        raise ValueError('SCHEDULER_STOPPED_CLOSED_RECEIPT_UNSAFE')
    receipt = json.loads(path.read_bytes())
    if (receipt.get('candidate_sha') != candidate or
            receipt.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED' or
            receipt.get('installation_started') is not True or
            receipt.get('inflight') != 'ABSENT_OWNED_RUNTIME_STOPPED' or
            set(receipt.get('containers', {})) != set(quiescence.OWNED) or
            any(originals[name]['Id'] != receipt['containers'][name].get('id') or
                originals[name]['Image'] != receipt['containers'][name].get('image')
                for name in quiescence.OWNED)):
        raise ValueError('SCHEDULER_STOPPED_CLOSED_INSTALL_IDENTITY_RED')
    quiescence.exact_clean_stopped(receipt)
    if (type(receipt.get('real_orders_sent')) is not int or
            receipt['real_orders_sent'] != 0 or
            receipt.get('ppi_watch_accessed') is not False or
            receipt.get('market_open_suspended') is not False or
            receipt.get('open_paper_carryover') is not False or
            receipt.get('spot_carryover') is not None or
            receipt.get('caucion_carryover') is not None or
            receipt.get('paused_postclose') is not False or
            receipt.get('intraday_caucion') is not False or
            receipt.get('pending_caucion') is not False or
            quiescence.admit(stopped=True, stopped_receipt=receipt) not in ('RUNNING', 'STOPPED')):
        raise ValueError('SCHEDULER_STOPPED_CLOSED_FLAT_RED')
    quiescence.critical_admission()


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


def operator_preopen_timer_disabled():
    """True only for the exact A125 disabled timer and an idle service."""
    enabled, rc1 = command('systemctl', 'is-enabled', OPERATOR_PREOPEN_TIMER)
    active, rc2 = command('systemctl', 'is-active', OPERATOR_PREOPEN_TIMER)
    service, rc3 = command('systemctl', 'show', OPERATOR_PREOPEN_TIMER,
                           '-p', 'Unit', '--value')
    if (rc1 != 1 or enabled != 'disabled' or rc2 != 3 or active != 'inactive' or
            rc3 != 0 or service != 'porota-preopen-rc6.service'):
        return False
    service_state, _ = command('systemctl', 'is-active', service)
    return service_state in {'inactive', 'failed'}


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
            if receipt.get('operator_pause_timer') is not None:
                if (receipt['operator_pause_timer'] != OPERATOR_PREOPEN_TIMER or
                        receipt.get('operator_pause_receipt_sha256') !=
                        exact_operator_preopen_pause() or
                        not operator_preopen_timer_disabled()):
                    raise ValueError('SCHEDULER_OPERATOR_PAUSE_RECEIPT_CHANGED')
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
        operator_pause_sha = None
        if OPERATOR_PREOPEN_TIMER in names and all(
                state == ('active','enabled') if name != OPERATOR_PREOPEN_TIMER
                else state == ('inactive','disabled')
                for name, state in zip(names, states)):
            operator_pause_sha = exact_operator_preopen_pause(
                require_original_marker=True)
            if not operator_preopen_timer_disabled():
                raise ValueError('SCHEDULER_OPERATOR_PREOPEN_TIMER_CHANGED')
        for name, state in zip(names, states):
            if state != ('active', 'enabled') and not (
                    operator_pause_sha is not None and
                    name == OPERATOR_PREOPEN_TIMER and state == ('inactive','disabled')):
                raise ValueError('SCHEDULER_EXPECTED_TIMER_NOT_ACTIVE:' + name)
        receipt = {'candidate_sha': candidate, 'policy_sha256': digest,
                   'timers': names, 'ppi_before': before, 'attempted': [],
                   'status': 'SUSPENDING', 'recorded_at': datetime.now(timezone.utc).isoformat()}
        if operator_pause_sha is not None:
            receipt['operator_pause_timer'] = OPERATOR_PREOPEN_TIMER
            receipt['operator_pause_receipt_sha256'] = operator_pause_sha
        persist(path, receipt)
        for name in names:
            receipt['attempted'].append(name)
            persist(path, receipt)
            if operator_pause_sha is not None and name == OPERATOR_PREOPEN_TIMER:
                if not operator_preopen_timer_disabled():
                    raise ValueError('SCHEDULER_OPERATOR_PREOPEN_TIMER_CHANGED')
                continue
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
        operator_pause_timer = receipt.get('operator_pause_timer')
        if operator_pause_timer is not None:
            if (operator_pause_timer != OPERATOR_PREOPEN_TIMER or
                    operator_pause_timer not in names or
                    receipt.get('operator_pause_receipt_sha256') !=
                    exact_operator_preopen_pause() or
                    not operator_preopen_timer_disabled()):
                raise ValueError('SCHEDULER_OPERATOR_PAUSE_RECEIPT_CHANGED')
        if action == 'recover-open':
            action = resolve_open_recovery()
        if action == 'recover-closed':
            action = resolve_closed_recovery()
        if action == 'recover-degraded-closed':
            action = 'hold-rescue-degraded'
        if action == 'recover-adopted-rescue':
            action = 'hold-adopted-rescue'
        if action in {'hold-rescue-degraded','hold-adopted-rescue'}:
            if (receipt.get('status') != 'SUSPENDED' or
                    set(receipt.get('attempted', [])) != set(names)):
                raise ValueError('SCHEDULER_RESCUE_PAUSE_RECEIPT_RED')
            for name in names:
                service, code = command('systemctl', 'show', name, '-p', 'Unit', '--value')
                timer_ok = (operator_preopen_timer_disabled() if name == operator_pause_timer
                            else (command('systemctl', 'is-active', name)[0] == 'inactive' and
                                  command('systemctl', 'is-enabled', name)[0] == 'enabled'))
                if (not timer_ok or
                        code or not service.endswith('.service') or
                        command('systemctl', 'is-active', service)[0] in
                        ('active', 'activating', 'reloading', 'deactivating')):
                    raise ValueError('SCHEDULER_RESCUE_TIMER_OR_JOB_CHANGED:' + name)
            status = (require_rescue_degraded_hold(candidate) if
                      action == 'hold-rescue-degraded' else
                      require_adopted_rescue_hold(candidate))
            if ppi_snapshot() != before:
                raise ValueError('SCHEDULER_PPI_WATCH_CHANGED')
            receipt['rescue_failure_disposition'] = status
            receipt['rescue_failure_recorded_at'] = datetime.now(timezone.utc).isoformat()
            persist(path, receipt)
            print(json.dumps({'status': status, 'candidate_sha': candidate,
                              'timer_count': len(names), 'ppi_watch_accessed': False}, sort_keys=True))
            return receipt
        if action in ('hold-open-flat', 'hold-closed-flat'):
            if (receipt.get('status') != 'SUSPENDED' or
                    set(receipt.get('attempted', [])) != set(names)):
                raise ValueError('SCHEDULER_OPEN_HOLD_RECEIPT_RED')
            for name in names:
                timer_ok = (operator_preopen_timer_disabled() if name == operator_pause_timer
                            else (command('systemctl', 'is-active', name)[0] == 'inactive' and
                                  command('systemctl', 'is-enabled', name)[0] == 'enabled'))
                if not timer_ok:
                    raise ValueError('SCHEDULER_OPEN_HOLD_TIMER_CHANGED:' + name)
                service, code = command('systemctl', 'show', name, '-p', 'Unit', '--value')
                if (code or not service.endswith('.service') or
                        command('systemctl', 'is-active', service)[0] in
                        ('active', 'activating', 'reloading', 'deactivating')):
                    raise ValueError('SCHEDULER_OPEN_HOLD_JOB_ACTIVE:' + name)
            if action == 'hold-open-flat':
                require_exact_stopped_open_flat(candidate)
            else:
                require_exact_stopped_closed_flat(candidate)
            if ppi_snapshot() != before:
                raise ValueError('SCHEDULER_PPI_WATCH_CHANGED')
            print(json.dumps({'status': 'STOPPED_OPEN_HELD' if action == 'hold-open-flat' else 'STOPPED_CLOSED_HELD', 'candidate_sha': candidate,
                              'timer_count': len(names), 'ppi_watch_accessed': False}, sort_keys=True))
            return receipt
        if action == 'restore':
            require_owned_running()
            for name in receipt['attempted']:
                if name == operator_pause_timer:
                    if not operator_preopen_timer_disabled():
                        raise ValueError('SCHEDULER_OPERATOR_PREOPEN_TIMER_CHANGED')
                    continue
                _, code = command('systemctl', 'start', name)
                if code or command('systemctl', 'is-active', name)[0] != 'active':
                    raise ValueError('SCHEDULER_RESTORE_FAILED:' + name)
        elif action == 'verify':
            # The preceding restore action durably transitions the exact
            # receipt before this independent verification step runs.
            if receipt.get('status') != 'RESTORED':
                raise ValueError('SCHEDULER_RESTORE_RECEIPT_MISSING')
            for name in names:
                if name == operator_pause_timer:
                    if not operator_preopen_timer_disabled():
                        raise ValueError('SCHEDULER_OPERATOR_PREOPEN_TIMER_CHANGED')
                    continue
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
                                           'hold-open-flat', 'recover-open',
                                           'hold-closed-flat', 'recover-closed',
                                           'recover-degraded-closed',
                                           'recover-adopted-rescue'], required=True)
    parser.add_argument('--policy-b64', required=True)
    args = parser.parse_args()
    run(args.candidate_sha, args.action, args.policy_b64)


if __name__ == '__main__':
    main()
