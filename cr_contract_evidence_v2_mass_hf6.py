"""Mass PPI catalogue -> Contract Evidence v2 ingestion for PAPER.

The catalogue is the PPI-primary identity authority.  This module reads every
current AVAILABLE full-key row and writes stable, append-only v2 evidence.  It
does not call a broker, infer order terms from ticker text, or authorize real
execution.  The only derived contract emitted here is the already-audited BYMA
unit convention for spot shares, CEDEARs and ETFs.
"""
from __future__ import annotations

import json
from collections import Counter

import cp_contract_evidence_v2_hf6 as evidence_v2
from bu_instrument_catalog import _candidate_has_ppi_primary
from rc6_multisource_discovery import canonical_family, canonical_market, canonical_settlement


SCHEMA = "rc6-contract-evidence-v2-mass-catalog-v1"
JOB_KEY = "CONTRACT_EVIDENCE_V2_MASS_PPI_CATALOG"
MAX_CATALOG_ROWS = 20_000
SPOT_UNIT_FAMILIES = frozenset({"ACCIONES", "CEDEARS", "ETFS"})
UNKNOWN = frozenset({"", "*", "UNKNOWN", "NO_VERIFICADO"})


def _catalog_rows(store):
    with store.connect() as connection:
        rows = connection.execute("""SELECT ticker,instrument_type,market,currency,
          settlement,settlement_source,status,last_seen_at,metadata_json
          FROM financial_instrument_catalog
          WHERE UPPER(COALESCE(status,''))='AVAILABLE'
          ORDER BY instrument_type,ticker,market,currency,settlement""").fetchall()
    if len(rows) > MAX_CATALOG_ROWS:
        raise RuntimeError("PPI_CATALOG_BOUND_EXCEEDED")
    return [dict(row) for row in rows]


def _raw(row):
    try:
        value = json.loads(row.get("metadata_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _full_identity(row):
    family = canonical_family(row.get("instrument_type"))
    return {
        "family": family,
        "ticker": str(row.get("ticker") or "").strip().upper(),
        "market": canonical_market(row.get("market")),
        "currency": str(row.get("currency") or "").strip().upper(),
        "settlement": canonical_settlement(row.get("settlement"), family),
    }


def planned_records(rows):
    """Return stable source records and explicit skip reasons."""
    records = []
    skipped = Counter()
    identities = Counter()
    for row in rows:
        raw = _raw(row)
        if not _candidate_has_ppi_primary(row.get("settlement_source"),
                                          json.dumps(raw, ensure_ascii=False)):
            skipped["NOT_PPI_PRIMARY"] += 1
            continue
        identity = _full_identity(row)
        if any(value in UNKNOWN for value in identity.values()):
            skipped["FULL_IDENTITY_INCOMPLETE"] += 1
            continue
        family = identity["family"]
        provider_family = str(raw.get("_provider_instrument_type") or
                              raw.get("type") or row.get("instrument_type") or "").strip().upper()
        payload = {
            "market": identity["market"],
            "currency": identity["currency"],
            "settlement": identity["settlement"],
            "ppi_identity_verified": True,
            "provider_instrument_type": provider_family,
            "provider_catalog_status": "AVAILABLE",
            "evidence_scope": "FULL_PPI_AVAILABLE_CATALOG",
            "readiness_guard": "PPI_IDENTITY_ONLY_UNLESS_EXPLICIT_TERM_PRESENT",
        }
        nominal = raw.get("nominalInPrice")
        if nominal not in (None, ""):
            # Preserve PPI's literal structured field.  It is quote-basis
            # evidence, not an order increment and not by itself a multiplier.
            payload["price_quote_unit"] = nominal
            payload["ppi_nominal_in_price"] = nominal
            payload["semantic_guard"] = "NOT_QUANTITY_STEP"
        records.append({
            **identity,
            "source_class": "PPI_STRUCTURED_API",
            "source_ref": "PPI_API:/api/1.0/marketdata/searchinstrument",
            "observed_at": row.get("last_seen_at"),
            "evidence": payload,
        })
        identities[family] += 1

        if family in SPOT_UNIT_FAMILIES and identity["market"] == "BYMA":
            records.append({
                **identity,
                "source_class": "DERIVED_OFFICIAL_RULE",
                "source_ref": "BYMA_SPOT_UNIT_CONVENTION:PAPER_OPEN",
                "observed_at": row.get("last_seen_at"),
                "evidence": {
                    "cash_multiplier": "1",
                    "quantity_min": "1",
                    "quantity_step": "1",
                    "derivation_rule": "one traded unit equals one PAPER quantity unit",
                    "evidence_scope": "BYMA_SPOT_ONLY",
                    "readiness_guard": "NOT_APPLICABLE_TO_FIXED_INCOME_OR_DERIVATIVES",
                },
            })
    return records, dict(skipped), dict(identities)


def collect(store, *, run_id):
    evidence_v2.start_run(
        store, run_id=run_id, job_key=JOB_KEY,
        detail="PPI full AVAILABLE catalogue; PAPER/SHADOW only; no broker routes")
    try:
        rows = _catalog_rows(store)
        records, skipped, identities = planned_records(rows)
        results = evidence_v2.record_snapshots(store, records)
        changed = sum(bool(item.get("changed")) for item in results)
        first_seen = sum(bool(item.get("first_seen")) for item in results)
        by_family = Counter(item["family"] for item in results)
        detail = json.dumps({
            "schema": SCHEMA,
            "catalog_available": len(rows),
            "identities_written": identities,
            "evidence_rows": dict(by_family),
            "first_seen": first_seen,
            "skipped": skipped,
            "paper_only": True,
            "real_routes_used": [],
        }, ensure_ascii=False, sort_keys=True)
        evidence_v2.finish_run(
            store, run_id=run_id, state="OK", records=len(results),
            changed=changed, conflicts=changed, detail=detail)
        return json.loads(detail)
    except Exception as exc:
        evidence_v2.finish_run(
            store, run_id=run_id, state="ERROR", detail=type(exc).__name__)
        raise


def assert_read_only_invariants():
    assert MAX_CATALOG_ROWS > 0
    assert SPOT_UNIT_FAMILIES == {"ACCIONES", "CEDEARS", "ETFS"}
    assert "order" not in JOB_KEY.lower()

