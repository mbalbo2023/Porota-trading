#!/usr/bin/env python3
from __future__ import annotations
import os,sqlite3,json
import rc4_a3_cem_history_runner as runner
class Store:
    def __init__(self,path): self.path=path
    def connect(self):
        c=sqlite3.connect(self.path,timeout=30); c.row_factory=sqlite3.Row; return c

def main():
    result=runner.run(Store(os.getenv('PAPER_V17_DB_PATH','/app/data/paper_v17/observer_v17.db')))
    print(json.dumps(result,ensure_ascii=False,sort_keys=True,default=str)); return 0
if __name__=='__main__': raise SystemExit(main())
