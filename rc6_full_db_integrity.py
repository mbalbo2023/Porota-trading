#!/usr/bin/env python3
"""RC6 full SQLite integrity probe (read-only, expensive by design).

This probe owns PRAGMA quick_check. It is intended for explicit preopen,
postclose or lower-frequency integrity windows, not the 5-minute liveness loop.
"""
from __future__ import annotations
import json, os, sqlite3, time
DB=os.getenv('PAPER_V17_DB_PATH','/app/data/paper_v17/observer_v17.db')


def probe(db_path=DB):
    t=time.perf_counter(); result={'schema':'POROTA_RC6_FULL_DB_INTEGRITY_V1','read_only':True,'check':'PRAGMA quick_check'}
    try:
        c=sqlite3.connect(f'file:{db_path}?mode=ro',uri=True,timeout=30); c.execute('PRAGMA query_only=ON')
        qc=c.execute('PRAGMA quick_check').fetchone()[0]
        real=c.execute('SELECT real_orders_sent FROM observer_state WHERE id=1').fetchone()
        c.close(); result.update(quick_check=qc,real_orders_sent=int(real[0] if real else -1),status='GREEN' if qc=='ok' and int(real[0] if real else -1)==0 else 'RED')
    except Exception as exc:
        result.update(status='RED',error=f'{type(exc).__name__}:{exc}')
    result['elapsed_seconds']=time.perf_counter()-t
    return result


def main():
    r=probe(); print(json.dumps(r,sort_keys=True,default=str)); return 0 if r['status']=='GREEN' else 2

if __name__=='__main__': raise SystemExit(main())
