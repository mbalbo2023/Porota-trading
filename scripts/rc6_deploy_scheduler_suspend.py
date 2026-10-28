#!/usr/bin/env python3
"""Temporarily pause only versioned RC6 timers for one intraday Deploy V2.

The receipt is written before each systemd mutation. Restore is idempotent on
failure; the deployed host control plane normally restores the timers.
"""
import argparse
import base64
import binascii
import gzip
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
import types
import zlib
from zoneinfo import ZoneInfo

ROOT = Path('/opt/porota-trading/data/deploy')
OPERATOR_PAUSE_DAY = '2026-10-27'
OPERATOR_PREOPEN_TIMER = 'porota-preopen-rc6.timer'
OPERATOR_PAUSE_RUN = 'A125-40501523106-1'
OPERATOR_PAUSE_SOURCE = '3fd020f408275a490e911e06f60a1ddc89153fe5'
OPERATOR_PAUSE_PRODUCT = '2bd9dbf576b253b8f83aab159c784d7fa7d06d89'
OPERATOR_PAUSE_IMAGE = 'sha256:586464c20b74ab3a7ba842df8dfafcd76f21eb4c7bed553f23f6f187924ff735'
# A151 is a fresh, single-day NO_GO decision. Its sidecars do not overwrite
# A125's live gate, whose exact bytes remain part of the A138/A143 evidence.
A151_OPERATOR_PAUSE_DAY = '2026-10-28'
A151_OPERATOR_PAUSE_SOURCE = '2325eba329878c62a61cb47bb66d33ccf279bd4a'
A151_OPERATOR_PAUSE_DEPLOY = '6676267140907a17e342a664f718cfdcf9b3ce72'
A151_OPERATOR_PAUSE_IMAGE = 'sha256:90e54acb1189a03acbed4b9228943ab556bdbe3a01fd515285951fc90eda93e7'
A151_OPERATOR_RECEIPT = 'operator-preopen-pause-20261028.json'
A151_PAPER_HOLD = 'paper_scalping_operator_hold_20261028.json'
FAILED_A138_SUSPENSION_CANDIDATE = 'dd22a1b0761cc4f231813edb6b502d1dd21bb8de'
A143_PENDING_CANDIDATE = '2325eba329878c62a61cb47bb66d33ccf279bd4a'
A169_STOPPED_CANDIDATE = 'b778c8063e96574ddf859907abb99fae6f4e3484'
IOL_OAUTH_GUARD_TIMER = 'porota-iol-oauth-health-rc6.timer'
IOL_OAUTH_GUARD_SERVICE = 'porota-iol-oauth-health-rc6.service'
# Legacy 16-timer policy that generated A138/A143 immutable receipts.
A138_ORIGINAL_TIMER_POLICY_SHA256 = 'dbfbe7d7036075bef0cc39eb546093ce1a879b454994c87543726835cf80066e'


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


def exact_operator_preopen_pause(*, require_original_marker=False,
                                 historical_hold_only=False):
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
    # Historical evidence is useful to explain why preopen remains DISABLED;
    # it never grants permission to rearm or to resume PAPER on a new day.
    if day != OPERATOR_PAUSE_DAY and not historical_hold_only:
        raise ValueError('SCHEDULER_OPERATOR_PAUSE_DAY_EXPIRED')
    if historical_hold_only and require_original_marker:
        raise ValueError('SCHEDULER_HISTORICAL_HOLD_NOT_ORIGINAL_MARKER')
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


def operator_preopen_hold_evidence():
    """Verify dated A125 receipt only to KEEP preopen disabled, never rearm."""
    today = datetime.now(ZoneInfo('America/Argentina/Buenos_Aires')).date().isoformat()
    return (exact_operator_preopen_pause() if today == OPERATOR_PAUSE_DAY
            else exact_operator_preopen_pause(historical_hold_only=True))


def exact_a151_operator_preopen_pause():
    """Attest the 28-Oct A151 sidecar without erasing A125 historical proof.

    The receipt attests the A143/PAPER-0 host at issuance, not a permanently
    pinned release marker: Deploy V2 is expected to update CURRENT_STATE_V2.
    It never authorizes PAPER, timer activation or a future trading day.
    """
    now_utc = datetime.now(timezone.utc)
    day = now_utc.astimezone(
        ZoneInfo('America/Argentina/Buenos_Aires')).date().isoformat()
    if day != A151_OPERATOR_PAUSE_DAY:
        raise ValueError('SCHEDULER_A151_OPERATOR_DAY_EXPIRED')
    if ROOT.is_symlink() or not ROOT.is_dir():
        raise ValueError('SCHEDULER_A151_ROOT_UNSAFE')
    a125_sha = exact_operator_preopen_pause(historical_hold_only=True)
    old_gate_path = ROOT/'paper_scalping_preopen_gate_v1.json'
    if old_gate_path.is_symlink() or not old_gate_path.is_file():
        raise ValueError('SCHEDULER_A151_LEGACY_GATE_UNSAFE')
    old_gate_sha = hashlib.sha256(old_gate_path.read_bytes()).hexdigest()
    receipt_path = ROOT/A151_OPERATOR_RECEIPT
    hold_path = ROOT/A151_PAPER_HOLD
    if (any(p.is_symlink() or not p.is_file() or p.stat().st_nlink != 1
            for p in (receipt_path, hold_path))):
        raise ValueError('SCHEDULER_A151_SIDECAR_PATH_UNSAFE')
    receipt, receipt_sha = _operator_pause_json(receipt_path, 65536)
    hold, hold_sha = _operator_pause_json(hold_path, 4096)
    try:
        issued = datetime.fromisoformat(receipt['issued_at_utc'])
        completed = datetime.fromisoformat(receipt['completed_at_utc'])
        held_at = datetime.fromisoformat(hold['issued_at_ar'])
        times_valid = (
            issued.tzinfo is not None and completed.tzinfo is not None and
            issued.utcoffset().total_seconds() == 0 and
            completed.utcoffset().total_seconds() == 0 and
            held_at.tzinfo is not None and
            held_at.utcoffset() == held_at.astimezone(
                ZoneInfo('America/Argentina/Buenos_Aires')).utcoffset() and
            0 <= (completed - issued).total_seconds() <= 300 and
            0 <= (now_utc - completed).total_seconds() and
            issued.astimezone(ZoneInfo('America/Argentina/Buenos_Aires'))
                .date().isoformat() == A151_OPERATOR_PAUSE_DAY and
            completed.astimezone(ZoneInfo('America/Argentina/Buenos_Aires'))
                .date().isoformat() == A151_OPERATOR_PAUSE_DAY and
            held_at.astimezone(ZoneInfo('America/Argentina/Buenos_Aires'))
                .date().isoformat() == A151_OPERATOR_PAUSE_DAY and
            issued <= held_at <= completed)
    except (KeyError, TypeError, ValueError, AttributeError):
        times_valid = False
    operation = receipt.get('operation_id') if type(receipt) is dict else None
    paper = receipt.get('paper_after') if type(receipt) is dict else None
    marker = receipt.get('marker_before') if type(receipt) is dict else None
    summary_keys = ('status', 'trade_day_ar', 'candidate_sha', 'phase',
                    'operation_id')
    if (type(receipt) is not dict or type(hold) is not dict or not times_valid or
            not isinstance(operation, str) or
            re.fullmatch(r'A151-[1-9][0-9]{0,15}-[1-9][0-9]{0,3}', operation) is None or
            receipt.get('schema') != 'rc6.operator-preopen-pause.v2' or
            receipt.get('status') != 'PAUSED_NO_GO' or
            receipt.get('trade_day_ar') != A151_OPERATOR_PAUSE_DAY or
            receipt.get('source_sha') != A151_OPERATOR_PAUSE_SOURCE or
            receipt.get('deploy_sha') != A151_OPERATOR_PAUSE_DEPLOY or
            receipt.get('image_id') != A151_OPERATOR_PAUSE_IMAGE or
            not isinstance(receipt.get('marker_before_sha256'), str) or
            re.fullmatch(r'[0-9a-f]{64}', receipt['marker_before_sha256']) is None or
            type(marker) is not dict or
            marker.get('candidate_sha') != A151_OPERATOR_PAUSE_SOURCE or
            marker.get('deploy_sha') != A151_OPERATOR_PAUSE_DEPLOY or
            marker.get('image_id') != A151_OPERATOR_PAUSE_IMAGE or
            marker.get('validation_status') != 'DEPLOYED_VALIDATION_PENDING' or
            marker.get('mode') != 'PRODUCTION_PAPER' or
            type(marker.get('real_orders_sent')) is not int or
            marker['real_orders_sent'] != 0 or
            receipt.get('a125_receipt_sha256') != a125_sha or
            receipt.get('gate_before_sha256') != old_gate_sha or
            receipt.get('timer_before') != {'enabled':'disabled','active':'inactive'} or
            receipt.get('timer_after') != receipt.get('timer_before') or
            type(receipt.get('service_before')) is not dict or
            receipt.get('service_before') != receipt.get('service_after') or
            receipt['service_before'].get('enabled') != 'static' or
            receipt['service_before'].get('active') not in {'failed','inactive'} or
            type(receipt.get('containers_before')) is not dict or
            receipt.get('containers_after') != receipt.get('containers_before') or
            type(paper) is not dict or
            receipt.get('paper_before') != paper or
            paper.get('mode') != 'PRODUCTION_PAPER' or
            any(type(paper.get(k)) is not int or paper[k] != 0 for k in
                ('real_orders_sent', 'open_spot', 'open_caucion', 'active_future')) or
            receipt.get('operator_paper_paused') is not True or
            receipt.get('ppi_watch_accessed') is not False or
            receipt.get('provider_requests') != 0 or
            receipt.get('admission_allowed') is not False or
            receipt.get('sidecar_after_sha256') != hold_sha or
            receipt.get('sidecar_after') != {k:hold.get(k) for k in summary_keys} or
            hold.get('schema') != 'RC6_PAPER_OPERATOR_HOLD_V1' or
            hold.get('status') != 'NO_GO_PAPER_SCALPING' or
            hold.get('trade_day_ar') != A151_OPERATOR_PAUSE_DAY or
            hold.get('candidate_sha') != A151_OPERATOR_PAUSE_SOURCE or
            hold.get('deploy_sha') != A151_OPERATOR_PAUSE_DEPLOY or
            hold.get('image_id') != A151_OPERATOR_PAUSE_IMAGE or
            hold.get('phase') != 'OPERATOR_PAUSE' or
            hold.get('operation_id') != operation or
            hold.get('a125_receipt_sha256') != a125_sha or
            hold.get('gate_before_sha256') != old_gate_sha or
            hold.get('marker_before_sha256') != receipt['marker_before_sha256'] or
            hold.get('operator_paper_paused') is not True or
            hold.get('execution_authorized') is not False or
            hold.get('readiness_verified') is not False or
            not operator_preopen_timer_disabled()):
        raise ValueError('SCHEDULER_A151_OPERATOR_PAUSE_EVIDENCE_RED')
    return receipt_sha, operation


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
    carveouts = [Path(path).name for path,spec in rows.items()
                 if spec.get('suspend_during_deploy') is False]
    if carveouts:
        exact = rows.get('systemd/' + IOL_OAUTH_GUARD_TIMER)
        if (carveouts != [IOL_OAUTH_GUARD_TIMER] or
                IOL_OAUTH_GUARD_TIMER not in names or
                not isinstance(exact, dict) or
                exact.get('role') != 'ACTIVE_TIMER' or
                exact.get('install') is not True or
                exact.get('enabled') is not True or
                exact.get('active') is not True):
            raise ValueError('SCHEDULER_IOL_CARVEOUT_POLICY_RED')
    if any(spec.get('suspend_during_deploy') is not None and
           spec.get('suspend_during_deploy') is not False
           for spec in rows.values()):
        raise ValueError('SCHEDULER_CARVEOUT_VALUE_INVALID')
    return names


def preserved_iol_guard(raw):
    """Only one OAuth timer may run during the failed-install RC6 hold."""
    names = timer_policy(raw)
    spec = json.loads(raw)['units'].get('systemd/' + IOL_OAUTH_GUARD_TIMER,{})
    return IOL_OAUTH_GUARD_TIMER if (
        spec.get('suspend_during_deploy') is False and
        IOL_OAUTH_GUARD_TIMER in names) else None


def _iol_guard_status(expected):
    if expected not in {('active','enabled'),('inactive','enabled')}:
        raise ValueError('SCHEDULER_IOL_EXPECTATION_INVALID')
    current = (command('systemctl','is-active',IOL_OAUTH_GUARD_TIMER)[0],
               command('systemctl','is-enabled',IOL_OAUTH_GUARD_TIMER)[0])
    service, code = command('systemctl','show',IOL_OAUTH_GUARD_TIMER,
                            '-p','Unit','--value')
    if current != expected or code or service != IOL_OAUTH_GUARD_SERVICE:
        raise ValueError('SCHEDULER_IOL_TIMER_OR_SERVICE_RED')
    return current


def a143_staged_module():
    """Use exact A145 source before host installation, not stale #751 imports."""
    encoded = os.environ.get('RC6_A145_QUIESCENCE_GZIP_B64','')
    if not 0 < len(encoded) < 150000:
        raise ValueError('SCHEDULER_A143_STAGED_HELPER_MISSING')
    compressed = base64.b64decode(encoded,validate=True)
    if not 0 < len(compressed) < 110000:
        raise ValueError('SCHEDULER_A143_STAGED_HELPER_COMPRESSED_RED')
    source = gzip.decompress(compressed)
    if not 20000 < len(source) < 210000:
        raise ValueError('SCHEDULER_A143_STAGED_HELPER_SIZE_RED')
    module = types.ModuleType('rc6_a145_scheduler_staged_quiescence')
    exec(compile(source,'candidate/rc6_deploy_quiescence.py','exec'),module.__dict__)
    if module.A143_PENDING_SOURCE_SHA != A143_PENDING_CANDIDATE:
        raise ValueError('SCHEDULER_A143_HELPER_CANDIDATE_CHANGED')
    return module


def a143_staged_pending_proof(*, held_continuation=None):
    return a143_staged_module().a143_pending_running_proof(
        allow_a151_held_open=True,
        operator_a151_proof=exact_a151_operator_preopen_pause,
        operator_timer_disabled=operator_preopen_timer_disabled,
        held_continuation=held_continuation)


def require_a143_fix_forward_hold(candidate):
    """Hold failed A145 without rearming a previously invalid observer."""
    q = a143_staged_module()
    path = q.ROOT/'data/deploy'/('quiescence-'+candidate+'.json')
    if not path.exists():
        # The installed #751 helper only knows postclose. Use the staged
        # candidate source and the exact A151 verifier on an intraday hold.
        pause_path = ROOT/('scheduler-suspend-'+candidate+'.json')
        pause, _ = _operator_pause_json(pause_path,65536)
        if (pause.get('candidate_sha') != candidate or
                pause.get('status') != 'SUSPENDED' or
                pause.get('preserved_iol_timer') != IOL_OAUTH_GUARD_TIMER):
            raise ValueError('SCHEDULER_A151_HOLD_RECEIPT_RED')
        previous = pause.get('a145_pending_proof')
        held = previous.get('operator_hold') if type(previous) is dict else None
        if pause.get('a169_stopped_origin') is True:
            parent, digest, _ = q.a169_prior_a143_stopped_proof(
                candidate,
                operator_a151_proof=exact_a151_operator_preopen_pause,
                operator_timer_disabled=operator_preopen_timer_disabled)
            if (pause.get('paused_from') != q.A170_FAILED_CANDIDATE or
                    pause.get('a169_origin_sha256') != digest or
                    pause.get('a170_origin_sha256') !=
                    q.A170_FAILED_RECEIPT_SHA256 or
                    parent.get('status') != 'PRIOR_A143_UNVALIDATED_HELD_STOPPED'):
                raise ValueError('SCHEDULER_A169_STOPPED_ORIGIN_CHANGED')
            return 'A143_STOPPED_UNVALIDATED_HELD_BEFORE_DRAIN'
        # A cleanup from a 16:59 stop may cross 17:00. Recheck the same A151
        # receipt and live flat #751 pair until the independent 20:00 close;
        # this continuation cannot admit a fresh drain or reactivate timers.
        a143_staged_pending_proof(held_continuation=held)
        return 'A143_RUNNING_UNVALIDATED_HELD_BEFORE_DRAIN'
    receipt,_ = q._a143_exact_json(path,65536)
    if (receipt.get('candidate_sha') != candidate or
            receipt.get('a143_pending_adopted') is not True or
            receipt.get('real_orders_sent') != 0 or
            receipt.get('ppi_watch_accessed') is not False or
            set(receipt.get('containers',{})) != set(q.OWNED)):
        raise ValueError('SCHEDULER_A143_CANDIDATE_RECEIPT_RED')
    if receipt.get('a169_origin_candidate') is not None:
        if (receipt.get('a169_origin_candidate') != A169_STOPPED_CANDIDATE or
                receipt.get('a169_origin_sha256') !=
                q.A169_PRIOR_RECEIPT_SHA256):
            raise ValueError('SCHEDULER_A169_CANDIDATE_ORIGIN_CHANGED')
    observed = {name:q.inspect(name) for name in q.OWNED}
    if receipt.get('installation_started') is False:
        if receipt.get('status') in {'DRAINED',
                                     'PRIOR_A143_UNVALIDATED_HELD_STOPPED'}:
            q.a143_stopped_continuation(
                receipt,
                operator_a151_proof=exact_a151_operator_preopen_pause,
                operator_timer_disabled=operator_preopen_timer_disabled)
            return 'A143_PRIOR_UNVALIDATED_STOPPED_HELD'
        if receipt.get('status') == 'DRAINING':
            # Any interrupted stop intent is uncertain; never restart.
            raise ValueError('SCHEDULER_A143_DRAIN_INCOMPLETE')
        raise ValueError('SCHEDULER_A143_PREINSTALL_STATUS_RED')
    if receipt.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED':
        raise ValueError('SCHEDULER_A143_NEW_INSTALL_STATUS_RED')
    if receipt.get('prior_a143_stopped_after_install') is True:
        if receipt.get('candidate_runtime_after_failure') is not None:
            raise ValueError('SCHEDULER_A143_OLD_NEW_RUNTIME_MIXED')
        q.a143_stopped_continuation(
            dict(receipt,status='DRAINED',installation_started=False),
            operator_a151_proof=exact_a151_operator_preopen_pause,
            operator_timer_disabled=operator_preopen_timer_disabled)
        return 'A143_PRIOR_UNVALIDATED_STOPPED_HELD'
    marker = q.current_runtime_marker()
    frozen,frozen_sha = q._a143_exact_json(
        q.ROOT/'data/deploy/porota-frozen-candidate.json',16384)
    intent = receipt.get('candidate_image_intent')
    runtime = receipt.get('candidate_runtime_after_failure')
    if type(intent) is not dict or type(runtime) is not dict:
        raise ValueError('SCHEDULER_A143_NEW_INTENT_OR_RUNTIME_MISSING')
    try:
        encoded = base64.b64decode(intent['frozen_manifest_b64'],validate=True)
        intent_at = datetime.fromisoformat(intent['recorded_at'])
        marker_at = datetime.fromisoformat(marker['recorded_at'])
    except (KeyError,ValueError,TypeError,binascii.Error) as exc:
        raise ValueError('SCHEDULER_A143_NEW_INTENT_INVALID') from exc
    if (len(encoded)>16384 or hashlib.sha256(encoded).hexdigest()!=frozen_sha or
            intent_at.tzinfo is None or marker_at.tzinfo is None or
            not intent_at<=marker_at<=datetime.now(timezone.utc) or
            not re.fullmatch(r'[0-9a-f]{40}',frozen.get('candidate_tree_sha','')) or
            not re.fullmatch(r'[0-9a-f]{64}',frozen.get('image_tar_sha256','')) or
            not re.fullmatch(r'sha256:[0-9a-f]{64}',frozen.get('image_id','')) or
            intent.get('manifest_sha256')!=frozen_sha or
            intent.get('candidate_tree_sha')!=frozen['candidate_tree_sha'] or
            intent.get('image_tar_sha256')!=frozen['image_tar_sha256'] or
            intent.get('image_id')!=marker.get('image_id') or
            runtime.get('status') not in {'running','exited'} or
            type(runtime.get('containers')) is not dict or
            set(runtime['containers'])!=set(q.OWNED) or
            type(runtime.get('flat_book')) is not dict or
            runtime['flat_book'].get('source_sha')!=candidate or
            runtime['flat_book'].get('real_orders_sent')!=0 or
            type(runtime.get('critical_admission')) is not dict or
            runtime['critical_admission'].get('capability')!='issues_only'):
        raise ValueError('SCHEDULER_A143_NEW_INTENT_PROVENANCE_RED')
    if (marker.get('candidate_sha') != candidate or
            marker.get('validation_status') != 'DEPLOYED_VALIDATION_PENDING' or
            marker.get('mode') != 'PRODUCTION_PAPER' or
            type(marker.get('real_orders_sent')) is not int or
            marker['real_orders_sent'] != 0 or
            marker.get('real_order_routes') != 'NOT_CALLED' or
            marker.get('ppi_watch_untouched') is not True or
            frozen.get('candidate_sha') != candidate or
            any(value['Image'] != intent['image_id'] or
                value['Id'] != runtime['containers'][name].get('id') or
                value['Image'] != runtime['containers'][name].get('image') or
                value['State']['Status'] != runtime['status'] or
                q.lifecycle(value) != runtime['containers'][name].get('lifecycle') or
                not re.fullmatch('[0-9a-f]{64}',value['Id']) or
                value['State']['Status'] not in {'running','exited'}
                for name,value in observed.items()) or
            set(observed)!=set(q.OWNED)):
        raise ValueError('SCHEDULER_A143_NEW_RUNTIME_NOT_PROVEN')
    book = q.rescue_flat_book()
    if (book['source_sha'] != candidate or book['real_orders_sent'] != 0 or
            q.critical_admission() != runtime['critical_admission']):
        raise ValueError('SCHEDULER_A143_NEW_PAPER_BOOK_RED')
    return 'A145_NEW_CANDIDATE_UNVALIDATED_HELD'


def adopt_a171_running_hold(candidate,path,names,digest,before,states):
    """Carry A171's exact failed, already-paused timers without a unit write."""
    q=a143_staged_module()
    if (candidate == q.A171_FAILED_CANDIDATE or len(names)!=16 or
            any(state != (('active','enabled') if name==IOL_OAUTH_GUARD_TIMER
                          else ('inactive','disabled') if name==OPERATOR_PREOPEN_TIMER
                          else ('inactive','enabled'))
                for name,state in zip(names,states)) or
            not operator_preopen_timer_disabled()):
        raise ValueError('SCHEDULER_A171_HELD_TIMER_CHANGED')
    prior_path=ROOT/('scheduler-suspend-'+q.A171_FAILED_CANDIDATE+'.json')
    prior,prior_sha=_operator_pause_json(prior_path,65536)
    historical=operator_preopen_hold_evidence()
    if (prior.get('candidate_sha') != q.A171_FAILED_CANDIDATE or
            prior.get('status') != 'SUSPENDED' or
            prior.get('policy_sha256') != digest or
            prior.get('timers') != names or
            prior.get('preserved_iol_timer') != IOL_OAUTH_GUARD_TIMER or
            prior.get('ppi_before') != before or
            set(prior.get('attempted',[])) != set(names) or
            prior.get('operator_pause_timer') != OPERATOR_PREOPEN_TIMER or
            prior.get('operator_pause_receipt_sha256') != historical or
            prior.get('paused_from') != q.A170_FAILED_CANDIDATE or
            prior.get('a169_origin_sha256') != q.A169_PRIOR_RECEIPT_SHA256 or
            prior.get('a170_origin_sha256') != q.A170_FAILED_RECEIPT_SHA256 or
            prior.get('rescue_failure_disposition') != 'A145_NEW_CANDIDATE_UNVALIDATED_HELD'):
        raise ValueError('SCHEDULER_A171_FAILED_HOLD_CHANGED')
    proof=q.a171_failed_running_proof(
        candidate,operator_a151_proof=exact_a151_operator_preopen_pause,
        operator_timer_disabled=operator_preopen_timer_disabled,
        require_installed_a171_source=True)
    for name in names:
        if name == IOL_OAUTH_GUARD_TIMER:
            _iol_guard_status(('active','enabled'))
            continue
        service,code=command('systemctl','show',name,'-p','Unit','--value')
        if (code or service != name.removesuffix('.timer')+'.service' or
                command('systemctl','is-active',service)[0] in
                {'active','activating','reloading','deactivating'}):
            raise ValueError('SCHEDULER_A171_JOB_ACTIVE:'+name)
    if ppi_snapshot()!=before:
        raise ValueError('SCHEDULER_PPI_WATCH_CHANGED')
    receipt=dict(prior,candidate_sha=candidate,
                 paused_from=q.A171_FAILED_CANDIDATE,
                 prior_receipt_sha256=prior_sha,
                 a171_running_origin=True,
                 a171_origin_sha256=q.A171_FAILED_RECEIPT_SHA256,
                 a171_previous_proof=proof,
                 recorded_at=datetime.now(timezone.utc).isoformat())
    receipt.pop('rescue_failure_disposition',None)
    receipt.pop('rescue_failure_recorded_at',None)
    persist(path,receipt)
    print(json.dumps({'status':'SUSPENDED','candidate_sha':candidate,
                      'paused_from':q.A171_FAILED_CANDIDATE,
                      'timer_count':len(names),'systemd_mutations':0,
                      'ppi_watch_mutated':False},sort_keys=True))
    return receipt


def require_a171_fix_forward_hold(candidate):
    """A failed A172 cannot rearm the known-unvalidated A171 observer."""
    q=a143_staged_module()
    pause,_=_operator_pause_json(ROOT/('scheduler-suspend-'+candidate+'.json'),65536)
    if (pause.get('candidate_sha')!=candidate or
            pause.get('status')!='SUSPENDED' or
            pause.get('a171_running_origin') is not True or
            pause.get('paused_from')!=q.A171_FAILED_CANDIDATE or
            pause.get('a171_origin_sha256')!=q.A171_FAILED_RECEIPT_SHA256):
        raise ValueError('SCHEDULER_A171_CANDIDATE_HOLD_CHANGED')
    path=q.ROOT/'data/deploy'/('quiescence-'+candidate+'.json')
    if not path.exists():
        proof=q.a171_failed_running_proof(
            candidate,operator_a151_proof=exact_a151_operator_preopen_pause,
            operator_timer_disabled=operator_preopen_timer_disabled,
            require_installed_a171_source=True)
        if proof != pause.get('a171_previous_proof'):
            raise ValueError('SCHEDULER_A171_PREDRAIN_PROOF_CHANGED')
        return 'A171_RUNNING_UNVALIDATED_HELD_BEFORE_DRAIN'
    receipt,_=q._a143_exact_json(path,65536)
    if (receipt.get('candidate_sha') != candidate or
            receipt.get('a171_pending_adopted') is not True or
            receipt.get('a171_previous_proof') != pause.get('a171_previous_proof') or
            receipt.get('a171_origin_sha256') != q.A171_FAILED_RECEIPT_SHA256):
        raise ValueError('SCHEDULER_A171_DRAIN_PROOF_CHANGED')
    if receipt.get('installation_started') is False:
        if receipt.get('status') not in {'DRAINED','PRIOR_A171_UNVALIDATED_HELD_STOPPED'}:
            raise ValueError('SCHEDULER_A171_DRAIN_INCOMPLETE')
        q.a171_stopped_continuation(receipt)
        return 'A171_PRIOR_UNVALIDATED_STOPPED_HELD'
    if receipt.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED':
        raise ValueError('SCHEDULER_A171_NEW_INSTALL_STATUS_RED')
    runtime=receipt.get('candidate_runtime_after_failure')
    if receipt.get('prior_a171_stopped_after_install') is True and runtime is None:
        q.a171_stopped_continuation(receipt)
        return 'A171_PRIOR_UNVALIDATED_STOPPED_HELD'
    if type(runtime) is not dict or runtime.get('status') != 'running':
        raise ValueError('SCHEDULER_A171_NEW_RUNTIME_MISSING')
    observed=q.candidate_runtime_after_failed_install(receipt)
    if (observed.get('status') != 'running' or
            observed.get('containers') != runtime.get('containers') or
            observed.get('flat_book',{}).get('source_sha') != candidate or
            observed.get('flat_book',{}).get('real_orders_sent') != 0 or
            observed.get('critical_admission',{}).get('capability') != 'issues_only'):
        raise ValueError('SCHEDULER_A171_NEW_RUNTIME_CHANGED')
    return 'A172_NEW_CANDIDATE_UNVALIDATED_HELD'


def adopt_a174_running_hold(candidate,path,names,digest,before,states):
    """Adopt only the pinned A174 RUNNING failure with no unit mutation."""
    q=a143_staged_module()
    if (candidate==q.A174_FAILED_CANDIDATE or len(names)!=16 or
            any(state != (('active','enabled') if name==IOL_OAUTH_GUARD_TIMER
                          else ('inactive','disabled') if name==OPERATOR_PREOPEN_TIMER
                          else ('inactive','enabled'))
                for name,state in zip(names,states)) or
            not operator_preopen_timer_disabled()):
        raise ValueError('SCHEDULER_A174_HELD_TIMER_CHANGED')
    prior,prior_sha=_operator_pause_json(
        ROOT/('scheduler-suspend-'+q.A174_FAILED_CANDIDATE+'.json'),65536)
    historical=operator_preopen_hold_evidence()
    if (prior_sha!=q.A174_SCHEDULER_RECEIPT_SHA256 or
            prior.get('candidate_sha')!=q.A174_FAILED_CANDIDATE or
            prior.get('status')!='SUSPENDED' or
            prior.get('policy_sha256')!=digest or
            prior.get('timers')!=names or
            prior.get('preserved_iol_timer')!=IOL_OAUTH_GUARD_TIMER or
            prior.get('ppi_before')!=before or
            set(prior.get('attempted',[]))!=set(names) or
            prior.get('operator_pause_timer')!=OPERATOR_PREOPEN_TIMER or
            prior.get('operator_pause_receipt_sha256')!=historical or
            prior.get('paused_from')!=q.A171_FAILED_CANDIDATE or
            prior.get('a171_running_origin') is not True or
            prior.get('a171_origin_sha256')!=q.A171_FAILED_RECEIPT_SHA256 or
            prior.get('rescue_failure_disposition')!='A172_NEW_CANDIDATE_UNVALIDATED_HELD'):
        raise ValueError('SCHEDULER_A174_FAILED_HOLD_CHANGED')
    proof=q.a174_failed_running_proof(
        candidate,operator_a151_proof=exact_a151_operator_preopen_pause,
        operator_timer_disabled=operator_preopen_timer_disabled,
        require_installed_a174_source=True)
    for name in names:
        if name==IOL_OAUTH_GUARD_TIMER:
            _iol_guard_status(('active','enabled'))
            continue
        service,code=command('systemctl','show',name,'-p','Unit','--value')
        if (code or service!=name.removesuffix('.timer')+'.service' or
                command('systemctl','is-active',service)[0] in
                {'active','activating','reloading','deactivating'}):
            raise ValueError('SCHEDULER_A174_JOB_ACTIVE:'+name)
    if ppi_snapshot()!=before:
        raise ValueError('SCHEDULER_PPI_WATCH_CHANGED')
    receipt=dict(prior,candidate_sha=candidate,
                 paused_from=q.A174_FAILED_CANDIDATE,
                 prior_receipt_sha256=prior_sha,
                 a174_running_origin=True,
                 a174_origin_sha256=q.A174_FAILED_RECEIPT_SHA256,
                 a174_previous_proof=proof,
                 recorded_at=datetime.now(timezone.utc).isoformat())
    receipt.pop('rescue_failure_disposition',None)
    receipt.pop('rescue_failure_recorded_at',None)
    persist(path,receipt)
    print(json.dumps({'status':'SUSPENDED','candidate_sha':candidate,
                      'paused_from':q.A174_FAILED_CANDIDATE,
                      'timer_count':len(names),'systemd_mutations':0,
                      'ppi_watch_mutated':False},sort_keys=True))
    return receipt


def require_a174_fix_forward_hold(candidate):
    """Keep A174 stopped after drain and hold any failed A175 runtime."""
    q=a143_staged_module()
    pause,_=_operator_pause_json(ROOT/('scheduler-suspend-'+candidate+'.json'),65536)
    if (pause.get('candidate_sha')!=candidate or pause.get('status')!='SUSPENDED' or
            pause.get('a174_running_origin') is not True or
            pause.get('paused_from')!=q.A174_FAILED_CANDIDATE or
            pause.get('prior_receipt_sha256')!=q.A174_SCHEDULER_RECEIPT_SHA256 or
            pause.get('a174_origin_sha256')!=q.A174_FAILED_RECEIPT_SHA256):
        raise ValueError('SCHEDULER_A174_CANDIDATE_HOLD_CHANGED')
    path=q.ROOT/'data/deploy'/('quiescence-'+candidate+'.json')
    if not path.exists():
        proof=q.a174_failed_running_proof(
            candidate,operator_a151_proof=exact_a151_operator_preopen_pause,
            operator_timer_disabled=operator_preopen_timer_disabled,
            require_installed_a174_source=True)
        if proof!=pause.get('a174_previous_proof'):
            raise ValueError('SCHEDULER_A174_PREDRAIN_PROOF_CHANGED')
        return 'A174_RUNNING_UNVALIDATED_HELD_BEFORE_DRAIN'
    receipt,_=q._a143_exact_json(path,65536)
    if (receipt.get('candidate_sha')!=candidate or
            receipt.get('a174_pending_adopted') is not True or
            receipt.get('a174_previous_proof')!=pause.get('a174_previous_proof') or
            receipt.get('a174_origin_sha256')!=q.A174_FAILED_RECEIPT_SHA256):
        raise ValueError('SCHEDULER_A174_DRAIN_PROOF_CHANGED')
    if receipt.get('installation_started') is False:
        if receipt.get('status') not in {'DRAINED','PRIOR_A174_UNVALIDATED_HELD_STOPPED'}:
            raise ValueError('SCHEDULER_A174_DRAIN_INCOMPLETE')
        q.a174_stopped_continuation(receipt)
        return 'A174_PRIOR_UNVALIDATED_STOPPED_HELD'
    if receipt.get('status')!='FIX_FORWARD_VALIDATION_REQUIRED':
        raise ValueError('SCHEDULER_A174_NEW_INSTALL_STATUS_RED')
    runtime=receipt.get('candidate_runtime_after_failure')
    if receipt.get('prior_a174_stopped_after_install') is True and runtime is None:
        q.a174_stopped_continuation(receipt)
        return 'A174_PRIOR_UNVALIDATED_STOPPED_HELD'
    if type(runtime) is not dict or runtime.get('status')!='running':
        raise ValueError('SCHEDULER_A174_NEW_RUNTIME_MISSING')
    observed=q.candidate_runtime_after_failed_install(receipt)
    if (observed.get('status')!='running' or
            observed.get('containers')!=runtime.get('containers') or
            observed.get('flat_book',{}).get('source_sha')!=candidate or
            observed.get('flat_book',{}).get('real_orders_sent')!=0 or
            observed.get('critical_admission',{}).get('capability')!='issues_only'):
        raise ValueError('SCHEDULER_A174_NEW_RUNTIME_CHANGED')
    return 'A175_NEW_CANDIDATE_UNVALIDATED_HELD'


def adopt_a143_legacy_hold_with_iol(candidate,path,names,digest,before,states):
    """Migrate A138's *exact* 16-paused hold to 15-paused + OAuth-only timer.

    Durable intent precedes the sole `systemctl start`. Never rearm preopen.
    A failure remains RED and never retries activation without new review.
    """
    if (candidate == A143_PENDING_CANDIDATE or
            IOL_OAUTH_GUARD_TIMER not in names or len(names)!=16 or
            any(state != (('inactive','disabled') if name==OPERATOR_PREOPEN_TIMER
                          else ('inactive','enabled'))
                for name,state in zip(names,states))):
        raise ValueError('SCHEDULER_A145_OLD16_HOLD_CHANGED')
    historical = operator_preopen_hold_evidence()
    if not operator_preopen_timer_disabled():
        raise ValueError('SCHEDULER_A145_PREOPEN_REARM_FORBIDDEN')
    prior_path = ROOT/('scheduler-suspend-'+A143_PENDING_CANDIDATE+'.json')
    prior, prior_sha = _operator_pause_json(prior_path,65536)
    a138_path = ROOT/('scheduler-suspend-'+FAILED_A138_SUSPENSION_CANDIDATE+'.json')
    a138, a138_sha = _operator_pause_json(a138_path,65536)
    if (prior.get('candidate_sha')!=A143_PENDING_CANDIDATE or
            prior.get('status')!='SUSPENDED' or
            prior.get('policy_sha256')!=A138_ORIGINAL_TIMER_POLICY_SHA256 or
            prior.get('timers')!=names or prior.get('ppi_before')!=before or
            set(prior.get('attempted',[]))!=set(names) or
            prior.get('paused_from')!=FAILED_A138_SUSPENSION_CANDIDATE or
            prior.get('prior_receipt_sha256')!=a138_sha or
            prior.get('adopted_a138_operator_hold') is not True or
            prior.get('operator_pause_timer')!=OPERATOR_PREOPEN_TIMER or
            prior.get('operator_pause_receipt_sha256')!=historical or
            prior.get('rescue_failure_disposition')!='RESCUE_CANDIDATE_UNVALIDATED_HELD' or
            a138.get('candidate_sha')!=FAILED_A138_SUSPENSION_CANDIDATE or
            a138.get('status')!='SUSPENDED' or
            a138.get('policy_sha256')!=A138_ORIGINAL_TIMER_POLICY_SHA256 or
            a138.get('timers')!=names or a138.get('ppi_before')!=before or
            a138.get('operator_pause_receipt_sha256')!=historical):
        raise ValueError('SCHEDULER_A145_A138_A143_CHAIN_RED')
    proof = a143_staged_pending_proof()
    if (proof.get('source_sha')!=A143_PENDING_CANDIDATE or
            proof.get('flat_book',{}).get('real_orders_sent')!=0):
        raise ValueError('SCHEDULER_A145_A143_RUNTIME_RED')
    for name in names:
        service, code = command('systemctl','show',name,'-p','Unit','--value')
        if (code or service != name.removesuffix('.timer')+'.service' or
                command('systemctl','is-active',service)[0] in
                {'active','activating','reloading','deactivating'}):
            raise ValueError('SCHEDULER_A145_SERVICE_ACTIVE:' + name)
    _iol_guard_status(('inactive','enabled'))
    if ppi_snapshot()!=before:
        raise ValueError('SCHEDULER_A145_PPI_WATCH_CHANGED')
    receipt = dict(prior,candidate_sha=candidate,policy_sha256=digest,
                   status='IOL_START_INTENT',
                   paused_from=A143_PENDING_CANDIDATE,
                   prior_receipt_sha256=prior_sha,
                   preserved_iol_timer=IOL_OAUTH_GUARD_TIMER,
                   a145_pending_proof=proof,
                   operator_pause_receipt_sha256=historical,
                   recorded_at=datetime.now(timezone.utc).isoformat())
    persist(path,receipt)
    _,code=command('systemctl','start',IOL_OAUTH_GUARD_TIMER)
    if code:
        raise ValueError('SCHEDULER_A145_IOL_START_FAILED')
    _iol_guard_status(('active','enabled'))
    for name,state in zip(names,states):
        if name == IOL_OAUTH_GUARD_TIMER:
            continue
        current = (command('systemctl','is-active',name)[0],
                   command('systemctl','is-enabled',name)[0])
        if current != state:
            raise ValueError('SCHEDULER_A145_15_TIMER_HOLD_CHANGED:' + name)
    if ppi_snapshot()!=before or not operator_preopen_timer_disabled():
        raise ValueError('SCHEDULER_A145_POSTSTART_GUARD_RED')
    receipt['status']='SUSPENDED'
    receipt['iol_guard_migration']='OLD16_PAUSED_TO_15_PLUS_IOL_RUNNING'
    persist(path,receipt)
    print(json.dumps({'status':'SUSPENDED','candidate_sha':candidate,
                      'timer_count':len(names),'paused_timers':len(names)-1,
                      'preserved_iol_timer':IOL_OAUTH_GUARD_TIMER,
                      'prior_receipt_sha256':prior_sha,
                      'ppi_watch_accessed':False,
                      'systemd_mutations':1},sort_keys=True))
    return receipt


def adopt_a169_stopped_hold(candidate, path, names, digest, before, states):
    """Carry A169's exact stopped PAPER hold without any systemd mutation."""
    if (candidate in {A143_PENDING_CANDIDATE, A169_STOPPED_CANDIDATE} or
            len(names) != 16 or IOL_OAUTH_GUARD_TIMER not in names or
            OPERATOR_PREOPEN_TIMER not in names or
            any(state != (('active','enabled') if name == IOL_OAUTH_GUARD_TIMER
                          else ('inactive','disabled') if name == OPERATOR_PREOPEN_TIMER
                          else ('inactive','enabled'))
                for name, state in zip(names, states))):
        raise ValueError('SCHEDULER_A169_TIMER_STATE_CHANGED')
    prior_path = ROOT/('scheduler-suspend-'+A169_STOPPED_CANDIDATE+'.json')
    prior, prior_sha = _operator_pause_json(prior_path,65536)
    historical = operator_preopen_hold_evidence()
    if (prior.get('candidate_sha') != A169_STOPPED_CANDIDATE or
            prior.get('status') != 'SUSPENDED' or
            prior.get('policy_sha256') != digest or
            prior.get('timers') != names or
            prior.get('ppi_before') != before or
            prior.get('preserved_iol_timer') != IOL_OAUTH_GUARD_TIMER or
            prior.get('operator_pause_timer') != OPERATOR_PREOPEN_TIMER or
            prior.get('operator_pause_receipt_sha256') != historical or
            set(prior.get('attempted', [])) != set(names) or
            len(prior['attempted']) != 16):
        raise ValueError('SCHEDULER_A169_PARENT_RECEIPT_CHANGED')
    q = a143_staged_module()
    parent, origin_sha, book = q.a169_prior_a143_stopped_proof(
        candidate,
        operator_a151_proof=exact_a151_operator_preopen_pause,
        operator_timer_disabled=operator_preopen_timer_disabled,
        require_installed_a170_source=True)
    if parent.get('status') != 'PRIOR_A143_UNVALIDATED_HELD_STOPPED' or book.get('real_orders_sent') != 0:
        raise ValueError('SCHEDULER_A169_STOPPED_PAPER_CHANGED')
    intermediate_path=ROOT/('scheduler-suspend-'+q.A170_FAILED_CANDIDATE+'.json')
    intermediate,intermediate_sha=_operator_pause_json(intermediate_path,65536)
    if (intermediate.get('candidate_sha') != q.A170_FAILED_CANDIDATE or
            intermediate.get('status') != 'SUSPENDED' or
            intermediate.get('policy_sha256') != digest or
            intermediate.get('timers') != names or
            set(intermediate.get('attempted',[])) != set(names) or
            intermediate.get('ppi_before') != before or
            intermediate.get('preserved_iol_timer') != IOL_OAUTH_GUARD_TIMER or
            intermediate.get('operator_pause_timer') != OPERATOR_PREOPEN_TIMER or
            intermediate.get('operator_pause_receipt_sha256') != historical or
            intermediate.get('paused_from') != A169_STOPPED_CANDIDATE or
            intermediate.get('prior_receipt_sha256') != prior_sha or
            intermediate.get('a169_origin_sha256') != origin_sha or
            intermediate.get('a169_stopped_origin') is not True):
        raise ValueError('SCHEDULER_A170_INTERMEDIATE_HOLD_CHANGED')
    for name in names:
        service, code = command('systemctl','show',name,'-p','Unit','--value')
        if (code or service != name.removesuffix('.timer')+'.service' or
                command('systemctl','is-active',service)[0] in
                {'active','activating','reloading','deactivating'}):
            raise ValueError('SCHEDULER_A169_WORKER_ACTIVE:' + name)
    _iol_guard_status(('active','enabled'))
    if ppi_snapshot()!=before or not operator_preopen_timer_disabled():
        raise ValueError('SCHEDULER_A169_PPI_OR_OPERATOR_CHANGED')
    receipt = dict(intermediate,candidate_sha=candidate,
                   paused_from=q.A170_FAILED_CANDIDATE,
                   prior_receipt_sha256=intermediate_sha,
                   a169_origin_sha256=origin_sha,
                   a170_origin_sha256=q.A170_FAILED_RECEIPT_SHA256,
                   a169_stopped_origin=True,
                   recorded_at=datetime.now(timezone.utc).isoformat())
    persist(path,receipt)
    print(json.dumps({'status':'SUSPENDED','candidate_sha':candidate,
                      'timer_count':16,'paused_timers':15,
                      'a169_stopped_origin_sha256':origin_sha,
                      'prior_receipt_sha256':prior_sha,
                      'real_orders_sent':0,'ppi_watch_accessed':False,
                      'systemd_mutations':0},sort_keys=True))
    return receipt


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


def adopt_a138_operator_paused_timers(candidate, path, names, digest, before, states):
    """A141: adopt the exact A138 16-timer hold without stopping another job.

    A125 intentionally disabled preopen while A138 stopped all other owned
    timers. The generic 'all inactive/enabled' recovery missed that one
    disabled timer and attempted to start a new suspension, failing before
    writing its receipt. Never infer a healthy runtime or rearm old A92.
    """
    if (candidate == FAILED_A138_SUSPENSION_CANDIDATE or
            OPERATOR_PREOPEN_TIMER not in names or
            any(state != (('inactive', 'disabled') if name == OPERATOR_PREOPEN_TIMER
                          else ('inactive', 'enabled'))
                for name, state in zip(names, states))):
        raise ValueError('SCHEDULER_A141_EXACT_PREPAUSED_STATES_REQUIRED')
    operator_sha = exact_operator_preopen_pause(require_original_marker=True)
    if not operator_preopen_timer_disabled():
        raise ValueError('SCHEDULER_A141_OPERATOR_TIMER_CHANGED')
    prior_path = ROOT/('scheduler-suspend-' + FAILED_A138_SUSPENSION_CANDIDATE + '.json')
    if (prior_path.is_symlink() or not prior_path.is_file() or
            prior_path.stat().st_size > 65536 or prior_path.stat().st_nlink != 1):
        raise ValueError('SCHEDULER_A141_A138_RECEIPT_UNSAFE')
    prior, prior_sha = _operator_pause_json(prior_path, 65536)
    if (prior.get('candidate_sha') != FAILED_A138_SUSPENSION_CANDIDATE or
            prior.get('status') != 'SUSPENDED' or
            prior.get('policy_sha256') != digest or prior.get('timers') != names or
            prior.get('ppi_before') != before or
            prior.get('operator_pause_timer') != OPERATOR_PREOPEN_TIMER or
            prior.get('operator_pause_receipt_sha256') != operator_sha or
            set(prior.get('attempted', [])) != set(names) or
            len(prior['attempted']) != len(names)):
        raise ValueError('SCHEDULER_A141_A138_RECEIPT_CHANGED')
    # Read-only PAPER/0 + flat A92 clean-stopped proof must precede the new
    # receipt. It is not a request to launch or restart old containers.
    if require_rescue_degraded_hold(candidate) != 'RESCUE_A92_STOPPED_A138_UNVALIDATED_HELD':
        raise ValueError('SCHEDULER_A141_A92_STOP_PROOF_RED')
    for name, expected in zip(names, states):
        active, _ = command('systemctl', 'is-active', name)
        enabled, _ = command('systemctl', 'is-enabled', name)
        service, code = command('systemctl', 'show', name, '-p', 'Unit', '--value')
        if ((active, enabled) != expected or code or not service.endswith('.service') or
                command('systemctl', 'is-active', service)[0] in
                {'active', 'activating', 'reloading', 'deactivating'}):
            raise ValueError('SCHEDULER_A141_TIMER_OR_JOB_CHANGED:' + name)
    if ppi_snapshot() != before or not operator_preopen_timer_disabled():
        raise ValueError('SCHEDULER_A141_OPERATOR_OR_PROVIDER_CHANGED')
    receipt = dict(prior, candidate_sha=candidate,
                   paused_from=FAILED_A138_SUSPENSION_CANDIDATE,
                   prior_receipt_sha256=prior_sha,
                   adopted_a138_operator_hold=True,
                   recorded_at=datetime.now(timezone.utc).isoformat())
    persist(path, receipt)
    print(json.dumps({'status':'SUSPENDED','candidate_sha':candidate,
                      'timer_count':len(names),'a138_operator_hold_adopted':True,
                      'prior_receipt_sha256':prior_sha,'real_orders_sent':0,
                      'systemd_mutations':0},sort_keys=True))
    return receipt


def run(candidate, action, policy_b64):
    if not re.fullmatch(r'[0-9a-f]{40}', candidate):
        raise ValueError('SCHEDULER_CANDIDATE_INVALID')
    raw = base64.b64decode(policy_b64, validate=True)
    if len(raw) > 100_000:
        raise ValueError('SCHEDULER_POLICY_BOUND')
    names = timer_policy(raw)
    preserved = preserved_iol_guard(raw)
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
                    receipt.get('preserved_iol_timer') != preserved or
                    receipt.get('ppi_before') != before or
                    receipt.get('status') != 'SUSPENDED' or
                    set(receipt.get('attempted', [])) != set(names)):
                raise ValueError('SCHEDULER_RECEIPT_EXISTS')
            if receipt.get('operator_pause_timer') is not None:
                if (receipt['operator_pause_timer'] != OPERATOR_PREOPEN_TIMER or
                        receipt.get('operator_pause_receipt_sha256') !=
                        operator_preopen_hold_evidence() or
                        not operator_preopen_timer_disabled()):
                    raise ValueError('SCHEDULER_OPERATOR_PAUSE_RECEIPT_CHANGED')
            for name in names:
                if name == preserved:
                    _iol_guard_status(('active','enabled'))
                    continue
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
        if preserved is not None and all(
                state == (('active','enabled') if name == preserved
                          else ('inactive','disabled') if name == OPERATOR_PREOPEN_TIMER
                          else ('inactive','enabled'))
                for name,state in zip(names,states)):
            # Before installation the host import is still the prior source.
            # Select the next exact continuation from the frozen candidate's
            # staged helper and the current runtime marker. The adoption
            # itself verifies the receipt, Docker IDs, flat book and NO_GO.
            staged = a143_staged_module()
            if (getattr(staged,'A174_FAILED_CANDIDATE',None) and
                    staged.current_runtime_marker().get('candidate_sha') ==
                    staged.A174_FAILED_CANDIDATE):
                return adopt_a174_running_hold(
                    candidate,path,names,digest,before,states)
            if (getattr(staged,'A171_FAILED_CANDIDATE',None) and
                    staged.current_runtime_marker().get('candidate_sha') ==
                    staged.A171_FAILED_CANDIDATE):
                return adopt_a171_running_hold(
                    candidate,path,names,digest,before,states)
            return adopt_a169_stopped_hold(
                candidate,path,names,digest,before,states)
        if preserved is not None and all(
                state == (('inactive','disabled') if name == OPERATOR_PREOPEN_TIMER
                          else ('inactive','enabled'))
                for name,state in zip(names,states)):
            return adopt_a143_legacy_hold_with_iol(
                candidate,path,names,digest,before,states)
        if OPERATOR_PREOPEN_TIMER in names and all(
                state == (('inactive','disabled') if name == OPERATOR_PREOPEN_TIMER
                          else ('inactive','enabled'))
                for name,state in zip(names,states)):
            return adopt_a138_operator_paused_timers(
                candidate,path,names,digest,before,states)
        if preserved is None and all(state == ('inactive', 'enabled') for state in states):
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
            operator_pause_sha = operator_preopen_hold_evidence()
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
        if preserved:
            receipt['preserved_iol_timer'] = preserved
        if operator_pause_sha is not None:
            receipt['operator_pause_timer'] = OPERATOR_PREOPEN_TIMER
            receipt['operator_pause_receipt_sha256'] = operator_pause_sha
        persist(path, receipt)
        for name in names:
            receipt['attempted'].append(name)
            persist(path, receipt)
            if name == preserved:
                _iol_guard_status(('active','enabled'))
                continue
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
                if name == preserved:
                    _iol_guard_status(('active','enabled'))
                    continue
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
                receipt.get('preserved_iol_timer') != preserved or
                receipt.get('ppi_before') != before or
                not set(receipt.get('attempted', [])) <= set(names)):
            raise ValueError('SCHEDULER_RECEIPT_IDENTITY_CHANGED')
        operator_pause_timer = receipt.get('operator_pause_timer')
        if operator_pause_timer is not None:
            if (operator_pause_timer != OPERATOR_PREOPEN_TIMER or
                    operator_pause_timer not in names or
                    receipt.get('operator_pause_receipt_sha256') !=
                    operator_preopen_hold_evidence() or
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
        if action == 'recover-a143-pending':
            action = 'hold-a143-pending'
        if action == 'recover-a171-pending':
            action = 'hold-a171-pending'
        if action == 'recover-a174-pending':
            action = 'hold-a174-pending'
        if action in {'hold-rescue-degraded','hold-adopted-rescue',
                      'hold-a143-pending','hold-a171-pending','hold-a174-pending'}:
            if (receipt.get('status') != 'SUSPENDED' or
                    set(receipt.get('attempted', [])) != set(names)):
                raise ValueError('SCHEDULER_RESCUE_PAUSE_RECEIPT_RED')
            for name in names:
                if name == preserved:
                    _iol_guard_status(('active','enabled'))
                    continue
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
                      require_adopted_rescue_hold(candidate) if
                      action == 'hold-adopted-rescue' else
                      require_a171_fix_forward_hold(candidate) if
                      action == 'hold-a171-pending' else
                      require_a174_fix_forward_hold(candidate) if
                      action == 'hold-a174-pending' else
                      require_a143_fix_forward_hold(candidate))
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
                if name == preserved:
                    _iol_guard_status(('active','enabled'))
                    continue
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
                if name == preserved:
                    _iol_guard_status(('active','enabled'))
                    continue
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
                if name == preserved:
                    _iol_guard_status(('active','enabled'))
                    continue
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
                                           'recover-adopted-rescue',
                                           'recover-a143-pending',
                                           'recover-a171-pending',
                                           'recover-a174-pending'], required=True)
    policy=parser.add_mutually_exclusive_group(required=True)
    policy.add_argument('--policy-b64')
    policy.add_argument('--policy-gzip-b64')
    args = parser.parse_args()
    encoded=args.policy_b64
    if args.policy_gzip_b64 is not None:
        try:
            packed=base64.b64decode(args.policy_gzip_b64,validate=True)
            if not 0<len(packed)<12000:
                raise ValueError('SCHEDULER_POLICY_COMPRESSED_BOUND')
            inflater=zlib.decompressobj(16+zlib.MAX_WBITS)
            raw=inflater.decompress(packed,100001)
            if (not 0<len(raw)<=100000 or not inflater.eof or
                    inflater.unused_data or inflater.unconsumed_tail):
                raise ValueError('SCHEDULER_POLICY_COMPRESSED_INVALID')
            encoded=base64.b64encode(raw).decode('ascii')
        except (binascii.Error,zlib.error) as exc:
            raise ValueError('SCHEDULER_POLICY_COMPRESSED_INVALID') from exc
    run(args.candidate_sha,args.action,encoded)


if __name__ == '__main__':
    main()
