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


class ReadOnlyUnixFeedServer:
    """Optional same-host read-only AF_UNIX cursor transport.

    It is never started automatically. A private parent directory (0700) and
    socket (0600) are required. The listener knows no broker login, account,
    order or PPI GET API.  Watch UID/permissions remain to be independently
    verified before any real integration.
    """

    def __init__(self, bus: PPISharedMarketFeed, *, socket_path: Path):
        self.bus = bus
        self.socket_path = Path(socket_path)
        self._server = None
        self._owned_socket_dev_inode = None

    def dispatch(self, request: Any) -> dict:
        """Pure read-only request protocol, testable without opening sockets."""
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

    def pull(self, consumer: str, *, after_sequence: int, limit: int = 32) -> dict:
        if not isinstance(consumer, str) or not _CONSUMER.fullmatch(consumer):
            raise ValueError("PPI_CONSUMER_ID_INVALID")
        if (isinstance(after_sequence, bool) or not isinstance(after_sequence, int)
            or after_sequence < 0 or isinstance(limit, bool)
            or not isinstance(limit, int) or not 1 <= limit <= 128):
            raise ValueError("PPI_CONSUMER_CURSOR_OR_BATCH_INVALID")
        request = _canonical_bytes({"op": "pull", "consumer": consumer,
                                    "after_sequence": after_sequence, "limit": limit}) + b"\n"
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
            response = json.loads(response_bytes)
        except (ValueError, UnicodeError) as exc:
            raise SharedFeedUnavailable("PPI_GATEWAY_PROTOCOL_INVALID") from exc
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
