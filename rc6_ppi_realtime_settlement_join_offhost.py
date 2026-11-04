"""A652 OFFHOST PoC: PPI Search four-key + realtime emitted settlement.

Official REST SearchInstrument lists ticker/type/market/currency, without
settlement. The official Python realtime Book example emits Ticker/Type/
Settlement/Date. Joining those *literal* fields can form a five-key candidate,
but subscriptions already select a settlement; an echo is not independent
settlement discovery or proof that the requested tenor exists. No capture
authenticity, PPI/Watch socket, quota, PIT or PAPER authority is claimed here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

from bs_instrument_contracts import cash_currency, family_name
from rc6_multisource_discovery import canonical_market, canonical_settlement


SCHEMA = "rc6.a652.ppi-realtime-settlement-join-offhost.v1"
MAX_SEARCH_ROWS = 20_000
MAX_SEARCH_AGE_SECONDS = 86_400
MAX_STREAM_AGE_SECONDS = 120
SUPPORTED_TERMS = frozenset({"INMEDIATA", "A-24HS", "A-48HS", "A-72HS"})
SUPPORTED_MARKETS = frozenset({"BYMA", "A3", "NYSE", "OTC", "FCI"})
_TICKER = re.compile(r"[A-Z0-9][A-Z0-9./-]{0,63}\Z")


def _result(reason, state="NO_VERIFICADO", *, candidate=None,
            search_four_key=False, stream_settlement=False, bounded_clocks=False):
    return {
        "schema": SCHEMA, "state": state, "reason": reason,
        "candidate_five_key": candidate,
        "search_four_key_observed": search_four_key,
        "stream_settlement_field_observed": stream_settlement,
        "clock_pair_structurally_bounded": bounded_clocks,
        "settlement_subscription_preselected": True,
        "settlement_independently_discovered": False,
        "provider_session_authenticated": False,
        "source_capture_signature_verified": False,
        "provider_response_signed": False,
        "full_five_key_authoritative": False,
        "ppi_realtime_subscription_available_runtime": "NO_VERIFICADO",
        "ppi_watch_isolation_verified": False,
        "ppi_realtime_subscription_limit_verified": False,
        "pit_book1_decision_book2_verified": False,
        "economic_contract_verified": False,
        "paper_instrument_ready": False,
        "paper_fill_accredited": False,
        "runtime_real_orders_sent": "NO_VERIFICADO",
        "ppi_gets_by_guard": 0, "socket_connections_by_guard": 0,
        "host_writes_by_guard": 0,
    }


def _clock(raw):
    if not isinstance(raw, str) or not 1 <= len(raw) <= 40:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)
    except (OverflowError, TypeError, ValueError):
        return None


def _ticker(raw):
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().upper()
    return normalized if _TICKER.fullmatch(normalized) else None


def _kind(raw):
    if not isinstance(raw, str):
        return None
    try:
        return family_name(raw)
    except ValueError:
        return None


def _term(raw):
    if not isinstance(raw, str) or not raw.strip():
        return None
    term = canonical_settlement(raw)
    return term if term in SUPPORTED_TERMS else None


def _search_four_key(row):
    if not isinstance(row, dict):
        return None
    ticker, family = _ticker(row.get("ticker")), _kind(row.get("type"))
    market = canonical_market(row.get("market"))
    if not ticker or not family or market not in SUPPORTED_MARKETS:
        return None
    try:
        currency = cash_currency(row.get("currency"))
    except ValueError:
        return None
    return ticker, family, market, currency


def propose_five_key(search_rows, stream_book, subscription, *,
                     search_observed_at, stream_received_at, evaluated_at):
    """Shape-only join; every outcome is NON-ADMITTING regardless of input.

    `subscription` carries the caller's preselected ticker/type/settlement; it
    CANNOT become a PPI-emitted field or independently discovered settlement.
    All three clocks are OFFHOST caller claims, not signed source receipts.
    `stream_book` must be one unmodified provider Book-shaped event, not an
    interpolated quote or a user-selected later market price.
    """
    if (not isinstance(subscription, dict)
            or set(subscription) != {"ticker", "type", "settlement"}):
        return _result("SUBSCRIPTION_SELECTOR_UNTRUSTED", "BLOQUEADO")
    ticker = _ticker(subscription["ticker"])
    family = _kind(subscription["type"])
    term = _term(subscription["settlement"])
    if not ticker or not family or not term:
        return _result("SUBSCRIPTION_SELECTOR_INVALID", "BLOQUEADO")
    selector = (ticker, family, term)

    if not isinstance(stream_book, dict):
        return _result("REALTIME_BOOK_NOT_SUPPLIED")
    if (not isinstance(stream_book.get("Settlement"), str)
            or not isinstance(stream_book.get("Ticker"), str)
            or not isinstance(stream_book.get("Type"), str)):
        return _result("REALTIME_EMITTED_IDENTITY_INCOMPLETE", "BLOQUEADO")
    emitted = (_ticker(stream_book["Ticker"]), _kind(stream_book["Type"]),
               _term(stream_book["Settlement"]))
    if None in emitted or emitted != selector:
        return _result("REALTIME_SUBSCRIPTION_EMITTED_IDENTITY_CONFLICT", "BLOQUEADO",
                       stream_settlement=emitted[2] is not None)
    if (stream_book.get("Trade") is not False
            or not isinstance(stream_book.get("Bids"), list)
            or not isinstance(stream_book.get("Offers"), list)
            or len(stream_book["Bids"]) > 200 or len(stream_book["Offers"]) > 200):
        return _result("REALTIME_BOOK_ENVELOPE_UNVERIFIED", "BLOQUEADO",
                       stream_settlement=True)

    observed = _clock(search_observed_at)
    native = _clock(stream_book.get("Date"))
    received = _clock(stream_received_at)
    evaluated = _clock(evaluated_at)
    if None in (observed, native, received, evaluated):
        return _result("REALTIME_CLOCK_MISSING_OR_NAIVE", "BLOQUEADO",
                       stream_settlement=True)
    if (not observed <= native <= received <= evaluated
            or (native - observed).total_seconds() > MAX_SEARCH_AGE_SECONDS
            or (received - native).total_seconds() > MAX_STREAM_AGE_SECONDS
            or (evaluated - received).total_seconds() > MAX_STREAM_AGE_SECONDS):
        return _result("REALTIME_CLOCK_STALE_REVERSED_OR_FUTURE", "BLOQUEADO",
                       stream_settlement=True)

    if not isinstance(search_rows, list) or not 1 <= len(search_rows) <= MAX_SEARCH_ROWS:
        return _result("SEARCH_RESULT_MISSING_OR_UNBOUNDED", "BLOQUEADO",
                       stream_settlement=True, bounded_clocks=True)
    four_keys = set()
    for row in search_rows:
        if not isinstance(row, dict):
            return _result("SEARCH_RESULT_ROW_INVALID", "BLOQUEADO",
                           stream_settlement=True, bounded_clocks=True)
        # The streaming subscription disambiguates ticker and type, never
        # the market/currency that only SearchInstrument would have observed.
        if _ticker(row.get("ticker")) == ticker and _kind(row.get("type")) == family:
            key = _search_four_key(row)
            if key is None:
                return _result("SEARCH_MATCH_FOUR_KEY_INVALID", "BLOQUEADO",
                               stream_settlement=True, bounded_clocks=True)
            four_keys.add(key)
    if not four_keys:
        return _result("SEARCH_FOUR_KEY_NOT_FOUND", stream_settlement=True,
                       bounded_clocks=True)
    if len(four_keys) != 1:
        return _result("SEARCH_MARKET_OR_CURRENCY_SIBLING_AMBIGUOUS", "BLOQUEADO",
                       search_four_key=True, stream_settlement=True,
                       bounded_clocks=True)

    four = next(iter(four_keys))
    return _result("STRUCTURAL_JOIN_ONLY_SUBSCRIPTION_BOOTSTRAP_AND_SOURCE_UNVERIFIED",
                   "EVIDENCE_ONLY_NO_GO", candidate={
                       "ticker": four[0], "family": four[1],
                       "market": four[2], "currency": four[3],
                       "settlement": term,
                   }, search_four_key=True, stream_settlement=True,
                   bounded_clocks=True)
