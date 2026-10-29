"""Causal, long-only OPTION PAPER signal; never an execution authority.

This is an experimental *candidate* selector.  A broker/PAPER engine must still
recheck exact catalog identity, global operator receipt, fees, liquidity and
cash/risk under the ledger write transaction.  An unverified complementary IV
or Greek is a warning, not an invented fair-value estimate or deploy veto.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from be_paper_engine import Quote
from bq_exit_policy import PaperSessionPolicy
from bs_instrument_contracts import (InstrumentContract, aware_datetime,
                                     decimal_value, family_name, utc_microseconds)
from rc6_performance.costs import paper_cost_contract


PAPER_ONLY = True
ENTRY_AUTHORITY = False
REAL_ROUTES_USED = ()
STRATEGY_ID = "OPTION_LONG_UNDERLYING_TREND_PAPER_V1"
ZERO = Decimal(0)
ONE = Decimal(1)


@dataclass(frozen=True)
class UnderlyingTrade:
    """One PPI primary observed transaction, with its separate receive clock."""

    price: object
    traded_at: str
    received_at: str


@dataclass(frozen=True)
class UnderlyingEvidence:
    symbol: str
    family: str
    market: str
    currency: str
    settlement: str
    bid: object
    ask: object
    bid_size: object
    ask_size: object
    book_at: str
    received_at: str
    metadata_source: str
    trades: tuple[UnderlyingTrade, ...]


@dataclass(frozen=True)
class OptionSignalDecision:
    action: str  # BUY is only a signal candidate, never a simulated fill.
    score: Decimal
    reason: str
    features: dict
    quantity: Decimal | None = None
    entry_authority: bool = False
    paper_only: bool = True
    real_routes_used: tuple = ()


def _number(value, label, *, positive=True):
    return decimal_value(value, label, positive=positive, nonnegative=not positive)


def _fresh(source_at, received_at, decision_at, *, max_age_seconds=120):
    source = utc_microseconds(source_at)
    received = utc_microseconds(received_at)
    now = utc_microseconds(decision_at)
    return (source <= received <= now and
            0 <= now - source <= max_age_seconds * 1_000_000)


def evaluate_option_entry(
    q: Quote, *, decision_at, underlying_evidence: UnderlyingEvidence | None,
    available_cash, risk_budget, max_position_notional,
    max_spread_bps=900, max_moneyness_fraction="0.15",
    max_premium_to_spot_fraction="0.08", max_expiry_days=45,
    min_expiry_days=1, min_momentum_fraction="0.003",
    depth_participation="0.25", implied_volatility_evidence=None,
) -> OptionSignalDecision:
    """Return a conservative candidate or a stable HOLD reason.

    The directional hypothesis is observed underlying momentum; it does not
    claim future returns, out-of-sample edge, theoretical option fair value or
    implied volatility. An unsupported option/underlying/clock fails closed.
    """
    features = {
        "strategy": STRATEGY_ID, "mode": "PRODUCTION_PAPER",
        "execution": "SIMULATION_CANDIDATE_ONLY", "entry_authority": False,
        "real_routes_used": [], "iv": {"status": "NO_VERIFICADO"},
        "greeks": {"status": "NO_VERIFICADO"},
        "warnings": ["IV_NO_VERIFICADO", "GREEKS_NO_VERIFICADO",
                     "EDGE_OOS_NO_VERIFICADO", "ACCOUNT_TARIFF_NO_VERIFICADO"],
    }

    def hold(reason):
        features["reason_code"] = reason
        return OptionSignalDecision("HOLD", ZERO, reason, features)

    try:
        at = aware_datetime(decision_at).isoformat()
        if not isinstance(q, Quote) or not isinstance(q.contract, InstrumentContract):
            return hold("OPTION_EXACT_CONTRACT_REQUIRED")
        contract = q.contract
        if (family_name(q.asset_class) != "OPCIONES" or contract.family != "OPCIONES"
                or contract.key != (q.symbol, "OPCIONES", q.market, q.currency, q.settlement)
                or contract.market != "BYMA" or contract.currency != "ARS"
                or contract.settlement != "INMEDIATA"
                or not str(q.metadata_source or "").startswith("PPI_CATALOG:")
                # The normalizer authenticates exact contract field provenance
                # before emitting this bound marker; it need not contain the
                # literal provider name. The runtime catalog guard still has
                # to reverify the exact bound contract under the write lock.
                or not (str(contract.metadata_source) == "CONTRACT_EVIDENCE_V2_BOUND"
                        or str(contract.metadata_source).startswith("PPI_SEARCH_INSTRUMENT"))
                or contract.option_right not in {"CALL", "PUT"}):
            return hold("OPTION_PPI_PRIMARY_EXACT_IDENTITY_REQUIRED")
        if not isinstance(underlying_evidence, UnderlyingEvidence):
            return hold("OPTION_UNDERLYING_EVIDENCE_MISSING")
        u = underlying_evidence
        if (u.symbol != contract.underlying
                or family_name(u.family) not in {"ACCIONES", "CEDEARS"}
                or u.market != "BYMA" or u.currency != "ARS"
                or u.settlement not in {"INMEDIATA", "A-24HS"}
                or not u.metadata_source.startswith("PPI_CATALOG:")):
            return hold("OPTION_UNDERLYING_PPI_IDENTITY_MISMATCH")
        if not _fresh(u.book_at, u.received_at, at):
            return hold("OPTION_UNDERLYING_BOOK_STALE_OR_FUTURE")
        if q.time_error(at, max_age_seconds=120):
            return hold("OPTION_BOOK_STALE_OR_FUTURE")
        if (not isinstance(max_expiry_days, int) or not isinstance(min_expiry_days, int)
                or not 1 <= min_expiry_days <= max_expiry_days <= 45):
            return hold("OPTION_EXPIRY_POLICY_INVALID")
        # Any caller may tighten these PAPER research limits, never loosen them.
        spread_limit = _number(max_spread_bps, "spread_limit")
        moneyness_limit = _number(max_moneyness_fraction, "moneyness_limit")
        premium_limit = _number(max_premium_to_spot_fraction, "premium_limit")
        participation = _number(depth_participation, "depth_participation")
        momentum_limit = _number(min_momentum_fraction, "momentum_limit")
        if (spread_limit > 900 or moneyness_limit > Decimal("0.15")
                or premium_limit > Decimal("0.08")
                or participation > Decimal("0.25") or participation > ONE
                or momentum_limit < Decimal("0.003")):
            return hold("OPTION_RESEARCH_LIMIT_CANNOT_BE_LOOSENED")
        expiry_seconds = (utc_microseconds(contract.expires_at)
                          - utc_microseconds(at)) / 1_000_000
        if not min_expiry_days * 86400 <= expiry_seconds <= max_expiry_days * 86400:
            return hold("OPTION_EXPIRY_OUTSIDE_SAFE_HORIZON")
        session_error = PaperSessionPolicy().admission_error(q, at)
        if session_error:
            return hold("OPTION_" + session_error)

        bid, ask = _number(q.bid, "option_bid"), _number(q.ask, "option_ask")
        ubid, uask = _number(u.bid, "underlying_bid"), _number(u.ask, "underlying_ask")
        depth = _number(q.ask_size, "option_ask_size")
        _number(q.bid_size, "option_bid_size")
        _number(u.ask_size, "underlying_ask_size")
        _number(u.bid_size, "underlying_bid_size")
        if bid > ask or ubid > uask:
            return hold("OPTION_CROSSED_BOOK")
        option_spread_bps = (ask - bid) / ask * 10000
        underlying_spread_bps = (uask - ubid) / uask * 10000
        if option_spread_bps > spread_limit or underlying_spread_bps > 200:
            return hold("OPTION_SPREAD_LIMIT")
        if depth * participation < ONE:
            return hold("OPTION_DEPTH_INSUFFICIENT")
        if len(u.trades) < 8 or len(u.trades) > 120:
            return hold("OPTION_UNDERLYING_SIGNAL_WINDOW_INCOMPLETE")
        stamps, prices = [], []
        for row in u.trades:
            if not isinstance(row, UnderlyingTrade) or not _fresh(
                    row.traded_at, row.received_at, at, max_age_seconds=1200):
                return hold("OPTION_UNDERLYING_TRADE_CLOCK_INVALID")
            stamps.append(utc_microseconds(row.traded_at))
            prices.append(_number(row.price, "underlying_trade_price"))
        if any(later - earlier < 60_000_000 for earlier, later in zip(stamps, stamps[1:])):
            return hold("OPTION_UNDERLYING_TRADE_SEQUENCE_INVALID")
        if stamps[-1] < utc_microseconds(at) - 120_000_000:
            return hold("OPTION_UNDERLYING_LAST_TRADE_STALE")
        spot = (ubid + uask) / 2
        if abs(prices[-1] / spot - ONE) > Decimal("0.05"):
            return hold("OPTION_UNDERLYING_BOOK_TRADE_DIVERGENCE")
        if abs(contract.strike / spot - ONE) > moneyness_limit:
            return hold("OPTION_OUTSIDE_MONEYNESS_BAND")
        if ask / spot > premium_limit:
            return hold("OPTION_PREMIUM_TOO_HIGH_RELATIVE_TO_UNDERLYING")

        short = sum(prices[-3:], ZERO) / 3
        long = sum(prices[-8:], ZERO) / 8
        momentum = short / long - ONE
        threshold = momentum_limit
        aligned = (momentum >= threshold if contract.option_right == "CALL"
                   else momentum <= -threshold)
        features.update(underlying_identity=list((u.symbol, u.family, u.market,
                                                  u.currency, u.settlement)),
                        underlying_book_at=u.book_at, observed_trade_count=len(prices),
                        trade_window=[u.trades[-8].traded_at, u.trades[-1].traded_at],
                        short_average=str(short), long_average=str(long),
                        underlying_momentum=str(momentum), option_right=contract.option_right,
                        option_spread_bps=str(option_spread_bps),
                        underlying_spread_bps=str(underlying_spread_bps),
                        strike=str(contract.strike), spot_mid=str(spot),
                        expires_at=contract.expires_at)
        if not aligned:
            return hold("OPTION_UNDERLYING_DIRECTIONAL_SIGNAL_NOT_READY")

        # One contract, full premium loss.  The ledger rechecks these amounts
        # with the live position count under a write transaction.
        qty = contract.quantity(ONE)
        premium_cash = ask * contract.cash_multiplier * qty
        cost_policy = paper_cost_contract("OPCIONES", "ARS", at)
        if cost_policy.binding_error(at):
            return hold("OPTION_PAPER_COST_POLICY_UNVERIFIED")
        entry_fee = cost_policy.leg_cost(premium_cash, decision_at=at)
        max_loss = premium_cash + entry_fee
        if (max_loss > _number(available_cash, "available_cash", positive=False)
                or max_loss > _number(risk_budget, "risk_budget", positive=False)
                or premium_cash > _number(max_position_notional, "max_notional", positive=False)):
            return hold("OPTION_FULL_PREMIUM_LOSS_EXCEEDS_BUDGET")
        if isinstance(implied_volatility_evidence, dict):
            iv = implied_volatility_evidence
            if all(iv.get(field) is not None for field in (
                    "value", "source", "source_at", "received_at")):
                try:
                    value = _number(iv["value"], "implied_volatility")
                    if (str(iv["source"]).strip() and value <= ONE and
                            _fresh(iv["source_at"], iv["received_at"], at)):
                        features["iv"] = {"status": "OBSERVED_FRESH", "value": str(value),
                                          "source": iv["source"], "source_at": iv["source_at"]}
                        features["warnings"].remove("IV_NO_VERIFICADO")
                except (ValueError, TypeError, OverflowError):
                    pass
        features.update(maximum_simulated_loss=str(max_loss), premium_cash=str(premium_cash),
                        entry_fee=str(entry_fee), quantity=str(qty),
                        cost_policy_sha256=cost_policy.diagnostic()["policy_sha256"],
                        reason_code="OPTION_LONG_PAPER_CANDIDATE")
        # No probability / fair-value inference from this trend score.
        score = min(ONE, abs(momentum) / (threshold * 5))
        features["score_is_probability"] = False
        return OptionSignalDecision("BUY", score, "OPTION_LONG_PAPER_CANDIDATE",
                                    features, qty)
    except (ValueError, TypeError, OverflowError, ArithmeticError, AttributeError):
        return hold("OPTION_SIGNAL_INPUT_UNVERIFIED")
