#!/usr/bin/env python3
"""Import RC6 W12 trusted-browser V2 captures into Contract Evidence v2.

Writes evidence/audit tables only. Generic current-web GET evidence is explicitly
partial and can never activate an instrument or fill missing contract fields by
inference.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from pathlib import Path

import cp_contract_evidence_v2_hf6 as ce

DB = "/app/data/paper_v17/observer_v17.db"
ROUTE_FAMILY = {
    "/Cotizaciones/FCIs": "FCI",
    "/Cotizaciones/FCIsExterior": "FCI_EXTERIOR",
    "/Cotizaciones/Acciones": "ACCIONES",
    "/Cotizaciones/AccionesUSA": "ACCIONES_USA",
    "/Cotizaciones/Bonos": "BONOS",
    "/Cotizaciones/Cauciones": "CAUCIONES",
    "/Cotizaciones/Cedears": "CEDEARS",
    "/Cotizaciones/ETFs": "ETF",
    "/Cotizaciones/Futuros": "FUTUROS",
    "/Cotizaciones/Letras": "LETRAS",
    "/Cotizaciones/Licitaciones": "LICITACIONES",
    "/Cotizaciones/Ons": "ON",
    "/Cotizaciones/Opciones": "OPCIONES",
    "/Cotizaciones/Indices": "INDICES",
    "/Cotizaciones/Monedas": "MONEDAS",
    "/Cotizaciones/Tasas": "TASAS",
}


class Store:
    def __init__(self, path): self.path = path
    def connect(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c


def nonempty(data):
    return {k: v for k, v in data.items() if v not in (None, "", [], {})}


def pick(obj, names):
    wanted = {x.lower().replace("-", "_") for x in names}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower().replace("-", "_") in wanted and v not in (None, "", [], {}):
                return v
        for v in obj.values():
            found = pick(v, names)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = pick(v, names)
            if found not in (None, "", [], {}):
                return found
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


def rows_for(item):
    if isinstance(item.get("rows"), list):
        return [x for x in item.get("rows") if isinstance(x, dict)]
    if isinstance(item.get("row"), dict) and item.get("row"):
        return [item.get("row")]
    return []


def record(store, *, family, ticker, market, settlement, source, evidence):
    return ce.record_snapshot(
        store, family=family, ticker=ticker, market=market, settlement=settlement,
        source_class="PPI_AUTHENTICATED_XHR", source_ref=source, evidence=evidence,
    )


def main():
    if len(sys.argv) != 2:
        raise SystemExit("USAGE:capture.json")
    raw = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if raw.get("schema") != "POROTA_RC6_PPI_TRUSTED_CONTRACT_V2":
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
        rid = "rc6-ce-v2-" + uuid.uuid4().hex
        run_ids[job] = rid
        ce.start_run(
            store, run_id=rid, job_key=job,
            detail="RC6 W12 V2 authenticated GET evidence; partial; NO_AUTO_ACTIVATION",
        )

    total = changed = conflicts = mapped = wildcard = 0
    notes = []
    for _, item in (raw.get("endpoints") or {}).items():
        job = str(item.get("observed_job") or "")
        route = str(item.get("observed_route") or "")
        source = str(item.get("source_url") or "PPI_AUTHENTICATED_XHR")
        family = ROUTE_FAMILY.get(route)
        if not family:
            notes.append("NO_FAMILY_ROUTE:" + route[:80])
            continue

        item_rows = rows_for(item)
        endpoint_mapped = 0
        for row in item_rows[:1000]:
            ticker = str(pick(row, ["ticker", "simbolo", "símbolo", "especie"]) or "").strip().upper()
            if not ticker:
                continue
            targets = cmap.get(ticker, [])
            for ident in targets:
                if ce.normalize_family(ident.get("instrument_type")) != ce.normalize_family(family):
                    continue
                evidence = nonempty({k: v for k, v in row.items() if str(k).lower() not in {"ticker", "simbolo", "símbolo", "especie"}})
                evidence.update({
                    "source_job": job,
                    "source_route": route,
                    "provider_kind": item.get("kind"),
                    "evidence_scope": "PARTIAL_AUTHENTICATED_GET_OBSERVATION",
                    "readiness_guard": "NO_AUTO_ACTIVATION_NO_CONTRACT_INFERENCE",
                })
                try:
                    result = record(
                        store, family=family, ticker=ticker,
                        market=ident.get("market") or "UNKNOWN",
                        settlement=ident.get("settlement") or "UNKNOWN",
                        source=source, evidence=evidence,
                    )
                    total += 1
                    mapped += 1
                    endpoint_mapped += 1
                    changed += int(bool(result.get("changed")))
                except Exception as exc:
                    notes.append("MAPPED:" + type(exc).__name__)

        # Every observed current-web endpoint still contributes auditable partial
        # evidence even when its payload has no mappable ticker. This is a family
        # wildcard observation, never a complete contract and never readiness.
        if endpoint_mapped == 0:
            evidence = {
                "provider_kind": item.get("kind"),
                "provider_schema": item.get("schema") or {},
                "sanitized_rows_sample": item_rows[:20],
                "sanitized_row_count": len(item_rows),
                "source_job": job,
                "source_route": route,
                "evidence_scope": "PARTIAL_AUTHENTICATED_GET_OBSERVATION",
                "readiness_guard": "NO_AUTO_ACTIVATION_NO_CONTRACT_INFERENCE",
                "contract_completeness": "NOT_PROVEN",
            }
            try:
                result = record(
                    store, family=family, ticker="*", market="UNKNOWN", settlement="UNKNOWN",
                    source=source, evidence=evidence,
                )
                total += 1
                wildcard += 1
                changed += int(bool(result.get("changed")))
            except Exception as exc:
                notes.append("WILDCARD:" + type(exc).__name__)

    state = "AMARILLO"
    detail = (
        f"records={total}; mapped={mapped}; wildcard={wildcard}; changed={changed}; "
        f"conflicts={conflicts}; no_auto_activation=YES; contract_completeness=NOT_PROVEN; "
        f"notes={','.join(sorted(set(notes)))[:900]}"
    )
    for rid in run_ids.values():
        ce.finish_run(
            store, run_id=rid, state=state, records=total, changed=changed,
            conflicts=conflicts, detail=detail,
        )

    with store.connect() as c:
        quick = c.execute("PRAGMA quick_check").fetchone()[0]
        runtime = c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
    if quick != "ok" or not runtime or runtime[0] != "PRODUCTION_PAPER" or int(runtime[1] or 0) != 0:
        raise SystemExit("POST_IMPORT_SAFETY_INVARIANT_FAILED")
    if total <= 0:
        raise SystemExit("NO_CONTRACT_EVIDENCE_IMPORTED")

    print(json.dumps({
        "state": state, "records": total, "mapped": mapped, "wildcard": wildcard,
        "changed": changed, "conflicts": conflicts, "notes": sorted(set(notes)),
        "contract_completeness": "NOT_PROVEN", "no_auto_activation": True,
        "quick_check": quick, "real_orders_sent": 0,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
