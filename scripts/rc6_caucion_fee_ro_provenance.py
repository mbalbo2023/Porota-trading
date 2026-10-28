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
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

LEGACY_AUTHORITY = "PPI_TARIFF_EFFECTIVE_2026-07-01+BYMA_RIGHTS_VERIFIED_2026-08-21"
PUBLIC_SOURCE = "BYMA_2026-08-14_REF06+PPI_2026-07-01_PUBLIC_NOT_ACCOUNT_VERIFIED"
MAX_ROWS = 10000
MAX_TERMS_BYTES = 65536
CENT = Decimal("0.01")
ART = ZoneInfo("America/Argentina/Buenos_Aires")


def _amount(value):
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise ValueError("NONFINITE_AMOUNT")
    return amount


def _money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _invalid_constant(_value):
    raise ValueError("NONFINITE_JSON")


def _when(value):
    at = datetime.fromisoformat(value)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("NAIVE_TIME")
    return at


def _strict_contract(row, terms, principal, interest):
    """Recheck all independent terms before comparing historical fee models."""
    if (row["source"] != "PRODUCTION_PAPER" or row["currency"] != "ARS"
            or row["instrument_id"] != terms["instrument_id"]
            or row["currency"] != terms["currency"]
            or row["fee_payment"] != terms["fee_payment"]
            or row["day_count_basis"] != terms["day_count_basis"]
            or _amount(row["annual_rate_fraction"]) != _amount(terms["annual_rate_fraction"])
            or terms["quoted_total_fees"] is not None
            or terms["fee_quote_principal"] is not None
            or terms["paper_fill_policy"] not in (
                "CONSERVATIVE_NOTIONAL_CAP", "LIVE_PPI_BID_PARTICIPATION_CAP")
            or not terms["metadata_source"].startswith((
                "CONTRACT_EVIDENCE_V2:", "PPI_PRIMARY_LIVE_BOOK+PAPER_POLICY:"))
            or not isinstance(terms["start_date"], str)):
        return False
    minimum = _amount(terms["minimum_principal"])
    available = _amount(terms["available_principal"])
    step = _amount(terms["principal_step"])
    if not (minimum > 0 and step > 0 and minimum <= principal <= available
            and principal % step == 0):
        return False
    start = date.fromisoformat(terms["start_date"])
    opened = _when(row["opened_at"])
    quoted = _when(terms["quoted_at"])
    maturity = _when(row["maturity_at"])
    contract_maturity = _when(terms["maturity_at"])
    settled = _when(row["settled_at"]) if row["settled_at"] else None
    if (opened.astimezone(ART).date() != start
            or maturity != contract_maturity or maturity <= opened
            or quoted > opened
            or (maturity.astimezone(ART).date() - start).days != row["interest_days"]
            or row["status"] not in {"OPEN", "MATURED"}
            or (row["status"] == "MATURED") != (settled is not None)
            or (settled is not None and settled < maturity)
            or interest <= _amount(row["total_fees"])):
        return False
    return True


def _allocation_link(conn, row, terms):
    """Link old allocation decision without accepting it as a broker fill."""
    rec = conn.execute("""SELECT request_id,decision_json FROM paper_caucion_allocations
                         WHERE paper_id=?""", (row["paper_id"],)).fetchone()
    if rec is None:
        return "ABSENT"
    try:
        if (rec[0] != row["request_id"] or not isinstance(rec[1], str)
                or len(rec[1].encode("utf-8")) > MAX_TERMS_BYTES):
            return "INCONSISTENT"
        decision = json.loads(rec[1], parse_constant=_invalid_constant)
        chosen = decision["selected"]
        offers = decision["manifest"]["offers"]
        if (decision["paper_id"] != row["paper_id"]
                or decision["mode"] != "PAPER_CAUCION_PLAN"
                or decision["promotion_allowed"] is not False
                or decision["data_certified"] is not False
                or decision["status"] != "PLACED_SIMULATED"
                or not any(isinstance(item, dict) and item.get("instrument_id") == terms["instrument_id"]
                           and item.get("fee_authority") == LEGACY_AUTHORITY for item in offers)
                or _amount(chosen["principal"]) != _amount(row["principal"])
                or _amount(chosen["gross_interest"]) != _amount(row["gross_interest"])
                or _amount(chosen["fees"]) != _amount(row["total_fees"])):
            return "INCONSISTENT"
        return "LINKED"
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError):
        return "INCONSISTENT"


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
        "strict_contracts_checked": 0, "public_model_nonpositive_net": 0,
        "allocation_linked": 0, "allocation_absent": 0,
        "allocation_inconsistent": 0,
    }
    path = Path(db_path)
    if not path.is_file() or path.is_symlink():
        result["reason"] = "DATABASE_UNAVAILABLE"
        return result
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            cols = {row[1] for row in conn.execute("PRAGMA table_info(paper_cauciones)")}
            required = {"paper_id", "request_fingerprint", "principal", "gross_interest",
                        "total_fees", "status", "terms_json", "interest_days",
                        "request_id", "source", "instrument_id", "currency",
                        "annual_rate_fraction", "day_count_basis", "fee_payment",
                        "opened_at", "maturity_at", "settled_at"}
            if not required.issubset(cols):
                result["reason"] = "SCHEMA_UNAVAILABLE"
                return result
            allocations = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                       "AND name='paper_caucion_allocations'").fetchone() is not None
            rows = conn.execute("SELECT * FROM paper_cauciones LIMIT ?", (MAX_ROWS + 1,))
            for row in rows:
                if result["rows"] >= MAX_ROWS:
                    result["reason"] = "UNBOUNDED_LEDGER"
                    return result
                result["rows"] += 1
                try:
                    paper_id, signature = row["paper_id"], row["request_fingerprint"]
                    raw_principal, raw_interest = row["principal"], row["gross_interest"]
                    raw_fees, status = row["total_fees"], row["status"]
                    raw, days = row["terms_json"], row["interest_days"]
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
                            or _money(principal * rate * days / day_count) != interest
                            or not _strict_contract(row, terms, principal, interest)):
                        raise ValueError("INCONSISTENT_OR_UNSUPPORTED_LEGACY_ROW")
                    result["strict_contracts_checked"] += 1
                    allocation = _allocation_link(conn, row, terms) if allocations else "ABSENT"
                    result[{"LINKED": "allocation_linked", "ABSENT": "allocation_absent",
                            "INCONSISTENT": "allocation_inconsistent"}[allocation]] += 1
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
                    result["public_model_nonpositive_net"] += new >= interest
                except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError):
                    result["unsupported_terms"] += 1
    except (OSError, sqlite3.Error):
        result["reason"] = "READ_ONLY_QUERY_FAILED"
        return result
    if (result["unsupported_terms"] or result["legacy_original_model_mismatches"]
            or result["allocation_inconsistent"]):
        result["reason"] = "HISTORICAL_RECORD_NOT_REPLAYABLE"
    elif result["legacy_rows"] == 0:
        result["reason"] = "NO_HISTORICAL_LEGACY_ROWS"
    else:
        result["status"] = "GREEN"
        result["reason"] = "FORENSIC_REPLAY_ONLY_NOT_BROKER_CERTIFICATION"
    return result
