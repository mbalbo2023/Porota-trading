"""Canonical expected friction and realized cash flow, with distinct price anchors.

The public tariff is a PAPER sensitivity model. Account-specific terms are never
certified by this module. Realized execution prices already include spread/slip.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
import hashlib
import json

from bs_instrument_contracts import aware_datetime, cash_currency, family_name, utc_microseconds

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
            * ledger_leg_rate(family)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


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


@dataclass(frozen=True)
class FeeTier:
    from_notional: Decimal
    commission_rate: Decimal
    minimum_commission: Decimal

    def __post_init__(self):
        for field in ("from_notional", "commission_rate", "minimum_commission"):
            object.__setattr__(self, field, number(getattr(self, field), nonnegative=True))


@dataclass(frozen=True)
class VersionedPaperCostContract:
    """Explicit simulator assumptions, separate from unknown account conditions.

    A missing required component closes BINDING. Zero is an explicit PAPER
    assumption only when present; it is never a substitute for an absent rate.
    The existing ledger policy has one combined fee cash boundary per fill.
    Component diagnostics remain unrounded sensitivities until that boundary.
    """
    family: str
    currency: str
    policy_version: str
    effective_at: str
    known_at: str
    source: str
    commission: Decimal | None
    rights: Decimal | None
    clearing: Decimal | None
    premium_rights: Decimal | None
    vat: Decimal | None
    commission_vat: bool | None
    rights_vat: bool | None
    clearing_vat: bool | None
    premium_rights_vat: bool | None
    minimum_commission: Decimal | None
    tiers: tuple[FeeTier, ...] | None
    rebate_policy: str | None
    fee_currency: str
    fx_rate: Decimal | None
    fx_source: str | None
    fee_quantum: Decimal | None
    rounding: str | None
    scope: str = "EXPLICIT_PAPER_ASSUMPTIONS"
    account_terms: str = "NO_VERIFICADO"

    def __post_init__(self):
        object.__setattr__(self, "family", family_name(self.family))
        object.__setattr__(self, "currency", cash_currency(self.currency))
        object.__setattr__(self, "fee_currency", cash_currency(self.fee_currency))
        for field in ("commission", "rights", "clearing", "premium_rights", "vat", "minimum_commission"):
            if getattr(self, field) is not None:
                object.__setattr__(self, field, number(getattr(self, field), nonnegative=True))
        for field in ("fx_rate", "fee_quantum"):
            if getattr(self, field) is not None:
                object.__setattr__(self, field, number(getattr(self, field), positive=True))
        for field in ("commission_vat", "rights_vat", "clearing_vat", "premium_rights_vat"):
            if getattr(self, field) is not None and not isinstance(getattr(self, field), bool):
                raise ValueError("COST_TAX_FLAG_INVALID:" + field)
        aware_datetime(self.effective_at)
        aware_datetime(self.known_at)
        if self.tiers is not None:
            if not all(isinstance(tier, FeeTier) for tier in self.tiers):
                raise ValueError("COST_TIERS_INVALID")
            if self.tiers and (self.tiers[0].from_notional != 0 or any(
                    after.from_notional <= before.from_notional
                    for before, after in zip(self.tiers, self.tiers[1:]))):
                raise ValueError("COST_TIERS_INVALID")
        if self.rebate_policy not in {None, "NONE", "COMMISSION_ON_SMALLER_INTRADAY_LEG"}:
            raise ValueError("COST_REBATE_POLICY_INVALID")
        if self.rounding not in {None, "ROUND_HALF_EVEN"}:
            raise ValueError("COST_ROUNDING_POLICY_INVALID")

    def binding_error(self, decision_at):
        if self.scope != "EXPLICIT_PAPER_ASSUMPTIONS" or not self.policy_version or not self.source:
            return "COST_POLICY_SCOPE_OR_PROVENANCE_REQUIRED"
        at = aware_datetime(decision_at)
        if utc_microseconds(self.effective_at) > utc_microseconds(at) or utc_microseconds(self.known_at) > utc_microseconds(at):
            return "COST_POLICY_NOT_KNOWN_OR_EFFECTIVE"
        required = ("commission", "rights", "clearing", "premium_rights", "vat",
                    "commission_vat", "rights_vat", "clearing_vat", "premium_rights_vat",
                    "minimum_commission", "tiers", "rebate_policy", "fx_rate",
                    "fx_source", "fee_quantum", "rounding")
        for field in required:
            if getattr(self, field) is None or (field == "fx_source" and not self.fx_source):
                return "COST_COMPONENT_UNKNOWN:" + field
        if self.fee_currency != self.currency:
            return "COST_CROSS_CURRENCY_MODEL_NOT_VALIDATED"
        if self.fx_rate != 1 or self.fx_source != "IDENTITY_SAME_SIMULATED_LEDGER_CURRENCY":
            return "COST_SAME_CURRENCY_FX_INVALID"
        if self.family not in SPOT_LEDGER_FAMILIES:
            return "FAMILY_ECONOMICS_NOT_VALIDATED"
        return ""

    def leg_components(self, notional, *, decision_at, rebated=False):
        error = self.binding_error(decision_at)
        if error:
            raise ValueError(error)
        base = number(notional, positive=True)
        tier = next((tier for tier in reversed(self.tiers) if base >= tier.from_notional), None)
        commission_rate = tier.commission_rate if tier else self.commission
        minimum = tier.minimum_commission if tier else self.minimum_commission
        commission = max(base * commission_rate, minimum)
        if rebated:
            if self.rebate_policy != "COMMISSION_ON_SMALLER_INTRADAY_LEG":
                raise ValueError("COST_REBATE_NOT_ELIGIBLE")
            commission = ZERO
        rights, clearing, premium = base * self.rights, base * self.clearing, base * self.premium_rights
        vat = self.vat * (commission * self.commission_vat + rights * self.rights_vat
                          + clearing * self.clearing_vat + premium * self.premium_rights_vat)
        return {"commission": commission, "rights": rights, "clearing": clearing,
                "premium_rights": premium, "vat": vat}

    def leg_cost(self, notional, *, decision_at, rebated=False):
        components = self.leg_components(notional, decision_at=decision_at, rebated=rebated)
        return sum(components.values(), ZERO).quantize(self.fee_quantum, rounding=ROUND_HALF_EVEN)

    def diagnostic(self):
        def render(value):
            return str(value) if isinstance(value, Decimal) else value
        fields = ("commission", "rights", "clearing", "premium_rights", "vat",
                  "commission_vat", "rights_vat", "clearing_vat", "premium_rights_vat",
                  "minimum_commission", "rebate_policy", "fee_currency", "fx_rate", "fx_source",
                  "fee_quantum", "rounding")
        result = {"schema_version": 1, "policy_version": self.policy_version,
                "scope": self.scope, "authority": self.scope,
                "account_authority": self.account_terms,
                "family": self.family, "currency": self.currency,
                "effective_at": self.effective_at, "known_at": self.known_at,
                "source": self.source, "account_terms": self.account_terms,
                "components": {field: render(getattr(self, field)) for field in fields},
                "tiers": ([{field: str(getattr(tier, field)) for field in
                            ("from_notional", "commission_rate", "minimum_commission")}
                           for tier in self.tiers] if self.tiers is not None else None),
                "rounding_boundary": "ONE_COMBINED_PAPER_FEE_PER_FILL",
                "price_tick_used_for_fee_rounding": False,
                "paper_assumptions": {
                    "clearing": render(self.clearing),
                    "minimum_commission": render(self.minimum_commission),
                    "tiers": "EXPLICIT_COMPONENT_TABLE",
                    "fee_currency": "SAME_AS_SIMULATED_LEDGER",
                    "account_tariff_margin_statement": "EXTERNAL_NO_VERIFICADO"},
                "personal_taxes": "OUT_OF_SCOPE"}
        result["policy_sha256"] = hashlib.sha256(json.dumps(
            result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        return result


def paper_cost_contract(family, currency, decision_at):
    """Current versioned PAPER schedule; not an authenticated account tariff."""
    import au_fee_schedule as schedule
    family = family_name(family)
    if family not in SPOT_LEDGER_FAMILIES:
        raise ValueError("FAMILY_ECONOMICS_NOT_VALIDATED")
    at = aware_datetime(decision_at)
    fee = schedule.arancel_de(family)
    contract = VersionedPaperCostContract(
        family=family, currency=cash_currency(currency),
        policy_version="POROTA_PAPER_PUBLIC_TARIFF_COMPONENTS_V1",
        effective_at="2026-07-01T00:00:00-03:00", known_at="2026-08-21T00:00:00-03:00",
        source="PAPER:au_fee_schedule:PPI_2026-07-01+BYMA_2026-08-21:ACCOUNT_UNVERIFIED",
        commission=number(fee.comision), rights=number(fee.derecho), clearing=ZERO,
        premium_rights=number(fee.derecho_sobre_prima), vat=number(schedule.IVA_PCT),
        commission_vat=fee.comision_iva, rights_vat=fee.derecho_iva,
        clearing_vat=False, premium_rights_vat=fee.prima_iva,
        minimum_commission=ZERO, tiers=(),
        rebate_policy=("COMMISSION_ON_SMALLER_INTRADAY_LEG"
                       if family in schedule.FAMILIAS_CON_BONIFICACION_INTRADIARIA else "NONE"),
        fee_currency=cash_currency(currency), fx_rate=Decimal("1"),
        fx_source="IDENTITY_SAME_SIMULATED_LEDGER_CURRENCY",
        fee_quantum=Decimal("0.01"), rounding="ROUND_HALF_EVEN")
    # Callers persist this result and enforce it in their canonical admission.
    # Constructing diagnostics on an early cut is permitted; it never bypasses
    # binding_error's exact known/effective clocks.
    contract.binding_error(at)
    return contract


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
