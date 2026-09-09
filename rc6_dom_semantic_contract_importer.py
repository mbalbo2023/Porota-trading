#!/usr/bin/env python3
"""Strict semantic linker from authenticated PPI DOM evidence to Contract Evidence v2.

Principle: link only identities that are visibly observed and uniquely match the
independent candidate universe. Missing contract fields remain NOT_PROVEN.
No execution/readiness activation is performed.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import unicodedata
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

# Headers whose visible values can be retained as contract/operational evidence.
# Header names and values are persisted verbatim; they are NOT translated into
# canonical contract fields by this importer.
CONTRACT_HEADERS = {
    "CATEGORIA", "MON.", "MONEDA", "PLAZO RESC.", "HORARIO LIMITE", "RIESGO",
    "RATIO", "FECHA VTO.", "CANT. DE DIAS", "INVERSION MINIMA",
    "MONEDA / ESPECIE", "PLAZO", "FECHA FIN", "ESTADO",
}
IDENTITY_HEADERS = {"ESPECIE", "NOMBRE"}
DISPLAY_PREFIX = {
    "BONOS": "BON ",
    "LETRAS": "LET ",
    "ON": "ON ",
    "ETF": "ETF ",
}
FAMILY_EQUIV = {
    "ACCIONES": {"ACCIONES", "ACCION"},
    "ACCIONES_USA": {"ACCIONES_USA", "ACCIONES-USA", "ACCION_USA", "ACCION-USA"},
    "BONOS": {"BONOS", "BONO"},
    "CAUCIONES": {"CAUCIONES", "CAUCION"},
    "CEDEARS": {"CEDEARS", "CEDEAR"},
    "ETF": {"ETF", "ETFS"},
    "FUTUROS": {"FUTUROS", "FUTURO"},
    "LETRAS": {"LETRAS", "LETRA"},
    "LICITACIONES": {"LICITACIONES", "LICITACION"},
    "ON": {"ON", "ONS", "OBLIGACIONES_NEGOCIABLES", "OBLIGACIONES NEGOCIABLES"},
    "OPCIONES": {"OPCIONES", "OPCION"},
    "FCI": {"FCI", "FCIS"},
    "FCI_EXTERIOR": {"FCI_EXTERIOR", "FCI-EXTERIOR", "FCI EXTERIOR"},
    "INDICES": {"INDICES", "INDICE"},
    "MONEDAS": {"MONEDAS", "MONEDA"},
    "TASAS": {"TASAS", "TASA"},
}


class Store:
    def __init__(self, path): self.path = path
    def connect(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c


def ascii_key(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().upper()


def identity_key(value):
    text = ascii_key(value)
    text = re.sub(r"\s*/\s*", "/", text)
    return text


def family_key(value):
    return ascii_key(value).replace("/", "_")


def family_matches(route_family, candidate_family):
    rf = family_key(route_family)
    cf = family_key(candidate_family)
    allowed = {family_key(x) for x in FAMILY_EQUIV.get(rf, {rf})}
    return cf in allowed


def candidate_rows(store):
    with store.connect() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "candidate_universe" not in tables:
            raise RuntimeError("CANDIDATE_UNIVERSE_MISSING")
        cols = {r[1] for r in c.execute("PRAGMA table_info(candidate_universe)")}
        required = {"ticker", "instrument_type"}
        if not required.issubset(cols):
            raise RuntimeError("CANDIDATE_UNIVERSE_IDENTITY_COLUMNS_MISSING")
        select = ["ticker", "instrument_type"]
        for optional in ("market", "settlement", "status"):
            if optional in cols: select.append(optional)
        where = " WHERE status='AVAILABLE'" if "status" in cols else ""
        rows = [dict(r) for r in c.execute("SELECT " + ",".join(select) + " FROM candidate_universe" + where)]
    for row in rows:
        row.setdefault("market", "UNKNOWN")
        row.setdefault("settlement", "UNKNOWN")
    return rows


def candidate_index(rows):
    out = {}
    for row in rows:
        ticker = identity_key(row.get("ticker"))
        if ticker:
            out.setdefault(ticker, []).append(row)
    return out


def identity_variants(route_family, observed):
    raw = identity_key(observed)
    variants = [(raw, "EXACT_VISIBLE_IDENTITY")]
    prefix = DISPLAY_PREFIX.get(family_key(route_family))
    if prefix and raw.startswith(identity_key(prefix)):
        # This only removes the literal family label rendered by PPI. It does not
        # parse or synthesize any financial attribute.
        suffix = raw[len(identity_key(prefix)):].strip()
        if suffix:
            variants.append((suffix, "VISIBLE_FAMILY_PREFIX_REMOVED"))
    # Formatting-only normalization for futures such as "AL30 / OCT26".
    compact = re.sub(r"\s*/\s*", "/", raw)
    if compact != raw:
        variants.append((compact, "VISIBLE_SEPARATOR_WHITESPACE_NORMALIZED"))
    seen = set()
    return [(v, m) for v, m in variants if v and not (v in seen or seen.add(v))]


def match_candidate(route_family, observed, index):
    matches = []
    for variant, method in identity_variants(route_family, observed):
        for row in index.get(variant, []):
            if family_matches(route_family, row.get("instrument_type")):
                matches.append((variant, method, row))
    # Unique financial identity only. If the same visible ticker maps to multiple
    # market/settlement identities, the DOM page did not prove which one it is.
    uniq = {}
    for variant, method, row in matches:
        key = (
            identity_key(row.get("ticker")), family_key(row.get("instrument_type")),
            ascii_key(row.get("market") or "UNKNOWN"), ascii_key(row.get("settlement") or "UNKNOWN"),
        )
        uniq[key] = (variant, method, row)
    if len(uniq) != 1:
        return None, ("NO_MATCH" if not uniq else "AMBIGUOUS_MATCH")
    return next(iter(uniq.values())), "UNIQUE_MATCH"


def header_map(headers, row):
    pairs = []
    for pos, value in enumerate(row):
        header = headers[pos] if pos < len(headers) else ""
        if header or value:
            pairs.append({"header": str(header), "value": str(value)})
    return pairs


def identity_from_row(headers, row, family):
    normalized = [ascii_key(h) for h in headers]
    for i, h in enumerate(normalized):
        if h in IDENTITY_HEADERS and i < len(row) and str(row[i]).strip():
            return headers[i], str(row[i]).strip()
    # Cauciones expose no ticker/species identity; days are evidence but not an
    # instrument identifier, so they intentionally remain family-level.
    return None, None


def contract_pairs(headers, row):
    out = []
    for i, h in enumerate(headers):
        if ascii_key(h) in CONTRACT_HEADERS and i < len(row) and str(row[i]).strip():
            out.append({"header": str(h), "value": str(row[i]).strip()})
    return out


def main():
    if len(sys.argv) != 2:
        raise SystemExit("USAGE:capture.json")
    raw = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if raw.get("schema") != "POROTA_RC6_PPI_DOM_SEMANTIC_V1":
        raise SystemExit("CAPTURE_SCHEMA_INVALID")
    if raw.get("auth_status") != "AUTHENTICATED_TRUSTED_DEVICE":
        raise SystemExit("CAPTURE_NOT_AUTHENTICATED")
    if int(raw.get("real_orders_sent") or 0) != 0:
        raise SystemExit("CAPTURE_ORDER_INVARIANT_FAILED")

    store = Store(DB)
    universe = candidate_rows(store)
    index = candidate_index(universe)
    run_id = "rc6-w12-semantic-" + uuid.uuid4().hex
    ce.start_run(
        store, run_id=run_id, job_key="CONTRACT_EVIDENCE_DOM_SEMANTIC",
        detail="Strict unique identity linking from authenticated DOM; missing fields remain NOT_PROVEN; NO_AUTO_ACTIVATION",
    )

    mapped = ambiguous = unmatched = family_level = changed = conflicts = 0
    mapped_families = set()
    family_summaries = {}
    for route in raw.get("routes") or []:
        route_name = str(route.get("route") or "")
        family = ROUTE_FAMILY.get(route_name)
        if not family:
            continue
        summary = family_summaries.setdefault(family, {"rows": 0, "mapped": 0, "ambiguous": 0, "unmatched": 0, "family_level": 0})
        for table in route.get("tables") or []:
            headers = [str(x) for x in (table.get("headers") or [])]
            table_unmapped = []
            for row in table.get("rows") or []:
                vals = [str(x) for x in row]
                summary["rows"] += 1
                id_header, observed = identity_from_row(headers, vals, family)
                cpairs = contract_pairs(headers, vals)
                if observed:
                    match, state = match_candidate(family, observed, index)
                    if match:
                        _, method, candidate = match
                        evidence = {
                            "source_route": route_name,
                            "identity_header": id_header,
                            "identity_observed": observed,
                            "semantic_link": "UNIQUE_CANDIDATE_UNIVERSE_MATCH",
                            "semantic_link_method": method,
                            "observed_contract_fields": cpairs,
                            "contract_completeness": "NOT_PROVEN",
                            "readiness_guard": "NO_AUTO_ACTIVATION_MISSING_FIELDS_STAY_MISSING",
                        }
                        result = ce.record_snapshot(
                            store, family=family, ticker=candidate.get("ticker"),
                            market=candidate.get("market") or "UNKNOWN",
                            settlement=candidate.get("settlement") or "UNKNOWN",
                            source_class="PPI_AUTHENTICATED_WEB",
                            source_ref=route.get("url") or route_name,
                            evidence=evidence,
                        )
                        mapped += 1
                        summary["mapped"] += 1
                        mapped_families.add(family)
                        changed += int(bool(result.get("changed")))
                    elif state == "AMBIGUOUS_MATCH":
                        ambiguous += 1
                        summary["ambiguous"] += 1
                        table_unmapped.append({"identity_observed": observed, "reason": state})
                    else:
                        unmatched += 1
                        summary["unmatched"] += 1
                        table_unmapped.append({"identity_observed": observed, "reason": state})
                else:
                    family_level += 1
                    summary["family_level"] += 1
                    if cpairs:
                        table_unmapped.append({"observed_contract_fields": cpairs, "reason": "NO_VISIBLE_INSTRUMENT_IDENTITY"})
            # Keep unmatched proof at family scope. This never creates a candidate
            # instrument or fills a missing identity.
            if table_unmapped:
                evidence = {
                    "source_route": route_name,
                    "table_headers_observed": headers,
                    "unlinked_observations_sample": table_unmapped[:30],
                    "unlinked_observation_count": len(table_unmapped),
                    "contract_completeness": "NOT_PROVEN",
                    "readiness_guard": "UNLINKED_EVIDENCE_NO_AUTO_ACTIVATION",
                }
                ce.record_snapshot(
                    store, family=family, ticker="*", market="UNKNOWN", settlement="UNKNOWN",
                    source_class="PPI_AUTHENTICATED_WEB",
                    source_ref=route.get("url") or route_name,
                    evidence=evidence,
                )

    detail = json.dumps({
        "mapped": mapped, "ambiguous": ambiguous, "unmatched": unmatched,
        "family_level": family_level, "mapped_families": sorted(mapped_families),
        "families": family_summaries, "no_auto_activation": True,
        "contract_completeness": "NOT_PROVEN",
    }, ensure_ascii=False, sort_keys=True)
    state = "VERDE_SEMANTIC_LINK" if mapped > 0 else "AMARILLO_NO_UNIQUE_LINKS"
    ce.finish_run(
        store, run_id=run_id, state=state, records=mapped,
        changed=changed, conflicts=conflicts, detail=detail,
    )

    with store.connect() as c:
        quick = c.execute("PRAGMA quick_check").fetchone()[0]
        runtime = c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
    if quick != "ok" or not runtime or runtime[0] != "PRODUCTION_PAPER" or int(runtime[1] or 0) != 0:
        raise SystemExit("POST_IMPORT_SAFETY_INVARIANT_FAILED")
    if mapped <= 0:
        raise SystemExit("NO_UNIQUE_SEMANTIC_LINKS")

    print(json.dumps({
        "state": state, "mapped": mapped, "mapped_families": sorted(mapped_families),
        "ambiguous": ambiguous, "unmatched": unmatched, "family_level": family_level,
        "candidate_universe_rows": len(universe), "changed": changed, "conflicts": conflicts,
        "contract_completeness": "NOT_PROVEN", "no_auto_activation": True,
        "quick_check": quick, "mode": runtime[0], "real_orders_sent": int(runtime[1] or 0),
        "family_summaries": family_summaries,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
