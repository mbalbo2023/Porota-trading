"""Pure, evidence-preserving SHADOW profile evaluation for RC6.

This module deliberately has no database, network, timer, collector, order or
runtime dependencies. Callers pass a frozen decision snapshot and receive the
factual baseline plus hypothetical profiles. The returned values are for
learning only: they cannot authorize or alter an order.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


MODE = "SHADOW_DUAL_EVALUATION"
BASELINE_CONSERVATIVE_V1 = "BASELINE_CONSERVATIVE_V1"
SHADOW_BALANCED_V1 = "SHADOW_BALANCED_V1"
SHADOW_AGGRESSIVE_V1 = "SHADOW_AGGRESSIVE_V1"
PROFILE_ORDER = (
    BASELINE_CONSERVATIVE_V1,
    SHADOW_BALANCED_V1,
    SHADOW_AGGRESSIVE_V1,
)

_OPEN_ACTIONS = frozenset({"BUY", "OPEN", "OPEN_SIMULATED", "ENTER"})


@dataclass(frozen=True)
class _Policy:
    score_threshold_scale: Decimal
    max_spread_scale: Decimal
    confirmation_delta: int


# These are explicit hypothesis parameters, not factual strategy parameters.
# They may only change after a versioned policy decision and accumulated evidence.
_POLICIES = {
    SHADOW_BALANCED_V1: _Policy(Decimal("0.90"), Decimal("1.10"), -1),
    SHADOW_AGGRESSIVE_V1: _Policy(Decimal("0.80"), Decimal("1.25"), -1),
}


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _hard_safety(snapshot: Mapping[str, Any]) -> tuple[bool | None, list[str]]:
    """Return ``None`` when safety evidence is absent; never assume it passed."""
    safety = snapshot.get("hard_safety")
    if not isinstance(safety, Mapping) or not safety:
        return None, ["HARD_SAFETY_EVIDENCE_MISSING"]
    failed = [str(name) for name, passed in safety.items() if passed is not True]
    return not failed, failed


def _factual(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    factual = snapshot.get("factual")
    if not isinstance(factual, Mapping):
        return {}
    return {
        "action": _text(factual.get("action")),
        "score": factual.get("score"),
        "reason": _text(factual.get("reason")),
        "decision_key": _text(factual.get("decision_key") or snapshot.get("decision_key")),
    }


def _insufficient(profile: str, factual: Mapping[str, Any], missing: list[str]) -> dict[str, Any]:
    return {
        "profile": profile,
        "mode": MODE,
        "state": "INSUFFICIENT_EVIDENCE",
        "action": None,
        "reason_codes": missing,
        "factual_action": factual.get("action") or None,
        "can_affect_factual": False,
    }


def _baseline(factual: Mapping[str, Any]) -> dict[str, Any]:
    if not factual.get("action"):
        return _insufficient(BASELINE_CONSERVATIVE_V1, factual, ["FACTUAL_DECISION_MISSING"])
    return {
        "profile": BASELINE_CONSERVATIVE_V1,
        "mode": MODE,
        "state": "FACTUAL_REFERENCE",
        "action": factual["action"],
        "score": factual.get("score"),
        "reason_codes": ["FROZEN_FACTUAL_DECISION"],
        "factual_action": factual["action"],
        "can_affect_factual": False,
    }


def _evaluate(profile: str, snapshot: Mapping[str, Any], factual: Mapping[str, Any]) -> dict[str, Any]:
    hard_passed, failed_gates = _hard_safety(snapshot)
    if hard_passed is None:
        return _insufficient(profile, factual, failed_gates)
    if not hard_passed:
        return {
            "profile": profile, "mode": MODE, "state": "HARD_SAFETY_BLOCKED",
            "action": "HOLD", "reason_codes": ["HARD_SAFETY_GATE"] + failed_gates,
            "factual_action": factual.get("action") or None, "can_affect_factual": False,
        }
    candidate = snapshot.get("candidate")
    if not isinstance(candidate, Mapping):
        return _insufficient(profile, factual, ["CANDIDATE_EVIDENCE_MISSING"])
    score = _decimal(candidate.get("score"))
    score_threshold = _decimal(candidate.get("score_threshold"))
    spread_bps = _decimal(candidate.get("spread_bps"))
    max_spread_bps = _decimal(candidate.get("max_spread_bps"))
    confirmations = candidate.get("confirmation_count")
    required_confirmations = candidate.get("required_confirmations")
    missing = []
    if _text(candidate.get("action")).upper() not in _OPEN_ACTIONS:
        missing.append("OPEN_CANDIDATE_MISSING")
    if score is None or score_threshold is None:
        missing.append("SCORE_EVIDENCE_MISSING")
    if spread_bps is None or max_spread_bps is None:
        missing.append("SPREAD_EVIDENCE_MISSING")
    if not isinstance(confirmations, int) or not isinstance(required_confirmations, int):
        missing.append("CONFIRMATION_EVIDENCE_MISSING")
    if missing:
        return _insufficient(profile, factual, missing)
    policy = _POLICIES[profile]
    thresholds = {
        "score": score_threshold * policy.score_threshold_scale,
        "spread_bps": max_spread_bps * policy.max_spread_scale,
        "confirmations": max(0, required_confirmations + policy.confirmation_delta),
    }
    failures = []
    if score < thresholds["score"]:
        failures.append("SCORE_BELOW_PROFILE_THRESHOLD")
    if spread_bps > thresholds["spread_bps"]:
        failures.append("SPREAD_ABOVE_PROFILE_LIMIT")
    if confirmations < thresholds["confirmations"]:
        failures.append("CONFIRMATIONS_BELOW_PROFILE_THRESHOLD")
    return {
        "profile": profile,
        "mode": MODE,
        "state": "EVALUATED",
        # This is an entry-criteria counterfactual, not a simulated fill:
        # downstream sizing/risk gates still require their own frozen evidence.
        "action": "HOLD" if failures else "CANDIDATE_OPEN",
        "reason_codes": failures or ["PROFILE_ENTRY_CRITERIA_MET"],
        "factual_action": factual.get("action") or None,
        "can_affect_factual": False,
        "effective_thresholds": {
            "score": str(thresholds["score"]),
            "max_spread_bps": str(thresholds["spread_bps"]),
            "required_confirmations": thresholds["confirmations"],
        },
    }


def evaluate_profiles(frozen_snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate versioned shadow profiles without mutating the snapshot.

    A missing input is reported as ``INSUFFICIENT_EVIDENCE``. Hard-safety gates
    are mandatory for every shadow profile and cannot be relaxed by this module.
    """
    if not isinstance(frozen_snapshot, Mapping):
        frozen_snapshot = {}
    snapshot = deepcopy(dict(frozen_snapshot))
    factual = _factual(snapshot)
    profiles = {
        name: _baseline(factual) if name == BASELINE_CONSERVATIVE_V1
        else _evaluate(name, snapshot, factual)
        for name in PROFILE_ORDER
    }
    return {
        "mode": MODE,
        "decision_key": factual.get("decision_key") or None,
        "factual": factual,
        "profiles": profiles,
        "writes": False,
        "orders": False,
    }
