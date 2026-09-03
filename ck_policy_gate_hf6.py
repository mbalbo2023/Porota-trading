"""RC4 policy admission authority for PAPER.

This module is deliberately PURE: it does not know PaperStore, SQLite, broker,
contracts, network or orders.  A separate adapter must build the observations
from real persisted evidence and pass them here.  This avoids the audit's
suggested integration calling methods/fields that do not exist in candidate1.

Defaults preserve candidate1 behavior.  Turning a policy BINDING remains an
explicit release/configuration decision and cannot make another rejected trade
open.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import os
import re

ZERO=Decimal("0")
EXPECTANCY_ENV="PAPER_EXPECTANCY_POLICY"
REGIME_ENV="PAPER_MARKET_REGIME_POLICY"
SECTOR_ENV="PAPER_SECTOR_CONCENTRATION_POLICY"
SECTOR_LIMIT_ENV="PAPER_MAX_POSITIONS_PER_SECTOR"


def _mode(name: str, default: str) -> str:
    return str(os.getenv(name, default) or default).strip().upper()


def _decimal(value, label: str) -> Decimal:
    try:
        result=Decimal(str(value))
    except (InvalidOperation,TypeError,ValueError) as exc:
        raise ValueError(f"{label} inválido") from exc
    if not result.is_finite():
        raise ValueError(f"{label} no finito")
    return result


def expectancy_block(samples) -> str:
    """Block only a mature negative empirical sample when explicitly BINDING."""
    if _mode(EXPECTANCY_ENV,"OBSERVATION_ONLY") != "BINDING":
        return ""
    for row in samples or ():
        if not isinstance(row,dict):
            continue
        if str(row.get("sample_state") or "").upper() != "OBSERVATIONAL":
            continue
        if _decimal(row.get("empirical_expectancy",0),"esperanza empírica") < ZERO:
            currency=str(row.get("currency") or "ARS").upper()
            return f"EXPECTANCY_NEGATIVE_{currency}"
    return ""


def regime_block(observation) -> str:
    """Optional long-entry veto from an already-computed breadth observation."""
    if _mode(REGIME_ENV,"ALERT_ONLY") != "BINDING":
        return ""
    if not isinstance(observation,dict):
        return "REGIME_EVIDENCE_REQUIRED"
    state=str(observation.get("state") or "").upper()
    if state == "INSUFFICIENT_SAMPLE":
        return ""
    if state == "BEARISH_BREADTH":
        return "BEARISH_BREADTH_NO_LONG_ENTRIES"
    if state not in {"MIXED_OR_POSITIVE"}:
        return "REGIME_EVIDENCE_REQUIRED"
    return ""


def sector_block(observation, candidate_sector=None, *, limit=None,
                 mapping_verified=False) -> str:
    """Prevent same-sector concentration without treating unknown as diversified.

    In BINDING mode a candidate must have a verified explicit sector mapping.
    No ticker/name inference is allowed.  The caller also has to confirm that
    the observation was built from verified mappings for the relevant book.
    """
    if _mode(SECTOR_ENV,"OBSERVATION_ONLY") != "BINDING":
        return ""
    try:
        cap=int(limit if limit is not None else os.getenv(SECTOR_LIMIT_ENV,"2"))
    except (TypeError,ValueError) as exc:
        raise ValueError("límite sectorial inválido") from exc
    if cap < 1:
        raise ValueError("límite sectorial debe ser al menos 1")
    sector=str(candidate_sector or "").strip()
    if not mapping_verified or not sector:
        return "SECTOR_MAPPING_REQUIRED"
    if not isinstance(observation,dict):
        return "SECTOR_CONTEXT_REQUIRED"
    if int(observation.get("unmapped_positions") or 0) > 0:
        return "SECTOR_BOOK_MAPPING_INCOMPLETE"
    groups=observation.get("groups") or ()
    for group in groups:
        if not isinstance(group,dict):
            continue
        if str(group.get("sector") or "").strip() == sector:
            if int(group.get("open_positions") or 0) >= cap:
                stable=re.sub(r"[^A-Z0-9]+","_",sector.upper()).strip("_") or "SECTOR"
                return f"SECTOR_CONCENTRATION_LIMIT_{stable}"
            return ""
    return ""


def admission_block(*, expectancy_samples=None, breadth=None, sectors=None,
                    candidate_sector=None, sector_limit=None,
                    sector_mapping_verified=False) -> str:
    """Return the first stable blocking reason, or empty string."""
    for blocker in (
        expectancy_block(expectancy_samples),
        regime_block(breadth),
        sector_block(sectors,candidate_sector,limit=sector_limit,
                     mapping_verified=sector_mapping_verified),
    ):
        if blocker:
            return blocker
    return ""


def active_policies() -> dict:
    return {
        "expectancy":_mode(EXPECTANCY_ENV,"OBSERVATION_ONLY"),
        "regime":_mode(REGIME_ENV,"ALERT_ONLY"),
        "sector_concentration":_mode(SECTOR_ENV,"OBSERVATION_ONLY"),
        "sector_limit":int(os.getenv(SECTOR_LIMIT_ENV,"2")),
    }
