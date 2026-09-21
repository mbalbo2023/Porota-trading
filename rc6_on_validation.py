"""Fail-closed comparison of PPI and IOL fixed-income evidence.

This module never calls a broker and never enables orders. It receives already
captured payloads so a pilot can prove whether two sources describe the same
ON and whether their analytics are comparable.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


REQUIRED_IDENTITY = ("symbol", "market", "currency")


def _decimal(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def validate_on_evidence(ppi: dict | None, iol: dict | None) -> dict:
    """Return a decision record; missing or contradictory fields block readiness."""
    if not isinstance(ppi, dict) or not isinstance(iol, dict):
        return {"state": "BLOCKED", "reason": "MISSING_SOURCE_PAYLOAD"}
    mismatches = []
    for key in REQUIRED_IDENTITY:
        left = str(ppi.get(key) or "").strip().upper()
        right = str(iol.get(key) or "").strip().upper()
        if not left or not right:
            mismatches.append(f"MISSING_{key.upper()}")
        elif left != right:
            mismatches.append(f"IDENTITY_MISMATCH_{key.upper()}")
    for key in ("settlement", "units_per_lot"):
        left, right = ppi.get(key), iol.get(key)
        if left is None or right is None:
            mismatches.append(f"MISSING_{key.upper()}")
        elif str(left).upper() != str(right).upper():
            mismatches.append(f"MISMATCH_{key.upper()}")
    for key in ("price", "clean_price", "dirty_price", "residual_value"):
        if key in ppi and key in iol:
            left, right = _decimal(ppi[key]), _decimal(iol[key])
            if left is None or right is None:
                mismatches.append(f"INVALID_{key.upper()}")
    state = "READY_SHADOW" if not mismatches else "BLOCKED"
    return {
        "state": state,
        "reason": "EVIDENCE_ALIGNED" if not mismatches else " ; ".join(mismatches),
        "mismatches": mismatches,
        "decision_effect": "OBSERVE_ONLY",
    }
