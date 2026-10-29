"""Point-in-time FCI cash-parking strategy, not an order or entry adapter.

The existing FCI allocator owns class, calendar, fee, NAV and cash-budget
arithmetic.  This module adds a deterministic *source-bound* selection policy
and a reservation for known near-term liabilities.  Its result can be used by
an independently reviewed, authenticated PAPER-only adapter; it deliberately
cannot authorize an actual runtime subscription on its own.

An attestation is a claim by an upstream ingestion boundary, not a substitute
for verifying that boundary.  A caller MUST NOT infer that a digest or the
source name proves that PPI/IOL authenticated a class.  In particular, the
existing IOL ``get_fci_funds`` inventory does not contain the required exact
class regulation/calendar/NAV/fee evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal

from bs_instrument_contracts import aware_datetime, decimal_value
from rc6_fund_paper_allocator import (
    FundAllocationBudget,
    FundAllocationEvidence,
    assess_fund_cash_parking,
)


PAPER_ONLY = True
RUNTIME_ENTRY_AUTHORITY = False
REAL_ROUTES_USED = ()
STRATEGY = "FCI_ARS_EXACT_CLASS_LIQUIDITY_PARKING_V1"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_EXTERNAL_UNAUTHENTICATED = "FCI_EXTERNAL_PROVENANCE_NOT_AUTHENTICATED"
_SOURCE_KINDS = frozenset({"PPI_AUTHENTICATED", "IOL_MCP_AUTHENTICATED", "FUND_MANAGER_AUTHENTICATED"})


@dataclass(frozen=True)
class FundSourceBinding:
    """Upstream attestation for a complete evidence snapshot.

    ``binding_sha256`` must be computed over all financially relevant fields,
    including timestamps. The upstream ingestion/authentication boundary is
    outside this pure strategy and MUST be independently verified before any
    runtime entry implementation is allowed.
    """

    class_key: str
    source_kind: str
    binding_sha256: str
    observed_at: str
    authenticated_adapter_id: str


@dataclass(frozen=True)
class FundCashStrategyRisk:
    """Explicit, independent liquidity reserves for short-horizon cash parking."""

    known_liabilities_ars: Decimal
    minimum_remaining_cash_ars: Decimal
    max_source_age_seconds: int
    max_fund_exposure_fraction: Decimal


@dataclass(frozen=True)
class FundStrategyDecision:
    status: str
    reasons: tuple[str, ...]
    class_key: str | None
    amount_ars: Decimal | None
    source_binding_sha256: str | None
    evaluated_at: str
    strategy: str = STRATEGY
    paper_only: bool = True
    entry_authority: bool = False
    real_routes_used: tuple = ()


def evidence_binding_sha256(evidence: FundAllocationEvidence) -> str:
    """Calculate a canonical digest; never treat this as authentication."""
    if not isinstance(evidence, FundAllocationEvidence):
        raise ValueError("FCI_EVIDENCE_REQUIRED")
    payload = json.dumps(asdict(evidence), default=str, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False,
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _amount(value):
    try:
        return decimal_value(value, "FCI_STRATEGY_RISK", nonnegative=True)
    except (TypeError, ValueError):
        return None


def _at(value):
    try:
        return aware_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None


def assess_paper_fund_strategy(evidence: FundAllocationEvidence | None,
                               budget: FundAllocationBudget | None,
                               binding: FundSourceBinding | None,
                               risk: FundCashStrategyRisk | None, *, as_of) -> FundStrategyDecision:
    """Score an exact-class FCI cash-parking intent without submitting it.

    The result ``PAPER_STRATEGY_CANDIDATE`` is a **research/decision result**,
    not a runtime authorization. An independently authenticated source adapter,
    exact catalog readiness, global PAPER admission and a locked ledger guard
    remain mandatory before any future subscription.
    """
    now = _at(as_of)
    if now is None:
        raise ValueError("FCI_STRATEGY_AS_OF_INVALID")
    errors: list[str] = []

    def fail(reason):
        if reason not in errors:
            errors.append(reason)

    # Do not invent class terms, fee schedules or NAV from a fund-name list.
    if not isinstance(evidence, FundAllocationEvidence) or not isinstance(budget, FundAllocationBudget):
        fail("FCI_STRATEGY_CLASS_OR_BUDGET_MISSING")
        return FundStrategyDecision("HOLD", tuple(errors), None, None, None, now.isoformat())

    base = assess_fund_cash_parking(evidence, budget, as_of=now)
    # The legacy allocator intentionally rejects all real external inputs.
    # Keep that safety invariant in place; the new strategy merely assesses
    # externally supplied inputs without making the runtime adapter callable.
    for code in base.reason_codes:
        if code != _EXTERNAL_UNAUTHENTICATED:
            fail(code)
    if evidence.provenance not in _SOURCE_KINDS:
        fail("FCI_STRATEGY_AUTHENTICATED_SOURCE_REQUIRED")
    if _EXTERNAL_UNAUTHENTICATED not in base.reason_codes:
        fail("FCI_STRATEGY_EXTERNAL_EVIDENCE_REQUIRED")

    ce = evidence.class_evidence
    class_key = ce.class_key if hasattr(ce, "class_key") else None
    terms = getattr(ce, "terms", None)
    if terms is None or not hasattr(terms, "paper_subscription_policy"):
        fail("FCI_STRATEGY_EXACT_CLASS_CONTRACT_REQUIRED")
    else:
        # An internal synthetic amount policy must never stand in for actual
        # broker/sociedad gerente subscription denomination rules.
        if (terms.paper_subscription_policy or
                terms.subscription_min is None or terms.subscription_step is None or
                str(terms.broker_subscription_min).upper() == "NO_VERIFICADO" or
                str(terms.broker_subscription_step).upper() == "NO_VERIFICADO"):
            fail("FCI_STRATEGY_BROKER_AMOUNT_TERMS_UNVERIFIED")
    digest = evidence_binding_sha256(evidence)
    if not isinstance(binding, FundSourceBinding):
        fail("FCI_STRATEGY_UPSTREAM_RECEIPT_MISSING")
    else:
        if (binding.class_key != class_key or
                binding.source_kind != evidence.provenance or
                not isinstance(binding.binding_sha256, str) or
                not _DIGEST.fullmatch(binding.binding_sha256) or
                binding.binding_sha256 != digest):
            fail("FCI_STRATEGY_RECEIPT_IDENTITY_OR_PAYLOAD_MISMATCH")
        if not isinstance(binding.authenticated_adapter_id, str) or not binding.authenticated_adapter_id.strip():
            fail("FCI_STRATEGY_SOURCE_ADAPTER_MISSING")

    if not isinstance(risk, FundCashStrategyRisk):
        fail("FCI_STRATEGY_LIQUIDITY_RISK_REQUIRED")
    else:
        liabilities = _amount(risk.known_liabilities_ars)
        remaining = _amount(risk.minimum_remaining_cash_ars)
        original_buffer = _amount(budget.minimum_cash_buffer_ars)
        fraction = _amount(risk.max_fund_exposure_fraction)
        requested = _amount(budget.requested_subscription_ars)
        available = _amount(budget.available_cash_ars)
        if (liabilities is None or remaining is None or original_buffer is None or fraction is None or
                fraction == 0 or fraction > 1 or requested is None or available is None):
            fail("FCI_STRATEGY_RISK_LIMITS_INVALID")
        elif (requested > available * fraction or
              requested + liabilities + max(remaining, original_buffer) > available):
            fail("FCI_STRATEGY_LIABILITY_OR_EXPOSURE_LIMIT")
        if (type(risk.max_source_age_seconds) is not int or
                not 0 < risk.max_source_age_seconds <= 86400):
            fail("FCI_STRATEGY_SOURCE_AGE_POLICY_INVALID")
        elif isinstance(binding, FundSourceBinding):
            observed = _at(binding.observed_at)
            age = (now - observed).total_seconds() if observed else -1
            if observed is None or not 0 <= age <= risk.max_source_age_seconds:
                fail("FCI_STRATEGY_SOURCE_RECEIPT_STALE_OR_FUTURE")

    # ``fee==0`` is currently the only fee model the existing locked ledger
    # can debit correctly. We intentionally reuse its fee check above.
    status = "HOLD" if errors else "PAPER_STRATEGY_CANDIDATE"
    return FundStrategyDecision(status, tuple(errors), class_key,
                                _amount(budget.requested_subscription_ars) if not errors else None,
                                digest if not errors else None, now.isoformat())
