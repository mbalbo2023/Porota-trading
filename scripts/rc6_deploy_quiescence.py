#!/usr/bin/env python3
"""Durable CLOSED/PAPER admission and drain before candidate host writes.

Recovery restarts the original containers only before installation begins.
After that boundary the receipt explicitly requires fix-forward validation.
"""
import argparse
import base64
import binascii
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
import types
import zlib
from zoneinfo import ZoneInfo

ROOT = Path('/opt/porota-trading')
OWNED = ('porota_production_observer', 'porota_production_dashboard')
FAILED_INSTALL_CANDIDATE = '38b04252f5e25d43bf40c2fa332a9d01d3eb8fb1'
FAILED_ADMIN_CANDIDATE = '7ae4d088d8cf90f670f8675d220244638f35d7f6'
FAILED_COLD2_CANDIDATE = '48a981cf327a637c0c074879fcdcd17475de8111'
ADMIN_CANCEL_REASON = 'ADMIN_CANCEL_NO_MARKET_EXECUTION'
RESCUE_MODE = 'DEGRADED_CLOSED_20261026'
ADOPTED_RESCUE_MODE = 'CLOSED_STOPPED_FAILED_RESCUE'
A143_PENDING_MODE = 'CLOSED_A143_PENDING_FIX_FORWARD'
A151_HELD_OPEN_MODE = 'A151_HELD_OPEN'
A151_HELD_STOPPED_MODE = 'A151_HELD_STOPPED'
A169_PRIOR_CANDIDATE = 'b778c8063e96574ddf859907abb99fae6f4e3484'
A169_PRIOR_RUN = '40755524824'
A169_PRIOR_ATTEMPT = '2'
A169_PRIOR_RECEIPT_SHA256 = 'ac2d6ec4bb3fe2a2ce809d89034cb564c1a3ce790de21838b654abc787471f81'
A169_FAILED_RECEIPT_SHA256 = '9ec4cea3e54e05534058020093d00aa55a6ad6c89303b5364010b243493e2659'
A170_FAILED_CANDIDATE = '33ea6d365c47d415fc5480a23b1338c208c2a29f'
A170_FAILED_RECEIPT_SHA256 = '2052b78c67f4385e662caeb1448d357f804d0605e6f2c710447b4130fa1df0e8'
A170_INSTALLED_QUIESCENCE_SHA256 = '5346f3b56681056bf91e2324e7e6a2c440d067749272090820057390bc8f3412'
A170_LOADED_IMAGE_ID = 'sha256:c3af151c90dcf124d38843e4fb99219b01bfc9863cc1d7a96bdedfd419e6d98f'
A171_FAILED_CANDIDATE = '4ce63a68c668cadd2a0aa7e8aded44ae4a3c46a6'
A171_FAILED_RECEIPT_SHA256 = '7a5e73359e85ef99796607228876443680f56bfe26ce201f4664474f700eabb5'
A171_CANDIDATE_TREE_SHA = '18693659202fc1a5cc0fa98113f0a1f9ad8a416d'
A171_DEPLOY_SHA = 'd9e2a4d7e55b27e9799ae6db44dfda9a013912c6'
A171_INSTALLED_QUIESCENCE_SHA256 = '3b7317deaffbd08d556c35584953e416d8aa02a5dccd21c059347bd121e5b373'
A171_LOADED_IMAGE_ID = 'sha256:e61052af13c01d9ce76e92f38b50c794dac8a146427e132d43a8a80554a879aa'
A171_RUNNING_IDS = {
    'porota_production_observer': '9b7d41f04b2e7847318c6f8ded0440b9d6a030a5bbb99c3ac5f31c2815beaa95',
    'porota_production_dashboard': '3d51f57985d8375f6c06c9a57d8a7f6c6dd4b99ee03bdeb0efac440f92e43aa3',
}
A171_PENDING_MODE = 'CLOSED_A171_PENDING_FIX_FORWARD'
A174_FAILED_CANDIDATE = 'b6e6ce75f29fe117aaa42b0bfcd9386ea93a0aa0'
A174_FAILED_RECEIPT_SHA256 = '364c9bb9f013da603345c90219e7c889d5b04ecff951afe08f4bfc7ec03c1528'
A174_SCHEDULER_RECEIPT_SHA256 = 'e0ddc6a0b50a83510b172a7653e5e26cad309aaa4a49207c9be7aa4cded6cec6'
A174_CANDIDATE_TREE_SHA = 'ac82bcb2617748294c9950a009864dbf95e336f4'
A174_DEPLOY_SHA = 'ff2775933b1565bb3a43fa130f9aa6359ecbdf48'
A174_INSTALLED_QUIESCENCE_SHA256 = 'c18c02a295a66e7d4f8a95655d04805276f28da456050f9d06a62e5c5b0b436a'
A174_FROZEN_SHA256 = '4715ef897a9216242d0604e06e7df8ceb30f47f48552ac0d282013e04de9a687'
A174_MARKER_SHA256 = '0191482515683e5184871d63e6030208e0b22ddfc727845b06c4fc9b59d5c6f9'
A174_LOADED_IMAGE_ID = 'sha256:9ca6e7ca98f11bbb82353434f54ffab15b218ec54319c83ae1661aaf897a40e9'
A174_RUNNING_IDS = {
    'porota_production_observer': '22235a9b2d808ac1f8f653939538fd382469d1ec4c8d150262b0a3d6d97d9dbd',
    'porota_production_dashboard': 'dbbb264e2afc0306f4c5e3cbae5fa49bdfbde9204ab660902ccf0adecc1458d7',
}
A174_PENDING_MODE = 'CLOSED_A174_PENDING_FIX_FORWARD'
A175_FAILED_CANDIDATE = '18ba517a846c6798f0d12f39072465fc02fa03b5'
A175_FAILED_RECEIPT_SHA256 = '5ddf0d5e340f7cac96bcbb339eeaeea3ed4aad635a2201fd55fffe375c961022'
A175_SCHEDULER_RECEIPT_SHA256 = '64a8afc3591c42d4e58f3acb34905043c0ffe323b695ecf3337f99b66d6b0b6e'
A175_CANDIDATE_TREE_SHA = '66c67bff66dd9f036cd7aa8de5ba312e04e53be6'
A175_DEPLOY_SHA = '3c5210fef9d4f1d4c95b286b0dbc61b6654fe055'
A175_INSTALLED_QUIESCENCE_SHA256 = '284c5cd7e32bd9672138031204fe0ff5d24277bc9e84ec2ea6dcfc06e51ec170'
A175_FROZEN_SHA256 = '1067182c282f17440850e50c7f8d347d84cfbb90090d7e71066483beb19450e0'
A175_MARKER_SHA256 = '42055f5225def0560453b2c40b5e4695082fa3235fc42a4596210cf0c1b8b161'
A175_LOADED_IMAGE_ID = 'sha256:9841cba8df3345fa22e39457645ba250431ad1f465ce4204d92f73f7816215b9'
A175_RUNNING_IDS = {
    'porota_production_observer': '72ed632d0514ec1835e3dcb7983aa2b13cfbcf2b46c5a62132e5c739f7df86eb',
    'porota_production_dashboard': '9b01cf17b7c3e46d96474d998840fc0e4c4c4f828dd825bb44744710b99686bb',
}
A175_FAILURE_BOOK_STATE = ('WAITING_MARKET','MARKET_CLOSED')
A176_OPERATOR_NO_GO_RECEIPT_SHA256 = '3218ccf5296306068d051b12588c4fac253a768ea3c81e0fc31c501adb34338c'  # Actions #40875630811, PAPER admission held.
A175_PENDING_MODE = 'CLOSED_A175_PENDING_FIX_FORWARD'
A143_PENDING_SOURCE_SHA = '2325eba329878c62a61cb47bb66d33ccf279bd4a'
A143_PENDING_DEPLOY_SHA = '6676267140907a17e342a664f718cfdcf9b3ce72'
A143_PENDING_TREE_SHA = 'e66e3756769e297687a062bec2fa6e8599c0a275'
A143_PENDING_IMAGE_ID = 'sha256:90e54acb1189a03acbed4b9228943ab556bdbe3a01fd515285951fc90eda93e7'
# #751 Predeploy artifact 12780414186 has a different Docker config ID from
# the image materialized by Docker load on this host. The exact frozen JSON
# member SHA256 is independently proven by its archive digest and A160 host
# read-only probe; the runtime ID is bound by the A143 marker, receipt and
# both live containers. Never substitute one identity for the other.
A143_PENDING_FROZEN_IMAGE_ID = 'sha256:092ebfcf75c6b754b53e84d034acde7e4c328a8866ad8f5e3e8f50685d088ff0'
A143_PENDING_FROZEN_SHA256 = '6a28244fda8549bc9089a27d184e7aa0003715bdf9269f8b90f5a4017c91a304'
RESCUE_SOURCE_SHA = '3fd020f408275a490e911e06f60a1ddc89153fe5'
RESCUE_IMAGE_ID = 'sha256:586464c20b74ab3a7ba842df8dfafcd76f21eb4c7bed553f23f6f187924ff735'
RESCUE_CALENDAR_SHA256 = '32c5b024e6166b91adaff473b4884ec838f2cb2c0f9197427ab9b6f276b15c7a'
RESCUE_HOURS_SHA256 = '380bb2dd5e7250201d76f765ea1cae854ee34c344d05e0da911e9501c68be86f'
# The original wire-mode/CLI label contains the first incident date.  Keep it
# stable for receipts, but bound admission to the verified COM18782 week.
# Any later date or a pre-20:00 ART attempt remains strictly fail-closed.
RESCUE_FIRST_DAY = '2026-10-26'
RESCUE_LAST_DAY = '2026-10-30'
FAILED_A92_RESCUE_CANDIDATE = 'dd22a1b0761cc4f231813edb6b502d1dd21bb8de'
FAILED_A92_RESCUE_DEPLOY = '7df3c8fb1d2e32f3eb07b9d969c5b00d16e49b21'


def candidate_operator_module(*, require_staged=False):
    """Use candidate A125/A151 source, never an old host helper for A151."""
    encoded = os.environ.get('RC6_A125_CANDIDATE_SOURCE_B64')
    compressed = os.environ.get('RC6_A125_CANDIDATE_SOURCE_GZIP_B64')
    if encoded and compressed:
        raise ValueError('QUIESCENCE_OPERATOR_SOURCE_AMBIGUOUS')
    if compressed or encoded:
        try:
            if compressed:
                if len(compressed) > 40000:
                    raise ValueError('QUIESCENCE_OPERATOR_COMPRESSED_TOO_LARGE')
                packed = base64.b64decode(compressed, validate=True)
                if not 0 < len(packed) < 30000:
                    raise ValueError('QUIESCENCE_OPERATOR_COMPRESSED_SIZE_RED')
                inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
                source = inflater.decompress(packed, 110000)
                if (not inflater.eof or inflater.unconsumed_tail or
                        inflater.unused_data or
                        hashlib.sha256(source).hexdigest() !=
                        os.environ.get('RC6_A125_CANDIDATE_SOURCE_SHA256')):
                    raise ValueError('QUIESCENCE_OPERATOR_COMPRESSED_HASH_RED')
            else:
                if len(encoded) > 150000:
                    raise ValueError('QUIESCENCE_OPERATOR_SOURCE_TOO_LARGE')
                source = base64.b64decode(encoded, validate=True)
            if not 0 < len(source) < 110000:
                raise ValueError('QUIESCENCE_OPERATOR_SOURCE_SIZE_RED')
            module = types.ModuleType('rc6_candidate_operator_no_go')
            exec(compile(source,'candidate/rc6_deploy_scheduler_suspend.py','exec'),
                 module.__dict__)
        except (binascii.Error,zlib.error,ValueError) as exc:
            raise ValueError('QUIESCENCE_OPERATOR_SOURCE_INVALID') from exc
    elif not require_staged:
        # The recovery script is also streamed over SSH on stdin before the
        # candidate is fully installed.  There is no __file__ or guaranteed
        # working directory in that route: bind imports to the fixed host root.
        if ROOT.is_dir() and str(ROOT) not in os.sys.path:
            os.sys.path.insert(0, str(ROOT))
        from scripts import rc6_deploy_scheduler_suspend as module
    else:
        raise ValueError('QUIESCENCE_A151_STAGED_OPERATOR_REQUIRED')
    return module


def operator_paper_no_go_proof(*, require_original_marker=False):
    """Verify A125 without trusting pre-candidate host code in streamed steps.

    The promote runner streams this exact candidate source before it installs
    anything. Once installed, the same function exists in the candidate repo.
    Neither route changes systemd or grants PAPER trading permission.
    """
    module = candidate_operator_module()
    digest = module.exact_operator_preopen_pause(
        require_original_marker=require_original_marker)
    if (not re.fullmatch('[0-9a-f]{64}',digest or '') or
            not module.operator_preopen_timer_disabled()):
        raise ValueError('QUIESCENCE_OPERATOR_PAUSE_TIMER_RED')
    return digest


def a151_held_open_proof(*, operator_a151_proof=None,
                         operator_timer_disabled=None,
                         continuation_only=False):
    """Admit only the exact dated A151 NO_GO for a flat #751 recovery.

    This is a deployment quiescence proof during the guardian's 10:30–17:00
    OPEN window, not a PAPER trading admission or a replacement for CLOSED.
    The current flat book, Docker pair and marker are checked separately by
    a143_pending_running_proof before any stop intent is persisted.
    """
    local = datetime.now(ZoneInfo('America/Argentina/Buenos_Aires'))
    end = 20 * 60 if continuation_only else 17 * 60
    if (local.date().isoformat() != '2026-10-28' or
            not (10 * 60 + 30 <= local.hour * 60 + local.minute < end)):
        raise ValueError('QUIESCENCE_A151_HELD_OPEN_WINDOW_RED')
    calendar_path = ROOT/'ak_byma_calendar.py'
    if calendar_path.is_symlink() or not calendar_path.is_file():
        raise ValueError('QUIESCENCE_A151_CALENDAR_SOURCE_RED')
    calendar_source = calendar_path.read_bytes()
    if hashlib.sha256(calendar_source).hexdigest() != RESCUE_CALENDAR_SHA256:
        raise ValueError('QUIESCENCE_A151_CALENDAR_SOURCE_RED')
    calendar = types.ModuleType('rc6_a151_held_open_calendar')
    exec(compile(calendar_source,str(calendar_path),'exec'),
         calendar.__dict__)
    if (calendar.CALENDARIO_ACTUALIZADO_EL.isoformat() != '2026-10-14' or
            2026 not in calendar.ANIOS_AUDITADOS or
            not calendar.es_dia_habil_operativo(local.date())):
        raise ValueError('QUIESCENCE_A151_CALENDAR_DAY_RED')
    if (operator_a151_proof is None) != (operator_timer_disabled is None):
        raise ValueError('QUIESCENCE_A151_OPERATOR_CALLBACK_PAIR_REQUIRED')
    if operator_a151_proof is None:
        module = candidate_operator_module(require_staged=True)
        operator_a151_proof = module.exact_a151_operator_preopen_pause
        operator_timer_disabled = module.operator_preopen_timer_disabled
    if not callable(operator_a151_proof) or not callable(operator_timer_disabled):
        raise ValueError('QUIESCENCE_A151_OPERATOR_PROOF_MISSING')
    receipt_sha, operation = operator_a151_proof()
    if (not isinstance(receipt_sha, str) or
            re.fullmatch('[0-9a-f]{64}', receipt_sha) is None or
            not isinstance(operation, str) or
            re.fullmatch(r'A151-[1-9][0-9]{0,15}-[1-9][0-9]{0,3}', operation) is None):
        raise ValueError('QUIESCENCE_A151_OPERATOR_RECEIPT_RED')
    # The receipt verifier attests the two immutable sidecars, historical
    # A125 digest, original #751 identity, disabled preopen and PAPER/0.
    # Explicitly recheck the live timer if using a directly supplied callback.
    if not operator_timer_disabled():
        raise ValueError('QUIESCENCE_A151_PREOPEN_REARMED')
    return {'admission': A151_HELD_OPEN_MODE,
            'business_day_art': '2026-10-28',
            'observed_at_utc': local.astimezone(timezone.utc).isoformat(),
            'operator_receipt_sha256': receipt_sha,
            'operator_operation_id': operation,
            'paper_execution_authorized': False}


def degraded_closed_rescue_proof():
    """Exact A92 postclose incident, not a new healthy CLOSED phase."""
    local = datetime.now(ZoneInfo('America/Argentina/Buenos_Aires'))
    day = local.date().isoformat()
    if not RESCUE_FIRST_DAY <= day <= RESCUE_LAST_DAY:
        raise ValueError('QUIESCENCE_RESCUE_WINDOW_RED')
    operator_pause_sha = None
    # The owner explicitly authorized immediate PAPER downtime on 27 Oct.
    # A92 is known STOPPED/CLOSED with a failing scanner. Earlier than the
    # normal postclose gate, require the independent exact A125 no-GO receipt
    # and a still-disabled preopen timer; absence always blocks host writes.
    if (local.hour, local.minute) < (20, 0):
        if day != '2026-10-27':
            raise ValueError('QUIESCENCE_RESCUE_WINDOW_RED')
        try:
            operator_pause_sha = operator_paper_no_go_proof(
                require_original_marker=True)
        except (OSError, ValueError) as exc:
            raise ValueError('QUIESCENCE_RESCUE_OPERATOR_PAUSE_RED') from exc
    calendar_path = ROOT / 'ak_byma_calendar.py'
    if (calendar_path.is_symlink() or not calendar_path.is_file() or
            hashlib.sha256(calendar_path.read_bytes()).hexdigest() != RESCUE_CALENDAR_SHA256):
        raise ValueError('QUIESCENCE_RESCUE_CALENDAR_SOURCE_RED')
    # Compile the exact pinned bytes in memory; no importlib pycache write on
    # the installed host before the durable drain receipt is committed.
    calendar = types.ModuleType('rc6_rescue_byma_calendar')
    exec(compile(calendar_path.read_bytes(), str(calendar_path), 'exec'), calendar.__dict__)
    if (2026 not in calendar.ANIOS_AUDITADOS or
            calendar.CALENDARIO_ACTUALIZADO_EL.isoformat() != '2026-10-14' or
            not calendar.es_dia_habil_operativo(local.date())):
        raise ValueError('QUIESCENCE_RESCUE_CALENDAR_DAY_RED')
    return {'observed_at_utc': local.astimezone(timezone.utc).isoformat(),
            'observed_at_art': local.isoformat(), 'business_day': day,
            'operator_paper_early': operator_pause_sha is not None,
            'operator_pause_sha256': operator_pause_sha,
            'calendar_sha256': RESCUE_CALENDAR_SHA256,
            'calendar_url': 'https://www.byma.com.ar/mercado/calendario-bursatil',
            'byma_hours_com18782_sha256': RESCUE_HOURS_SHA256,
            'byma_hours_url': 'https://www.byma.com.ar/comunicados/comunicado-18782-horarios',
            'byma_latest_session_end_art': '19:00',
            'a3_latest_session_end_art': '17:30'}


def scanner_crash_full_proof():
    """Bounded read-only child+trace proof for A92 STOPPED/CLOSED incident."""
    path = (ROOT/'data/paper_v17/artifacts/observer_v17.db/runtime-health.json')
    if (path.is_symlink() or not path.is_file() or path.stat().st_size > 131072 or
            path.stat().st_nlink != 1):
        raise ValueError('QUIESCENCE_RESCUE_CHILD_HEALTH_UNSAFE')
    raw = path.read_bytes()
    health = json.loads(raw)
    scanner = health.get('children', {}).get('scanner', {})
    now = datetime.now(timezone.utc)
    stamp = datetime.fromisoformat(health['recorded_at'])
    last_exit = datetime.fromisoformat(scanner['last_exit_at'])
    phase = scanner.get('state')
    if phase == 'CRASH_BACKOFF':
        phase_ok = scanner.get('pid') is None and scanner.get('last_error') == 'CHILD_EXITED'
    elif phase == 'RUNNING':
        # A92 alternates roughly 30 seconds of backoff and a short-lived
        # scanner. A recent completed failure is still visible while RUNNING.
        started = datetime.fromisoformat(scanner['started_at'])
        phase_ok = (type(scanner.get('pid')) is int and scanner['pid'] > 0 and
                    scanner.get('last_error') is None and started.tzinfo is not None and
                    last_exit <= started <= now and
                    0 <= (now-last_exit).total_seconds() <= 60)
    else:
        phase_ok = False
    if (stamp.tzinfo is None or last_exit.tzinfo is None or
            not 0 <= (now-stamp).total_seconds() <= 30 or
            not 0 <= (now-last_exit).total_seconds() <= 60 or
            health.get('schema') != 'rc6.runtime-child-health.v1' or
            health.get('source_sha') != RESCUE_SOURCE_SHA or
            health.get('mode') != 'PRODUCTION_PAPER' or
            type(health.get('real_orders_sent')) is not int or
            health['real_orders_sent'] != 0 or
            health.get('real_routes') != 'NOT_CALLED' or
            not phase_ok or
            type(scanner.get('restarts')) is not int or scanner['restarts'] < 90):
        raise ValueError('QUIESCENCE_RESCUE_CHILD_CRASH_UNVERIFIED')
    logs = subprocess.run(['docker','logs','--since','90s','--tail','500',OWNED[0]],
                          capture_output=True,text=True,timeout=8)
    raw_log = logs.stdout + logs.stderr
    if logs.returncode or len(raw_log) > 256*1024:
        raise ValueError('QUIESCENCE_RESCUE_TRACE_UNAVAILABLE')
    match = re.search(r'File [^\n]*bi_operational_services\.py", line 424, in _period_data'
                      r'[\s\S]{0,3000}?sqlite3\.OperationalError: database or disk is full',
                      raw_log)
    if not match:
        raise ValueError('QUIESCENCE_RESCUE_SQLITE_FULL_TRACE_MISSING')
    return {'recorded_at':health['recorded_at'],
            'health_sha256':hashlib.sha256(raw).hexdigest(),
            'scanner_restarts':scanner['restarts'],
            'scanner_state':phase,
            'last_exit_at':scanner['last_exit_at'],
            'trace_sha256':hashlib.sha256(match.group().encode()).hexdigest(),
            'failure':'A92_REPORT_SQLITE_FULL'}


def exact_rescue_receipt(receipt):
    """Prevent a replayed, future-dated or mode-switched rescue receipt."""
    proof = receipt.get('rescue_calendar_and_hours')
    prior_state = receipt.get('rescue_pre_state')
    crash = receipt.get('rescue_child_crash_proof')
    if (prior_state not in (['DEGRADED', 'BOOT_CALENDAR'], ['STOPPED', 'CLOSED']) or
            (prior_state == ['STOPPED', 'CLOSED'] and
             (not isinstance(crash, dict) or crash.get('failure') != 'A92_REPORT_SQLITE_FULL' or
              type(crash.get('scanner_restarts')) is not int or
              crash['scanner_restarts'] < 90 or
              crash.get('scanner_state') not in {'CRASH_BACKOFF', 'RUNNING'} or
              not re.fullmatch('[0-9a-f]{64}', crash.get('health_sha256','')) or
              not re.fullmatch('[0-9a-f]{64}', crash.get('trace_sha256','')))) or
            receipt.get('degraded_closed_rescue') is not True or
            receipt.get('rescue_source_sha') != RESCUE_SOURCE_SHA or
            receipt.get('rescue_image_id') != RESCUE_IMAGE_ID or
            not isinstance(proof, dict) or
            not RESCUE_FIRST_DAY <= str(proof.get('business_day')) <= RESCUE_LAST_DAY or
            proof.get('calendar_sha256') != RESCUE_CALENDAR_SHA256 or
            proof.get('byma_hours_com18782_sha256') != RESCUE_HOURS_SHA256 or
            proof.get('byma_latest_session_end_art') != '19:00' or
            proof.get('a3_latest_session_end_art') != '17:30' or
            type(proof.get('operator_paper_early', False)) is not bool or
            (proof.get('operator_paper_early', False) and
             (proof['business_day'] != '2026-10-27' or
              not re.fullmatch('[0-9a-f]{64}', proof.get('operator_pause_sha256') or ''))) or
            (not proof.get('operator_paper_early', False) and
             proof.get('operator_pause_sha256') is not None) or
            set(receipt.get('containers', {})) != set(OWNED) or
            any(receipt['containers'][name].get('image') != RESCUE_IMAGE_ID
                for name in OWNED)):
        raise ValueError('QUIESCENCE_RESCUE_RECEIPT_CHANGED')
    try:
        at = datetime.fromisoformat(proof['observed_at_utc'])
        art = datetime.fromisoformat(proof['observed_at_art'])
        now = datetime.now(timezone.utc)
        if (at.tzinfo is None or art.tzinfo is None or
                at.utcoffset().total_seconds() != 0 or
                art.utcoffset().total_seconds() != -10800 or
                at != art.astimezone(timezone.utc) or at > now or
                art.astimezone(ZoneInfo('America/Argentina/Buenos_Aires')).date().isoformat()
                != proof['business_day'] or
                ((art.hour, art.minute) < (20, 0) and
                 not proof.get('operator_paper_early', False)) or
                ((art.hour, art.minute) >= (20, 0) and
                 proof.get('operator_paper_early', False))):
            raise ValueError('QUIESCENCE_RESCUE_RECEIPT_CLOCK_RED')
        if prior_state == ['STOPPED', 'CLOSED']:
            health_at = datetime.fromisoformat(crash['recorded_at'])
            last_exit = datetime.fromisoformat(crash['last_exit_at'])
            if (health_at.tzinfo is None or last_exit.tzinfo is None or
                    not 0 <= (at-health_at).total_seconds() <= 30 or
                    not 0 <= (at-last_exit).total_seconds() <= 60):
                raise ValueError('QUIESCENCE_RESCUE_RECEIPT_CHILD_CLOCK_RED')
        recorded = receipt.get('recorded_at')
        if recorded:
            recorded_at = datetime.fromisoformat(recorded)
            if recorded_at.tzinfo is None or not at <= recorded_at <= now:
                raise ValueError('QUIESCENCE_RESCUE_RECEIPT_CLOCK_RED')
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('QUIESCENCE_RESCUE_RECEIPT_CLOCK_RED') from exc
    return proof


def current_runtime_marker():
    """Read the installed provenance; observer_state has no source_sha column."""
    path = ROOT/'data/deploy/CURRENT_STATE_V2.json'
    if (path.is_symlink() or not path.is_file() or path.stat().st_size > 65536):
        raise ValueError('QUIESCENCE_RESCUE_CURRENT_STATE_UNSAFE')
    current = json.loads(path.read_bytes())
    at = datetime.fromisoformat(current['recorded_at'])
    if (current.get('schema_version') != 2 or at.tzinfo is None or
            at > datetime.now(timezone.utc) or
            not re.fullmatch('[0-9a-f]{40}',current.get('candidate_sha','')) or
            not re.fullmatch('sha256:[0-9a-f]{64}',current.get('image_id','')) or
            current.get('mode') != 'PRODUCTION_PAPER' or
            type(current.get('real_orders_sent')) is not int or
            current['real_orders_sent'] != 0 or
            current.get('real_order_routes') != 'NOT_CALLED' or
            current.get('validation_status') not in
            {'VALIDATED_RUNTIME','DEPLOYED_VALIDATION_PENDING'}):
        raise ValueError('QUIESCENCE_RESCUE_CURRENT_STATE_RED')
    return current


def exact_a92_marker():
    current = current_runtime_marker()
    if (current['candidate_sha'] != RESCUE_SOURCE_SHA or
            current['image_id'] != RESCUE_IMAGE_ID or
            current['validation_status'] != 'VALIDATED_RUNTIME'):
        raise ValueError('QUIESCENCE_RESCUE_A92_PROVENANCE_RED')
    return current


def fresh_a92_child_source():
    path = ROOT/'data/paper_v17/artifacts/observer_v17.db/runtime-health.json'
    if (path.is_symlink() or not path.is_file() or path.stat().st_size > 131072 or
            path.stat().st_nlink != 1):
        raise ValueError('QUIESCENCE_RESCUE_CHILD_HEALTH_UNSAFE')
    health = json.loads(path.read_bytes())
    at = datetime.fromisoformat(health['recorded_at'])
    if (at.tzinfo is None or not 0 <= (datetime.now(timezone.utc)-at).total_seconds() <= 30 or
            health.get('schema') != 'rc6.runtime-child-health.v1' or
            health.get('source_sha') != RESCUE_SOURCE_SHA or
            health.get('mode') != 'PRODUCTION_PAPER' or
            type(health.get('real_orders_sent')) is not int or
            health['real_orders_sent'] != 0 or health.get('real_routes') != 'NOT_CALLED'):
        raise ValueError('QUIESCENCE_RESCUE_CHILD_SOURCE_RED')
    return health


def rescue_flat_book():
    """Bounded PAPER/0 book with separate installed marker provenance."""
    marker = current_runtime_marker()
    path = ROOT / 'data/paper_v17/observer_v17.db'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)) as c:
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA busy_timeout=200')
        end = time.monotonic() + 2
        c.set_progress_handler(lambda: int(time.monotonic() > end), 1000)
        row = c.execute('SELECT mode,real_orders_sent,process_state,session_state '
                        'FROM observer_state WHERE id=1').fetchone()
        if not row or row[0] != 'PRODUCTION_PAPER' or type(row[1]) is not int or row[1] != 0:
            raise ValueError('QUIESCENCE_RESCUE_PAPER_ZERO_RED')
        for table, state in (('paper_positions', 'OPEN'), ('paper_cauciones', 'OPEN'),
                             ('paper_future_positions', 'ACTIVE')):
            if c.execute('SELECT 1 FROM '+table+' WHERE status=? LIMIT 1', (state,)).fetchone():
                raise ValueError('QUIESCENCE_RESCUE_BOOK_NOT_FLAT')
        return {'process_state': row[2], 'session_state': row[3],
                'source_sha': marker['candidate_sha'], 'real_orders_sent': 0}


def candidate_image_intent(candidate, image_id, manifest_path):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id or ''):
        raise ValueError('QUIESCENCE_RESCUE_CANDIDATE_IMAGE_INVALID')
    path = Path(manifest_path or '')
    if (not path.is_absolute() or path.is_symlink() or not path.is_file() or
            path.stat().st_size > 16384):
        raise ValueError('QUIESCENCE_RESCUE_FROZEN_MANIFEST_UNSAFE')
    raw = path.read_bytes()
    frozen = json.loads(raw)
    if (frozen.get('candidate_sha') != candidate or
            not re.fullmatch('[0-9a-f]{40}', frozen.get('candidate_tree_sha', '')) or
            not re.fullmatch('[0-9a-f]{64}', frozen.get('image_tar_sha256', ''))):
        raise ValueError('QUIESCENCE_RESCUE_FROZEN_IDENTITY_RED')
    tag = 'porota-trading-bot:17.0.0-rc6-candidate-' + candidate
    images = json.loads(command(['docker', 'image', 'inspect', tag]))
    if len(images) != 1 or images[0].get('Id') != image_id:
        raise ValueError('QUIESCENCE_RESCUE_IMAGE_TAG_CHANGED')
    return {'image_id': image_id, 'manifest_sha256': hashlib.sha256(raw).hexdigest(),
            'frozen_manifest_b64': base64.b64encode(raw).decode('ascii'),
            'candidate_tree_sha': frozen['candidate_tree_sha'],
            'image_tar_sha256': frozen['image_tar_sha256'],
            'recorded_at': datetime.now(timezone.utc).isoformat()}


def candidate_runtime_after_failed_install(receipt):
    intent = receipt.get('candidate_image_intent')
    if not isinstance(intent, dict):
        raise ValueError('QUIESCENCE_RESCUE_IMAGE_INTENT_MISSING')
    recorded = datetime.fromisoformat(intent['recorded_at'])
    if recorded.tzinfo is None or recorded > datetime.now(timezone.utc):
        raise ValueError('QUIESCENCE_RESCUE_IMAGE_INTENT_CLOCK_RED')
    observed = {name: inspect(name) for name in OWNED}
    if (any(not re.fullmatch('[0-9a-f]{64}', v['Id']) or
            v['Image'] != intent['image_id'] for v in observed.values()) or
            len({v['State']['Status'] for v in observed.values()}) != 1):
        raise ValueError('QUIESCENCE_RESCUE_CANDIDATE_RUNTIME_MIXED')
    status = next(iter(observed.values()))['State']['Status']
    if status not in {'running', 'exited'}:
        raise ValueError('QUIESCENCE_RESCUE_CANDIDATE_STATUS_RED')
    proof = {}
    for name, value in observed.items():
        state = lifecycle(value)
        if status == 'exited':
            finished = datetime.fromisoformat(state['FinishedAt'].replace('Z', '+00:00'))
            if (type(state['ExitCode']) is not int or state['ExitCode'] != 0 or
                    state['OOMKilled'] is not False or finished.tzinfo is None or
                    not recorded <= finished <= datetime.now(timezone.utc)):
                raise ValueError('QUIESCENCE_RESCUE_CANDIDATE_STOP_UNCLEAN')
        proof[name] = {'id': value['Id'], 'image': value['Image'],
                       'lifecycle': state}
    book = rescue_flat_book()
    if book['source_sha'] not in {receipt['candidate_sha'], RESCUE_SOURCE_SHA}:
        raise ValueError('QUIESCENCE_RESCUE_CANDIDATE_SOURCE_RED')
    critical = critical_admission()
    return {'status': status, 'containers': proof, 'flat_book': book,
            'critical_admission': critical,
            'observed_at': datetime.now(timezone.utc).isoformat()}


def _a143_exact_json(path, limit):
    """Read an exact local receipt without following aliases or duplicate keys."""
    if (path.is_symlink() or not path.is_file() or
            path.stat().st_size > limit or path.stat().st_nlink != 1):
        raise ValueError('QUIESCENCE_A143_RECEIPT_UNSAFE:' + path.name)
    raw = path.read_bytes()
    def no_duplicate(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('QUIESCENCE_A143_DUPLICATE_KEY')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=no_duplicate,
                       parse_constant=lambda _: (_ for _ in ()).throw(
                           ValueError('QUIESCENCE_A143_NONFINITE')))
    if type(value) is not dict:
        raise ValueError('QUIESCENCE_A143_JSON_OBJECT_REQUIRED')
    return value, hashlib.sha256(raw).hexdigest()


def a143_pending_running_proof(originals=None, *, allow_a151_held_open=False,
                               operator_a151_proof=None,
                               operator_timer_disabled=None,
                               held_continuation=None):
    """A145: admit ONLY the installed #751 running, failed-validation PAPER pair.

    The old A92 and any healthy-successor routes are intentionally excluded.
    This is a pre-stop RECOVERY proof, never a VALIDATED_RUNTIME assertion.
    """
    held_open = None
    close = None
    if held_continuation is not None and not allow_a151_held_open:
        raise ValueError('QUIESCENCE_A151_CONTINUATION_MODE_INVALID')
    if allow_a151_held_open:
        local = datetime.now(ZoneInfo('America/Argentina/Buenos_Aires'))
        # A151 is one single day. Historical independently postclosed
        # recovery stays available on 29/30; it cannot borrow A151's receipt.
        day_a151 = local.date().isoformat() == '2026-10-28'
        if held_continuation is not None and not day_a151:
            raise ValueError('QUIESCENCE_A151_OPERATOR_DAY_EXPIRED')
        minute = local.hour * 60 + local.minute
        if day_a151 and (10 * 60 + 30 <= minute < 17 * 60 or (
                held_continuation is not None and minute < 20 * 60)):
            held_open = a151_held_open_proof(
                operator_a151_proof=operator_a151_proof,
                operator_timer_disabled=operator_timer_disabled,
                continuation_only=held_continuation is not None)
            if held_continuation is not None and (
                    type(held_continuation) is not dict or
                    held_continuation.get('admission') != A151_HELD_OPEN_MODE or
                    held_continuation.get('business_day_art') != '2026-10-28' or
                    held_continuation.get('operator_receipt_sha256') !=
                    held_open['operator_receipt_sha256'] or
                    held_continuation.get('operator_operation_id') !=
                    held_open['operator_operation_id'] or
                    held_continuation.get('paper_execution_authorized') is not False):
                raise ValueError('QUIESCENCE_A151_CONTINUATION_RECEIPT_CHANGED')
        else:
            close = adopted_rescue_closed_proof()
    else:
        close = adopted_rescue_closed_proof()  # Historical CLOSED route unchanged.
    folder = ROOT/'data/deploy'
    marker, marker_digest = _a143_exact_json(folder/'CURRENT_STATE_V2.json', 65536)
    recorded = datetime.fromisoformat(marker['recorded_at'])
    if (recorded.tzinfo is None or recorded > datetime.now(timezone.utc) or
            marker.get('schema_version') != 2 or
            marker.get('candidate_sha') != A143_PENDING_SOURCE_SHA or
            marker.get('deploy_sha') != A143_PENDING_DEPLOY_SHA or
            marker.get('image_id') != A143_PENDING_IMAGE_ID or
            marker.get('validation_status') != 'DEPLOYED_VALIDATION_PENDING' or
            marker.get('deployment_strategy') != 'FIX_FORWARD_ONLY' or
            marker.get('build_once') is not True or
            marker.get('mode') != 'PRODUCTION_PAPER' or
            type(marker.get('real_orders_sent')) is not int or
            marker['real_orders_sent'] != 0 or
            marker.get('real_order_routes') != 'NOT_CALLED' or
            marker.get('ppi_watch_untouched') is not True):
        raise ValueError('QUIESCENCE_A143_PENDING_MARKER_RED')
    frozen, frozen_digest = _a143_exact_json(folder/'porota-frozen-candidate.json', 16384)
    if (frozen_digest != A143_PENDING_FROZEN_SHA256 or
            frozen.get('candidate_sha') != A143_PENDING_SOURCE_SHA or
            frozen.get('image_id') != A143_PENDING_FROZEN_IMAGE_ID or
            frozen.get('candidate_tree_sha') != A143_PENDING_TREE_SHA or
            not re.fullmatch('[0-9a-f]{64}',frozen.get('image_tar_sha256',''))):
        raise ValueError('QUIESCENCE_A143_FROZEN_IDENTITY_RED')
    prior, prior_digest = _a143_exact_json(
        folder/('quiescence-'+A143_PENDING_SOURCE_SHA+'.json'), 65536)
    intent = prior.get('candidate_image_intent')
    runtime = prior.get('candidate_runtime_after_failure')
    if (prior.get('candidate_sha') != A143_PENDING_SOURCE_SHA or
            prior.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED' or
            prior.get('installation_started') is not True or
            prior.get('degraded_closed_rescue') is not True or
            type(prior.get('real_orders_sent')) is not int or
            prior['real_orders_sent'] != 0 or
            prior.get('ppi_watch_accessed') is not False or
            not isinstance(intent,dict) or not isinstance(runtime,dict) or
            intent.get('image_id') != A143_PENDING_IMAGE_ID or
            intent.get('candidate_tree_sha') != frozen['candidate_tree_sha'] or
            intent.get('manifest_sha256') != frozen_digest or
            runtime.get('status') != 'running' or
            set(runtime.get('containers',{})) != set(OWNED) or
            not isinstance(runtime.get('flat_book'),dict) or
            runtime['flat_book'].get('source_sha') != A143_PENDING_SOURCE_SHA or
            runtime['flat_book'].get('real_orders_sent') != 0):
        raise ValueError('QUIESCENCE_A143_PRIOR_RECEIPT_RED')
    observed = originals or {name:inspect(name) for name in OWNED}
    if set(observed) != set(OWNED):
        raise ValueError('QUIESCENCE_A143_RUNTIME_SET_CHANGED')
    for name in OWNED:
        value = observed[name]
        expected = runtime['containers'][name]
        if (not re.fullmatch('[0-9a-f]{64}',value['Id']) or
                value['Image'] != A143_PENDING_IMAGE_ID or
                value['State']['Status'] != 'running' or
                value['Id'] != expected.get('id') or
                value['Image'] != expected.get('image') or
                lifecycle(value) != expected.get('lifecycle')):
            raise ValueError('QUIESCENCE_A143_RUNTIME_IMAGE_OR_ID_RED')
    book = rescue_flat_book()
    if (held_open is not None and
            (book['process_state'],book['session_state']) != ('DEGRADED','BOOT_CALENDAR')):
        raise ValueError('QUIESCENCE_A151_HELD_OPEN_BOOK_STATE_RED')
    if ((book['process_state'],book['session_state']) not in
            {('DEGRADED','BOOT_CALENDAR'),('DEGRADED','MARKET_CLOSED'),
             ('STOPPED','CLOSED')} or
            book['source_sha'] != A143_PENDING_SOURCE_SHA or
            book['real_orders_sent'] != 0):
        raise ValueError('QUIESCENCE_A143_CLOSED_FLAT_BOOK_RED')
    db_path = ROOT/'data/paper_v17/observer_v17.db'
    with closing(sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True,timeout=.2)) as c:
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA busy_timeout=200')
        heartbeat = c.execute('SELECT heartbeat_at FROM observer_state WHERE id=1').fetchone()
    if not heartbeat:
        raise ValueError('QUIESCENCE_A143_HEARTBEAT_MISSING')
    stamp = datetime.fromisoformat(heartbeat[0])
    if (stamp.tzinfo is None or not
            0 <= (datetime.now(timezone.utc)-stamp).total_seconds() <= 180):
        raise ValueError('QUIESCENCE_A143_HEARTBEAT_STALE')
    critical = critical_admission()
    return {'source_sha':A143_PENDING_SOURCE_SHA,
            'image_id':A143_PENDING_IMAGE_ID,
            'candidate_tree_sha':frozen['candidate_tree_sha'],
            'marker_sha256':marker_digest,
            'frozen_sha256':frozen_digest,
            'prior_receipt_sha256':prior_digest,
            'containers':{name:{'id':observed[name]['Id'],
                                'image':observed[name]['Image'],
                                'lifecycle':lifecycle(observed[name])} for name in OWNED},
            'flat_book':book, 'critical_admission':critical,
            'close':close, 'operator_hold':held_open}


def a143_stopped_continuation(receipt, *, operator_a151_proof=None,
                              operator_timer_disabled=None):
    """A143 is still unvalidated after stopping. Never restart or forgive drift."""
    if (receipt.get('a143_pending_adopted') is not True or
            receipt.get('installation_started') is not False or
            receipt.get('status') not in {'DRAINING','DRAINED',
                                          'PRIOR_A143_UNVALIDATED_HELD_STOPPED'}):
        raise ValueError('QUIESCENCE_A143_STOP_MODE_RED')
    proof = receipt.get('a143_previous_proof')
    if (not isinstance(proof,dict) or
            proof.get('source_sha') != A143_PENDING_SOURCE_SHA or
            proof.get('image_id') != A143_PENDING_IMAGE_ID or
            set(proof.get('containers',{})) != set(OWNED)):
        raise ValueError('QUIESCENCE_A143_STOP_PROOF_INVALID')
    held_open = proof.get('operator_hold')
    if held_open is not None:
        if (type(held_open) is not dict or
                held_open.get('admission') != A151_HELD_OPEN_MODE or
                held_open.get('business_day_art') != '2026-10-28' or
                held_open.get('paper_execution_authorized') is not False or
                proof.get('close') is not None or
                datetime.now(ZoneInfo('America/Argentina/Buenos_Aires'))
                .date().isoformat() != '2026-10-28'):
            raise ValueError('QUIESCENCE_A151_STOP_HOLD_EXPIRED_OR_CHANGED')
        if (operator_a151_proof is None) != (operator_timer_disabled is None):
            raise ValueError('QUIESCENCE_A151_STOP_CALLBACK_PAIR_REQUIRED')
        if operator_a151_proof is None:
            operator = candidate_operator_module(require_staged=True)
            operator_a151_proof = operator.exact_a151_operator_preopen_pause
            operator_timer_disabled = operator.operator_preopen_timer_disabled
        receipt_sha, operation = operator_a151_proof()
        if (receipt_sha != held_open.get('operator_receipt_sha256') or
                operation != held_open.get('operator_operation_id') or
                not operator_timer_disabled()):
            raise ValueError('QUIESCENCE_A151_STOP_HOLD_CHANGED')
    folder = ROOT/'data/deploy'
    marker, marker_sha = _a143_exact_json(folder/'CURRENT_STATE_V2.json', 65536)
    frozen, frozen_sha = _a143_exact_json(folder/'porota-frozen-candidate.json', 16384)
    _, prior_sha = _a143_exact_json(
        folder/('quiescence-'+A143_PENDING_SOURCE_SHA+'.json'), 65536)
    if (marker_sha != proof.get('marker_sha256') or
            frozen_sha != proof.get('frozen_sha256') or
            frozen_sha != A143_PENDING_FROZEN_SHA256 or
            prior_sha != proof.get('prior_receipt_sha256') or
            marker.get('candidate_sha') != A143_PENDING_SOURCE_SHA or
            marker.get('deploy_sha') != A143_PENDING_DEPLOY_SHA or
            marker.get('image_id') != A143_PENDING_IMAGE_ID or
            marker.get('validation_status') != 'DEPLOYED_VALIDATION_PENDING' or
            frozen.get('candidate_sha') != A143_PENDING_SOURCE_SHA or
            frozen.get('image_id') != A143_PENDING_FROZEN_IMAGE_ID):
        raise ValueError('QUIESCENCE_A143_STOP_SOURCE_CHANGED')
    exact_clean_stopped(receipt)
    for name in OWNED:
        if (receipt['containers'][name]['id'] != proof['containers'][name]['id'] or
                receipt['containers'][name]['image'] != proof['containers'][name]['image']):
            raise ValueError('QUIESCENCE_A143_STOP_DOCKER_ID_CHANGED')
    book = rescue_flat_book()
    # An exact A151 prestate may acknowledge SIGTERM as STOPPED while keeping
    # BOOT_CALENDAR. No other calendar transition is admitted by that receipt.
    allowed = ({('DEGRADED','BOOT_CALENDAR'),('STOPPED','BOOT_CALENDAR')}
               if held_open is not None else
               {('DEGRADED','BOOT_CALENDAR'),('DEGRADED','MARKET_CLOSED'),
                ('STOPPED','CLOSED')})
    if held_open is not None and (proof['flat_book'].get('process_state'),
                                  proof['flat_book'].get('session_state')) != \
            ('DEGRADED','BOOT_CALENDAR'):
        raise ValueError('QUIESCENCE_A151_STOP_PRESTATE_CHANGED')
    # An exact A145 DEGRADED/MARKET_CLOSED parent may acknowledge SIGTERM as
    # STOPPED/MARKET_CLOSED. Admit that transition only after both Docker
    # identities prove the prior A143 clean stop; never as a fresh preflight.
    if proof['flat_book'].get('process_state')=='DEGRADED' and \
            proof['flat_book'].get('session_state')=='MARKET_CLOSED':
        allowed.add(('STOPPED','MARKET_CLOSED'))
    if ((book['process_state'],book['session_state']) not in allowed or
            book['source_sha'] != A143_PENDING_SOURCE_SHA or
            book['real_orders_sent'] != 0 or
            critical_admission() != proof.get('critical_admission')):
        raise ValueError('QUIESCENCE_A143_STOP_BOOK_OR_CRITICAL_CHANGED')
    return book


def a169_prior_a143_stopped_proof(candidate, originals=None, *,
                                  operator_a151_proof=None,
                                  operator_timer_disabled=None,
                                  require_installed_a170_source=False):
    """Admit only A169's exact stopped #751 pair as an unvalidated parent.

    This is an incident-specific read-only continuation.  The former
    candidate failed before image installation; its stable Docker identities
    and original running proof are reused without restarting either process.
    """
    if (not re.fullmatch('[0-9a-f]{40}', candidate) or
            candidate in {A143_PENDING_SOURCE_SHA, A169_PRIOR_CANDIDATE,
                          A170_FAILED_CANDIDATE}):
        raise ValueError('QUIESCENCE_A169_NEW_CANDIDATE_REQUIRED')
    local = datetime.now(ZoneInfo('America/Argentina/Buenos_Aires'))
    if (local.date().isoformat() != '2026-10-28' or
            (local.hour, local.minute) >= (20, 0)):
        raise ValueError('QUIESCENCE_A169_HELD_CONTINUATION_WINDOW_RED')
    prior, digest = _a143_exact_json(
        ROOT/'data/deploy'/('quiescence-'+A169_PRIOR_CANDIDATE+'.json'), 65536)
    admin = prior.get('administrative_reconciliation')
    if (digest != A169_PRIOR_RECEIPT_SHA256 or
            prior.get('candidate_sha') != A169_PRIOR_CANDIDATE or
            prior.get('status') != 'PRIOR_A143_UNVALIDATED_HELD_STOPPED' or
            prior.get('runtime_health') != 'PRIOR_A143_PENDING_NOT_GO' or
            prior.get('installation_started') is not False or
            prior.get('a143_pending_adopted') is not True or
            prior.get('candidate_image_intent') is not None or
            prior.get('candidate_runtime_after_failure') is not None or
            prior.get('real_orders_sent') != 0 or
            prior.get('ppi_watch_accessed') is not False or
            type(admin) is not dict or
            admin.get('schema') != 'rc6.a169.exact-held-stop.v1' or
            admin.get('failure_signature') != 'GZIP_STAGED_MTIME_OUT_OF_RANGE' or
            admin.get('host_attempt') != [A169_PRIOR_CANDIDATE,
                                           A169_PRIOR_RUN, A169_PRIOR_ATTEMPT] or
            admin.get('prior_receipt_sha256') != A169_FAILED_RECEIPT_SHA256):
        raise ValueError('QUIESCENCE_A169_EXACT_PARENT_CHANGED')
    book = a143_stopped_continuation(
        prior, operator_a151_proof=operator_a151_proof,
        operator_timer_disabled=operator_timer_disabled)
    observed = originals if originals is not None else {
        name: inspect(name) for name in OWNED}
    if (set(observed) != set(OWNED) or
            any(observed[name]['Id'] != prior['containers'][name]['id'] or
                observed[name]['Image'] != prior['containers'][name]['image'] or
                observed[name]['State']['Status'] != 'exited' for name in OWNED) or
            book.get('real_orders_sent') != 0 or
            book.get('source_sha') != A143_PENDING_SOURCE_SHA):
        raise ValueError('QUIESCENCE_A169_PARENT_RUNTIME_CHANGED')
    # A170 loaded and tagged its frozen image, but COLD2 failed before mode
    # manager could start either new container. Bind that intervening exact
    # failure, not merely the older A169 stop, before another candidate writes.
    intermediate, intermediate_sha = _a143_exact_json(
        ROOT/'data/deploy'/('quiescence-'+A170_FAILED_CANDIDATE+'.json'),65536)
    intent = intermediate.get('candidate_image_intent')
    if (intermediate_sha != A170_FAILED_RECEIPT_SHA256 or
            intermediate.get('candidate_sha') != A170_FAILED_CANDIDATE or
            intermediate.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED' or
            intermediate.get('installation_started') is not True or
            intermediate.get('a143_pending_adopted') is not True or
            intermediate.get('prior_a143_stopped_after_install') is not True or
            intermediate.get('runtime_health') != 'PRIOR_A143_PENDING_NOT_GO' or
            intermediate.get('candidate_runtime_after_failure') is not None or
            'inflight' in intermediate or
            type(intent) is not dict or
            intent.get('image_id') != A170_LOADED_IMAGE_ID or
            not re.fullmatch('[0-9a-f]{64}',intent.get('manifest_sha256','')) or
            intermediate.get('a169_origin_candidate') != A169_PRIOR_CANDIDATE or
            intermediate.get('a169_origin_sha256') != digest or
            intermediate.get('a143_previous_proof') != prior.get('a143_previous_proof') or
            intermediate.get('containers') != prior.get('containers') or
            intermediate.get('stop_evidence') != prior.get('stop_evidence') or
            intermediate.get('stop_attempted') != prior.get('stop_attempted') or
            intermediate.get('real_orders_sent') != 0 or
            intermediate.get('ppi_watch_accessed') is not False):
        raise ValueError('QUIESCENCE_A170_INTERMEDIATE_FAILURE_CHANGED')
    if require_installed_a170_source:
        installed = ROOT/'scripts/rc6_deploy_quiescence.py'
        if (installed.is_symlink() or not installed.is_file() or
                hashlib.sha256(installed.read_bytes()).hexdigest() !=
                    A170_INSTALLED_QUIESCENCE_SHA256):
            raise ValueError('QUIESCENCE_A170_INSTALLED_SOURCE_CHANGED')
        for tag in ('porota-trading-bot:17.0.0-rc6',
                    'porota-trading-bot:17.0.0-rc6-candidate-'+A170_FAILED_CANDIDATE):
            images = json.loads(command(['docker','image','inspect',tag]))
            if (type(images) is not list or len(images) != 1 or
                    images[0].get('Id') != A170_LOADED_IMAGE_ID):
                raise ValueError('QUIESCENCE_A170_DOCKER_TAG_CHANGED')
    return prior, digest, book


def a171_failed_running_proof(candidate, originals=None, *,
                              operator_a151_proof=None,
                              operator_timer_disabled=None,
                              require_installed_a171_source=False):
    """Prove the exact failed A171 image is still running CLOSED/PAPER/NO_GO.

    The COLD2 archive and six fee restatements succeeded in A171; its preopen
    guardian result did not. This proof never declares that partial run GREEN.
    It binds the cleanup receipt, new Docker IDs, frozen artifact, pending
    marker, disabled operator timer and current flat book before another stop.
    """
    if (not re.fullmatch('[0-9a-f]{40}', candidate) or
            candidate in {A143_PENDING_SOURCE_SHA, A169_PRIOR_CANDIDATE,
                          A170_FAILED_CANDIDATE, A171_FAILED_CANDIDATE}):
        raise ValueError('QUIESCENCE_A171_NEW_CANDIDATE_REQUIRED')
    local = datetime.now(ZoneInfo('America/Argentina/Buenos_Aires'))
    if local.date().isoformat() != '2026-10-28':
        raise ValueError('QUIESCENCE_A171_DATED_HOLD_EXPIRED')
    prior, digest = _a143_exact_json(
        ROOT/'data/deploy'/('quiescence-'+A171_FAILED_CANDIDATE+'.json'),65536)
    intent, runtime = prior.get('candidate_image_intent'), prior.get('candidate_runtime_after_failure')
    held = (prior.get('a143_previous_proof') or {}).get('operator_hold')
    if (digest != A171_FAILED_RECEIPT_SHA256 or
            prior.get('candidate_sha') != A171_FAILED_CANDIDATE or
            prior.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED' or
            prior.get('installation_started') is not True or
            prior.get('a143_pending_adopted') is not True or
            prior.get('a169_origin_candidate') != A169_PRIOR_CANDIDATE or
            prior.get('a169_origin_sha256') != A169_PRIOR_RECEIPT_SHA256 or
            prior.get('a170_origin_candidate') != A170_FAILED_CANDIDATE or
            prior.get('a170_origin_sha256') != A170_FAILED_RECEIPT_SHA256 or
            prior.get('inflight') != 'ABSENT_OWNED_RUNTIME_STOPPED' or
            prior.get('prior_a143_stopped_after_install') is not None or
            prior.get('runtime_health') != 'NEW_A143_CANDIDATE_NOT_GO' or
            prior.get('market_open_suspended') is not False or
            any(prior.get(k) is not False for k in
                ('open_paper_carryover','paused_postclose','intraday_caucion','pending_caucion')) or
            prior.get('spot_carryover') is not None or
            prior.get('caucion_carryover') is not None or
            type(prior.get('real_orders_sent')) is not int or
            prior['real_orders_sent'] != 0 or
            prior.get('ppi_watch_accessed') is not False or
            type(intent) is not dict or type(runtime) is not dict or
            type(held) is not dict or held.get('admission') != A151_HELD_OPEN_MODE or
            held.get('business_day_art') != '2026-10-28' or
            held.get('paper_execution_authorized') is not False or
            runtime.get('status') != 'running' or
            set(runtime.get('containers',{})) != set(OWNED) or
            type(runtime.get('flat_book')) is not dict or
            runtime['flat_book'].get('source_sha') != A171_FAILED_CANDIDATE or
            runtime['flat_book'].get('real_orders_sent') != 0 or
            type(runtime.get('critical_admission')) is not dict or
            runtime['critical_admission'].get('capability') != 'issues_only'):
        raise ValueError('QUIESCENCE_A171_EXACT_FAILURE_CHANGED')
    if operator_a151_proof is None or operator_timer_disabled is None:
        if operator_a151_proof is not None or operator_timer_disabled is not None:
            raise ValueError('QUIESCENCE_A171_OPERATOR_CALLBACK_PAIR_REQUIRED')
        operator = candidate_operator_module(require_staged=True)
        operator_a151_proof = operator.exact_a151_operator_preopen_pause
        operator_timer_disabled = operator.operator_preopen_timer_disabled
    receipt_sha, operation = operator_a151_proof()
    if (receipt_sha != held.get('operator_receipt_sha256') or
            operation != held.get('operator_operation_id') or
            not operator_timer_disabled()):
        raise ValueError('QUIESCENCE_A171_NO_GO_HOLD_CHANGED')
    frozen, frozen_sha = _a143_exact_json(
        ROOT/'data/deploy/porota-frozen-candidate.json',16384)
    marker = current_runtime_marker()
    try:
        encoded = base64.b64decode(intent['frozen_manifest_b64'],validate=True)
        intent_at = datetime.fromisoformat(intent['recorded_at'])
        marker_at = datetime.fromisoformat(marker['recorded_at'])
    except (KeyError,ValueError,TypeError,binascii.Error) as exc:
        raise ValueError('QUIESCENCE_A171_IMAGE_INTENT_INVALID') from exc
    if (len(encoded)>16384 or hashlib.sha256(encoded).hexdigest()!=frozen_sha or
            intent_at.tzinfo is None or marker_at.tzinfo is None or
            not intent_at<=marker_at<=datetime.now(timezone.utc) or
            frozen.get('candidate_sha') != A171_FAILED_CANDIDATE or
            frozen.get('candidate_tree_sha') != A171_CANDIDATE_TREE_SHA or
            not re.fullmatch('[0-9a-f]{64}',frozen.get('image_tar_sha256','')) or
            intent.get('manifest_sha256') != frozen_sha or
            intent.get('candidate_tree_sha') != A171_CANDIDATE_TREE_SHA or
            intent.get('image_tar_sha256') != frozen['image_tar_sha256'] or
            intent.get('image_id') != A171_LOADED_IMAGE_ID or
            marker.get('candidate_sha') != A171_FAILED_CANDIDATE or
            marker.get('deploy_sha') != A171_DEPLOY_SHA or
            marker.get('image_id') != A171_LOADED_IMAGE_ID or
            marker.get('validation_status') != 'DEPLOYED_VALIDATION_PENDING' or
            marker.get('ppi_watch_untouched') is not True):
        raise ValueError('QUIESCENCE_A171_INSTALLED_PROVENANCE_CHANGED')
    observed = originals if originals is not None else {name:inspect(name) for name in OWNED}
    if set(observed) != set(OWNED):
        raise ValueError('QUIESCENCE_A171_RUNTIME_SET_CHANGED')
    for name in OWNED:
        value, row = observed[name], runtime['containers'][name]
        if (value['Id'] != A171_RUNNING_IDS[name] or
                value['Image'] != A171_LOADED_IMAGE_ID or
                value['State']['Status'] != 'running' or
                row.get('id') != value['Id'] or row.get('image') != value['Image'] or
                row.get('lifecycle') != lifecycle(value)):
            raise ValueError('QUIESCENCE_A171_RUNTIME_IDENTITY_CHANGED')
    if (rescue_flat_book() != runtime['flat_book'] or
            runtime['flat_book'].get('process_state') != 'WAITING_MARKET' or
            runtime['flat_book'].get('session_state') not in {'CLOSED','MARKET_CLOSED'} or
            admit() != 'WAITING_MARKET' or
            critical_admission().get('capability') != 'issues_only'):
        raise ValueError('QUIESCENCE_A171_RUNNING_BOOK_CHANGED')
    if require_installed_a171_source:
        installed = ROOT/'scripts/rc6_deploy_quiescence.py'
        if (installed.is_symlink() or not installed.is_file() or
                hashlib.sha256(installed.read_bytes()).hexdigest() !=
                A171_INSTALLED_QUIESCENCE_SHA256):
            raise ValueError('QUIESCENCE_A171_INSTALLED_SOURCE_CHANGED')
        for tag in ('porota-trading-bot:17.0.0-rc6',
                    'porota-trading-bot:17.0.0-rc6-candidate-'+A171_FAILED_CANDIDATE):
            images=json.loads(command(['docker','image','inspect',tag]))
            if type(images) is not list or len(images)!=1 or images[0].get('Id')!=A171_LOADED_IMAGE_ID:
                raise ValueError('QUIESCENCE_A171_DOCKER_TAG_CHANGED')
    return {'origin_candidate':A171_FAILED_CANDIDATE,'origin_sha256':digest,
            'image_id':A171_LOADED_IMAGE_ID,
            'containers':{name:{'id':observed[name]['Id'],'image':observed[name]['Image'],
                                'lifecycle':lifecycle(observed[name])} for name in OWNED},
            'flat_book':runtime['flat_book'],
            'operator_receipt_sha256':receipt_sha,
            'operator_operation_id':operation}


def a171_receipt_origin(receipt):
    """Bind durable A171 stop intent to the pinned failed runtime receipt."""
    if (receipt.get('a171_pending_adopted') is not True or
            receipt.get('a171_origin_candidate') != A171_FAILED_CANDIDATE or
            receipt.get('a171_origin_sha256') != A171_FAILED_RECEIPT_SHA256 or
            type(receipt.get('a171_previous_proof')) is not dict or
            receipt.get('a171_previous_proof',{}).get('origin_sha256') != A171_FAILED_RECEIPT_SHA256 or
            receipt.get('market_open_suspended') is not False or
            receipt.get('inflight') != 'ABSENT_OWNED_RUNTIME_STOPPED' or
            set(receipt.get('containers',{})) != set(OWNED) or
            set(receipt.get('stop_evidence',{})) != set(OWNED) or
            set(receipt.get('stop_attempted',[])) != set(OWNED)):
        raise ValueError('QUIESCENCE_A171_STOPPED_RECEIPT_CHANGED')
    proof=receipt['a171_previous_proof']
    original=proof['containers']
    prior, prior_sha = _a143_exact_json(
        ROOT/'data/deploy'/('quiescence-'+A171_FAILED_CANDIDATE+'.json'),65536)
    if (prior_sha != A171_FAILED_RECEIPT_SHA256 or
            prior.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED' or
            prior.get('candidate_runtime_after_failure',{}).get('status') != 'running' or
            proof.get('origin_candidate') != A171_FAILED_CANDIDATE or
            proof.get('image_id') != A171_LOADED_IMAGE_ID or
            proof.get('flat_book') != prior['candidate_runtime_after_failure'].get('flat_book') or
            proof.get('operator_receipt_sha256') !=
                prior.get('a143_previous_proof',{}).get('operator_hold',{}).get('operator_receipt_sha256') or
            proof.get('operator_operation_id') !=
                prior.get('a143_previous_proof',{}).get('operator_hold',{}).get('operator_operation_id')):
        raise ValueError('QUIESCENCE_A171_ORIGIN_RECEIPT_CHANGED')
    for name in OWNED:
        previous, stopped = original[name], receipt['containers'][name]
        if (previous.get('id') != A171_RUNNING_IDS[name] or
                previous.get('image') != A171_LOADED_IMAGE_ID or
                stopped != {'id':previous['id'],'image':previous['image']} or
                previous != prior['candidate_runtime_after_failure']['containers'][name] or
                receipt['stop_evidence'][name].get('before') != previous.get('lifecycle') or
                receipt['stop_evidence'][name].get('classification') not in
                {'CLEAN_EXIT','INHERITED_OOM_FLAG_CLEAN_EXIT'} or
                receipt['stop_evidence'][name].get('after',{}).get('Status') != 'exited' or
                receipt['stop_evidence'][name].get('after',{}).get('ExitCode') != 0):
            raise ValueError('QUIESCENCE_A171_INHERITED_RUNTIME_CHANGED')
    return prior


def a171_stopped_continuation(receipt):
    """Keep the exact A171 pair stopped after the new candidate has drained."""
    a171_receipt_origin(receipt)
    exact_clean_stopped(receipt)
    book=rescue_flat_book()
    if (book['source_sha'] != A171_FAILED_CANDIDATE or
            book['real_orders_sent'] != 0 or
            (book['process_state'],book['session_state']) not in
            {('STOPPED','MARKET_CLOSED'),('STOPPED','CLOSED'),
             ('WAITING_MARKET','MARKET_CLOSED'),('WAITING_MARKET','CLOSED')} or
            critical_admission().get('capability') != 'issues_only'):
        raise ValueError('QUIESCENCE_A171_STOPPED_BOOK_CHANGED')
    return book


def a174_failed_running_proof(candidate, originals=None, *,
                              operator_a151_proof=None,
                              operator_timer_disabled=None,
                              require_installed_a174_source=False):
    """Prove only the pinned, still-running A174 failure for fix-forward.

    A174's durable failure book was DEGRADED/CLOSED. A later successful login
    moved the live, flat PAPER book to WAITING_MARKET/MARKET_CLOSED. Both facts
    are checked independently; neither grants a trading or validation GO.
    """
    if (not re.fullmatch('[0-9a-f]{40}',candidate) or candidate in
            {A143_PENDING_SOURCE_SHA,A169_PRIOR_CANDIDATE,A170_FAILED_CANDIDATE,
             A171_FAILED_CANDIDATE,A174_FAILED_CANDIDATE}):
        raise ValueError('QUIESCENCE_A174_NEW_CANDIDATE_REQUIRED')
    if datetime.now(ZoneInfo('America/Argentina/Buenos_Aires')).date().isoformat() != '2026-10-28':
        raise ValueError('QUIESCENCE_A174_A151_DATED_HOLD_EXPIRED')
    prior,digest=_a143_exact_json(
        ROOT/'data/deploy'/('quiescence-'+A174_FAILED_CANDIDATE+'.json'),65536)
    intent,runtime=prior.get('candidate_image_intent'),prior.get('candidate_runtime_after_failure')
    previous=prior.get('a171_previous_proof')
    if (digest!=A174_FAILED_RECEIPT_SHA256 or
            prior.get('candidate_sha')!=A174_FAILED_CANDIDATE or
            prior.get('status')!='FIX_FORWARD_VALIDATION_REQUIRED' or
            prior.get('installation_started') is not True or
            prior.get('a171_pending_adopted') is not True or
            prior.get('a171_origin_candidate')!=A171_FAILED_CANDIDATE or
            prior.get('a171_origin_sha256')!=A171_FAILED_RECEIPT_SHA256 or
            prior.get('inflight')!='ABSENT_OWNED_RUNTIME_STOPPED' or
            prior.get('runtime_health')!='NEW_A172_CANDIDATE_NOT_GO' or
            prior.get('market_open_suspended') is not False or
            any(prior.get(k) is not False for k in
                ('open_paper_carryover','paused_postclose','intraday_caucion','pending_caucion')) or
            prior.get('spot_carryover') is not None or
            prior.get('caucion_carryover') is not None or
            type(prior.get('real_orders_sent')) is not int or
            prior['real_orders_sent']!=0 or
            prior.get('ppi_watch_accessed') is not False or
            type(previous) is not dict or
            previous.get('origin_candidate')!=A171_FAILED_CANDIDATE or
            previous.get('origin_sha256')!=A171_FAILED_RECEIPT_SHA256 or
            type(intent) is not dict or type(runtime) is not dict or
            runtime.get('status')!='running' or
            set(runtime.get('containers',{}))!=set(OWNED) or
            type(runtime.get('flat_book')) is not dict or
            runtime['flat_book']!={
                'source_sha':A174_FAILED_CANDIDATE,'real_orders_sent':0,
                'process_state':'DEGRADED','session_state':'CLOSED'} or
            type(runtime.get('critical_admission')) is not dict or
            runtime['critical_admission'].get('capability')!='issues_only'):
        raise ValueError('QUIESCENCE_A174_EXACT_FAILURE_CHANGED')
    if operator_a151_proof is None or operator_timer_disabled is None:
        if operator_a151_proof is not None or operator_timer_disabled is not None:
            raise ValueError('QUIESCENCE_A174_OPERATOR_CALLBACK_PAIR_REQUIRED')
        operator=candidate_operator_module(require_staged=True)
        operator_a151_proof=operator.exact_a151_operator_preopen_pause
        operator_timer_disabled=operator.operator_preopen_timer_disabled
    receipt_sha,operation=operator_a151_proof()
    if (receipt_sha!=previous.get('operator_receipt_sha256') or
            operation!=previous.get('operator_operation_id') or
            not operator_timer_disabled()):
        raise ValueError('QUIESCENCE_A174_NO_GO_HOLD_CHANGED')
    frozen,frozen_sha=_a143_exact_json(
        ROOT/'data/deploy/porota-frozen-candidate.json',16384)
    marker,marker_sha=_a143_exact_json(
        ROOT/'data/deploy/CURRENT_STATE_V2.json',65536)
    if current_runtime_marker()!=marker:
        raise ValueError('QUIESCENCE_A174_MARKER_CHANGED')
    try:
        encoded=base64.b64decode(intent['frozen_manifest_b64'],validate=True)
        intent_at=datetime.fromisoformat(intent['recorded_at'])
        marker_at=datetime.fromisoformat(marker['recorded_at'])
    except (KeyError,ValueError,TypeError,binascii.Error) as exc:
        raise ValueError('QUIESCENCE_A174_IMAGE_INTENT_INVALID') from exc
    if (frozen_sha!=A174_FROZEN_SHA256 or marker_sha!=A174_MARKER_SHA256 or
            len(encoded)>16384 or hashlib.sha256(encoded).hexdigest()!=frozen_sha or
            intent_at.tzinfo is None or marker_at.tzinfo is None or
            not intent_at<=marker_at<=datetime.now(timezone.utc) or
            frozen.get('candidate_sha')!=A174_FAILED_CANDIDATE or
            frozen.get('candidate_tree_sha')!=A174_CANDIDATE_TREE_SHA or
            not re.fullmatch('[0-9a-f]{64}',frozen.get('image_tar_sha256','')) or
            intent.get('manifest_sha256')!=frozen_sha or
            intent.get('candidate_tree_sha')!=A174_CANDIDATE_TREE_SHA or
            intent.get('image_tar_sha256')!=frozen['image_tar_sha256'] or
            intent.get('image_id')!=A174_LOADED_IMAGE_ID or
            marker.get('candidate_sha')!=A174_FAILED_CANDIDATE or
            marker.get('deploy_sha')!=A174_DEPLOY_SHA or
            marker.get('image_id')!=A174_LOADED_IMAGE_ID or
            marker.get('validation_status')!='DEPLOYED_VALIDATION_PENDING' or
            marker.get('ppi_watch_untouched') is not True):
        raise ValueError('QUIESCENCE_A174_INSTALLED_PROVENANCE_CHANGED')
    observed=originals if originals is not None else {name:inspect(name) for name in OWNED}
    if set(observed)!=set(OWNED):
        raise ValueError('QUIESCENCE_A174_RUNTIME_SET_CHANGED')
    for name in OWNED:
        value,row=observed[name],runtime['containers'][name]
        if (value['Id']!=A174_RUNNING_IDS[name] or
                value['Image']!=A174_LOADED_IMAGE_ID or
                value['State']['Status']!='running' or
                row.get('id')!=value['Id'] or row.get('image')!=value['Image'] or
                row.get('lifecycle')!=lifecycle(value)):
            raise ValueError('QUIESCENCE_A174_RUNTIME_IDENTITY_CHANGED')
    book=rescue_flat_book()
    if (book.get('source_sha')!=A174_FAILED_CANDIDATE or
            book.get('real_orders_sent')!=0 or
            (book.get('process_state'),book.get('session_state')) not in
                {('WAITING_MARKET','MARKET_CLOSED'),('WAITING_MARKET','CLOSED')} or
            admit()!=book['process_state'] or
            critical_admission().get('capability')!='issues_only'):
        raise ValueError('QUIESCENCE_A174_RUNNING_BOOK_CHANGED')
    if require_installed_a174_source:
        installed=ROOT/'scripts/rc6_deploy_quiescence.py'
        if (installed.is_symlink() or not installed.is_file() or
                hashlib.sha256(installed.read_bytes()).hexdigest()!=
                A174_INSTALLED_QUIESCENCE_SHA256):
            raise ValueError('QUIESCENCE_A174_INSTALLED_SOURCE_CHANGED')
        for tag in ('porota-trading-bot:17.0.0-rc6',
                    'porota-trading-bot:17.0.0-rc6-candidate-'+A174_FAILED_CANDIDATE):
            images=json.loads(command(['docker','image','inspect',tag]))
            if type(images) is not list or len(images)!=1 or images[0].get('Id')!=A174_LOADED_IMAGE_ID:
                raise ValueError('QUIESCENCE_A174_DOCKER_TAG_CHANGED')
    return {'origin_candidate':A174_FAILED_CANDIDATE,'origin_sha256':digest,
            'image_id':A174_LOADED_IMAGE_ID,
            'containers':{name:{'id':observed[name]['Id'],'image':observed[name]['Image'],
                                'lifecycle':lifecycle(observed[name])} for name in OWNED},
            'flat_book':book,'failure_flat_book':runtime['flat_book'],
            'operator_receipt_sha256':receipt_sha,'operator_operation_id':operation,
            'marker_sha256':marker_sha,'frozen_sha256':frozen_sha}


def a174_receipt_origin(receipt):
    """Bind the new candidate's durable stop to the immutable A174 failure."""
    if (receipt.get('a174_pending_adopted') is not True or
            receipt.get('a174_origin_candidate')!=A174_FAILED_CANDIDATE or
            receipt.get('a174_origin_sha256')!=A174_FAILED_RECEIPT_SHA256 or
            type(receipt.get('a174_previous_proof')) is not dict or
            receipt['a174_previous_proof'].get('origin_sha256')!=A174_FAILED_RECEIPT_SHA256 or
            receipt.get('market_open_suspended') is not False or
            receipt.get('inflight')!='ABSENT_OWNED_RUNTIME_STOPPED' or
            set(receipt.get('containers',{}))!=set(OWNED) or
            set(receipt.get('stop_evidence',{}))!=set(OWNED) or
            set(receipt.get('stop_attempted',[]))!=set(OWNED)):
        raise ValueError('QUIESCENCE_A174_STOPPED_RECEIPT_CHANGED')
    prior,prior_sha=_a143_exact_json(
        ROOT/'data/deploy'/('quiescence-'+A174_FAILED_CANDIDATE+'.json'),65536)
    proof=receipt['a174_previous_proof']
    if (prior_sha!=A174_FAILED_RECEIPT_SHA256 or
            prior.get('status')!='FIX_FORWARD_VALIDATION_REQUIRED' or
            prior.get('candidate_runtime_after_failure',{}).get('status')!='running' or
            proof.get('origin_candidate')!=A174_FAILED_CANDIDATE or
            proof.get('image_id')!=A174_LOADED_IMAGE_ID or
            proof.get('failure_flat_book')!=prior['candidate_runtime_after_failure'].get('flat_book') or
            proof.get('marker_sha256')!=A174_MARKER_SHA256 or
            proof.get('frozen_sha256')!=A174_FROZEN_SHA256 or
            proof.get('operator_receipt_sha256')!=
                prior.get('a171_previous_proof',{}).get('operator_receipt_sha256') or
            proof.get('operator_operation_id')!=
                prior.get('a171_previous_proof',{}).get('operator_operation_id')):
        raise ValueError('QUIESCENCE_A174_ORIGIN_RECEIPT_CHANGED')
    for name in OWNED:
        previous,stopped=proof['containers'][name],receipt['containers'][name]
        if (previous.get('id')!=A174_RUNNING_IDS[name] or
                previous.get('image')!=A174_LOADED_IMAGE_ID or
                stopped!={'id':previous['id'],'image':previous['image']} or
                previous!=prior['candidate_runtime_after_failure']['containers'][name] or
                receipt['stop_evidence'][name].get('before')!=previous.get('lifecycle') or
                receipt['stop_evidence'][name].get('classification') not in
                    {'CLEAN_EXIT','INHERITED_OOM_FLAG_CLEAN_EXIT'} or
                receipt['stop_evidence'][name].get('after',{}).get('Status')!='exited' or
                receipt['stop_evidence'][name].get('after',{}).get('ExitCode')!=0):
            raise ValueError('QUIESCENCE_A174_INHERITED_RUNTIME_CHANGED')
    return prior


def a174_stopped_continuation(receipt):
    """Keep the exact A174 pair stopped after the new candidate drains."""
    a174_receipt_origin(receipt)
    exact_clean_stopped(receipt)
    book=rescue_flat_book()
    if (book.get('source_sha')!=A174_FAILED_CANDIDATE or
            book.get('real_orders_sent')!=0 or
            (book.get('process_state'),book.get('session_state')) not in
                {('STOPPED','MARKET_CLOSED'),('STOPPED','CLOSED'),
                 ('WAITING_MARKET','MARKET_CLOSED'),('WAITING_MARKET','CLOSED')} or
            critical_admission().get('capability')!='issues_only'):
        raise ValueError('QUIESCENCE_A174_STOPPED_BOOK_CHANGED')
    return book


def a175_failed_running_proof(candidate, originals=None, *,
                              operator_a151_proof=None,
                              operator_timer_disabled=None,
                              operator_current_no_go_proof=None,
                              require_installed_a175_source=False):
    """Prove only the pinned, still-running A175 failure for fix-forward.

    The failed-run book and the current live book are separately attested.
    A151 applies only on 28-Oct ART; 29-Oct requires a new exact NO_GO seal.
    """
    if (not re.fullmatch('[0-9a-f]{40}',candidate) or candidate in
            {A143_PENDING_SOURCE_SHA,A169_PRIOR_CANDIDATE,A170_FAILED_CANDIDATE,
             A171_FAILED_CANDIDATE,A174_FAILED_CANDIDATE,A175_FAILED_CANDIDATE}):
        raise ValueError('QUIESCENCE_A175_NEW_CANDIDATE_REQUIRED')
    business_day=datetime.now(ZoneInfo('America/Argentina/Buenos_Aires')).date().isoformat()
    if business_day not in {'2026-10-28','2026-10-29'}:
        raise ValueError('QUIESCENCE_A175_DATED_HOLD_EXPIRED')
    prior,digest=_a143_exact_json(
        ROOT/'data/deploy'/('quiescence-'+A175_FAILED_CANDIDATE+'.json'),65536)
    intent,runtime=prior.get('candidate_image_intent'),prior.get('candidate_runtime_after_failure')
    previous=prior.get('a174_previous_proof')
    if (digest!=A175_FAILED_RECEIPT_SHA256 or
            prior.get('candidate_sha')!=A175_FAILED_CANDIDATE or
            prior.get('status')!='FIX_FORWARD_VALIDATION_REQUIRED' or
            prior.get('installation_started') is not True or
            prior.get('a174_pending_adopted') is not True or
            prior.get('a174_origin_candidate')!=A174_FAILED_CANDIDATE or
            prior.get('a174_origin_sha256')!=A174_FAILED_RECEIPT_SHA256 or
            prior.get('inflight')!='ABSENT_OWNED_RUNTIME_STOPPED' or
            prior.get('runtime_health')!='NEW_A175_CANDIDATE_NOT_GO' or
            prior.get('market_open_suspended') is not False or
            any(prior.get(k) is not False for k in
                ('open_paper_carryover','paused_postclose','intraday_caucion','pending_caucion')) or
            prior.get('spot_carryover') is not None or
            prior.get('caucion_carryover') is not None or
            type(prior.get('real_orders_sent')) is not int or
            prior['real_orders_sent']!=0 or
            prior.get('ppi_watch_accessed') is not False or
            type(previous) is not dict or
            previous.get('origin_candidate')!=A174_FAILED_CANDIDATE or
            previous.get('origin_sha256')!=A174_FAILED_RECEIPT_SHA256 or
            type(intent) is not dict or type(runtime) is not dict or
            runtime.get('status')!='running' or
            set(runtime.get('containers',{}))!=set(OWNED) or
            type(runtime.get('flat_book')) is not dict or
            not isinstance(A175_FAILURE_BOOK_STATE,tuple) or
            runtime['flat_book']!={
                'source_sha':A175_FAILED_CANDIDATE,'real_orders_sent':0,
                'process_state':A175_FAILURE_BOOK_STATE[0],
                'session_state':A175_FAILURE_BOOK_STATE[1]} or
            type(runtime.get('critical_admission')) is not dict or
            runtime['critical_admission'].get('capability')!='issues_only'):
        raise ValueError('QUIESCENCE_A175_EXACT_FAILURE_CHANGED')
    if operator_timer_disabled is None or (
            business_day=='2026-10-28' and operator_a151_proof is None) or (
            business_day=='2026-10-29' and operator_current_no_go_proof is None):
        if (operator_a151_proof is not None or operator_timer_disabled is not None or
                operator_current_no_go_proof is not None):
            raise ValueError('QUIESCENCE_A175_OPERATOR_CALLBACK_SET_REQUIRED')
        operator=candidate_operator_module(require_staged=True)
        operator_a151_proof=operator.exact_a151_operator_preopen_pause
        operator_timer_disabled=operator.operator_preopen_timer_disabled
        operator_current_no_go_proof=operator.exact_a176_operator_no_go_continuation
    if business_day=='2026-10-28':
        receipt_sha,operation=operator_a151_proof()
        hold_valid=(receipt_sha==previous.get('operator_receipt_sha256') and
                    operation==previous.get('operator_operation_id'))
    else:
        current=operator_current_no_go_proof()
        receipt_sha,operation=current.get('receipt_sha256'),current.get('operation_id')
        hold_valid=(current.get('status')=='NO_GO_PENDING_EXPLICIT_REARM' and
            current.get('business_day_art')=='2026-10-29' and
            current.get('paper_execution_authorized') is False and
            current.get('a151_origin_sha256')==previous.get('operator_receipt_sha256') and
            current.get('a175_quiescence_sha256')==A175_FAILED_RECEIPT_SHA256 and
            current.get('a175_scheduler_sha256')==A175_SCHEDULER_RECEIPT_SHA256 and
            receipt_sha==A176_OPERATOR_NO_GO_RECEIPT_SHA256)
    if not hold_valid or not operator_timer_disabled():
        raise ValueError('QUIESCENCE_A175_NO_GO_HOLD_CHANGED')
    frozen,frozen_sha=_a143_exact_json(
        ROOT/'data/deploy/porota-frozen-candidate.json',16384)
    marker,marker_sha=_a143_exact_json(
        ROOT/'data/deploy/CURRENT_STATE_V2.json',65536)
    if current_runtime_marker()!=marker:
        raise ValueError('QUIESCENCE_A175_MARKER_CHANGED')
    try:
        encoded=base64.b64decode(intent['frozen_manifest_b64'],validate=True)
        intent_at=datetime.fromisoformat(intent['recorded_at'])
        marker_at=datetime.fromisoformat(marker['recorded_at'])
    except (KeyError,ValueError,TypeError,binascii.Error) as exc:
        raise ValueError('QUIESCENCE_A175_IMAGE_INTENT_INVALID') from exc
    if (frozen_sha!=A175_FROZEN_SHA256 or marker_sha!=A175_MARKER_SHA256 or
            len(encoded)>16384 or hashlib.sha256(encoded).hexdigest()!=frozen_sha or
            intent_at.tzinfo is None or marker_at.tzinfo is None or
            not intent_at<=marker_at<=datetime.now(timezone.utc) or
            frozen.get('candidate_sha')!=A175_FAILED_CANDIDATE or
            frozen.get('candidate_tree_sha')!=A175_CANDIDATE_TREE_SHA or
            not re.fullmatch('[0-9a-f]{64}',frozen.get('image_tar_sha256','')) or
            intent.get('manifest_sha256')!=frozen_sha or
            intent.get('candidate_tree_sha')!=A175_CANDIDATE_TREE_SHA or
            intent.get('image_tar_sha256')!=frozen['image_tar_sha256'] or
            intent.get('image_id')!=A175_LOADED_IMAGE_ID or
            marker.get('candidate_sha')!=A175_FAILED_CANDIDATE or
            marker.get('deploy_sha')!=A175_DEPLOY_SHA or
            marker.get('image_id')!=A175_LOADED_IMAGE_ID or
            marker.get('validation_status')!='DEPLOYED_VALIDATION_PENDING' or
            marker.get('ppi_watch_untouched') is not True):
        raise ValueError('QUIESCENCE_A175_INSTALLED_PROVENANCE_CHANGED')
    observed=originals if originals is not None else {name:inspect(name) for name in OWNED}
    if set(observed)!=set(OWNED):
        raise ValueError('QUIESCENCE_A175_RUNTIME_SET_CHANGED')
    for name in OWNED:
        value,row=observed[name],runtime['containers'][name]
        if (value['Id']!=A175_RUNNING_IDS[name] or
                value['Image']!=A175_LOADED_IMAGE_ID or
                value['State']['Status']!='running' or
                row.get('id')!=value['Id'] or row.get('image')!=value['Image'] or
                row.get('lifecycle')!=lifecycle(value)):
            raise ValueError('QUIESCENCE_A175_RUNTIME_IDENTITY_CHANGED')
    book=rescue_flat_book()
    if (book.get('source_sha')!=A175_FAILED_CANDIDATE or
            book.get('real_orders_sent')!=0 or
            (book.get('process_state'),book.get('session_state')) not in
                {('WAITING_MARKET','MARKET_CLOSED'),('WAITING_MARKET','CLOSED')} or
            admit()!=book['process_state'] or
            critical_admission().get('capability')!='issues_only'):
        raise ValueError('QUIESCENCE_A175_RUNNING_BOOK_CHANGED')
    if require_installed_a175_source:
        installed=ROOT/'scripts/rc6_deploy_quiescence.py'
        if (installed.is_symlink() or not installed.is_file() or
                hashlib.sha256(installed.read_bytes()).hexdigest()!=
                A175_INSTALLED_QUIESCENCE_SHA256):
            raise ValueError('QUIESCENCE_A175_INSTALLED_SOURCE_CHANGED')
        for tag in ('porota-trading-bot:17.0.0-rc6',
                    'porota-trading-bot:17.0.0-rc6-candidate-'+A175_FAILED_CANDIDATE):
            images=json.loads(command(['docker','image','inspect',tag]))
            if type(images) is not list or len(images)!=1 or images[0].get('Id')!=A175_LOADED_IMAGE_ID:
                raise ValueError('QUIESCENCE_A175_DOCKER_TAG_CHANGED')
    return {'origin_candidate':A175_FAILED_CANDIDATE,'origin_sha256':digest,
            'image_id':A175_LOADED_IMAGE_ID,
            'containers':{name:{'id':observed[name]['Id'],'image':observed[name]['Image'],
                                'lifecycle':lifecycle(observed[name])} for name in OWNED},
            'flat_book':book,'failure_flat_book':runtime['flat_book'],
            'operator_receipt_sha256':receipt_sha,'operator_operation_id':operation,
            'operator_a151_origin_sha256':previous.get('operator_receipt_sha256'),
            'operator_hold_business_day':business_day,
            'marker_sha256':marker_sha,'frozen_sha256':frozen_sha}


def a175_receipt_origin(receipt):
    """Bind the new candidate's durable stop to the immutable A175 failure."""
    if (receipt.get('a175_pending_adopted') is not True or
            receipt.get('a175_origin_candidate')!=A175_FAILED_CANDIDATE or
            receipt.get('a175_origin_sha256')!=A175_FAILED_RECEIPT_SHA256 or
            type(receipt.get('a175_previous_proof')) is not dict or
            receipt['a175_previous_proof'].get('origin_sha256')!=A175_FAILED_RECEIPT_SHA256 or
            receipt.get('market_open_suspended') is not False or
            receipt.get('inflight')!='ABSENT_OWNED_RUNTIME_STOPPED' or
            set(receipt.get('containers',{}))!=set(OWNED) or
            set(receipt.get('stop_evidence',{}))!=set(OWNED) or
            set(receipt.get('stop_attempted',[]))!=set(OWNED)):
        raise ValueError('QUIESCENCE_A175_STOPPED_RECEIPT_CHANGED')
    prior,prior_sha=_a143_exact_json(
        ROOT/'data/deploy'/('quiescence-'+A175_FAILED_CANDIDATE+'.json'),65536)
    proof=receipt['a175_previous_proof']
    if (prior_sha!=A175_FAILED_RECEIPT_SHA256 or
            prior.get('status')!='FIX_FORWARD_VALIDATION_REQUIRED' or
            prior.get('candidate_runtime_after_failure',{}).get('status')!='running' or
            proof.get('origin_candidate')!=A175_FAILED_CANDIDATE or
            proof.get('image_id')!=A175_LOADED_IMAGE_ID or
            proof.get('failure_flat_book')!=prior['candidate_runtime_after_failure'].get('flat_book') or
            proof.get('marker_sha256')!=A175_MARKER_SHA256 or
            proof.get('frozen_sha256')!=A175_FROZEN_SHA256 or
            proof.get('operator_a151_origin_sha256')!=
                prior.get('a174_previous_proof',{}).get('operator_receipt_sha256') or
            proof.get('operator_hold_business_day')!=
                datetime.now(ZoneInfo('America/Argentina/Buenos_Aires')).date().isoformat() or
            proof.get('operator_receipt_sha256')!=
                (A176_OPERATOR_NO_GO_RECEIPT_SHA256 if
                 proof.get('operator_hold_business_day')=='2026-10-29' else
                 prior.get('a174_previous_proof',{}).get('operator_receipt_sha256')) or
            (proof.get('operator_hold_business_day')=='2026-10-28' and
             proof.get('operator_operation_id')!=
                prior.get('a174_previous_proof',{}).get('operator_operation_id'))):
        raise ValueError('QUIESCENCE_A175_ORIGIN_RECEIPT_CHANGED')
    if proof.get('operator_hold_business_day')=='2026-10-29':
        operator=candidate_operator_module()
        current=operator.exact_a176_operator_no_go_continuation()
        if (current.get('receipt_sha256')!=proof.get('operator_receipt_sha256') or
                current.get('operation_id')!=proof.get('operator_operation_id') or
                current.get('paper_execution_authorized') is not False or
                not operator.operator_preopen_timer_disabled()):
            raise ValueError('QUIESCENCE_A175_CURRENT_NO_GO_CHANGED')
    for name in OWNED:
        previous,stopped=proof['containers'][name],receipt['containers'][name]
        if (previous.get('id')!=A175_RUNNING_IDS[name] or
                previous.get('image')!=A175_LOADED_IMAGE_ID or
                stopped!={'id':previous['id'],'image':previous['image']} or
                previous!=prior['candidate_runtime_after_failure']['containers'][name] or
                receipt['stop_evidence'][name].get('before')!=previous.get('lifecycle') or
                receipt['stop_evidence'][name].get('classification') not in
                    {'CLEAN_EXIT','INHERITED_OOM_FLAG_CLEAN_EXIT'} or
                receipt['stop_evidence'][name].get('after',{}).get('Status')!='exited' or
                receipt['stop_evidence'][name].get('after',{}).get('ExitCode')!=0):
            raise ValueError('QUIESCENCE_A175_INHERITED_RUNTIME_CHANGED')
    return prior


def a175_stopped_continuation(receipt):
    """Keep the exact A175 pair stopped after the new candidate drains."""
    a175_receipt_origin(receipt)
    exact_clean_stopped(receipt)
    book=rescue_flat_book()
    if (book.get('source_sha')!=A175_FAILED_CANDIDATE or
            book.get('real_orders_sent')!=0 or
            (book.get('process_state'),book.get('session_state')) not in
                {('STOPPED','MARKET_CLOSED'),('STOPPED','CLOSED'),
                 ('WAITING_MARKET','MARKET_CLOSED'),('WAITING_MARKET','CLOSED')} or
            critical_admission().get('capability')!='issues_only'):
        raise ValueError('QUIESCENCE_A175_STOPPED_BOOK_CHANGED')
    return book


def adopted_rescue_closed_proof():
    """Independent current-day close; the 26 October incident clock is spent."""
    local = datetime.now(ZoneInfo('America/Argentina/Buenos_Aires'))
    if (not '2026-10-26' <= local.date().isoformat() <= '2026-10-30' or
            (local.hour, local.minute) < (20, 0)):
        raise ValueError('QUIESCENCE_ADOPTED_CLOSE_WINDOW_RED')
    path = ROOT/'ak_byma_calendar.py'
    if (path.is_symlink() or not path.is_file() or
            hashlib.sha256(path.read_bytes()).hexdigest() != RESCUE_CALENDAR_SHA256):
        raise ValueError('QUIESCENCE_ADOPTED_CALENDAR_SOURCE_RED')
    calendar = types.ModuleType('rc6_adopted_byma_calendar')
    exec(compile(path.read_bytes(), str(path), 'exec'), calendar.__dict__)
    if (calendar.CALENDARIO_ACTUALIZADO_EL.isoformat() != '2026-10-14' or
            not calendar.es_dia_habil_operativo(local.date())):
        raise ValueError('QUIESCENCE_ADOPTED_CALENDAR_DAY_RED')
    # COM18782 expires with the November replacement. No admission on/after 2 Nov.
    return {'observed_at_utc': local.astimezone(timezone.utc).isoformat(),
            'business_day_art': local.date().isoformat(),
            'calendar_sha256': RESCUE_CALENDAR_SHA256,
            'byma_hours_com18782_sha256': RESCUE_HOURS_SHA256,
            'latest_session_end_art': '19:00'}


def failed_rescue_candidate_matches(folder, originals):
    """Select the special route only for an exact prior rescue image/ID pair."""
    paths = list(folder.glob('quiescence-*.json'))
    if len(paths) > 128:
        raise ValueError('QUIESCENCE_ADOPTED_RECEIPT_INVENTORY_BOUND')
    for path in paths:
        if (path.is_symlink() or not path.is_file() or path.stat().st_size > 65536):
            raise ValueError('QUIESCENCE_ADOPTED_RECEIPT_INVENTORY_UNSAFE')
        row = json.loads(path.read_bytes())
        runtime = row.get('candidate_runtime_after_failure')
        if (row.get('degraded_closed_rescue') is True and
                isinstance(runtime, dict) and runtime.get('status') == 'exited' and
                set(runtime.get('containers', {})) == set(OWNED) and
                all(originals[name]['Id'] == runtime['containers'][name].get('id') and
                    originals[name]['Image'] == runtime['containers'][name].get('image')
                    for name in OWNED)):
            return True
    return False


def adopt_failed_rescue_candidate_stopped(candidate, folder, originals):
    """Read-only exact continuation from two cleanly exited candidate containers."""
    close = adopted_rescue_closed_proof()
    if candidate == RESCUE_SOURCE_SHA or any(
            v['State']['Status'] != 'exited' or not re.fullmatch('[0-9a-f]{64}', v['Id'])
            for v in originals.values()):
        raise ValueError('QUIESCENCE_ADOPTED_STOP_REQUIRED')
    paths = list(folder.glob('quiescence-*.json'))
    if len(paths) > 128:
        raise ValueError('QUIESCENCE_ADOPTED_RECEIPT_INVENTORY_BOUND')
    eligible = []
    for path in paths:
        prior = path.name.removeprefix('quiescence-').removesuffix('.json')
        if (path.is_symlink() or not path.is_file() or path.stat().st_size > 65536 or
                not re.fullmatch('[0-9a-f]{40}', prior)):
            raise ValueError('QUIESCENCE_ADOPTED_RECEIPT_INVENTORY_UNSAFE')
        raw = path.read_bytes()
        row = json.loads(raw)
        if (prior == candidate or row.get('candidate_sha') != prior or
                row.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED' or
                row.get('installation_started') is not True or
                row.get('degraded_closed_rescue') is not True):
            continue
        runtime = row.get('candidate_runtime_after_failure')
        intent = row.get('candidate_image_intent')
        if (not isinstance(runtime, dict) or runtime.get('status') != 'exited' or
                not isinstance(intent, dict) or
                set(runtime.get('containers', {})) != set(OWNED)):
            continue
        if all(originals[name]['Id'] == runtime['containers'][name].get('id') and
               originals[name]['Image'] == runtime['containers'][name].get('image')
               for name in OWNED):
            eligible.append((path, row, raw))
    if len(eligible) != 1:
        raise ValueError('QUIESCENCE_ADOPTED_PRIOR_RECEIPT_AMBIGUOUS_OR_MISSING')
    prior_path, previous, raw = eligible[0]
    exact_rescue_receipt(previous)
    prior_sha = previous['candidate_sha']
    runtime = previous['candidate_runtime_after_failure']
    intent = previous['candidate_image_intent']
    try:
        frozen_raw = base64.b64decode(intent['frozen_manifest_b64'], validate=True)
        frozen = json.loads(frozen_raw)
    except (KeyError, ValueError, TypeError, binascii.Error) as exc:
        raise ValueError('QUIESCENCE_ADOPTED_FROZEN_MANIFEST_RED') from exc
    if (not re.fullmatch('[0-9a-f]{40}', intent.get('candidate_tree_sha', '')) or
            not re.fullmatch('[0-9a-f]{64}', intent.get('manifest_sha256', '')) or
            not re.fullmatch('[0-9a-f]{64}', intent.get('image_tar_sha256', '')) or
            hashlib.sha256(frozen_raw).hexdigest() != intent['manifest_sha256'] or
            frozen.get('candidate_sha') != prior_sha or
            frozen.get('candidate_tree_sha') != intent['candidate_tree_sha'] or
            frozen.get('image_tar_sha256') != intent['image_tar_sha256'] or
            intent.get('image_id') == RESCUE_IMAGE_ID or
            runtime.get('critical_admission') != critical_admission()):
        raise ValueError('QUIESCENCE_ADOPTED_PROVENANCE_RED')
    intent_at = datetime.fromisoformat(intent['recorded_at'])
    observed_at = datetime.fromisoformat(runtime['observed_at'])
    if (intent_at.tzinfo is None or observed_at.tzinfo is None or
            not intent_at <= observed_at <= datetime.now(timezone.utc)):
        raise ValueError('QUIESCENCE_ADOPTED_CLOCK_RED')
    tag = 'porota-trading-bot:17.0.0-rc6-candidate-' + prior_sha
    images = json.loads(command(['docker', 'image', 'inspect', tag]))
    if len(images) != 1 or images[0].get('Id') != intent['image_id']:
        raise ValueError('QUIESCENCE_ADOPTED_IMAGE_TAG_CHANGED')
    for name, value in originals.items():
        prior_proof = runtime['containers'][name]
        state = lifecycle(value)
        finished = datetime.fromisoformat(state['FinishedAt'].replace('Z', '+00:00'))
        if (value['Image'] != intent['image_id'] or
                state != prior_proof.get('lifecycle') or
                type(state['ExitCode']) is not int or state['ExitCode'] != 0 or
                state['OOMKilled'] is not False or finished.tzinfo is None or
                not intent_at <= finished <= datetime.now(timezone.utc)):
            raise ValueError('QUIESCENCE_ADOPTED_CLEAN_STOP_CHANGED')
    book = rescue_flat_book()
    if (book['source_sha'] != prior_sha or
            book != runtime.get('flat_book') or
            (book['process_state'], book['session_state']) not in
            {('DEGRADED', 'BOOT_CALENDAR'), ('STOPPED', 'BOOT_CALENDAR'),
             ('STOPPED', 'CLOSED'), ('STOPPED', 'MARKET_CLOSED')}):
        raise ValueError('QUIESCENCE_ADOPTED_SOURCE_BOOK_CHANGED')
    current = current_runtime_marker()
    if (current['candidate_sha'] != prior_sha or
            current['image_id'] != intent['image_id'] or
            current['validation_status'] != 'DEPLOYED_VALIDATION_PENDING' or
            datetime.fromisoformat(current['recorded_at']) < intent_at):
        raise ValueError('QUIESCENCE_ADOPTED_CURRENT_PROVENANCE_RED')
    return {'candidate_sha': candidate, 'status': 'DRAINED',
            'installation_started': False, 'inflight': 'ABSENT_OWNED_RUNTIME_STOPPED',
            'real_orders_sent': 0, 'ppi_watch_accessed': False,
            'market_open_suspended': False, 'open_paper_carryover': False,
            'paused_postclose': False, 'intraday_caucion': False,
            'pending_caucion': False, 'adopted_failed_rescue': True,
            'adopted_from_candidate': prior_sha,
            'prior_receipt_sha256': hashlib.sha256(raw).hexdigest(),
            'candidate_image_id': intent['image_id'],
            'candidate_tree_sha': intent['candidate_tree_sha'],
            'containers': {name: {'id': v['Id'], 'image': v['Image']}
                           for name, v in originals.items()},
            'stop_attempted': list(OWNED),
            'stop_evidence': {name: {'after': lifecycle(v),
                                     'classification': 'CANDIDATE_CLEAN_EXIT'}
                              for name, v in originals.items()},
            'adopted_book': book, 'critical_admission': runtime['critical_admission'],
            'adopted_close_proof': close}


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
          allow_pending_caucion=False, allow_degraded_closed_rescue=False,
          initial=False, stopped_receipt=None):
    if allow_degraded_closed_rescue:
        if (allow_admin_issue624 or allow_market_open_suspend or
                allow_open_paper_carryover or allow_postclose_pending or
                allow_intraday_caucion or allow_pending_caucion):
            raise ValueError('QUIESCENCE_RESCUE_MODE_INVALID')
        degraded_closed_rescue_proof()
    if allow_pending_caucion and (allow_market_open_suspend or
                                  allow_open_paper_carryover or allow_postclose_pending or
                                  allow_intraday_caucion or allow_admin_issue624):
        raise ValueError('QUIESCENCE_PENDING_MODE_INVALID')
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
        if allow_degraded_closed_rescue:
            exact_a92_marker()
            if stopped:
                prior = (stopped_receipt or {}).get('rescue_pre_state')
                valid = ((prior == ['DEGRADED','BOOT_CALENDAR'] and
                          process in {'DEGRADED','STOPPED'} and session == 'BOOT_CALENDAR') or
                         (prior == ['STOPPED','CLOSED'] and
                          process == 'STOPPED' and session == 'CLOSED'))
                if not stopped_receipt or not valid:
                    raise ValueError('QUIESCENCE_RESCUE_STOP_RECEIPT_RED')
                exact_rescue_receipt(stopped_receipt)
                exact_clean_stopped(stopped_receipt)
            elif (process, session) == ('STOPPED','CLOSED'):
                fresh_a92_child_source()
                scanner_crash_full_proof()
            elif (process, session) != ('DEGRADED', 'BOOT_CALENDAR'):
                raise ValueError('QUIESCENCE_RESCUE_STATE_CHANGED')
            else:
                fresh_a92_child_source()
        elif allow_postclose_pending and (not stopped or
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
                if table == 'paper_cauciones' and allow_pending_caucion:
                    verified_pending_caucion_snapshot()
                    continue
                if table == 'paper_positions' and allow_admin_issue624:
                    admin_issue624_target(c)
                    continue
                raise ValueError('QUIESCENCE_OPEN_POSITION')
        return process


def admit_poststart_flat(pre_mode, candidate=None):
    """Recheck live PAPER book and the exact untouched pending receipt."""
    if pre_mode not in {'OPEN', 'CLOSED', 'CLOSED_CAUCION', RESCUE_MODE,
                        ADOPTED_RESCUE_MODE, A143_PENDING_MODE,
                        A151_HELD_STOPPED_MODE, A171_PENDING_MODE,
                        A174_PENDING_MODE}:
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
    if pre_mode in {RESCUE_MODE, ADOPTED_RESCUE_MODE, A143_PENDING_MODE,
                    A171_PENDING_MODE, A174_PENDING_MODE} and actual != 'CLOSED':
        # Only the explicitly owner-paused 27-Oct A92 rescue can validate a
        # genuinely OPEN PAPER observer after installation.  This is never a
        # trading GO: the A125 no-GO gate and disabled timer must survive.
        if pre_mode != RESCUE_MODE or actual != 'OPEN' or not candidate or not re.fullmatch(
                '[0-9a-f]{40}',candidate):
            raise ValueError('QUIESCENCE_RESCUE_POSTSTART_CLOSED_REQUIRED')
        receipt_path = ROOT/'data/deploy'/('quiescence-'+candidate+'.json')
        if (receipt_path.is_symlink() or not receipt_path.is_file() or
                receipt_path.stat().st_size > 65536):
            raise ValueError('QUIESCENCE_RESCUE_EARLY_RECEIPT_UNSAFE')
        receipt = json.loads(receipt_path.read_bytes())
        exact_rescue_receipt(receipt)
        proof = receipt['rescue_calendar_and_hours']
        if proof.get('operator_paper_early') is not True:
            raise ValueError('QUIESCENCE_RESCUE_POSTSTART_CLOSED_REQUIRED')
        if (receipt.get('candidate_sha') != candidate or
                receipt.get('installation_started') is not True):
            raise ValueError('QUIESCENCE_RESCUE_EARLY_RECEIPT_RED')
        try:
            if operator_paper_no_go_proof() != proof['operator_pause_sha256']:
                raise ValueError('PAUSE_RECEIPT_HASH_CHANGED')
        except (OSError,ValueError) as exc:
            raise ValueError('QUIESCENCE_RESCUE_EARLY_PAUSE_LOST') from exc
    # admit rereads committed mode, integer zero orders, a fresh heartbeat and
    # all three flat ledgers. A phase change between reads fails closed.
    pending = pre_mode == 'CLOSED_CAUCION'
    if pending and actual != 'CLOSED':
        raise ValueError('QUIESCENCE_PENDING_POSTSTART_PHASE_CHANGED')
    if admit(allow_market_open_suspend=actual == 'OPEN',
             allow_pending_caucion=pending) != expected:
        raise ValueError('QUIESCENCE_POSTSTART_BOOK_CHANGED')
    if pre_mode == A151_HELD_STOPPED_MODE:
        if not candidate or not re.fullmatch('[0-9a-f]{40}',candidate):
            raise ValueError('QUIESCENCE_A169_POSTSTART_CANDIDATE_INVALID')
        receipt_path=ROOT/'data/deploy'/('quiescence-'+candidate+'.json')
        receipt,_=_a143_exact_json(receipt_path,65536)
        prior,origin_sha=_a143_exact_json(
            ROOT/'data/deploy'/('quiescence-'+A169_PRIOR_CANDIDATE+'.json'),65536)
        interim, interim_sha = _a143_exact_json(
            ROOT/'data/deploy'/('quiescence-'+A170_FAILED_CANDIDATE+'.json'),65536)
        held=(receipt.get('a143_previous_proof') or {}).get('operator_hold')
        intent=receipt.get('candidate_image_intent')
        if (origin_sha != A169_PRIOR_RECEIPT_SHA256 or
                prior.get('status') != 'PRIOR_A143_UNVALIDATED_HELD_STOPPED' or
                receipt.get('candidate_sha') != candidate or
                receipt.get('status') != 'HOST_INSTALLATION_STARTED' or
                receipt.get('installation_started') is not True or
                receipt.get('a143_pending_adopted') is not True or
                receipt.get('a169_origin_candidate') != A169_PRIOR_CANDIDATE or
                receipt.get('a169_origin_sha256') != origin_sha or
                interim_sha != A170_FAILED_RECEIPT_SHA256 or
                interim.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED' or
                receipt.get('a170_origin_candidate') != A170_FAILED_CANDIDATE or
                receipt.get('a170_origin_sha256') != interim_sha or
                receipt.get('a143_previous_proof') != prior.get('a143_previous_proof') or
                receipt.get('containers') != prior.get('containers') or
                receipt.get('stop_evidence') != prior.get('stop_evidence') or
                receipt.get('stop_attempted') != prior.get('stop_attempted') or
                receipt.get('real_orders_sent') != 0 or
                receipt.get('ppi_watch_accessed') is not False or
                type(held) is not dict or
                held.get('admission') != A151_HELD_OPEN_MODE or
                held.get('paper_execution_authorized') is not False or
                type(intent) is not dict or
                re.fullmatch('sha256:[0-9a-f]{64}',intent.get('image_id','')) is None):
            raise ValueError('QUIESCENCE_A169_POSTSTART_ORIGIN_CHANGED')
        operator=candidate_operator_module()
        proof_sha,operation=operator.exact_a151_operator_preopen_pause()
        if (proof_sha != held.get('operator_receipt_sha256') or
                operation != held.get('operator_operation_id') or
                not operator.operator_preopen_timer_disabled()):
            raise ValueError('QUIESCENCE_A169_POSTSTART_NO_GO_HOLD_CHANGED')
        if actual == 'OPEN':
            local=datetime.now(ZoneInfo('America/Argentina/Buenos_Aires'))
            if local.date().isoformat() != '2026-10-28' or (local.hour,local.minute) >= (20,0):
                raise ValueError('QUIESCENCE_A169_POSTSTART_OPEN_WINDOW_RED')
        running={name:inspect(name) for name in OWNED}
        if (any(running[name]['State']['Status'] != 'running' or
                running[name]['Image'] != intent['image_id'] or
                running[name]['Id'] == receipt['containers'][name]['id']
                for name in OWNED)):
            raise ValueError('QUIESCENCE_A169_POSTSTART_CANDIDATE_IMAGE_RED')
    if pre_mode == A171_PENDING_MODE:
        receipt,_=_a143_exact_json(
            ROOT/'data/deploy'/('quiescence-'+str(candidate)+'.json'),65536)
        intent=receipt.get('candidate_image_intent')
        if (receipt.get('candidate_sha') != candidate or
                receipt.get('status') != 'HOST_INSTALLATION_STARTED' or
                receipt.get('installation_started') is not True or
                type(intent) is not dict or
                not re.fullmatch('sha256:[0-9a-f]{64}',intent.get('image_id',''))):
            raise ValueError('QUIESCENCE_A171_POSTSTART_RECEIPT_CHANGED')
        a171_receipt_origin(receipt)
        running={name:inspect(name) for name in OWNED}
        if any(running[name]['State']['Status'] != 'running' or
               running[name]['Image'] != intent['image_id'] or
               running[name]['Id'] == A171_RUNNING_IDS[name] for name in OWNED):
            raise ValueError('QUIESCENCE_A171_POSTSTART_IMAGE_CHANGED')
    if pre_mode == A174_PENDING_MODE:
        receipt,_=_a143_exact_json(
            ROOT/'data/deploy'/('quiescence-'+str(candidate)+'.json'),65536)
        intent=receipt.get('candidate_image_intent')
        if (receipt.get('candidate_sha')!=candidate or
                receipt.get('status')!='HOST_INSTALLATION_STARTED' or
                receipt.get('installation_started') is not True or
                type(intent) is not dict or
                not re.fullmatch('sha256:[0-9a-f]{64}',intent.get('image_id',''))):
            raise ValueError('QUIESCENCE_A174_POSTSTART_RECEIPT_CHANGED')
        a174_receipt_origin(receipt)
        running={name:inspect(name) for name in OWNED}
        if any(running[name]['State']['Status']!='running' or
               running[name]['Image']!=intent['image_id'] or
               running[name]['Id']==A174_RUNNING_IDS[name] for name in OWNED):
            raise ValueError('QUIESCENCE_A174_POSTSTART_IMAGE_CHANGED')
    if pre_mode == RESCUE_MODE:
        if not candidate or not re.fullmatch('[0-9a-f]{40}', candidate):
            raise ValueError('QUIESCENCE_RESCUE_CANDIDATE_INVALID')
        receipt_path = ROOT / 'data/deploy' / ('quiescence-' + candidate + '.json')
        if (receipt_path.is_symlink() or not receipt_path.is_file() or
                receipt_path.stat().st_size > 65536):
            raise ValueError('QUIESCENCE_RESCUE_RECEIPT_INVALID')
        receipt = json.loads(receipt_path.read_text())
        if (receipt.get('candidate_sha') != candidate or
                receipt.get('degraded_closed_rescue') is not True or
                receipt.get('rescue_pre_state') not in
                (['DEGRADED', 'BOOT_CALENDAR'], ['STOPPED', 'CLOSED']) or
                receipt.get('installation_started') is not True or
                receipt.get('real_orders_sent') != 0 or
                receipt.get('ppi_watch_accessed') is not False):
            raise ValueError('QUIESCENCE_RESCUE_POSTSTART_RECEIPT_CHANGED')
    if pre_mode == ADOPTED_RESCUE_MODE:
        if not candidate or not re.fullmatch('[0-9a-f]{40}', candidate):
            raise ValueError('QUIESCENCE_ADOPTED_POSTSTART_CANDIDATE_RED')
        receipt_path = ROOT/'data/deploy'/('quiescence-' + candidate + '.json')
        if (receipt_path.is_symlink() or not receipt_path.is_file() or
                receipt_path.stat().st_size > 65536):
            raise ValueError('QUIESCENCE_ADOPTED_POSTSTART_RECEIPT_UNSAFE')
        receipt = json.loads(receipt_path.read_bytes())
        if (receipt.get('candidate_sha') != candidate or
                receipt.get('adopted_failed_rescue') is not True or
                receipt.get('installation_started') is not True or
                receipt.get('real_orders_sent') != 0 or
                receipt.get('ppi_watch_accessed') is not False or
                not re.fullmatch('[0-9a-f]{40}',receipt.get('adopted_from_candidate','')) or
                not re.fullmatch('[0-9a-f]{64}',receipt.get('prior_receipt_sha256',''))):
            raise ValueError('QUIESCENCE_ADOPTED_POSTSTART_RECEIPT_CHANGED')
        prior_path = ROOT/'data/deploy'/(
            'quiescence-' + receipt['adopted_from_candidate'] + '.json')
        if (prior_path.is_symlink() or not prior_path.is_file() or
                prior_path.stat().st_size > 65536 or
                hashlib.sha256(prior_path.read_bytes()).hexdigest() !=
                receipt['prior_receipt_sha256']):
            raise ValueError('QUIESCENCE_ADOPTED_POSTSTART_PRIOR_CHANGED')
    if pending:
        if not candidate or not re.fullmatch('[0-9a-f]{40}', candidate):
            raise ValueError('QUIESCENCE_PENDING_CANDIDATE_INVALID')
        receipt_path = ROOT / 'data/deploy' / ('quiescence-' + candidate + '.json')
        if (receipt_path.is_symlink() or not receipt_path.is_file() or
                receipt_path.stat().st_size > 65536):
            raise ValueError('QUIESCENCE_PENDING_RECEIPT_INVALID')
        receipt = json.loads(receipt_path.read_text())
        if (receipt.get('candidate_sha') != candidate or
                receipt.get('pending_caucion') is not True or
                receipt.get('installation_started') is not True or
                receipt.get('real_orders_sent') != 0 or
                receipt.get('ppi_watch_accessed') is not False or
                verified_pending_caucion_snapshot() != receipt.get('caucion_carryover')):
            raise ValueError('QUIESCENCE_PENDING_POSTSTART_LEDGER_CHANGED')
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


def verified_pending_caucion_snapshot():
    """One exact, internally reconciled PAPER caucion; never settle it early."""
    import base64
    import sys
    import types
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from bt_caucion_paper import validate_position
    source_b64 = os.environ.get('RC6_CANDIDATE_CAUCION_AUDIT_B64')
    if source_b64:
        raw_source = base64.b64decode(source_b64, validate=True)
        if not 8000 <= len(raw_source) <= 16000:
            raise ValueError('QUIESCENCE_CANDIDATE_AUDIT_BOUND')
        audit = types.ModuleType('rc6_candidate_cb_caucion_audit')
        exec(compile(raw_source, 'candidate/cb_caucion_audit.py', 'exec'), audit.__dict__)
    else:
        import cb_caucion_audit as audit
        raw_source = Path(audit.__file__).read_bytes()
    audit_sha256 = hashlib.sha256(raw_source).hexdigest()
    path = ROOT / 'data/paper_v17/observer_v17.db'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA busy_timeout=200')
        deadline = time.monotonic() + 2
        c.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        c.execute('BEGIN')
        rows = c.execute("SELECT * FROM paper_cauciones WHERE status='OPEN' LIMIT 2").fetchall()
        if len(rows) != 1:
            raise ValueError('QUIESCENCE_PENDING_EXACT_ONE_REQUIRED')
        p = dict(rows[0])
        opened = datetime.fromisoformat(p['opened_at'])
        maturity = datetime.fromisoformat(p['maturity_at'])
        now = datetime.now(timezone.utc)
        local = opened.astimezone(ZoneInfo('America/Argentina/Buenos_Aires'))
        if (opened.tzinfo is None or maturity.tzinfo is None or not opened <= now < maturity
                or p['source'] != 'PRODUCTION_PAPER' or p['currency'] != 'ARS'
                or p['status'] != 'OPEN' or p['settled_at'] is not None
                or (local.date().isoformat(), local.hour, local.minute) !=
                   ('2026-10-22', 16, 30)
                or p['maturity_at'] != '2026-10-23T17:00:00-03:00'):
            raise ValueError('QUIESCENCE_PENDING_INCIDENT_IDENTITY_CHANGED')
        validate_position(p)
        allocations = c.execute('SELECT * FROM paper_caucion_allocations WHERE paper_id=? LIMIT 2',
                                (p['paper_id'],)).fetchall()
        if len(allocations) != 1:
            raise ValueError('QUIESCENCE_PENDING_ALLOCATION_MISSING')
        _, placement = audit._validated(allocations[0], c)
        if not placement or placement['paper_id'] != p['paper_id'] or placement['status'] != 'OPEN':
            raise ValueError('QUIESCENCE_PENDING_ALLOCATION_CHANGED')
        raw = json.dumps({'position': p, 'allocation': dict(allocations[0])},
                         sort_keys=True, separators=(',', ':')).encode()
        if len(raw) > 65536:
            raise ValueError('QUIESCENCE_PENDING_SNAPSHOT_BOUND')
        return {'open_count': 1, 'sha256': hashlib.sha256(raw).hexdigest(),
                'paper_id_sha256': hashlib.sha256(p['paper_id'].encode()).hexdigest(),
                'maturity_at': p['maturity_at'], 'allocation_reconciled': True,
                'audit_source_sha256': audit_sha256}


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


def adopt_stopped_a92_after_failed_rescue(candidate, folder, originals):
    """A139: adopt only the two old A92 containers stopped by failed A138.

    The previous promotion copied its candidate files and archived COLD, but
    aborted before mode_manager started new containers.  A92 remains the
    installed *Docker/runtime marker* identity.  Never start that broken image
    and never treat its BOOT_CALENDAR as a healthy CLOSED state.
    """
    if candidate == FAILED_A92_RESCUE_CANDIDATE or not re.fullmatch('[0-9a-f]{40}',candidate):
        raise ValueError('QUIESCENCE_A139_NEW_CANDIDATE_REQUIRED')
    prior_path = folder/('quiescence-'+FAILED_A92_RESCUE_CANDIDATE+'.json')
    if (prior_path.is_symlink() or not prior_path.is_file() or
            prior_path.stat().st_size > 65536):
        raise ValueError('QUIESCENCE_A139_PRIOR_RECEIPT_UNSAFE')
    raw = prior_path.read_bytes()
    previous = json.loads(raw)
    if (previous.get('candidate_sha') != FAILED_A92_RESCUE_CANDIDATE or
            previous.get('status') not in {'HOST_INSTALLATION_STARTED',
                                           'FIX_FORWARD_VALIDATION_REQUIRED'} or
            previous.get('installation_started') is not True or
            previous.get('degraded_closed_rescue') is not True or
            previous.get('inflight') != 'ABSENT_OWNED_RUNTIME_STOPPED' or
            previous.get('market_open_suspended') is not False or
            previous.get('open_paper_carryover') is not False or
            previous.get('spot_carryover') is not None or
            previous.get('caucion_carryover') is not None or
            previous.get('pending_caucion') is not False or
            previous.get('ppi_watch_accessed') is not False or
            type(previous.get('real_orders_sent')) is not int or
            previous['real_orders_sent'] != 0 or
            previous.get('candidate_runtime_initial') is not None or
            previous.get('candidate_runtime_after_failure') is not None or
            not isinstance(previous.get('candidate_image_intent'), dict)):
        raise ValueError('QUIESCENCE_A139_PRIOR_PHASE_RED')
    exact_rescue_receipt(previous)
    exact_clean_stopped(previous)
    for name in OWNED:
        now = originals[name]
        if (now['Id'] != previous['containers'][name]['id'] or
                now['Image'] != RESCUE_IMAGE_ID or
                now['State']['Status'] != 'exited' or
                lifecycle(now) != previous['stop_evidence'][name]['after']):
            raise ValueError('QUIESCENCE_A139_OLD_A92_IDENTITY_CHANGED')
    marker = exact_a92_marker()
    if marker.get('deploy_sha') not in (None,'2bd9dbf576b253b8f83aab159c784d7fa7d06d89'):
        raise ValueError('QUIESCENCE_A139_OLD_DEPLOY_CHANGED')
    book = rescue_flat_book()
    if ((book['process_state'],book['session_state']) not in
            {('STOPPED','BOOT_CALENDAR'),('STOPPED','CLOSED')} or
            book['source_sha'] != RESCUE_SOURCE_SHA or
            admit(stopped=True, allow_degraded_closed_rescue=True,
                  stopped_receipt=previous) != 'STOPPED'):
        raise ValueError('QUIESCENCE_A139_STOPPED_BOOK_CHANGED')
    critical = critical_admission()
    if critical != previous.get('critical_admission'):
        raise ValueError('QUIESCENCE_A139_CRITICAL_CHANGED')
    proof = degraded_closed_rescue_proof()
    return {'candidate_sha':candidate, 'status':'DRAINED',
            'installation_started':False, 'inflight':'ABSENT_OWNED_RUNTIME_STOPPED',
            'stop_attempted':list(OWNED),
            'stop_evidence':previous['stop_evidence'],
            'containers':previous['containers'],
            'real_orders_sent':0, 'ppi_watch_accessed':False,
            'admin_cancel_paper_id':None, 'market_open_suspended':False,
            'open_paper_carryover':False, 'paused_postclose':False,
            'intraday_caucion':False, 'pending_caucion':False,
            'spot_carryover':None, 'caucion_carryover':None,
            'degraded_closed_rescue':True,
            'rescue_pre_state':previous['rescue_pre_state'],
            'rescue_child_crash_proof':previous.get('rescue_child_crash_proof'),
            'rescue_source_sha':RESCUE_SOURCE_SHA,
            'rescue_image_id':RESCUE_IMAGE_ID,
            'rescue_calendar_and_hours':proof,
            'critical_admission':critical,
            'adopted_stopped_a92_rescue':True,
            'adopted_from_candidate':FAILED_A92_RESCUE_CANDIDATE,
            'prior_receipt_sha256':hashlib.sha256(raw).hexdigest()}


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
                                       allow_intraday_caucion=False,
                                       allow_pending_caucion=False):
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
    if allow_pending_caucion and (previous.get('pending_caucion') is not True or
                                  previous.get('market_open_suspended') is not False or
                                  previous.get('open_paper_carryover') is not False):
        raise ValueError('QUIESCENCE_PRIOR_PENDING_CAUCION_PROOF_MISSING')
    process = admit(stopped=True, allow_admin_issue624=bool(admin_paper_id),
                    allow_market_open_suspend=allow_market_open_suspend,
                    allow_open_paper_carryover=allow_open_paper_carryover,
                    allow_postclose_pending=allow_postclose_pending,
                    allow_intraday_caucion=allow_intraday_caucion,
                    allow_pending_caucion=allow_pending_caucion,
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
    if allow_pending_caucion:
        caucion = verified_pending_caucion_snapshot()
    if (allow_postclose_pending or allow_intraday_caucion) and previous.get('caucion_carryover') != caucion:
        raise ValueError('QUIESCENCE_PRIOR_CAUCION_CHANGED')
    if allow_pending_caucion and previous.get('caucion_carryover') != caucion:
        raise ValueError('QUIESCENCE_PRIOR_PENDING_CAUCION_CHANGED')
    return {'candidate_sha': candidate,
            'spot_carryover': carryover,
            'open_paper_carryover': allow_open_paper_carryover,
            'paused_postclose': allow_postclose_pending,
            'intraday_caucion': allow_intraday_caucion,
            'pending_caucion': allow_pending_caucion,
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
        allow_postclose_pending=False, allow_intraday_caucion=False,
         allow_pending_caucion=False, allow_degraded_closed_rescue=False,
         allow_adopt_failed_rescue=False, allow_a143_pending_running=False,
         allow_a143_pending_stopped=False,
         allow_a151_held_open=False,
         allow_a171_running_pending=False,
         allow_a174_running_pending=False,
         allow_a175_running_pending=False,
         candidate_image_id=None,
         frozen_manifest=None):
    if allow_a175_running_pending and (
            allow_a174_running_pending or allow_a171_running_pending or
            allow_admin_issue624 or allow_market_open_suspend or
            allow_open_paper_carryover or allow_postclose_pending or
            allow_intraday_caucion or allow_pending_caucion or
            allow_degraded_closed_rescue or allow_adopt_failed_rescue or
            allow_a143_pending_running or allow_a143_pending_stopped or
            allow_a151_held_open or action not in {'drain','install'} or
            candidate == A175_FAILED_CANDIDATE):
        raise ValueError('QUIESCENCE_A175_PENDING_MODE_INVALID')
    if allow_a174_running_pending and (
            allow_a175_running_pending or allow_a171_running_pending or allow_admin_issue624 or
            allow_market_open_suspend or allow_open_paper_carryover or
            allow_postclose_pending or allow_intraday_caucion or
            allow_pending_caucion or allow_degraded_closed_rescue or
            allow_adopt_failed_rescue or allow_a143_pending_running or
            allow_a143_pending_stopped or allow_a151_held_open or
            action not in {'drain','install'} or
            candidate==A174_FAILED_CANDIDATE):
        raise ValueError('QUIESCENCE_A174_PENDING_MODE_INVALID')
    if allow_a171_running_pending and (
            allow_admin_issue624 or allow_market_open_suspend or
            allow_open_paper_carryover or allow_postclose_pending or
            allow_intraday_caucion or allow_pending_caucion or
            allow_degraded_closed_rescue or allow_adopt_failed_rescue or
            allow_a143_pending_running or allow_a143_pending_stopped or
            allow_a151_held_open or action not in {'drain','install'} or
            candidate == A171_FAILED_CANDIDATE):
        raise ValueError('QUIESCENCE_A171_PENDING_MODE_INVALID')
    if (allow_a143_pending_running or allow_a143_pending_stopped) and (
            allow_admin_issue624 or allow_market_open_suspend or
            allow_open_paper_carryover or allow_postclose_pending or
            allow_intraday_caucion or allow_pending_caucion or
            allow_degraded_closed_rescue or allow_adopt_failed_rescue or
            action not in {'drain','install'} or
            candidate == A143_PENDING_SOURCE_SHA or
            allow_a143_pending_running == allow_a143_pending_stopped):
        raise ValueError('QUIESCENCE_A143_PENDING_MODE_INVALID')
    if allow_a151_held_open and not (
            allow_a143_pending_running or allow_a143_pending_stopped):
        raise ValueError('QUIESCENCE_A151_HELD_OPEN_MODE_INVALID')
    if allow_adopt_failed_rescue and (allow_admin_issue624 or allow_market_open_suspend or
            allow_open_paper_carryover or allow_postclose_pending or
            allow_intraday_caucion or allow_pending_caucion or
            allow_degraded_closed_rescue or action not in {'drain','install'}):
        raise ValueError('QUIESCENCE_ADOPTED_MODE_INVALID')
    if allow_degraded_closed_rescue and (allow_admin_issue624 or allow_market_open_suspend or
            allow_open_paper_carryover or allow_postclose_pending or
            allow_intraday_caucion or allow_pending_caucion or action not in {'drain', 'install'}):
        raise ValueError('QUIESCENCE_RESCUE_MODE_INVALID')
    if allow_pending_caucion and (allow_admin_issue624 or allow_market_open_suspend or
                                  allow_open_paper_carryover or allow_postclose_pending or
                                  allow_intraday_caucion or action not in {'drain', 'install'}):
        raise ValueError('QUIESCENCE_PENDING_MODE_INVALID')
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
        if allow_a143_pending_running or allow_a143_pending_stopped:
            if action == 'drain' and receipt is None:
                originals = {name:inspect(name) for name in OWNED}
                if allow_a143_pending_stopped:
                    prior_receipt, origin_sha, _ = a169_prior_a143_stopped_proof(
                        candidate, originals,
                        require_installed_a170_source=True)
                    prior = prior_receipt['a143_previous_proof']
                else:
                    prior = a143_pending_running_proof(
                        originals, allow_a151_held_open=allow_a151_held_open)
                if allow_a151_held_open != (prior.get('operator_hold') is not None):
                    raise ValueError('QUIESCENCE_A151_HELD_OPEN_PHASE_CHANGED')
                receipt = {'candidate_sha':candidate,
                           'status':'DRAINED' if allow_a143_pending_stopped else 'DRAINING',
                           'installation_started':False,
                           'a143_pending_adopted':True,
                           'a143_previous_proof':prior,
                           'containers':{name:{'id':originals[name]['Id'],
                                               'image':originals[name]['Image']}
                                         for name in OWNED},
                           'stop_attempted':(list(prior_receipt['stop_attempted'])
                                             if allow_a143_pending_stopped else []),
                           'stop_evidence':(prior_receipt['stop_evidence']
                                            if allow_a143_pending_stopped else {}),
                           'real_orders_sent':0,'ppi_watch_accessed':False,
                           'market_open_suspended':False,
                           'open_paper_carryover':False,
                           'paused_postclose':False,'intraday_caucion':False,
                           'pending_caucion':False,
                           'spot_carryover':None,'caucion_carryover':None,
                           'recorded_at':datetime.now(timezone.utc).isoformat()}
                if allow_a143_pending_stopped:
                    receipt.update(a169_origin_candidate=A169_PRIOR_CANDIDATE,
                                   a169_origin_sha256=origin_sha,
                                   a170_origin_candidate=A170_FAILED_CANDIDATE,
                                   a170_origin_sha256=A170_FAILED_RECEIPT_SHA256)
                    a143_stopped_continuation(receipt)
                    # The inherited pair is proven cleanly exited only after
                    # the exact A143 stop, PAPER book and hold are rechecked.
                    receipt['inflight'] = 'ABSENT_OWNED_RUNTIME_STOPPED'
                    persist(path,receipt)  # No Docker mutation; exact inherited stop.
                else:
                    persist(path,receipt)  # Durable intent precedes Docker stop.
                    for name in OWNED:
                        receipt['stop_attempted'].append(name)
                        persist(path,receipt)
                        command(['docker','stop','--time','45',name])
                        stopped = inspect(name)
                        receipt['stop_evidence'][name] = {
                            'before':lifecycle(originals[name]),
                            'after':lifecycle(stopped)}
                        persist(path,receipt)
                        receipt['stop_evidence'][name]['classification'] = clean_stop(
                            originals[name],stopped)
                        persist(path,receipt)
                    a143_stopped_continuation(receipt)
                    receipt.update(status='DRAINED',
                                   inflight='ABSENT_OWNED_RUNTIME_STOPPED',
                                   recorded_at=datetime.now(timezone.utc).isoformat())
                    persist(path,receipt)
            else:
                if (not receipt or receipt.get('candidate_sha') != candidate or
                        receipt.get('status') != 'DRAINED' or
                        receipt.get('a143_pending_adopted') is not True or
                        receipt.get('installation_started') is not False or
                        receipt.get('inflight') != 'ABSENT_OWNED_RUNTIME_STOPPED'):
                    raise ValueError('QUIESCENCE_A143_DRAIN_RECEIPT_CHANGED')
                prior = receipt.get('a143_previous_proof')
                if (type(prior) is not dict or
                        allow_a151_held_open != (prior.get('operator_hold') is not None)):
                    raise ValueError('QUIESCENCE_A151_HELD_OPEN_RECEIPT_MODE_CHANGED')
                if allow_a143_pending_stopped:
                    parent, digest, _ = a169_prior_a143_stopped_proof(
                        candidate, require_installed_a170_source=True)
                    if (receipt.get('a169_origin_candidate') != A169_PRIOR_CANDIDATE or
                            receipt.get('a169_origin_sha256') != digest or
                            receipt.get('a170_origin_candidate') != A170_FAILED_CANDIDATE or
                            receipt.get('a170_origin_sha256') != A170_FAILED_RECEIPT_SHA256 or
                            receipt.get('a143_previous_proof') != parent['a143_previous_proof'] or
                            receipt.get('containers') != parent['containers'] or
                            receipt.get('stop_evidence') != parent['stop_evidence'] or
                            receipt.get('stop_attempted') != parent['stop_attempted']):
                        raise ValueError('QUIESCENCE_A169_INHERITED_STOP_CHANGED')
                elif receipt.get('a169_origin_sha256') is not None:
                    raise ValueError('QUIESCENCE_A169_MODE_CHANGED')
                a143_stopped_continuation(receipt)
                if action == 'install':
                    receipt.update(status='HOST_INSTALLATION_STARTED',
                                   installation_started=True,
                                   recorded_at=datetime.now(timezone.utc).isoformat())
                    persist(path,receipt)
            print(json.dumps({'status':receipt['status'],
                              'candidate_sha':candidate,
                              'a143_pending_adopted':True,
                              'real_orders_sent':0,
                              'ppi_watch_accessed':False}),flush=True)
            return receipt
        if allow_adopt_failed_rescue:
            originals = {name: inspect(name) for name in OWNED}
            expected = adopt_failed_rescue_candidate_stopped(candidate, folder, originals)
            if action == 'drain' and receipt is None:
                receipt = expected
            else:
                if (not receipt or receipt.get('candidate_sha') != candidate or
                        receipt.get('status') != 'DRAINED' or
                        receipt.get('installation_started') is not False or
                        receipt.get('adopted_failed_rescue') is not True or
                        any(receipt.get(key) != expected.get(key) for key in
                            ('adopted_from_candidate','prior_receipt_sha256',
                             'candidate_image_id','candidate_tree_sha',
                             'containers','stop_evidence','adopted_book',
                             'critical_admission')) or
                        receipt.get('adopted_close_proof',{}).get('business_day_art') !=
                        expected['adopted_close_proof']['business_day_art']):
                    raise ValueError('QUIESCENCE_ADOPTED_RECEIPT_CHANGED')
                if action == 'install':
                    receipt.update(status='HOST_INSTALLATION_STARTED',
                                   installation_started=True)
            receipt['recorded_at'] = datetime.now(timezone.utc).isoformat()
            persist(path, receipt)
            print(json.dumps({'status': receipt['status'], 'candidate_sha':candidate,
                              'adopted_failed_rescue': True, 'real_orders_sent': 0,
                              'ppi_watch_accessed': False}), flush=True)
            return receipt
        if action == 'drain':
            if receipt is not None:
                if (receipt.get('candidate_sha') != candidate or receipt.get('status') != 'DRAINED' or
                        receipt.get('installation_started') is not False or
                        receipt.get('degraded_closed_rescue', False) != allow_degraded_closed_rescue or
                        receipt.get('market_open_suspended') != allow_market_open_suspend or
                        receipt.get('open_paper_carryover') != allow_open_paper_carryover or
                        receipt.get('paused_postclose', False) != allow_postclose_pending or
                        receipt.get('intraday_caucion', False) != allow_intraday_caucion):
                    raise ValueError('QUIESCENCE_EXISTING_RECEIPT')
                if receipt.get('pending_caucion', False) != allow_pending_caucion:
                    raise ValueError('QUIESCENCE_EXISTING_PENDING_MODE_CHANGED')
                if allow_a171_running_pending != (receipt.get('a171_pending_adopted') is True):
                    raise ValueError('QUIESCENCE_A171_EXISTING_MODE_CHANGED')
                if allow_a174_running_pending != (receipt.get('a174_pending_adopted') is True):
                    raise ValueError('QUIESCENCE_A174_EXISTING_MODE_CHANGED')
                if allow_a175_running_pending != (receipt.get('a175_pending_adopted') is True):
                    raise ValueError('QUIESCENCE_A175_EXISTING_MODE_CHANGED')
                if allow_a171_running_pending:
                    a171_stopped_continuation(receipt)
                if allow_a174_running_pending:
                    a174_stopped_continuation(receipt)
                if allow_a175_running_pending:
                    a175_stopped_continuation(receipt)
                exact_clean_stopped(receipt)
                admit(stopped=True, allow_market_open_suspend=allow_market_open_suspend,
                      allow_open_paper_carryover=allow_open_paper_carryover,
                      allow_postclose_pending=allow_postclose_pending,
                      allow_intraday_caucion=allow_intraday_caucion,
                      allow_pending_caucion=allow_pending_caucion,
                      allow_degraded_closed_rescue=allow_degraded_closed_rescue,
                      stopped_receipt=receipt)
                if allow_open_paper_carryover and spot_carryover_snapshot() != receipt.get('spot_carryover'):
                    raise ValueError('QUIESCENCE_PAUSED_LEDGER_CHANGED')
                if allow_postclose_pending and pending_caucion_snapshot() != receipt.get('caucion_carryover'):
                    raise ValueError('QUIESCENCE_PAUSED_CAUCION_CHANGED')
                if allow_intraday_caucion and pending_caucion_snapshot() != receipt.get('caucion_carryover'):
                    raise ValueError('QUIESCENCE_INTRADAY_CAUCION_CHANGED')
                if allow_pending_caucion and verified_pending_caucion_snapshot() != receipt.get('caucion_carryover'):
                    raise ValueError('QUIESCENCE_PENDING_CAUCION_CHANGED')
                print(json.dumps({'status': 'DRAINED', 'candidate_sha': candidate,
                                  'prepaused_runtime_adopted': True, 'real_orders_sent': 0,
                                  'ppi_watch_accessed': False}), flush=True)
                return receipt
            originals = {name: inspect(name) for name in OWNED}
            a171_proof = None
            a174_proof = None
            a175_proof = None
            if allow_a171_running_pending:
                operator=candidate_operator_module(require_staged=True)
                a171_proof=a171_failed_running_proof(
                    candidate,originals,
                    operator_a151_proof=operator.exact_a151_operator_preopen_pause,
                    operator_timer_disabled=operator.operator_preopen_timer_disabled,
                    require_installed_a171_source=True)
            if allow_a174_running_pending:
                operator=candidate_operator_module(require_staged=True)
                a174_proof=a174_failed_running_proof(
                    candidate,originals,
                    operator_a151_proof=operator.exact_a151_operator_preopen_pause,
                    operator_timer_disabled=operator.operator_preopen_timer_disabled,
                    require_installed_a174_source=True)
            if allow_a175_running_pending:
                operator=candidate_operator_module(require_staged=True)
                a175_proof=a175_failed_running_proof(
                    candidate,originals,
                    operator_a151_proof=operator.exact_a151_operator_preopen_pause,
                    operator_timer_disabled=operator.operator_preopen_timer_disabled,
                    operator_current_no_go_proof=operator.exact_a176_operator_no_go_continuation,
                    require_installed_a175_source=True)
            stopped_owned = all(v['State']['Status'] == 'exited' for v in originals.values())
            if allow_degraded_closed_rescue and not stopped_owned and any(
                    not re.fullmatch('[0-9a-f]{64}',v['Id']) or
                                           v['Image'] != RESCUE_IMAGE_ID or
                                           v['State']['Status'] != 'running'
                                           for v in originals.values()):
                raise ValueError('QUIESCENCE_RESCUE_ORIGINAL_RUNTIME_CHANGED')
            if allow_degraded_closed_rescue and stopped_owned:
                receipt=adopt_stopped_a92_after_failed_rescue(candidate,folder,originals)
                persist(path,receipt)
                print(json.dumps({'status':'DRAINED','candidate_sha':candidate,
                                  'adopted_stopped_a92_rescue':True,
                                  'real_orders_sent':0,'ppi_watch_accessed':False}),flush=True)
                return receipt
            # After an installed OPEN failure SQLite may still say RUNNING,
            # including a completely flat book. Select the exact previous
            # stop receipt before considering that stale live heartbeat.
            if stopped_owned and allow_market_open_suspend:
                admin_paper_id = None
                process = None
            else:
                process = admit(stopped=not (allow_postclose_pending or allow_degraded_closed_rescue),
                                allow_admin_issue624=allow_admin_issue624,
                                allow_market_open_suspend=allow_market_open_suspend,
                                allow_open_paper_carryover=allow_open_paper_carryover,
                                allow_postclose_pending=allow_postclose_pending,
                                allow_intraday_caucion=allow_intraday_caucion,
                                allow_pending_caucion=allow_pending_caucion,
                                allow_degraded_closed_rescue=allow_degraded_closed_rescue, initial=True)
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
                    allow_intraday_caucion=allow_intraday_caucion,
                    allow_pending_caucion=allow_pending_caucion)
                persist(path, receipt)
            else:
                required_process = (process if allow_degraded_closed_rescue else
                                    'WAITING_MARKET' if allow_postclose_pending else
                                    'RUNNING' if allow_market_open_suspend else 'WAITING_MARKET')
                if process != required_process or any(v['State']['Status'] != 'running' for v in originals.values()):
                    raise ValueError('QUIESCENCE_RUNTIME_NOT_RUNNING_OR_EXACT_PRIOR_STOP')
                confirmed_process = admit(allow_admin_issue624=bool(admin_paper_id),
                      allow_market_open_suspend=allow_market_open_suspend,
                      allow_open_paper_carryover=allow_open_paper_carryover,
                      allow_postclose_pending=allow_postclose_pending,
                      allow_intraday_caucion=allow_intraday_caucion,
                      allow_pending_caucion=allow_pending_caucion,
                      allow_degraded_closed_rescue=allow_degraded_closed_rescue)
                if allow_degraded_closed_rescue and confirmed_process != process:
                    raise ValueError('QUIESCENCE_RESCUE_PHASE_CHANGED_BEFORE_STOP')
                rescue_proof = degraded_closed_rescue_proof() if allow_degraded_closed_rescue else None
                child_proof = (scanner_crash_full_proof() if
                               allow_degraded_closed_rescue and process == 'STOPPED' else None)
                critical = critical_admission() if allow_degraded_closed_rescue else None
                caucion_before = (verified_pending_caucion_snapshot() if allow_pending_caucion
                                  else pending_caucion_snapshot() if allow_intraday_caucion else None)
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
                    'pending_caucion': allow_pending_caucion,
                    'degraded_closed_rescue': allow_degraded_closed_rescue,
                    'rescue_pre_state': ([process, 'CLOSED' if process == 'STOPPED' else 'BOOT_CALENDAR']
                                         if allow_degraded_closed_rescue else None),
                    'rescue_child_crash_proof': child_proof,
                    'rescue_source_sha': RESCUE_SOURCE_SHA if allow_degraded_closed_rescue else None,
                    'rescue_image_id': RESCUE_IMAGE_ID if allow_degraded_closed_rescue else None,
                    'rescue_calendar_and_hours': rescue_proof,
                    'critical_admission': critical,
                    'postclose_live_closed': allow_postclose_pending}
                if a171_proof is not None:
                    receipt.update(a171_pending_adopted=True,
                                   a171_origin_candidate=A171_FAILED_CANDIDATE,
                                   a171_origin_sha256=A171_FAILED_RECEIPT_SHA256,
                                   a171_previous_proof=a171_proof)
                if a174_proof is not None:
                    receipt.update(a174_pending_adopted=True,
                                   a174_origin_candidate=A174_FAILED_CANDIDATE,
                                   a174_origin_sha256=A174_FAILED_RECEIPT_SHA256,
                                   a174_previous_proof=a174_proof)
                if a175_proof is not None:
                    receipt.update(a175_pending_adopted=True,
                                   a175_origin_candidate=A175_FAILED_CANDIDATE,
                                   a175_origin_sha256=A175_FAILED_RECEIPT_SHA256,
                                   a175_previous_proof=a175_proof)
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
                      allow_pending_caucion=allow_pending_caucion,
                      allow_degraded_closed_rescue=allow_degraded_closed_rescue,
                      stopped_receipt=receipt)
                if allow_degraded_closed_rescue:
                    if critical_admission() != critical:
                        raise ValueError('QUIESCENCE_RESCUE_CRITICAL_CHANGED_AFTER_STOP')
                if allow_intraday_caucion and pending_caucion_snapshot() != caucion_before:
                    raise ValueError('QUIESCENCE_INTRADAY_CAUCION_CHANGED_AFTER_STOP')
                if allow_pending_caucion and verified_pending_caucion_snapshot() != caucion_before:
                    raise ValueError('QUIESCENCE_PENDING_CAUCION_CHANGED_AFTER_STOP')
                if admin_paper_id and admin_target_on_host() != admin_paper_id:
                    raise ValueError('QUIESCENCE_ADMIN_TARGET_CHANGED_AFTER_STOP')
                receipt.update(status='ADMIN_DRAINED_PENDING' if admin_paper_id else 'DRAINED',
                               inflight='ABSENT_OWNED_RUNTIME_STOPPED',
                               spot_carryover=spot_carryover_snapshot() if allow_open_paper_carryover else None,
                               caucion_carryover=(verified_pending_caucion_snapshot() if allow_pending_caucion
                                   else pending_caucion_snapshot() if (
                                   allow_postclose_pending or allow_intraday_caucion) else None))
                if allow_a171_running_pending:
                    a171_stopped_continuation(receipt)
                if allow_a174_running_pending:
                    a174_stopped_continuation(receipt)
                if allow_a175_running_pending:
                    a175_stopped_continuation(receipt)
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
            if allow_a171_running_pending != (receipt.get('a171_pending_adopted') is True):
                raise ValueError('QUIESCENCE_A171_INSTALL_MODE_CHANGED')
            if allow_a174_running_pending != (receipt.get('a174_pending_adopted') is True):
                raise ValueError('QUIESCENCE_A174_INSTALL_MODE_CHANGED')
            if allow_a175_running_pending != (receipt.get('a175_pending_adopted') is True):
                raise ValueError('QUIESCENCE_A175_INSTALL_MODE_CHANGED')
            if allow_a171_running_pending:
                a171_stopped_continuation(receipt)
            if allow_a174_running_pending:
                a174_stopped_continuation(receipt)
            if allow_a175_running_pending:
                a175_stopped_continuation(receipt)
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
            if bool(receipt.get('pending_caucion', False)) != allow_pending_caucion:
                raise ValueError('QUIESCENCE_INSTALL_PENDING_CAUCION_MODE_CHANGED')
            if bool(receipt.get('degraded_closed_rescue', False)) != allow_degraded_closed_rescue:
                raise ValueError('QUIESCENCE_INSTALL_RESCUE_MODE_CHANGED')
            if allow_degraded_closed_rescue:
                exact_rescue_receipt(receipt)
                if critical_admission() != receipt.get('critical_admission'):
                    raise ValueError('QUIESCENCE_INSTALL_RESCUE_PROOF_RED')
            admit(stopped=True, allow_market_open_suspend=allow_market_open_suspend,
                  allow_open_paper_carryover=allow_open_paper_carryover,
                  allow_postclose_pending=allow_postclose_pending,
                  allow_intraday_caucion=allow_intraday_caucion,
                  allow_pending_caucion=allow_pending_caucion,
                  allow_degraded_closed_rescue=allow_degraded_closed_rescue,
                  stopped_receipt=receipt)
            if allow_open_paper_carryover and spot_carryover_snapshot() != receipt.get('spot_carryover'):
                raise ValueError('QUIESCENCE_INSTALL_CARRYOVER_CHANGED')
            if allow_postclose_pending and pending_caucion_snapshot() != receipt.get('caucion_carryover'):
                raise ValueError('QUIESCENCE_INSTALL_CAUCION_CHANGED')
            if allow_intraday_caucion and pending_caucion_snapshot() != receipt.get('caucion_carryover'):
                raise ValueError('QUIESCENCE_INSTALL_INTRADAY_CAUCION_CHANGED')
            if allow_pending_caucion and verified_pending_caucion_snapshot() != receipt.get('caucion_carryover'):
                raise ValueError('QUIESCENCE_INSTALL_PENDING_CAUCION_CHANGED')
            receipt.update(status='HOST_INSTALLATION_STARTED', installation_started=True)
        elif action == 'verify-carryover':
            if not receipt or receipt.get('installation_started') is not True or not (
                    receipt.get('open_paper_carryover') or receipt.get('intraday_caucion') or
                    receipt.get('pending_caucion')):
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
            if receipt.get('pending_caucion') is True:
                if verified_pending_caucion_snapshot() != receipt.get('caucion_carryover'):
                    raise ValueError('QUIESCENCE_PENDING_CAUCION_CARRYOVER_CHANGED')
                receipt['caucion_verified_count'] = 1
            receipt['carryover_verified_at'] = datetime.now(timezone.utc).isoformat()
        elif action == 'image-intent':
            if (not receipt or receipt.get('candidate_sha') != candidate or
                    receipt.get('status') != 'HOST_INSTALLATION_STARTED' or
                    receipt.get('installation_started') is not True or
                    not (receipt.get('degraded_closed_rescue') is True or
                         receipt.get('a143_pending_adopted') is True or
                         receipt.get('a171_pending_adopted') is True or
                         receipt.get('a174_pending_adopted') is True or
                         receipt.get('a175_pending_adopted') is True) or
                    receipt.get('candidate_image_intent') is not None):
                raise ValueError('QUIESCENCE_RESCUE_IMAGE_INTENT_STATE_RED')
            if receipt.get('degraded_closed_rescue') is True:
                exact_rescue_receipt(receipt)
            elif receipt.get('a143_pending_adopted') is True:
                # A151/A143 is held and flat. Recheck the old stopped pair
                # before recording the new materialized image identity; this
                # durable intent precedes mode_manager and any new container.
                operator = candidate_operator_module()
                a143_stopped_continuation(
                    dict(receipt,status='DRAINED',installation_started=False),
                    operator_a151_proof=operator.exact_a151_operator_preopen_pause,
                    operator_timer_disabled=operator.operator_preopen_timer_disabled)
            elif receipt.get('a171_pending_adopted') is True:
                a171_stopped_continuation(receipt)
            elif receipt.get('a175_pending_adopted') is True:
                a175_stopped_continuation(receipt)
            else:
                a174_stopped_continuation(receipt)
            receipt['candidate_image_intent'] = candidate_image_intent(
                candidate, candidate_image_id, frozen_manifest)
        elif action == 'capture-runtime':
            if (not receipt or receipt.get('candidate_sha') != candidate or
                    receipt.get('status') != 'HOST_INSTALLATION_STARTED' or
                    receipt.get('installation_started') is not True or
                    receipt.get('degraded_closed_rescue') is not True or
                    not isinstance(receipt.get('candidate_image_intent'), dict) or
                    receipt.get('candidate_runtime_initial') is not None):
                raise ValueError('QUIESCENCE_RESCUE_CAPTURE_STATE_RED')
            observed = candidate_runtime_after_failed_install(receipt)
            if observed['status'] != 'running':
                raise ValueError('QUIESCENCE_RESCUE_CAPTURE_RUNNING_REQUIRED')
            receipt['candidate_runtime_initial'] = observed
        elif action == 'recover':
            if not receipt: return {'status': 'NO_DRAIN_RECEIPT'}
            if receipt['installation_started']:
                receipt['status'] = 'FIX_FORWARD_VALIDATION_REQUIRED'
                if receipt.get('degraded_closed_rescue') is True:
                    try:
                        observed = {name: inspect(name) for name in OWNED}
                        old_stopped = all(
                            observed[name]['Id'] == receipt['containers'][name]['id'] and
                            observed[name]['Image'] == receipt['containers'][name]['image'] and
                            observed[name]['State']['Status'] == 'exited' for name in OWNED)
                        if old_stopped:
                            # Even after loading the new image, a failure
                            # before mode_manager leaves the original clean
                            # stop intact. An intent does not imply install.
                            exact_clean_stopped(receipt)
                            admit(stopped=True, allow_degraded_closed_rescue=True,
                                  stopped_receipt=receipt)
                            critical_admission()
                        else:
                            observed = candidate_runtime_after_failed_install(receipt)
                            initial = receipt.get('candidate_runtime_initial')
                            if initial is not None and any(
                                    observed['containers'][name]['id'] != initial['containers'][name]['id'] or
                                    observed['containers'][name]['image'] != initial['containers'][name]['image']
                                    for name in OWNED):
                                raise ValueError('QUIESCENCE_RESCUE_CAPTURE_IDENTITY_CHANGED')
                            receipt['candidate_runtime_after_failure'] = observed
                    except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                        receipt['status'] = 'RECOVERY_UNVERIFIED'
                        receipt['rescue_failure_reason'] = type(exc).__name__ + ':' + str(exc)
                elif receipt.get('a175_pending_adopted') is True:
                    # The A175 origin is a failed, validation-pending runtime.
                    # It must NEVER be restarted as if it were a healthy base.
                    try:
                        observed={name:inspect(name) for name in OWNED}
                        old_stopped=all(
                            observed[name]['Id']==A175_RUNNING_IDS[name] and
                            observed[name]['Image']==A175_LOADED_IMAGE_ID and
                            observed[name]['State']['Status']=='exited'
                            for name in OWNED)
                        if old_stopped:
                            a175_stopped_continuation(receipt)
                            receipt['prior_a175_stopped_after_install']=True
                            receipt['runtime_health']='PRIOR_A175_PENDING_NOT_GO'
                        else:
                            runtime=candidate_runtime_after_failed_install(receipt)
                            if (runtime['flat_book']['source_sha']!=candidate or
                                    runtime['flat_book']['real_orders_sent']!=0 or
                                    runtime['critical_admission'].get('capability')!='issues_only'):
                                raise ValueError('QUIESCENCE_A175_NEW_RUNTIME_UNVERIFIED')
                            receipt['candidate_runtime_after_failure']=runtime
                            receipt['runtime_health']='NEW_A178_CANDIDATE_NOT_GO'
                    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
                        receipt['status']='RECOVERY_UNVERIFIED'
                elif receipt.get('a174_pending_adopted') is True:
                    try:
                        observed={name:inspect(name) for name in OWNED}
                        old_stopped=all(
                            observed[name]['Id']==A174_RUNNING_IDS[name] and
                            observed[name]['Image']==A174_LOADED_IMAGE_ID and
                            observed[name]['State']['Status']=='exited'
                            for name in OWNED)
                        if old_stopped:
                            a174_stopped_continuation(receipt)
                            receipt['prior_a174_stopped_after_install']=True
                            receipt['runtime_health']='PRIOR_A174_PENDING_NOT_GO'
                        else:
                            runtime=candidate_runtime_after_failed_install(receipt)
                            if (runtime['flat_book']['source_sha']!=candidate or
                                    runtime['flat_book']['real_orders_sent']!=0 or
                                    runtime['critical_admission'].get('capability')!='issues_only'):
                                raise ValueError('QUIESCENCE_A174_NEW_RUNTIME_UNVERIFIED')
                            receipt['candidate_runtime_after_failure']=runtime
                            receipt['runtime_health']='NEW_A175_CANDIDATE_NOT_GO'
                    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
                        receipt['status']='RECOVERY_UNVERIFIED'
                elif receipt.get('a171_pending_adopted') is True:
                    try:
                        observed={name:inspect(name) for name in OWNED}
                        old_stopped=all(
                            observed[name]['Id']==A171_RUNNING_IDS[name] and
                            observed[name]['Image']==A171_LOADED_IMAGE_ID and
                            observed[name]['State']['Status']=='exited'
                            for name in OWNED)
                        if old_stopped:
                            a171_stopped_continuation(receipt)
                            receipt['prior_a171_stopped_after_install']=True
                            receipt['runtime_health']='PRIOR_A171_PENDING_NOT_GO'
                        else:
                            runtime=candidate_runtime_after_failed_install(receipt)
                            if (runtime['flat_book']['source_sha'] != candidate or
                                    runtime['flat_book']['real_orders_sent'] != 0 or
                                    runtime['critical_admission'].get('capability') != 'issues_only'):
                                raise ValueError('QUIESCENCE_A171_NEW_RUNTIME_UNVERIFIED')
                            receipt['candidate_runtime_after_failure']=runtime
                            receipt['runtime_health']='NEW_A172_CANDIDATE_NOT_GO'
                    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
                        receipt['status']='RECOVERY_UNVERIFIED'
                elif receipt.get('a143_pending_adopted') is True:
                    # Keep the old pair stopped if failure precedes startup;
                    # after startup require the durable pre-start image intent.
                    try:
                        observed = {name:inspect(name) for name in OWNED}
                        old_stopped = all(
                            observed[name]['Id'] == receipt['containers'][name]['id'] and
                            observed[name]['Image'] == receipt['containers'][name]['image'] and
                            observed[name]['State']['Status'] == 'exited'
                            for name in OWNED)
                        if old_stopped:
                            operator = candidate_operator_module()
                            a143_stopped_continuation(
                                dict(receipt,status='DRAINED',installation_started=False),
                                operator_a151_proof=operator.exact_a151_operator_preopen_pause,
                                operator_timer_disabled=operator.operator_preopen_timer_disabled)
                            receipt['prior_a143_stopped_after_install'] = True
                            receipt['runtime_health'] = 'PRIOR_A143_PENDING_NOT_GO'
                        else:
                            runtime = candidate_runtime_after_failed_install(receipt)
                            if (runtime['flat_book']['source_sha'] != candidate or
                                    runtime['flat_book']['real_orders_sent'] != 0 or
                                    runtime['critical_admission'].get('capability') != 'issues_only'):
                                raise ValueError('QUIESCENCE_A143_NEW_RUNTIME_UNVERIFIED')
                            receipt['candidate_runtime_after_failure'] = runtime
                            receipt['runtime_health'] = 'NEW_A143_CANDIDATE_NOT_GO'
                    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
                        receipt['status'] = 'RECOVERY_UNVERIFIED'
            elif receipt.get('a175_pending_adopted') is True:
                try:
                    a175_stopped_continuation(receipt)
                    receipt['status']='PRIOR_A175_UNVALIDATED_HELD_STOPPED'
                    receipt['runtime_health']='PRIOR_A175_PENDING_NOT_GO'
                except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
                    receipt['status']='RECOVERY_UNVERIFIED'
            elif receipt.get('a174_pending_adopted') is True:
                try:
                    a174_stopped_continuation(receipt)
                    receipt['status']='PRIOR_A174_UNVALIDATED_HELD_STOPPED'
                    receipt['runtime_health']='PRIOR_A174_PENDING_NOT_GO'
                except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
                    receipt['status']='RECOVERY_UNVERIFIED'
            elif receipt.get('a171_pending_adopted') is True:
                try:
                    a171_stopped_continuation(receipt)
                    receipt['status']='PRIOR_A171_UNVALIDATED_HELD_STOPPED'
                    receipt['runtime_health']='PRIOR_A171_PENDING_NOT_GO'
                except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
                    receipt['status']='RECOVERY_UNVERIFIED'
            elif receipt.get('a143_pending_adopted') is True:
                # A preinstall failure must never restart old #751.
                try:
                    a143_stopped_continuation(receipt)
                    receipt['status'] = 'PRIOR_A143_UNVALIDATED_HELD_STOPPED'
                    receipt['runtime_health'] = 'PRIOR_A143_PENDING_NOT_GO'
                except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
                    receipt['status'] = 'RECOVERY_UNVERIFIED'
            elif receipt.get('adopted_failed_rescue') is True:
                # The original for this attempt is already a failed candidate.
                # A preinstall failure must not restart that image and must
                # preserve its exact clean stop for the next fix-forward.
                originals = {name: inspect(name) for name in OWNED}
                if (any(originals[name]['Id'] != receipt['containers'][name]['id'] or
                        originals[name]['Image'] != receipt['containers'][name]['image'] or
                        originals[name]['State']['Status'] != 'exited' or
                        lifecycle(originals[name]) != receipt['stop_evidence'][name]['after']
                        for name in OWNED) or
                        rescue_flat_book() != receipt.get('adopted_book') or
                        critical_admission() != receipt.get('critical_admission')):
                    receipt['status'] = 'RECOVERY_UNVERIFIED'
                else:
                    receipt['status'] = 'PRIOR_FAILED_CANDIDATE_HELD_STOPPED'
                    receipt['runtime_health'] = 'PRIOR_FAILED_CANDIDATE_UNVALIDATED'
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
                receipt['status'] = ('ORIGINAL_RUNTIME_DEGRADED_RESUMED' if
                                     receipt.get('degraded_closed_rescue') is True and not failures else
                                     'ORIGINAL_RUNTIME_UNVALIDATED_RESUMED' if
                                     receipt.get('adopted_failed_rescue') is True and not failures else
                                     'ORIGINAL_RUNTIME_RESUMED' if not failures else 'RECOVERY_UNVERIFIED')
                if receipt.get('degraded_closed_rescue') is True:
                    receipt['runtime_health'] = ('A92_SCANNER_CRASH_BACKOFF_UNVERIFIED' if
                        receipt.get('rescue_pre_state') == ['STOPPED','CLOSED'] else
                        'DEGRADED_UNVERIFIED_AFTER_RECOVERY')
                if receipt.get('adopted_failed_rescue') is True:
                    receipt['runtime_health'] = 'PRIOR_FAILED_CANDIDATE_UNVALIDATED'
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
                                            'install', 'image-intent', 'capture-runtime',
                                            'verify-carryover', 'recover'], required=True)
    parser.add_argument('--admin-cancel-issue624', action='store_true')
    parser.add_argument('--market-open-suspend', action='store_true')
    parser.add_argument('--carry-open-paper-spots', action='store_true')
    parser.add_argument('--paused-postclose', action='store_true')
    parser.add_argument('--intraday-caucion', action='store_true')
    parser.add_argument('--pending-paper-caucion', action='store_true')
    parser.add_argument('--rescue-degraded-closed-20261026', action='store_true')
    parser.add_argument('--adopt-failed-rescue-candidate', action='store_true')
    parser.add_argument('--adopt-a143-running-pending', action='store_true')
    parser.add_argument('--adopt-a143-stopped-pending', action='store_true')
    parser.add_argument('--adopt-a171-running-pending', action='store_true')
    parser.add_argument('--adopt-a174-running-pending', action='store_true')
    parser.add_argument('--adopt-a175-running-pending', action='store_true')
    parser.add_argument('--a151-held-open', action='store_true')
    parser.add_argument('--candidate-image-id')
    parser.add_argument('--frozen-manifest')
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
    if args.pending_paper_caucion and (args.market_open_suspend or args.carry_open_paper_spots or
                                       args.paused_postclose or args.intraday_caucion or
                                       args.admin_cancel_issue624 or args.action not in {'drain','install'}):
        parser.error('--pending-paper-caucion requires isolated closed PAPER drain/install')
    if args.rescue_degraded_closed_20261026 and (
            args.market_open_suspend or args.carry_open_paper_spots or
            args.paused_postclose or args.intraday_caucion or args.pending_paper_caucion or
            args.admin_cancel_issue624 or args.action not in {'drain', 'install'}):
        parser.error('--rescue-degraded-closed-20261026 requires isolated incident drain/install')
    if args.adopt_failed_rescue_candidate and (
            args.rescue_degraded_closed_20261026 or args.market_open_suspend or
            args.carry_open_paper_spots or args.paused_postclose or
            args.intraday_caucion or args.pending_paper_caucion or
            args.admin_cancel_issue624 or args.action not in {'drain','install'}):
        parser.error('--adopt-failed-rescue-candidate requires isolated stopped drain/install')
    if (args.adopt_a143_running_pending or args.adopt_a143_stopped_pending) and (
            args.rescue_degraded_closed_20261026 or args.adopt_failed_rescue_candidate or
            args.market_open_suspend or args.carry_open_paper_spots or
            args.paused_postclose or args.intraday_caucion or
            args.pending_paper_caucion or args.admin_cancel_issue624 or
            args.action not in {'drain','install'} or
            args.adopt_a143_running_pending == args.adopt_a143_stopped_pending):
        parser.error('A143 pending admission requires one isolated recovery drain/install')
    if args.a151_held_open and not (
            args.adopt_a143_running_pending or args.adopt_a143_stopped_pending):
        parser.error('--a151-held-open requires exact A143 pending recovery drain/install')
    if args.adopt_a171_running_pending and (
            args.rescue_degraded_closed_20261026 or args.adopt_failed_rescue_candidate or
            args.adopt_a143_running_pending or args.adopt_a143_stopped_pending or
            args.a151_held_open or args.market_open_suspend or
            args.carry_open_paper_spots or args.paused_postclose or
            args.intraday_caucion or args.pending_paper_caucion or
            args.admin_cancel_issue624 or args.action not in {'drain','install'}):
        parser.error('--adopt-a171-running-pending requires isolated recovery drain/install')
    if args.adopt_a174_running_pending and (
            args.rescue_degraded_closed_20261026 or args.adopt_failed_rescue_candidate or
            args.adopt_a143_running_pending or args.adopt_a143_stopped_pending or
            args.adopt_a171_running_pending or args.a151_held_open or
            args.market_open_suspend or args.carry_open_paper_spots or
            args.paused_postclose or args.intraday_caucion or
            args.pending_paper_caucion or args.admin_cancel_issue624 or
            args.action not in {'drain','install'}):
        parser.error('--adopt-a174-running-pending requires isolated recovery drain/install')
    if args.adopt_a175_running_pending and (
            args.rescue_degraded_closed_20261026 or args.adopt_failed_rescue_candidate or
            args.adopt_a143_running_pending or args.adopt_a143_stopped_pending or
            args.adopt_a171_running_pending or args.adopt_a174_running_pending or
            args.a151_held_open or args.market_open_suspend or
            args.carry_open_paper_spots or args.paused_postclose or
            args.intraday_caucion or args.pending_paper_caucion or
            args.admin_cancel_issue624 or args.action not in {'drain','install'}):
        parser.error('--adopt-a175-running-pending requires isolated recovery drain/install')
    if ((args.candidate_image_id or args.frozen_manifest) and args.action != 'image-intent') or (
            args.action == 'image-intent' and
            (not args.candidate_image_id or not args.frozen_manifest)):
        parser.error('--candidate-image-id and --frozen-manifest require image-intent')
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
    if args.pending_paper_caucion:
        options['allow_pending_caucion'] = True
    if args.rescue_degraded_closed_20261026:
        options['allow_degraded_closed_rescue'] = True
    if args.adopt_failed_rescue_candidate:
        options['allow_adopt_failed_rescue'] = True
    if args.adopt_a143_running_pending:
        options['allow_a143_pending_running'] = True
    if args.adopt_a143_stopped_pending:
        options['allow_a143_pending_stopped'] = True
    if args.a151_held_open:
        options['allow_a151_held_open'] = True
    if args.adopt_a171_running_pending:
        options['allow_a171_running_pending'] = True
    if args.adopt_a174_running_pending:
        options['allow_a174_running_pending'] = True
    if args.adopt_a175_running_pending:
        options['allow_a175_running_pending'] = True
    if args.action == 'image-intent':
        options.update(candidate_image_id=args.candidate_image_id,
                       frozen_manifest=args.frozen_manifest)
    receipt = run(args.candidate_sha, args.action, **options)
    if receipt['status']=='RECOVERY_UNVERIFIED':return 2
    if receipt['status']=='FIX_FORWARD_VALIDATION_REQUIRED' and not args.allow_validation_pending:return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
