"""Bounded, offline evidence inventory for FUTUROS and INDICES.

This tool consumes an explicitly supplied PPI catalogue snapshot. It never
opens a database, calls a provider, constructs an order or upgrades a contract
to PAPER-ready. In particular, reviewed static DLR terms are NOT a fresh
book, a broker margin or an executable contract for another future series.

The optional CLI accepts a previously exported JSON snapshot; producing that
snapshot and authorizing its provenance belong to the read-only control plane.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import timezone
from hashlib import sha256
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from bs_instrument_contracts import aware_datetime
from rc6_multisource_discovery import (
    canonical_family, canonical_market, canonical_settlement,
)
from rc6_ppi_future_contract_policy import (
    standard_dlr_future_evidence, standard_dlr_terms,
)


SCHEMA = "rc6-future-index-readonly-catalog-audit-v1"
MAX_ROWS = 20_000
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_SAMPLES = 20
SCOPED_FAMILIES = frozenset({"FUTUROS", "INDICES"})
UNKNOWN = frozenset({"", "*", "UNKNOWN", "NO_VERIFICADO"})
ART = ZoneInfo("America/Argentina/Buenos_Aires")


def _identity(row):
    family = canonical_family(row.get("instrument_type"))
    key = (
        str(row.get("ticker") or "").strip().upper(),
        family,
        canonical_market(row.get("market")),
        str(row.get("currency") or "").strip().upper(),
        canonical_settlement(row.get("settlement"), family),
    )
    return key if all(value not in UNKNOWN for value in key) else None


def _metadata(row):
    value = row.get("metadata_json")
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value or "{}")
    except (ValueError, TypeError):
        return None
    return result if isinstance(result, dict) else None


def _valid_clock(value, at):
    try:
        clock = aware_datetime(value, "catalog last_seen_at")
    except (ValueError, TypeError):
        return False
    return clock <= at


def _reason(row, key, at):
    if key is None:
        return "FULL_IDENTITY_INCOMPLETE"
    metadata = _metadata(row)
    if metadata is None:
        return "CATALOG_METADATA_INVALID"
    if str(row.get("status") or "").upper() != "AVAILABLE":
        return "CATALOG_NOT_AVAILABLE"
    if metadata.get("_discovery_source") != "PPI_PRIMARY":
        return "PPI_PRIMARY_SOURCE_UNPROVED"
    if not _valid_clock(row.get("last_seen_at"), at):
        return "CATALOG_CLOCK_MISSING_OR_FUTURE"
    if key[1] == "INDICES":
        # PPI index identity is observational; no direct contract or orders.
        return "INDEX_CONTEXT_ONLY_LIVE_VALUE_UNPROVED"

    if metadata.get("_contract_conflicts"):
        return "FUTURE_CONTRACT_CONFLICT_UNREVIEWED"
    if str(row.get("settlement_source") or "").upper() != "PPI_FIELD":
        return "FUTURE_SETTLEMENT_PROVENANCE_UNPROVED"
    if key[2:] != ("A3", "ARS", "INMEDIATA"):
        return "FUTURE_A3_ARS_EXACT_IDENTITY_REQUIRED"
    if row.get("capability") != "NEEDS_FUTURES_MARGIN_AND_CONTRACT":
        return "FUTURE_CAPABILITY_NOT_REVIEWED"
    terms = standard_dlr_terms(key[0])
    if terms is None:
        return "FUTURE_SERIES_CONTRACT_NOT_REVIEWED"
    if standard_dlr_future_evidence(row) is None:
        return "FUTURE_STATIC_POLICY_UNRESOLVED"
    if at >= aware_datetime(terms["expires_at"]):
        return "FUTURE_SERIES_EXPIRED"
    # Never promote static contract evidence to signal/fill/margin/fee readiness.
    return "DLR_STATIC_CONTRACT_ONLY_DYNAMIC_UNVERIFIED"


def audit_catalog(rows, *, as_of, sample_limit=10):
    """Audit a bounded snapshot without mutating or inferring trade readiness.

    Each supplied row represents one provider catalogue record. Repeated exact
    five-part identities are flagged as ambiguous, not silently deduplicated.
    The hash binds the supplied bytes/values, not a live provider or runtime.
    """
    at = aware_datetime(as_of, "audit as_of")
    if type(rows) is not list or len(rows) > MAX_ROWS:
        raise ValueError("CATALOG_AUDIT_ROWS_INVALID_OR_UNBOUNDED")
    if type(sample_limit) is not int or not 0 <= sample_limit <= MAX_SAMPLES:
        raise ValueError("CATALOG_AUDIT_SAMPLE_LIMIT_INVALID")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("CATALOG_AUDIT_NON_OBJECT_ROW")

    scoped = [row for row in rows if canonical_family(row.get("instrument_type"))
              in SCOPED_FAMILIES]
    keys = [_identity(row) for row in scoped]
    multiplicities = Counter(key for key in keys if key is not None)
    reasons = Counter()
    examples = []
    for row, key in zip(scoped, keys):
        reason = ("DUPLICATE_EXACT_IDENTITY" if key is not None and
                  multiplicities[key] > 1 else _reason(row, key, at))
        reasons[reason] += 1
        if len(examples) < sample_limit:
            examples.append({
                "identity": list(key) if key is not None else None,
                "reason": reason,
                "evidence_state": "NO_VERIFICADO",
                "paper_tradable": False,
            })

    digest = sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return {
        "schema": SCHEMA,
        "as_of_utc": at.astimezone(timezone.utc).isoformat(),
        "as_of_art": at.astimezone(ART).isoformat(),
        "evidence_scope": "SUPPLIED_PPI_CATALOG_SNAPSHOT_ONLY",
        "input_sha256": digest,
        "supplied_rows": len(rows),
        "scoped_rows": len(scoped),
        # Keep the denominator complete even when a key is too incomplete to
        # count as an identity. Dropping that row would hide an actual blocker.
        "future_rows": sum(canonical_family(row.get("instrument_type")) == "FUTUROS"
                           for row in scoped),
        "index_rows": sum(canonical_family(row.get("instrument_type")) == "INDICES"
                          for row in scoped),
        "distinct_full_identities": len(multiplicities),
        "ambiguous_full_identities": sum(count > 1 for count in multiplicities.values()),
        "reasons": dict(sorted(reasons.items())),
        "examples": examples,
        "operational_readiness": "NO_VERIFICADO",
        "paper_tradable_count": 0,
        "real_routes_used": [],
        "paper_only": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-json", type=Path, required=True,
                        help="Previously exported, provenance-reviewed JSON row array")
    parser.add_argument("--as-of", required=True, help="Aware ISO-8601 evidence cutoff")
    parser.add_argument("--samples", type=int, default=10)
    options = parser.parse_args(argv)
    if options.catalog_json.stat().st_size > MAX_INPUT_BYTES:
        parser.error("CATALOG_AUDIT_INPUT_TOO_LARGE")
    payload = json.loads(options.catalog_json.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    result = audit_catalog(rows, as_of=options.as_of, sample_limit=options.samples)
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
