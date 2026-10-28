"""RC6 learning-policy contract: observe first, promote only by evidence.

This module deliberately separates *learning gates* from *hard safety blocks*.
Learning gates start SHADOW/observation-only and can never self-promote. Safety
blocks are not experiments and remain mandatory.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable

LEARNING_STAGES = (
    "COLLECTING_EVIDENCE",
    "SHADOW",
    "SHADOW_VALIDATION",
    "EVIDENCE_SUFFICIENT",
    "ELIGIBLE_FOR_BINDING_DECISION",
    "BINDING_PAPER",
    "BINDING_VALIDATION",
    "BINDING_PAPER_PROVEN",
    "REAL_MONEY_GOVERNANCE",
    "CANARY_REAL_MONEY",
)

LEARNING_POLICIES = (
    "ECONOMIC_GATE",
    "EXPECTANCY",
    "MARKET_REGIME",
    "SECTOR_CONCENTRATION",
)

# These are invariants, not tunable experiments. Keep the word BINDING out of
# operator-facing labels so it cannot be confused with SHADOW->BINDING policy
# promotion.
HARD_SAFETY_BLOCKS = (
    "REAL_ORDER_CAPABILITY_BLOCKED",
    "EXECUTION_SIMULATED",
    "REAL_ORDERS_SENT_ZERO",
    "REAL_ORDER_ROUTING_BLOCKED",
    "CLOSED_SESSION_NO_NEW_EXECUTION",
    "STALE_OR_MISSING_BOOK_NO_EXECUTION",
    "CRITICAL_DB_OR_CONFIG_FAIL_CLOSED",
    "DERIVATIVES_REAL_EXECUTION_BLOCKED",
)


@dataclass(frozen=True)
class LearningPolicy:
    key: str
    stage: str
    authority: str
    can_block_paper: bool
    automatic_promotion: bool
    purpose: str


def policy_contract(key: str, stage: str = "SHADOW") -> LearningPolicy:
    key = str(key or "").upper().strip()
    stage = str(stage or "").upper().strip()
    if key not in LEARNING_POLICIES:
        raise ValueError("LEARNING_POLICY_UNKNOWN")
    if stage not in LEARNING_STAGES:
        raise ValueError("LEARNING_STAGE_UNKNOWN")
    can_block = stage in {"BINDING_PAPER", "BINDING_VALIDATION", "BINDING_PAPER_PROVEN"}
    authority = "BINDING_PAPER" if can_block else "SHADOW"
    return LearningPolicy(
        key=key,
        stage=stage,
        authority=authority,
        can_block_paper=can_block,
        automatic_promotion=False,
        purpose="LEARN_COUNTERFACTUAL_BEFORE_VETO",
    )


def current_learning_contracts() -> tuple[LearningPolicy, ...]:
    """RC6 stays SHADOW except the explicitly authorized sector risk guard."""
    return tuple(
    policy_contract(key, "BINDING_PAPER" if key == "SECTOR_CONCENTRATION" else "SHADOW")
    for key in LEARNING_POLICIES
)


def promotion_is_authorized(*, current_stage: str, requested_stage: str,
                            explicit_operator_authorization: bool,
                            versioned_release: bool,
                            evidence_sufficient: bool,
                            tests_green: bool) -> bool:
    """Return whether a manual promotion request may proceed.

    This function never performs promotion. It only codifies the gate. A future
    caller still needs a versioned configuration change and release workflow.
    """
    current = str(current_stage or "").upper()
    requested = str(requested_stage or "").upper()
    if current not in LEARNING_STAGES or requested not in LEARNING_STAGES:
        return False
    if requested not in {"BINDING_PAPER", "BINDING_VALIDATION", "BINDING_PAPER_PROVEN"}:
        return False
    return all((explicit_operator_authorization, versioned_release,
                evidence_sufficient, tests_green))


def counterfactual_class(*, would_block: bool, realized_net_pnl: float | int | None) -> str:
    """Classify one SHADOW verdict once a realized PAPER outcome exists."""
    if realized_net_pnl is None:
        return "OUTCOME_PENDING"
    pnl = float(realized_net_pnl)
    if would_block and pnl < 0:
        return "TRUE_POSITIVE_LOSS_AVOIDED"
    if would_block and pnl > 0:
        return "FALSE_POSITIVE_GAIN_REMOVED"
    if not would_block and pnl > 0:
        return "TRUE_NEGATIVE_GAIN_ALLOWED"
    if not would_block and pnl < 0:
        return "FALSE_NEGATIVE_LOSS_ALLOWED"
    return "NEUTRAL_ZERO_PNL"


def summarize_counterfactuals(rows: Iterable[dict]) -> dict:
    counts = {
        "evaluated": 0,
        "would_block": 0,
        "would_allow": 0,
        "losses_avoided": 0,
        "gains_removed": 0,
        "gains_allowed": 0,
        "losses_allowed": 0,
        "outcome_pending": 0,
    }
    actual_pnl = 0.0
    hypothetical_blocked_pnl = 0.0
    for row in rows:
        counts["evaluated"] += 1
        block = bool(row.get("would_block"))
        counts["would_block" if block else "would_allow"] += 1
        value = row.get("realized_net_pnl")
        label = counterfactual_class(would_block=block, realized_net_pnl=value)
        if label == "OUTCOME_PENDING":
            counts["outcome_pending"] += 1
            continue
        pnl = float(value or 0)
        actual_pnl += pnl
        if not block:
            hypothetical_blocked_pnl += pnl
        if label == "TRUE_POSITIVE_LOSS_AVOIDED":
            counts["losses_avoided"] += 1
        elif label == "FALSE_POSITIVE_GAIN_REMOVED":
            counts["gains_removed"] += 1
        elif label == "TRUE_NEGATIVE_GAIN_ALLOWED":
            counts["gains_allowed"] += 1
        elif label == "FALSE_NEGATIVE_LOSS_ALLOWED":
            counts["losses_allowed"] += 1
    settled_blocks = counts["losses_avoided"] + counts["gains_removed"]
    precision = (counts["losses_avoided"] / settled_blocks) if settled_blocks else None
    return {
        **counts,
        "actual_paper_pnl": actual_pnl,
        "counterfactual_pnl_if_gate_bound": hypothetical_blocked_pnl,
        "block_precision": precision,
        "automatic_promotion": False,
        "real_money_authorized": False,
    }


def contract_snapshot() -> dict:
    return {
        "learning_policies": [asdict(p) for p in current_learning_contracts()],
        "hard_safety_blocks": list(HARD_SAFETY_BLOCKS),
        "learning_path": list(LEARNING_STAGES),
        "real_money_authorized": False,
        "automatic_promotion": False,
    }
