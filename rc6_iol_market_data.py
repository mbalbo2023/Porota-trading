"""IOL market data to the existing PAPER quote contract.

The caller chooses the source-clock evidence. An HTTP capture clock is
never a last trade or order book timestamp. The offhost harness exercises
this adapter without creating an operating-mode or release authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json

from be_paper_engine import Quote
from bs_instrument_contracts import aware_datetime, decimal_value
from iol_rest_readonly_rc6 import IOLRestError
from rc6_multisource_discovery import canonical_family


MARKETS = {"BYMA": "BCBA", "NYSE": "NYSE", "NASDAQ": "NASDAQ"}
TERMS = {"INMEDIATA": "T0", "A-24HS": "T1"}
SPOT = frozenset({"ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES", "OPCIONES"})


@dataclass(frozen=True)
class MarketCapture:
    provider_at: str
    received_at: str
    data_sha256: str


@dataclass(frozen=True)
class IOLObservation:
    quote: Quote
    current: MarketCapture | None
    book: MarketCapture


def _instant(stamp):
    try:
        return aware_datetime(stamp).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise IOLRestError("IOL_MARKET_CLOCK_UNVERIFIED") from None


def _positive(value):
    try:
        return decimal_value(value, "IOL", positive=True)
    except ValueError:
        raise IOLRestError("IOL_MARKET_NUMERIC_UNVERIFIED") from None


def _capture(data, *, received, venue_key, currency, source_time):
    if not isinstance(data, dict) or (
            data.get("symbol"), data.get("market"), data.get("settlement_term")) != venue_key:
        raise IOLRestError("IOL_MARKET_IDENTITY_MISMATCH")
    if data.get("currency") not in (None, currency):
        raise IOLRestError("IOL_MARKET_CURRENCY_MISMATCH")
    event = _instant(source_time)
    if event > received:
        raise IOLRestError("IOL_MARKET_CLOCK_IN_FUTURE")
    try:
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise IOLRestError("IOL_MARKET_DATA_UNVERIFIED") from None
    return MarketCapture(event.isoformat(), received.isoformat(), hashlib.sha256(canonical.encode()).hexdigest())


class IOLMarketReader:
    """GET-only market observations for verified five-part instrument mappings."""

    def __init__(self, rest, *, trade_clock=None, clock=None):
        self.rest = rest
        self.trade_clock = trade_clock
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def observe(self, identity, *, contract=None, exit_only=False, max_age_seconds=120):
        symbol, family, market, currency, term = identity
        if family not in SPOT or market not in MARKETS or term not in TERMS:
            raise IOLRestError("IOL_MARKET_IDENTITY_UNSUPPORTED")
        if contract is not None and contract.key != tuple(identity):
            raise IOLRestError("IOL_EXACT_CONTRACT_MISMATCH")
        venue_key = symbol, MARKETS[market], TERMS[term]
        # The depth response can omit currency. Bind it to an independent exact
        # asset response as well as to the symbol/market/term depth response.
        asset = self.rest.call("get_asset_info", {"symbol": symbol, "market": venue_key[1]})
        if asset.get("currency") != currency:
            raise IOLRestError("IOL_ASSET_CURRENCY_UNVERIFIED")
        if canonical_family(asset.get("type")) != family:
            raise IOLRestError("IOL_ASSET_FAMILY_UNVERIFIED")

        current = None
        if not exit_only:
            raw = self.rest.read_market_data("quote", *venue_key)
            received = _instant(self.clock())
            if not isinstance(raw, dict) or raw.get("currency") != currency:
                raise IOLRestError("IOL_QUOTE_CURRENCY_UNVERIFIED")
            source_time = self.trade_clock(raw) if self.trade_clock is not None else None
            if source_time is None:
                raise IOLRestError("IOL_LAST_TRADE_CLOCK_UNVERIFIED")
            current = _capture(raw, received=received, venue_key=venue_key,
                               currency=currency, source_time=source_time)
            if (received - _instant(current.provider_at)).total_seconds() > max_age_seconds:
                raise IOLRestError("IOL_LAST_TRADE_STALE")
            prices = raw.get("prices")
            last = _positive(prices.get("current") if isinstance(prices, dict) else None)

        depth = self.rest.read_market_data("depth", *venue_key)
        received = _instant(self.clock())
        book = _capture(depth, received=received, venue_key=venue_key, currency=currency,
                        source_time=depth.get("last_updated") if isinstance(depth, dict) else None)
        sides = depth.get("bids"), depth.get("asks")
        if not all(isinstance(levels, list) and levels and isinstance(levels[0], dict) for levels in sides):
            raise IOLRestError("IOL_BOOK_TWO_SIDED_UNVERIFIED")
        bid, ask = [_positive(levels[0].get("price")) for levels in sides]
        bid_size, ask_size = [_positive(levels[0].get("size")) for levels in sides]
        if bid > ask:
            raise IOLRestError("IOL_BOOK_CROSSED")
        quote = Quote(symbol, family, term, last if current else bid, bid, ask, bid_size, ask_size,
                      book.received_at, contract=contract, currency=currency, market=market,
                      metadata_source="IOL_REST", book_at=book.provider_at,
                      trade_at=current.provider_at if current else None,
                      last_kind="TRADE" if current else "REFERENCE",
                      opening_block_reason="IOL_EXIT_OBSERVATION_ONLY" if exit_only else "")
        quote.monetary_identity()
        error = quote.time_error(book.received_at, require_trade=not exit_only, max_age_seconds=max_age_seconds)
        if error:
            raise IOLRestError(error)
        return IOLObservation(quote, current, book)
