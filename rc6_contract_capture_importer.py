#!/usr/bin/env python3
"""Import a sanitized RC6 trusted-browser capture into Contract Evidence v2.

Executed inside the observer container. It writes evidence/audit tables only;
it never changes candidate eligibility and cannot activate an instrument.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from pathlib import Path

import cp_contract_evidence_v2_hf6 as ce

DB = "/app/data/paper_v17/observer_v17.db"


class Store:
    def __init__(self, path): self.path = path
    def connect(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c


def nonempty(data):
    return {k:v for k,v in data.items() if v not in (None,"",[],{})}


def pick(obj, names):
    wanted = {x.lower().replace("-", "_") for x in names}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower().replace("-", "_") in wanted and v not in (None,"",[],{}):
                return v
        for v in obj.values():
            found = pick(v, names)
            if found not in (None,"",[],{}): return found
    elif isinstance(obj, list):
        for v in obj:
            found = pick(v, names)
            if found not in (None,"",[],{}): return found
    return None


def candidate_map(store):
    with store.connect() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "candidate_universe" not in tables:
            return {}
        out = {}
        for row in c.execute("SELECT ticker,instrument_type,market,settlement,status FROM candidate_universe WHERE status='AVAILABLE'"):
            item = dict(row)
            out.setdefault(str(item.get("ticker") or "").upper(), []).append(item)
        return out


def main():
    if len(sys.argv) != 2:
        raise SystemExit("USAGE:capture.json")
    raw = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if raw.get("schema") != "POROTA_RC6_PPI_TRUSTED_CONTRACT_V1":
        raise SystemExit("CAPTURE_SCHEMA_INVALID")
    if raw.get("auth_status") != "AUTHENTICATED_TRUSTED_DEVICE":
        raise SystemExit("CAPTURE_NOT_AUTHENTICATED")
    if int(raw.get("real_orders_sent") or 0) != 0:
        raise SystemExit("CAPTURE_ORDER_INVARIANT_FAILED")

    jobs = list(dict.fromkeys(raw.get("jobs") or []))
    store = Store(DB)
    cmap = candidate_map(store)
    run_ids = {}
    for job in jobs:
        rid = "rc6-ce-" + uuid.uuid4().hex
        run_ids[job] = rid
        ce.start_run(store, run_id=rid, job_key=job,
                     detail="RC6 authenticated GET/XHR evidence; no auto activation")

    total = changed = conflicts = 0
    notes = []
    for _, item in (raw.get("endpoints") or {}).items():
        kind = item.get("kind")
        job = str(item.get("observed_job") or "")
        route = str(item.get("observed_route") or "")
        source = str(item.get("source_url") or "PPI_AUTHENTICATED_XHR")

        if kind == "InstrumentosOperables":
            for row in item.get("rows") or []:
                ticker = str(row.get("ticker") or "").upper()
                if not ticker: continue
                evidence = nonempty({k:v for k,v in row.items() if k != "ticker"})
                evidence.update({"evidence_scope":"PARTIAL_AUTHENTICATED_XHR","source_job":job,
                                 "source_route":route,"readiness_guard":"NO_AUTO_ACTIVATION"})
                targets = cmap.get(ticker, [])
                if not targets:
                    notes.append("UNMAPPED:" + ticker)
                    continue
                for ident in targets:
                    try:
                        result = ce.record_snapshot(store, family=ident.get("instrument_type"), ticker=ticker,
                            market=ident.get("market") or "UNKNOWN", settlement=ident.get("settlement") or "UNKNOWN",
                            source_class="PPI_AUTHENTICATED_XHR", source_ref=source, evidence=evidence)
                        total += 1; changed += int(bool(result.get("changed")))
                    except Exception as exc:
                        notes.append("INSTRUMENTOS:" + type(exc).__name__)

        elif kind == "CaucionesOperables":
            rows = item.get("rows") or []
            if not rows:
                rows = [{"provider_schema":item.get("schema") or {},"evidence_scope":"ENDPOINT_SCHEMA_ONLY"}]
            for row in rows:
                ticker = str(pick(row,["ticker","simbolo","símbolo","especie"]) or "*").upper()
                market = str(pick(row,["market","mercado"]) or "UNKNOWN")
                settlement = str(pick(row,["settlement","liquidacion","liquidación","plazo"]) or "UNKNOWN")
                evidence = nonempty({**row,"source_job":job,"source_route":route,
                                     "readiness_guard":"NO_AUTO_ACTIVATION_MISSING_FIELDS_REMAIN"})
                try:
                    result = ce.record_snapshot(store, family="CAUCIONES", ticker=ticker, market=market,
                        settlement=settlement, source_class="PPI_AUTHENTICATED_XHR", source_ref=source, evidence=evidence)
                    total += 1; changed += int(bool(result.get("changed")))
                except Exception as exc:
                    notes.append("CAUCIONES:" + type(exc).__name__)

        elif kind == "DatosTecnicos":
            row = item.get("row") or {}
            ticker = str(row.get("ticker") or "").upper()
            for ident in cmap.get(ticker, []):
                if ce.normalize_family(ident.get("instrument_type")) not in {"BONOS","LETRAS","ON","LEBAC","NOBAC"}:
                    continue
                evidence = nonempty({k:v for k,v in row.items() if k != "ticker"})
                evidence.update({"source_job":job,"source_route":route,"readiness_guard":"NO_AUTO_ACTIVATION"})
                try:
                    result = ce.record_snapshot(store, family=ident.get("instrument_type"), ticker=ticker,
                        market=ident.get("market") or "UNKNOWN", settlement=ident.get("settlement") or "UNKNOWN",
                        source_class="PPI_AUTHENTICATED_XHR", source_ref=source, evidence=evidence)
                    total += 1; changed += int(bool(result.get("changed")))
                except Exception as exc:
                    notes.append("TECHNICAL:" + type(exc).__name__)

        elif kind == "SubyacenteOpciones":
            rows = item.get("rows") or []
            if rows:
                try:
                    result = ce.record_snapshot(store, family="OPCIONES", ticker="*", market="UNKNOWN", settlement="UNKNOWN",
                        source_class="PPI_AUTHENTICATED_XHR", source_ref=source,
                        evidence={"underlyings":rows,"source_job":job,"source_route":route,
                                  "evidence_scope":"UNDERLYING_CATALOG_ONLY","readiness_guard":"NO_AUTO_ACTIVATION"})
                    total += 1; changed += int(bool(result.get("changed")))
                except Exception as exc:
                    notes.append("OPTIONS:" + type(exc).__name__)

        elif kind == "SCHEMA_ONLY":
            family = {"CONTRACT_EVIDENCE_AUCTIONS":"LICITACIONES",
                      "CONTRACT_EVIDENCE_CAUCIONES":"CAUCIONES"}.get(job)
            if family:
                try:
                    result = ce.record_snapshot(store, family=family, ticker="*", market="UNKNOWN", settlement="UNKNOWN",
                        source_class="PPI_AUTHENTICATED_XHR", source_ref=source,
                        evidence={"provider_schema":item.get("schema") or {},"source_job":job,"source_route":route,
                                  "evidence_scope":"ENDPOINT_SCHEMA_ONLY","readiness_guard":"NO_AUTO_ACTIVATION"})
                    total += 1; changed += int(bool(result.get("changed")))
                except Exception as exc:
                    notes.append("SCHEMA:" + type(exc).__name__)

    state = "AMARILLO"
    detail = f"records={total}; changed={changed}; conflicts={conflicts}; no_auto_activation=YES; notes={','.join(sorted(set(notes)))[:1000]}"
    for rid in run_ids.values():
        ce.finish_run(store, run_id=rid, state=state, records=total, changed=changed,
                      conflicts=conflicts, detail=detail)

    with store.connect() as c:
        quick = c.execute("PRAGMA quick_check").fetchone()[0]
        runtime = c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
    if quick != "ok" or not runtime or runtime[0] != "PRODUCTION_PAPER" or int(runtime[1] or 0) != 0:
        raise SystemExit("POST_IMPORT_SAFETY_INVARIANT_FAILED")
    print(json.dumps({"state":state,"records":total,"changed":changed,"conflicts":conflicts,
                      "notes":sorted(set(notes)),"quick_check":quick,"real_orders_sent":0},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
