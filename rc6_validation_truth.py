"""Truth labels for RC6 validation claims.

These labels deliberately separate code delivery from evidence.  A historic
ledger record must never be rendered as a current green operational claim.
"""
from __future__ import annotations

from datetime import datetime, timezone

IMPLEMENTATION_STATES = {"IMPLEMENTED", "NOT_IMPLEMENTED", "UNKNOWN"}
CLAIM_STATES = {
    "VERIFIED_CURRENT", "VERIFIED_HISTORICAL", "STALE", "PENDING",
    "BLOCKED_BY_POLICY",
}


def implementation_status(record: dict | None) -> str:
    value = str((record or {}).get("implementation_status") or "UNKNOWN").upper()
    return value if value in IMPLEMENTATION_STATES else "UNKNOWN"


def historical_claim(record: dict | None) -> str:
    """Classify old immutable evidence without treating it as live runtime."""
    if not record:
        return "PENDING"
    if str(record.get("state") or "").upper() == "GREEN":
        return "VERIFIED_HISTORICAL"
    return "STALE"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
