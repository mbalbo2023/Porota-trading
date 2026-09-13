"""Single PAPER-only decision truth for one RC6 CAUCIONES evaluation cycle.

This controller does not fetch PPI and does not place cauciones. It combines
versioned schedule evidence, canonical snapshots, the aggregate freshness gate,
persisted specialized readiness, and the optional intraday opportunity policy.
The same persisted gate can then be consumed by runtime/dashboard/deploy proof.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from rc6_caucion_fresh_data_agent import evaluate_caucion_fresh_data_agent
from rc6_caucion_intraday_opportunity import (
    OpportunityPolicy,
    OpportunityReference,
    evaluate_intraday_opportunities,
)
from rc6_caucion_readiness_state import persist_gate
from rc6_caucion_schedule_gate import CaucionScheduleEvidence, evaluate_schedule

CONTROLLER_REAL_ORDER_CAPABILITY = False


def evaluate_and_persist_caucion_cycle(
    store,
    canonical_snapshots: Sequence[Mapping[str, Any]],
    *,
    now,
    heartbeat_at,
    schedule_evidence: CaucionScheduleEvidence | None,
    contract_status_by_ticker: Mapping[str, str],
    references: Mapping[str, OpportunityReference] | None = None,
    opportunity_policy: OpportunityPolicy | None = None,
    liquidity_deadline=None,
) -> dict:
    """Evaluate a single cycle and persist its fail-closed specialized truth."""
    schedule = evaluate_schedule(schedule_evidence, now=now)
    gate = evaluate_caucion_fresh_data_agent(
        canonical_snapshots,
        now=now,
        heartbeat_at=heartbeat_at,
        calendar_state=schedule["calendar_state"],
        cutoff_state=schedule["cutoff_state"],
        contract_status_by_ticker=contract_status_by_ticker,
    )
    readiness = persist_gate(store, gate, evaluated_at=now)

    if not readiness["green"]:
        opportunity = {
            "status":"HOLD",
            "code":"CAUCION_SPECIALIZED_READINESS_NOT_GREEN",
            "candidates":[],
            "selected":None,
            "promotion_allowed":False,
            "real_order_capability":False,
        }
    elif opportunity_policy is None:
        opportunity = {
            "status":"HOLD",
            "code":"OPPORTUNITY_POLICY_NOT_CONFIGURED",
            "candidates":[],
            "selected":None,
            "promotion_allowed":False,
            "real_order_capability":False,
        }
    elif liquidity_deadline is None:
        opportunity = {
            "status":"HOLD",
            "code":"LIQUIDITY_DEADLINE_MISSING",
            "candidates":[],
            "selected":None,
            "promotion_allowed":False,
            "real_order_capability":False,
        }
    else:
        opportunity = evaluate_intraday_opportunities(
            canonical_snapshots,
            references or {},
            freshness_gate=gate,
            policy=opportunity_policy,
            now=now,
            liquidity_deadline=liquidity_deadline,
        )

    return {
        "family":"CAUCIONES",
        "schedule":schedule,
        "freshness_gate":gate,
        "readiness":readiness,
        "opportunity":opportunity,
        "real_order_capability":False,
    }
