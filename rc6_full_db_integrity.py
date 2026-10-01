#!/usr/bin/env python3
"""RC6 full SQLite integrity probe (read-only, expensive by design).

This probe owns PRAGMA quick_check. It is intended for explicit preopen,
postclose or lower-frequency integrity windows, not the 5-minute liveness loop.

The systemd entry point defers the expensive scan while the market is open.
Direct probe() callers keep the historical behavior unless they explicitly
request that deferral, preserving deterministic test/audit use.
"""
from __future__ import annotations
import json, os, sqlite3, time

DB=os.getenv('PAPER_V17_DB_PATH','/app/data/paper_v17/observer_v17.db')


def probe(db_path=DB, *, defer_when_market_open=False):
    t=time.perf_counter()
    result={
        'schema':'POROTA_RC6_FULL_DB_INTEGRITY_V1',
        'read_only':True,
        'check':'PRAGMA quick_check',
    }
    try:
        c=sqlite3.connect(f'file:{db_path}?mode=ro',uri=True,timeout=30)
        c.execute('PRAGMA query_only=ON')
        state=c.execute(
            'SELECT real_orders_sent,session_state FROM observer_state WHERE id=1'
        ).fetchone()
        real_orders=int(state[0] if state else -1)
        session_state=str(state[1] if state and state[1] is not None else 'UNKNOWN')
        result.update(real_orders_sent=real_orders,session_state=session_state)

        if real_orders != 0:
            result.update(
                quick_check='NOT_RUN_SAFETY',
                state='SAFETY_BLOCK',
                status='RED',
            )
        elif defer_when_market_open and session_state.upper() == 'MARKET_OPEN':
            result.update(
                quick_check='DEFERRED_MARKET_OPEN',
                state='DEFERRED_MARKET_OPEN',
                status='GREEN',
            )
        else:
            qc=c.execute('PRAGMA quick_check').fetchone()[0]
            result.update(
                quick_check=qc,
                state='COMPLETED',
                status='GREEN' if qc=='ok' else 'RED',
            )
        c.close()
    except Exception as exc:
        result.update(status='RED',state='ERROR',error=f'{type(exc).__name__}:{exc}')
    result['elapsed_seconds']=time.perf_counter()-t
    return result


def main():
    r=probe(defer_when_market_open=True)
    print(json.dumps(r,sort_keys=True,default=str))
    return 0 if r['status']=='GREEN' else 2


if __name__=='__main__':
    raise SystemExit(main())
