"""HF6 v2 market-source arbitration.

Live trading decisions must use one complete primary source per family.
A secondary source may validate or raise conflicts, but cannot fill individual
live fields into a decision assembled from another source.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

LIVE_FIELD_MIXING_ALLOWED = False
REAL_ORDER_ROUTING_ALLOWED = False


@dataclass(frozen=True)
class SourceSnapshot:
    source: str
    family: str
    observed_at: datetime
    payload: Mapping[str, Any]
    fresh: bool


@dataclass(frozen=True)
class ArbitrationResult:
    family: str
    primary_source: str
    status: str
    snapshot: Optional[SourceSnapshot]
    reason: str
    secondary_source: Optional[str] = None


# Initial HF6-v2 policy. A3 is validation/contract/history for derivatives,
# not silent live fallback. Promotion of a family to A3 live requires an
# explicit reviewed configuration change and deploy approval.
PRIMARY_LIVE_SOURCE = {
    "ACCIONES": "PPI",
    "CEDEARS": "PPI",
    "BONOS": "PPI",
    "LETRAS": "PPI",
    "ON": "PPI",
    "CAUCIONES": "PPI",
    "FCI_LOCAL": "PPI",
    "LICITACIONES": "PPI",
    "OPCIONES": "PPI",
    "FUTUROS": "PPI",
}

SECONDARY_VALIDATION_SOURCE = {
    "OPCIONES": "A3_PRIMARY",
    "FUTUROS": "A3_PRIMARY",
}


def primary_live_source(family: str) -> str:
    return PRIMARY_LIVE_SOURCE.get(str(family).upper(), "PPI")


def select_live_snapshot(
    family: str,
    snapshots: Mapping[str, SourceSnapshot],
) -> ArbitrationResult:
    """Return a whole-source live snapshot or HOLD.

    This deliberately refuses field-by-field fallback. If the configured
    primary source is unavailable/stale, a secondary source does not silently
    replace it unless the family itself has first been promoted by policy.
    """
    fam = str(family).upper()
    primary = primary_live_source(fam)
    secondary = SECONDARY_VALIDATION_SOURCE.get(fam)
    snap = snapshots.get(primary)
    if snap is None:
        return ArbitrationResult(
            fam, primary, "HOLD_DATA_SOURCE", None,
            f"PRIMARY_SOURCE_MISSING:{primary}", secondary,
        )
    if not snap.fresh:
        return ArbitrationResult(
            fam, primary, "HOLD_DATA_SOURCE", None,
            f"PRIMARY_SOURCE_STALE:{primary}", secondary,
        )
    if snap.family.upper() != fam:
        return ArbitrationResult(
            fam, primary, "HOLD_DATA_SOURCE", None,
            f"PRIMARY_SOURCE_FAMILY_MISMATCH:{primary}", secondary,
        )
    return ArbitrationResult(
        fam, primary, "PRIMARY_SOURCE_READY", snap,
        "WHOLE_SOURCE_SELECTED", secondary,
    )


def compare_parallel_numeric(
    family: str,
    primary_value: Optional[float],
    secondary_value: Optional[float],
    *,
    tolerance_pct: float,
) -> str:
    """Compare two independent observations without choosing/averaging them."""
    if primary_value is None or secondary_value is None:
        return "VALIDATION_INCOMPLETE"
    p = float(primary_value)
    s = float(secondary_value)
    if p == 0:
        return "SOURCE_CONFLICT" if s != 0 else "SOURCES_ALIGNED"
    diff_pct = abs(s - p) / abs(p) * 100.0
    return "SOURCE_CONFLICT" if diff_pct > float(tolerance_pct) else "SOURCES_ALIGNED"


def assert_source_invariants() -> None:
    if LIVE_FIELD_MIXING_ALLOWED is not False:
        raise AssertionError("LIVE_FIELD_MIXING_ALLOWED must remain false")
    if REAL_ORDER_ROUTING_ALLOWED is not False:
        raise AssertionError("REAL_ORDER_ROUTING_ALLOWED must remain false")
    # Initial derivative policy must remain PPI-primary until explicitly promoted.
    for family in ("FUTUROS", "OPCIONES"):
        if PRIMARY_LIVE_SOURCE.get(family) != "PPI":
            raise AssertionError(f"{family} live source changed without reviewed promotion")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
