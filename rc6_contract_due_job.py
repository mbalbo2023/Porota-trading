#!/usr/bin/env python3
"""Read-only due-check for RC6 Contract Evidence jobs."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

from rc6_contract_schedule import JOB_TO_CADENCE, due

DB = os.getenv("PAPER_V17_DB_PATH", "/opt/porota-trading/data/paper_v17/observer_v17.db")


def main():
    last = {key: None for key in JOB_TO_CADENCE}
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "contract_evidence_v2_runs" in tables:
            for key in last:
                row = c.execute(
                    "SELECT started_at FROM contract_evidence_v2_runs WHERE job_key=? ORDER BY started_at DESC LIMIT 1",
                    (key,),
                ).fetchone()
                last[key] = row[0] if row else None
        c.close()
    except Exception as exc:
        print(json.dumps({"state":"RED","reason":type(exc).__name__,"due_jobs":[],"last_runs":last}, sort_keys=True))
        return 2
    now = datetime.now(timezone.utc)
    due_jobs = [key for key, stamp in last.items() if due(stamp, key, now)]
    print(json.dumps({"state":"OK","due_jobs":due_jobs,"last_runs":last,"generated_at":now.isoformat(),"read_only":True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
