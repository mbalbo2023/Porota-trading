"""Contratos financieros v17: unidades explícitas, sin fallback a acciones.

Este módulo describe y calcula; no envía órdenes ni habilita una estrategia.
Los factores, garantías y vencimientos deben provenir de datos del contrato,
no de inferencias a partir del ticker. Una familia conocida puede carecer
todavía de metadatos suficientes para operar un instrumento concreto.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
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

    def __post_init__(self):
        object.__setattr__(self, "family", family_name(self.family))
        object.__setattr__(self, "currency", cash_currency(self.currency))
        if not all(str(x or "").strip() for x in (self.symbol, self.market, self.settlement, self.metadata_source)):
            raise ValueError("Contrato incompleto: símbolo, mercado, plazo y fuente son obligatorios")
        for field in ("cash_multiplier", "quantity_step"):
            object.__setattr__(self, field, decimal_value(getattr(self, field), field, positive=True))
        if self.family in {"OPCIONES", "FUTUROS"}:
            aware_datetime(self.expires_at, "vencimiento")
            if self.quantity_step != self.quantity_step.to_integral_value():
                raise ValueError("Los contratos derivados requieren lotes enteros")
        if self.family == "OPCIONES":
            if not self.underlying or self.option_right not in {"CALL", "PUT"}:
                raise ValueError("Opción sin subyacente o derecho válido")
            object.__setattr__(self, "strike", decimal_value(self.strike, "strike", positive=True))
        if self.family == "FUTUROS":
            for field in ("initial_margin", "maintenance_margin"):
                object.__setattr__(self, field, decimal_value(getattr(self, field), field, positive=True))
            if self.maintenance_margin > self.initial_margin:
                raise ValueError("Garantía de mantenimiento superior a la inicial")

    @property
    def key(self):
        return (self.symbol, self.family, self.market, self.currency, self.settlement)

    def quantity(self, value):
        qty = decimal_value(value, "cantidad", positive=True)
        if qty % self.quantity_step:
            raise ValueError("Cantidad incompatible con el lote del instrumento")
        return qty

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
            return self.initial_margin * self.quantity(quantity) + cost
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

    def margin_deficit(self, collateral, quantity):
        if self.family != "FUTUROS":
            raise ValueError("Sólo para garantías de futuros")
        balance = decimal_value(collateral, "garantía remanente")
        qty = self.quantity(quantity)
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
