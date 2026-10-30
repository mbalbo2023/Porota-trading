"""Conservative PIT FI risk inputs for the PAPER portfolio and supervisors.

This is a *read-only input contract*. It consumes the reconciled A199 FI
portfolio bridge from one caller-owned SQLite snapshot. It never admits an
entry, manufactures issuer evidence, sends an order, settles a market sale,
converts FX, or closes a maturity. The common engine may use its cost/nominal
facts only after explicitly handling the blockers and preserving the original
spot buy cash commitment. A complete native FI lifecycle remains a separate
capability, with its own tests and external evidence.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from bs_instrument_contracts import aware_datetime, cash_currency, decimal_value, family_name
from rc6_fixed_income_event_reducer import EconomicEvent
from rc6_fixed_income_portfolio_bridge import (
    FiPositionProjection, FiValuationProjection, native_exit_authorized,
    position_at, valuation_at,
)

PAPER_ONLY = True
ENTRY_AUTHORITY = False
REAL_ROUTES_USED = ()
SCHEMA = "rc6.fixed-income-risk-projection.v1"
_SUPPORTED_SETTLEMENT = frozenset({"INMEDIATA", "CI", "T+0", "A-24HS", "24HS", "T+1"})


@dataclass(frozen=True)
class FiRealizedComponent:
    """Economic PAPER cash, never a broker-confirmed trade or tax statement."""

    event_id: str
    kind: str
    booked_at: str
    currency: str
    cash: Decimal
    redeemed_nominal: Decimal
    allocated_entry_basis: Decimal
    allocated_entry_cost: Decimal
    realized_before_unverified_fees: Decimal | None


@dataclass(frozen=True)
class FiRiskInputs:
    """Conservative values, plus explicit blockers that callers MUST check."""

    paper_id: str
    identity: tuple[str, str, str, str, str]
    as_of: str
    opening_nominal: Decimal
    residual_nominal: Decimal
    redeemed_nominal: Decimal
    spot_buy_cash_commitment: Decimal
    remaining_entry_cost_basis: Decimal
    amortized_entry_cost_basis: Decimal
    remaining_full_loss_risk_reserve: Decimal
    residual_bid_exposure: Decimal
    unrealized_before_unverified_exit_costs: Decimal
    realized_components: tuple[FiRealizedComponent, ...]
    confirmed_event_cash_by_currency: tuple[tuple[str, Decimal], ...]
    confirmed_coupon_cash_by_currency: tuple[tuple[str, Decimal], ...]
    confirmed_principal_cash_by_currency: tuple[tuple[str, Decimal], ...]
    realized_losses_by_currency: tuple[tuple[str, Decimal], ...]
    known_realized_by_currency: tuple[tuple[str, Decimal], ...]
    total_realized_native: Decimal | None
    blockers: tuple[str, ...]
    risk_input_status: str = "FI_NATIVE_EXECUTION_UNVERIFIED"
    preserve_original_spot_cash_commitment: bool = True
    entry_authority: bool = False
    native_exit_authorized: bool = False
    paper_only: bool = True


def _quote_error(quote, root, cutoff, max_mark_age_seconds):
    if quote is None:
        return "FI_RISK_MARK_MISSING"
    try:
        family = family_name(quote.asset_class)
        expected = (root["symbol"], family_name(root["asset_class"]), root["market"],
                    root["currency"], root["settlement"])
        actual = (quote.symbol, family, quote.market, cash_currency(quote.currency),
                  quote.settlement)
        if expected != actual or quote.monetary_identity() != (root["currency"], root["market"]):
            return "FI_RISK_MARK_IDENTITY_MISMATCH"
        contract = quote.contract
        if (contract is None or contract.key != expected
                or contract.cash_multiplier <= 0 or contract.quantity_step <= 0):
            return "FI_RISK_MARK_CONTRACT_MISSING_OR_MISMATCH"
        feature = json.loads(root["features_json"])
        factor = decimal_value(feature["contract_cash_multiplier"], "FI entry multiplier", positive=True)
        if factor != contract.cash_multiplier:
            return "FI_RISK_MARK_CONTRACT_MISSING_OR_MISMATCH"
        if type(max_mark_age_seconds) not in {int, float} or not 0 < max_mark_age_seconds <= 3600:
            return "FI_RISK_MARK_AGE_POLICY_INVALID"
        time_error = quote.time_error(cutoff, max_age_seconds=max_mark_age_seconds)
        if time_error:
            return "FI_RISK_MARK_CLOCK_OR_FRESHNESS_INVALID:" + str(time_error)
        if decimal_value(quote.bid, "FI bid", positive=True) > decimal_value(
                quote.ask, "FI ask", positive=True):
            return "FI_RISK_MARK_CROSSED_BOOK"
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, json.JSONDecodeError):
        return "FI_RISK_MARK_UNVERIFIABLE"
    return ""


def _event_components(connection, view, cutoff, entry_price, multiplier, entry_cost):
    rows = connection.execute("""SELECT id,kind,event_json,booked_at
      FROM paper_fi_economic_events WHERE paper_id=? ORDER BY id""", (view.paper_id,))
    components, paid, redeemed_total = [], {}, Decimal(0)
    for row in rows:
        if aware_datetime(row["booked_at"]) > cutoff:
            continue
        event = EconomicEvent(**json.loads(row["event_json"]))
        if event.kind == "FIXING":
            continue
        currency = cash_currency(event.payment_currency)
        amount = decimal_value(event.cash_amount, "FI event cash", nonnegative=True)
        nominal = (decimal_value(event.nominal_redeemed, "FI redeemed nominal", positive=True)
                   if event.kind == "AMORTIZATION" else Decimal(0))
        redeemed_total += nominal
        basis = entry_price * nominal * multiplier
        fee = entry_cost * nominal / view.initial_nominal
        # Coupon is income in its own payout currency; principal redeemed in
        # another currency has no native PnL until an authenticated FX basis.
        realized = (amount-basis-fee if event.kind == "AMORTIZATION"
                    and currency == view.trading_currency else
                    None if event.kind == "AMORTIZATION" else amount)
        components.append(FiRealizedComponent(
            event.event_id, event.kind, row["booked_at"], currency, amount, nominal,
            basis, fee, realized))
        paid[currency] = paid.get(currency, Decimal(0)) + amount
    if redeemed_total != view.redeemed_nominal or tuple(sorted(paid.items())) != view.cash_by_currency:
        raise ValueError("FI_RISK_PIT_LEDGER_PROJECTION_MISMATCH")
    return tuple(components)


def risk_inputs_at(connection, paper_id, as_of, quote, *, max_mark_age_seconds=120):
    """Return verified-in-code PIT inputs and NEVER grant FI trade authority.

    Preconditions: caller owns ``BEGIN`` or ``BEGIN IMMEDIATE`` on the SQLite
    connection, the quote originates from its authorized collection path, and
    issuer/economic receipts were independently reviewed before ledger import.
    Each failure is explicit; invalid state never silently means zero risk.

    ``spot_buy_cash_commitment`` must remain deducted once in the original spot
    cash ledger. Add ``confirmed_event_cash_by_currency`` once by payout
    currency; do not replace the original commitment with residual cost basis.
    For exposure/PnL use residual units, not the original spot nominal. The
    returned realized components do not include unverified taxes/exit fees.
    """
    if not connection.in_transaction:
        raise ValueError("FI_RISK_SNAPSHOT_TRANSACTION_REQUIRED")
    cutoff = aware_datetime(as_of)
    row = connection.execute("SELECT * FROM paper_positions WHERE paper_id=?", (paper_id,)).fetchone()
    if row is None or row["status"] != "OPEN":
        raise ValueError("FI_RISK_PAPER_LOT_NOT_OPEN")
    settlement = row["settlement"]
    if (not isinstance(settlement, str) or settlement != settlement.strip()
            or settlement.upper() not in _SUPPORTED_SETTLEMENT):
        raise ValueError("FI_RISK_SETTLEMENT_AMBIGUOUS")
    view: FiPositionProjection | None = position_at(connection, paper_id, cutoff)
    if view is None:
        raise ValueError("FI_RISK_EVENT_LEDGER_MISSING_OR_POSITION_NOT_YET_OPEN")
    error = _quote_error(quote, row, cutoff, max_mark_age_seconds)
    if error:
        raise ValueError(error)
    valuation: FiValuationProjection | None = valuation_at(
        connection, paper_id, cutoff, quote.bid)
    if valuation is None or valuation.residual_nominal != view.residual_nominal:
        raise ValueError("FI_RISK_PIT_VALUATION_MISSING")
    features = json.loads(row["features_json"])
    factor = decimal_value(features["contract_cash_multiplier"], "FI multiplier", positive=True)
    entry_price = decimal_value(row["entry_price"], "FI entry price", positive=True)
    entry_cost = decimal_value(row["entry_cost"], "FI entry fee", nonnegative=True)
    components = _event_components(connection, view, cutoff, entry_price, factor, entry_cost)
    realized_by_ccy, losses_by_ccy = {}, {}
    for component in components:
        if component.realized_before_unverified_fees is None:
            continue
        ccy, pnl = component.currency, component.realized_before_unverified_fees
        realized_by_ccy[ccy] = realized_by_ccy.get(ccy, Decimal(0)) + pnl
        if pnl < 0:
            # Losses are summed per event, not netted against coupon gains.
            losses_by_ccy[ccy] = losses_by_ccy.get(ccy, Decimal(0)) - pnl
    basis_remaining = valuation.cost_basis_amortized
    basis_original = entry_price * view.initial_nominal * factor + entry_cost
    if basis_original-basis_remaining < 0:
        raise ValueError("FI_RISK_AMORTIZED_ENTRY_BASIS_INVALID")
    comparable_native = valuation.realized_pnl
    if comparable_native is not None and comparable_native != realized_by_ccy.get(
            view.trading_currency, Decimal(0)):
        raise ValueError("FI_RISK_REALIZED_COMPONENT_RECONCILIATION_FAILED")
    allowed, native_blocker = native_exit_authorized(connection, paper_id, cutoff)
    if allowed:
        # This version has no approved native execution owner by design.
        raise ValueError("FI_RISK_UNEXPECTED_NATIVE_EXIT_AUTHORITY")
    blockers = [native_blocker, "FI_ACCOUNT_COSTS_AND_EXIT_FEES_UNVERIFIED"]
    if any(name != view.trading_currency and amount
           for name, amount in view.cash_by_currency):
        blockers.append("FI_CROSS_CURRENCY_FX_UNVERIFIED")
    identity = (row["symbol"], family_name(row["asset_class"]), row["market"],
                row["currency"], settlement)
    return FiRiskInputs(
        paper_id=paper_id, identity=identity, as_of=cutoff.isoformat(),
        opening_nominal=view.initial_nominal, residual_nominal=view.residual_nominal,
        redeemed_nominal=view.redeemed_nominal,
        spot_buy_cash_commitment=basis_original,
        remaining_entry_cost_basis=basis_remaining,
        amortized_entry_cost_basis=basis_original-basis_remaining,
        # A total loss of the residual invested amount is more conservative
        # than assuming a stop will fill in an illiquid credit instrument.
        remaining_full_loss_risk_reserve=basis_remaining,
        residual_bid_exposure=valuation.exposure,
        unrealized_before_unverified_exit_costs=valuation.unrealized_pnl,
        realized_components=components,
        confirmed_event_cash_by_currency=view.cash_by_currency,
        confirmed_coupon_cash_by_currency=view.coupon_by_currency,
        confirmed_principal_cash_by_currency=view.principal_by_currency,
        realized_losses_by_currency=tuple(sorted(losses_by_ccy.items())),
        known_realized_by_currency=tuple(sorted(realized_by_ccy.items())),
        total_realized_native=comparable_native,
        blockers=tuple(blockers))
