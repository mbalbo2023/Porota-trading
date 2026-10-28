#!/usr/bin/env python3
"""Bounded, read-only diagnostic of old PAPER caucion public-fee assumptions.

An old ``VERIFIED`` label is NOT evidence of broker fees.  This diagnostic
never repairs or accepts that label.  It reports only aggregate counters and
compares immutable history with two *hypothetical* public-tariff models.

Source for the alternative BYMA model: official August 2026 table, page 1
(0.0450% CAUCION trading), page 2 (0.0450% guarantee administration), and
page 3, reference 06 (proportional to term up to 90 days on greater value).
PDF SHA256 f63f26bcecdcbd51263d798e75e0dda7c413afef6f9c2c3c37d487b7132aac87
PPI public ARS colocadora commission: 2% p.a. plus VAT and market rights,
tariff effective 2026-07-01. Account pass-through remains unverified.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import sqlite3

LEGACY_AUTHORITY = "PPI_TARIFF_EFFECTIVE_2026-07-01+BYMA_RIGHTS_VERIFIED_2026-08-21"
PUBLIC_SOURCE = "BYMA_2026-08-14_REF06+PPI_2026-07-01_PUBLIC_NOT_ACCOUNT_VERIFIED"
MAX_ROWS = 10000
MAX_TERMS_BYTES = 65536
CENT = Decimal("0.01")


def _amount(value):
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise ValueError("NONFINITE_AMOUNT")
    return amount


def _money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _invalid_constant(_value):
    raise ValueError("NONFINITE_JSON")


def historical_public_fee_provenance(db_path):
    """No identifiers, monetary amounts, URLs or row data in this receipt."""
    result = {
        "schema": "RC6_CAUCION_FEE_RO_PROVENANCE_V1",
        "status": "RED", "reason": "NOT_CHECKED", "read_only": True,
        "source": PUBLIC_SOURCE, "broker_account_fees_verified": False,
        "accounting_reconciled": False, "data_mutation_attempted": False,
        "rows": 0, "legacy_rows": 0, "legacy_matured": 0,
        "legacy_original_model_matches": 0, "legacy_original_model_mismatches": 0,
        "public_model_higher_than_saved": 0, "public_model_not_higher": 0,
        "unsupported_terms": 0, "other_authority_rows": 0,
    }
    path = Path(db_path)
    if not path.is_file() or path.is_symlink():
        result["reason"] = "DATABASE_UNAVAILABLE"
        return result
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as conn:
            conn.execute("PRAGMA query_only=ON")
            cols = {row[1] for row in conn.execute("PRAGMA table_info(paper_cauciones)")}
            required = {"paper_id", "request_fingerprint", "principal", "gross_interest",
                        "total_fees", "status", "terms_json", "interest_days"}
            if not required.issubset(cols):
                result["reason"] = "SCHEMA_UNAVAILABLE"
                return result
            rows = conn.execute("""SELECT paper_id,request_fingerprint,principal,
                   gross_interest,total_fees,status,terms_json,interest_days
                   FROM paper_cauciones LIMIT ?""", (MAX_ROWS + 1,))
            for paper_id, signature, raw_principal, raw_interest, raw_fees, status, raw, days in rows:
                if result["rows"] >= MAX_ROWS:
                    result["reason"] = "UNBOUNDED_LEDGER"
                    return result
                result["rows"] += 1
                try:
                    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_TERMS_BYTES:
                        raise ValueError("TERMS_SIZE")
                    terms = json.loads(raw, parse_constant=_invalid_constant)
                    if not isinstance(terms, dict):
                        raise ValueError("TERMS_TYPE")
                    authority = terms.get("fee_authority")
                    if authority != LEGACY_AUTHORITY:
                        result["other_authority_rows"] += 1
                        continue
                    result["legacy_rows"] += 1
                    result["legacy_matured"] += status == "MATURED"
                    # Confirm the old terms and capital were not rewritten.
                    principal = _amount(raw_principal)
                    interest = _amount(raw_interest)
                    saved = _amount(raw_fees)
                    rate = _amount(terms["annual_rate_fraction"])
                    day_count = int(terms["day_count_basis"])
                    if (not isinstance(paper_id, str) or not paper_id.startswith("PAPER-CAUCION-")
                            or not isinstance(signature, str) or days not in range(1, 91)
                            or int(terms["day_count_basis"]) not in (360, 365)
                            or principal <= 0 or interest < 0 or saved < 0
                            or any(_money(v) != v for v in (principal, interest, saved))
                            or hashlib.sha256((raw + "|" + format(principal.normalize(), "f")).encode()).hexdigest() != signature
                            or _money(principal * rate * days / day_count) != interest):
                        raise ValueError("INCONSISTENT_OR_UNSUPPORTED_LEGACY_ROW")
                    # Diagnostic of the *old* 365-day annual model, not fee proof.
                    old = _money(principal * (Decimal("0.0200") + Decimal("0.00045"))
                                 * Decimal("1.21") * days / 365)
                    if old != saved:
                        result["legacy_original_model_mismatches"] += 1
                        continue
                    result["legacy_original_model_matches"] += 1
                    # Counterfactual public-source sensitivity; never booked.
                    # Two listed BYMA components; pass-through not certified.
                    new = _money(principal * Decimal("0.02") * Decimal("1.21") * days / 365
                                 + (principal + interest) * Decimal("0.00045")
                                 * Decimal("1.21") * days / 90 * 2)
                    result["public_model_higher_than_saved" if new > saved
                           else "public_model_not_higher"] += 1
                except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError):
                    result["unsupported_terms"] += 1
    except (OSError, sqlite3.Error):
        result["reason"] = "READ_ONLY_QUERY_FAILED"
        return result
    if result["unsupported_terms"] or result["legacy_original_model_mismatches"]:
        result["reason"] = "HISTORICAL_RECORD_NOT_REPLAYABLE"
    elif result["legacy_rows"] == 0:
        result["reason"] = "NO_HISTORICAL_LEGACY_ROWS"
    else:
        result["status"] = "GREEN"
        result["reason"] = "FORENSIC_REPLAY_ONLY_NOT_BROKER_CERTIFICATION"
    return result
