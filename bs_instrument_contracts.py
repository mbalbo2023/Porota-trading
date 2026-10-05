"""Contratos financieros v17: unidades explícitas, sin fallback a acciones.

Este módulo describe y calcula; no envía órdenes ni habilita una estrategia.
Los factores, garantías y vencimientos deben provenir de datos del contrato,
no de inferencias a partir del ticker. Una familia conocida puede carecer
todavía de metadatos suficientes para operar un instrumento concreto.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import unicodedata


FAMILIES = frozenset({"ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
                      "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI"})
SPOT_FAMILIES = frozenset({"ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
                           "OBLIGACIONES"})
_ALIASES = {"ACCION": "ACCIONES", "EQUITY": "ACCIONES", "CEDEAR": "CEDEARS",
            "ETF": "ETFS", "BONO": "BONOS", "TITULOSPUBLICOS": "BONOS",
            "LETRA": "LETRAS", "ON": "OBLIGACIONES",
            "OBLIGACIONESNEGOCIABLES": "OBLIGACIONES", "OPCION": "OPCIONES",
            "OPTIONS": "OPCIONES", "FUTURO": "FUTUROS", "FUTURES": "FUTUROS",
            "CAUCION": "CAUCIONES", "LEBAC": "LETRAS", "NOBAC": "LETRAS",
            "ACCIONESUSA": "ACCIONES", "FCIEXTERIOR": "FCI"}
ZERO = Decimal("0")
CASH_CURRENCIES = frozenset({"ARS", "USD", "USD_MEP", "USD_CCL"})


def cash_currency(value):
    """Normaliza las etiquetas observadas de PPI sin netear MEP y CCL.

    USD sin plaza conserva una caja propia; no se presume billete ni divisa.
    El ticker y su descripción NO determinan la moneda de negociación.
    """
    key = " ".join(str(value or "").strip().upper().split())
    key = "".join(c for c in unicodedata.normalize("NFD", key) if not unicodedata.combining(c))
    aliases = {"PESOS": "ARS", "PESO ARGENTINO": "ARS",
               "DOLARES BILLETE | MEP": "USD_MEP", "DOLARES DIVISA | CCL": "USD_CCL"}
    key = aliases.get(key, key)
    if key not in CASH_CURRENCIES:
        raise ValueError(f"Moneda/plaza no reconocida: {value!r}")
    return key


def decimal_value(value, name, *, positive=False, nonnegative=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{name}: número inválido") from exc
    if not result.is_finite() or (positive and result <= 0) or (nonnegative and result < 0):
        raise ValueError(f"{name}: valor fuera de rango")
    return result


def aware_datetime(value, name="fecha"):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name}: fecha inválida") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{name}: falta zona horaria")
    return result


def utc_microseconds(value):
    """An exact UTC instant, including offsets, without SQLite/float rounding."""
    if value is None:
        return None
    delta = aware_datetime(value).astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def register_exact_time_sql(connection):
    """Install a read-only scalar on this connection; no schema/data mutation."""
    connection.create_function("rc6_instant_us", 1, utc_microseconds, deterministic=True)


def family_name(value):
    key = str(value or "").upper().strip().replace("_", "").replace("-", "").replace(" ", "")
    key = _ALIASES.get(key, key)
    if key not in FAMILIES:
        raise ValueError(f"Familia no reconocida: {value!r}")
    return key


@dataclass(frozen=True)
class InstrumentContract:
    symbol: str
    family: str
    currency: str
    market: str
    settlement: str
    # Efectivo por una unidad cotizada y una unidad de cantidad.
    # Ej.: bono cotizado por 100 VN => 0.01; opción de 100 => 100.
    cash_multiplier: Decimal
    quantity_step: Decimal
    metadata_source: str
    expires_at: str | None = None
    initial_margin: Decimal | None = None
    maintenance_margin: Decimal | None = None
    underlying: str | None = None
    strike: Decimal | None = None
    option_right: str | None = None
    minimum_quantity: Decimal | None = None
    paper_margin_policy: str | None = None
    paper_margin_rate: Decimal | None = None
    price_tick: Decimal | None = None
    price_tick_source: str | None = None
    price_tick_known_at: str | None = None
    price_tick_effective_at: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "family", family_name(self.family))
        object.__setattr__(self, "currency", cash_currency(self.currency))
        if not all(str(x or "").strip() for x in (self.symbol, self.market, self.settlement, self.metadata_source)):
            raise ValueError("Contrato incompleto: símbolo, mercado, plazo y fuente son obligatorios")
        for field in ("cash_multiplier", "quantity_step"):
            object.__setattr__(self, field, decimal_value(getattr(self, field), field, positive=True))
        if self.price_tick is not None:
            object.__setattr__(self, "price_tick", decimal_value(self.price_tick, "price_tick", positive=True))
            if not str(self.price_tick_source or "").strip():
                raise ValueError("PRICE_TICK_PROVENANCE_REQUIRED")
        for field in ("price_tick_known_at", "price_tick_effective_at"):
            if getattr(self, field) is not None:
                aware_datetime(getattr(self, field), field)
        if self.minimum_quantity is not None:
            object.__setattr__(self, "minimum_quantity", decimal_value(self.minimum_quantity, "minimum_quantity", positive=True))
            if self.minimum_quantity % self.quantity_step:
                raise ValueError("Cantidad mínima incompatible con incremento")
        if self.family in {"OPCIONES", "FUTUROS"}:
            aware_datetime(self.expires_at, "vencimiento")
            if self.quantity_step != self.quantity_step.to_integral_value():
                raise ValueError("Los contratos derivados requieren lotes enteros")
        if self.family == "OPCIONES":
            if not self.underlying or self.option_right not in {"CALL", "PUT"}:
                raise ValueError("Opción sin subyacente o derecho válido")
            object.__setattr__(self, "strike", decimal_value(self.strike, "strike", positive=True))
        if self.family == "FUTUROS":
            policy = str(self.paper_margin_policy or "").upper()
            if self.initial_margin is None and policy != "CONSERVATIVE_NOTIONAL_RATE":
                raise ValueError("Futuro sin margen publicado ni policy PAPER")
            if policy:
                object.__setattr__(self, "paper_margin_policy", policy)
                rate = decimal_value(self.paper_margin_rate, "paper_margin_rate", positive=True)
                if rate > 1:
                    raise ValueError("paper_margin_rate superior al nocional")
                object.__setattr__(self, "paper_margin_rate", rate)
            if self.initial_margin is None:
                return
            object.__setattr__(self, "initial_margin", decimal_value(
                self.initial_margin, "initial_margin", positive=True))
            # A3/Argentina Clearing publishes one margin requirement. PAPER
            # keeps that full amount as both reserve and maintenance floor;
            # this is a conservative simulator policy, not a provider claim.
            maintenance = (self.initial_margin if self.maintenance_margin is None
                           else self.maintenance_margin)
            object.__setattr__(self, "maintenance_margin", decimal_value(
                maintenance, "maintenance_margin", positive=True))
            if self.maintenance_margin > self.initial_margin:
                raise ValueError("Garantía de mantenimiento superior a la inicial")

    @property
    def key(self):
        return (self.symbol, self.family, self.market, self.currency, self.settlement)

    def quantity(self, value):
        qty = decimal_value(value, "cantidad", positive=True)
        if self.minimum_quantity is not None and qty < self.minimum_quantity:
            raise ValueError("Cantidad inferior al mínimo del instrumento")
        if qty % self.quantity_step:
            raise ValueError("Cantidad incompatible con el lote del instrumento")
        return qty

    def execution_price_terms(self):
        """Execution grid only; it is not a cash quantum or settlement rule.

        Legacy DLR snapshots retain their original digest and receive only the
        narrowly validated standard-contract rule. Its retrieval date does not
        establish historical effectivity, PPI series availability or fees.
        """
        if self.price_tick is not None:
            return {"price_tick": self.price_tick, "source": self.price_tick_source,
                    "known_at": self.price_tick_known_at,
                    "effective_at": self.price_tick_effective_at,
                    "scope": "CONTRACT_EXECUTION_GRID"}
        if self.family == "FUTUROS":
            from rc6_ppi_future_contract_policy import standard_dlr_terms
            terms = standard_dlr_terms(self.symbol)
            if (terms and self.currency == "ARS" and self.market == "A3"
                    and self.settlement == "INMEDIATA"
                    and self.cash_multiplier == Decimal("1000")
                    and self.quantity_step == self.minimum_quantity == Decimal("1")
                    and self.underlying == terms["underlying"]
                    and aware_datetime(self.expires_at) == aware_datetime(terms["expires_at"])):
                return {"price_tick": Decimal(terms["price_tick"]),
                        "source": terms["price_tick_source"],
                        "known_at": terms["price_tick_known_at"],
                        "effective_at": terms["price_tick_effective_at"],
                        "historical_effectivity": "NO_VERIFICADO",
                        "scope": "STANDARD_DLR_LONG_PAPER_EXECUTION_GRID_V1"}
        raise ValueError("EXECUTION_PRICE_GRID_REQUIRED")

    def price(self, value, *, price_kind, source=None, rule=None, at=None):
        """Validate a typed price and preserve the supplied Decimal precision."""
        result = decimal_value(value, "precio", positive=True)
        kind = str(price_kind or "").upper()
        if kind in {"QUOTE", "TRADE", "FILL", "BOOK_MARK"}:
            terms = self.execution_price_terms()
            if result % terms["price_tick"]:
                raise ValueError("EXECUTION_PRICE_OFF_GRID:" + kind)
        elif kind == "PAPER_SETTLEMENT":
            # Explicit simulator input, never an official settlement claim.
            pass
        elif kind in {"OFFICIAL_SETTLEMENT", "OFFICIAL_MARK"}:
            if (not str(source or "").strip() or not isinstance(rule, dict)
                    or rule.get("mode") != "PRESERVE_PUBLISHED_DECIMAL"
                    or not str(rule.get("version") or "").strip()
                    or at is None):
                raise ValueError("OFFICIAL_PRICE_RULE_REQUIRED")
            decision = aware_datetime(at)
            for clock in ("known_at", "effective_at"):
                if not rule.get(clock) or utc_microseconds(rule[clock]) > utc_microseconds(decision):
                    raise ValueError("OFFICIAL_PRICE_RULE_NOT_KNOWN_OR_EFFECTIVE")
            quantum = decimal_value(rule.get("quantum"), "official_price_quantum", positive=True)
            if result % quantum:
                raise ValueError("OFFICIAL_PRICE_PRECISION_INVALID")
        else:
            raise ValueError("PRICE_KIND_REQUIRED")
        return result

    def executable_fill(self, value, *, side):
        """Quantize once in the adverse direction at the executable boundary."""
        raw = decimal_value(value, "precio fill", positive=True)
        direction = {"BUY": ROUND_CEILING, "SELL": ROUND_FLOOR}.get(str(side).upper())
        if direction is None:
            raise ValueError("EXECUTION_FILL_SIDE_REQUIRED")
        tick = self.execution_price_terms()["price_tick"]
        result = (raw / tick).to_integral_value(rounding=direction) * tick
        return self.price(result, price_kind="FILL")

    def notional(self, price, quantity):
        if self.family == "CAUCIONES":
            raise ValueError("La caución se calcula por capital, tasa y plazo; no precio por cantidad")
        return decimal_value(price, "precio", nonnegative=True) * self.quantity(quantity) * self.cash_multiplier

    def cash_required(self, price, quantity, fees=ZERO, *, side="LONG"):
        if side != "LONG":
            raise ValueError("La apertura vendida requiere una política de garantías específica")
        cost = decimal_value(fees, "costos", nonnegative=True)
        if self.family == "FUTUROS":
            # Garantía bloqueada; el nocional NO se paga como si fuera una acción.
            reserve = (self.initial_margin * self.quantity(quantity)
                       if self.initial_margin is not None else
                       self.notional(price, quantity) * self.paper_margin_rate)
            return reserve + cost
        return self.notional(price, quantity) + cost

    def pnl(self, entry_price, exit_price, quantity, *, side="LONG"):
        if self.family == "CAUCIONES":
            raise ValueError("Usar el ciclo de intereses y vencimiento de cauciones")
        if side not in {"LONG", "SHORT"} or (side == "SHORT" and self.family != "FUTUROS"):
            raise ValueError("Sentido de posición no admitido por este cálculo")
        entry = decimal_value(entry_price, "precio de entrada", nonnegative=True)
        exit_ = decimal_value(exit_price, "precio de salida", nonnegative=True)
        return (exit_ - entry) * self.quantity(quantity) * self.cash_multiplier * (1 if side == "LONG" else -1)

    def option_max_loss(self, premium, quantity, entry_fees=ZERO):
        if self.family != "OPCIONES":
            raise ValueError("Sólo para opciones compradas antes del ejercicio")
        return self.cash_required(premium, quantity, entry_fees)

    def daily_variation(self, previous_settlement, settlement_price, quantity, *, side="LONG"):
        if self.family != "FUTUROS":
            raise ValueError("Sólo los futuros usan este ajuste diario")
        return self.pnl(previous_settlement, settlement_price, quantity, side=side)

    def margin_deficit(self, collateral, quantity, price=None):
        if self.family != "FUTUROS":
            raise ValueError("Sólo para garantías de futuros")
        balance = decimal_value(collateral, "garantía remanente")
        qty = self.quantity(quantity)
        if self.initial_margin is None:
            if price is None:
                raise ValueError("Policy PAPER de margen requiere precio vigente")
            required = decimal_value(price, "precio", positive=True) * qty * self.cash_multiplier * self.paper_margin_rate
            return max(ZERO, required - balance)
        return max(ZERO, self.initial_margin * qty - balance) if balance < self.maintenance_margin * qty else ZERO


def contract_from_metadata(symbol, family, metadata):
    """Recibe metadatos NORMALIZADOS, no adivina nombres de campos de PPI.

    El adaptador deberá respaldar cada valor con un payload verificable.
    Incluye FCI; su rescate no equivale a una venta intradiaria de acciones.
    """
    fields = {name: metadata[name] for name in InstrumentContract.__dataclass_fields__
              if name not in {"symbol", "family"} and name in metadata}
    try:
        return InstrumentContract(symbol=symbol, family=family, **fields)
    except TypeError as exc:
        raise ValueError("Faltan metadatos financieros del instrumento") from exc
