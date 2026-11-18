"""RC6 A678: bounded, OFFHOST-testable PPI market-data fan-out contract.

This module NEVER opens a PPI connection, imports the broker SDK, logs in,
starts a thread, or sends an order.  It is an *inert* data-plane primitive for
a future single acquisition owner.  The existing productive scanner, EXIT
reader and external PPI Watch are NOT rewired merely by importing it.

An actual PPI source must be independently authenticated and its catalog
attested upstream.  A hash of synthetic rows is NOT a provider signature:
all events retain ``ppi_catalog_authority=NO_VERIFICADO`` and
``paper_ready=False`` regardless of successful structural validation.

The official PPI Python SDK documents MarketData live subscription messages
with Ticker/Type/Settlement/Date/Trade and Bids/Offers; SearchInstrument
supplies ticker/type/market/currency.  These contracts do not establish a
broker rate limit, live account entitlement, PIT signer or Book2 witness.

See https://itatppi.github.io/ppi-official-api-docs/api/documentacionPython/
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import stat
import threading
import time
from typing import Any


SCHEMA = "rc6.ppi.shared-market-feed.offhost.v1"
_TEXT = re.compile(r"[A-Z0-9][A-Z0-9./_-]{0,63}\Z")
_CONSUMER = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PRIORITY = {
    "EXIT_CRITICAL": 0,
    "WATCH_CRITICAL": 1,
    "OPENED_CRITICAL": 1,
    "HOT": 2,
    "WARM": 3,
    "DISCOVERY": 4,
}
_MAX_LEVELS = 40
NATIVE_READ_SCHEMA = "rc6.ppi.native-read-receipts.offhost.v1"
_NATIVE_REST_METHODS = frozenset(("current", "book", "intraday"))
_NATIVE_UNBUDGETED_METHODS = frozenset(
    ("history", "search_instruments", "market_configuration"))
_NATIVE_CONSUMER_METHODS = {
    "scanner": frozenset(("current", "book")),
    "exit_reader": frozenset(("book",)),
    "scalping": frozenset(("intraday",)),
}
_UNSAFE_NATIVE_FIELD_PARTS = frozenset((
    "account", "order", "portfolio", "password", "secret", "token",
    "authorization", "credential", "balance", "holdings", "person",
    "private", "api_key", "api_secret", "client_id", "user_id",
))


class CatalogIdentityError(ValueError):
    """Broker five-key cannot be determined from PPI catalog + event."""


class EventRejected(ValueError):
    """Malformed, stale, conflicted or causally invalid source event."""


class SharedFeedUnavailable(ConnectionError):
    """Producer unavailable; consumers MUST pause rather than use stale data."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _value(raw: Any, field: str) -> str:
    if not isinstance(raw, str):
        raise CatalogIdentityError(f"PPI_IDENTITY_{field}_INVALID")
    value = raw.strip().upper()
    if not _TEXT.fullmatch(value):
        raise CatalogIdentityError(f"PPI_IDENTITY_{field}_INVALID")
    return value


def _five(value: Any) -> tuple[str, str, str, str, str]:
    if not isinstance(value, (tuple, list)) or len(value) != 5:
        raise CatalogIdentityError("PPI_FIVE_KEY_IDENTITY_INVALID")
    return tuple(_value(item, "COMPONENT") for item in value)


def _time(raw: Any, *, reason: str) -> datetime:
    if isinstance(raw, datetime):
        value = raw
    elif isinstance(raw, str) and len(raw) <= 60:
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise EventRejected(f"PPI_{reason}_INVALID") from exc
    else:
        raise EventRejected(f"PPI_{reason}_INVALID")
    if value.tzinfo is None or value.utcoffset() is None:
        raise EventRejected(f"PPI_{reason}_NAIVE")
    return value.astimezone(timezone.utc)


def _number(raw: Any, *, name: str, positive: bool) -> str:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str, Decimal)):
        raise EventRejected(f"PPI_{name}_INVALID")
    try:
        numeric = Decimal(str(raw))
    except InvalidOperation as exc:
        raise EventRejected(f"PPI_{name}_INVALID") from exc
    if not numeric.is_finite() or numeric > Decimal("1000000000000000") or (
            numeric <= 0 if positive else numeric < 0):
        raise EventRejected(f"PPI_{name}_INVALID")
    return str(numeric)


def _levels(raw: Any, *, side: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) > _MAX_LEVELS:
        raise EventRejected("PPI_BOOK_DEPTH_UNBOUNDED")
    levels = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise EventRejected("PPI_BOOK_LEVEL_INVALID")
        position = item.get("Position", index)
        if isinstance(position, bool) or not isinstance(position, int) or position < 0:
            raise EventRejected("PPI_BOOK_POSITION_INVALID")
        levels.append({
            "position": position,
            "price": _number(item.get("Price"), name=f"{side}_PRICE", positive=True),
            "quantity": _number(item.get("Quantity"), name=f"{side}_QTY", positive=False),
        })
    return levels


class PPISharedMarketFeed:
    """One in-memory producer, independent bounded consumer read cursors.

    No auto network, no SDK access, no SQLite, no runtime timer.  This class
    deliberately does not create a 'signed' or 'READY' flag from local input.
    PPI catalog rows are structural *candidates* until a separately pinned
    genuine capture and signer attests their source.
    """

    def __init__(self, *, ppi_catalog_rows: list[dict[str, Any]],
                 catalog_source_digest: str, max_events: int = 128,
                 max_subscriptions: int = 60, max_age_seconds: float = 5):
        if not _SHA256.fullmatch(str(catalog_source_digest)):
            raise CatalogIdentityError("PPI_CATALOG_SOURCE_DIGEST_INVALID")
        if not isinstance(ppi_catalog_rows, list) or not 1 <= len(ppi_catalog_rows) <= 20_000:
            raise CatalogIdentityError("PPI_CATALOG_ROWS_MISSING_OR_UNBOUNDED")
        if any(not isinstance(v, int) or isinstance(v, bool) for v in
               (max_events, max_subscriptions)) or not 1 <= max_events <= 4096 or not 1 <= max_subscriptions <= 512:
            raise ValueError("PPI_SHARED_FEED_CAPACITY_INVALID")
        if not isinstance(max_age_seconds, (int, float)) or not 0 < max_age_seconds <= 120:
            raise ValueError("PPI_SHARED_FEED_AGE_INVALID")
        self._catalog: dict[tuple[str, str], set[tuple[str, str]]] = {}
        for row in ppi_catalog_rows:
            if not isinstance(row, dict):
                raise CatalogIdentityError("PPI_CATALOG_ROW_INVALID")
            ticker = _value(row.get("ticker"), "TICKER")
            family = _value(row.get("type"), "TYPE")
            market = _value(row.get("market"), "MARKET")
            currency = _value(row.get("currency"), "CURRENCY")
            self._catalog.setdefault((ticker, family), set()).add((market, currency))
        self.catalog_source_digest = catalog_source_digest
        self.max_events = max_events
        self.max_subscriptions = max_subscriptions
        self.max_age_seconds = max_age_seconds
        self._subscriptions: dict[tuple[str, ...], str] = {}
        self._consumers: dict[str, set[tuple[str, ...]] | None] = {}
        self._events: deque[tuple[int, bytes]] = deque(maxlen=max_events)
        self._source_time: dict[tuple[tuple[str, ...], str], tuple[datetime, str, int]] = {}
        self._quarantined: set[tuple[tuple[str, ...], str]] = set()
        # Only the last event in the retained ring can be returned as a
        # duplicate.  Older events are rejected by _source_time, not cached.
        # Memory stays O(max_events + max_subscriptions), never O(runtime).
        self._published_by_source: dict[tuple[tuple[str, ...], str, str], dict] = {}
        self._last_sequence = 0
        self._dropped = 0
        self._lock = threading.RLock()

    def register_consumer(self, name: str, *, watched_five_keys=None) -> None:
        if not isinstance(name, str) or not _CONSUMER.fullmatch(name):
            raise ValueError("PPI_CONSUMER_ID_INVALID")
        keys = None if watched_five_keys is None else {_five(key) for key in watched_five_keys}
        with self._lock:
            if name in self._consumers:
                raise ValueError("PPI_CONSUMER_ALREADY_REGISTERED")
            if len(self._consumers) >= 64:
                raise ValueError("PPI_CONSUMER_COUNT_UNBOUNDED")
            self._consumers[name] = keys

    def plan_subscription(self, five_key, *, priority: str) -> str:
        """Bounded dynamic focus; 9500 catalog symbols are NEVER auto-subscribed.

        A lower priority cannot evict a reserved critical/EXIT subscription.
        The future acquisition owner may use this plan to choose actual SDK
        subscriptions, but calling it never touches a broker connection.
        """
        identity = _five(five_key)
        if priority not in _PRIORITY:
            raise ValueError("PPI_SUBSCRIPTION_PRIORITY_INVALID")
        with self._lock:
            if identity in self._subscriptions:
                if _PRIORITY[priority] < _PRIORITY[self._subscriptions[identity]]:
                    self._subscriptions[identity] = priority
                return "ADMITTED"
            # The preflight may reserve a new five-key even when a catalog is
            # incomplete, but no native event is admitted unless the catalog
            # resolves its Market/Currency unambiguously.
            if len(self._subscriptions) == self.max_subscriptions:
                worst = max(self._subscriptions, key=lambda key:
                            (_PRIORITY[self._subscriptions[key]], key))
                if _PRIORITY[priority] >= _PRIORITY[self._subscriptions[worst]]:
                    return "CAPACITY_HOLD"
                self._subscriptions.pop(worst)
            self._subscriptions[identity] = priority
            return "ADMITTED"

    def active_subscriptions(self) -> list[list[str]]:
        with self._lock:
            return [list(key) for key in sorted(self._subscriptions,
                    key=lambda key: (_PRIORITY[self._subscriptions[key]], key))]

    def _identity(self, raw: dict) -> tuple[tuple[str, str, str, str, str], str]:
        try:
            ticker = _value(raw.get("Ticker"), "TICKER")
            settlement = _value(raw.get("Settlement"), "SETTLEMENT")
        except CatalogIdentityError as exc:
            raise CatalogIdentityError("PPI_STREAM_IDENTITY_INCOMPLETE") from exc
        if "Type" in raw:
            family = _value(raw.get("Type"), "TYPE")
            type_provenance = "NATIVE_EVENT"
        else:
            # The official SDK example can omit Type in an event, even though
            # subscriptions are typed.  Resolve ONLY when exactly ONE active
            # five-key subscription matches the two native context fields.
            # This is not native PPI proof and never authorizes PAPER.
            with self._lock:
                matching = [key for key in self._subscriptions
                            if key[0] == ticker and key[4] == settlement]
            if len(matching) != 1:
                raise CatalogIdentityError(
                    "PPI_STREAM_TYPE_SUBSCRIPTION_CONTEXT_NOT_UNIQUE")
            family = matching[0][1]
            type_provenance = "UNIQUE_ACTIVE_SUBSCRIPTION_NOT_NATIVE"
        four = self._catalog.get((ticker, family))
        if not four:
            raise CatalogIdentityError("PPI_STREAM_IDENTITY_NOT_IN_PPI_CATALOG")
        if len(four) != 1:
            raise CatalogIdentityError("PPI_CATALOG_IDENTITY_AMBIGUOUS_MARKET_CURRENCY")
        market, currency = next(iter(four))
        five = (ticker, family, market, currency, settlement)
        with self._lock:
            if type_provenance != "NATIVE_EVENT":
                active = [key for key in self._subscriptions
                          if key[0] == ticker and key[4] == settlement]
                if active != [five]:
                    raise CatalogIdentityError(
                        "PPI_STREAM_TYPE_SUBSCRIPTION_CONTEXT_NOT_UNIQUE")
            if five not in self._subscriptions:
                raise CatalogIdentityError("PPI_STREAM_IDENTITY_NOT_SUBSCRIBED")
        for native_name, selected in (("Market", market), ("Currency", currency)):
            if native_name in raw and _value(raw[native_name], native_name) != selected:
                raise CatalogIdentityError(f"PPI_STREAM_NATIVE_{native_name.upper()}_CONFLICT")
        return five, type_provenance

    def publish_native_market_event(self, raw: dict, *, received_at,
                                    received_monotonic_ns: int,
                                    session_fingerprint: str | None = None) -> dict:
        """Publish *one* PPI-shaped Book or trade after strict sanitization.

        Input material can be synthetic; this method never treats a local hash
        as an independently authenticated provider receipt.
        """
        if not isinstance(raw, dict) or raw.get("Trade") not in (True, False) or not isinstance(raw.get("Trade"), bool):
            raise EventRejected("PPI_NATIVE_MARKET_EVENT_SCHEMA_INVALID")
        identity, type_provenance = self._identity(raw)
        event_at = _time(raw.get("Date"), reason="EVENT_TIME")
        arrival = _time(received_at, reason="RECEIVE_TIME")
        lag = (arrival - event_at).total_seconds()
        if not 0 <= lag <= self.max_age_seconds:
            raise EventRejected("PPI_EVENT_STALE_FUTURE_OR_INVERTED")
        if isinstance(received_monotonic_ns, bool) or not isinstance(received_monotonic_ns, int) or received_monotonic_ns <= 0:
            raise EventRejected("PPI_RECEIVE_MONOTONIC_INVALID")
        if (session_fingerprint is not None
            and not _SHA256.fullmatch(str(session_fingerprint))):
            raise EventRejected("PPI_SESSION_FINGERPRINT_INVALID")
        kind = "TRADE" if raw["Trade"] else "BOOK"
        if kind == "BOOK":
            payload = {
                "bids": _levels(raw.get("Bids"), side="BID"),
                "offers": _levels(raw.get("Offers"), side="OFFER"),
            }
        else:
            payload = {
                "price": _number(raw.get("Price"), name="TRADE_PRICE", positive=True),
                "volume": _number(raw.get("VolumeAmount", 0), name="TRADE_VOLUME", positive=False),
            }
        normalized_source = {
            "identity": list(identity), "native_event_time_utc": event_at.isoformat(),
            "kind": kind, "market_payload": payload,
            "type_provenance": type_provenance,
            "session_fingerprint": session_fingerprint,
        }
        source_sha = _sha(normalized_source)
        scope = (identity, kind)
        exact_event = (identity, kind, source_sha)
        with self._lock:
            if scope in self._quarantined:
                raise EventRejected("PPI_MARKET_EVENT_SOURCE_QUARANTINED")
            duplicate = self._published_by_source.get(exact_event)
            if duplicate is not None:
                return json.loads(_canonical_bytes(duplicate))
            previous = self._source_time.get(scope)
            if previous is not None and event_at <= previous[0]:
                if event_at == previous[0] and source_sha != previous[1]:
                    self._quarantined.add(scope)
                    raise EventRejected("PPI_MARKET_EVENT_TIMESTAMP_CONFLICT_QUARANTINED")
                raise EventRejected("PPI_MARKET_EVENT_OUT_OF_ORDER")
            if previous is not None and received_monotonic_ns <= previous[2]:
                raise EventRejected("PPI_MARKET_EVENT_RECEIVE_CLOCK_NOT_MONOTONIC")
            sequence = self._last_sequence + 1
            event = {
                "schema": SCHEMA, "sequence": sequence,
                "ppi_five_key": list(identity), "ppi_catalog_source_digest": self.catalog_source_digest,
                "ppi_catalog_authority": "NO_VERIFICADO",  # local hash != PPI signature
                "type_provenance": type_provenance,
                "native_event_time_utc": event_at.isoformat(),
                "receive_time_utc": arrival.isoformat(),
                "receive_monotonic_ns": received_monotonic_ns,
                "session_fingerprint": session_fingerprint,
                "kind": kind, "market_payload": payload,
                "source_normalized_sha256": source_sha,
                "paper_ready": False, "order_route": "NOT_PRESENT",
            }
            event["event_sha256"] = _sha(event)
            encoded = _canonical_bytes(event)
            if len(self._events) >= self.max_events:
                self._dropped += 1
                # Do not retain a second copy of an evicted market event.
                _, evicted = self._events[0]
                evicted_event = json.loads(evicted)
                evicted_key = (tuple(evicted_event["ppi_five_key"]),
                               evicted_event["kind"],
                               evicted_event["source_normalized_sha256"])
                self._published_by_source.pop(evicted_key, None)
            self._events.append((sequence, encoded))
            self._last_sequence = sequence
            self._source_time[scope] = (event_at, source_sha,
                                        received_monotonic_ns)
            self._published_by_source[exact_event] = event
            return json.loads(encoded)

    def pull(self, consumer: str, *, after_sequence: int, limit: int = 32) -> dict:
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
            raise ValueError("PPI_CONSUMER_CURSOR_INVALID")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 128:
            raise ValueError("PPI_CONSUMER_BATCH_INVALID")
        with self._lock:
            if consumer not in self._consumers:
                raise ValueError("PPI_CONSUMER_UNREGISTERED")
            oldest = self._events[0][0] if self._events else self._last_sequence + 1
            if after_sequence < oldest - 1:
                return {"schema": SCHEMA, "state": "GAP", "events": [],
                        "oldest_sequence": oldest, "producer_cursor": self._last_sequence}
            allowed = self._consumers[consumer]
            events = []
            for sequence, encoded in self._events:
                if sequence <= after_sequence:
                    continue
                obj = json.loads(encoded)
                if allowed is None or tuple(obj["ppi_five_key"]) in allowed:
                    events.append(obj)
                    if len(events) >= limit:
                        break
            return {"schema": SCHEMA, "state": "OK", "events": events,
                    "oldest_sequence": oldest, "producer_cursor": self._last_sequence}

    def get_post_commit_book(self, five_key, *, book1_receipt_sha256: str,
                             commit_at, commit_monotonic_ns: int) -> dict:
        """Structural Book2 only; never treats a cached Book1 as a Book2.

        This does not sign/authorize a PAPER decision.  A separate pinned PIT
        signer must attest COMMIT, author, product SHA, Book1 and Book2.
        """
        identity = _five(five_key)
        if not _SHA256.fullmatch(str(book1_receipt_sha256)):
            raise EventRejected("PPI_PIT_BOOK1_RECEIPT_INVALID")
        committed = _time(commit_at, reason="COMMIT_TIME")
        if isinstance(commit_monotonic_ns, bool) or not isinstance(commit_monotonic_ns, int) or commit_monotonic_ns <= 0:
            raise EventRejected("PPI_PIT_COMMIT_MONOTONIC_INVALID")
        with self._lock:
            if (identity, "BOOK") in self._quarantined:
                raise EventRejected("PPI_PIT_BOOK_SOURCE_QUARANTINED")
            book1 = None
            candidates = []
            for sequence, encoded in self._events:
                event = json.loads(encoded)
                if tuple(event["ppi_five_key"]) != identity or event["kind"] != "BOOK":
                    continue
                if event["event_sha256"] == book1_receipt_sha256:
                    book1 = event
                elif (_time(event["native_event_time_utc"], reason="EVENT_TIME") > committed
                      and event["receive_monotonic_ns"] > commit_monotonic_ns):
                    candidates.append(event)
            if book1 is None:
                raise EventRejected("PPI_PIT_POST_COMMIT_BOOK1_NOT_RETAINED")
            if not _SHA256.fullmatch(str(book1.get("session_fingerprint"))):
                raise EventRejected("PPI_PIT_SESSION_NOT_PROVEN")
            if (_time(book1["native_event_time_utc"], reason="EVENT_TIME") > committed
                or book1["receive_monotonic_ns"] >= commit_monotonic_ns):
                raise EventRejected("PPI_PIT_BOOK1_NOT_PRE_COMMIT")
            candidates = [event for event in candidates if event["sequence"] > book1["sequence"]]
            if not candidates:
                raise EventRejected("PPI_PIT_POST_COMMIT_BOOK2_NOT_OBSERVED")
            # FIRST eligible Book2.  Returning the last update would let the
            # paper decision cherry-pick a later market outcome (lookahead).
            if candidates[0].get("session_fingerprint") != book1["session_fingerprint"]:
                raise EventRejected("PPI_PIT_BOOK2_SESSION_CHANGED")
            return candidates[0]

    def stats(self) -> dict:
        with self._lock:
            return {"schema": SCHEMA, "published": self._last_sequence,
                    "dropped_from_ring": self._dropped,
                    "quarantined_sources": len(self._quarantined),
                    "retained_source_events": len(self._published_by_source),
                    "subscribed": len(self._subscriptions),
                    "consumers": sorted(self._consumers),
                    "provider_calls_by_bus": 0,
                    "paper_authority": "NO_VERIFICADO"}


class PPIReadOnlyNativeReceiptStore:
    """Bounded native REST evidence cache, not an alternative PPI GET path.

    An upstream single, guarded acquisition owner must first obtain a genuine
    REST response and publish its original JSON payload with the independent
    native/receive clocks, five-key catalog context, and send-budget receipt.
    This class neither imports PPI nor calls the provider or SQLite.  A caller
    may never cause provider I/O by requesting an old or missing receipt.

    Provider origin, budget receipt signature and catalog authority are NOT
    verified by constructing a local digest; every returned receipt remains
    OBSERVE_ONLY / paper_ready=False until independent signed evidence exists.
    """

    def __init__(self, feed: PPISharedMarketFeed, *,
                 max_receipts: int = 64, max_age_seconds: float = 5,
                 clock=None, monotonic_ns=None):
        if not isinstance(feed, PPISharedMarketFeed):
            raise TypeError("PPI_NATIVE_FEED_REQUIRED")
        if (type(max_receipts) is not int or not 1 <= max_receipts <= 256
                or type(max_age_seconds) not in (int, float)
                or not 0 < max_age_seconds <= 120):
            raise ValueError("PPI_NATIVE_CACHE_LIMIT_INVALID")
        self.feed = feed
        self.max_receipts = max_receipts
        self.max_age_seconds = max_age_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic_ns = monotonic_ns or time.monotonic_ns
        self._receipts: deque[dict] = deque(maxlen=max_receipts)
        self._latest_native: dict[tuple[tuple[str, ...], str], datetime] = {}
        self._lock = threading.RLock()
        self._dropped = 0

    def _registered_identity(self, five_key) -> tuple[str, ...]:
        identity = _five(five_key)
        with self.feed._lock:
            source = self.feed._catalog.get((identity[0], identity[1]))
            if source is None or len(source) != 1 or next(iter(source)) != (identity[2], identity[3]):
                raise CatalogIdentityError("PPI_NATIVE_CATALOG_FIVE_KEY_NOT_UNIQUE")
        return identity

    @staticmethod
    def _native_raw(raw_payload: Any, *, method: str):
        # REST marketdata shapes are NOT the uppercase realtime callback
        # shapes. PPI's official Python examples document:
        #   current:  {date,price,volume,openingPrice,max,min}
        #   book:     {date,bids:[{position,price,quantity}],offers:[...]}
        #   intraday: [{date,price,volume},...]
        # Never convert or relabel these as stream events or a candle close.
        if (method == "intraday" and
                (not isinstance(raw_payload, list) or not 1 <= len(raw_payload) <= 512)):
            raise EventRejected("PPI_NATIVE_PAYLOAD_INVALID")
        if method != "intraday" and not isinstance(raw_payload, dict):
            raise EventRejected("PPI_NATIVE_PAYLOAD_INVALID")
        # This is market-data ONLY.  Refuse anything resembling account,
        # order, balance or credential data even when upstream makes a mistake.
        pending = [(raw_payload, 0)]
        count = 0
        while pending:
            node, depth = pending.pop()
            count += 1
            # Up to 512 intraday rows with three scalar fields each still
            # have fewer than 4096 bounded nodes; 64KiB is a second cap.
            if count > 4096 or depth > 6:
                raise EventRejected("PPI_NATIVE_PAYLOAD_UNBOUNDED")
            if isinstance(node, dict):
                for key, value in node.items():
                    if type(key) is not str or not 1 <= len(key) <= 64:
                        raise EventRejected("PPI_NATIVE_PAYLOAD_UNSAFE")
                    name = key.strip().lower()
                    if any(part in name for part in _UNSAFE_NATIVE_FIELD_PARTS):
                        raise EventRejected("PPI_NATIVE_PAYLOAD_UNSAFE")
                    pending.append((value, depth + 1))
            elif isinstance(node, list):
                if len(node) > (512 if method == "intraday" and node is raw_payload else 256):
                    raise EventRejected("PPI_NATIVE_PAYLOAD_UNBOUNDED")
                pending.extend((value, depth + 1) for value in node)
            elif node is not None and type(node) not in (str, bool, int, float):
                raise EventRejected("PPI_NATIVE_PAYLOAD_UNSAFE")
            elif type(node) is str and len(node) > 2048:
                raise EventRejected("PPI_NATIVE_PAYLOAD_UNBOUNDED")
        try:
            encoded = _canonical_bytes(raw_payload)
        except (TypeError, ValueError, OverflowError, RecursionError) as error:
            raise EventRejected("PPI_NATIVE_PAYLOAD_UNSAFE") from error
        if len(encoded) > 65536:
            raise EventRejected("PPI_NATIVE_PAYLOAD_UNBOUNDED")
        if method == "book":
            for side in ("bids", "offers"):
                levels = raw_payload.get(side)
                if not isinstance(levels, list) or len(levels) > _MAX_LEVELS:
                    raise EventRejected("PPI_NATIVE_REST_BOOK_LEVELS_INVALID")
                for level in levels:
                    if not isinstance(level, dict):
                        raise EventRejected("PPI_NATIVE_REST_BOOK_LEVEL_INVALID")
                    if (type(level.get("position")) is not int
                            or level["position"] < 0):
                        raise EventRejected("PPI_NATIVE_REST_BOOK_POSITION_INVALID")
                    # Official documentation renders zero-valued placeholders.
                    # Preserve native evidence; PAPER tradability still needs
                    # separate strictly positive executable-book validation.
                    _number(level.get("price"), name="REST_BOOK_PRICE", positive=False)
                    _number(level.get("quantity"), name="REST_BOOK_QTY", positive=False)
        elif method == "current":
            # Mandatory fields from official Current REST example, not a
            # permissive Book/Trade object masquerading as Current.
            if not set(("date", "price", "volume", "openingPrice", "max", "min")).issubset(raw_payload):
                raise EventRejected("PPI_NATIVE_REST_CURRENT_FIELDS_MISSING")
            for name in ("price", "openingPrice", "max", "min"):
                _number(raw_payload[name], name=f"REST_CURRENT_{name}", positive=False)
            _number(raw_payload["volume"], name="REST_CURRENT_VOLUME", positive=False)
        elif method == "intraday":
            for row in raw_payload:
                if not isinstance(row, dict) or not set(("date", "price", "volume")).issubset(row):
                    raise EventRejected("PPI_NATIVE_REST_INTRADAY_ROW_INVALID")
                _number(row["price"], name="REST_INTRADAY_PRICE", positive=False)
                _number(row["volume"], name="REST_INTRADAY_VOLUME", positive=False)
        return json.loads(encoded)

    @staticmethod
    def _raw_native_time(raw, *, method: str) -> tuple[datetime, str]:
        # Intraday may contain many historic rows in its native sequence;
        # the receipt time is the maximum native row date, NOT a claim that
        # any bar is closed or that the provider sorted those rows.
        if method == "intraday":
            times = [_time(row.get("date"), reason="REST_INTRADAY_DATE")
                     for row in raw]
            return max(times), "MAX_NATIVE_ROW_DATE"
        return _time(raw.get("date"), reason="REST_NATIVE_DATE"), "REST_NATIVE_DATE"

    def publish_native_rest_receipt(self, *, method, five_key, raw_payload,
                                    native_at, received_at,
                                    received_monotonic_ns,
                                    session_fingerprint,
                                    guard_budget_receipt_sha256,
                                    capture_type):
        """Internal producer seam; no socket operation can invoke this."""
        if method not in _NATIVE_REST_METHODS or capture_type != "REST_" + str(method).upper():
            raise EventRejected("PPI_NATIVE_CAPTURE_TYPE_INVALID")
        identity = self._registered_identity(five_key)
        if (not _SHA256.fullmatch(str(session_fingerprint))
                or not _SHA256.fullmatch(str(guard_budget_receipt_sha256))):
            raise EventRejected("PPI_NATIVE_BUDGET_RECEIPT_INVALID")
        if (type(received_monotonic_ns) is not int
                or received_monotonic_ns <= 0
                or received_monotonic_ns > self.monotonic_ns()):
            raise EventRejected("PPI_NATIVE_RECEIVE_MONOTONIC_INVALID")
        native_time = _time(native_at, reason="NATIVE_TIME")
        recv_time = _time(received_at, reason="NATIVE_RECEIVE_TIME")
        now_time = _time(self.clock(), reason="NATIVE_CLOCK")
        lag = (recv_time - native_time).total_seconds()
        if (not 0 <= lag <= self.max_age_seconds
                or recv_time > now_time):
            raise EventRejected("PPI_NATIVE_RECEIPT_CLOCK_INVALID")
        raw = self._native_raw(raw_payload, method=method)
        # Source date must be from the exact REST raw body, never a caller-
        # manufactured stream Date or a postdecision/current clock.
        raw_time, time_basis = self._raw_native_time(raw, method=method)
        if raw_time != native_time:
            raise EventRejected("PPI_NATIVE_CLOCK_NOT_IN_RAW_RECEIPT")
        # The REST response examples do not contain instrument identity:
        # five-key is bound to the guarded SDK request and PPI catalog, NOT
        # fabricated as fields supposedly signed inside the native payload.
        if isinstance(raw, dict):
            for field, part in (("ticker", 0), ("type", 1), ("market", 2),
                                ("currency", 3), ("settlement", 4)):
                if field in raw and _value(raw[field], field) != identity[part]:
                    raise CatalogIdentityError(f"PPI_NATIVE_{field.upper()}_CONFLICT")
        receipt = {
            "schema": NATIVE_READ_SCHEMA, "method": method,
            "capture_type": capture_type, "source": "PPI",
            "identity_provenance": "GUARDED_REQUEST_CONTEXT_NOT_NATIVE_PAYLOAD",
            "native_time_basis": time_basis,
            "ppi_five_key": list(identity),
            "ppi_catalog_source_digest": self.feed.catalog_source_digest,
            "ppi_catalog_authority": "NO_VERIFICADO",
            "native_at_utc": native_time.isoformat(),
            "received_at_utc": recv_time.isoformat(),
            "receive_monotonic_ns": received_monotonic_ns,
            "session_fingerprint": session_fingerprint,
            "guard_budget_receipt_sha256": guard_budget_receipt_sha256,
            # The read-only HTTP guard's per-SEND budget receipt has NOT been
            # independently exposed to this G2 cache. A 64-hex string is
            # structural input only, never proof of a budgeted provider call.
            "guard_budget_receipt_authority": "NO_VERIFICADO",
            "raw_payload": raw, "raw_sha256": _sha(raw),
            "paper_ready": False, "order_route": "NOT_PRESENT",
        }
        receipt["receipt_sha256"] = _sha(receipt)
        with self._lock:
            scope = (identity, method)
            previous = self._latest_native.get(scope)
            if previous is not None and native_time <= previous:
                raise EventRejected("PPI_NATIVE_RECEIPT_NOT_NEWER")
            if len(self._receipts) == self.max_receipts:
                self._dropped += 1
                evicted = self._receipts[0]
                evicted_scope = (tuple(evicted["ppi_five_key"]), evicted["method"])
                if (self._latest_native.get(evicted_scope) ==
                    _time(evicted["native_at_utc"], reason="NATIVE_TIME")):
                    self._latest_native.pop(evicted_scope, None)
            self._receipts.append(json.loads(_canonical_bytes(receipt)))
            self._latest_native[scope] = native_time
        return json.loads(_canonical_bytes(receipt))

    @staticmethod
    def _state(reason):
        return {"schema": NATIVE_READ_SCHEMA, "state": reason,
                "provider_calls_by_rpc": 0}

    def read_native(self, *, consumer, method, five_key,
                    book1_receipt_sha256=None, commit_at=None,
                    commit_monotonic_ns=None):
        """Return existing native RAW only; never request from a provider."""
        if type(method) is not str or type(consumer) is not str:
            return self._state("DENIED_READ_ONLY")
        if consumer not in _NATIVE_CONSUMER_METHODS:
            return self._state("DENIED_READ_ONLY")
        if method in _NATIVE_UNBUDGETED_METHODS:
            return self._state("NO_BUDGET_AUTHORITY")
        if method not in _NATIVE_REST_METHODS or method not in _NATIVE_CONSUMER_METHODS[consumer]:
            return self._state("DENIED_READ_ONLY")
        try:
            identity = self._registered_identity(five_key)
        except (CatalogIdentityError, ValueError, TypeError):
            return self._state("DENIED_READ_ONLY")
        with self.feed._lock:
            allowed = self.feed._consumers.get(consumer, False)
            if allowed is False or (allowed is not None and identity not in allowed):
                return self._state("DENIED_READ_ONLY")
        causal_args = (book1_receipt_sha256, commit_at, commit_monotonic_ns)
        causal = any(x is not None for x in causal_args)
        if causal and (method != "book" or any(x is None for x in causal_args)):
            return self._state("PPI_PIT_POST_COMMIT_CONTRACT_INCOMPLETE")
        if causal:
            if not _SHA256.fullmatch(str(book1_receipt_sha256)):
                return self._state("PPI_PIT_BOOK1_RECEIPT_INVALID")
            try:
                committed = _time(commit_at, reason="COMMIT_TIME")
            except EventRejected:
                return self._state("PPI_PIT_COMMIT_TIME_INVALID")
            if type(commit_monotonic_ns) is not int or commit_monotonic_ns <= 0:
                return self._state("PPI_PIT_COMMIT_MONOTONIC_INVALID")
        now_time = _time(self.clock(), reason="NATIVE_CLOCK")
        now_mono = self.monotonic_ns()
        with self._lock:
            entries = [item for item in self._receipts if
                       tuple(item["ppi_five_key"]) == identity and
                       item["method"] == method]
            if not entries:
                return self._state("NO_AUTHENTIC_NATIVE_RECEIPT")
            if causal:
                book1 = next((item for item in entries
                              if item["receipt_sha256"] == book1_receipt_sha256), None)
                if book1 is None:
                    return self._state("PPI_PIT_BOOK1_NOT_RETAINED")
                if (_time(book1["native_at_utc"], reason="NATIVE_TIME") > committed
                    or book1["receive_monotonic_ns"] >= commit_monotonic_ns):
                    return self._state("PPI_PIT_BOOK1_NOT_PRE_COMMIT")
                # Choose FIRST post-COMMIT native book; never latest/cherry-picked.
                eligible = [item for item in entries if
                    _time(item["native_at_utc"], reason="NATIVE_TIME") > committed
                    and item["receive_monotonic_ns"] > commit_monotonic_ns]
                if not eligible:
                    return self._state("NO_AUTHENTIC_NATIVE_RECEIPT")
                receipt = eligible[0]
                if receipt["session_fingerprint"] != book1["session_fingerprint"]:
                    return self._state("PPI_PIT_BOOK2_SESSION_CHANGED")
            else:
                receipt = entries[-1]
            native = _time(receipt["native_at_utc"], reason="NATIVE_TIME")
            received = _time(receipt["received_at_utc"], reason="NATIVE_RECEIVE_TIME")
            age = (now_time - received).total_seconds()
            if (age < 0 or age > self.max_age_seconds or
                    not 0 <= (now_mono - receipt["receive_monotonic_ns"]) / 1e9 <= self.max_age_seconds
                    or (now_time - native).total_seconds() > self.max_age_seconds):
                return self._state("PPI_NATIVE_RECEIPT_STALE")
            return {"schema": NATIVE_READ_SCHEMA, "state": "OK",
                    "provider_calls_by_rpc": 0,
                    "receipt": json.loads(_canonical_bytes(receipt))}

    def dispatch(self, request):
        if not (isinstance(request, dict)
                and set(request) <= {"op", "consumer", "method", "ppi_five_key",
                                     "book1_receipt_sha256", "commit_at", "commit_monotonic_ns"}
                and request.get("op") == "native_read"):
            return self._state("DENIED_READ_ONLY")
        try:
            return self.read_native(
                consumer=request.get("consumer"),
                method=request.get("method"),
                five_key=request.get("ppi_five_key"),
                book1_receipt_sha256=request.get("book1_receipt_sha256"),
                commit_at=request.get("commit_at"),
                commit_monotonic_ns=request.get("commit_monotonic_ns"),
            )
        except (ValueError, TypeError, KeyError):
            return self._state("DENIED_READ_ONLY")

    def stats(self):
        with self._lock:
            return {"schema": NATIVE_READ_SCHEMA,
                    "retained_raw_receipts": len(self._receipts),
                    "dropped_raw_receipts": self._dropped,
                    "retained_latest_native_sources": len(self._latest_native),
                    "provider_calls_by_rpc": 0,
                    "paper_authority": "NO_VERIFICADO"}


class ReadOnlyUnixFeedServer:
    """Optional same-host read-only AF_UNIX cursor transport.

    It is never started automatically. A private parent directory (0700) and
    socket (0600) are required. The listener knows no broker login, account,
    order or PPI GET API.  Watch UID/permissions remain to be independently
    verified before any real integration.
    """

    def __init__(self, bus: PPISharedMarketFeed, *, socket_path: Path,
                 native_receipts: PPIReadOnlyNativeReceiptStore | None = None):
        self.bus = bus
        if native_receipts is not None and native_receipts.feed is not bus:
            raise ValueError("PPI_NATIVE_RECEIPT_WRONG_FEED")
        self.native_receipts = native_receipts
        self.socket_path = Path(socket_path)
        self._server = None
        self._owned_socket_dev_inode = None

    def dispatch(self, request: Any) -> dict:
        """Pure read-only request protocol, testable without opening sockets."""
        if isinstance(request, dict) and request.get("op") == "native_read":
            if self.native_receipts is None:
                return PPIReadOnlyNativeReceiptStore._state(
                    "NO_AUTHENTIC_NATIVE_RECEIPT")
            return self.native_receipts.dispatch(request)
        if not (isinstance(request, dict)
                and set(request) <= {"op", "consumer", "after_sequence", "limit"}
                and request.get("op") == "pull"):
            return {"state": "DENIED_READ_ONLY"}
        try:
            return self.bus.pull(request.get("consumer"),
                                 after_sequence=request.get("after_sequence"),
                                 limit=request.get("limit", 32))
        except (ValueError, TypeError):
            return {"state": "DENIED_READ_ONLY"}

    def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("PPI_UNIX_FEED_ALREADY_STARTED")
        parent = self.socket_path.parent
        mode = parent.stat().st_mode
        if not stat.S_ISDIR(mode) or mode & 0o077 or self.socket_path.exists() or self.socket_path.is_symlink():
            raise PermissionError("PPI_UNIX_FEED_PRIVATE_PATH_REQUIRED")
        outer = self

        class _Server(socketserver.UnixStreamServer):
            # One bounded short-lived request at a time; no unbounded threads
            # or worker pool on a low-memory droplet.
            request_queue_size = 8
            allow_reuse_address = False

        class _Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(1.0)
                try:
                    request_data = self.rfile.readline(4097)
                    if not request_data.endswith(b"\n") or len(request_data) > 4096:
                        response = {"state": "DENIED_READ_ONLY"}
                    else:
                        request = json.loads(request_data)
                        response = outer.dispatch(request)
                except (ValueError, TypeError, TimeoutError, OSError):
                    response = {"state": "DENIED_READ_ONLY"}
                data = _canonical_bytes(response)
                if len(data) > 131_072:
                    data = _canonical_bytes({"state": "RESPONSE_TOO_LARGE"})
                self.wfile.write(data + b"\n")

        server = _Server(str(self.socket_path), _Handler, bind_and_activate=True)
        os.chmod(self.socket_path, 0o600)
        own = os.stat(self.socket_path, follow_symlinks=False)
        self._owned_socket_dev_inode = (own.st_dev, own.st_ino)
        self._server = server

    def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("PPI_UNIX_FEED_NOT_STARTED")
        self._server.serve_forever(poll_interval=0.05)

    def shutdown(self) -> None:
        if self._server is not None:
            self._server.shutdown()

    def close(self) -> None:
        if self._server is not None:
            self._server.server_close()
            self._server = None
        # Only remove our exact socket node. Never remove arbitrary paths.
        if self._owned_socket_dev_inode is not None:
            try:
                item = os.stat(self.socket_path, follow_symlinks=False)
            except FileNotFoundError:
                item = None
            if (item is not None and stat.S_ISSOCK(item.st_mode)
                and (item.st_dev, item.st_ino) == self._owned_socket_dev_inode):
                self.socket_path.unlink()
            self._owned_socket_dev_inode = None


class ReadOnlyUnixFeedClient:
    """Bounded consumer facade for a future G2 single acquisition worker.

    Missing worker, denied socket, invalid envelope or cursor GAP can NEVER
    trigger an implicit independent PPI request.  Callers explicitly handle
    `SharedFeedUnavailable` / `GAP` with PAPER decision hold and evidence.
    """

    def __init__(self, *, socket_path: Path, timeout_seconds: float = 0.5):
        if (isinstance(timeout_seconds, bool) or
            not isinstance(timeout_seconds, (int, float)) or
            not 0 < timeout_seconds <= 5):
            raise ValueError("PPI_SHARED_CLIENT_TIMEOUT_INVALID")
        self.socket_path = Path(socket_path)
        self.timeout_seconds = timeout_seconds

    def _request(self, packet: dict) -> dict:
        """One bounded AF_UNIX request, never broker/provider I/O."""
        request = _canonical_bytes(packet) + b"\n"
        if len(request) > 4096:
            raise ValueError("PPI_SHARED_CLIENT_REQUEST_UNBOUNDED")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(str(self.socket_path))
                connection.sendall(request)
                response_bytes = bytearray()
                while len(response_bytes) <= 131_072:
                    chunk = connection.recv(min(4096, 131_073 - len(response_bytes)))
                    if not chunk:
                        break
                    response_bytes.extend(chunk)
                    if b"\n" in chunk:
                        break
        except (OSError, TimeoutError) as exc:
            raise SharedFeedUnavailable("PPI_GATEWAY_UNAVAILABLE") from exc
        if (len(response_bytes) > 131_072 or not response_bytes.endswith(b"\n")
            or b"\n" in response_bytes[:-1]):
            raise SharedFeedUnavailable("PPI_GATEWAY_PROTOCOL_INVALID")
        try:
            result = json.loads(response_bytes)
        except (ValueError, UnicodeError) as exc:
            raise SharedFeedUnavailable("PPI_GATEWAY_PROTOCOL_INVALID") from exc
        if not isinstance(result, dict):
            raise SharedFeedUnavailable("PPI_GATEWAY_PROTOCOL_INVALID")
        return result

    def pull(self, consumer: str, *, after_sequence: int, limit: int = 32) -> dict:
        if not isinstance(consumer, str) or not _CONSUMER.fullmatch(consumer):
            raise ValueError("PPI_CONSUMER_ID_INVALID")
        if (isinstance(after_sequence, bool) or not isinstance(after_sequence, int)
            or after_sequence < 0 or isinstance(limit, bool)
            or not isinstance(limit, int) or not 1 <= limit <= 128):
            raise ValueError("PPI_CONSUMER_CURSOR_OR_BATCH_INVALID")
        response = self._request({"op": "pull", "consumer": consumer,
                                  "after_sequence": after_sequence, "limit": limit})
        if not isinstance(response, dict) or response.get("schema") != SCHEMA:
            raise SharedFeedUnavailable("PPI_GATEWAY_RESPONSE_DENIED_OR_INVALID")
        if response.get("state") not in ("OK", "GAP"):
            raise SharedFeedUnavailable("PPI_GATEWAY_PROTOCOL_INVALID")
        if (not isinstance(response.get("events"), list) or
            len(response["events"]) > limit or
            not isinstance(response.get("producer_cursor"), int) or
            not isinstance(response.get("oldest_sequence"), int)):
            raise SharedFeedUnavailable("PPI_GATEWAY_PROTOCOL_INVALID")
        last_seq = after_sequence
        for event in response["events"]:
            if not isinstance(event, dict):
                raise SharedFeedUnavailable("PPI_GATEWAY_EVENT_INVALID")
            event_body = {key: value for key, value in event.items()
                          if key != "event_sha256"}
            if (event.get("schema") != SCHEMA or event.get("paper_ready") is not False
                or event.get("ppi_catalog_authority") != "NO_VERIFICADO"
                or event.get("event_sha256") != _sha(event_body)
                or not isinstance(event.get("sequence"), int)
                or event["sequence"] <= last_seq):
                raise SharedFeedUnavailable("PPI_GATEWAY_EVENT_INVALID")
            last_seq = event["sequence"]
        if response["state"] == "GAP" and response["events"]:
            raise SharedFeedUnavailable("PPI_GATEWAY_GAP_CONTAINS_EVENTS")
        return response

    def read_native(self, consumer: str, *, method: str, five_key,
                    book1_receipt_sha256=None, commit_at=None,
                    commit_monotonic_ns=None) -> dict:
        """Read a previously acquired PPI REST RAW receipt; no implicit GET.

        Any non-OK state is a HARD HOLD for PAPER consumers. The returned
        local digest never independently certifies provider origin or READY.
        """
        if not isinstance(consumer, str) or not _CONSUMER.fullmatch(consumer):
            raise ValueError("PPI_NATIVE_CONSUMER_INVALID")
        if type(method) is not str or method not in _NATIVE_REST_METHODS | _NATIVE_UNBUDGETED_METHODS:
            raise ValueError("PPI_NATIVE_METHOD_NOT_READ_ONLY")
        identity = _five(five_key)
        req = {"op": "native_read", "consumer": consumer, "method": method,
               "ppi_five_key": list(identity)}
        if any(value is not None for value in
               (book1_receipt_sha256, commit_at, commit_monotonic_ns)):
            req.update({"book1_receipt_sha256": book1_receipt_sha256,
                        "commit_at": (commit_at.isoformat() if isinstance(commit_at, datetime)
                                      else commit_at),
                        "commit_monotonic_ns": commit_monotonic_ns})
        result = self._request(req)
        if (result.get("schema") != NATIVE_READ_SCHEMA
            or type(result.get("state")) is not str
            or result.get("provider_calls_by_rpc") != 0):
            raise SharedFeedUnavailable("PPI_NATIVE_RPC_ENVELOPE_INVALID")
        if result["state"] != "OK":
            if "receipt" in result:
                raise SharedFeedUnavailable("PPI_NATIVE_RPC_DENIAL_WITH_DATA")
            return result
        receipt = result.get("receipt")
        if not isinstance(receipt, dict):
            raise SharedFeedUnavailable("PPI_NATIVE_RPC_RECEIPT_INVALID")
        unsigned = {key: value for key, value in receipt.items()
                    if key != "receipt_sha256"}
        if (receipt.get("schema") != NATIVE_READ_SCHEMA
            or receipt.get("method") != method
            or receipt.get("ppi_five_key") != list(identity)
            or receipt.get("paper_ready") is not False
            or receipt.get("order_route") != "NOT_PRESENT"
            or receipt.get("ppi_catalog_authority") != "NO_VERIFICADO"
            or receipt.get("guard_budget_receipt_authority") != "NO_VERIFICADO"
            or receipt.get("receipt_sha256") != _sha(unsigned)
            or receipt.get("raw_sha256") != _sha(receipt.get("raw_payload"))):
            raise SharedFeedUnavailable("PPI_NATIVE_RPC_RECEIPT_INVALID")
        return result
