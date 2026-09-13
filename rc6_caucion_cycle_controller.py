"""Single PAPER-only decision truth for one RC6 CAUCIONES evaluation cycle.

This controller does not fetch PPI and does not place cauciones. It combines
versioned schedule evidence, canonical snapshots, the aggregate freshness gate,
persisted specialized readiness, and the optional intraday opportunity policy.
The same persisted gate can then be consumed by runtime/dashboard/deploy proof.

CAUCIONES spans ARS and USD_MEP. Schedule/cutoff truth is therefore evaluated
per currency and aggregated fail-closed: the ten-ticker family gate cannot be
GREEN unless every currency present in the expected universe has independently
verified OPEN market and broker/PPI cutoff evidence.
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


def _snapshot_currencies(canonical_snapshots) -> tuple[str, ...]:
    values = set()
    for raw in canonical_snapshots or ():
        if isinstance(raw, Mapping):
            value = str(raw.get("currency") or "").strip().upper()
            if value:
                values.add(value)
    return tuple(sorted(values))


def _schedule_hold(reason: str, currencies, per_currency=None) -> dict:
    return {
        "state":"HOLD",
        "calendar_state":"CLOSED",
        "cutoff_state":"CLOSED",
        "reason":reason,
        "currencies":list(currencies),
        "per_currency":dict(per_currency or {}),
    }


def _aggregate_schedule(
    canonical_snapshots,
    *,
    now,
    schedule_evidence: CaucionScheduleEvidence | None = None,
    schedule_evidence_by_currency: Mapping[str, CaucionScheduleEvidence] | None = None,
) -> dict:
    currencies = _snapshot_currencies(canonical_snapshots)
    if not currencies:
        return _schedule_hold("CAUCION_SNAPSHOT_CURRENCY_MISSING", currencies)

    evidence_map = {}
    if isinstance(schedule_evidence_by_currency, Mapping):
        evidence_map = {
            str(k or "").strip().upper(): v
            for k, v in schedule_evidence_by_currency.items()
            if str(k or "").strip()
        }
    elif isinstance(schedule_evidence, CaucionScheduleEvidence):
        # Backward compatibility is safe only for a single-currency cycle.
        if len(currencies) != 1:
            return _schedule_hold("MULTI_CURRENCY_SCHEDULE_EVIDENCE_REQUIRED", currencies)
        evidence_map = {currencies[0]: schedule_evidence}
    else:
        return _schedule_hold("CAUCION_SCHEDULE_EVIDENCE_MISSING", currencies)

    per_currency = {}
    reasons = []
    for currency in currencies:
        evidence = evidence_map.get(currency)
        if not isinstance(evidence, CaucionScheduleEvidence):
            per_currency[currency] = _schedule_hold(
                "CAUCION_SCHEDULE_EVIDENCE_MISSING", (currency,)
            )
            reasons.append(f"{currency}:CAUCION_SCHEDULE_EVIDENCE_MISSING")
            continue
        if str(evidence.currency).upper() != currency:
            per_currency[currency] = _schedule_hold(
                "CAUCION_SCHEDULE_CURRENCY_MISMATCH", (currency,)
            )
            reasons.append(f"{currency}:CAUCION_SCHEDULE_CURRENCY_MISMATCH")
            continue
        result = evaluate_schedule(evidence, now=now)
        per_currency[currency] = result
        if result.get("state") != "OPEN":
            reasons.append(f"{currency}:{result.get('reason') or 'SCHEDULE_NOT_OPEN'}")

    if reasons or set(per_currency) != set(currencies):
        return _schedule_hold(
            reasons[0] if reasons else "CAUCION_SCHEDULE_INCOMPLETE",
            currencies,
            per_currency,
        ) | {"reasons": reasons}

    return {
        "state":"OPEN",
        "calendar_state":"OPEN",
        "cutoff_state":"OPEN",
        "reason":"ALL_CAUCION_CURRENCY_WINDOWS_AND_PPI_CUTOFFS_OPEN",
        "currencies":list(currencies),
        "per_currency":per_currency,
    }


def evaluate_and_persist_caucion_cycle(
    store,
    canonical_snapshots: Sequence[Mapping[str, Any]],
    *,
    now,
    heartbeat_at,
    schedule_evidence: CaucionScheduleEvidence | None = None,
    schedule_evidence_by_currency: Mapping[str, CaucionScheduleEvidence] | None = None,
    contract_status_by_ticker: Mapping[str, str],
    references: Mapping[str, OpportunityReference] | None = None,
    opportunity_policy: OpportunityPolicy | None = None,
    liquidity_deadline=None,
) -> dict:
    """Evaluate a single cycle and persist its fail-closed specialized truth."""
    schedule = _aggregate_schedule(
        canonical_snapshots,
        now=now,
        schedule_evidence=schedule_evidence,
        schedule_evidence_by_currency=schedule_evidence_by_currency,
    )
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
