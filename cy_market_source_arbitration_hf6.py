"""HF6 v2 market-source policy.

The live PAPER decision path uses one complete configured primary source per
family. In the initial HF6-v2 policy that source is PPI.

A3/Primary is NOT consulted synchronously to validate a PPI trading decision.
It is a background evidence source for derivative contracts, histories and
post-close/feed-quality analysis. This avoids adding latency or creating false
conflicts from observations taken at different instants.

AUTHORITATIVE RULE
------------------
If PPI has all live fields required by the family and they are fresh, PPI wins
because it is the broker used by Porota. A difference observed later against A3
MUST NOT put a live instrument or position in HOLD and MUST NOT veto a PPI
trading decision. Background differences are diagnostics only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

LIVE_FIELD_MIXING_ALLOWED = False
SYNCHRONOUS_SECONDARY_VALIDATION_ALLOWED = False
BACKGROUND_DIVERGENCE_CAN_HOLD_LIVE = False
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


# Initial HF6-v2 policy. PPI remains the live source for every family until a
# family-level promotion is explicitly reviewed, tested and approved.
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

# These sources may enrich Contract Evidence / History Store or be compared in
# an asynchronous audit. They are not queried by select_live_snapshot().
BACKGROUND_EVIDENCE_SOURCE = {
    "OPCIONES": "A3_PRIMARY",
    "FUTUROS": "A3_PRIMARY",
}


def primary_live_source(family: str) -> str:
    return PRIMARY_LIVE_SOURCE.get(str(family).upper(), "PPI")


def select_live_snapshot(
    family: str,
    snapshots: Mapping[str, SourceSnapshot],
) -> ArbitrationResult:
    """Return the configured whole-source live snapshot or HOLD.

    There is no field-by-field fallback and no synchronous secondary check.
    If PPI is missing/stale/incomplete for the live requirements of the family,
    the result is HOLD until a separately approved family-level source policy
    says otherwise.

    If PPI is complete and fresh, a background A3 difference is irrelevant to
    this function and cannot change the returned READY result.
    """
    fam = str(family).upper()
    primary = primary_live_source(fam)
    snap = snapshots.get(primary)
    if snap is None:
        return ArbitrationResult(
            fam, primary, "HOLD_DATA_SOURCE", None,
            f"PRIMARY_SOURCE_MISSING:{primary}",
        )
    if not snap.fresh:
        return ArbitrationResult(
            fam, primary, "HOLD_DATA_SOURCE", None,
            f"PRIMARY_SOURCE_STALE:{primary}",
        )
    if snap.family.upper() != fam:
        return ArbitrationResult(
            fam, primary, "HOLD_DATA_SOURCE", None,
            f"PRIMARY_SOURCE_FAMILY_MISMATCH:{primary}",
        )
    return ArbitrationResult(
        fam, primary, "PRIMARY_SOURCE_READY", snap,
        "PPI_AUTHORITATIVE_WHEN_COMPLETE_AND_FRESH",
    )


def compare_background_numeric(
    primary_value: Optional[float],
    evidence_value: Optional[float],
    *,
    tolerance_pct: float,
) -> str:
    """Offline/asynchronous quality comparison only.

    This function must never be used as a live trading gate, must never change
    a live readiness state and must never put an instrument/position in HOLD.
    Its output is for diagnostics, source-quality statistics and post-close
    review only.
    """
    if primary_value is None or evidence_value is None:
        return "BACKGROUND_COMPARISON_INCOMPLETE"
    p = float(primary_value)
    s = float(evidence_value)
    if p == 0:
        return "BACKGROUND_DIVERGENCE" if s != 0 else "BACKGROUND_ALIGNED"
    diff_pct = abs(s - p) / abs(p) * 100.0
    return "BACKGROUND_DIVERGENCE" if diff_pct > float(tolerance_pct) else "BACKGROUND_ALIGNED"


def assert_source_invariants() -> None:
    if LIVE_FIELD_MIXING_ALLOWED is not False:
        raise AssertionError("LIVE_FIELD_MIXING_ALLOWED must remain false")
    if SYNCHRONOUS_SECONDARY_VALIDATION_ALLOWED is not False:
        raise AssertionError("SYNCHRONOUS_SECONDARY_VALIDATION_ALLOWED must remain false")
    if BACKGROUND_DIVERGENCE_CAN_HOLD_LIVE is not False:
        raise AssertionError("Background divergence must never hold live PPI data")
    if REAL_ORDER_ROUTING_ALLOWED is not False:
        raise AssertionError("REAL_ORDER_ROUTING_ALLOWED must remain false")
    for family in ("FUTUROS", "OPCIONES"):
        if PRIMARY_LIVE_SOURCE.get(family) != "PPI":
            raise AssertionError(f"{family} live source changed without reviewed promotion")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
