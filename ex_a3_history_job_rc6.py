#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sqlite3
import sys

import ew_a3_history_rc6 as runner


class Store:
    def __init__(self,path): self.path=path
    def connect(self):
        c=sqlite3.connect(self.path,timeout=30)
        c.row_factory=sqlite3.Row
        return c


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    mode=(argv[0] if argv else os.getenv('A3_HISTORY_MODE','DAILY_INCREMENTAL')).upper()
    result=runner.run(
        Store(os.getenv('PAPER_V17_DB_PATH','/app/data/paper_v17/observer_v17.db')),
        mode=mode,
        batch_limit=int(os.getenv('A3_HISTORY_BATCH_LIMIT','40')),
        throttle_seconds=float(os.getenv('A3_HISTORY_THROTTLE_SECONDS','1.0')),
    )
    print(json.dumps(result,ensure_ascii=False,sort_keys=True,default=str))
    if not result.get('ran'):
        return 0 if result.get('reason')=='RUNTIME_NOT_CLOSED_OR_SAFE' else 2
    return 2 if int(result.get('failed') or 0)>0 else 0

if __name__=='__main__': raise SystemExit(main())
