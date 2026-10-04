"""Canonical expected friction and realized cash flow, with distinct price anchors.

The public tariff is a PAPER sensitivity model. Account-specific terms are never
certified by this module. Realized execution prices already include spread/slip.
"""
from dataclasses import dataclass
from decimal import Decimal

from .common import number, identity

ZERO = Decimal(0)
SPOT_LEDGER_FAMILIES = frozenset({"ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES", "OPCIONES"})


def ledger_leg_rate(family, *, rebated=False):
    """Existing eight-decimal PAPER tariff, including the option premium charge."""
    import au_fee_schedule as schedule
    if family not in SPOT_LEDGER_FAMILIES:
        raise ValueError("FAMILY_ECONOMICS_NOT_VALIDATED")
    if rebated and family in schedule.FAMILIAS_CON_BONIFICACION_INTRADIARIA:
        return number(schedule.costo_por_tramo_bonificado(family), nonnegative=True)
    return number(schedule.costo_por_tramo(family), nonnegative=True)


def ledger_leg_cost(price, quantity, family):
    # Preserve the factual ledger's per-fill cent rounding, distinct from the
    # unrounded unit-price sensitivity used before execution.
    return (number(price, positive=True) * number(quantity, positive=True)
            * ledger_leg_rate(family)).quantize(Decimal("0.01"))


def smaller_leg_rebate(buy_notional, sell_notional, full_rate, low_rate, *, rounded=False):
    discount = number(full_rate, nonnegative=True) - number(low_rate, nonnegative=True)
    if discount < 0:
        raise ValueError("INVALID_INTRADAY_REBATE")
    value = min(number(buy_notional, positive=True), number(sell_notional, positive=True)) * discount
    return value.quantize(Decimal("0.01")) if rounded else value


def price_sensitivity_fees(buy_notional, sell_notional, full_rate, low_rate):
    buy, sell = number(buy_notional, positive=True), number(sell_notional, positive=True)
    return (buy + sell) * number(full_rate, nonnegative=True) - smaller_leg_rebate(buy, sell, full_rate, low_rate)


@dataclass(frozen=True)
class FeeModel:
    commission: Decimal
    rights: Decimal
    vat: Decimal
    commission_vat: bool
    rights_vat: bool
    provenance: str
    account_terms: str = "NO_VERIFICADO"

    def __post_init__(self):
        for key in ("commission", "rights", "vat"):
            object.__setattr__(self, key, number(getattr(self, key), nonnegative=True))
        if not self.provenance or not isinstance(self.commission_vat, bool) or not isinstance(self.rights_vat, bool):
            raise ValueError("FEE_PROVENANCE_REQUIRED")

    @property
    def full_rate(self):
        return self.commission * (1 + self.vat * self.commission_vat) + self.low_rate

    @property
    def low_rate(self):
        return self.rights * (1 + self.vat * self.rights_vat)


def paper_fee_model(family):
    # This adapter consumes the existing authority, never an invented account tariff.
    import au_fee_schedule as schedule
    if family not in {"ACCIONES", "CEDEARS", "ETFS"}:
        raise ValueError("FAMILY_ECONOMICS_NOT_VALIDATED")
    fee = schedule.arancel_de(family)
    return FeeModel(number(fee.comision), number(fee.derecho), number(schedule.IVA_PCT),
                    fee.comision_iva, fee.derecho_iva,
                    "PAPER:au_fee_schedule:PPI_2026-07-01+BYMA_2026-08-21")


def expected_round_trip_cost(entry, exit_price, quantity, multiplier, model, *,
                             intraday_eligible=False, prices_include_friction=True,
                             spread=0, entry_slippage=0, exit_slippage=0):
    entry, exit_price = number(entry, positive=True), number(exit_price, positive=True)
    qty, factor = number(quantity, positive=True), number(multiplier, positive=True)
    frictions = [number(x, nonnegative=True) for x in (spread, entry_slippage, exit_slippage)]
    if prices_include_friction and any(frictions):
        raise ValueError("EXECUTION_PRICE_FRICTION_DOUBLE_COUNT")
    buy, sell = entry * qty * factor, exit_price * qty * factor
    rebate_base = min(buy, sell) if intraday_eligible else ZERO
    commission = (buy + sell - rebate_base) * model.commission
    rights = (buy + sell) * model.rights
    vat = commission * model.vat * model.commission_vat + rights * model.vat * model.rights_vat
    explicit = commission + rights + vat
    execution_friction = buy * sum(frictions, ZERO)
    return {"commission": commission, "rights": rights, "vat": vat,
            "explicit_fees": explicit, "spread": buy * frictions[0],
            "entry_slippage": buy * frictions[1], "exit_slippage": buy * frictions[2],
            "total_expected_friction": explicit + execution_friction,
            "price_anchor": "EXECUTION_PRICES" if prices_include_friction else "PRE_EXECUTION_REFERENCE",
            "intraday_discount": intraday_eligible, "provenance": model.provenance,
            "account_terms": model.account_terms}


def realized_round_trip_cost(position, fills):
    ident = identity(position)
    if ident[1] not in {"ACCIONES", "CEDEARS", "ETFS"} and not position.get("contract_cash_multiplier"):
        raise ValueError("CONTRACT_MULTIPLIER_REQUIRED")
    factor = number(position.get("contract_cash_multiplier", 1), positive=True)
    buy_qty = sell_qty = buy = sell = charges = ZERO
    seen = set()
    for fill in fills:
        fill_id = fill.get("fill_id", fill.get("id"))
        if fill_id is None or fill_id in seen or fill.get("paper_id") != position["paper_id"]:
            raise ValueError("FILL_IDENTITY_OR_DUPLICATE")
        seen.add(fill_id)
        for key, value in zip(("symbol", "asset_class", "settlement", "currency", "market"), ident):
            if key in fill and str(fill[key]).strip().upper() != value:
                raise ValueError("FILL_IDENTITY_MISMATCH")
        qty, price = number(fill["quantity"], positive=True), number(fill["price"], positive=True)
        charges += number(fill.get("costs"), nonnegative=True)
        if fill["side"] == "BUY_SIMULATED":
            buy_qty += qty
            buy += qty * price * factor
        elif fill["side"] == "SELL_SIMULATED":
            sell_qty += qty
            sell += qty * price * factor
        else:
            raise ValueError("PAPER_FILL_REQUIRED")
    if buy_qty != sell_qty or buy_qty != number(position["quantity"], positive=True):
        raise ValueError("FILL_QUANTITY_RECONCILIATION")
    gross, net = sell - buy, sell - buy - charges
    for field, actual in (("gross_pnl", gross), ("net_pnl", net)):
        if position.get(field) is not None and abs(number(position[field]) - actual) > Decimal("0.01"):
            raise ValueError("LEDGER_MONEY_RECONCILIATION:" + field)
    if position.get("entry_cost") is not None and position.get("exit_cost") is not None:
        if abs(number(position["entry_cost"]) + number(position["exit_cost"]) - charges) > Decimal("0.01"):
            raise ValueError("LEDGER_COST_RECONCILIATION")
    return {"gross": gross, "costs": charges, "net": net, "entry_notional": buy,
            "net_return": net / buy, "turnover": buy + sell, "fills": len(fills),
            "currency": ident[3], "cost_components": "NO_VERIFICADO",
            "spread_and_slippage": "EMBEDDED_IN_FILLS", "reconciled": True}
