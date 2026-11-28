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
from rc6_iol_trade_stream import TRADE_EVIDENCE, TRADE_HOLDS, TRADE_STATUS, matched_trade


MARKETS = {"BYMA": "BCBA", "NYSE": "NYSE", "NASDAQ": "NASDAQ"}
TERMS = {"INMEDIATA": "T0", "A-24HS": "T1"}
SPOT = frozenset({"ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES", "OPCIONES"})
OPTION_STREAM_TERM = "T1"
OPTION_PREMIUM_IDENTITY = ("OPCIONES", "BYMA", "ARS", "INMEDIATA")


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


def read_iol_market_payload(rest, endpoint, identity):
    """Issue exactly the requested kind of market read with the existing REST client."""
    symbol, _, market, _, term = identity
    venue = MARKETS[market]
    if endpoint == "asset":
        return rest.call("get_asset_info", {"symbol": symbol, "market": venue})
    return rest.read_market_data(endpoint, symbol, venue, TERMS[term])


def iol_trade_stream_key(identity):
    """Return the IOL subscription key for the exact economic identity.

    IOL documents options under T1 in its WebSocket API. BYMA settles the
    premium immediately and the exercise in T+1. The subscription term does
    not alter either economic leg or authorize a REST term substitution.
    """
    symbol, family, market, _, term = identity
    if family == "OPCIONES":
        if tuple(identity[1:]) != OPTION_PREMIUM_IDENTITY:
            raise IOLRestError("IOL_MARKET_IDENTITY_UNSUPPORTED")
        return symbol, MARKETS[market], OPTION_STREAM_TERM
    return symbol, MARKETS[market], TERMS[term]


def iol_option_trade_binding(identity, evidence):
    return (
        tuple(identity[1:]) == OPTION_PREMIUM_IDENTITY
        and isinstance(evidence, dict)
        and evidence.get("identity") == list(identity)
        and evidence.get("transport_key") == list(iol_trade_stream_key(identity))
    )


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


def _best_level(levels, *, bid):
    if not isinstance(levels, list) or not levels or any(not isinstance(level, dict) for level in levels):
        raise IOLRestError("IOL_BOOK_TWO_SIDED_UNVERIFIED")
    levels = [(_positive(level.get("price")), _positive(level.get("size"))) for level in levels]
    pick = max if bid else min
    price, size = pick(levels, key=lambda level: level[0])
    if sum(level[0] == price for level in levels) != 1:
        raise IOLRestError("IOL_BOOK_BEST_LEVEL_AMBIGUOUS")
    return price, size


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

    def __init__(self, rest, *, trade_clock=None, clock=None, capture=None, gateway_evidence=False):
        self.rest = rest
        self.trade_clock = trade_clock
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.capture = capture
        self.gateway_evidence = gateway_evidence

    def _read(self, endpoint, identity):
        if self.capture is not None:
            payload, received_at = self.capture(endpoint, identity)
        else:
            payload = read_iol_market_payload(self.rest, endpoint, identity)
            received_at = self.clock()
        return payload, _instant(received_at)

    def observe(self, identity, *, contract=None, exit_only=False, max_age_seconds=120):
        symbol, family, market, currency, term = identity
        if family not in SPOT or market not in MARKETS or term not in TERMS:
            raise IOLRestError("IOL_MARKET_IDENTITY_UNSUPPORTED")
        if family == "OPCIONES" and tuple(identity[1:]) != OPTION_PREMIUM_IDENTITY:
            raise IOLRestError("IOL_MARKET_IDENTITY_UNSUPPORTED")
        if contract is not None and contract.key != tuple(identity):
            raise IOLRestError("IOL_EXACT_CONTRACT_MISMATCH")
        venue_key = symbol, MARKETS[market], TERMS[term]
        # The depth response can omit currency. Bind it to an independent exact
        # asset response as well as to the symbol/market/term depth response.
        asset, _ = self._read("asset", identity)
        if (not isinstance(asset, dict)
                or (asset.get("symbol"), asset.get("market")) != venue_key[:2]):
            raise IOLRestError("IOL_ASSET_IDENTITY_MISMATCH")
        if asset.get("currency") != currency:
            raise IOLRestError("IOL_ASSET_CURRENCY_UNVERIFIED")
        if canonical_family(asset.get("type")) != family:
            raise IOLRestError("IOL_ASSET_FAMILY_UNVERIFIED")

        current = None
        evidence = None
        if not exit_only:
            raw, received = self._read("quote", identity)
            if not isinstance(raw, dict) or raw.get("currency") != currency:
                raise IOLRestError("IOL_QUOTE_CURRENCY_UNVERIFIED")
            evidence = raw.get(TRADE_EVIDENCE) if self.gateway_evidence else None
            source_time = matched_trade(evidence, raw, received)
            if family == "OPCIONES" and evidence is not None and not iol_option_trade_binding(identity, evidence):
                raise IOLRestError("IOL_OPTION_STREAM_TRANSPORT_UNVERIFIED")
            if source_time is None and self.trade_clock is not None and family != "OPCIONES":
                source_time = self.trade_clock(raw)
                evidence = None
            if source_time is None:
                hold = raw.get(TRADE_STATUS) if self.gateway_evidence else None
                raise IOLRestError(hold if isinstance(hold, str) and hold in TRADE_HOLDS
                                   else "IOL_LAST_TRADE_CLOCK_UNVERIFIED")
            current = _capture(raw, received=received, venue_key=venue_key,
                               currency=currency, source_time=source_time)
            if (received - _instant(current.provider_at)).total_seconds() > max_age_seconds:
                raise IOLRestError("IOL_LAST_TRADE_STALE")
            prices = raw.get("prices")
            last = _positive(prices.get("current") if isinstance(prices, dict) else None)

        depth, received = self._read("depth", identity)
        book = _capture(depth, received=received, venue_key=venue_key, currency=currency,
                        source_time=depth.get("last_updated") if isinstance(depth, dict) else None)
        bid, bid_size = _best_level(depth.get("bids"), bid=True)
        ask, ask_size = _best_level(depth.get("asks"), bid=False)
        if bid > ask:
            raise IOLRestError("IOL_BOOK_CROSSED")
        quote = Quote(symbol, family, term, last if current else bid, bid, ask, bid_size, ask_size,
                      book.received_at, contract=contract, currency=currency, market=market,
                      metadata_source="IOL_REST", book_at=book.provider_at,
                      trade_at=current.provider_at if current else None,
                      last_kind="TRADE" if current else "REFERENCE",
                      opening_block_reason="IOL_EXIT_OBSERVATION_ONLY" if exit_only else "",
                      iol_trade_evidence=evidence)
        quote.monetary_identity()
        error = quote.time_error(_instant(self.clock()).isoformat(), require_trade=not exit_only,
                                 max_age_seconds=max_age_seconds)
        if error:
            raise IOLRestError(error)
        return IOLObservation(quote, current, book)
