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
    # Frequent health was split from the expensive full SQLite scan on 2026-09-07.
    # Both probes are read-only and are part of the proven RC6 host control-plane.
    'porota-fast-functional-health-rc6.timer',
    'porota-full-db-integrity-rc6.timer',
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
    # RestartCount is cumulative for the lifetime of the container and can stay
    # non-zero after an intentional/recovered restart. Treat it as evidence, not
    # as a permanent readiness veto. Current running/image/read-only state plus
    # observer heartbeat/DB invariants determine readiness.
    ok = len(parts) == 4 and parts[0] == 'true' and parts[1] == EXPECTED_IMAGE
    if require_readonly:
        ok = ok and parts[3] == 'true'
    restart_count = None
    if len(parts) == 4:
        try:
            restart_count = int(parts[2])
        except (TypeError, ValueError):
            restart_count = None
    return {'state':'GREEN' if ok else 'RED','raw':out,'error':err,
            'restart_count':restart_count,'restart_count_policy':'INFORMATIONAL'}


def observer_db():
    """Lightweight live-state probe; full SQLite scans are delegated postclose."""
    try:
        c = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        row = c.execute('SELECT mode,process_state,session_state,ppi_auth,real_orders_sent,heartbeat_at FROM observer_state WHERE id=1').fetchone()
        c.close()
        state = dict(row) if row else {}
        auth = str(state.get('ppi_auth') or '').upper()
        phase = str(state.get('session_state') or '').upper()
        auth_ok = auth in {'OK','AUTHENTICATED'} or (phase == 'MARKET_CLOSED' and auth == 'NOT_ATTEMPTED')
        ok = (state.get('mode') == 'PRODUCTION_PAPER' and
              int(state.get('real_orders_sent') or 0) == 0 and auth_ok)
        return {
            'state':'GREEN' if ok else 'RED',
            'observer_state':state,
            'integrity_scan':'DELEGATED_TO_porota-full-db-integrity-rc6.service',
        }
    except Exception as exc:
        return {'state':'RED','detail':f'{type(exc).__name__}:{exc}'}


def full_db_integrity_evidence():
    """Require a completed successful run without performing a live full DB scan."""
    rc1, result, err1 = cmd([
        'systemctl','show','porota-full-db-integrity-rc6.service','-p','Result','--value'
    ])
    rc2, status, err2 = cmd([
        'systemctl','show','porota-full-db-integrity-rc6.service','-p','ExecMainStatus','--value'
    ])
    rc3, last_exit, err3 = cmd([
        'systemctl','show','porota-full-db-integrity-rc6.service','-p','ExecMainExitTimestamp','--value'
    ])
    ok = (
        rc1 == 0 and rc2 == 0 and rc3 == 0 and
        result == 'success' and status == '0' and bool(last_exit.strip())
    )
    return {
        'state':'GREEN' if ok else 'RED',
        'result':result,
        'exec_main_status':status,
        'last_exit':last_exit,
        'error':' | '.join(x for x in (err1,err2,err3) if x),
        'policy':'FULL_SCAN_POSTCLOSE_ONLY',
    }


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
        'full_db_integrity': full_db_integrity_evidence(),
        'disk': {'state':'GREEN' if free >= MIN_FREE_BYTES else 'RED','free':free,'minimum':MIN_FREE_BYTES},
        'required_rc6_timers': required_timers(),
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
