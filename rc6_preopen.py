#!/usr/bin/env python3
"""RC6 preopen readiness gate for the host.

Read-only and fail-closed. It never sends an order, edits configuration, restarts
units or sends Telegram directly. On a non-operational BYMA day it returns
NOT_DUE. Legacy release units are deliberately not part of this RC6 readiness
contract: deployment owns one-time retirement; daily RC6 health depends only on
RC6-native runtime state.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
from datetime import date, datetime
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import ak_byma_calendar as byma
from bu_instrument_catalog import rc6_underlying_opening_block
from rc6_paper_admission_gate import focus_contracts, operator_pause_reason, publish_preopen
from scripts.rc6_operator_paper_rearm import evaluate_rearm

TZ = ZoneInfo('America/Argentina/Buenos_Aires')
ROOT = Path('/opt/porota-trading')
DB = ROOT / 'data/paper_v17/observer_v17.db'
BYMA_MORNING_WATCH = ROOT / 'data/market/byma_morning_watch_latest.json'
BYMA_AUTHORITY_REVIEW = ROOT / 'ops/policy/rc6-byma-reviewed-authority-v1.json'
IOL_FAMILY_REFERENCE = ROOT / 'data/market/iol_family_reference_latest.json'
EXPECTED_IMAGE = 'porota-trading-bot:17.0.0-rc6'
BLOCKING_MIN_FREE_BYTES = 2 * 1024**3
TARGET_FREE_BYTES = 8 * 1024**3
IOL_FALLBACK_ORDER = [
    'IOL_LIVE_BOUNDED_RETRY', 'IOL_LAST_KNOWN_GOOD',
    'PPI_PRIMARY', 'BYMA_PUBLIC_COMPLEMENTARY',
]
REQUIRED_TIMERS = (
    'porota-fast-functional-health-rc6.timer',
    'porota-full-db-integrity-rc6.timer',
    'porota-preopen-rc6.timer',
    'porota-candle-integrity-rc6.timer',
    'porota-byma-morning-watch-rc6.timer',
)

# A186: an upstream session/freshness failure must not veto a frozen Deploy V2
# artifact. This is deliberately narrower than the normal PAPER preopen gate:
# malformed provider contracts, BYMA authority changes, SQLite, source identity,
# container health, timers and real-order safety remain independently RED.
PPI_AUTH_OUTAGE_STATES = frozenset({'ERROR', 'COOLDOWN', 'NOT_ATTEMPTED'})
DEPLOY_SOURCE_AVAILABILITY_REASONS = {
    'observer_db': frozenset({'PPI_AUTH_UNAVAILABLE', 'PAPER_CANDIDATES_UNAVAILABLE'}),
    'byma_morning_watch': frozenset({
        'BYMA_WATCH_NOT_TODAY', 'BYMA_DRY_VALIDATION_LATEST_NOT_SAFE',
        'BYMA_WATCH_DEGRADED', 'BYMA_WATCH_UNAVAILABLE',
    }),
    'iol_reference_fallback': frozenset({'IOL_REFERENCE_UNAVAILABLE'}),
}
SOURCE_CHECK_PROVIDER = {
    'observer_db': 'PPI', 'byma_morning_watch': 'BYMA',
    'iol_reference_fallback': 'IOL',
}


def cmd(args, timeout=20):
    try:
        p = subprocess.run(args, cwd=ROOT, text=True, capture_output=True,
                           timeout=timeout, check=False)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as exc:
        return 999, '', f'{type(exc).__name__}:{exc}'


def _deploy_json(filename, limit):
    """Read one exact host receipt without following links or trusting its size."""
    path = ROOT / 'data/deploy' / filename
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        observed = os.fstat(fd)
        if (not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1 or
                not 0 < observed.st_size <= limit):
            raise ValueError('DEPLOY_PROVENANCE_FILE_UNSAFE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        if len(raw) != observed.st_size:
            raise ValueError('DEPLOY_PROVENANCE_FILE_CHANGED')
        def distinct_pairs(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError('DEPLOY_PROVENANCE_DUPLICATE_KEY')
                value[key] = item
            return value
        def invalid_constant(_):
            raise ValueError('DEPLOY_PROVENANCE_NONFINITE')
        value = json.loads(raw, object_pairs_hook=distinct_pairs,
                           parse_constant=invalid_constant)
        if type(value) is not dict:
            raise ValueError('DEPLOY_PROVENANCE_NOT_OBJECT')
        return value
    finally:
        os.close(fd)


def _image_id(tag):
    rc, image_id, _ = cmd(['docker', 'image', 'inspect', '-f', '{{.Id}}', tag])
    if rc or not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id):
        raise ValueError('PREOPEN_IMAGE_TAG_UNVERIFIED')
    return image_id


def _source_digest(rel):
    path = ROOT / rel
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        observed = os.fstat(fd)
        if (not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1 or
                not 0 < observed.st_size <= 1024 * 1024):
            raise ValueError('PREOPEN_BUNDLE_SOURCE_UNSAFE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) != observed.st_size:
            raise ValueError('PREOPEN_BUNDLE_SOURCE_CHANGED')
        return hashlib.sha256(raw).hexdigest()
    finally:
        os.close(fd)


def _validated_runtime_marker(candidate, image_id):
    marker = _deploy_json('CURRENT_STATE_V2.json', 65536)
    recorded = marker.get('candidate_sha')
    if (not isinstance(recorded, str) or
            not re.fullmatch(r'[0-9a-f]{40}', recorded) or
            (candidate is not None and recorded != candidate) or
            marker.get('validation_status') != 'VALIDATED_RUNTIME' or
            marker.get('image_id') != image_id or
            marker.get('mode') != 'PRODUCTION_PAPER' or
            type(marker.get('real_orders_sent')) is not int or
            marker['real_orders_sent'] != 0 or
            marker.get('real_order_routes') != 'NOT_CALLED' or
            marker.get('ppi_watch_untouched') is not True):
        raise ValueError('PREOPEN_VALIDATED_RUNTIME_IDENTITY_RED')
    return recorded


def _candidate_image_provenance(candidate, image_id, deploy_scheduler_candidate):
    if (not re.fullmatch(r'[0-9a-f]{40}', candidate) or
            not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id)):
        raise ValueError('PREOPEN_CANDIDATE_IDENTITY_INVALID')
    frozen = _deploy_json('porota-frozen-candidate.json', 16384)
    manifest = _deploy_json('porota-deploy-bundle-v2-manifest.json', 2 * 1024 * 1024)
    tree = frozen.get('candidate_tree_sha')
    if (frozen.get('candidate_sha') != candidate or
            not isinstance(tree, str) or not re.fullmatch(r'[0-9a-f]{40}', tree) or
            not re.fullmatch(r'sha256:[0-9a-f]{64}', str(frozen.get('image_id'))) or
            not re.fullmatch(r'[0-9a-f]{64}', str(frozen.get('image_tar_sha256'))) or
            frozen.get('paper_mode_required') != 'PRODUCTION_PAPER' or
            type(frozen.get('real_orders_sent_required')) is not int or
            frozen['real_orders_sent_required'] != 0 or
            frozen.get('real_order_capability_required') != 'BLOCKED' or
            frozen.get('build_once') is not True):
        raise ValueError('PREOPEN_FROZEN_IDENTITY_RED')
    # A Docker load may normalize its image configuration. The frozen tar's
    # original image ID is then diagnostic; the running immutable .Image must
    # match BOTH materialized candidate and stable alias IDs exactly.
    candidate_tag = EXPECTED_IMAGE + '-candidate-' + candidate
    if (_image_id(candidate_tag) != image_id or
            _image_id(EXPECTED_IMAGE) != image_id):
        raise ValueError('PREOPEN_IMAGE_ALIAS_RED')
    rc, encoded_env, _ = cmd(['docker', 'image', 'inspect', '-f',
                              '{{json .Config.Env}}', candidate_tag])
    if rc or len(encoded_env) > 32768:
        raise ValueError('PREOPEN_IMAGE_SOURCE_UNVERIFIED')
    env = json.loads(encoded_env)
    if type(env) is not list or not all(type(item) is str for item in env):
        raise ValueError('PREOPEN_IMAGE_SOURCE_UNVERIFIED')
    for key, expected in (('POROTA_BUILD_SHA', candidate),
                          ('POROTA_CANDIDATE_TREE_SHA', tree)):
        if [item for item in env if item.startswith(key + '=')] != [key + '=' + expected]:
            raise ValueError('PREOPEN_IMAGE_SOURCE_RED')
    files = manifest.get('files')
    if (manifest.get('schema_version') != 1 or manifest.get('status') != 'GREEN' or
            type(files) is not list or type(manifest.get('file_count')) is not int or
            manifest['file_count'] != len(files) or
            not re.fullmatch(r'[0-9a-f]{64}', str(manifest.get('bundle_sha256')))):
        raise ValueError('PREOPEN_BUNDLE_MANIFEST_RED')
    for rel in ('rc6_preopen.py', '_version.py'):
        matching = [row for row in files if type(row) is dict and row.get('path') == rel]
        if (len(matching) != 1 or
                not re.fullmatch(r'[0-9a-f]{64}', str(matching[0].get('sha256'))) or
                _source_digest(rel) != matching[0]['sha256']):
            raise ValueError('PREOPEN_BUNDLE_SOURCE_RED')
    if deploy_scheduler_candidate is None:
        _validated_runtime_marker(candidate, image_id)
    elif deploy_scheduler_candidate != candidate:
        raise ValueError('PREOPEN_DEPLOY_CANDIDATE_MISMATCH')


def container(name, require_readonly=False, deploy_scheduler_candidate=None):
    rc, out, err = cmd(['docker','inspect','-f',
        '{{.State.Running}}|{{.Config.Image}}|{{.Image}}|{{.RestartCount}}|{{.State.OOMKilled}}|{{.HostConfig.ReadonlyRootfs}}', name])
    parts = out.split('|') if rc == 0 else []
    ok = (len(parts) == 6 and parts[0] == 'true' and parts[3] == '0' and
          parts[4] == 'false' and re.fullmatch(r'sha256:[0-9a-f]{64}', parts[2]))
    if require_readonly:
        ok = ok and parts[5] == 'true'
    reason = None
    if ok:
        try:
            if parts[1] == EXPECTED_IMAGE:
                # Preserve containers originally started under the stable tag.
                if _image_id(EXPECTED_IMAGE) != parts[2]:
                    raise ValueError('PREOPEN_STABLE_IMAGE_ID_RED')
                if deploy_scheduler_candidate is not None:
                    raise ValueError('PREOPEN_DEPLOY_IMAGE_NOT_CANDIDATE')
                _validated_runtime_marker(None, parts[2])
            elif parts[1].startswith(EXPECTED_IMAGE + '-candidate-'):
                candidate = parts[1][len(EXPECTED_IMAGE + '-candidate-'):]
                _candidate_image_provenance(candidate, parts[2], deploy_scheduler_candidate)
            else:
                raise ValueError('PREOPEN_IMAGE_TAG_RED')
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            ok = False
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
    return {'state':'GREEN' if ok else 'RED','raw':out,'error':err,
            'reason':reason}


def observer_db():
    try:
        c = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        # Keep this daily gate bounded. Full database integrity is owned by the
        # dedicated lower-frequency timer; the former full integrity scan
        # exhausted a deployment runner.
        c.execute('SELECT 1').fetchone()
        schema_version = int(c.execute('PRAGMA schema_version').fetchone()[0])
        row = c.execute('SELECT mode,process_state,session_state,ppi_auth,real_orders_sent,heartbeat_at FROM observer_state WHERE id=1').fetchone()
        state = dict(row) if row else {}
        auth = str(state.get('ppi_auth') or '').upper()
        phase = str(state.get('session_state') or '').upper()
        auth_ok = auth in {'OK','AUTHENTICATED'} or (phase == 'MARKET_CLOSED' and auth == 'NOT_ATTEMPTED')
        runtime = runtime_contracts(c, datetime.now(TZ))
        c.close()
        # Zero candidates is *instrument readiness*, not a broken SQLite
        # contract. It remains RED in regular PAPER preopen, but a technical
        # Deploy V2 may classify this exact, independently safe state AMBER.
        workers = runtime.get('workers') or {}
        candidate_only_red = (
            runtime.get('state') == 'RED' and
            type(runtime.get('scalping_eligible_identities')) is int and
            runtime['scalping_eligible_identities'] == 0 and
            type(runtime.get('unclassified_residuals')) is int and
            runtime['unclassified_residuals'] == 0 and
            set(workers) == {'scalping', 'caucion_cash_sweep', 'supervisor'} and
            all(type(row) is dict and row.get('state') == 'GREEN'
                for row in workers.values()) and
            runtime.get('real_routes_expected') == [])
        safety_ok = (schema_version >= 0 and state.get('mode') == 'PRODUCTION_PAPER' and
                     type(state.get('real_orders_sent')) is int and
                     state['real_orders_sent'] == 0 and
                     (runtime['state'] == 'GREEN' or candidate_only_red))
        result = {'state':'GREEN' if safety_ok and auth_ok and not candidate_only_red else 'RED',
                'bounded_readonly_probe':'GREEN','schema_version':schema_version,
                'observer_state':state,'runtime_contracts':runtime}
        if safety_ok and candidate_only_red:
            result['reason'] = 'PAPER_CANDIDATES_UNAVAILABLE'
        elif safety_ok and not auth_ok and auth in PPI_AUTH_OUTAGE_STATES:
            result['reason'] = 'PPI_AUTH_UNAVAILABLE'
        return result
    except Exception as exc:
        return {'state':'RED','detail':f'{type(exc).__name__}:{exc}'}


def runtime_contracts(connection, now):
    """Read-only WS15 invariants from the canonical SQLite runtime."""
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    required = {
        'candidate_identity_v2', 'intraday_scalping_worker_state',
        'paper_caucion_cash_sweep_state', 'paper_supervisor_state',
        'paper_positions', 'paper_cauciones',
    }
    missing = sorted(required - tables)
    if missing:
        return {'state':'RED','reason':'WS15_RUNTIME_TABLES_MISSING','missing':missing}
    residual = [dict(row) for row in connection.execute("""
      SELECT instrument_type,status,can_simulate,COUNT(*) AS count
      FROM candidate_identity_v2
      WHERE instrument_type IN ('OPCIONES','FUTUROS','ON','OBLIGACIONES')
      GROUP BY instrument_type,status,can_simulate
      ORDER BY instrument_type,status,can_simulate""")]
    unclassified = sum(row['count'] for row in residual
                       if row['status'] not in {'AVAILABLE','PAUSED_EXPLICIT'}
                       or (row['status']=='AVAILABLE') != bool(row['can_simulate']))
    scalping_eligible = connection.execute("""SELECT COUNT(*)
      FROM candidate_identity_v2
      WHERE status='AVAILABLE' AND can_simulate=1
        AND instrument_type IN ('ACCIONES','CEDEARS','ETFS')""").fetchone()[0]
    workers = {}
    ok = not unclassified and scalping_eligible > 0
    for name, table in (
            ('scalping','intraday_scalping_worker_state'),
            ('caucion_cash_sweep','paper_caucion_cash_sweep_state'),
            ('supervisor','paper_supervisor_state')):
        row = dict(connection.execute(f'SELECT * FROM {table} WHERE id=1').fetchone() or {})
        try:
            heartbeat = datetime.fromisoformat(
                str(row.get('heartbeat_at') or '').replace('Z','+00:00'))
            if heartbeat.tzinfo is None:
                raise ValueError('naive heartbeat')
            age = (now-heartbeat.astimezone(TZ)).total_seconds()
        except (TypeError,ValueError):
            age = None
        worker_ok = bool(row) and age is not None and 0 <= age <= 300
        if name == 'caucion_cash_sweep':
            try:
                routes = json.loads(row.get('routes_json') or 'null')
            except (TypeError,ValueError):
                routes = None
            worker_ok = (worker_ok and row.get('real_orders_sent') is not None
                         and int(row['real_orders_sent']) == 0
                         and routes == [])
        workers[name] = {'state':'GREEN' if worker_ok else 'RED',
                         'runtime_state':row.get('state'),
                         'heartbeat_age_seconds':age}
        ok = ok and worker_ok
    return {'state':'GREEN' if ok else 'RED',
            'residual_classification':residual,
            'unclassified_residuals':unclassified,
            'scalping_eligible_identities':scalping_eligible,
            'workers':workers,'real_routes_expected':[]}


def iol_reference_state(today):
    """IOL failure is a provenance state, never a synthetic zero value."""
    try:
        payload = json.loads(IOL_FAMILY_REFERENCE.read_text(encoding='utf-8'))
        cache = str(payload.get('cache_state') or '').upper()
        now = datetime.now(TZ)
        try:
            refreshed = datetime.fromisoformat(
                str(payload.get('refreshed_at') or '').replace('Z','+00:00'))
            age = ((now-refreshed.astimezone(TZ)).total_seconds()
                   if refreshed.tzinfo else None)
        except (TypeError, ValueError):
            age = None
        fallback = payload.get('fallback_order') or []
        continuation = payload.get('continuation_state')
        no_zero = continuation == 'CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL'
        caucion_ars = ((payload.get('cauciones') or {}).get('ARS') or [])
        caucion_state = str((payload.get('section_states') or {}).get(
            'caucion:ARS') or '').upper()
        usable_ars = bool(caucion_ars) and caucion_state != 'SOURCE_UNAVAILABLE_NO_LKG'
        exact_fallback = fallback == IOL_FALLBACK_ORDER
        contract_safe = (no_zero and exact_fallback and
                         payload.get('real_money_authorized') is False and
                         payload.get('decision_effect') == 'OBSERVE_ONLY')
        last_good = payload.get('last_known_good_at')
        try:
            last_good_time = datetime.fromisoformat(str(last_good).replace('Z','+00:00'))
            last_good_age = (now-last_good_time.astimezone(TZ)).total_seconds() if last_good_time.tzinfo else None
        except (TypeError, ValueError):
            last_good_age = None
        recent_lkg = last_good_age is not None and 0 <= last_good_age <= 7*86400
        sections = payload.get('section_states') or {}
        if (not contract_safe or not isinstance(sections, dict) or not sections or
                cache not in {'LIVE_FRESH','CACHE_FRESH','CACHE_STALE','SOURCE_UNAVAILABLE'}):
            state = 'RED'
            effective = 'SOURCE_UNAVAILABLE'
        elif (cache == 'LIVE_FRESH' and payload.get('source') == 'IOL_MCP'
              and isinstance(payload.get('live_sections'), list)
              and any(sections.get(key) == 'LIVE_FRESH'
                      for key in payload['live_sections'])
              and recent_lkg and usable_ars
              and last_good_age is not None and last_good_age <= 240
              and age is not None and 0 <= age <= 240):
            state = 'GREEN'
            reason = 'IOL_LIVE_OR_LKG_READY'
            effective = 'LIVE_FRESH'
        elif (recent_lkg and usable_ars and age is not None
              and 0 <= age <= 7*86400):
            # Old market data may be displayed with its provenance, but it
            # never becomes a current quote or a global deployment veto.
            state = 'AMBER'
            reason = 'IOL_STALE_LKG_WITH_ALTERNATIVES'
            effective = 'CACHE_STALE'
        else:
            state = 'AMBER'
            reason = 'IOL_UNAVAILABLE_WITH_PPI_BYMA_ALTERNATIVES'
            effective = 'SOURCE_UNAVAILABLE'
        return {'state':state,'cache_state':cache,
                'reason':reason if state != 'RED' else 'IOL_FAILSAFE_CONTRACT_INVALID',
                'refreshed_at':payload.get('refreshed_at'),
                'age_seconds':round(age,1) if age is not None else None,
                'effective_cache_state':effective,
                'last_good_age_seconds':round(last_good_age,1) if last_good_age is not None else None,
                'caucion_ars_state':caucion_state,
                'caucion_ars_records':len(caucion_ars),
                'fallback_order':fallback,'continuation_state':continuation,
                'last_known_good_at':last_good,'never_zero_fill':no_zero}
    except Exception as exc:
        return {'state':'RED','reason':'IOL_REFERENCE_UNAVAILABLE',
                'detail':f'{type(exc).__name__}:{exc}'}


def deploy_optional_source_availability(check_name, result):
    """Turn a *classified outage* into an alert for Deploy V2, never PAPER GO.

    This must not be called by regular preopen. A provider's absent snapshot or
    failed authentication is different from a corrupt cache, unsafe decision
    effect, unreviewed exchange-calendar change or broken system infrastructure.
    """
    if not isinstance(result, dict) or result.get('state') != 'RED':
        return result
    reason = result.get('reason')
    if reason not in DEPLOY_SOURCE_AVAILABILITY_REASONS.get(check_name, ()):
        return result
    if reason in {'BYMA_WATCH_UNAVAILABLE', 'IOL_REFERENCE_UNAVAILABLE'}:
        # Missing source snapshot is availability; parse/permissions errors are
        # data-integrity or local filesystem failures and remain hard RED.
        if not str(result.get('detail') or '').startswith('FileNotFoundError:'):
            return result
    degraded = dict(result)
    degraded.update(state='AMBER', source_state='RED',
                    deploy_effect='ALERT_ONLY',
                    provider=('PAPER_READINESS' if reason == 'PAPER_CANDIDATES_UNAVAILABLE'
                              else SOURCE_CHECK_PROVIDER[check_name]))
    return degraded


def preopen_phase(now):
    local = now.astimezone(TZ)
    minutes = local.hour*60 + local.minute
    if 9*60+45 <= minutes < 10*60+20:
        return 'T_MINUS_45'
    if 10*60+20 <= minutes < 10*60+30:
        return 'T_MINUS_10'
    return 'MANUAL_OR_OUTSIDE_SCHEDULE'


BLOCKED_HISTORY_TIMERS = (
    'porota-history-postclose-rc6.timer',
)


def blocked_history_timers():
    """Quarantine must be visible: inactive and not enabled is the only GREEN."""
    values = {}
    ok = True
    for unit in BLOCKED_HISTORY_TIMERS:
        rc1, enabled, _ = cmd(['systemctl', 'is-enabled', unit])
        rc2, active, _ = cmd(['systemctl', 'is-active', unit])
        good = enabled in {'disabled', 'masked'} and active in {'inactive', 'failed'}
        values[unit] = {
            'state': 'GREEN' if good else 'RED',
            'enabled': enabled,
            'active': active,
            'is_enabled_rc': rc1,
            'is_active_rc': rc2,
        }
        ok = ok and good
    return {'state': 'GREEN' if ok else 'RED', 'units': values,
            'policy': 'RC6_HISTORY_QUARANTINE'}


def required_timers(deploy_scheduler_candidate=None):
    values = {}
    ok = True
    optional_source_alerts = []
    units = REQUIRED_TIMERS
    operator_pause_timer = None
    operator_pause_sha = None
    operator_no_go_continuation = None
    preserved_iol_timer = None
    today_ar = datetime.now(TZ).date().isoformat()
    rearmed = evaluate_rearm(now=datetime.now(TZ), context='host')[0]
    if deploy_scheduler_candidate is None and today_ar >= '2026-10-29' and not rearmed:
        # The operator keeps the daily preopen timer disabled. Other daily
        # timers may have been restored after Deploy; A176 verifies the
        # historical hold and current PAPER/0 without reusing the held-16
        # snapshot as a live timer-status claim.
        try:
            from scripts import rc6_deploy_scheduler_suspend as suspend
            proof = suspend.exact_a176_operator_no_go_continuation(
                **({'historical_anchor': True} if today_ar > '2026-10-29' else {}))
            if (type(proof) is not dict or
                    proof.get('status') != 'NO_GO_PENDING_EXPLICIT_REARM' or
                    proof.get('business_day_art') != '2026-10-29' or
                    proof.get('paper_execution_authorized') is not False or
                    not re.fullmatch(r'A176-[1-9][0-9]{0,15}-[1-9][0-9]{0,3}',
                                     str(proof.get('operation_id'))) or
                    any(not re.fullmatch(r'[0-9a-f]{64}', str(proof.get(key)))
                        for key in ('receipt_sha256', 'a151_origin_sha256',
                                    'a175_quiescence_sha256', 'a175_scheduler_sha256'))):
                raise ValueError('PREOPEN_DAILY_A176_NO_GO_PROOF_RED')
            operator_no_go_continuation = proof
            operator_pause_timer = suspend.OPERATOR_PREOPEN_TIMER
            operator_pause_sha = proof['receipt_sha256']
            # A176 is historical 29-Oct authority, not a fresh 30-Oct receipt.
            # Its unchanged digest plus the *current* disabled timer proves
            # continued NO_GO without fabricating a new operator operation.
            operator_pause_operation = (proof['operation_id'] if today_ar == '2026-10-29'
                                        else 'A180-NO-GO-' + proof['receipt_sha256'][:16])
            day = today_ar
        except (OSError, ValueError, TypeError, KeyError, AttributeError,
                json.JSONDecodeError) as exc:
            return {'state':'RED','units':values,'reason':type(exc).__name__}
    if deploy_scheduler_candidate is not None:
        # A canonical intraday deployment intentionally pauses its exact
        # versioned timer set until runtime acceptance. Daily preopen calls
        # without this explicit candidate retain the active-timer contract.
        try:
            from scripts import rc6_deploy_scheduler_suspend as suspend
            if re.fullmatch(r'[0-9a-f]{40}', deploy_scheduler_candidate) is None:
                raise ValueError('PREOPEN_DEPLOY_CANDIDATE_INVALID')
            raw = (ROOT/'ops/policy/host-control-plane-reconciliation-v2.json').read_bytes()
            units = tuple(suspend.timer_policy(raw))
            # A145 permits exactly one OAuth guardian timer to stay ACTIVE
            # throughout the held deploy. Its policy carveout and scheduler
            # receipt must agree; every other non-preopen timer stays paused.
            preserved_iol_timer = suspend.preserved_iol_guard(raw)
            path = ROOT/'data/deploy'/('scheduler-suspend-'+deploy_scheduler_candidate+'.json')
            if (path.is_symlink() or not path.is_file() or path.stat().st_size > 65536 or
                    len(units) != 16 or not set(REQUIRED_TIMERS) <= set(units)):
                raise ValueError('PREOPEN_DEPLOY_RECEIPT_UNSAFE')
            receipt = json.loads(path.read_bytes())
            if (receipt.get('candidate_sha') != deploy_scheduler_candidate or
                    receipt.get('status') != 'SUSPENDED' or
                    receipt.get('policy_sha256') != hashlib.sha256(raw).hexdigest() or
                    receipt.get('timers') != list(units) or
                    set(receipt.get('attempted', [])) != set(units) or
                    receipt.get('preserved_iol_timer') != preserved_iol_timer or
                    receipt.get('ppi_before') != suspend.ppi_snapshot()):
                raise ValueError('PREOPEN_DEPLOY_RECEIPT_CHANGED')
            if receipt.get('operator_pause_timer') is not None:
                operator_pause_timer = receipt['operator_pause_timer']
                # The scheduler chain still anchors A125's original 27-Oct
                # gate bytes. A151 is a separate, same-day sidecar; never
                # replace the historical A125 digest inside that chain.
                historical_sha = suspend.operator_preopen_hold_evidence()
                if (operator_pause_timer != suspend.OPERATOR_PREOPEN_TIMER or
                        operator_pause_timer not in units or
                        receipt.get('operator_pause_receipt_sha256') != historical_sha or
                        not suspend.operator_preopen_timer_disabled()):
                    raise ValueError('PREOPEN_OPERATOR_PAUSE_EVIDENCE_CHANGED')
                day = datetime.now(TZ).date().isoformat()
                if day == suspend.OPERATOR_PAUSE_DAY:
                    operator_pause_sha = historical_sha
                    operator_pause_operation = suspend.OPERATOR_PAUSE_RUN
                elif day == suspend.A151_OPERATOR_PAUSE_DAY:
                    operator_pause_sha, operator_pause_operation = (
                        suspend.exact_a151_operator_preopen_pause())
                elif day >= '2026-10-29' and not rearmed:
                    # A151's dated authorization cannot be carried forward by
                    # inference. A176 is an independently checked host receipt,
                    # issued under the canonical mutex with PAPER admission held.
                    operator_no_go_continuation = (
                        suspend.exact_a176_operator_no_go_continuation(
                            **({'historical_anchor': True} if day > '2026-10-29' else {})))
                    proof = operator_no_go_continuation
                    if (type(proof) is not dict or
                            proof.get('status') != 'NO_GO_PENDING_EXPLICIT_REARM' or
                            proof.get('business_day_art') != '2026-10-29' or
                            proof.get('paper_execution_authorized') is not False or
                            not re.fullmatch(r'A176-[1-9][0-9]{0,15}-[1-9][0-9]{0,3}',
                                             str(proof.get('operation_id'))) or
                            any(not re.fullmatch(r'[0-9a-f]{64}', str(proof.get(key)))
                                for key in ('receipt_sha256', 'a151_origin_sha256',
                                            'a175_quiescence_sha256', 'a175_scheduler_sha256'))):
                        raise ValueError('PREOPEN_A176_NO_GO_PROOF_RED')
                    operator_pause_sha = proof['receipt_sha256']
                    operator_pause_operation = (proof['operation_id'] if day == '2026-10-29'
                                                else 'A180-NO-GO-' + proof['receipt_sha256'][:16])
                else:
                    raise ValueError('PREOPEN_OPERATOR_PAUSE_DAY_EXPIRED')
        except (OSError, ValueError, TypeError, KeyError, AttributeError,
                json.JSONDecodeError) as exc:
            return {'state':'RED','units':values,'reason':type(exc).__name__}
    for unit in units:
        rc1, enabled, _ = cmd(['systemctl','is-enabled',unit])
        rc2, active, _ = cmd(['systemctl','is-active',unit])
        optional_degraded = False
        if deploy_scheduler_candidate is None:
            if unit == operator_pause_timer:
                service_rc, service, _ = cmd(['systemctl','show',unit,'-p','Unit','--value'])
                service_state = (cmd(['systemctl','is-active',service])[1]
                                 if service_rc == 0 and service ==
                                 'porota-preopen-rc6.service' else 'unknown')
                good = (rc1 == 1 and enabled == 'disabled' and rc2 == 3 and
                        active == 'inactive' and service_state in {'inactive','failed'})
            else:
                good = rc1 == 0 and enabled == 'enabled' and rc2 == 0 and active == 'active'
        else:
            service_rc, service, _ = cmd(['systemctl','show',unit,'-p','Unit','--value'])
            service_state = cmd(['systemctl','is-active',service])[1] if service_rc == 0 and service.endswith('.service') else 'unknown'
            if unit == operator_pause_timer:
                good = (rc1 == 1 and enabled == 'disabled' and rc2 == 3 and active == 'inactive'
                        and service == 'porota-preopen-rc6.service' and
                        service_state in {'inactive','failed'})
            elif unit == preserved_iol_timer:
                # Periodic OAuth health work may legitimately be running. The
                # service identity must remain exact, never a generic active
                # timer exemption or trading/preopen permission. Its classified
                # exit 2 means IOL is degraded, not that the timer topology is
                # broken. A wrong unit, an unclassified exit, or an unreadable
                # systemd result remains RED. IOL provenance is separately
                # checked by iol_reference_state() and PAPER instrument gates.
                topology_good = (rc1 == 0 and enabled == 'enabled' and
                                 rc2 == 0 and active == 'active' and
                                 service_rc == 0 and
                                 service == suspend.IOL_OAUTH_GUARD_SERVICE)
                if topology_good and service_state == 'failed':
                    meta_rc, raw_meta, _ = cmd([
                        'systemctl', 'show', service, '-p', 'Result',
                        '-p', 'ExecMainCode', '-p', 'ExecMainStatus'])
                    meta = {}
                    for line in raw_meta.splitlines():
                        if '=' not in line:
                            meta = {}
                            break
                        key, value = line.split('=', 1)
                        if key in meta:
                            meta = {}
                            break
                        meta[key] = value
                    optional_degraded = (meta_rc == 0 and meta == {
                        'Result': 'exit-code', 'ExecMainCode': '1',
                        'ExecMainStatus': '2'})
                good = topology_good and (
                    service_state in {'inactive','active','activating',
                                      'deactivating','reloading'} or
                    optional_degraded)
            else:
                good = (rc1 == 0 and enabled == 'enabled' and rc2 == 3 and active == 'inactive'
                        and service_state not in {'unknown','active','activating','reloading','deactivating'})
        values[unit] = {'state':'AMBER' if optional_degraded and good else
                        'GREEN' if good else 'RED',
                        'enabled':enabled,'active':active}
        if (unit == preserved_iol_timer and deploy_scheduler_candidate is not None
                and optional_degraded and good):
            values[unit]['service_unit'] = service
            values[unit]['service_state'] = service_state
            values[unit]['reason'] = 'IOL_OAUTH_GUARDIAN_CLASSIFIED_EXIT2'
            optional_source_alerts.append(unit)
        ok = ok and good
    output = {'state':'GREEN' if ok else 'RED','units':values}
    if optional_source_alerts:
        output['optional_source_alerts'] = optional_source_alerts
    if operator_pause_timer is not None:
        output.update(operator_paper_paused=True,
                      operator_pause_day_ar=day,
                      operator_pause_operation_id=operator_pause_operation,
                      operator_pause_receipt_sha256=operator_pause_sha)
        if operator_no_go_continuation is not None:
            output['operator_no_go_continuation'] = operator_no_go_continuation
    return output


def byma_morning_watch(today, *, allow_latest_safe=False):
    """Require today's read-only BYMA authority check before preopen.

    Changes to hours/calendar are blocking until reviewed because they can
    change the legal/operational trading window. Other official changes are
    surfaced as AMBER for review without globally disabling unrelated PAPER
    families. Missing/degraded monitoring is RED: the requested daily control
    did not complete.
    """
    try:
        payload=json.loads(BYMA_MORNING_WATCH.read_text(encoding='utf-8'))
        observed=datetime.fromisoformat(str(payload.get('observed_at') or '').replace('Z','+00:00'))
        if observed.tzinfo is None:
            return {'state':'RED','reason':'BYMA_WATCH_TIMESTAMP_NAIVE'}
        local=observed.astimezone(TZ)
        age = (datetime.now(TZ) - observed.astimezone(TZ)).total_seconds()
        state=str(payload.get('state') or '').upper()
        changed={str(x).upper() for x in payload.get('changed_components',[]) if x}
        pending=payload.get('pending_review') or {}
        if not isinstance(pending,dict):
            return {'state':'RED','reason':'BYMA_PENDING_REVIEW_INVALID'}
        critical=(changed & {'HOURS','CALENDAR'} if state == 'CHANGED_REVIEW_REQUIRED' else set()) | (set(pending) & {'HOURS','CALENDAR'})
        if critical and state != 'DEGRADED':
            try:
                review=json.loads(BYMA_AUTHORITY_REVIEW.read_text(encoding='utf-8'))
                assert review.get('schema') == 'porota-byma-authority-review-v1'
                assert review.get('decision_effect') == 'OBSERVE_ONLY'
                assert review.get('real_money_authorized') is False
                assert today <= date.fromisoformat(str(review.get('valid_through_ar') or ''))
                pages=payload.get('pages') or {}
                approved=review.get('pages') or {}
                for name in critical:
                    page=pages.get(name) or {}
                    expected=approved.get(name) or {}
                    digest=page.get('sha256')
                    assert page.get('status') == 'OK' and page.get('scope') == 'h1-to-footer-v2'
                    assert page.get('url') == expected.get('url')
                    assert isinstance(digest,str) and len(digest) == 64
                    assert digest == expected.get('sha256')
                    if name in pending:
                        assert pending[name].get('sha256') == digest
            except (OSError,ValueError,TypeError,KeyError,AssertionError,AttributeError):
                return {'state':'RED','reason':'BYMA_AUTHORITY_CHANGE_REVIEW_REQUIRED',
                        'critical_components':sorted(critical),
                        'changed_components':sorted(changed)}
        if local.date() != today and not allow_latest_safe:
            return {'state':'RED','reason':'BYMA_WATCH_NOT_TODAY',
                    'observed_at':payload.get('observed_at')}
        if local.date() != today and allow_latest_safe:
            if not (0 <= age <= 72*3600 and state in {'NO_CHANGE','BASELINE_CREATED'}):
                return {'state':'RED','reason':'BYMA_DRY_VALIDATION_LATEST_NOT_SAFE',
                        'watch_state':state,'age_seconds':round(age,1),
                        'changed_components':sorted(changed)}
            return {'state':'AMBER','reason':'DRY_VALIDATION_LATEST_SAFE',
                    'observed_at':payload.get('observed_at'),
                    'age_seconds':round(age,1),'changed_components':sorted(changed)}
        if state == 'DEGRADED':
            return {'state':'RED','reason':'BYMA_WATCH_DEGRADED',
                    'changed_components':sorted(changed),'errors':payload.get('errors',[])}
        if state == 'CHANGED_REVIEW_REQUIRED':
            if critical:
                return {'state':'AMBER','reason':'BYMA_CRITICAL_REVIEWED_EXACT',
                        'critical_components':sorted(critical),
                        'changed_components':sorted(changed)}
            return {'state':'AMBER','reason':'BYMA_CHANGE_REVIEW_REQUIRED',
                    'changed_components':sorted(changed)}
        if state in {'NO_CHANGE','BASELINE_CREATED'}:
            return {'state':'GREEN','reason':state,
                    'changed_components':sorted(changed),
                    'reviewed_critical_components':sorted(critical)}
        return {'state':'RED','reason':'BYMA_WATCH_STATE_UNKNOWN','watch_state':state}
    except Exception as exc:
        return {'state':'RED','reason':'BYMA_WATCH_UNAVAILABLE',
                'detail':f'{type(exc).__name__}:{exc}'}


def release_identity():
    try:
        import _version
        ok = (_version.VERSION == '17.0.0-rc6' and
              _version.MODE == 'PRODUCTION_PAPER' and
              _version.EXECUTION == 'SIMULATED' and
              _version.REAL_ORDER_CAPABILITY == 'BLOCKED')
        return {'state':'GREEN' if ok else 'RED','version':_version.VERSION,
                'mode':_version.MODE,'execution':_version.EXECUTION,
                'real_order_capability':_version.REAL_ORDER_CAPABILITY}
    except Exception as exc:
        return {'state':'RED','detail':f'{type(exc).__name__}:{exc}'}


def foreign_market_policy(today):
    if today.isoformat() != '2026-09-07':
        return {'state':'GREEN','event':None}
    probe = datetime(2026,9,7,10,15,tzinfo=TZ)
    focus_symbols = ('AAPL','AAPLD','AAPLC')
    nonfocus_symbols = ('MSFT','KO')
    focus_reasons = {s:rc6_underlying_opening_block(s,'CEDEARS',probe) for s in focus_symbols}
    nonfocus_reasons = {s:rc6_underlying_opening_block(s,'CEDEARS',probe) for s in nonfocus_symbols}
    argentina_equity = rc6_underlying_opening_block('GGAL','ACCIONES',probe)
    all_reasons = tuple(focus_reasons.values()) + tuple(nonfocus_reasons.values())
    ok = all(v == 'UNDERLYING_MARKET_CLOSED: US_LABOR_DAY' for v in all_reasons) and not argentina_equity
    return {'state':'GREEN' if ok else 'RED','event':'US_LABOR_DAY',
            'blocked_focus_cedears':focus_reasons,
            'blocked_nonfocus_cedear_probes':nonfocus_reasons,
            'argentina_equity_block':argentina_equity or None,
            'policy':'OBSERVE_AND_RECORD_QUOTES; HOLD_NEW_CEDEAR_OPENINGS_WHILE_US_MARKET_CLOSED'}


def main(phase_override=None, deploy_scheduler_candidate=None, publish_paper_gate=False):
    now = datetime.now(TZ)
    today = now.date()
    if not byma.es_dia_habil_operativo(today):
        result = {'schema':'POROTA_RC6_PREOPEN_V3','generated_at_ar':now.isoformat(timespec='seconds'),
                          'status':'NOT_DUE','reason':'BYMA_NON_OPERATIONAL_DAY',
                          'read_only':not publish_paper_gate,'network_order_test_performed':False}
        if publish_paper_gate:
            publish_preopen(result, now=now)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0

    free = shutil.disk_usage('/').free
    disk_state = ('RED' if free < BLOCKING_MIN_FREE_BYTES else
                  'AMBER' if free < TARGET_FREE_BYTES else 'GREEN')
    checks = {
        'release_identity': release_identity(),
        'observer_container': container('porota_production_observer', True,
                                        deploy_scheduler_candidate),
        'dashboard_container': container('porota_production_dashboard', False,
                                         deploy_scheduler_candidate),
        'observer_db': observer_db(),
        'disk': {'state':disk_state,'free':free,
                 'blocking_minimum':BLOCKING_MIN_FREE_BYTES,
                 'target':TARGET_FREE_BYTES,
                 'reason':'RUNTIME_RESERVE_BELOW_TARGET' if disk_state == 'AMBER' else None},
        'required_rc6_timers': required_timers(deploy_scheduler_candidate),
        'byma_morning_watch': byma_morning_watch(
            today, allow_latest_safe=phase_override is not None),
        'iol_reference_fallback': iol_reference_state(today),
        'history_quarantine': blocked_history_timers(),
        'foreign_market_policy': foreign_market_policy(today),
    }
    if publish_paper_gate:
        checks['paper_focus_contracts'] = focus_contracts(DB)
    if deploy_scheduler_candidate is not None:
        for key in SOURCE_CHECK_PROVIDER:
            checks[key] = deploy_optional_source_availability(key, checks[key])
    reds = [k for k,v in checks.items() if v.get('state') == 'RED']
    paused = checks['required_rc6_timers'].get('operator_paper_paused') is True
    result = {'schema':'POROTA_RC6_PREOPEN_V3','generated_at_ar':now.isoformat(timespec='seconds'),
              'phase':phase_override or preopen_phase(now),
              'status':'RED' if reds else 'PAUSED_NO_GO' if paused else 'GREEN',
              'red_checks':reds,'checks':checks,
              'paper_admission_hold':operator_pause_reason(now, context='host') or None,
              'dry_equivalent':phase_override is not None,
              'read_only':not publish_paper_gate,'network_order_test_performed':False,'direct_telegram_send':False}
    if deploy_scheduler_candidate is not None:
        alerts = [
            {'provider':checks[key].get('provider', provider), 'check':key,
             'reason':checks[key].get('reason') or 'SOURCE_AMBER',
             'deploy_effect':'ALERT_ONLY',
             'paper_readiness':'FAIL_CLOSED_PER_INSTRUMENT'}
            for key,provider in SOURCE_CHECK_PROVIDER.items()
            if checks[key].get('state') == 'AMBER'
        ]
        if alerts:
            result['external_source_alerts'] = alerts
    rearm_authorized, _, rearm_summary = evaluate_rearm(now=now, context='host')
    if rearm_authorized:
        # This is source-bound operator consent, not an execution permission.
        # The actual PAPER admission remains behind a fresh v2 T-10 receipt.
        result['operator_rearm_authority'] = rearm_summary
    if paused:
        result['operator_paper_paused'] = True
        result['operator_pause_day_ar'] = checks['required_rc6_timers']['operator_pause_day_ar']
        result['operator_pause_operation_id'] = checks['required_rc6_timers']['operator_pause_operation_id']
        result['operator_pause_receipt_sha256'] = checks['required_rc6_timers']['operator_pause_receipt_sha256']
        if 'operator_no_go_continuation' in checks['required_rc6_timers']:
            result['operator_no_go_continuation'] = (
                checks['required_rc6_timers']['operator_no_go_continuation'])
    if publish_paper_gate:
        if paused and today.isoformat() >= '2026-10-29':
            # The historical A125 LIVE gate is immutable. The durable A176
            # sidecar is the NO_GO authority; no GO receipt is written here.
            result['paper_admission_receipt'] = {
                'status':'NO_GO_PAPER_SCALPING',
                'reason':'OPERATOR_NO_GO_PENDING_EXPLICIT_REARM',
                'continuation_receipt_sha256':result['operator_pause_receipt_sha256'],
                'new_receipt_written':False}
        else:
            try:
                result['paper_admission_receipt'] = publish_preopen(result, now=now)
            except (OSError, ValueError) as exc:
                result['status'] = 'RED'
                result['red_checks'].append('paper_admission_receipt')
                result['paper_admission_receipt'] = {'status':'NO_GO_PAPER_SCALPING',
                                                      'reason':type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result['status'] in {'GREEN','PAUSED_NO_GO'} else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=('T_MINUS_45','T_MINUS_10'))
    parser.add_argument('--deploy-scheduler-candidate')
    parser.add_argument('--publish-paper-gate', action='store_true')
    args = parser.parse_args()
    raise SystemExit(main(args.phase, args.deploy_scheduler_candidate, args.publish_paper_gate))
