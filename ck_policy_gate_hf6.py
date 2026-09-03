"""RC4 policy evaluation and optional PAPER admission authority.

The calculation is pure: no store, broker, SQLite, network, or order access.

Critical invariant
------------------
A policy is evaluated in every mode so SHADOW/OBSERVATION_ONLY can persist a
real counterfactual.  Authority is separate from evaluation:

    would_block   = what the policy would decide from the supplied evidence
    execute_block = whether the current policy mode has BINDING authority

Missing evidence is never silently converted into a PASS.  It is represented
as ``evaluable=False`` with a stable evidence reason.  Sector/regime are
fail-closed if somebody configures them BINDING without the prerequisite
evidence.  Expectancy keeps the historical candidate1 behavior for a short
sample (collect evidence, do not block), but such a sample is NOT eligible for
a future BINDING transition.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation
import os
import re
from typing import Any

ZERO = Decimal("0")

EXPECTANCY_ENV = "PAPER_EXPECTANCY_POLICY"
REGIME_ENV = "PAPER_MARKET_REGIME_POLICY"
SECTOR_ENV = "PAPER_SECTOR_CONCENTRATION_POLICY"
SECTOR_LIMIT_ENV = "PAPER_MAX_POSITIONS_PER_SECTOR"


@dataclass(frozen=True)
class PolicyAssessment:
    policy: str
    mode: str
    evaluable: bool
    evidence_state: str
    verdict: str
    would_block: bool
    execute_block: bool
    binding_eligible: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mode(name: str, default: str) -> str:
    return str(os.getenv(name, default) or default).strip().upper()


def _decimal(value, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} inválido") from exc
    if not result.is_finite():
        raise ValueError(f"{label} no finito")
    return result


def _assessment(*, policy: str, mode: str, evaluable: bool,
                evidence_state: str, verdict: str = "",
                binding_eligible: bool | None = None,
                fail_closed_when_binding: bool = False) -> PolicyAssessment:
    would_block = bool(verdict) if evaluable else False
    eligible = evaluable if binding_eligible is None else bool(binding_eligible)
    execute = mode == "BINDING" and (
        would_block or (fail_closed_when_binding and not evaluable)
    )
    return PolicyAssessment(
        policy=policy,
        mode=mode,
        evaluable=bool(evaluable),
        evidence_state=str(evidence_state),
        verdict=str(verdict),
        would_block=would_block,
        execute_block=execute,
        binding_eligible=eligible,
    )


def assess_expectancy(samples) -> PolicyAssessment:
    """Evaluate empirical expectancy independently from its authority mode."""
    mode = _mode(EXPECTANCY_ENV, "OBSERVATION_ONLY")
    mature = []
    for row in samples or ():
        if not isinstance(row, dict):
            continue
        if str(row.get("sample_state") or "").upper() != "OBSERVATIONAL":
            continue
        try:
            value = _decimal(row.get("empirical_expectancy"), "esperanza empírica")
        except ValueError:
            return _assessment(
                policy="EXPECTANCY", mode=mode, evaluable=False,
                evidence_state="INVALID_EVIDENCE",
                binding_eligible=False)
        mature.append((str(row.get("currency") or "ARS").upper(), value))

    if not mature:
        return _assessment(
            policy="EXPECTANCY", mode=mode, evaluable=False,
            evidence_state="INSUFFICIENT_SAMPLE",
            binding_eligible=False)

    for currency, value in mature:
        if value < ZERO:
            return _assessment(
                policy="EXPECTANCY", mode=mode, evaluable=True,
                evidence_state="OBSERVATIONAL",
                verdict=f"EXPECTANCY_NEGATIVE_{currency}",
                binding_eligible=True)
    return _assessment(
        policy="EXPECTANCY", mode=mode, evaluable=True,
        evidence_state="OBSERVATIONAL",
        binding_eligible=True)


def assess_regime(observation) -> PolicyAssessment:
    """Evaluate breadth without pretending missing/short evidence is a market state."""
    mode = _mode(REGIME_ENV, "ALERT_ONLY")
    if not isinstance(observation, dict):
        return _assessment(
            policy="REGIME", mode=mode, evaluable=False,
            evidence_state="EVIDENCE_REQUIRED",
            binding_eligible=False, fail_closed_when_binding=True)
    state = str(observation.get("state") or "").upper()
    if state == "INSUFFICIENT_SAMPLE":
        return _assessment(
            policy="REGIME", mode=mode, evaluable=False,
            evidence_state=state,
            binding_eligible=False, fail_closed_when_binding=True)
    if state == "BEARISH_BREADTH":
        return _assessment(
            policy="REGIME", mode=mode, evaluable=True,
            evidence_state=state,
            verdict="BEARISH_BREADTH_NO_LONG_ENTRIES",
            binding_eligible=True)
    if state == "MIXED_OR_POSITIVE":
        return _assessment(
            policy="REGIME", mode=mode, evaluable=True,
            evidence_state=state,
            binding_eligible=True)
    return _assessment(
        policy="REGIME", mode=mode, evaluable=False,
        evidence_state="UNKNOWN_STATE",
        binding_eligible=False, fail_closed_when_binding=True)


def assess_sector(observation, candidate_sector=None, *, limit=None,
                  mapping_verified=False) -> PolicyAssessment:
    """Evaluate concentration only from explicit, provenance-verified sector data."""
    mode = _mode(SECTOR_ENV, "OBSERVATION_ONLY")
    try:
        cap = int(limit if limit is not None else os.getenv(SECTOR_LIMIT_ENV, "2"))
    except (TypeError, ValueError) as exc:
        raise ValueError("límite sectorial inválido") from exc
    if cap < 1:
        raise ValueError("límite sectorial debe ser al menos 1")

    sector = str(candidate_sector or "").strip()
    if not mapping_verified or not sector:
        return _assessment(
            policy="SECTOR", mode=mode, evaluable=False,
            evidence_state="SECTOR_MAPPING_REQUIRED",
            binding_eligible=False, fail_closed_when_binding=True)
    if not isinstance(observation, dict):
        return _assessment(
            policy="SECTOR", mode=mode, evaluable=False,
            evidence_state="SECTOR_CONTEXT_REQUIRED",
            binding_eligible=False, fail_closed_when_binding=True)
    if int(observation.get("unmapped_positions") or 0) > 0:
        return _assessment(
            policy="SECTOR", mode=mode, evaluable=False,
            evidence_state="SECTOR_BOOK_MAPPING_INCOMPLETE",
            binding_eligible=False, fail_closed_when_binding=True)

    for group in observation.get("groups") or ():
        if not isinstance(group, dict):
            continue
        if str(group.get("sector") or "").strip() != sector:
            continue
        count = int(group.get("open_positions") or 0)
        if count >= cap:
            stable = re.sub(r"[^A-Z0-9]+", "_", sector.upper()).strip("_") or "SECTOR"
            return _assessment(
                policy="SECTOR", mode=mode, evaluable=True,
                evidence_state="VERIFIED_MAPPING",
                verdict=f"SECTOR_CONCENTRATION_LIMIT_{stable}",
                binding_eligible=True)
        return _assessment(
            policy="SECTOR", mode=mode, evaluable=True,
            evidence_state="VERIFIED_MAPPING",
            binding_eligible=True)

    return _assessment(
        policy="SECTOR", mode=mode, evaluable=True,
        evidence_state="VERIFIED_MAPPING",
        binding_eligible=True)


def evaluate_policies(*, expectancy_samples=None, breadth=None, sectors=None,
                      candidate_sector=None, sector_limit=None,
                      sector_mapping_verified=False) -> dict:
    """Evaluate every policy and return both counterfactual and authority outcome."""
    assessments = (
        assess_expectancy(expectancy_samples),
        assess_regime(breadth),
        assess_sector(
            sectors, candidate_sector, limit=sector_limit,
            mapping_verified=sector_mapping_verified),
    )
    actual_reason = ""
    for item in assessments:
        if item.execute_block:
            actual_reason = item.verdict or item.evidence_state
            break
    return {
        "assessments": [item.as_dict() for item in assessments],
        "would_block_any": any(item.would_block for item in assessments),
        "execute_block_any": any(item.execute_block for item in assessments),
        "execute_block_reason": actual_reason,
        "all_binding_eligible": all(item.binding_eligible for item in assessments),
    }


def admission_block(**kwargs) -> str:
    """Compatibility adapter: return only the reason with actual BINDING authority."""
    return str(evaluate_policies(**kwargs)["execute_block_reason"] or "")


def active_policies() -> dict:
    return {
        "expectancy": _mode(EXPECTANCY_ENV, "OBSERVATION_ONLY"),
        "regime": _mode(REGIME_ENV, "ALERT_ONLY"),
        "sector_concentration": _mode(SECTOR_ENV, "OBSERVATION_ONLY"),
        "sector_limit": int(os.getenv(SECTOR_LIMIT_ENV, "2")),
    }
