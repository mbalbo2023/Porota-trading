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
import re
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
              state.get('real_orders_sent') is not None and
              int(state['real_orders_sent']) == 0 and auth_ok)
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
    units = REQUIRED_TIMERS
    operator_pause_timer = None
    operator_pause_sha = None
    preserved_iol_timer = None
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
                else:
                    raise ValueError('PREOPEN_OPERATOR_PAUSE_DAY_EXPIRED')
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            return {'state':'RED','units':values,'reason':type(exc).__name__}
    for unit in units:
        rc1, enabled, _ = cmd(['systemctl','is-enabled',unit])
        rc2, active, _ = cmd(['systemctl','is-active',unit])
        if deploy_scheduler_candidate is None:
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
                # timer exemption or trading/preopen permission.
                good = (rc1 == 0 and enabled == 'enabled' and rc2 == 0 and active == 'active'
                        and service == suspend.IOL_OAUTH_GUARD_SERVICE and
                        service_state in {'inactive','active','activating','deactivating','reloading'})
            else:
                good = (rc1 == 0 and enabled == 'enabled' and rc2 == 3 and active == 'inactive'
                        and service_state not in {'unknown','active','activating','reloading','deactivating'})
        values[unit] = {'state':'GREEN' if good else 'RED','enabled':enabled,'active':active}
        ok = ok and good
    output = {'state':'GREEN' if ok else 'RED','units':values}
    if operator_pause_timer is not None:
        output.update(operator_paper_paused=True,
                      operator_pause_day_ar=day,
                      operator_pause_operation_id=operator_pause_operation,
                      operator_pause_receipt_sha256=operator_pause_sha)
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
        'observer_container': container('porota_production_observer', True),
        'dashboard_container': container('porota_production_dashboard', False),
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
    reds = [k for k,v in checks.items() if v.get('state') == 'RED']
    paused = bool(deploy_scheduler_candidate is not None and
                  checks['required_rc6_timers'].get('operator_paper_paused') is True)
    result = {'schema':'POROTA_RC6_PREOPEN_V3','generated_at_ar':now.isoformat(timespec='seconds'),
              'phase':phase_override or preopen_phase(now),
              'status':'RED' if reds else 'PAUSED_NO_GO' if paused else 'GREEN',
              'red_checks':reds,'checks':checks,
              'paper_admission_hold':operator_pause_reason(now) or None,
              'dry_equivalent':phase_override is not None,
              'read_only':not publish_paper_gate,'network_order_test_performed':False,'direct_telegram_send':False}
    if paused:
        result['operator_paper_paused'] = True
        result['operator_pause_day_ar'] = checks['required_rc6_timers']['operator_pause_day_ar']
        result['operator_pause_operation_id'] = checks['required_rc6_timers']['operator_pause_operation_id']
        result['operator_pause_receipt_sha256'] = checks['required_rc6_timers']['operator_pause_receipt_sha256']
    if publish_paper_gate:
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
