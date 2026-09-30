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
from datetime import datetime
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import ak_byma_calendar as byma
from bu_instrument_catalog import rc6_underlying_opening_block

TZ = ZoneInfo('America/Argentina/Buenos_Aires')
ROOT = Path('/opt/porota-trading')
DB = ROOT / 'data/paper_v17/observer_v17.db'
BYMA_MORNING_WATCH = ROOT / 'data/market/byma_morning_watch_latest.json'
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
    'porota-host-general-backup-rc6.timer',
    'porota-preopen-rc6.timer',
    'porota-candle-integrity-rc6.timer',
    'porota-byma-morning-watch-rc6.timer',
)


def cmd(args, timeout=20):
    try:
        p = subprocess.run(args, cwd=ROOT, text=True, capture_output=True,
                           timeout=timeout, check=False)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as exc:
        return 999, '', f'{type(exc).__name__}:{exc}'


def container(name, require_readonly=False):
    rc, out, err = cmd(['docker','inspect','-f',
        '{{.State.Running}}|{{.Config.Image}}|{{.RestartCount}}|{{.HostConfig.ReadonlyRootfs}}', name])
    parts = out.split('|') if rc == 0 else []
    ok = len(parts) == 4 and parts[0] == 'true' and parts[1] == EXPECTED_IMAGE and parts[2] == '0'
    if require_readonly:
        ok = ok and parts[3] == 'true'
    return {'state':'GREEN' if ok else 'RED','raw':out,'error':err}


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
        ok = (schema_version >= 0 and state.get('mode') == 'PRODUCTION_PAPER' and
              int(state.get('real_orders_sent') or 0) == 0 and auth_ok)
        ok = ok and runtime['state'] == 'GREEN'
        return {'state':'GREEN' if ok else 'RED',
                'bounded_readonly_probe':'GREEN','schema_version':schema_version,
                'observer_state':state,'runtime_contracts':runtime}
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
            age = max(0.0,(now-heartbeat.astimezone(TZ)).total_seconds())
        except (TypeError,ValueError):
            age = None
        worker_ok = bool(row) and age is not None and age <= 300
        if name == 'caucion_cash_sweep':
            try:
                routes = json.loads(row.get('routes_json') or 'null')
            except (TypeError,ValueError):
                routes = None
            worker_ok = (worker_ok and int(row.get('real_orders_sent',-1)) == 0
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
        refreshed = datetime.fromisoformat(
            str(payload.get('refreshed_at') or '').replace('Z','+00:00'))
        if refreshed.tzinfo is None:
            raise ValueError('naive timestamp')
        cache = str(payload.get('cache_state') or '').upper()
        age = (datetime.now(TZ)-refreshed.astimezone(TZ)).total_seconds()
        fallback = payload.get('fallback_order') or []
        continuation = payload.get('continuation_state')
        no_zero = continuation == 'CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL'
        caucion_ars = ((payload.get('cauciones') or {}).get('ARS') or [])
        caucion_state = str((payload.get('section_states') or {}).get(
            'caucion:ARS') or '').upper()
        usable_ars = bool(caucion_ars) and caucion_state != 'SOURCE_UNAVAILABLE_NO_LKG'
        exact_fallback = fallback == IOL_FALLBACK_ORDER
        last_good = payload.get('last_known_good_at')
        sections = payload.get('section_states') or {}
        if (cache in {'LIVE_FRESH','CACHE_FRESH'} and no_zero and exact_fallback
                and last_good and usable_ars and 0 <= age <= 86400):
            state = 'GREEN'
            reason = 'IOL_LIVE_OR_LKG_READY'
        elif (cache == 'CACHE_STALE' and no_zero and exact_fallback and last_good
              and usable_ars
              and 0 <= age <= 7*86400):
            state = 'AMBER'
            reason = 'IOL_STALE_LKG_WITH_ALTERNATIVES'
        elif (cache == 'SOURCE_UNAVAILABLE' and no_zero and exact_fallback
              and isinstance(sections, dict) and sections):
            state = 'AMBER'
            reason = 'CONTINUE_WITH_PPI_BYMA_ALTERNATIVES'
        else:
            state = 'RED'
        return {'state':state,'cache_state':cache,
                'reason':reason if state != 'RED' else 'IOL_FAILSAFE_CONTRACT_INVALID',
                'refreshed_at':refreshed.isoformat(),
                'age_seconds':round(age,1),
                'caucion_ars_state':caucion_state,
                'caucion_ars_records':len(caucion_ars),
                'fallback_order':fallback,'continuation_state':continuation,
                'last_known_good_at':last_good,'never_zero_fill':no_zero}
    except Exception as exc:
        return {'state':'RED','reason':'IOL_REFERENCE_UNAVAILABLE',
                'detail':f'{type(exc).__name__}:{exc}'}


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


def required_timers():
    values = {}
    ok = True
    for unit in REQUIRED_TIMERS:
        rc1, enabled, _ = cmd(['systemctl','is-enabled',unit])
        rc2, active, _ = cmd(['systemctl','is-active',unit])
        good = rc1 == 0 and enabled == 'enabled' and rc2 == 0 and active == 'active'
        values[unit] = {'state':'GREEN' if good else 'RED','enabled':enabled,'active':active}
        ok = ok and good
    return {'state':'GREEN' if ok else 'RED','units':values}


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
            critical=changed & {'HOURS','CALENDAR'}
            if critical:
                return {'state':'RED','reason':'BYMA_AUTHORITY_CHANGE_REVIEW_REQUIRED',
                        'critical_components':sorted(critical),
                        'changed_components':sorted(changed)}
            return {'state':'AMBER','reason':'BYMA_CHANGE_REVIEW_REQUIRED',
                    'changed_components':sorted(changed)}
        if state in {'NO_CHANGE','BASELINE_CREATED'}:
            return {'state':'GREEN','reason':state,
                    'changed_components':sorted(changed)}
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


def main(phase_override=None):
    now = datetime.now(TZ)
    today = now.date()
    if not byma.es_dia_habil_operativo(today):
        print(json.dumps({'schema':'POROTA_RC6_PREOPEN_V3','generated_at_ar':now.isoformat(timespec='seconds'),
                          'status':'NOT_DUE','reason':'BYMA_NON_OPERATIONAL_DAY',
                          'read_only':True,'network_order_test_performed':False}, ensure_ascii=False, sort_keys=True))
        return 0

    free = shutil.disk_usage('/').free
    disk_state = ('RED' if free < BLOCKING_MIN_FREE_BYTES else
                  'AMBER' if free < TARGET_FREE_BYTES else 'GREEN')
    checks = {
        'release_identity': release_identity(),
        'observer_container': container('porota_production_observer', True),
        'dashboard_container': container('porota_production_dashboard', False),
        'observer_db': observer_db(),
        'disk': {'state':disk_state,'free':free,
                 'blocking_minimum':BLOCKING_MIN_FREE_BYTES,
                 'target':TARGET_FREE_BYTES,
                 'reason':'RUNTIME_RESERVE_BELOW_TARGET' if disk_state == 'AMBER' else None},
        'required_rc6_timers': required_timers(),
        'byma_morning_watch': byma_morning_watch(
            today, allow_latest_safe=phase_override is not None),
        'iol_reference_fallback': iol_reference_state(today),
        'history_quarantine': blocked_history_timers(),
        'foreign_market_policy': foreign_market_policy(today),
    }
    reds = [k for k,v in checks.items() if v.get('state') == 'RED']
    result = {'schema':'POROTA_RC6_PREOPEN_V3','generated_at_ar':now.isoformat(timespec='seconds'),
              'phase':phase_override or preopen_phase(now),
              'status':'GREEN' if not reds else 'RED','red_checks':reds,'checks':checks,
              'dry_equivalent':phase_override is not None,
              'read_only':True,'network_order_test_performed':False,'direct_telegram_send':False}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if not reds else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=('T_MINUS_45','T_MINUS_10'))
    args = parser.parse_args()
    raise SystemExit(main(args.phase))
