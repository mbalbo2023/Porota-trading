#!/usr/bin/env python3
"""RC6 preopen readiness gate for the host.

Read-only and fail-closed. It never sends an order, edits configuration, restarts
units or sends Telegram directly. On a non-operational BYMA day it returns
NOT_DUE. Legacy release units are deliberately not part of this RC6 readiness
contract: deployment owns one-time retirement; daily RC6 health depends only on
RC6-native runtime state.
"""
from __future__ import annotations

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
EXPECTED_IMAGE = 'porota-trading-bot:17.0.0-rc6'
MIN_FREE_BYTES = 8 * 1024**3
REQUIRED_TIMERS = (
    'porota-functional-health-rc6.timer',
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
        qc = c.execute('PRAGMA quick_check').fetchone()[0]
        row = c.execute('SELECT mode,process_state,session_state,ppi_auth,real_orders_sent,heartbeat_at FROM observer_state WHERE id=1').fetchone()
        c.close()
        state = dict(row) if row else {}
        auth = str(state.get('ppi_auth') or '').upper()
        phase = str(state.get('session_state') or '').upper()
        auth_ok = auth in {'OK','AUTHENTICATED'} or (phase == 'MARKET_CLOSED' and auth == 'NOT_ATTEMPTED')
        ok = (qc == 'ok' and state.get('mode') == 'PRODUCTION_PAPER' and
              int(state.get('real_orders_sent') or 0) == 0 and auth_ok)
        return {'state':'GREEN' if ok else 'RED','quick_check':qc,'observer_state':state}
    except Exception as exc:
        return {'state':'RED','detail':f'{type(exc).__name__}:{exc}'}


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


def byma_morning_watch(today):
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
        if local.date() != today:
            return {'state':'RED','reason':'BYMA_WATCH_NOT_TODAY',
                    'observed_at':payload.get('observed_at')}
        state=str(payload.get('state') or '').upper()
        changed={str(x).upper() for x in payload.get('changed_components',[]) if x}
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


def main():
    now = datetime.now(TZ)
    today = now.date()
    if not byma.es_dia_habil_operativo(today):
        print(json.dumps({'schema':'POROTA_RC6_PREOPEN_V2','generated_at_ar':now.isoformat(timespec='seconds'),
                          'status':'NOT_DUE','reason':'BYMA_NON_OPERATIONAL_DAY',
                          'read_only':True,'network_order_test_performed':False}, ensure_ascii=False, sort_keys=True))
        return 0

    free = shutil.disk_usage('/').free
    checks = {
        'release_identity': release_identity(),
        'observer_container': container('porota_production_observer', True),
        'dashboard_container': container('porota_production_dashboard', False),
        'observer_db': observer_db(),
        'disk': {'state':'GREEN' if free >= MIN_FREE_BYTES else 'RED','free':free,'minimum':MIN_FREE_BYTES},
        'required_rc6_timers': required_timers(),
        'byma_morning_watch': byma_morning_watch(today),
        'history_quarantine': blocked_history_timers(),
        'foreign_market_policy': foreign_market_policy(today),
    }
    reds = [k for k,v in checks.items() if v.get('state') == 'RED']
    result = {'schema':'POROTA_RC6_PREOPEN_V2','generated_at_ar':now.isoformat(timespec='seconds'),
              'status':'GREEN' if not reds else 'RED','red_checks':reds,'checks':checks,
              'read_only':True,'network_order_test_performed':False,'direct_telegram_send':False}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if not reds else 2


if __name__ == '__main__':
    raise SystemExit(main())
