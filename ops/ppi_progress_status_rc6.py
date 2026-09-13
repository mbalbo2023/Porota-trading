#!/usr/bin/env python3
"""Read-only progress reporter for PPI API and PPI Web residual stages.

Purpose: make server state, not chat history, the source of truth. Produces a
compact machine-readable JSON plus a human status line with progress, recent
throughput and an ETA estimate only when recent completions support one.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

API_RUN_ID="PPI-HIST-20260912-001"
API_TERMINAL={"ALREADY_COVERED","DONE_VALID","DONE_PARTIAL","DONE_EMPTY","ERROR"}
WEB_TERMINAL={"DONE_VALID","DONE_PARTIAL","DONE_EMPTY","ERROR"}


def parse_dt(value):
    if not value: return None
    try:
        dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception: return None


def estimate_eta(finished_values, pending:int, now=None):
    now=now or datetime.now(timezone.utc)
    recent=[]
    for v in finished_values:
        dt=parse_dt(v)
        if dt and 0 <= (now-dt).total_seconds() <= 3600: recent.append(dt)
    n=len(recent)
    rate=n  # completions/hour over fixed trailing 60m window
    if pending <= 0:
        return {"rate_per_hour":float(rate),"eta_minutes":0,"confidence":"HIGH","sample_last_hour":n}
    if rate <= 0:
        return {"rate_per_hour":0.0,"eta_minutes":None,"confidence":"UNAVAILABLE","sample_last_hour":0}
    confidence="HIGH" if n>=30 else "MEDIUM" if n>=10 else "LOW"
    return {"rate_per_hour":float(rate),"eta_minutes":round(pending/rate*60,1),"confidence":confidence,"sample_last_hour":n}


def api_status(db_path:Path, expected:int=1960):
    con=sqlite3.connect(f"file:{db_path}?mode=ro",uri=True,timeout=15)
    con.row_factory=sqlite3.Row
    try:
        states={r[0]:int(r[1]) for r in con.execute("SELECT state,count(*) FROM ppi_history_ingest_tasks WHERE run_id=? GROUP BY state",(API_RUN_ID,))}
        runtime=con.execute("SELECT * FROM ppi_history_ingest_runtime WHERE run_id=?",(API_RUN_ID,)).fetchone()
        finished=[r[0] for r in con.execute("SELECT finished_at FROM ppi_history_ingest_tasks WHERE run_id=? AND finished_at IS NOT NULL",(API_RUN_ID,))]
    finally: con.close()
    done=sum(states.get(s,0) for s in ("ALREADY_COVERED","DONE_VALID","DONE_PARTIAL","DONE_EMPTY"))
    errors=states.get("ERROR",0)
    pending=sum(states.get(s,0) for s in ("PENDING","RETRYABLE","RUNNING"))
    terminal=done+errors
    eta=estimate_eta(finished,pending)
    heartbeat=str(runtime["heartbeat"]) if runtime else None
    hb=parse_dt(heartbeat); age=(datetime.now(timezone.utc)-hb).total_seconds() if hb else None
    disk=float(runtime["disk_free_gib"]) if runtime else None
    safety=str(runtime["safety"]) if runtime else None
    status=str(runtime["status"]) if runtime else "UNKNOWN"
    healthy=(age is not None and age < 180 and safety=="PRODUCTION_PAPER|0" and (disk is None or disk>=3.0))
    sem="GREEN" if healthy else "RED" if (safety not in (None,"PRODUCTION_PAPER|0") or (disk is not None and disk<2.0)) else "YELLOW"
    return {"stage":"PPI_API","run_id":API_RUN_ID,"status":status,"semaphore":sem,"expected":expected,"done":done,"errors":errors,"pending":pending,"terminal":terminal,"progress_pct":round(100*terminal/expected,2) if expected else 100.0,"family":str(runtime["family"] or "") if runtime else "","batch":int(runtime["last_committed_batch"] or 0) if runtime else 0,"heartbeat":heartbeat,"heartbeat_age_sec":round(age,1) if age is not None else None,"rows_canonical":int(runtime["rows_canonical"] or 0) if runtime else None,"disk_free_gib":round(disk,3) if disk is not None else None,"safety":safety,"eta":eta,"state_counts":dict(sorted(states.items()))}


def web_status(db_path:Path,run_id:str):
    con=sqlite3.connect(f"file:{db_path}?mode=ro",uri=True,timeout=15)
    try:
        run=con.execute("SELECT manifest_rows,status,heartbeat FROM residual_run WHERE run_id=?",(run_id,)).fetchone()
        if not run: raise RuntimeError("WEB_RUN_NOT_FOUND")
        states={r[0]:int(r[1]) for r in con.execute("SELECT state,count(*) FROM residual_task WHERE run_id=? GROUP BY state",(run_id,))}
        finished=[r[0] for r in con.execute("SELECT finished_at FROM residual_task WHERE run_id=? AND finished_at IS NOT NULL",(run_id,))]
        cur=con.execute("SELECT symbol,instrument_type,market,settlement FROM residual_task WHERE run_id=? AND state='RUNNING' ORDER BY started_at LIMIT 1",(run_id,)).fetchone()
    finally: con.close()
    total=int(run[0]); terminal=sum(states.get(s,0) for s in WEB_TERMINAL); pending=states.get("PENDING",0)+states.get("RUNNING",0)
    eta=estimate_eta(finished,pending)
    hb=parse_dt(run[2]); age=(datetime.now(timezone.utc)-hb).total_seconds() if hb else None
    sem="GREEN" if age is not None and age<180 else "YELLOW" if age is not None and age<600 else "RED"
    return {"stage":"PPI_WEB","run_id":run_id,"status":run[1],"semaphore":sem,"total":total,"terminal":terminal,"pending":pending,"progress_pct":round(100*terminal/total,2) if total else 100.0,"heartbeat":run[2],"heartbeat_age_sec":round(age,1) if age is not None else None,"current":list(cur) if cur else None,"eta":eta,"state_counts":dict(sorted(states.items()))}


def status_line(x):
    eta=x["eta"]; em="completed" if eta["eta_minutes"]==0 else "ETA unavailable" if eta["eta_minutes"] is None else f"ETA~{eta['eta_minutes']}m ({eta['confidence']})"
    base=f"{x['semaphore']} {x['stage']} {x['progress_pct']}% terminal={x.get('terminal')} pending={x.get('pending')} rate={eta['rate_per_hour']:.0f}/h {em}"
    if x["stage"]=="PPI_API": base+=f" family={x.get('family') or '-'} errors={x.get('errors')} disk={x.get('disk_free_gib')}GiB safety={x.get('safety')}"
    elif x.get("current"): base+=f" current={'|'.join(map(str,x['current']))}"
    return base


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--stage",choices=("api","web"),required=True)
    ap.add_argument("--db",type=Path,required=True)
    ap.add_argument("--run-id")
    ap.add_argument("--expected",type=int,default=1960)
    args=ap.parse_args()
    x=api_status(args.db,args.expected) if args.stage=="api" else web_status(args.db,args.run_id or "PPI-WEB-RESIDUAL-RC6")
    print("STATUS_LINE="+status_line(x))
    print("STATUS_JSON="+json.dumps(x,ensure_ascii=False,sort_keys=True))

if __name__=="__main__": main()
