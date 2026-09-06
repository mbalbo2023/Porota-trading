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
EXPECTED_IMAGE = 'porota-trading-bot:17.0.0-rc6'
MIN_FREE_BYTES = 8 * 1024**3
REQUIRED_TIMERS = (
    'porota-functional-health-rc6.timer',
    'porota-history-postclose-rc6.timer',
    'porota-host-general-backup-rc6.timer',
    'porota-preopen-rc6.timer',
    'porota-candle-integrity-rc6.timer',
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
    symbols = ('AAPL','AAPLD','AAPLC')
    reasons = {s:rc6_underlying_opening_block(s,'CEDEARS',probe) for s in symbols}
    argentina_equity = rc6_underlying_opening_block('GGAL','ACCIONES',probe)
    ok = all(v == 'UNDERLYING_MARKET_CLOSED: US_LABOR_DAY' for v in reasons.values()) and not argentina_equity
    return {'state':'GREEN' if ok else 'RED','event':'US_LABOR_DAY',
            'blocked_focus_cedears':reasons,'argentina_equity_block':argentina_equity or None,
            'policy':'OBSERVE_AND_RECORD_QUOTES; HOLD_NEW_APPLE_CEDEAR_OPENINGS'}


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
