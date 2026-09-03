#!/usr/bin/env python3
"""Lightweight read-only functional health snapshot for RC4.

Designed for a five-minute host timer.  It performs no network calls, no Docker
operations and opens the observer SQLite database in ``mode=ro`` with
``PRAGMA query_only=ON``.  Its only write is an atomic sanitized JSON snapshot
under ``data/functional_health`` for the dashboard.

The hourly/deep introspection remains a separate job.  Keeping the lightweight
and deep checks separate avoids running expensive diagnostics every five
minutes while still detecting safety/runtime regressions quickly.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile

DEFAULT_DB=os.getenv("PAPER_V17_DB_PATH","/opt/porota-trading/data/paper_v17/observer_v17.db")
DEFAULT_OUTPUT=os.getenv("POROTA_FUNCTIONAL_HEALTH_SNAPSHOT",
    "/opt/porota-trading/data/functional_health/latest.json")


def _dt(value):
    if not value:
        return None
    try:
        result=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        if result.tzinfo is None:
            result=result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except (TypeError,ValueError):
        return None


def _age(value, now):
    dt=_dt(value)
    return None if dt is None else max(0.0,(now-dt).total_seconds())


def _tables(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _rows(c,sql,args=()):
    return [dict(r) for r in c.execute(sql,args)]


def _one(c,sql,args=(),default=None):
    row=c.execute(sql,args).fetchone()
    return row[0] if row and row[0] is not None else default


def collect(db_path: str, *, now=None) -> dict:
    now_dt=_dt(now) if now is not None else datetime.now(timezone.utc)
    if now_dt is None:
        now_dt=datetime.now(timezone.utc)
    result={
        "schema":"porota-functional-health-rc4-v1",
        "generated_at":now_dt.isoformat(),
        "db_path":str(db_path),
        "checks":{},"warnings":[],"critical":[],
    }
    c=sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro",uri=True,timeout=5)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    try:
        tables=_tables(c)
        quick=str(_one(c,"PRAGMA quick_check",default="UNKNOWN"))
        result["checks"]["sqlite_quick_check"]=quick
        if quick.lower()!="ok":
            result["critical"].append("SQLITE_QUICK_CHECK_NOT_OK")

        observer=dict(c.execute("SELECT * FROM observer_state WHERE id=1").fetchone() or {}) \
            if "observer_state" in tables else {}
        heartbeat_age=_age(observer.get("heartbeat_at"),now_dt)
        observer_public={k:observer.get(k) for k in (
            "mode","process_state","session_state","ppi_auth","heartbeat_at",
            "last_market_data_at","real_orders_sent","http_allowed","http_blocked","detail")}
        observer_public["heartbeat_age_seconds"]=heartbeat_age
        result["observer"]=observer_public
        if str(observer.get("mode") or "")!="PRODUCTION_PAPER":
            result["critical"].append("MODE_NOT_PRODUCTION_PAPER")
        if int(observer.get("real_orders_sent") or 0)!=0:
            result["critical"].append("REAL_ORDERS_NONZERO")
        if heartbeat_age is None or heartbeat_age>180:
            result["critical"].append("OBSERVER_HEARTBEAT_STALE")
        if str(observer.get("session_state") or "").upper()=="MARKET_OPEN" and str(observer.get("ppi_auth") or "")!="OK":
            result["critical"].append("PPI_AUTH_NOT_OK_DURING_MARKET")

        open_positions=[]
        if "paper_positions" in tables:
            open_positions=_rows(c,"""SELECT paper_id,symbol,asset_class,currency,market,
                settlement,opened_at FROM paper_positions WHERE status='OPEN'
                ORDER BY opened_at""")
        marks={}
        if "paper_position_marks" in tables:
            marks={r["paper_id"]:dict(r) for r in c.execute("SELECT * FROM paper_position_marks")}
        stale=[]; missing=[]
        for position in open_positions:
            mark=marks.get(position["paper_id"])
            if not mark:
                missing.append(position["paper_id"]); continue
            age=_age(mark.get("book_at") or mark.get("marked_at"),now_dt)
            if age is None or age>180:
                stale.append({"paper_id":position["paper_id"],"symbol":position["symbol"],"age_seconds":age})
        result["positions"]={
            "open":len(open_positions),"missing_marks":len(missing),
            "stale_marks":len(stale),"stale_sample":stale[:20],
        }
        if missing:
            result["warnings"].append(f"OPEN_POSITIONS_WITHOUT_MARK={len(missing)}")
        if stale:
            result["warnings"].append(f"OPEN_POSITIONS_WITH_STALE_MARK={len(stale)}")

        if open_positions and "paper_exit_intents" in tables:
            supervised={r[0] for r in c.execute("SELECT paper_id FROM paper_exit_intents")}
            unsupervised=[p["paper_id"] for p in open_positions if p["paper_id"] not in supervised]
        else:
            unsupervised=[] if not open_positions else [p["paper_id"] for p in open_positions]
        result["positions"]["without_exit_intent"]=len(unsupervised)
        if unsupervised:
            result["critical"].append(f"OPEN_POSITIONS_WITHOUT_EXIT_INTENT={len(unsupervised)}")

        worker_tables=(
            ("supervisor","paper_supervisor_state"),
            ("exit_reader","paper_exit_reader_state"),
            ("candles","candle_worker_state"),
            ("scalping","intraday_scalping_worker_state"),
            ("telegram","paper_notification_worker"),
        )
        workers={}
        market_open=str(observer.get("session_state") or "").upper()=="MARKET_OPEN"
        for label,table in worker_tables:
            if table not in tables:
                workers[label]={"state":"NOT_INSTALLED","heartbeat_at":None,"heartbeat_age_seconds":None}
                continue
            row=dict(c.execute(f"SELECT * FROM {table} WHERE id=1").fetchone() or {})
            age=_age(row.get("heartbeat_at"),now_dt)
            workers[label]={"state":row.get("state"),"heartbeat_at":row.get("heartbeat_at"),
                            "heartbeat_age_seconds":age,"detail":row.get("detail")}
            limit=300 if label=="scalping" else 90
            if market_open and (age is None or age>limit):
                result["warnings"].append(f"WORKER_STALE_{label.upper()}")
        result["workers"]=workers

        if "paper_daily_risk" in tables:
            risk=[]
            # One latest row per currency by evaluated_at; no cross-currency sum.
            currencies=[r[0] for r in c.execute("SELECT DISTINCT currency FROM paper_daily_risk ORDER BY currency")]
            for currency in currencies:
                row=c.execute("""SELECT * FROM paper_daily_risk WHERE currency=?
                    ORDER BY evaluated_at DESC LIMIT 1""",(currency,)).fetchone()
                if row:
                    risk.append(dict(row))
            result["daily_risk_by_currency"]=risk
            for row in risk:
                if str(row.get("state") or "").upper() not in {"READY","OK"}:
                    result["warnings"].append(
                        f"DAILY_RISK_{str(row.get('currency') or 'UNKNOWN').upper()}_{str(row.get('state') or 'UNKNOWN').upper()}")

        health=_rows(c,"SELECT component,state,checked_at,last_success_at,detail FROM api_health ORDER BY component") \
            if "api_health" in tables else []
        result["api_health"]=health
        result["api_non_green"]=[x for x in health if str(x.get("state") or "").upper() not in {"OK","VERDE","READY","NO_APLICA"}]

        sync=_rows(c,"SELECT source,status,last_attempt_at,last_success_at,items,detail FROM source_sync ORDER BY source") \
            if "source_sync" in tables else []
        result["source_sync"]=sync

        result["history"]={
            "legacy_instruments":_one(c,"SELECT COUNT(*) FROM production_history WHERE row_count>0",default=0)
                if "production_history" in tables else 0,
            "legacy_rows":_one(c,"SELECT COALESCE(SUM(row_count),0) FROM production_history",default=0)
                if "production_history" in tables else 0,
            "legacy_last_download":_one(c,"SELECT MAX(downloaded_at) FROM production_history")
                if "production_history" in tables else None,
        }
        if "contract_evidence_runs" in tables:
            row=c.execute("SELECT * FROM contract_evidence_runs ORDER BY rowid DESC LIMIT 1").fetchone()
            result["contract_evidence_latest_run"]=dict(row) if row else None

        result["checks"]["db_bytes"]=Path(db_path).stat().st_size if Path(db_path).exists() else None
        wal=Path(str(db_path)+"-wal")
        result["checks"]["wal_bytes"]=wal.stat().st_size if wal.exists() else 0
    finally:
        c.close()

    result["state"]="CRITICAL" if result["critical"] else "WARN" if result["warnings"] else "OK"
    return result


def atomic_json(path: Path, payload: dict) -> None:
    path=path.resolve(); path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=f".{path.name}.",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(payload,stream,ensure_ascii=False,indent=2,default=str)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp,path)
    finally:
        try: os.unlink(tmp)
        except FileNotFoundError: pass


def main(argv=None) -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--db",default=DEFAULT_DB)
    parser.add_argument("--output",default=DEFAULT_OUTPUT)
    args=parser.parse_args(argv)
    payload=collect(args.db)
    atomic_json(Path(args.output),payload)
    print(json.dumps({
        "state":payload["state"],"generated_at":payload["generated_at"],
        "critical":payload["critical"],"warnings":payload["warnings"],
        "output":str(Path(args.output).resolve())},ensure_ascii=False,sort_keys=True))
    return 2 if payload["state"]=="CRITICAL" else 0


if __name__=="__main__":
    raise SystemExit(main())
