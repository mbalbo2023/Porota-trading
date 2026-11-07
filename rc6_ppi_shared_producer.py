"""RC6 A678 G2: deliberately inert, market-only adapter for a *single* PPI SDK owner.

This is an OFFHOST contract, NOT a live acquisition or runtime cutover.
It imports no SDK, sends no HTTP, logs into no account and starts no thread at
import/construction.  An explicitly owned production worker will have to inject
the *market-only* SDK realtime facet, a market-data Instrument factory and an
independently approved stream authority.  No account/order methods are exposed.

The PPI example documents ``realtime.connect_to_market_data`` followed by
``subscribe_to_element(Instrument(ticker, type, settlement))`` and
``start_connections``.  The example does not read Type from received packets;
the G1 fan-out (not this adapter) must enforce a unique PPI catalog and active
five-key subscription before allowing optional context binding.

https://itatppi.github.io/ppi-official-api-docs/api/ejemploPython/

The existing ``bd_ppi_readonly_guard`` wraps ``requests`` in product workers
and denies streaming semantics.  Integrating this adapter into production
therefore requires an isolated acquisition child and a verified SDK handshake,
NOT disabling the established read-only/order protection.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
import threading
import time
from typing import Any, Callable


SCHEMA = "rc6.ppi.shared-sdk-producer.offhost.v1"
_FIELD = re.compile(r"[A-Z0-9][A-Z0-9./_-]{0,63}\Z")
_PRIORITIES = frozenset(("EXIT_CRITICAL", "WATCH_CRITICAL", "OPENED_CRITICAL",
                         "HOT", "WARM", "DISCOVERY"))
_MARKET_NATIVE_FIELDS = frozenset(("Ticker", "Type", "Settlement", "Date", "Trade",
                                   "Bids", "Offers", "Price", "VolumeAmount",
                                   "OpeningPrice", "MaxDay", "MinDay",
                                   "VolumeTotalAmount", "Market", "Currency"))


class ProducerBlocked(RuntimeError):
    """No market-data transport activity is authorized for this invocation."""


def _five_key(identity: Any) -> tuple[str, str, str, str, str]:
    if not isinstance(identity, (list, tuple)) or len(identity) != 5:
        raise ProducerBlocked("PPI_PRODUCER_FIVE_KEY_INVALID")
    value = []
    for component in identity:
        if not isinstance(component, str) or not _FIELD.fullmatch(component.strip().upper()):
            raise ProducerBlocked("PPI_PRODUCER_FIVE_KEY_INVALID")
        value.append(component.strip().upper())
    return tuple(value)


class MarketOnlySDKTransport:
    """Narrow wrapper around *only* the documented PPI SDK MARKET realtime API.

    A production composition root provides the SDK's ``.realtime`` facet and
    its Instrument constructor; importing this module neither imports PPI nor
    performs authentication.  Deliberately no account, orders, funds, cancel,
    credentials or sessions API.  A caller must not inject the full SDK object.
    """

    def __init__(self, *, realtime: Any, instrument_factory: Callable):
        if not callable(instrument_factory) or not all(callable(getattr(realtime, key, None))
                for key in ("connect_to_market_data", "subscribe_to_element", "start_connections")):
            raise ProducerBlocked("PPI_MARKET_ONLY_SDK_FACET_INVALID")
        self.__market = realtime
        self.__instrument = instrument_factory

    def connect_market_data(self, on_connect, on_disconnect, on_market_data) -> None:
        self.__market.connect_to_market_data(on_connect, on_disconnect, on_market_data)

    def subscribe_market_data(self, ticker: str, instrument_type: str,
                              settlement: str) -> None:
        self.__market.subscribe_to_element(self.__instrument(ticker, instrument_type, settlement))

    def start_market_data(self) -> None:
        """Blocking in the SDK; owner must run it in its own worker process."""
        self.__market.start_connections()


class PPISharedSDKProducer:
    """Fail-closed event producer that uses a *separately* authorized transport.

    ``stream_authority.permits(action, five_key=...) is True`` is a *required*
    external decision.  Fake/locally-injected authority in OFFHOST tests does
    not demonstrate a valid provider entitlement or signed runtime approval.
    No default authority exists.  A local random fingerprint distinguishes
    reconnect generations, NOT cryptographic proof of a provider session.

    Currently focus is frozen after connection.  The official SDK example
    does not document safe unsubscribe; replacing a subscription on a still
    open socket could exceed broker capacity despite a bounded local planner.
    Dynamic funnel rebinding must use a controlled reconnect/verified API.
    No automatic REST fallback exists: without the independent RuntimePPIBudget
    and the existing exact wire guard, disconnected means fail closed.
    """

    def __init__(self, *, feed: Any, transport: Any, stream_authority: Any,
                 max_message_bytes: int = 65536,
                 received_clock: Callable[[], datetime] | None = None,
                 monotonic_ns: Callable[[], int] | None = None):
        if any(not callable(getattr(feed, field, None)) for field in
               ("plan_subscription", "active_subscriptions", "publish_native_market_event")):
            raise ProducerBlocked("PPI_PRODUCER_FEED_CONTRACT_INVALID")
        if any(not callable(getattr(transport, field, None)) for field in
               ("connect_market_data", "start_market_data", "subscribe_market_data")):
            raise ProducerBlocked("PPI_PRODUCER_TRANSPORT_INVALID")
        if not callable(getattr(stream_authority, "permits", None)):
            raise ProducerBlocked("PPI_STREAM_AUTHORITY_MISSING")
        if (type(max_message_bytes) is not int or not 1024 <= max_message_bytes <= 262144):
            raise ProducerBlocked("PPI_STREAM_MESSAGE_CAP_INVALID")
        self._feed = feed
        self._transport = transport
        self._authority = stream_authority
        self._max_bytes = max_message_bytes
        self._utcnow = received_clock or (lambda: datetime.now(timezone.utc))
        self._monotonic_ns = monotonic_ns or time.monotonic_ns
        self._state = "NOT_STARTED"
        self._session_fingerprint: str | None = None
        self._attempts = 0
        self._published = 0
        self._rejected = 0
        self._subscription_failures = 0
        self._last_failure_code: str | None = None
        self._wire_subscriptions: set[tuple[str, str, str, str, str]] = set()
        self._lock = threading.RLock()

    def _permits(self, action: str, identity=None) -> bool:
        try:
            return self._authority.permits(action, five_key=identity) is True
        except Exception:
            return False

    def plan(self, identity: Any, *, priority: str) -> str:
        """Plan bounded focus OFFHOST; never subscribe or perform provider I/O."""
        key = _five_key(identity)
        if priority not in _PRIORITIES:
            raise ProducerBlocked("PPI_PRODUCER_PRIORITY_INVALID")
        with self._lock:
            if self._state not in ("NOT_STARTED", "DISCONNECTED"):
                raise ProducerBlocked("PPI_STREAM_FOCUS_RECONNECT_REQUIRED")
            return self._feed.plan_subscription(key, priority=priority)

    def run_market_only(self) -> None:
        """Must be called only by an authorized, isolated producer worker.

        May block indefinitely inside SDK ``start_connections``.  No thread,
        login, SDK object or wire fallback is created here.  Once called the
        same driver cannot be started a second time; reconnect is SDK-owned.
        """
        with self._lock:
            if self._state != "NOT_STARTED":
                raise ProducerBlocked("PPI_STREAM_DOUBLE_START_BLOCKED")
            if not self._permits("CONNECT_MARKET_DATA"):
                self._state = "BLOCKED"
                raise ProducerBlocked("PPI_STREAM_AUTHORITY_NOT_VERIFIED")
            self._state = "CONNECTING"
            self._attempts += 1
        try:
            self._transport.connect_market_data(
                self._on_market_connect, self._on_market_disconnect, self._on_market_data)
            self._transport.start_market_data()
        except BaseException:
            with self._lock:
                self._state = "BLOCKED"
                self._session_fingerprint = None
                self._last_failure_code = "PPI_STREAM_TRANSPORT_ERROR"
            raise

    def _on_market_connect(self) -> None:
        with self._lock:
            if self._state not in ("CONNECTING", "CONNECTED", "DISCONNECTED", "DEGRADED", "BLOCKED"):
                self._state = "BLOCKED"
                return
            if not self._permits("CONNECT_MARKET_DATA"):
                self._state = "BLOCKED"
                self._session_fingerprint = None
                self._last_failure_code = "PPI_STREAM_AUTHORITY_NOT_VERIFIED"
                return
            self._session_fingerprint = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
            self._state = "CONNECTED"
            self._wire_subscriptions.clear()
            planned = self._feed.active_subscriptions()
            # G1 returns prioritized (EXIT first) bounded snapshots. No
            # automatic 9500-instrument subscribe loop is permitted.
            for key in planned:
                try:
                    identity = _five_key(key)
                    if not self._permits("SUBSCRIBE_MARKET_DATA", identity):
                        raise ProducerBlocked("PPI_STREAM_SUBSCRIPTION_NOT_AUTHORIZED")
                    self._transport.subscribe_market_data(identity[0], identity[1], identity[4])
                    self._wire_subscriptions.add(identity)
                except Exception:
                    self._subscription_failures += 1
                    self._state = "DEGRADED"
                    self._last_failure_code = "PPI_STREAM_SUBSCRIPTION_FAILED"
                    # No REST calls, unverified retries or guessed Book.

    def _on_market_disconnect(self) -> None:
        with self._lock:
            self._state = "DISCONNECTED"
            self._session_fingerprint = None
            self._wire_subscriptions.clear()
            self._last_failure_code = "PPI_STREAM_DISCONNECTED_NO_AUTO_REST"

    def _on_market_data(self, payload: Any) -> None:
        """Market-only callback. Invalid events are discarded, not paper-ready."""
        with self._lock:
            if self._state not in ("CONNECTED", "DEGRADED") or self._session_fingerprint is None:
                self._rejected += 1
                self._last_failure_code = "PPI_STREAM_NOT_READY"
                return
            if not self._permits("RECEIVE_MARKET_DATA"):
                self._state = "BLOCKED"
                self._session_fingerprint = None
                self._rejected += 1
                self._last_failure_code = "PPI_STREAM_AUTHORITY_REVOKED"
                return
            try:
                if isinstance(payload, str):
                    raw_bytes = payload.encode("utf-8")
                    if len(raw_bytes) > self._max_bytes:
                        raise ProducerBlocked("PPI_STREAM_MESSAGE_UNBOUNDED")
                    raw = json.loads(payload)
                elif isinstance(payload, bytes):
                    if len(payload) > self._max_bytes:
                        raise ProducerBlocked("PPI_STREAM_MESSAGE_UNBOUNDED")
                    raw = json.loads(payload.decode("utf-8"))
                else:
                    raw = payload
                    # json.dumps(...) first materializes the whole object;
                    # use bounded incremental encoding in the callback.
                    encoded_size = 0
                    for chunk in json.JSONEncoder(ensure_ascii=True,
                        allow_nan=False, separators=(",", ":")).iterencode(raw):
                        encoded_size += len(chunk.encode("utf-8"))
                        if encoded_size > self._max_bytes:
                            raise ProducerBlocked("PPI_STREAM_MESSAGE_UNBOUNDED")
                if not isinstance(raw, dict) or not set(raw).issubset(_MARKET_NATIVE_FIELDS):
                    raise ProducerBlocked("PPI_STREAM_NOT_MARKET_ONLY_PAYLOAD")
                ticker, settlement, family = (raw.get("Ticker"), raw.get("Settlement"), raw.get("Type"))
                if any(not isinstance(value, str) for value in (ticker, settlement)):
                    raise ProducerBlocked("PPI_STREAM_WIRE_SUBSCRIPTION_NOT_VERIFIED")
                if family is not None and not isinstance(family, str):
                    raise ProducerBlocked("PPI_STREAM_WIRE_SUBSCRIPTION_NOT_VERIFIED")
                match = [key for key in self._wire_subscriptions
                    if key[0] == ticker.strip().upper() and key[4] == settlement.strip().upper()
                    and (family is None or key[1] == family.strip().upper())]
                if len(match) != 1:
                    # G1 also binds to *catalog*, not arbitrary provider
                    # three-key data. Here we additionally require a proven
                    # subscription in the actual current wire generation.
                    raise ProducerBlocked("PPI_STREAM_WIRE_SUBSCRIPTION_NOT_VERIFIED")
                received = self._utcnow()
                if not isinstance(received, datetime) or received.tzinfo is None:
                    raise ProducerBlocked("PPI_STREAM_RECEIVE_CLOCK_UNVERIFIED")
                stamp = self._monotonic_ns()
                if type(stamp) is not int or stamp <= 0:
                    raise ProducerBlocked("PPI_STREAM_RECEIVE_CLOCK_UNVERIFIED")
                result = self._feed.publish_native_market_event(raw, received_at=received,
                    received_monotonic_ns=stamp, session_fingerprint=self._session_fingerprint)
                if not isinstance(result, dict) or result.get("paper_ready") is not False:
                    raise ProducerBlocked("PPI_STREAM_FEED_DID_NOT_FAIL_CLOSED")
                self._published += 1
            except Exception:
                # SDK callback thread must not die, but no failed validation
                # may be reinterpreted as a price or an EXIT Book.
                self._rejected += 1
                self._last_failure_code = "PPI_STREAM_EVENT_REJECTED"

    def status(self) -> dict[str, Any]:
        """Bounded, privacy-safe technical snapshot; NOT broker quota evidence."""
        with self._lock:
            return {"schema": SCHEMA, "state": self._state,
                    "connection_attempts": self._attempts,
                    "published": self._published,
                    "rejected": self._rejected,
                    "subscription_failures": self._subscription_failures,
                    "wire_subscriptions_count": len(self._wire_subscriptions),
                    "session_generation_known": self._session_fingerprint is not None,
                    "session_attested_by_provider": False,
                    "provider_quota": "NO_VERIFICADO",
                    "paper_ready": False, "orders_exposed": False,
                    "rest_fallback": "BLOCKED_UNWIRED",
                    "failure_code": self._last_failure_code}
