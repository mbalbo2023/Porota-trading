"""Fail-closed comparison of PPI and IOL fixed-income evidence.

PPI is authoritative for operational identity, price and settlement. IOL is
complementary for contract terms and analytics. This module never calls brokers,
changes decisions, or enables orders.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

PRIMARY_SOURCE = "PPI"
COMPLEMENTARY_SOURCE = "IOL"
AUTHORITY = "PPI_PRIMARY_IOL_COMPLEMENTARY"

_REQUIRED_PPI = (
    "symbol", "market", "currency", "settlement", "units_per_lot", "isin",
    "source", "observed_at", "price", "price_basis",
)
_REQUIRED_IOL = (
    "symbol", "market", "currency", "settlement", "units_per_lot", "isin",
    "source", "observed_at", "price", "price_basis", "clean_price",
    "dirty_price", "accrued_interest", "residual_value", "maturity",
    "cash_flows", "yield_to_maturity", "modified_duration",
)
_COMPARED_NUMBERS = (
    "price", "clean_price", "dirty_price", "accrued_interest", "residual_value",
)


def _decimal(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _instant(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            return None
        return result
    except (TypeError, ValueError):
        return None


def validate_on_evidence(ppi: dict | None, iol: dict | None, *,
                         price_tolerance_pct=None, max_snapshot_age_seconds=120) -> dict:
    """Compare a matched snapshot; missing policy or evidence always blocks."""
    mismatches = []
    if not isinstance(ppi, dict) or not isinstance(iol, dict):
        return {
            "state": "BLOCKED", "reason": "MISSING_SOURCE_PAYLOAD",
            "mismatches": ["MISSING_SOURCE_PAYLOAD"], "decision_effect": "OBSERVE_ONLY",
            "authority": AUTHORITY,
        }

    if _decimal(price_tolerance_pct) is None or _decimal(price_tolerance_pct) < 0:
        mismatches.append("PRICE_TOLERANCE_POLICY_REQUIRED")

    for source_name, payload, required in (
        (PRIMARY_SOURCE, ppi, _REQUIRED_PPI),
        (COMPLEMENTARY_SOURCE, iol, _REQUIRED_IOL),
    ):
        for key in required:
            value = payload.get(key)
            if value is None or value == "" or (key == "cash_flows" and not value):
                mismatches.append(f"MISSING_{source_name}_{key.upper()}")
        actual_source = str(payload.get("source") or "").strip().upper()
        if actual_source and not actual_source.startswith(source_name):
            mismatches.append(f"WRONG_SOURCE_{source_name}")
        observed = _instant(payload.get("observed_at"))
        if observed is None:
            mismatches.append(f"INVALID_{source_name}_OBSERVED_AT")

    identity = ("symbol", "market", "currency", "settlement", "units_per_lot", "isin")
    for key in identity:
        left = str(ppi.get(key) or "").strip().upper()
        right = str(iol.get(key) or "").strip().upper()
        if left and right and left != right:
            mismatches.append(f"IDENTITY_MISMATCH_{key.upper()}")

    ppi_time = _instant(ppi.get("observed_at"))
    iol_time = _instant(iol.get("observed_at"))
    if ppi_time is not None and iol_time is not None:
        skew = abs((ppi_time - iol_time).total_seconds())
        if skew > max_snapshot_age_seconds:
            mismatches.append("SNAPSHOT_TIME_SKEW")
        age = max(0.0, (datetime.now(ppi_time.tzinfo) - ppi_time).total_seconds())
        if age > max_snapshot_age_seconds:
            mismatches.append("PPI_SNAPSHOT_STALE")

    ppi_basis = str(ppi.get("price_basis") or "").strip().upper()
    iol_basis = str(iol.get("price_basis") or "").strip().upper()
    if ppi_basis not in {"CLEAN", "DIRTY"} or iol_basis not in {"CLEAN", "DIRTY"}:
        mismatches.append("PRICE_BASIS_UNVERIFIED")
    elif ppi_basis != iol_basis:
        mismatches.append("PRICE_BASIS_MISMATCH")

    tolerance = _decimal(price_tolerance_pct)
    for key in _COMPARED_NUMBERS:
        left, right = _decimal(ppi.get(key)), _decimal(iol.get(key))
        if left is None:
            mismatches.append(f"MISSING_OR_INVALID_PPI_{key.upper()}")
        if right is None:
            mismatches.append(f"MISSING_OR_INVALID_IOL_{key.upper()}")
        if left is not None and right is not None and tolerance is not None:
            denominator = abs(left) if left else Decimal("1")
            difference_pct = abs(left - right) / denominator * Decimal("100")
            if difference_pct > tolerance:
                mismatches.append(f"VALUE_MISMATCH_{key.upper()}")

    # Contract terms are complementary IOL evidence; PPI remains authoritative
    # for executable identity, quote, book and settlement.
    iol_maturity = str(iol.get("maturity") or "").strip()[:10]
    if not iol_maturity:
        mismatches.append("MISSING_IOL_MATURITY")

    flows = iol.get("cash_flows")
    if not isinstance(flows, (list, tuple)) or not flows:
        mismatches.append("IOL_CASH_FLOWS_MISSING")
    for key in ("yield_to_maturity", "modified_duration"):
        if _decimal(iol.get(key)) is None:
            mismatches.append(f"IOL_{key.upper()}_INVALID")

    mismatches = list(dict.fromkeys(mismatches))
    state = "READY_SHADOW" if not mismatches else "BLOCKED"
    return {
        "state": state,
        "reason": "EVIDENCE_ALIGNED" if not mismatches else "; ".join(mismatches),
        "mismatches": mismatches,
        "decision_effect": "OBSERVE_ONLY",
        "authority": AUTHORITY,
        "primary_source": PRIMARY_SOURCE,
        "complementary_source": COMPLEMENTARY_SOURCE,
    }
