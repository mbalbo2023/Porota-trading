"""Pure RC6 CAUCIONES intraday opportunity evaluator for PAPER.

It does not fetch data, persist state, access a broker, scrape, or place orders.
A high gross TNA is never enough: canonical executable evidence, a GREEN
freshness gate, exact costs, liquidity compatibility and an explicit versioned
reference policy are mandatory. Missing evidence is HOLD.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from bs_instrument_contracts import aware_datetime, cash_currency
from rc6_caucion_offer_adapter import (
    MAX_DYNAMIC_AGE_SECONDS,
    CaucionOfferAdapterError,
    offer_from_canonical_snapshot,
)
from rc6_caucion_paper_bridge import FRESHNESS_GATE_NAME

BPS = Decimal("10000")


class CaucionOpportunityError(ValueError):
    pass


def _d(value: Any, field: str, *, nonnegative: bool = False) -> Decimal:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CaucionOpportunityError(f"invalid {field}") from exc
    if not out.is_finite() or (nonnegative and out < 0):
        raise CaucionOpportunityError(f"invalid {field}")
    return out


@dataclass(frozen=True)
class OpportunityReference:
    ticker: str
    currency: str
    net_annual_rate_fraction: Decimal
    observed_at: str
    sample_count: int
    source: str

    def __post_init__(self):
        ticker = str(self.ticker or "").strip().upper()
        if not ticker:
            raise CaucionOpportunityError("reference ticker missing")
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "currency", cash_currency(self.currency))
        object.__setattr__(self, "net_annual_rate_fraction",
                           _d(self.net_annual_rate_fraction, "reference net annual rate", nonnegative=True))
        aware_datetime(self.observed_at, "reference observed_at")
        if int(self.sample_count) <= 0:
            raise CaucionOpportunityError("reference sample_count must be positive")
        object.__setattr__(self, "sample_count", int(self.sample_count))
        if not str(self.source or "").strip() or str(self.source).strip().upper() == "UNKNOWN":
            raise CaucionOpportunityError("reference source missing")


@dataclass(frozen=True)
class OpportunityPolicy:
    version: str
    currency: str
    minimum_net_annual_rate_fraction: Decimal
    minimum_advantage_bps: Decimal
    minimum_reference_samples: int
    reference_max_age_seconds: int
    maximum_quote_age_seconds: int

    def __post_init__(self):
        if not str(self.version or "").strip() or str(self.version).strip().upper() == "UNKNOWN":
            raise CaucionOpportunityError("policy version missing")
        object.__setattr__(self, "currency", cash_currency(self.currency))
        object.__setattr__(self, "minimum_net_annual_rate_fraction",
                           _d(self.minimum_net_annual_rate_fraction, "minimum net annual rate", nonnegative=True))
        object.__setattr__(self, "minimum_advantage_bps",
                           _d(self.minimum_advantage_bps, "minimum advantage bps", nonnegative=True))
        if int(self.minimum_reference_samples) < 2:
            raise CaucionOpportunityError("minimum_reference_samples must be >=2")
        if int(self.reference_max_age_seconds) <= 0:
            raise CaucionOpportunityError("reference_max_age_seconds must be positive")
        quote_age = int(self.maximum_quote_age_seconds)
        if quote_age <= 0 or quote_age > MAX_DYNAMIC_AGE_SECONDS:
            raise CaucionOpportunityError("maximum_quote_age_seconds cannot relax canonical TTL")
        object.__setattr__(self, "minimum_reference_samples", int(self.minimum_reference_samples))
        object.__setattr__(self, "reference_max_age_seconds", int(self.reference_max_age_seconds))
        object.__setattr__(self, "maximum_quote_age_seconds", quote_age)


def _gate_error(gate: Mapping[str, Any] | None) -> str:
    if not isinstance(gate, Mapping):
        return "FRESHNESS_GATE_MISSING"
    if gate.get("name") != FRESHNESS_GATE_NAME or gate.get("family") != "CAUCIONES":
        return "FRESHNESS_GATE_IDENTITY_INVALID"
    if gate.get("green") is not True:
        return "FRESHNESS_GATE_RED"
    if gate.get("contract_status") != "READY_PAPER_CANDIDATE":
        return "CONTRACT_GATE_NOT_READY"
    if gate.get("real_order_capability") not in (False, 0):
        return "REAL_ORDER_CAPABILITY_MUST_BE_ZERO"
    return ""


def evaluate_intraday_opportunities(
    canonical_snapshots: Sequence[Mapping[str, Any]],
    references: Mapping[str, OpportunityReference],
    *,
    freshness_gate: Mapping[str, Any],
    policy: OpportunityPolicy,
    now,
    liquidity_deadline,
) -> dict:
    """Rank opportunities without executing them.

    The annualized metric is simple annualization of exact net profit at the
    exact fee-quoted principal. It is a comparison metric only; no reinvestment
    or compounding is assumed.
    """
    if not isinstance(policy, OpportunityPolicy):
        return {"status":"HOLD","code":"OPPORTUNITY_POLICY_NOT_CONFIGURED",
                "candidates":[],"selected":None,"promotion_allowed":False}
    gate_error = _gate_error(freshness_gate)
    if gate_error:
        return {"status":"HOLD","code":gate_error,"candidates":[],"selected":None,
                "promotion_allowed":False,"policy_version":policy.version}

    at = aware_datetime(now, "opportunity evaluation time")
    deadline = aware_datetime(liquidity_deadline, "liquidity deadline")
    if deadline <= at:
        return {"status":"HOLD","code":"LIQUIDITY_DEADLINE_INVALID","candidates":[],
                "selected":None,"promotion_allowed":False,"policy_version":policy.version}

    rows = []
    for raw in canonical_snapshots or ():
        ticker = str(raw.get("ticker") if isinstance(raw, Mapping) else "UNKNOWN").upper()
        row = {"ticker": ticker, "state":"HOLD", "code":"", "net_annual_rate_fraction":None,
               "advantage_bps":None, "principal":None, "net_profit":None}
        try:
            offer = offer_from_canonical_snapshot(
                raw, now=at, max_age_seconds=policy.maximum_quote_age_seconds)
            ticker = offer.instrument_id.upper()
            row["ticker"] = ticker
            if offer.currency != policy.currency:
                row["code"] = "OTHER_CURRENCY"
                rows.append(row); continue
            if aware_datetime(offer.maturity_at) > deadline:
                row["code"] = "MATURITY_OUTSIDE_LIQUIDITY_WINDOW"
                rows.append(row); continue
            principal = offer.fee_quote_principal
            if principal is None or offer.quoted_total_fees is None:
                row["code"] = "EXACT_FEE_BUDGET_REQUIRED"
                rows.append(row); continue
            interest, fees, net = offer.economics(principal)
            if net <= 0:
                row["code"] = "NET_PROFIT_NON_POSITIVE"
                rows.append(row); continue
            annualized = (net / principal * Decimal(offer.day_count_basis)
                          / Decimal(offer.interest_days))
            ref = references.get(ticker) if isinstance(references, Mapping) else None
            if not isinstance(ref, OpportunityReference):
                row["code"] = "REFERENCE_MISSING"
                rows.append(row); continue
            if ref.currency != offer.currency or ref.ticker != ticker:
                row["code"] = "REFERENCE_IDENTITY_MISMATCH"
                rows.append(row); continue
            ref_age = (at - aware_datetime(ref.observed_at)).total_seconds()
            if ref_age < -5 or ref_age > policy.reference_max_age_seconds:
                row["code"] = "REFERENCE_STALE_OR_FUTURE"
                rows.append(row); continue
            if ref.sample_count < policy.minimum_reference_samples:
                row["code"] = "REFERENCE_SAMPLE_INSUFFICIENT"
                rows.append(row); continue
            advantage = (annualized - ref.net_annual_rate_fraction) * BPS
            row.update(principal=str(principal), net_profit=str(net),
                       net_annual_rate_fraction=str(annualized), advantage_bps=str(advantage),
                       reference_rate=str(ref.net_annual_rate_fraction), reference_source=ref.source,
                       reference_samples=ref.sample_count)
            if annualized < policy.minimum_net_annual_rate_fraction:
                row["code"] = "NET_ANNUAL_RATE_BELOW_POLICY_FLOOR"
            elif advantage < policy.minimum_advantage_bps:
                row["code"] = "ADVANTAGE_BELOW_POLICY_THRESHOLD"
            else:
                row["state"] = "OPPORTUNITY_CANDIDATE"
                row["code"] = "VALIDATED_INTRADAY_CAUCION_OPPORTUNITY"
        except (CaucionOfferAdapterError, CaucionOpportunityError, ValueError,
                TypeError, ArithmeticError) as exc:
            row["code"] = "INVALID_CANONICAL_OR_ECONOMIC_EVIDENCE:" + type(exc).__name__
        rows.append(row)

    eligible = [row for row in rows if row["state"] == "OPPORTUNITY_CANDIDATE"]
    selected = None
    if eligible:
        selected = max(
            eligible,
            key=lambda row: (
                Decimal(row["net_annual_rate_fraction"]),
                Decimal(row["advantage_bps"]),
                Decimal(row["net_profit"]),
                row["ticker"],
            ),
        )
    return {
        "status":"OPPORTUNITY_CANDIDATE" if selected else "HOLD",
        "code":"VALIDATED_INTRADAY_CAUCION_OPPORTUNITY" if selected else "NO_VALIDATED_INTRADAY_OPPORTUNITY",
        "currency":policy.currency,
        "policy_version":policy.version,
        "freshness_evidence_id":freshness_gate.get("evidence_id"),
        "candidates":rows,
        "selected":selected,
        "promotion_allowed":False,
        "real_order_capability":False,
    }
