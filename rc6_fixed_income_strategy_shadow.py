"""Causal fixed-income PAPER scenarios, never native BUY authority.

An unchanged current bid is an explicit *future-exit scenario*, not a forecast.
Projected contract flows remain separate from confirmed settlement receipts.
For a real closed PAPER lot use rc6_fixed_income_paper_bridge instead: this
module never writes a fill, a position, a cash ledger, or an order.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from be_paper_engine import Quote
from bq_exit_policy import PaperSessionPolicy
from bs_instrument_contracts import (InstrumentContract, aware_datetime,
                                     cash_currency, decimal_value, family_name,
                                     utc_microseconds)
from rc6_fixed_income_event_reducer import EconomicEvent, PositionSeed, reduce_events
from rc6_performance.costs import paper_cost_contract


PAPER_ONLY = True
ENTRY_AUTHORITY = False
REAL_ROUTES_USED = ()
FAMILIES = frozenset({"BONOS", "LETRAS", "OBLIGACIONES"})
BOOK_SOURCES = frozenset({"PPI_PRIMARY", "IOL_MCP", "BYMA_PUBLIC", "SYNTHETIC_TEST"})
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
ZERO = Decimal(0)


@dataclass(frozen=True)
class ContractProof:
    contract: InstrumentContract
    source_reference: str
    source_sha256: str
    source_at: str
    known_at: str


@dataclass(frozen=True)
class ProjectedFlow:
    obligation_id: str
    kind: str  # COUPON or AMORTIZATION; the latter fully redeems at maturity.
    due_at: str
    cash_per_vn: object
    payment_currency: str
    source_reference: str
    source_sha256: str
    source_at: str
    known_at: str


@dataclass(frozen=True)
class RiskLimits:
    cash_available: object
    max_allocation: object
    max_loss_budget: object
    stress_bid_drop: object
    max_spread_bps: object
    max_holding_days: int
    participation: object
    slippage_fraction: object


@dataclass(frozen=True)
class ShadowScenario:
    identity: tuple[str, str, str, str, str]
    decision_at: str
    planned_exit_at: str
    exit_mode: str
    quantity_vn: Decimal
    entry_cash: Decimal | None
    exit_cash_scenario: Decimal | None
    projected_flows: tuple[tuple[str, Decimal], ...]
    scenario_net: Decimal | None
    stress_loss: Decimal | None
    fee_policy_sha256: str | None
    projected_obligations: tuple[tuple[str, str, str, str], ...]
    status: str
    reason: str
    signal: str
    pit_status: str = "EXPLICIT_INPUT_CLOCKS_ONLY"
    oos_status: str = "OOS_NO_VERIFICADO"
    cost_scope: str = "PAPER_PUBLIC_ESTIMATE_ACCOUNT_UNVERIFIED"
    observed_book_source: str = "NO_VERIFICADO"
    entry_authority: bool = False
    real_routes_used: tuple = ()


@dataclass(frozen=True)
class ShadowExitReplay:
    identity: tuple[str, str, str, str, str]
    scenario_net_by_currency: tuple[tuple[str, Decimal], ...]
    receipt_cash_by_currency: tuple[tuple[str, Decimal], ...]
    cost_scope: str = "PAPER_PUBLIC_ESTIMATE_ACCOUNT_UNVERIFIED"
    status: str = "INDICATIVE_BOOK_REPLAY_NOT_LEDGER_REALIZATION"
    receipt_provenance: str = "EXTERNAL_SOURCE_AUTHENTICATION_REQUIRED"
    observed_entry_source: str = "NO_VERIFICADO"
    observed_exit_source: str = "NO_VERIFICADO"
    entry_authority: bool = False
    real_routes_used: tuple = ()


def _clock(value, name):
    try:
        result = utc_microseconds(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("FI_SHADOW_" + name + "_INVALID") from exc
    if result is None:
        raise ValueError("FI_SHADOW_" + name + "_INVALID")
    return result


def _provenance(ref, digest, source_at, known_at, cutoff):
    if not isinstance(ref, str) or not ref.strip() or not SHA256.fullmatch(str(digest)):
        raise ValueError("FI_SHADOW_SOURCE_PROOF_REQUIRED")
    if not _clock(source_at, "SOURCE_AT") <= _clock(known_at, "KNOWN_AT") <= cutoff:
        raise ValueError("FI_SHADOW_FUTURE_OR_REVISED_SOURCE")


def _amount(value, label, *, positive=True):
    try:
        return decimal_value(value, label, positive=positive, nonnegative=not positive)
    except ValueError as exc:
        raise ValueError("FI_SHADOW_" + label + "_INVALID") from exc


def _identity(quote: Quote, contract: InstrumentContract):
    family = family_name(quote.asset_class)
    if family not in FAMILIES or contract.family != family or quote.market != "BYMA":
        raise ValueError("FI_SHADOW_FAMILY_OR_MARKET_UNSUPPORTED")
    monetary = quote.monetary_identity()
    key = (quote.symbol, family, quote.market, monetary[0], quote.settlement)
    if contract.key != key or quote.contract != contract or contract.minimum_quantity is None:
        raise ValueError("FI_SHADOW_EXACT_NOMINAL_CONTRACT_REQUIRED")
    if contract.quantity_step != contract.quantity_step.to_integral_value():
        raise ValueError("FI_SHADOW_VN_STEP_NOT_INTEGRAL")
    return key


def _blocked(key, at, exit_at, mode, qty, reason, *, flows=(), policy_sha=None):
    return ShadowScenario(key, at, exit_at, mode, qty, None, None, flows, None,
                          None, policy_sha, (), "BLOCKED", reason, "HOLD")


def evaluate_shadow(quote: Quote, proof: ContractProof, flows: Iterable[ProjectedFlow],
                    limits: RiskLimits, *, quantity_vn, decision_at, planned_exit_at,
                    exit_mode="MARKET_EXIT", book_reference, book_sha256,
                    book_source_kind) -> ShadowScenario:
    """Score a static-bid carry or fixed-redemption scenario with PIT inputs.

    A positive result is a research hypothesis. No OOS validation, live exit
    liquidity, actual account tariff, or future fixing is implied.
    """
    if not isinstance(quote, Quote) or not isinstance(proof, ContractProof):
        raise ValueError("FI_SHADOW_QUOTE_AND_CONTRACT_REQUIRED")
    if book_source_kind not in BOOK_SOURCES:
        raise ValueError("FI_SHADOW_BOOK_SOURCE_KIND_UNVERIFIED")
    contract = proof.contract
    if not isinstance(contract, InstrumentContract):
        raise ValueError("FI_SHADOW_CONTRACT_REQUIRED")
    key = _identity(quote, contract)
    at = aware_datetime(decision_at).isoformat()
    exit_at = aware_datetime(planned_exit_at).isoformat()
    cutoff, horizon = _clock(at, "DECISION_AT"), _clock(exit_at, "EXIT_AT")
    if horizon <= cutoff or exit_mode not in {"MARKET_EXIT", "MATURITY_REDEMPTION"}:
        raise ValueError("FI_SHADOW_EXIT_HORIZON_OR_MODE_INVALID")
    _provenance(proof.source_reference, proof.source_sha256,
                proof.source_at, proof.known_at, cutoff)
    _provenance(book_reference, book_sha256, quote.book_at, quote.observed_at, cutoff)
    if not isinstance(limits, RiskLimits) or not isinstance(limits.max_holding_days, int) or (
        limits.max_holding_days < 1 or
        (aware_datetime(exit_at) - aware_datetime(at)).total_seconds() > limits.max_holding_days * 86400
    ):
        raise ValueError("FI_SHADOW_RISK_HORIZON_INVALID")
    qty = _amount(quantity_vn, "QUANTITY_VN")
    if qty < contract.minimum_quantity or qty % contract.quantity_step:
        return _blocked(key, at, exit_at, exit_mode, qty, "FI_SHADOW_VN_LOT_INVALID")
    if quote.time_error(at, require_trade=True, max_age_seconds=120,
                        max_trade_age_seconds=900):
        return _blocked(key, at, exit_at, exit_mode, qty, "FI_SHADOW_BOOK_OR_TRADE_CLOCK_INVALID")
    if PaperSessionPolicy().admission_error(quote, at):
        return _blocked(key, at, exit_at, exit_mode, qty, "FI_SHADOW_ENTRY_SESSION_CLOSED")
    if (exit_mode == "MARKET_EXIT"
            and PaperSessionPolicy().execution_error(quote, exit_at)):
        return _blocked(key, at, exit_at, exit_mode, qty, "FI_SHADOW_EXIT_SESSION_CLOSED")
    ask, bid = _amount(quote.ask, "ASK"), _amount(quote.bid, "BID")
    if bid > ask or _amount(quote.ask_size, "ASK_SIZE") <= 0 or (
        _amount(quote.bid_size, "BID_SIZE") <= 0
    ):
        return _blocked(key, at, exit_at, exit_mode, qty, "FI_SHADOW_BOOK_INVALID")
    participation = _amount(limits.participation, "PARTICIPATION")
    if participation > 1 or qty > min(quote.ask_size, quote.bid_size) * participation:
        return _blocked(key, at, exit_at, exit_mode, qty, "FI_SHADOW_DEPTH_INSUFFICIENT")
    spread_bps = (ask - bid) / ask * 10000
    if spread_bps > _amount(limits.max_spread_bps, "MAX_SPREAD_BPS"):
        return _blocked(key, at, exit_at, exit_mode, qty, "FI_SHADOW_SPREAD_LIMIT")
    slip = _amount(limits.slippage_fraction, "SLIPPAGE", positive=False)
    stress = _amount(limits.stress_bid_drop, "STRESS", positive=False)
    if slip >= 1 or stress >= 1:
        raise ValueError("FI_SHADOW_STRESS_OR_SLIPPAGE_INVALID")
    try:
        policy = paper_cost_contract(key[1], key[3], at)
        fee_error = policy.binding_error(at)
    except (ValueError, TypeError):
        return _blocked(key, at, exit_at, exit_mode, qty, "FI_SHADOW_PAPER_COST_UNAVAILABLE")
    if fee_error:
        return _blocked(key, at, exit_at, exit_mode, qty,
                        "FI_SHADOW_" + fee_error, policy_sha=policy.diagnostic()["policy_sha256"])
    policy_sha = policy.diagnostic()["policy_sha256"]
    flow_rows = tuple(flows)
    if not flow_rows:
        return _blocked(key, at, exit_at, exit_mode, qty,
                        "FI_SHADOW_CASHFLOW_SCHEDULE_REQUIRED", policy_sha=policy_sha)
    totals: dict[str, Decimal] = {}
    obligations: set[str] = set()
    scheduled: list[tuple[str, str, str, str]] = []
    redemptions = 0
    for flow in flow_rows:
        if not isinstance(flow, ProjectedFlow) or flow.kind not in {"COUPON", "AMORTIZATION"}:
            raise ValueError("FI_SHADOW_FLOW_KIND_INVALID")
        if not flow.obligation_id or flow.obligation_id in obligations:
            raise ValueError("FI_SHADOW_FLOW_OBLIGATION_INVALID")
        obligations.add(flow.obligation_id)
        _provenance(flow.source_reference, flow.source_sha256,
                    flow.source_at, flow.known_at, cutoff)
        due = _clock(flow.due_at, "FLOW_DUE_AT")
        if due <= cutoff or due > horizon:
            raise ValueError("FI_SHADOW_FLOW_OUTSIDE_HOLDING_HORIZON")
        currency = cash_currency(flow.payment_currency)
        if flow.kind == "COUPON" and due == horizon and exit_mode == "MARKET_EXIT":
            return _blocked(key, at, exit_at, exit_mode, qty,
                            "FI_SHADOW_EXIT_DAY_ENTITLEMENT_UNVERIFIED", policy_sha=policy_sha)
        scheduled.append((flow.obligation_id, flow.kind,
                          aware_datetime(flow.due_at).isoformat(), currency))
        amount = _amount(flow.cash_per_vn, "FLOW_CASH_PER_VN") * qty
        totals[currency] = totals.get(currency, ZERO) + amount
        if flow.kind == "AMORTIZATION":
            redemptions += 1
            if exit_mode != "MATURITY_REDEMPTION" or due != horizon:
                return _blocked(key, at, exit_at, exit_mode, qty,
                                "FI_SHADOW_AMORTIZATION_REQUIRES_MATURITY_LIFECYCLE",
                                flows=tuple(sorted(totals.items())), policy_sha=policy_sha)
    projection = tuple(sorted(totals.items()))
    if (exit_mode == "MATURITY_REDEMPTION" and redemptions != 1) or (
        exit_mode == "MARKET_EXIT" and redemptions
    ):
        return _blocked(key, at, exit_at, exit_mode, qty,
                        "FI_SHADOW_EXIT_CASHFLOW_INCOMPLETE", flows=projection,
                        policy_sha=policy_sha)
    if any(currency != key[3] for currency in totals):
        return _blocked(key, at, exit_at, exit_mode, qty,
                        "FI_SHADOW_CROSS_CURRENCY_NO_VERIFIED_FX", flows=projection,
                        policy_sha=policy_sha)
    unit = contract.cash_multiplier
    entry_price, exit_price = ask * (1 + slip), bid * (1 - slip)
    entry_gross = entry_price * qty * unit
    entry_fee = policy.leg_cost(entry_gross, decision_at=at)
    entry_cash = entry_gross + entry_fee
    available = _amount(limits.cash_available, "CASH_AVAILABLE")
    allocation = _amount(limits.max_allocation, "MAX_ALLOCATION")
    loss_budget = _amount(limits.max_loss_budget, "MAX_LOSS_BUDGET")
    if entry_cash > min(available, allocation):
        return _blocked(key, at, exit_at, exit_mode, qty,
                        "FI_SHADOW_CASH_OR_ALLOCATION_LIMIT", flows=projection,
                        policy_sha=policy_sha)
    if exit_mode == "MARKET_EXIT":
        exit_gross = exit_price * qty * unit
        exit_cash = exit_gross - policy.leg_cost(exit_gross, decision_at=at)
        stress_gross = bid * (1 - stress) * (1 - slip) * qty * unit
        stressed = stress_gross - policy.leg_cost(stress_gross, decision_at=at)
        stress_loss = max(ZERO, entry_cash - stressed)
    else:
        exit_cash = ZERO  # No secondary-market sale or fictitious exit fee.
        stress_loss = entry_cash  # Full principal at risk on default.
    if stress_loss > loss_budget:
        return _blocked(key, at, exit_at, exit_mode, qty,
                        "FI_SHADOW_STRESS_RISK_BUDGET", flows=projection,
                        policy_sha=policy_sha)
    scenario_net = exit_cash + totals.get(key[3], ZERO) - entry_cash
    positive = scenario_net > 0
    return ShadowScenario(key, at, exit_at, exit_mode, qty, entry_cash,
                          exit_cash, projection, scenario_net, stress_loss,
                          policy_sha, tuple(sorted(scheduled)), "SHADOW_HYPOTHESIS",
                          "STATIC_BID_OR_FIXED_REDEMPTION_SCENARIO",
                          "POSITIVE_SCENARIO" if positive else "NONPOSITIVE_SCENARIO",
                          observed_book_source=book_source_kind)


def replay_shadow_exit(plan: ShadowScenario, seed: PositionSeed,
                       receipts: Iterable[EconomicEvent], *, as_of,
                       exit_book: Quote | None = None,
                       exit_book_reference: str | None = None,
                       exit_book_sha256: str | None = None,
                       exit_book_source_kind: str | None = None) -> ShadowExitReplay:
    """Compare a scenario with later proof; never create simulated fills.

    A123 validates declared receipt type, hash shape and PIT ordering; the
    caller must authenticate its source independently. A127 remains the
    separate adapter for closed PAPER BUY/SELL fills and booked trade PnL.
    """
    if plan.status != "SHADOW_HYPOTHESIS" or plan.entry_cash is None:
        raise ValueError("FI_SHADOW_VALID_PLAN_REQUIRED")
    if (seed.symbol, family_name(seed.family), seed.market, seed.trading_currency,
            seed.settlement) != plan.identity or (
        seed.nominal_unit != "VN" or _amount(seed.initial_nominal, "INITIAL_VN") != plan.quantity_vn
        or aware_datetime(seed.opened_at) != aware_datetime(plan.decision_at)
    ):
        raise ValueError("FI_SHADOW_REPLAY_IDENTITY_MISMATCH")
    end = aware_datetime(as_of).isoformat()
    if _clock(end, "AS_OF") < _clock(plan.planned_exit_at, "PLANNED_EXIT_AT"):
        raise ValueError("FI_SHADOW_EXIT_NOT_OBSERVED")
    ledger = reduce_events(seed, receipts, as_of=end)
    if tuple(sorted((row.obligation_id, row.kind, row.effective_at, row.currency)
                    for row in ledger.cash_entries)) != plan.projected_obligations:
        raise ValueError("FI_SHADOW_RECEIPT_OBLIGATIONS_MISMATCH")
    if set(currency for currency, _ in ledger.cash_by_currency) != set(
        currency for currency, _ in plan.projected_flows
    ):
        raise ValueError("FI_SHADOW_RECEIPT_CURRENCIES_MISMATCH")
    if plan.exit_mode == "MATURITY_REDEMPTION":
        if exit_book is not None or ledger.residual_nominal != ZERO:
            raise ValueError("FI_SHADOW_MATURITY_PROOF_REQUIRED")
        exit_cash = ZERO
    else:
        if ledger.residual_nominal != ledger.initial_nominal or exit_book is None:
            raise ValueError("FI_SHADOW_MARKET_EXIT_BOOK_REQUIRED")
        if exit_book_source_kind not in BOOK_SOURCES:
            raise ValueError("FI_SHADOW_EXIT_BOOK_SOURCE_UNVERIFIED")
        _provenance(exit_book_reference, exit_book_sha256,
                    exit_book.book_at, exit_book.observed_at, _clock(end, "AS_OF"))
        if abs(_clock(exit_book.book_at, "EXIT_BOOK_AT")
               - _clock(plan.planned_exit_at, "PLANNED_EXIT_AT")) > 120_000_000:
            raise ValueError("FI_SHADOW_EXIT_NOT_AT_PLANNED_HORIZON")
        if (exit_book.symbol, family_name(exit_book.asset_class), exit_book.market,
                exit_book.currency, exit_book.settlement) != plan.identity or (
            exit_book.time_error(end, max_age_seconds=120) or
            PaperSessionPolicy().execution_error(exit_book, end)
        ):
            raise ValueError("FI_SHADOW_EXIT_BOOK_UNVERIFIED")
        bid = _amount(exit_book.bid, "EXIT_BID")
        contract = exit_book.contract
        if contract is None or contract.key != plan.identity:
            raise ValueError("FI_SHADOW_EXIT_CONTRACT_MISMATCH")
        policy = paper_cost_contract(plan.identity[1], plan.identity[3], end)
        if policy.binding_error(end) or policy.diagnostic()["policy_sha256"] != plan.fee_policy_sha256:
            raise ValueError("FI_SHADOW_EXIT_COST_POLICY_CHANGED")
        gross = bid * plan.quantity_vn * contract.cash_multiplier
        exit_cash = gross - policy.leg_cost(gross, decision_at=end)
    by_currency = dict(ledger.cash_by_currency)
    trade_currency = plan.identity[3]
    by_currency[trade_currency] = (by_currency.get(trade_currency, ZERO)
                                  + exit_cash - plan.entry_cash)
    return ShadowExitReplay(plan.identity, tuple(sorted(by_currency.items())),
                            ledger.cash_by_currency,
                            observed_entry_source=plan.observed_book_source,
                            observed_exit_source=(exit_book_source_kind or "NO_MARKET_EXIT"))
