#!/usr/bin/env python3
"""Read-only due-check for RC4 Contract Evidence subjobs."""
from __future__ import annotations
import json, os, sqlite3
from datetime import datetime, timezone
from rc4_contract_schedule import JOB_TO_CADENCE, due

DB=os.getenv('PAPER_V17_DB_PATH','/app/data/paper_v17/observer_v17.db')

def main():
    result={key:None for key in JOB_TO_CADENCE}
    try:
        c=sqlite3.connect(f'file:{DB}?mode=ro',uri=True,timeout=10); c.row_factory=sqlite3.Row
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'contract_evidence_v2_runs' in tables:
            for key in result:
                row=c.execute("SELECT started_at FROM contract_evidence_v2_runs WHERE job_key=? ORDER BY started_at DESC LIMIT 1",(key,)).fetchone()
                result[key]=row[0] if row else None
        c.close()
    except Exception:
        pass
    now=datetime.now(timezone.utc)
    due_jobs=[key for key,last in result.items() if due(last,key,now)]
    print(json.dumps({'state':'OK','due_jobs':due_jobs,'last_runs':result,'generated_at':now.isoformat()},sort_keys=True))
    return 0
if __name__=='__main__': raise SystemExit(main())
