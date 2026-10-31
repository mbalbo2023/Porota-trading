"""Fail-closed FCI cash-parking assessment and offline PAPER test adapter.

This module never obtains or authenticates provider evidence. An explicit
synthetic class/fee/calendar/NAV fixture can exercise the durable PAPER
lifecycle without suggesting that a real fund class is READY. For actual
classes, the missing authenticated broker/fund-manager source adapter remains
an independent blocking dependency. There are no real-order routes here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bs_instrument_contracts import aware_datetime, decimal_value
from rc6_fund_paper_reconciler import FundClassEvidence
from rc6_paper_family_lifecycle import FamilyPaperExecutor, PaperFundTerms


PAPER_ONLY = True
REAL_ROUTES_USED = ()
RUNTIME_ENTRY_AUTHORITY = False
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_HHMM = re.compile(r"([01]\d|2[0-3]):([0-5]\d)\Z")


@dataclass(frozen=True)
class FundNavMark:
    nav_per_unit: Decimal
    effective_at: str
    published_at: str
    recorded_at: str
    source_reference: str
    source_sha256: str


@dataclass(frozen=True)
class FundAllocationEvidence:
    class_evidence: FundClassEvidence
    provenance: str  # SYNTHETIC_TEST_ONLY or UNVERIFIED_EXTERNAL
    calendar_date: str  # explicit fund calendar session, not inferred weekday
    calendar_reference: str
    calendar_sha256: str
    calendar_known_at: str
    cutoff_local_hhmm: str
    cutoff_zone: str
    redemption_lead_days: int  # evidence-specific operational assumption
    subscription_fee_ars: Decimal | None
    fee_reference: str
    fee_sha256: str
    fee_known_at: str
    last_nav: FundNavMark | None


@dataclass(frozen=True)
class FundAllocationBudget:
    available_cash_ars: Decimal
    minimum_cash_buffer_ars: Decimal
    maximum_position_ars: Decimal
    requested_subscription_ars: Decimal
    holding_horizon_days: int
    maximum_nav_age_days: int


@dataclass(frozen=True)
class FundCashProjection:
    """Pure ARS cash arithmetic, not a subscription or broker authorization.

    ``subscription_net_ars`` is the intended fund amount *before* account
    fees. The eventual locked lifecycle must debit ``gross_cash_debit_ars``
    exactly once before a nonzero fee can ever be admitted. At present the
    executor debits the net amount only, so nonzero fees remain HOLD.
    """

    subscription_net_ars: Decimal
    fee_ars: Decimal
    gross_cash_debit_ars: Decimal
    minimum_cash_required_ars: Decimal
    cash_after_debit_ars: Decimal
    cash_after_buffer_ars: Decimal
    within_position_limit: bool
    within_cash_limit: bool
    paper_only: bool = True
    runtime_entry_authority: bool = False


@dataclass(frozen=True)
class FundAllocationDecision:
    status: str
    reason_codes: tuple[str, ...]
    amount_ars: Decimal | None
    evaluated_at: str
    source_provenance: str
    strategy: str = "FCI_ARS_CASH_PARKING_NO_RETURN_FORECAST"
    paper_only: bool = True
    real_routes_used: tuple = ()
    runtime_entry_authority: bool = False


def _at(value):
    try:
        return aware_datetime(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _amount(value):
    try:
        return decimal_value(value, "FCI_AMOUNT", nonnegative=True)
    except (ValueError, TypeError):
        return None


def _is_hash(value):
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _source(reference, digest, known_at, at, *, synthetic):
    if not isinstance(reference, str) or not reference.strip() or not _is_hash(digest):
        return False
    if synthetic and not reference.startswith("SYNTHETIC_"):
        return False
    stamp = _at(known_at)
    return stamp is not None and stamp <= at


def project_fund_subscription_cash(*, subscription_net_ars, fee_ars,
                                   available_cash_ars, minimum_cash_buffer_ars,
                                   maximum_position_ars) -> FundCashProjection:
    """Calculate fee-inclusive cash needs without granting entry authority.

    An unknown, negative or non-finite fee is a hard error. This projection
    cannot mutate a PAPER ledger, authenticate a source, or release the
    separate nonzero-fee guard in ``assess_fund_cash_parking``.
    """
    net = _amount(subscription_net_ars)
    fee = _amount(fee_ars)
    available = _amount(available_cash_ars)
    buffer = _amount(minimum_cash_buffer_ars)
    maximum = _amount(maximum_position_ars)
    if any(x is None for x in (net, fee, available, buffer, maximum)) or net == 0:
        raise ValueError("FCI_CASH_PROJECTION_INVALID")
    gross = net + fee
    after_debit = available - gross
    return FundCashProjection(
        net, fee, gross, gross + buffer, after_debit, after_debit - buffer,
        net <= maximum, after_debit >= buffer,
    )


def assess_fund_cash_parking(evidence: FundAllocationEvidence | None,
                             budget: FundAllocationBudget | None, *, as_of) -> FundAllocationDecision:
    """Assess explicit, point-in-time inputs; no projected returns or NAV fills.

    An accepted result is *synthetic offline test only*. External inputs stay
    on HOLD until a separately reviewed authenticated provider adapter exists.
    """
    at = _at(as_of)
    if at is None:
        raise ValueError("FCI_AS_OF_INVALID")
    codes: list[str] = []

    def block(code):
        if code not in codes:
            codes.append(code)

    if not isinstance(evidence, FundAllocationEvidence):
        block("FCI_CLASS_SCHEDULE_FEES_NAV_MISSING")
    if not isinstance(budget, FundAllocationBudget):
        block("FCI_RISK_BUDGET_MISSING")
    if not isinstance(evidence, FundAllocationEvidence) or not isinstance(budget, FundAllocationBudget):
        return FundAllocationDecision("HOLD", tuple(codes), None, at.isoformat(), "NO_SOURCE")

    synthetic = evidence.provenance == "SYNTHETIC_TEST_ONLY"
    if not synthetic:
        block("FCI_EXTERNAL_PROVENANCE_NOT_AUTHENTICATED")
    ce = evidence.class_evidence
    if not isinstance(ce, FundClassEvidence) or not isinstance(ce.terms, PaperFundTerms):
        block("FCI_EXACT_CLASS_UNKNOWN")
    else:
        terms = ce.terms
        # PPI catalogs local fund classes under BYMA; the specialized ledger
        # also accepts the FCI venue. Preserve the exact supplied venue rather
        # than rewriting it or treating it as a source authentication claim.
        if ((terms.family, terms.currency, ce.class_key) != ("FCI", "ARS", terms.symbol)
                or terms.market not in {"FCI", "BYMA"}):
            block("FCI_EXACT_CLASS_MISMATCH")
        if not _source(ce.regulation_reference, ce.regulation_sha256,
                       ce.known_at, at, synthetic=synthetic):
            block("FCI_EXACT_REGULATION_UNVERIFIED_AT_PIT")
        if synthetic and not terms.metadata_source.startswith("SYNTHETIC_"):
            block("FCI_SYNTHETIC_TERMS_SCOPE_MISMATCH")
    if not _source(evidence.calendar_reference, evidence.calendar_sha256,
                   evidence.calendar_known_at, at, synthetic=synthetic):
        block("FCI_CLASS_CALENDAR_UNVERIFIED_AT_PIT")
    if not _source(evidence.fee_reference, evidence.fee_sha256,
                   evidence.fee_known_at, at, synthetic=synthetic):
        block("FCI_ACCOUNT_FEES_UNVERIFIED_AT_PIT")
    fee = _amount(evidence.subscription_fee_ars)
    if fee is None:
        block("FCI_EXPLICIT_ACCOUNT_FEE_UNKNOWN")
    elif fee != 0:
        # The existing durable executor reserves only the subscription amount.
        # Until a fee-aware atomic debit exists, accepting an extra fee would
        # pretend that unreserved cash is available for a second strategy.
        block("FCI_NONZERO_FEE_NOT_MODELED_IN_LEDGER")

    local = None
    match = _HHMM.fullmatch(evidence.cutoff_local_hhmm) if isinstance(
        evidence.cutoff_local_hhmm, str) else None
    try:
        if evidence.cutoff_zone != "America/Argentina/Buenos_Aires":
            raise ValueError
        local = at.astimezone(ZoneInfo(evidence.cutoff_zone))
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        block("FCI_CLASS_CUTOFF_ZONE_UNKNOWN")
    if not match:
        block("FCI_CLASS_CUTOFF_UNKNOWN")
    if local is not None:
        if evidence.calendar_date != local.date().isoformat():
            block("FCI_CLASS_CALENDAR_DATE_NOT_PROVEN")
        if match and (local.hour, local.minute, local.second, local.microsecond) >= (
                int(match[1]), int(match[2]), 0, 0):
            block("FCI_CLASS_CUTOFF_PASSED")

    nav = evidence.last_nav
    if not isinstance(nav, FundNavMark):
        block("FCI_NAV_POINT_IN_TIME_MISSING")
    else:
        nav_price = _amount(nav.nav_per_unit)
        effective, published, recorded = map(_at, (
            nav.effective_at, nav.published_at, nav.recorded_at))
        if nav_price is None or nav_price == 0:
            block("FCI_NAV_NONPOSITIVE")
        if not _source(nav.source_reference, nav.source_sha256, nav.recorded_at,
                       at, synthetic=synthetic):
            block("FCI_NAV_SOURCE_UNVERIFIED_AT_PIT")
        if not all((effective, published, recorded)) or not (
                effective <= published <= recorded <= at):
            block("FCI_NAV_FUTURE_OR_CLOCK_INCONSISTENT")
        elif (not isinstance(budget.maximum_nav_age_days, int)
              or isinstance(budget.maximum_nav_age_days, bool)
              or budget.maximum_nav_age_days < 0
              or (at - effective).total_seconds() > 86400 * budget.maximum_nav_age_days):
            block("FCI_NAV_TOO_OLD_OR_AGE_LIMIT_UNKNOWN")

    amount = _amount(budget.requested_subscription_ars)
    available = _amount(budget.available_cash_ars)
    buffer = _amount(budget.minimum_cash_buffer_ars)
    maximum = _amount(budget.maximum_position_ars)
    if any(value is None for value in (amount, available, buffer, maximum)) or amount == 0:
        block("FCI_CASH_BUDGET_INVALID")
    else:
        if amount > maximum or amount + buffer > available:
            block("FCI_CASH_OR_POSITION_LIMIT")
        if fee is not None:
            projection = project_fund_subscription_cash(
                subscription_net_ars=amount, fee_ars=fee,
                available_cash_ars=available, minimum_cash_buffer_ars=buffer,
                maximum_position_ars=maximum)
            if not projection.within_cash_limit:
                block("FCI_FEE_INCLUSIVE_CASH_LIMIT")
        if isinstance(ce, FundClassEvidence) and isinstance(ce.terms, PaperFundTerms):
            try:
                ce.terms.subscription_amount(amount)
            except ValueError:
                block("FCI_CLASS_AMOUNT_POLICY_REJECTED")
    days = evidence.redemption_lead_days
    if (not isinstance(days, int) or isinstance(days, bool) or days < 0
        or not isinstance(budget.holding_horizon_days, int)
        or isinstance(budget.holding_horizon_days, bool)
        or budget.holding_horizon_days < days):
        block("FCI_REDEMPTION_HORIZON_NOT_COVERED")

    status = "HOLD" if codes else "SYNTHETIC_OFFLINE_PAPER_CANDIDATE"
    return FundAllocationDecision(status, tuple(codes), amount if not codes else None,
                                  at.isoformat(), evidence.provenance)


def simulate_fund_subscription(executor: FamilyPaperExecutor, *, evidence,
                               budget, lifecycle_id, event_id, occurred_at,
                               cash_guard, admission_guard):
    """Delegate only a synthetic offline fixture to the durable PAPER ledger.

    Both guards are required; the existing executor rechecks cash and admission
    in its locked transaction and retains idempotency. The runtime store path
    is forbidden regardless of what caller-supplied evidence claims.
    """
    if not isinstance(executor, FamilyPaperExecutor):
        raise ValueError("FCI_PAPER_EXECUTOR_REQUIRED")
    if cash_guard is None or not callable(cash_guard) or not callable(admission_guard):
        raise ValueError("FCI_PAPER_ADMISSION_GUARDS_REQUIRED")
    from cg_paper_workspace import database_path
    if Path(executor.store.path).resolve() == Path(database_path()).resolve():
        raise ValueError("FCI_RUNTIME_ADAPTER_NOT_AUTHORIZED")
    decision = assess_fund_cash_parking(evidence, budget, as_of=occurred_at)
    if decision.status != "SYNTHETIC_OFFLINE_PAPER_CANDIDATE":
        raise ValueError("FCI_PAPER_ALLOCATION_HOLD:" + ",".join(decision.reason_codes))
    return executor.subscribe_fund(
        evidence.class_evidence.terms, lifecycle_id=lifecycle_id,
        event_id=event_id, amount=decision.amount_ars, occurred_at=occurred_at,
        cash_guard=cash_guard, admission_guard=admission_guard)
