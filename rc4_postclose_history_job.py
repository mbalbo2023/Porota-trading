#!/usr/bin/env python3
from __future__ import annotations
import os,sqlite3,json
import cs_postclose_history_scheduler_hf6 as scheduler
class Store:
    def __init__(self,path): self.path=path
    def connect(self):
        c=sqlite3.connect(self.path,timeout=30); c.row_factory=sqlite3.Row; return c

def main():
    store=Store(os.getenv('PAPER_V17_DB_PATH','/app/data/paper_v17/observer_v17.db'))
    with store.connect() as c:
        row=c.execute('SELECT session_state,real_orders_sent FROM observer_state WHERE id=1').fetchone()
    if not row or int(row[1] or 0)!=0: raise SystemExit('FAIL_CLOSED_RUNTIME_INVARIANT')
    result=scheduler.run_if_due(store,phase=str(row[0] or 'UNKNOWN'))
    print(json.dumps(result,ensure_ascii=False,sort_keys=True,default=str)); return 0
if __name__=='__main__': raise SystemExit(main())
