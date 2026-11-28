"""Canonical expected friction and realized cash flow, with distinct price anchors.

The public tariff is a PAPER sensitivity model. Account-specific terms are never
certified by this module. Realized execution prices already include spread/slip.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_EVEN
import hashlib
import json

from bs_instrument_contracts import aware_datetime, cash_currency, family_name, utc_microseconds

from .common import number, identity

ZERO = Decimal(0)
MERV_LISTED_TERMS = ("INMEDIATA", "A-24HS")
MERV_LISTING_KNOWN_AT = "2026-11-04T00:00:00-03:00"
SPOT_LEDGER_FAMILIES = frozenset({"ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES", "OPCIONES"})
_COST_COMPONENTS = ("commission", "rights", "clearing", "premium_rights", "vat",
                    "commission_vat", "rights_vat", "clearing_vat", "premium_rights_vat",
                    "minimum_commission", "rebate_policy", "fee_currency", "fx_rate", "fx_source",
                    "fee_quantum", "rounding")
_IOL_DATA_CHANNEL = "IOL_REST"
_IOL_PAPER_FEE_BASES = frozenset({"HISTORICAL_PPI_PUBLIC_ASSUMPTIONS", "PAPER_COMPONENT_OVERRIDES",
                                "LOCAL_ETF_TARIFF_UNVERIFIED"})


def _policy_digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def ledger_leg_rate(family, *, currency=None, rebated=False):
    """Existing eight-decimal PAPER tariff, including the option premium charge."""
    import au_fee_schedule as schedule
    if family not in SPOT_LEDGER_FAMILIES:
        raise ValueError("FAMILY_ECONOMICS_NOT_VALIDATED")
    exact = {"currency": currency} if family == "LETRAS" else {}
    if rebated and family in schedule.FAMILIAS_CON_BONIFICACION_INTRADIARIA:
        return number(schedule.costo_por_tramo_bonificado(family, **exact), nonnegative=True)
    return number(schedule.costo_por_tramo(family, **exact), nonnegative=True)


def ledger_leg_cost(price, quantity, family, *, currency=None):
    # Preserve the factual ledger's per-fill cent rounding, distinct from the
    # unrounded unit-price sensitivity used before execution.
    return (number(price, positive=True) * number(quantity, positive=True)
            * ledger_leg_rate(family, currency=currency)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


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
    market_data_identity: tuple[str, str, str, str, str] | None = None
    fee_assumption_basis: str | None = None

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
        if self.market_data_identity is not None or self.fee_assumption_basis is not None:
            key = self.market_data_identity
            if (type(key) is not tuple or len(key) != 5
                    or any(not isinstance(value, str) or not value for value in key)
                    or key[1] not in SPOT_LEDGER_FAMILIES or key[3] != self.currency
                    or (key[1] != self.family and not is_local_merv_listing(
                        key[1], key[3], symbol=key[0], market=key[2], settlement=key[4]))
                    or self.fee_assumption_basis not in _IOL_PAPER_FEE_BASES
                    or self.account_terms != "NO_VERIFICADO"):
                raise ValueError("COST_PROVENANCE_CONTEXT_INVALID")

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

    def linear_leg_rate(self, *, decision_at, rebated=False):
        """Eight-place sensitivity of a proportional PAPER schedule."""
        if self.minimum_commission != ZERO or self.tiers:
            raise ValueError("COST_PROPORTIONAL_SENSITIVITY_UNAVAILABLE")
        components = self.leg_components(Decimal(1), decision_at=decision_at, rebated=rebated)
        return sum(components.values(), ZERO).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN)

    def sale_cost(self, *, buy_notional, sell_notional, buy_quantity, sell_quantity,
                  sold_quantity=ZERO, bought_at, decision_at, intraday_eligible=False):
        """Net sale cost and applied rebate in the existing nonnegative fee ledger."""
        sell_cost = self.leg_cost(sell_notional, decision_at=decision_at)
        if not intraday_eligible or self.rebate_policy != "COMMISSION_ON_SMALLER_INTRADAY_LEG":
            return sell_cost, ZERO
        if buy_notional * sell_quantity <= sell_notional * buy_quantity:
            discount = (self.leg_cost(buy_notional, decision_at=bought_at) -
                        self.leg_cost(buy_notional, decision_at=bought_at, rebated=True))
            # Prorate the commission actually booked on the original buy. A
            # partial sale does not incur another minimum on that purchase.
            after = (discount * (sold_quantity + sell_quantity) / buy_quantity).quantize(
                self.fee_quantum, rounding=ROUND_HALF_EVEN)
            before = (discount * sold_quantity / buy_quantity).quantize(
                self.fee_quantum, rounding=ROUND_HALF_EVEN)
            rebate = min(sell_cost, after - before)
        else:
            rebate = sell_cost - self.leg_cost(sell_notional, decision_at=decision_at, rebated=True)
        return sell_cost - rebate, rebate

    def affordable_quantity(self, *, entry_unit, stop_unit, lot, quantity_limit, cash, risk, decision_at):
        """Largest executable lot under both budgets, including discontinuous fee tiers."""
        entry_lot, stop_lot = entry_unit * lot, stop_unit * lot
        loss_lot = max(ZERO, entry_lot - stop_lot)
        upper = min(int(quantity_limit / lot), int(cash / entry_lot))
        if loss_lot > 0:
            upper = min(upper, int(risk / loss_lot))
        if upper < 1:
            return ZERO
        # A tier can reduce the absolute fee at a threshold. The budgets are
        # monotone only within each interval of a constant buy/sell tier pair.
        breaks = {1, upper + 1}
        for tier in self.tiers:
            for unit in (entry_lot, stop_lot):
                start = int((tier.from_notional / unit).to_integral_value(rounding=ROUND_CEILING))
                if 1 < start <= upper:
                    breaks.add(start)
        edges = sorted(breaks)

        def fits(lots):
            buy, sell = entry_lot * lots, stop_lot * lots
            buy_fee = self.leg_cost(buy, decision_at=decision_at)
            return (buy + buy_fee <= cash and
                    loss_lot * lots + buy_fee + self.leg_cost(sell, decision_at=decision_at) <= risk)

        for lower, next_lower in reversed(list(zip(edges, edges[1:]))):
            if not fits(lower):
                continue
            upper = next_lower - 1
            while lower < upper:
                mid = (lower + upper + 1) // 2
                if fits(mid):
                    lower = mid
                else:
                    upper = mid - 1
            return lower * lot
        return ZERO

    def diagnostic(self):
        def render(value):
            return str(value) if isinstance(value, Decimal) else value
        result = {"schema_version": 1, "policy_version": self.policy_version,
                "scope": self.scope, "authority": self.scope,
                "account_authority": self.account_terms,
                "family": self.family, "currency": self.currency,
                "effective_at": self.effective_at, "known_at": self.known_at,
                "source": self.source, "account_terms": self.account_terms,
                "components": {field: render(getattr(self, field)) for field in _COST_COMPONENTS},
                "tiers": ([{field: str(getattr(tier, field)) for field in
                            ("from_notional", "commission_rate", "minimum_commission")}
                           for tier in self.tiers] if self.tiers is not None else None),
                "rounding_boundary": "ONE_COMBINED_PAPER_FEE_PER_FILL",
                "price_tick_used_for_fee_rounding": False,
                "paper_assumptions": {
                    "clearing": render(self.clearing),
                    "minimum_commission": render(self.minimum_commission),
                    "tiers": "EXPLICIT_COMPONENT_TABLE" if self.tiers is not None else None,
                    "fee_currency": "SAME_AS_SIMULATED_LEDGER",
                    "account_tariff_margin_statement": "EXTERNAL_NO_VERIFICADO"},
                "personal_taxes": "OUT_OF_SCOPE"}
        if self.market_data_identity is not None:
            base_digest = _policy_digest(result)
            public = self.fee_assumption_basis == "HISTORICAL_PPI_PUBLIC_ASSUMPTIONS"
            result.update(schema_version=2, schedule_sha256=base_digest,
                market_data={"provider": "IOL", "channel": _IOL_DATA_CHANNEL,
                             "identity": list(self.market_data_identity)},
                tariff_provenance={"assumption_basis": self.fee_assumption_basis,
                    "tariff_provider": "PPI" if public else "NO_VERIFICADO",
                    "tariff_channel": "PUBLIC_TARIFF" if public else "NO_VERIFICADO",
                    "simulation_channel": "PAPER", "iol_tariff_evidence": "NO_VERIFICADO",
                    "account_segment": "NO_VERIFICADO", "account_channel": "NO_VERIFICADO"})
        result["policy_sha256"] = _policy_digest(result)
        return result

    @classmethod
    def from_diagnostic(cls, payload):
        """Read back the versioned receipt and require its canonical digest and fields."""
        try:
            terms = {name: payload[name] for name in (
                "family", "currency", "policy_version", "effective_at", "known_at", "source",
                "scope", "account_terms")}
            terms.update({name: payload["components"][name] for name in _COST_COMPONENTS})
            tiers = payload["tiers"]
            if payload["schema_version"] == 2:
                terms["market_data_identity"] = tuple(payload["market_data"]["identity"])
                terms["fee_assumption_basis"] = payload["tariff_provenance"]["assumption_basis"]
            contract = cls(**terms, tiers=None if tiers is None else tuple(FeeTier(**tier) for tier in tiers))
            if contract.diagnostic() != payload:
                raise ValueError("noncanonical cost snapshot")
            return contract
        except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
            raise ValueError("PAPER_COST_POLICY_SNAPSHOT_INVALID") from exc


def is_local_merv_listing(family, currency, *, symbol, market, settlement):
    return (family in {"ACCIONES", "ETFS"} and currency == "ARS"
            and str(symbol or "").strip().upper() == "MERV"
            and str(market or "").strip().upper() == "BYMA"
            and str(settlement or "").strip().upper() in MERV_LISTED_TERMS)


def local_etf_reference_keys(at):
    """BYMA launch notice and the Fima secondary-market factsheet for MERV."""
    if aware_datetime(at) < aware_datetime(MERV_LISTING_KNOWN_AT):
        return ()
    return tuple(("MERV", "ETFS", "BYMA", "ARS", term) for term in MERV_LISTED_TERMS)


def paper_cost_contract(family, currency, decision_at, *, symbol=None, market=None, settlement=None,
                        market_data_channel=None):
    """Current versioned PAPER schedule; not an authenticated account tariff."""
    import au_fee_schedule as schedule
    family = family_name(family)
    if family not in SPOT_LEDGER_FAMILIES:
        raise ValueError("FAMILY_ECONOMICS_NOT_VALIDATED")
    at = aware_datetime(decision_at)
    currency = cash_currency(currency)
    observed_identity = (symbol, family, market, currency, settlement) if market_data_channel == _IOL_DATA_CHANNEL else None
    # BYMA's 03/11/2026 launch and the Fima gForce MSCI Merval FCIA ETF
    # fact sheet identify the ARS secondary listing MERV at T+0 and T+1.
    # Select its economic class separately from PPI's instrument/search key.
    local_ars_etf = is_local_merv_listing(family, currency, symbol=symbol, market=market,
                                       settlement=settlement)
    if local_ars_etf:
        family = "ETFS"
    fee = None if local_ars_etf else schedule.arancel_de(family, currency=currency)
    revised = family in {"LETRAS", "OBLIGACIONES"}
    # The pre-existing BYMA rights assumption for ARS Letras has no proved
    # currency-specific mapping to USD/MEP/CCL. Keep the public broker rate
    # visible, but never bind a dollar PAPER decision on an invented right.
    unbound_dollar_rights = family == "LETRAS" and currency != "ARS"
    # An ETF abroad has a USD minimum (which differs between PPI channels),
    # while a local CEDEAR of an ETF is a different instrument.  The generic
    # ETFS family carries neither that identity nor the execution venue.
    unbound_etf_terms = family == "ETFS"
    rebate_policy = ("COMMISSION_ON_SMALLER_INTRADAY_LEG"
                     if family in schedule.FAMILIAS_CON_BONIFICACION_INTRADIARIA else "NONE")
    version = "POROTA_PAPER_PUBLIC_TARIFF_COMPONENTS_V1"
    effective_at = "2026-07-01T00:00:00-03:00"
    known_at = "2026-08-21T00:00:00-03:00"
    source = "PAPER:au_fee_schedule:PPI_PUBLIC_2026-07-01:BYMA_RIGHTS_UNVERIFIED:ACCOUNT_UNVERIFIED"
    if local_ars_etf:
        version = "POROTA_PAPER_LOCAL_ETF_TERMS_V1"
        # The notice provides a publication date, without an intraday timestamp.
        effective_at = known_at = MERV_LISTING_KNOWN_AT
        source = "PAPER:BYMA_FIMA_MERV_ARS:ETF_LOCAL_TARIFF_UNVERIFIED:ACCOUNT_UNVERIFIED"
    elif unbound_etf_terms:
        version = "POROTA_PAPER_PUBLIC_TARIFF_COMPONENTS_V2"
        known_at = "2026-10-27T00:13:27Z"
        source = "PAPER:PPI_PUBLIC_2026-07-01:ETF_VENUE_AND_MINIMUM_UNVERIFIED:BYMA_RIGHTS_UNVERIFIED:ACCOUNT_UNVERIFIED"
    elif revised:
        version = "POROTA_PAPER_PUBLIC_TARIFF_COMPONENTS_V2"
        known_at = "2026-10-26T19:12:00Z"
        source = "PAPER:PPI_PUBLIC_2026-07-01:OBSERVED_2026-10-26:BYMA_RIGHTS_UNVERIFIED:ACCOUNT_UNVERIFIED"
    assumption_basis = None
    if observed_identity is not None:
        if local_ars_etf:
            assumption_basis = "LOCAL_ETF_TARIFF_UNVERIFIED"
        else:
            baseline = schedule.LETRAS_DOLARES if unbound_dollar_rights else schedule.ARANCELES[family]
            assumption_basis = ("HISTORICAL_PPI_PUBLIC_ASSUMPTIONS"
                if fee == baseline and number(schedule.IVA_PCT) == Decimal("0.21")
                else "PAPER_COMPONENT_OVERRIDES")
    contract = VersionedPaperCostContract(
        family=family, currency=currency,
        policy_version=version, effective_at=effective_at, known_at=known_at, source=source,
        commission=(None if local_ars_etf else number(fee.comision)),
        rights=(None if unbound_dollar_rights or unbound_etf_terms else number(fee.derecho)),
        clearing=(None if local_ars_etf else ZERO),
        premium_rights=(None if local_ars_etf else number(fee.derecho_sobre_prima)),
        vat=number(schedule.IVA_PCT),
        commission_vat=(None if local_ars_etf else fee.comision_iva),
        rights_vat=(None if unbound_dollar_rights or unbound_etf_terms else fee.derecho_iva),
        clearing_vat=(None if local_ars_etf else False),
        premium_rights_vat=(None if local_ars_etf else fee.prima_iva),
        minimum_commission=(None if unbound_etf_terms else ZERO),
        tiers=(None if local_ars_etf else ()),
        rebate_policy=(None if local_ars_etf else rebate_policy),
        fee_currency=cash_currency(currency), fx_rate=Decimal("1"),
        fx_source="IDENTITY_SAME_SIMULATED_LEDGER_CURRENCY",
        fee_quantum=Decimal("0.01"), rounding="ROUND_HALF_EVEN",
        market_data_identity=observed_identity, fee_assumption_basis=assumption_basis)
    # Callers persist this result and enforce it in their canonical admission.
    # Constructing diagnostics on an early cut is permitted; it never bypasses
    # binding_error's exact known/effective clocks.
    contract.binding_error(at)
    return contract


def paper_quote_cost_contract(quote, decision_at):
    return paper_cost_contract(quote.asset_class, quote.currency, decision_at,
                               symbol=quote.symbol, market=quote.market, settlement=quote.settlement,
                               market_data_channel=quote.metadata_source)


def paper_fee_model(family):
    # This adapter consumes the existing authority, never an invented account tariff.
    import au_fee_schedule as schedule
    if family not in {"ACCIONES", "CEDEARS", "ETFS"}:
        raise ValueError("FAMILY_ECONOMICS_NOT_VALIDATED")
    fee = schedule.arancel_de(family)
    return FeeModel(number(fee.comision), number(fee.derecho), number(schedule.IVA_PCT),
                    fee.comision_iva, fee.derecho_iva,
                    "PAPER:au_fee_schedule:PPI_PUBLIC_2026-07-01:BYMA_RIGHTS_UNVERIFIED:ACCOUNT_UNVERIFIED")


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
