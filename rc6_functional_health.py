#!/usr/bin/env python3
"""RC6 lightweight functional health probe.

Read-only against the PAPER database. It never mutates runtime state, sends
orders, changes configuration, or emits Telegram. Non-zero exit means a safety
or liveness invariant is not currently satisfied.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sqlite3

from _version import MODE, EXECUTION, REAL_ORDER_CAPABILITY, VERSION

DB = os.getenv('PAPER_V17_DB_PATH', '/app/data/paper_v17/observer_v17.db')
MAX_HEARTBEAT_AGE_SECONDS = 180


def parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def main() -> int:
    result = {
        'schema': 'POROTA_RC6_FUNCTIONAL_HEALTH_V1',
        'version': VERSION,
        'mode': MODE,
        'execution': EXECUTION,
        'real_order_capability': REAL_ORDER_CAPABILITY,
        'read_only': True,
        'network_order_test_performed': False,
    }
    try:
        c = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        qc = c.execute('PRAGMA quick_check').fetchone()[0]
        row = c.execute('SELECT mode,process_state,session_state,ppi_auth,real_orders_sent,heartbeat_at,last_market_data_at FROM observer_state WHERE id=1').fetchone()
        c.close()
        state = dict(row) if row else {}
        hb = parse_time(state.get('heartbeat_at'))
        age = None if hb is None else max(0.0, (datetime.now(timezone.utc) - hb.astimezone(timezone.utc)).total_seconds())
        checks = {
            'quick_check': qc == 'ok',
            'mode': state.get('mode') == 'PRODUCTION_PAPER',
            'real_orders_sent': int(state.get('real_orders_sent') or 0) == 0,
            'release_identity': MODE == 'PRODUCTION_PAPER' and EXECUTION == 'SIMULATED' and REAL_ORDER_CAPABILITY == 'BLOCKED',
            'heartbeat_fresh': age is not None and age <= MAX_HEARTBEAT_AGE_SECONDS,
        }
        result.update(quick_check=qc, observer_state=state, heartbeat_age_seconds=age, checks=checks)
        result['status'] = 'GREEN' if all(checks.values()) else 'RED'
    except Exception as exc:
        result.update(status='RED', error=f'{type(exc).__name__}:{exc}')
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result['status'] == 'GREEN' else 2


if __name__ == '__main__':
    raise SystemExit(main())
