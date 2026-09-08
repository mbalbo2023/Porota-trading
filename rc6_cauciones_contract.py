from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import re
from typing import Iterable, Mapping, Any


_TICKER_RE = re.compile(r"^(PESOS|DOLAR)([1-9][0-9]*)$")
_MIN_BY_PREFIX = {
    "PESOS": Decimal("100000"),
    "DOLAR": Decimal("100"),
}


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

    # PPI support confirmed BYMA cauciones use calendar days and Actual/365.
    return capital_d * (tna_d / Decimal("100")) * Decimal(term_days) / Decimal("365")


def gross_interest_for_ticker(ticker: str, capital: Any, tna_percent: Any) -> Decimal:
    identity = parse_ticker(ticker)
    validate_amount(ticker, capital)
    return gross_interest(capital, tna_percent, identity.term_days)


def theoretical_liquidity_date(order_load_date: date, ticker: str) -> date:
    """Return the support-backed theoretical liquidity date.

    PPI confirmed the term counts calendar days beginning on the order-load date.
    Therefore Friday + PESOS3 => Monday. This function deliberately performs no
    holiday/business-day adjustment: PPI did not provide a general rule for a
    theoretical liquidity date landing outside an operational settlement window.
    """
    identity = parse_ticker(ticker)
    return order_load_date + timedelta(days=identity.term_days)


def placed_side_book_levels(book_payload: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    """Return the book side PPI support identified for caucion colocadora.

    PPI support explicitly stated: colocadora uses Bids. No fallback to Offers/Asks
    is permitted because that would silently change market semantics.
    """
    for key in ("bids", "Bids", "BIDS"):
        levels = book_payload.get(key)
        if levels is not None:
            if not isinstance(levels, list):
                raise CaucionContractError("book bids must be a list")
            return levels
    raise CaucionContractError("book payload has no bids field")


def quote_from_book_level(ticker: str, level: Mapping[str, Any]) -> CaucionQuote:
    """Normalize one bid level using PPI's caucion semantics.

    `price` is annual TNA percentage and `quantity` is the monto of the tomadora
    position according to PPI support.
    """
    parse_ticker(ticker)
    try:
        price = Decimal(str(level["price"]))
        quantity = Decimal(str(level["quantity"]))
    except (KeyError, InvalidOperation, ValueError, TypeError) as exc:
        raise CaucionContractError("book level requires numeric price and quantity") from exc

    if price < 0 or quantity < 0:
        raise CaucionContractError("price/TNA and quantity must be non-negative")
    return CaucionQuote(
        ticker=ticker.upper(),
        tna_percent=price,
        quantity=quantity,
        side_source="BIDS",
    )


__all__ = [
    "CaucionContractError",
    "CaucionIdentity",
    "CaucionQuote",
    "parse_ticker",
    "validate_amount",
    "gross_interest",
    "gross_interest_for_ticker",
    "theoretical_liquidity_date",
    "placed_side_book_levels",
    "quote_from_book_level",
]
