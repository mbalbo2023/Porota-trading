#!/usr/bin/env python3
"""Generate exact PPI Web residual after the PPI API historical run closes.

Read-only SQLite only. No observer, broker, network or requests imports. It fails
closed while any API task remains PENDING/RETRYABLE/RUNNING.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

RUN_ID = "PPI-HIST-20260912-001"
ACTIVE_STATES = {"PENDING", "RETRYABLE", "RUNNING"}


def runtime_db_path() -> Path:
    explicit = str(os.getenv("PPI_HISTORY_RUNTIME_DB", "") or os.getenv("PAPER_V17_DB_PATH", "")).strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    return (data_dir / "paper_v17" / "observer_v17.db").resolve()


def classify_task(state: str, provider_rows: int, valid_rows: int) -> str | None:
    state = str(state or "").upper(); provider_rows = int(provider_rows or 0); valid_rows = int(valid_rows or 0)
    if state == "ERROR": return "HARD_PROVIDER_ERROR"
    if state == "DONE_PARTIAL": return "PARTIAL_VALID"
    if state == "DONE_EMPTY": return "NO_PROVIDER_ROWS" if provider_rows == 0 else "PROVIDER_INVALID"
    return None


def build_manifest(rows: list[tuple]) -> list[dict[str, object]]:
    out=[]; seen=set()
    for row in rows:
        symbol,family,market,settlement,state,provider_rows,valid_rows,last_error,result=row
        key=tuple(str(x or "").strip().upper() for x in (symbol,family,market,settlement))
        if key in seen: raise RuntimeError(f"duplicate task identity: {key}")
        seen.add(key); residual_class=classify_task(str(state),int(provider_rows or 0),int(valid_rows or 0))
        if residual_class is None: continue
        out.append({
            "symbol":key[0],"instrument_type":key[1],"market":key[2],"settlement":key[3],
            "residual_class":residual_class,"api_state":str(state),"provider_rows":int(provider_rows or 0),
            "valid_rows":int(valid_rows or 0),"api_result":str(result or ""),"api_error":str(last_error or ""),
        })
    return out


def load_from_runtime(db_path: Path | None = None) -> list[dict[str, object]]:
    path=(db_path or runtime_db_path()).resolve()
    if not path.is_file(): raise RuntimeError(f"RUNTIME_DB_MISSING:{path}")
    c=sqlite3.connect(f"file:{path}?mode=ro",uri=True,timeout=30)
    try:
        c.execute("PRAGMA query_only=ON")
        qc=c.execute("PRAGMA quick_check").fetchone()[0]
        if qc != "ok": raise RuntimeError(f"RUNTIME_DB_QUICK_CHECK_FAILED:{qc}")
        state_rows=c.execute("SELECT state,count(*) FROM ppi_history_ingest_tasks WHERE run_id=? GROUP BY state",(RUN_ID,)).fetchall()
        state_counts={str(s).upper():int(n) for s,n in state_rows}
        active=sum(state_counts.get(s,0) for s in ACTIVE_STATES)
        if active: raise RuntimeError(f"API ingestion still active: {active} tasks remain")
        total=sum(state_counts.values())
        if total != 1960: raise RuntimeError(f"UNIVERSE_COUNT_MISMATCH:{total}")
        rows=c.execute("""SELECT symbol,instrument_type,market,settlement,state,
                                  provider_rows,valid_rows,last_error,result
                           FROM ppi_history_ingest_tasks WHERE run_id=?
                           ORDER BY instrument_type,symbol,market,settlement""",(RUN_ID,)).fetchall()
    finally:
        c.close()
    return build_manifest(list(rows))


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row,ensure_ascii=False,sort_keys=True)+"\n" for row in rows),encoding="utf-8")


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--output",type=Path,required=True); ap.add_argument("--db",type=Path)
    args=ap.parse_args(); rows=load_from_runtime(args.db); write_jsonl(args.output,rows)
    by_class={}
    for row in rows: by_class[str(row["residual_class"])]=by_class.get(str(row["residual_class"]),0)+1
    print(json.dumps({"run_id":RUN_ID,"residual_identities":len(rows),"by_class":dict(sorted(by_class.items())),
                      "output":str(args.output),"mode":"READ_ONLY_MANIFEST_GENERATION","db":str((args.db or runtime_db_path()).resolve())},
                     ensure_ascii=False,sort_keys=True))
    return 0

if __name__ == "__main__": raise SystemExit(main())
