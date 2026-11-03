"""Read-only bridge from a closed PAPER lot to confirmed income events.

Only a completed simulated BUY/SELL pair can realize price PnL. A123 records
coupon/amortization cash in its own currency from explicit settlement proofs.
No FX translation, strategy signal, source authentication, broker fill or
account-specific fee is inferred by joining these two ledgers. An amortization
before a spot sale requires a native nominal rebasing lifecycle and is blocked.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from bs_instrument_contracts import (aware_datetime, cash_currency,
                                     contract_from_metadata, decimal_value)
from cd_spot_ledger import single_close_proceeds
from rc6_fixed_income_event_reducer import (EconomicEvent, PositionSeed, reduce_events)
from rc6_performance.costs import paper_cost_contract


PAPER_ONLY = True
ENTRY_AUTHORITY = False
REAL_ROUTES_USED = ()
_FAMILIES = frozenset({"BONOS", "LETRAS", "OBLIGACIONES"})


@dataclass(frozen=True)
class FixedIncomePaperSnapshot:
    paper_id: str
    symbol: str
    family: str
    trading_currency: str
    entry_cash: Decimal
    exit_cash: Decimal
    trade_net_pnl: Decimal
    receipt_cash_by_currency: tuple[tuple[str, Decimal], ...]
    net_by_currency: tuple[tuple[str, Decimal], ...]
    cost_scope: str = "PAPER_ESTIMATE_NOT_ACCOUNT_FEE"
    status: str = "CLOSED_PAPER_INPUT_RECONCILIATION_ONLY"
    entry_authority: bool = False
    real_routes_used: tuple = ()


def _exact_position(root, seed):
    if not isinstance(seed, PositionSeed) or not isinstance(root, dict):
        raise ValueError("FIXED_INCOME_POSITION_PROOF_REQUIRED")
    if root.get("asset_class") not in _FAMILIES or root.get("source") != "PRODUCTION_PAPER":
        raise ValueError("FIXED_INCOME_PAPER_FAMILY_REQUIRED")
    if (root.get("paper_id"), root.get("symbol"), root.get("asset_class"),
        root.get("market"), root.get("currency"), root.get("settlement")) != (
        seed.paper_id, seed.symbol, seed.family, seed.market,
        seed.trading_currency, seed.settlement
    ):
        raise ValueError("FIXED_INCOME_POSITION_IDENTITY_MISMATCH")
    if seed.nominal_unit != "VN" or (
        decimal_value(root.get("quantity"), "VN", positive=True)
        != decimal_value(seed.initial_nominal, "VN", positive=True)
    ) or aware_datetime(root.get("opened_at")) != aware_datetime(seed.opened_at):
        raise ValueError("FIXED_INCOME_POSITION_VN_OR_TIME_MISMATCH")
    try:
        features = json.loads(root.get("features_json") or "")
        snapshot = features["financial_contract"]
        contract = contract_from_metadata(root["symbol"], root["asset_class"], snapshot)
        multiplier = decimal_value(features["contract_cash_multiplier"], "price basis", positive=True)
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("FIXED_INCOME_PRICE_BASIS_UNVERIFIED") from exc
    if (contract.key != (seed.symbol, seed.family, seed.market,
                         seed.trading_currency, seed.settlement)
            or contract.cash_multiplier != multiplier):
        raise ValueError("FIXED_INCOME_CONTRACT_PRICE_BASIS_MISMATCH")
    try:
        economics = features["economics"]
        diagnostic = economics["cost_contract"]
        policy = paper_cost_contract(seed.family, seed.trading_currency, seed.opened_at)
        if (economics["modeled_tariff"] != "FULL_PER_LEG_CONSERVATIVE_NO_INTRADAY_REBATE"
                or diagnostic["policy_sha256"] != policy.diagnostic()["policy_sha256"]
                or diagnostic["scope"] != "EXPLICIT_PAPER_ASSUMPTIONS"
                or diagnostic["account_terms"] != "NO_VERIFICADO"
                or policy.binding_error(seed.opened_at)):
            raise ValueError("cost contract")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("FIXED_INCOME_PAPER_COST_POLICY_UNVERIFIED") from exc
    return multiplier, policy


def reconcile_closed_paper_lot(root, fills, seed: PositionSeed,
                               events: tuple[EconomicEvent, ...], *, as_of):
    """Join an exact closed lot and independently evidenced economic events.

    A partial sale, a remaining position, missing quote basis, or a change in
    nominal from amortization has no closed-lot authority here. The result is
    an arithmetic PAPER statement, not proof of actual account settlement.
    """
    at = aware_datetime(as_of)
    factor, policy = _exact_position(root, seed)
    if root.get("status") != "CLOSED" or not root.get("closed_at") or (
        aware_datetime(root["closed_at"]) > at
    ):
        raise ValueError("FIXED_INCOME_CLOSED_LOT_REQUIRED")
    if not isinstance(fills, (list, tuple)) or len(fills) != 2:
        raise ValueError("FIXED_INCOME_ONE_BUY_ONE_SELL_REQUIRED")
    buy = next((fill for fill in fills if fill.get("side") == "BUY_SIMULATED"), None)
    sell = next((fill for fill in fills if fill.get("side") == "SELL_SIMULATED"), None)
    if not buy or not sell or any(fill.get("paper_id") != seed.paper_id for fill in fills):
        raise ValueError("FIXED_INCOME_FILL_PAIR_IDENTITY_INVALID")
    qty = decimal_value(seed.initial_nominal, "VN", positive=True)
    entry = decimal_value(root["entry_price"], "entry price", positive=True)
    entry_fee = decimal_value(root["entry_cost"], "entry cost", nonnegative=True)
    if (buy.get("source") != root["source"]
            or aware_datetime(buy["filled_at"]) != aware_datetime(seed.opened_at)
            or decimal_value(buy["quantity"], "BUY VN", positive=True) != qty
            or decimal_value(buy["price"], "BUY price", positive=True) != entry
            or decimal_value(buy["costs"], "BUY cost", nonnegative=True) != entry_fee):
        raise ValueError("FIXED_INCOME_BUY_FILL_MISMATCH")
    exit_cash = single_close_proceeds(root, [sell])
    qty = decimal_value(seed.initial_nominal, "VN", positive=True)
    exit_price = decimal_value(root["exit_price"], "exit price", positive=True)
    if (policy.binding_error(root["closed_at"])
            or policy.leg_cost(entry * qty * factor, decision_at=seed.opened_at) != entry_fee
            or policy.leg_cost(exit_price * qty * factor, decision_at=root["closed_at"])
               != decimal_value(root["exit_cost"], "exit cost", nonnegative=True)):
        raise ValueError("FIXED_INCOME_PAPER_FEE_LEDGER_MISMATCH")
    income = reduce_events(seed, events, as_of=at)
    if income.residual_nominal != income.initial_nominal:
        raise ValueError("FIXED_INCOME_AMORTIZATION_REQUIRES_NATIVE_VN_REBASE")
    if any(aware_datetime(row.effective_at) > aware_datetime(root["closed_at"])
           for row in income.cash_entries):
        raise ValueError("FIXED_INCOME_POST_EXIT_CASH_NOT_ENTITLED")
    entry_cash = entry * qty * factor + entry_fee
    trade_net = exit_cash - entry_cash
    if trade_net != decimal_value(root["net_pnl"], "spot net pnl"):
        raise ValueError("FIXED_INCOME_SPOT_PNL_MISMATCH")
    cash_by_currency = dict(income.cash_by_currency)
    net = dict(cash_by_currency)
    currency = cash_currency(seed.trading_currency)
    net[currency] = net.get(currency, Decimal(0)) + trade_net
    return FixedIncomePaperSnapshot(
        seed.paper_id, seed.symbol, seed.family, currency, entry_cash, exit_cash,
        trade_net, tuple(sorted(cash_by_currency.items())), tuple(sorted(net.items())),
    )


def reconcile_closed_paper_store(path, seed, events, *, as_of):
    """Load one PAPER lot and fills read-only; never touch a broker/provider."""
    resolved = Path(path).resolve(strict=True)
    with sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        root = db.execute("SELECT * FROM paper_positions WHERE paper_id=?", (seed.paper_id,)).fetchone()
        if not root:
            raise ValueError("FIXED_INCOME_PAPER_POSITION_NOT_FOUND")
        fills = [dict(row) for row in db.execute(
            "SELECT * FROM paper_fills WHERE paper_id=? ORDER BY id", (seed.paper_id,))]
    return reconcile_closed_paper_lot(dict(root), fills, seed, tuple(events), as_of=as_of)
