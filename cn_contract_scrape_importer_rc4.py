"""Import sanitized authenticated-PPI scraper output into Contract Evidence v2.

This importer intentionally records FAMILY/ROUTE discovery evidence only. It
does not guess ticker contracts from HTML tables and never calls readiness
automatically. Structured XHR/API normalization is a separate RC4 step.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from cp_contract_evidence_v2_hf6 import finish_run, record_snapshot, start_run
from cq_contract_readiness_hf6 import canonical_family

SCRAPER_SCHEMA="porota-ppi-authenticated-family-evidence-v1"
JOB_KEY="PPI_AUTHENTICATED_WEB_EVIDENCE"
SOURCE_CLASS="PPI_AUTHENTICATED_WEB"


class SQLiteStore:
    def __init__(self,path):
        self.path=str(path)
    def connect(self):
        c=sqlite3.connect(self.path,timeout=20)
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c


def _safe_source(source: dict) -> dict:
    tables=[]
    for table in (source.get("tables") or [])[:20]:
        if not isinstance(table,dict):
            continue
        tables.append({
            "headers":[str(x)[:120] for x in (table.get("headers") or [])[:80]],
            "row_count_sampled":int(table.get("row_count_sampled") or 0),
        })
    return {
        "evidence_scope":"FAMILY_ROUTE_DISCOVERY_UNNORMALIZED",
        "route":str(source.get("route") or "")[:300],
        "http":int(source.get("http") or 0),
        "final_url":str(source.get("final_url") or "")[:500],
        "authenticated_target_reached":bool(source.get("authenticated_target_reached")),
        "title":str(source.get("title") or "")[:250],
        "table_count":int(source.get("table_count") or 0),
        "detected_fields":[str(x)[:120] for x in (source.get("detected_fields") or [])[:100]],
        "tables":tables,
        "automatic_ready_paper":False,
    }


def _route_identity(route: str) -> str:
    digest=hashlib.sha256(str(route).encode("utf-8")).hexdigest()[:16].upper()
    return "__ROUTE__"+digest


def import_payload(store, payload: dict, *, run_id: str) -> dict:
    if not isinstance(payload,dict) or payload.get("schema") != SCRAPER_SCHEMA:
        raise ValueError("PPI_WEB_SCRAPE_SCHEMA_INVALID")
    safety=payload.get("safety") or {}
    if int(safety.get("order_posts",0) or 0) != 0 or int(safety.get("mutation_requests",0) or 0) != 0:
        raise ValueError("PPI_WEB_SCRAPE_SAFETY_VIOLATION")
    if any(bool(safety.get(key)) for key in ("tokens_persisted","cookies_persisted","raw_html_persisted","password_persisted")):
        raise ValueError("PPI_WEB_SCRAPE_SECRET_PERSISTENCE_VIOLATION")

    auth_state=str((payload.get("auth") or {}).get("status") or "UNKNOWN")[:120]
    start_run(store,run_id=run_id,job_key=JOB_KEY,source_class=SOURCE_CLASS,
              auth_state=auth_state,started_at=payload.get("observed_at"),
              detail="Importación de evidencia web sanitizada; sin promoción automática.")

    counters=Counter()
    try:
        if auth_state != "AUTHENTICATED":
            finish_run(store,run_id=run_id,state="BLOCKED_AUTH",auth_state=auth_state,
                       blocked=1,detail="Autenticación web no reutilizable/2FA/error; fail-closed.")
            return {"state":"BLOCKED_AUTH","auth_state":auth_state,"recorded":0,"changed":0}

        for family,info in sorted((payload.get("families") or {}).items()):
            canonical=canonical_family(family)
            if not isinstance(info,dict):
                counters["errors"]+=1
                continue
            for source in info.get("sources") or ():
                counters["observed"]+=1
                if not isinstance(source,dict) or not source.get("authenticated_target_reached"):
                    counters["blocked"]+=1
                    continue
                evidence=_safe_source(source)
                route=evidence.get("route") or "PPI_AUTHENTICATED_WEB"
                result=record_snapshot(
                    store,family=canonical,ticker=_route_identity(route),market="PPI_WEB",
                    source_class=SOURCE_CLASS,source_ref=route,evidence=evidence,
                    observed_at=payload.get("observed_at"),
                )
                counters["recorded"]+=1
                if result.get("changed"):
                    counters["changed"]+=1

        state="OK" if counters["errors"] == 0 else "PARTIAL"
        finish_run(store,run_id=run_id,state=state,auth_state=auth_state,
                   observed=counters["observed"],recorded=counters["recorded"],
                   changed=counters["changed"],blocked=counters["blocked"],
                   errors=counters["errors"],
                   detail="Evidencia web de familia/ruta versionada; requiere normalización contractual para readiness.")
        return {"state":state,"auth_state":auth_state,**dict(counters)}
    except Exception as exc:
        try:
            finish_run(store,run_id=run_id,state="ERROR",auth_state=auth_state,
                       observed=counters["observed"],recorded=counters["recorded"],
                       changed=counters["changed"],blocked=counters["blocked"],
                       errors=counters["errors"]+1,
                       detail=f"{type(exc).__name__}: importación abortada")
        finally:
            raise


def main(argv=None) -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--db",required=True)
    parser.add_argument("--input",required=True)
    parser.add_argument("--run-id",default="")
    args=parser.parse_args(argv)
    payload=json.loads(Path(args.input).read_text(encoding="utf-8"))
    run_id=args.run_id or ("ppi-web-"+uuid.uuid4().hex)
    result=import_payload(SQLiteStore(args.db),payload,run_id=run_id)
    print(json.dumps({"run_id":run_id,**result},ensure_ascii=False,sort_keys=True))
    return 0 if result.get("state") in {"OK","PARTIAL","BLOCKED_AUTH"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
