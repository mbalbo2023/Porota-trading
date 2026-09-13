from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import re
from typing import Iterable, Mapping, Any
from zoneinfo import ZoneInfo

_TICKER_RE = re.compile(r"^(PESOS|DOLAR)([1-9][0-9]*)$")
_MIN_BY_PREFIX = {"PESOS": Decimal("100000"), "DOLAR": Decimal("100")}
_ARGENTINA_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
_MARKET_OPEN = time(10, 30)
_MARKET_CLOSE = time(17, 0)

# IMPORTANT: PPI exposes a generic book with bids/offers. The mapping of those
# sides to CAUCION COLOCADORA/TOMADORA is not yet proven by an explicit PPI
# semantic contract. Raw book data therefore fails closed unless upstream code
# supplies explicit, auditable semantic proof. This module must never guess the
# colocadora side from generic bid/offer vocabulary.
COLOCADORA_SIDE_PROOF = "COLOCADORA_SIDE_VALIDATED"
BOOK_RATE_PROOF = "TNA_PERCENT_VALIDATED"
BOOK_DEPTH_PROOF = "COLOCADORA_EXECUTABLE_PRINCIPAL_VALIDATED"


class CaucionContractError(ValueError):
    pass


@dataclass(frozen=True)
class CaucionIdentity:
    ticker: str
    currency_prefix: str
    term_days: int


@dataclass(frozen=True)
class CaucionQuote:
    ticker: str
    tna_percent: Decimal
    quantity: Decimal
    side_source: str


@dataclass(frozen=True)
class CaucionCostEstimate:
    gross_interest: Decimal
    ppi_commission: Decimal
    iva_on_ppi_commission: Decimal
    net_before_market_rights_and_other_taxes: Decimal
    market_rights_known: bool
    budget_is_authoritative_for_full_costs: bool


def parse_ticker(ticker: str) -> CaucionIdentity:
    raw = str(ticker).strip().upper()
    match = _TICKER_RE.fullmatch(raw)
    if not match:
        raise CaucionContractError(f"invalid caucion ticker: {ticker!r}")
    prefix, days_raw = match.groups()
    return CaucionIdentity(ticker=raw, currency_prefix=prefix, term_days=int(days_raw))


def validate_amount(ticker: str, amount: Any) -> Decimal:
    identity = parse_ticker(ticker)
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CaucionContractError("amount is not numeric") from exc
    if not value.is_finite():
        raise CaucionContractError("amount must be finite")
    if value != value.to_integral_value():
        raise CaucionContractError("caucion amount must be an integer; quantity step is 1")
    minimum = _MIN_BY_PREFIX[identity.currency_prefix]
    if value < minimum:
        raise CaucionContractError(
            f"amount below PPI-supported minimum for {identity.currency_prefix}: {minimum}"
        )
    return value


def gross_interest(capital: Any, tna_percent: Any, term_days: int) -> Decimal:
    try:
        capital_d = Decimal(str(capital))
        tna_d = Decimal(str(tna_percent))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CaucionContractError("capital/TNA must be numeric") from exc
    if term_days <= 0:
        raise CaucionContractError("term_days must be positive")
    if capital_d < 0 or tna_d < 0:
        raise CaucionContractError("capital and TNA must be non-negative")
    return capital_d * (tna_d / Decimal("100")) * Decimal(term_days) / Decimal("365")


def gross_interest_for_ticker(ticker: str, capital: Any, tna_percent: Any) -> Decimal:
    identity = parse_ticker(ticker)
    validate_amount(ticker, capital)
    return gross_interest(capital, tna_percent, identity.term_days)


def theoretical_liquidity_date(order_load_date: date, ticker: str) -> date:
    return order_load_date + timedelta(days=parse_ticker(ticker).term_days)


def is_same_day_concertation_window(moment: datetime) -> bool:
    if moment.tzinfo is None:
        raise CaucionContractError("moment must be timezone-aware")
    local = moment.astimezone(_ARGENTINA_TZ).timetz().replace(tzinfo=None)
    return _MARKET_OPEN <= local <= _MARKET_CLOSE


def ppi_annual_commission_percent(ticker: str) -> Decimal | None:
    return Decimal("2") if parse_ticker(ticker).currency_prefix == "PESOS" else None


def estimate_ppi_costs_before_market_rights(
    ticker: str,
    capital: Any,
    tna_percent: Any,
    *,
    ppi_commission_annual_percent: Any,
    iva_percent: Any,
) -> CaucionCostEstimate:
    identity = parse_ticker(ticker)
    capital_d = validate_amount(ticker, capital)
    try:
        commission_pct = Decimal(str(ppi_commission_annual_percent))
        iva_pct = Decimal(str(iva_percent))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CaucionContractError("commission and IVA must be numeric") from exc
    if commission_pct < 0 or iva_pct < 0:
        raise CaucionContractError("commission and IVA must be non-negative")
    if identity.currency_prefix == "PESOS" and commission_pct != Decimal("2"):
        raise CaucionContractError("PESOS colocadora PPI annual commission must be 2 percent")
    if identity.currency_prefix == "DOLAR" and commission_pct > Decimal("1"):
        raise CaucionContractError("DOLAR colocadora PPI annual commission cannot exceed 1 percent")
    gross = gross_interest(capital_d, tna_percent, identity.term_days)
    commission = gross_interest(capital_d, commission_pct, identity.term_days)
    iva = commission * iva_pct / Decimal("100")
    return CaucionCostEstimate(
        gross,
        commission,
        iva,
        gross - commission - iva,
        False,
        True,
    )


def placed_side_book_levels(
    book_payload: Mapping[str, Any],
    *,
    side_key: str | None = None,
    semantic_proof: str | None = None,
) -> Iterable[Mapping[str, Any]]:
    """Return an explicitly validated colocadora side; never infer bids/offers.

    ``side_key`` remains an upstream evidence decision. Until A4 produces an
    auditable PPI-specific mapping, callers must not supply the proof token in
    production. This function deliberately has no default/fallback side.
    """
    if semantic_proof != COLOCADORA_SIDE_PROOF:
        raise CaucionContractError("book side->colocadora semantics not validated")
    normalized = str(side_key or "").strip().lower()
    if normalized not in {"bids", "offers"}:
        raise CaucionContractError("validated colocadora side must be explicitly bids or offers")
    for key, levels in book_payload.items():
        if str(key).lower() != normalized:
            continue
        if not isinstance(levels, list):
            raise CaucionContractError(f"book {normalized} must be a list")
        return levels
    raise CaucionContractError(f"book payload has no explicit {normalized} field")


def quote_from_book_level(
    ticker: str,
    level: Mapping[str, Any],
    *,
    side_key: str | None = None,
    side_semantic_proof: str | None = None,
    rate_semantic_proof: str | None = None,
    depth_semantic_proof: str | None = None,
) -> CaucionQuote:
    """Parse a book level only after side/rate/depth semantics are proven.

    Generic PPI ``price``/``quantity`` names are insufficient evidence by
    themselves. Requiring three independent proof tokens prevents this helper
    from silently turning raw market data into an executable PAPER offer.
    """
    parse_ticker(ticker)
    if side_semantic_proof != COLOCADORA_SIDE_PROOF:
        raise CaucionContractError("book side->colocadora semantics not validated")
    normalized = str(side_key or "").strip().lower()
    if normalized not in {"bids", "offers"}:
        raise CaucionContractError("validated colocadora side must be explicitly bids or offers")
    if rate_semantic_proof != BOOK_RATE_PROOF:
        raise CaucionContractError("book price->TNA semantics not validated")
    if depth_semantic_proof != BOOK_DEPTH_PROOF:
        raise CaucionContractError("book quantity->executable principal semantics not validated")
    try:
        price = Decimal(str(level["price"]))
        quantity = Decimal(str(level["quantity"]))
    except (KeyError, InvalidOperation, ValueError, TypeError) as exc:
        raise CaucionContractError("book level requires numeric price and quantity") from exc
    if not price.is_finite() or not quantity.is_finite() or price < 0 or quantity < 0:
        raise CaucionContractError("price/TNA and quantity must be finite and non-negative")
    return CaucionQuote(ticker.upper(), price, quantity, normalized.upper())
