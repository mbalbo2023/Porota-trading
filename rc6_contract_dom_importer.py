#!/usr/bin/env python3
"""Import sanitized authenticated PPI DOM quote-table evidence into Contract Evidence v2.

Only aggregate route/family snapshots are written. No column semantics, instrument IDs,
market/settlement values, or economic contract fields are inferred. Readiness therefore
remains fail-closed for any fields not explicitly proven elsewhere.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import cp_contract_evidence_v2_hf6 as ce

DB = "/app/data/paper_v17/observer_v17.db"
ROUTE_FAMILY = {
    "/Cotizaciones/Acciones": "ACCIONES",
    "/Cotizaciones/AccionesUSA": "ACCIONES_USA",
    "/Cotizaciones/Bonos": "BONOS",
    "/Cotizaciones/Cauciones": "CAUCIONES",
    "/Cotizaciones/Cedears": "CEDEARS",
    "/Cotizaciones/ETFs": "ETF",
    "/Cotizaciones/FCIs": "FCI_LOCAL",
    "/Cotizaciones/FCIsExterior": "FCI_EXTERIOR",
    "/Cotizaciones/Futuros": "FUTUROS",
    "/Cotizaciones/Letras": "LETRAS",
    "/Cotizaciones/Licitaciones": "LICITACIONES",
    "/Cotizaciones/Ons": "ON",
    "/Cotizaciones/Opciones": "OPCIONES",
}


class Store:
    def __init__(self, path: str):
        self.path = path

    def connect(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c


def fail(reason: str, code: int = 4) -> int:
    print(json.dumps({"state": "FAIL_CLOSED", "reason": reason, "records": 0}, sort_keys=True))
    return code


def main() -> int:
    if len(sys.argv) != 2:
        return fail("CAPTURE_PATH_REQUIRED")
    path = Path(sys.argv[1])
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return fail("CAPTURE_READ_ERROR:" + type(exc).__name__)
    if raw.get("schema") != "POROTA_RC6_PPI_AUTH_DOM_V1":
        return fail("UNSUPPORTED_CAPTURE_SCHEMA")
    if raw.get("auth_status") != "AUTHENTICATED_TRUSTED_DEVICE":
        return fail("AUTH_NOT_TRUSTED")
    if int(raw.get("real_orders_sent") or 0) != 0:
        return fail("REAL_ORDER_SAFETY_INVARIANT")
    if raw.get("blocked_nonread"):
        return fail("SUSPICIOUS_FIRST_PARTY_MUTATION")
    observed_at = str(raw.get("generated_at") or "")
    if not observed_at:
        return fail("MISSING_GENERATED_AT")

    store = Store(DB)
    records = []
    skipped = []
    for route_item in raw.get("routes") or []:
        if not isinstance(route_item, dict):
            continue
        route = str(route_item.get("requested") or "")
        family = ROUTE_FAMILY.get(route)
        if not family or not route_item.get("reached"):
            skipped.append({"route": route, "reason": "UNSUPPORTED_OR_NOT_REACHED"})
            continue
        material = []
        for table in route_item.get("tables") or []:
            if not isinstance(table, dict) or not table.get("materializable"):
                continue
            headers = [str(x)[:100] for x in (table.get("headers") or []) if str(x).strip()][:40]
            rows = []
            for row in (table.get("rows") or [])[:100]:
                if isinstance(row, list):
                    rows.append([str(x)[:180] for x in row[:40]])
            if headers and rows:
                material.append({
                    "table_index": int(table.get("table_index") or 0),
                    "headers": headers,
                    "row_count": int(table.get("row_count") or len(rows)),
                    "rows": rows,
                })
        if not material:
            skipped.append({"route": route, "reason": "NO_EXPLICIT_HEADER_TABLE"})
            continue
        evidence = {
            "provider": "PPI_AUTHENTICATED_WEB",
            "route": route,
            "tables": material,
        }
        try:
            result = ce.record_snapshot(
                store,
                family=family,
                ticker="*",
                market="UNKNOWN",
                settlement="UNKNOWN",
                source_class="PPI_AUTHENTICATED_WEB",
                source_ref="https://trading.portfoliopersonal.com" + route,
                evidence=evidence,
                observed_at=observed_at,
            )
            records.append({
                "route": route,
                "family": family,
                "snapshot_id": result.get("snapshot_id"),
                "changed": bool(result.get("changed")),
                "status": result.get("status"),
            })
        except Exception as exc:
            return fail("RECORD_ERROR:" + family + ":" + type(exc).__name__)

    state = "GREEN" if records else "AMARILLO"
    print(json.dumps({
        "state": state,
        "source_class": "PPI_AUTHENTICATED_WEB",
        "records": len(records),
        "families": sorted({x["family"] for x in records}),
        "recorded": records,
        "skipped": skipped,
        "real_orders_sent": 0,
    }, ensure_ascii=False, sort_keys=True))
    return 0 if records else 6


if __name__ == "__main__":
    raise SystemExit(main())
