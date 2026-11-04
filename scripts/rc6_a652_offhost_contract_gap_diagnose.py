#!/usr/bin/env python3
"""A652: bounded OFFHOST RCA triage for A626 contract terms present but held.

The A626 aggregate has no per-instrument rejection reasons.  Without a second,
independently staged OFFHOST evidence sample, they are UNKNOWN.  This script
never connects to SQLite, the host, PPI, the broker, or a trading order route.
It prints counts and fixed reason labels only; private identifiers and source
records never leave memory.  A caller-provided sample is not provider proof.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cp_contract_evidence_v2_hf6 import _assert_sanitized
from rc6_contract_bridge import normalize_group


SCHEMA = "rc6.a652.offhost-contract-rejection-rca.v1"
SAMPLE_SCHEMA = "rc6.a652.offhost-gap-evidence-sample.v1"
AGGREGATE_SCHEMA = "rc6.a626.sql-budget-pid1.consolidated-sanitized.v1"
FAMILIES = ("BONOS", "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI")
IDENTITY = ("ticker", "family", "market", "currency", "settlement")
MAX_AGGREGATE_BYTES = 256 * 1024
MAX_SAMPLE_BYTES = 8 * 1024 * 1024
MAX_CASES = 821
MAX_RECORDS_PER_CASE = 16
SAFE_REASON = re.compile(r"[A-Za-z][A-Za-z0-9_:-]{0,95}\Z")


class Rejected(ValueError):
    """Input rejected without revealing private source contents."""


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise Rejected("DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def _reject_constant(_):
    raise Rejected("NONFINITE_JSON")


def _read_private_json(path, maximum):
    path = Path(path)
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_nlink != 1 or not 0 < info.st_size <= maximum):
            raise Rejected("INPUT_FILE_UNTRUSTED")
        with path.open("rb") as stream:
            raw = stream.read(maximum + 1)
        if len(raw) != info.st_size:
            raise Rejected("INPUT_CHANGED_OR_UNBOUNDED")
        value = json.loads(raw, object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
        if not isinstance(value, dict):
            raise Rejected("INPUT_OBJECT_REQUIRED")
        return value
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        if isinstance(exc, Rejected):
            raise
        raise Rejected("INPUT_UNREADABLE_OR_MALFORMED") from None


def _aware_utc(value):
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if at.tzinfo is None:
            raise ValueError("naive")
        return at.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise Rejected("CLOCK_INVALID") from None


def _source_aggregate(aggregate):
    if aggregate.get("schema") != AGGREGATE_SCHEMA:
        raise Rejected("A626_SCHEMA_INVALID")
    sql = aggregate.get("a626_sql")
    diagnostic = sql.get("gap_terms_diagnostic") if isinstance(sql, dict) else None
    if (not isinstance(sql, dict) or not isinstance(diagnostic, dict)
            or diagnostic.get("state") != "EVIDENCE_ONLY_NO_GO"
            or sql.get("status") != "DIAGNOSTIC_ONLY"
            or aggregate.get("data_collection_status") != "COMPLETE"):
        raise Rejected("A626_NOT_COMPLETE_OR_DIAGNOSTIC")
    by_family = diagnostic.get("by_family")
    if not isinstance(by_family, dict) or set(by_family) != set(FAMILIES):
        raise Rejected("A626_FAMILY_DENOMINATOR_INVALID")
    result = {}
    total_gaps = total_present = 0
    for family in FAMILIES:
        row = by_family[family]
        if not isinstance(row, dict):
            raise Rejected("A626_FAMILY_ROW_INVALID")
        values = [row.get(k) for k in ("gap_rows", "offline_terms_present_not_admitted",
                                    "offline_terms_missing", "no_exact_current_payload")]
        if (any(type(n) is not int or n < 0 for n in values)
                or sum(values[1:]) != values[0]):
            raise Rejected("A626_GAP_ARITHMETIC_INVALID")
        total_gaps += values[0]
        total_present += values[1]
        result[family] = values[1]
    if (total_gaps != diagnostic.get("gap_total")
            or total_gaps != sql.get("capability_gaps_measured")
            or total_gaps > MAX_CASES):
        raise Rejected("A626_TOTAL_MISMATCH")
    run_id = aggregate.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9]{8,20}", run_id):
        raise Rejected("A626_RUN_ID_INVALID")
    measured = _aware_utc(sql.get("measured_at_utc"))
    return run_id, measured, total_gaps, total_present, result


def _safe_reason_list(claim):
    bridge = claim.get("contract_bridge") if isinstance(claim, dict) else None
    if not isinstance(bridge, dict) or bridge.get("status") not in {"NORMALIZED", "BLOCKED"}:
        raise Rejected("BRIDGE_RESULT_INVALID")
    reasons = bridge.get("gaps")
    if not isinstance(reasons, list) or len(reasons) > 64:
        raise Rejected("BRIDGE_GAPS_INVALID")
    if any(not isinstance(item, str) or not SAFE_REASON.fullmatch(item) for item in reasons):
        raise Rejected("BRIDGE_REASON_NOT_SAFELISTED")
    if bridge["status"] == "NORMALIZED" and not reasons:
        return ["NORMALIZED_BUT_CATALOG_GAP_UNEXPLAINED"]
    return sorted(set(reasons)) or ["BRIDGE_BLOCKED_UNATTRIBUTED"]


def _sample_cases(sample, run_id, measured, available):
    if (sample.get("schema") != SAMPLE_SCHEMA or sample.get("a626_run_id") != run_id
            or _aware_utc(sample.get("measured_at_utc")) != measured):
        raise Rejected("SAMPLE_PROVENANCE_CLAIM_MISMATCH")
    cases = sample.get("cases")
    if not isinstance(cases, list) or len(cases) > MAX_CASES:
        raise Rejected("SAMPLE_CASES_INVALID")
    counts = Counter()
    reasons = {family: Counter() for family in FAMILIES}
    identities = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"identity", "catalog_capability", "records"}:
            raise Rejected("SAMPLE_CASE_SHAPE_INVALID")
        key = case["identity"]
        if (not isinstance(key, dict) or set(key) != set(IDENTITY)
                or any(not isinstance(key[field], str) or not 0 < len(key[field]) <= 96
                       or any(c in key[field] for c in "\n\r\x00") for field in IDENTITY)):
            raise Rejected("SAMPLE_IDENTITY_INVALID")
        key_tuple = tuple(key[field] for field in IDENTITY)
        if key_tuple in identities:
            raise Rejected("SAMPLE_IDENTITY_DUPLICATE")
        identities.add(key_tuple)
        family = key["family"]
        cap = case["catalog_capability"]
        if (family not in FAMILIES or not isinstance(cap, str)
                or not SAFE_REASON.fullmatch(cap) or cap.startswith("READY_PAPER_")):
            raise Rejected("SAMPLE_NOT_A_CAPABILITY_GAP")
        records = case["records"]
        if not isinstance(records, list) or not 1 <= len(records) <= MAX_RECORDS_PER_CASE:
            raise Rejected("SAMPLE_RECORDS_INVALID")
        checked = []
        for record in records:
            if (not isinstance(record, dict)
                    or not {"source_class", "source_ref", "observed_at", "evidence_hash", "evidence"}.issubset(record)
                    or any(record.get(field) != key[field] for field in IDENTITY)
                    or not isinstance(record.get("evidence"), dict)):
                raise Rejected("SAMPLE_RECORD_SHAPE_OR_KEY_INVALID")
            try:
                _assert_sanitized(record["evidence"])
            except (ValueError, TypeError):
                raise Rejected("SAMPLE_SENSITIVE_FIELD") from None
            checked.append(record)
        counts[family] += 1
        if counts[family] > available[family]:
            raise Rejected("SAMPLE_EXCEEDS_A626_COHORT")
        # Existing consumer logic decides why a *supplied* group is held.
        # No catalog, DB, provider, or PAPER state is modified or inferred.
        try:
            claim = normalize_group(checked, now=measured)
        except Exception:  # Supplied JSON is untrusted; never leak bridge exception text.
            reasons[family]["BRIDGE_INPUT_REJECTED"] += 1
            continue
        for reason in _safe_reason_list(claim):
            reasons[family][reason] += 1
    return counts, reasons


def diagnose(aggregate, sample=None):
    run_id, measured, gaps, present, available = _source_aggregate(aggregate)
    counts, reasons = (_sample_cases(sample, run_id, measured, available)
                       if sample is not None else (Counter(), {f: Counter() for f in FAMILIES}))
    rows = {}
    for family in FAMILIES:
        measured_count = available[family]
        rows[family] = {
            "a626_offline_terms_present_not_admitted": measured_count,
            "offhost_sampled": counts[family],
            "unattributed_without_offhost_sample": measured_count - counts[family],
            "sample_bridge_reasons": dict(sorted(reasons[family].items())),
        }
    return {
        "schema": SCHEMA,
        "a626_run_id": run_id,
        "a626_measured_at_utc": measured.isoformat(),
        "a626_all_contract_gaps": gaps,
        "a626_terms_present_not_admitted": present,
        "sampled": sum(counts.values()),
        "unattributed": present - sum(counts.values()),
        "sample_status": "NONE" if sample is None else "CALLER_PROVIDED_OFFHOST_NOT_AUTHENTICATED",
        "by_family": rows,
        "provider_authenticity_verified": False,
        "ppi_full_five_key_verified": False,
        "causal_pit_book_verified": False,
        "paper_instrument_ready": False,
        "runtime_real_orders_sent": "NO_VERIFICADO",
        "mutation_operations": 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="A652 OFFHOST aggregate-only RCA (never PAPER READY)")
    parser.add_argument("--aggregate", required=True)
    parser.add_argument("--sample")
    args = parser.parse_args(argv)
    try:
        aggregate = _read_private_json(args.aggregate, MAX_AGGREGATE_BYTES)
        sample = _read_private_json(args.sample, MAX_SAMPLE_BYTES) if args.sample else None
        result = diagnose(aggregate, sample)
    except Rejected as exc:
        print("RC6_A652_FAIL_CLOSED:" + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
