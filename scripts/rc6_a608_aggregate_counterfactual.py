#!/usr/bin/env python3
"""A608: strictly bounded OFFHOST counterfactual from A385 aggregates.

The A385 artifact intentionally contains NO per-instrument rows, Book, NAV,
trade, decision, order, fee, or latency observations. This module therefore
computes *mathematical ceilings* only. It cannot execute instrument admission
gates or attribute hypothetical PAPER fills to genuine market evidence.

No imports of network, SQLite, provider SDK, broker, PPI Watch or runtime code.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys


SCHEMA = "rc6.a608.a385-aggregate-counterfactual.v1"
INPUT_SCHEMA = "rc6.a385.ten-family-source-export-readonly.v1"
MAX_INPUT_BYTES = 128 * 1024
MAX_FAMILIES = 10
ACTIVE_FAMILIES = (
    "ACCIONES", "CEDEARS", "BONOS", "LETRAS", "OBLIGACIONES",
    "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
)
ALL_FAMILIES = set(ACTIVE_FAMILIES) | {"ETFS"}
CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,55}(?::[A-Z][A-Z0-9_]{0,55})?$")
SHA = re.compile(r"[0-9a-f]{64}\Z")


class CounterfactualRejected(ValueError):
    """Fail-closed evidence contract; error codes contain no instrument data."""


def _unique(pairs: list[tuple[str, object]]) -> dict:
    obj = {}
    for name, value in pairs:
        if name in obj:
            raise CounterfactualRejected("DUPLICATE_JSON_KEY")
        obj[name] = value
    return obj


def _count(value: object, bound: int = 60000) -> bool:
    return type(value) is int and 0 <= value <= bound


def _ensure(condition: bool, label: str) -> None:
    if not condition:
        raise CounterfactualRejected(label)


def load_pinned(raw: bytes, *, expected_sha256: str) -> tuple[dict, str]:
    _ensure(isinstance(raw, bytes) and 0 < len(raw) <= MAX_INPUT_BYTES,
            "A385_INPUT_SIZE_OR_TYPE_INVALID")
    _ensure(isinstance(expected_sha256, str) and SHA.fullmatch(expected_sha256) is not None,
            "A385_EXPECTED_DIGEST_INVALID")
    digest = hashlib.sha256(raw).hexdigest()
    _ensure(digest == expected_sha256, "A385_ARTIFACT_SHA256_MISMATCH")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except CounterfactualRejected:
        raise
    except (UnicodeError, ValueError) as exc:
        raise CounterfactualRejected("A385_JSON_INVALID") from exc
    _ensure(isinstance(value, dict), "A385_SCHEMA_INVALID")
    return value, digest


def _validated_families(payload: dict) -> list[dict]:
    _ensure(payload.get("schema") == INPUT_SCHEMA and
            payload.get("source") == "EXISTING_FULL_UNIVERSE_JSON_ONLY" and
            payload.get("read_only") is True and
            payload.get("db_opened") is False and
            payload.get("provider_calls") == 0 and
            payload.get("order_routes_called") is False and
            payload.get("admission_authority") is False and
            payload.get("runtime_sha_verified") is False and
            payload.get("causal_pit_receipt_validated") is False,
            "A385_PROVENANCE_OR_SAFETY_INVALID")
    _ensure(payload.get("status") == "NO_VERIFICADO" and
            payload.get("reason") == "FAMILY_COVERAGE_INCOMPLETE" and
            payload.get("families_expected") == MAX_FAMILIES and
            payload.get("families_present") == len(ACTIVE_FAMILIES),
            "A385_NINE_FAMILY_HOLD_SCOPE_MISMATCH")
    _ensure(payload.get("identity_semantic_mismatch_count") == 0 and
            payload.get("duplicate_exact_identity_count") == 0 and
            payload.get("unmapped_family_instruments") == 0 and
            payload.get("source_contract_ready_in_export") == 0,
            "A385_IDENTITY_OR_READY_COUNTER_CONFLICT")
    _ensure(isinstance(payload.get("source_sha256"), str) and
            SHA.fullmatch(payload["source_sha256"]) is not None and
            _count(payload.get("source_bytes"), 48 * 1024 * 1024) and
            isinstance(payload.get("source_generated_at_utc"), str),
            "A385_SOURCE_IDENTITY_UNVERIFIED")
    try:
        generated = datetime.fromisoformat(payload["source_generated_at_utc"])
        _ensure(generated.utcoffset() is not None, "A385_SOURCE_CLOCK_INVALID")
    except (ValueError, TypeError) as exc:
        raise CounterfactualRejected("A385_SOURCE_CLOCK_INVALID") from exc

    sources = payload.get("source_diagnostics")
    _ensure(isinstance(sources, list) and len(sources) == 3 and
            [x.get("source") for x in sources if isinstance(x, dict)] ==
            ["PPI", "IOL", "BYMA"], "A385_SOURCE_DIAGNOSTICS_INVALID")
    for row in sources:
        _ensure(all(_count(row.get(k)) for k in (
            "records", "present", "structured", "native_fresh", "native_stale",
            "native_unknown", "receipt_present", "exact_identity", "selection_eligible")),
            "A385_SOURCE_DIAGNOSTICS_INVALID")
    _ensure(all(s["selection_eligible"] == 0 for s in sources) and
            sources[0]["present"] == sources[0]["native_fresh"] ==
            sources[0]["receipt_present"] == 0,
            "A385_SOURCE_SCENARIO_NOT_ZERO_BASELINE")

    families = payload.get("families")
    _ensure(isinstance(families, list) and len(families) == MAX_FAMILIES,
            "A385_FAMILY_LIST_INVALID")
    by_family = {}
    for family in families:
        _ensure(isinstance(family, dict) and family.get("family") in ALL_FAMILIES and
                family["family"] not in by_family, "A385_FAMILY_IDENTITY_INVALID")
        for key in ("instruments", "source_contract_ready", "source_contract_not_ready",
                    "source_selection_absent", "identity_unresolved",
                    "first_known_at_not_exported"):
            _ensure(_count(family.get(key)), "A385_FAMILY_COUNTER_INVALID")
        n = family["instruments"]
        _ensure(family["source_contract_ready"] == 0 and
                family["source_contract_not_ready"] == n and
                family["source_selection_absent"] == n and
                family["first_known_at_not_exported"] == n and
                family["identity_unresolved"] == 0,
                "A385_FAMILY_INTERSECTION_UNVERIFIED")
        reasons = family.get("reason_counts")
        _ensure(isinstance(reasons, dict) and all(
            CODE.fullmatch(k) and _count(v) for k, v in reasons.items()),
            "A385_REASON_HISTOGRAM_INVALID")
        _ensure(sum(reasons.values()) == n and "OTHER_REASON_CODES" not in reasons,
                "A385_HISTOGRAM_INCOMPLETE")
        eligible = family.get("eligible_source_counts")
        _ensure(isinstance(eligible, dict) and all(_count(v) and v == 0
                                                for v in eligible.values()),
                "A385_ELIGIBLE_SOURCE_INTERSECTION_UNVERIFIED")
        _ensure(family.get("status") == (
            "NO_FAMILY_SAMPLE_IN_EXPORT" if n == 0 else "NO_SOURCE_CONTRACT_READY"),
            "A385_FAMILY_STATUS_MISMATCH")
        by_family[family["family"]] = family
    _ensure(set(by_family) == ALL_FAMILIES and by_family["ETFS"]["instruments"] == 0 and
            all(by_family[name]["instruments"] > 0 for name in ACTIVE_FAMILIES),
            "A385_NINE_ACTIVE_FAMILIES_REQUIRED")
    catalog = sum(by_family[name]["instruments"] for name in ACTIVE_FAMILIES)
    _ensure(catalog == payload.get("universe_instruments") ==
            payload.get("mapped_family_instruments") and
            all(s["records"] == catalog for s in sources),
            "A385_DENOMINATOR_CONFLICT")
    return [by_family[name] for name in ACTIVE_FAMILIES]


def counterfactual(payload: dict, input_sha256: str) -> dict:
    """Compute nine-family *ceilings*, not individual gate outcomes or fills."""
    rows = _validated_families(payload)
    output = []
    for family in rows:
        n = family["instruments"]
        # This is a classification in A385's reason field, *not* admission.
        preliminary = sum(v for reason, v in family["reason_counts"].items()
                          if reason.startswith("NO_FRESH_STRUCTURED_SOURCE:READY_PAPER_"))
        unresolved = n - preliminary
        _ensure(all(reason.startswith("NO_FRESH_STRUCTURED_SOURCE:")
                    for reason in family["reason_counts"]),
                "A385_REASON_PREFIX_DIVERGED")
        output.append({
            "family": family["family"], "catalog_instruments": n,
            "preliminary_ready_paper_labels": preliminary,
            "explicit_contract_gap_labels": unresolved,
            "contract_gap_codes": {key.split(":", 1)[1]: value
                                   for key, value in family["reason_counts"].items()
                                   if not key.startswith("NO_FRESH_STRUCTURED_SOURCE:READY_PAPER_")},
            "baseline_observed_source_contract_ready": 0,
            "contract_only_observed_source_gated_ceiling": 0,
            "ppi_pit_only_preliminary_contract_ceiling": preliminary,
            "both_ppi_and_contracts_theoretical_catalog_ceiling": n,
            "verified_paper_admissions": 0,
            "verified_postdecision_causal_fills": 0,
            "fci_requires_causal_nav_not_book_fill": family["family"] == "FCI",
            "per_instrument_gate_results": "NOT_CALCULABLE_AGGREGATE_ONLY",
            "book2_or_nav_after_decision_latency": "NO_RECEIPT_IN_A385",
        })
    return {
        "schema": SCHEMA, "status": "AGGREGATE_CEILINGS_ONLY_NO_PAPER_GO",
        "mode": "OFFHOST_PAPER_SHADOW_READ_ONLY", "db_opened": False,
        "provider_calls": 0, "order_routes_called": False,
        "real_orders_sent": 0, "ppi_watch_touched": False,
        "input_a385_json_sha256": input_sha256,
        "input_source_export_sha256": payload["source_sha256"],
        "input_source_generated_at_utc": payload["source_generated_at_utc"],
        "input_a385_status": payload["status"],
        "input_a385_reason": payload["reason"],
        "known_at_missing_in_export": payload["universe_instruments"],
        "catalog_instruments": sum(row["catalog_instruments"] for row in output),
        "preliminary_ready_paper_total": sum(row["preliminary_ready_paper_labels"] for row in output),
        "explicit_contract_gap_total": sum(row["explicit_contract_gap_labels"] for row in output),
        "observed_source_contract_ready_total": 0,
        "verified_paper_admissions_total": 0,
        "verified_postdecision_causal_fills_total": 0,
        "family_rows": output,
        "limitations": [
            "A385_HAS_NO_INSTRUMENT_ROWS_OR_IDENTIFIERS",
            "A385_HAS_NO_AUTHENTIC_PPI_BOOK_OR_NAV",
            "A385_HAS_NO_FIRST_KNOWN_AT_PER_INSTRUMENT",
            "A385_HAS_NO_DECISION_PLUS_LATENCY_PLUS_BOOK2_SEQUENCE",
            "A385_HAS_NO_BROKER_FEES_MARGIN_OR_SETTLEMENT_ENTITLEMENT",
            "SIMULATED_SCENARIOS_ARE_UPPER_BOUNDS_NOT_EVIDENCE_OF_GATE_PASS",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True,
                        help="offhost A385 family_snapshot.json; '-' reads stdin")
    parser.add_argument("--sha256", required=True,
                        help="expected SHA256 of exact A385 JSON bytes")
    args = parser.parse_args(argv)
    try:
        if args.input == "-":
            raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        else:
            path = Path(args.input)
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
                raise CounterfactualRejected("A385_INPUT_PATH_UNSAFE")
            raw = path.read_bytes()
        payload, digest = load_pinned(raw, expected_sha256=args.sha256)
        report = counterfactual(payload, digest)
    except (OSError, CounterfactualRejected) as exc:
        print("A608_FAIL_CLOSED=" + (str(exc) if isinstance(exc, CounterfactualRejected)
                                      else "INPUT_UNAVAILABLE"), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
