"""Point-in-time PAPER fixed-income position/accounting bridge.

This is a verified *reader* of the A198 event ledger, not an admission,
redemption, market-sale or FX executor. The legacy spot ledger still owns the
original buy cash commitment. Its original quantity must not be silently
reduced for ``PaperBroker._cash``: doing so and also crediting principal from
this ledger would count recovered cost basis twice.

The caller must explicitly wire the cash and valuation projections, and must
keep FI native exits fail-closed until a separate entitlement-aware sale and
redemption allocator is implemented and tested. Source/document authenticity
belongs to the externally approved receipts, never to a digest by itself.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timezone
from decimal import Decimal

from bs_instrument_contracts import aware_datetime, cash_currency, decimal_value
from rc6_fixed_income_event_ledger import MAX_LOT_EVENTS, _canonical, _seed
from rc6_fixed_income_event_reducer import EconomicEvent, reduce_events

PAPER_ONLY = True
ENTRY_AUTHORITY = False
REAL_ROUTES_USED = ()
SCHEMA = "rc6.fixed-income-portfolio-projection.v1"
FI_FAMILIES = frozenset({"BONOS", "LETRAS", "OBLIGACIONES"})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class FiPositionProjection:
    paper_id: str
    symbol: str
    family: str
    trading_currency: str
    opened_at: str
    initial_nominal: Decimal
    residual_nominal: Decimal
    redeemed_nominal: Decimal
    cash_by_currency: tuple[tuple[str, Decimal], ...]
    coupon_by_currency: tuple[tuple[str, Decimal], ...]
    principal_by_currency: tuple[tuple[str, Decimal], ...]
    event_count: int
    event_ids: tuple[str, ...]
    paper_only: bool = True
    entry_authority: bool = False
    native_exit_authorized: bool = False


@dataclass(frozen=True)
class FiValuationProjection:
    paper_id: str
    trading_currency: str
    residual_nominal: Decimal
    exposure: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal | None
    cost_basis_amortized: Decimal
    valuation_status: str
    native_exit_authorized: bool = False


def _instant(value):
    return aware_datetime(value).astimezone(timezone.utc)


def _same_time(left, right):
    return _instant(left) == _instant(right)


def _required_schema(connection):
    names = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'paper_fi_%'")}
    required = {"paper_fi_lots", "paper_fi_economic_events", "paper_fi_cash_entries"}
    if not required.issubset(names):
        raise ValueError("FI_PORTFOLIO_LEDGER_SCHEMA_MISSING")


def _validated_events(connection, paper_id, seed, as_of):
    """Reconcile all event/cash rows under the caller's SQLite snapshot.

    The entire lot is checked even when querying an earlier PIT cutoff: a
    future appended orphan or altered cash row cannot silently pass as a
    seemingly valid current portfolio. No data is modified.
    """
    rows = connection.execute(
        "SELECT * FROM paper_fi_economic_events WHERE paper_id=? ORDER BY id",
        (paper_id,)).fetchall()
    if len(rows) > MAX_LOT_EVENTS:
        raise ValueError("FI_PORTFOLIO_EVENT_BOUND_EXCEEDED")
    cash_rows = {row["event_row_id"]: row for row in connection.execute(
        "SELECT * FROM paper_fi_cash_entries WHERE paper_id=?", (paper_id,))}
    if len(cash_rows) > len(rows):
        raise ValueError("FI_PORTFOLIO_ORPHAN_CASH_ENTRY")
    events, visible, last_booked, used_cash = [], [], _instant(seed.opened_at), set()
    for row in rows:
        try:
            event = EconomicEvent(**json.loads(row["event_json"]))
            if (_canonical(event.__dict__) != row["event_json"]
                    or event.paper_id != paper_id or event.event_id != row["event_id"]
                    or event.obligation_id != row["obligation_id"] or event.kind != row["kind"]
                    or event.source_sha256 != row["source_sha256"]
                    or not isinstance(row["receipt_sha256"], str)
                    or not _SHA256.fullmatch(row["receipt_sha256"])):
                raise ValueError("FI_PORTFOLIO_EVENT_ROW_CONFLICT")
            booked = _instant(row["booked_at"])
            if booked < last_booked or booked < _instant(event.recorded_at):
                raise ValueError("FI_PORTFOLIO_BOOKING_CLOCK_INVALID")
            last_booked = booked
            cash = cash_rows.get(row["id"])
            if event.kind == "FIXING":
                if cash is not None or row["entitlement_record_at"] is not None:
                    raise ValueError("FI_PORTFOLIO_FIXING_CASH_CONFLICT")
            else:
                if (cash is None or cash["paper_id"] != paper_id
                        or cash["kind"] != event.kind
                        or cash["currency"] != cash_currency(event.payment_currency)
                        or decimal_value(cash["amount"], "FI cash", nonnegative=True) !=
                           decimal_value(event.cash_amount, "event cash", nonnegative=True)
                        or not _same_time(cash["economic_at"], event.effective_at)
                        or not _same_time(cash["known_at"], event.recorded_at)
                        or not _same_time(cash["booked_at"], booked)
                        or row["entitlement_record_at"] is None
                        or not (_instant(seed.opened_at) <= _instant(row["entitlement_record_at"])
                                <= _instant(event.effective_at))):
                    raise ValueError("FI_PORTFOLIO_EVENT_CASH_CONFLICT")
                used_cash.add(row["id"])
            events.append(event)
            if booked <= as_of:
                visible.append(event)
        except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            raise ValueError("FI_PORTFOLIO_EVENT_ROW_INVALID") from exc
    if used_cash != set(cash_rows):
        raise ValueError("FI_PORTFOLIO_ORPHAN_CASH_ENTRY")
    # The reducer validates contractual order, nominal and economic clocks.
    if events:
        reduce_events(seed, events, as_of=max(as_of, last_booked).isoformat())
    return visible


def position_at(connection, paper_id, as_of):
    """Validate one intact buy lot and project economic events by booking time.

    Returns ``None`` only for a valid lot not yet opened at the requested time
    or when there is no FI ledger lot. An invalid lot never becomes a zero.
    """
    _required_schema(connection)
    lot = connection.execute(
        "SELECT seed_json,seed_sha256 FROM paper_fi_lots WHERE paper_id=?", (paper_id,)
    ).fetchone()
    if lot is None:
        if connection.execute("SELECT 1 FROM paper_fi_economic_events WHERE paper_id=? LIMIT 1",
                              (paper_id,)).fetchone() or connection.execute(
            "SELECT 1 FROM paper_fi_cash_entries WHERE paper_id=? LIMIT 1", (paper_id,)).fetchone():
            raise ValueError("FI_PORTFOLIO_ORPHAN_EVENT_OR_CASH")
        return None
    seed = _seed(connection, paper_id)
    if seed.family not in FI_FAMILIES:
        raise ValueError("FI_PORTFOLIO_FAMILY_NOT_SUPPORTED")
    if (_canonical(seed.__dict__) != lot["seed_json"]
            or seed.position_sha256 != lot["seed_sha256"]):
        raise ValueError("FI_PORTFOLIO_IMMUTABLE_SEED_CHANGED")
    cutoff = _instant(as_of)
    if cutoff < _instant(seed.opened_at):
        return None
    snapshot = reduce_events(seed, _validated_events(connection, paper_id, seed, cutoff),
                             as_of=cutoff.isoformat())
    coupons, principal = {}, {}
    for entry in snapshot.cash_entries:
        target = coupons if entry.kind == "COUPON" else principal
        target[entry.currency] = target.get(entry.currency, Decimal(0)) + entry.amount
    return FiPositionProjection(
        seed.paper_id, seed.symbol, seed.family, seed.trading_currency, seed.opened_at,
        snapshot.initial_nominal, snapshot.residual_nominal,
        snapshot.initial_nominal - snapshot.residual_nominal,
        snapshot.cash_by_currency, tuple(sorted(coupons.items())),
        tuple(sorted(principal.items())), len(snapshot.applied_event_ids),
        snapshot.applied_event_ids)


def positions_at(connection, as_of):
    """Read only existing FI ledger lots, without inventing unsourced events."""
    _required_schema(connection)
    if connection.execute("""SELECT 1 FROM paper_fi_economic_events e
        LEFT JOIN paper_fi_lots l ON e.paper_id=l.paper_id
        WHERE l.paper_id IS NULL LIMIT 1""").fetchone() or connection.execute(
        """SELECT 1 FROM paper_fi_cash_entries c
        LEFT JOIN paper_fi_lots l ON c.paper_id=l.paper_id
        WHERE l.paper_id IS NULL LIMIT 1""").fetchone():
        raise ValueError("FI_PORTFOLIO_ORPHAN_EVENT_OR_CASH")
    result = []
    for row in connection.execute("SELECT paper_id FROM paper_fi_lots ORDER BY paper_id"):
        projection = position_at(connection, row[0], as_of)
        if projection is not None:
            result.append(projection)
    return tuple(result)


def cash_effect_checked(connection, currency, as_of, *, income_only=False):
    """Reconciled FI cash by payout currency, never converted to trade currency."""
    currency = cash_currency(currency)
    return sum((amount for view in positions_at(connection, as_of)
                for name, amount in (view.coupon_by_currency if income_only
                                     else view.cash_by_currency)
                if name == currency), Decimal(0))


def valuation_at(connection, paper_id, as_of, mark_price):
    """PIT residual exposure/PnL without claiming a mark's freshness.

    The caller must independently validate the PPI book, price basis, contract,
    exchange calendar and fees. Cross-currency payouts are never silently
    translated: realized PnL is deliberately unavailable in that case.
    """
    view = position_at(connection, paper_id, as_of)
    if view is None:
        return None
    row = connection.execute("SELECT entry_price,entry_cost,features_json FROM paper_positions WHERE paper_id=?",
                             (paper_id,)).fetchone()
    features = json.loads(row["features_json"])
    multiplier = decimal_value(features["contract_cash_multiplier"], "FI multiplier", positive=True)
    entry = decimal_value(row["entry_price"], "FI entry", positive=True)
    entry_cost = decimal_value(row["entry_cost"], "FI entry cost", nonnegative=True)
    mark = decimal_value(mark_price, "FI mark", positive=True)
    basis_redeemed = entry * view.redeemed_nominal * multiplier
    entry_cost_redeemed = entry_cost * view.redeemed_nominal / view.initial_nominal
    residual_cost = entry_cost - entry_cost_redeemed
    unrealized = ((mark-entry) * view.residual_nominal * multiplier - residual_cost)
    payouts = dict(view.cash_by_currency)
    cross_currency = any(amount != 0 and currency != view.trading_currency
                         for currency, amount in payouts.items())
    realized = None if cross_currency else (
        payouts.get(view.trading_currency, Decimal(0)) - basis_redeemed - entry_cost_redeemed)
    return FiValuationProjection(
        view.paper_id, view.trading_currency, view.residual_nominal,
        mark * view.residual_nominal * multiplier, unrealized, realized,
        entry * view.residual_nominal * multiplier + residual_cost,
        "FI_CROSS_CURRENCY_PNL_UNVERIFIED" if cross_currency else "FI_PAPER_COST_BASIS_RECONCILED")


def native_exit_authorized(connection, paper_id, as_of):
    """Explicit fail-closed capability until native sale/redemption is coded.

    Return a stable blocker rather than inventing SELL_SIMULATED for a maturity.
    This check also validates event integrity and PIT ownership.
    """
    view = position_at(connection, paper_id, as_of)
    if view is None:
        return False, "FI_NATIVE_LEDGER_LOT_MISSING"
    if view.residual_nominal == 0:
        return False, "FI_NATIVE_REDEMPTION_CLOSE_LEDGER_MISSING"
    return False, "FI_NATIVE_ENTITLEMENT_AWARE_MARKET_SALE_MISSING"
