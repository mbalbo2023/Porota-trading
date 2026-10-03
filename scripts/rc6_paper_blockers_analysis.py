"""Offline exhaustive RC6 blocker classification; never promotes instruments.

Input is a frozen Actions census artifact, not a DB connection. All identities
are classified; limited examples are presentation only. Source terms absent
from the projection remain NO_VERIFICADO, never inferred from a ticker.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path

EXPECTED_PRODUCT = "da697c6e6c2274579f9e4a112fabc4327475dd35"
KEY_FIELDS = ("ticker", "instrument_type", "market", "currency", "settlement")
TAXONOMY = {
    "A": "requisito financiero/contractual; falta o conflicto debe investigarse",
    "B": "policy PAPER o derivacion oficial a verificar; no termino real inventado",
    "C": "control de cuenta/broker real; verificar consumidor PAPER",
    "D": "enriquecimiento sin consumidor PAPER OPEN; requiere revisar el gate real",
    "E": "identidad, autoridad PPI o ambiguedad",
    "F": "vigencia, freshness o condicion dinamica",
    "NO_VERIFICADO": "motivo sin clasificacion demostrable en esta proyeccion",
}


def key(row, *, evidence=False):
    family = row.get("family") if evidence else row.get("instrument_type")
    if evidence and family == "ON":
        family = "OBLIGACIONES"
    return (str(row.get("ticker") or ""), str(family or ""), str(row.get("market") or ""),
            str(row.get("currency") or ""), str(row.get("settlement") or ""))


def metadata(row, fields):
    value = row.get("metadata_json")
    if isinstance(value, str):
        value = json.loads(value)
    if isinstance(value, list):
        if len(value) != len(fields):
            raise ValueError("METADATA_PROJECTION_WIDTH_MISMATCH")
        return dict(zip(fields, value))
    return value if isinstance(value, dict) else {}


def field(meta, path):
    if path in meta:
        return meta[path]
    value = meta
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def classify(reason):
    if any(s in reason for s in ("PPI_PRIMARY_IDENTITY", "IDENTITY_", "RETRY_IDENTITY", "OBSERVED_SHADOW", "MISSING_CURRENCY_OR_MARKET")):
        return "E"
    if any(s in reason for s in ("FRESHNESS", "STALE", "EVIDENCE_NOT_CURRENT", "EXPIRED", "EXPIRY_PASSED")):
        return "F"
    if any(s in reason.lower() for s in ("account_balance", "buying_power", "real_collateral", "broker_account_permission", "ppi_rofex_enabled")):
        return "C"
    if reason.startswith("MISSING:paper_"):
        return "B"
    if reason in {"MISSING:fee_schedule", "MISSING:trading_session"}:
        return "D"
    if any(s in reason for s in ("CAPABILITY:", "MISSING:", "CONFLICT:", "CHANGE_REVIEW_REQUIRED", "INVALID_", "CONTRACT_INVALID")):
        return "A"
    return "NO_VERIFICADO"


def analyze(payload):
    if payload.get("complete") is not True:
        raise ValueError("CENSUS_INCOMPLETE")
    if payload.get("product_sha") != EXPECTED_PRODUCT:
        raise ValueError("CENSUS_PRODUCT_SHA_MISMATCH")
    state = payload.get("observer") or {}
    if state.get("mode") != "PRODUCTION_PAPER" or state.get("real_orders_sent") != 0:
        raise ValueError("CENSUS_SAFETY_MISMATCH")
    tables = payload["tables"]
    candidates = tables["candidate_identity_v2"]
    catalog = tables["financial_instrument_catalog"]
    c_map = {key(r): r for r in catalog}
    if len(c_map) != len(catalog) or len({key(r) for r in candidates}) != len(candidates):
        raise ValueError("DUPLICATE_EXACT_IDENTITY")
    if set(c_map) != {key(r) for r in candidates}:
        raise ValueError("CANDIDATE_CATALOG_SCOPE_MISMATCH")
    ev = defaultdict(list)
    for r in tables.get("contract_evidence_v2_current", []):
        ev[key(r, evidence=True)].append(r)
    out = []
    cohorts = defaultdict(list)
    categories = Counter()
    source_counts = Counter()
    family_counts = defaultdict(Counter)
    evidence_counts = Counter()
    for row in sorted(candidates, key=key):
        k = key(row)
        cat = c_map[k]
        meta = metadata(cat, payload.get("metadata_projection_fields") or [])
        ready = row.get("status") == "AVAILABLE" and row.get("can_simulate") == 1
        raw = str(row.get("detail") or "")
        tokens = [s for s in raw.split(";") if s and not s.startswith("PAUSED_EXPLICIT:")]
        if ready:
            tokens = []
        gaps = field(meta, "_contract_bridge.gaps") or []
        if not isinstance(gaps, list):
            gaps = [str(gaps)]
        reasons = sorted(set(tokens + ([] if ready else [str(g) for g in gaps])))
        classes = sorted({classify(s) for s in reasons}) if not ready else []
        if not ready and not classes:
            classes = ["NO_VERIFICADO"]
        source = str(field(meta, "_discovery_source") or "NO_VERIFICADO")
        evidence_key = (k[0], "OBLIGACIONES" if k[1] == "ON" else k[1], *k[2:])
        references = ev.get(evidence_key, [])
        sources = sorted({r["source_class"] for r in references})
        same_instrument_other_family = None
        if k[1] in {"ON", "OBLIGACIONES"}:
            other = "ON" if k[1] == "OBLIGACIONES" else "OBLIGACIONES"
            sibling = c_map.get((k[0], other, *k[2:]))
            if sibling:
                sibling_meta = metadata(sibling, payload.get("metadata_projection_fields") or [])
                isin_a, isin_b = field(meta, "isin"), field(sibling_meta, "isin")
                same_instrument_other_family = {
                    "other_family": other, "catalog_status": sibling.get("status"),
                    "capability": sibling.get("capability"),
                    "isin_match": bool(isin_a == isin_b) if isin_a and isin_b else None,
                    "selection_authorized": False,
                }
        entry = dict(row)
        entry.update(catalog_status=cat.get("status"), capability=cat.get("capability"),
            discovery_source=source, availability_source=field(meta,"_availability_source"),
            provider_instrument_type=field(meta,"_provider_instrument_type"),
            settlement_source=cat.get("settlement_source"), last_seen_at=cat.get("last_seen_at"),
            isin=field(meta,"isin"), caja_valores_code=field(meta,"cajaValoresCode"),
            nominal_in_price=field(meta,"nominalInPrice"), bridge_status=field(meta,"_contract_bridge.status"),
            bridge_gaps=gaps, taxonomy=classes, exact_reasons=reasons,
            evidence_sources=sources, evidence_references=references,
            evidence_values_status="NO_VERIFICADO_NOT_EXPORTED",
            alias_sibling=same_instrument_other_family,
            proposed_action=("PRESERVE_READY_AND_DYNAMIC_GATES" if ready else
                "RESOLVE_EXACT_PRIMARY_IDENTITY" if "E" in classes else
                "REFRESH_OR_VALIDATE_EXPIRY" if classes == ["F"] else
                "REVIEW_EXACT_CONTRACT_REQUIREMENTS"),
            automatic_promotion=False)
        out.append(entry)
        family_counts[k[1]]["total"] += 1
        family_counts[k[1]]["ready" if ready else "blocked"] += 1
        if not ready:
            for cat_name in classes:
                categories[(k[1],cat_name)] += 1
            source_counts[(k[1],source)] += 1
            for s in sources:
                evidence_counts[(k[1],s)] += 1
            cohorts[(k[1],row.get("status"),cat.get("capability"),tuple(reasons))].append(entry)
    cohort_rows = []
    for (family,status,capability,reasons),rows in sorted(cohorts.items(),key=lambda x:(x[0][0],-len(x[1]),str(x[0]))):
        cohort_rows.append(dict(family=family,status=status,capability=capability,n=len(rows),
            reasons=list(reasons),examples=[{k:r.get(k) for k in (*KEY_FIELDS,"discovery_source","evidence_sources","bridge_gaps","alias_sibling")} for r in rows[:2]]))
    blocked = sum(r["blocked"] for r in family_counts.values())
    if sum(r["n"] for r in cohort_rows) != blocked or len(out) != len(candidates):
        raise ValueError("EXHAUSTIVE_RECONCILIATION_FAILED")
    report = {
        "schema_version":1,"product_sha":EXPECTED_PRODUCT,"captured_at":payload.get("captured_at"),
        "census_schema":payload.get("schema_version"),"census_elapsed_seconds":payload.get("elapsed_seconds"),
        "identity_rows":len(out),"ready":len(out)-blocked,"blocked":blocked,
        "family_counts":dict(family_counts),"exclusive_causal_cohorts":cohort_rows,
        "categories_overlap":True,"classification_effect":"AUDIT_ONLY_NOT_PROMOTION",
        "category_counts":[dict(family=f,category=c,n=n) for (f,c),n in sorted(categories.items())],
        "blocked_discovery_sources":[dict(family=f,source=s,n=n) for (f,s),n in sorted(source_counts.items())],
        "blocked_evidence_source_coverage":[dict(family=f,source=s,n=n) for (f,s),n in sorted(evidence_counts.items())],
        "taxonomy":TAXONOMY,"real_orders_sent":state["real_orders_sent"],
        "promoted_identities":0,"database_writes":0,"broker_calls":0,
        "limitations":["Evidence values were not exported; current references do not prove all required terms.",
            "PAUSED_EXPLICIT is a presentation classification, not proof of a PPI suspension.",
            "A-F classifications are diagnostic candidates, never authority to change readiness.",
            "Examples are explanatory; counts and matrix cover every identity."],
    }
    return report,out


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--census",required=True)
    p.add_argument("--output",required=True)
    args=p.parse_args()
    raw=Path(args.census).read_bytes()
    report,matrix=analyze(json.loads(raw))
    report["census_sha256"]=hashlib.sha256(raw).hexdigest()
    folder=Path(args.output)
    folder.mkdir(parents=True,exist_ok=True)
    (folder/"analysis.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    (folder/"matrix.json").write_text(json.dumps(matrix,ensure_ascii=False),encoding="utf-8")
    fields=(*KEY_FIELDS,"status","can_simulate","detail","catalog_status","capability","discovery_source","last_seen_at","taxonomy","bridge_gaps","evidence_sources","proposed_action")
    with (folder/"matrix.csv").open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader()
        for r in matrix:
            w.writerow({k:json.dumps(r[k],ensure_ascii=False) if isinstance(r.get(k),(list,dict)) else r.get(k) for k in fields})
    print(json.dumps(report,indent=2,ensure_ascii=False))


if __name__ == "__main__":
    main()
