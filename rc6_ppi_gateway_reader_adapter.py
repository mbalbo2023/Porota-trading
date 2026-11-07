"""A678 G2: strictly READ_ONLY adapter for the *inert* shared PPI event feed.

This is deliberately NOT a drop-in active replacement for ProductMarketReader.
The G1 transport exposes only normalized STREAM observations over a local
read-only Unix socket.  It does not expose native authenticated REST Current,
Book, Intraday, catalog, history, configuration or PIT raw Book receipts.

The native reader methods therefore fail CLOSED.  In particular, returning a
reconstructed BOOK dictionary to ``ScannerPrimaryRecorder.capture_raw_book``
would misrepresent a normalized quote as independent native PPI evidence.
Future G2 implementations must add separately reviewed authenticated native
read RPC contracts before any production consumer cutover.

No PPI SDK, requests library, credentials, filesystem socket creation, SQLite
connection or order-writing interface is imported or instantiated here.
"""

from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import json
import re
import threading
from typing import Any, Callable

from rc6_ppi_shared_feed import SCHEMA, SharedFeedUnavailable


_SHA = re.compile(r"[0-9a-f]{64}\Z")
_WORD = re.compile(r"[A-Z0-9][A-Z0-9./_-]{0,63}\Z")
_PRIORITIES = frozenset((
    "DISCOVERY", "WARM", "HOT", "OPENED_CRITICAL", "EXIT_CRITICAL", "WATCH_CRITICAL"
))
_CONSUMERS = frozenset(("scanner", "exit_reader", "scalping", "watch"))


class GatewayEvidenceRejected(RuntimeError):
    """A source/data-method precondition is missing; no alternate PPI GET."""


def _sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _five_key(value: Any) -> tuple[str, str, str, str, str]:
    if (not isinstance(value, (tuple, list)) or len(value) != 5
        or any(not isinstance(item, str) or not _WORD.fullmatch(item) for item in value)):
        raise ValueError("PPI_GATEWAY_FIVE_KEY_INVALID")
    return tuple(value)


def _utc_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not 1 <= len(value) <= 60:
        raise GatewayEvidenceRejected(f"PPI_GATEWAY_{field}_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GatewayEvidenceRejected(f"PPI_GATEWAY_{field}_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GatewayEvidenceRejected(f"PPI_GATEWAY_{field}_NAIVE")
    return parsed.astimezone(timezone.utc)


class SharedPPIReaderAdapter:
    """Observe G1 STREAM evidence; do not forge PPI SDK-native REST reads.

    ``client`` is an injected ``ReadOnlyUnixFeedClient`` or deterministic fake
    with the same read-only ``pull`` method.  Constructor performs no IO.
    A successful poll remains ``OBSERVE_ONLY`` because the G1 event itself
    records ``ppi_catalog_authority=NO_VERIFICADO`` / ``paper_ready=False``.
    """

    def __init__(self, *, client: Any, consumer: str,
                 catalog_source_digest: str,
                 now_utc: Callable[[], datetime] | None = None,
                 max_age_seconds: float = 5, batch_size: int = 32,
                 retain_events: int = 64):
        if consumer not in _CONSUMERS:
            raise ValueError("PPI_GATEWAY_CONSUMER_INVALID")
        if not _SHA.fullmatch(str(catalog_source_digest)):
            raise ValueError("PPI_GATEWAY_CATALOG_DIGEST_INVALID")
        if not callable(getattr(client, "pull", None)):
            raise TypeError("PPI_GATEWAY_READONLY_PULL_REQUIRED")
        if (isinstance(max_age_seconds, bool) or
            not isinstance(max_age_seconds, (int, float)) or
            not 0 < max_age_seconds <= 120):
            raise ValueError("PPI_GATEWAY_MAX_AGE_INVALID")
        if (isinstance(batch_size, bool) or not isinstance(batch_size, int)
            or not 1 <= batch_size <= 128):
            raise ValueError("PPI_GATEWAY_BATCH_INVALID")
        if (isinstance(retain_events, bool) or not isinstance(retain_events, int)
            or not 1 <= retain_events <= 512):
            raise ValueError("PPI_GATEWAY_RETENTION_INVALID")
        self._client = client
        self._consumer = consumer
        self._source_digest = catalog_source_digest
        self._now = now_utc or (lambda: datetime.now(timezone.utc))
        self._max_age = max_age_seconds
        self._batch = batch_size
        self._retain = retain_events
        self._cursor = 0
        self._events: list[dict] = []
        self._state = "NO_VERIFICADO"
        self._last_error = None
        self._closed = False
        self._lock = threading.RLock()
        self._scope = threading.local()

    @property
    def metrics(self) -> dict:
        with self._lock:
            return {
                "schema": SCHEMA,
                "consumer": self._consumer,
                "state": self._state,
                "cursor": self._cursor,
                "retained": len(self._events),
                "paper_authority": "NO_VERIFICADO",
                "authenticated": False,
                "budget_enabled": False,
                "provider_requests": 0,
            }

    @property
    def budget_enabled(self) -> bool:
        return False

    @property
    def last_read_error_code(self):
        return self._last_error

    @contextmanager
    def read_scope(self, *, priority: str | None = None, identity=None):
        if priority is not None and priority not in _PRIORITIES:
            raise ValueError("PPI_GATEWAY_PRIORITY_INVALID")
        if identity is None and priority in {"EXIT_CRITICAL", "OPENED_CRITICAL", "WATCH_CRITICAL"}:
            raise ValueError("PPI_GATEWAY_EXACT_IDENTITY_REQUIRED")
        key = _five_key(identity) if identity is not None else None
        prev = getattr(self._scope, "request", None)
        self._scope.request = (key, priority)
        try:
            yield
        finally:
            self._scope.request = prev

    def _native_unavailable(self, method: str, request=None):
        if self._closed:
            self._last_error = "PPI_GATEWAY_ADAPTER_CLOSED"
            raise GatewayEvidenceRejected(self._last_error)
        if request is not None:
            scope = getattr(self._scope, "request", None)
            if scope is None or scope[0] is None:
                self._last_error = "PPI_GATEWAY_EXACT_IDENTITY_REQUIRED"
                raise GatewayEvidenceRejected(self._last_error)
            key = scope[0]
            if tuple(request) != (key[0], key[1], key[4]):
                self._last_error = "PPI_GATEWAY_IDENTITY_MISMATCH"
                raise GatewayEvidenceRejected(self._last_error)
        self._last_error = "PPI_GATEWAY_METHOD_NOT_SUPPORTED"
        raise GatewayEvidenceRejected(self._last_error + ":" + method)

    def login_once(self) -> None:
        """A responsive Unix socket is NOT authenticated producer evidence."""
        self._last_error = "PPI_GATEWAY_AUTH_ATTESTATION_MISSING"
        raise GatewayEvidenceRejected(self._last_error)

    def current(self, ticker: str, instrument_type: str, settlement: str):
        return self._native_unavailable("CURRENT", (ticker, instrument_type, settlement))

    def book(self, ticker: str, instrument_type: str, settlement: str):
        return self._native_unavailable("NATIVE_BOOK", (ticker, instrument_type, settlement))

    def intraday(self, ticker: str, instrument_type: str, settlement: str):
        return self._native_unavailable("INTRADAY", (ticker, instrument_type, settlement))

    def history(self, ticker: str, instrument_type: str, settlement: str, date_from, date_to):
        return self._native_unavailable("HISTORY", (ticker, instrument_type, settlement))

    def search_instruments(self, ticker: str, instrument_type: str,
                           name: str | None = None, market: str = "BYMA"):
        return self._native_unavailable("SEARCH")

    def market_configuration(self):
        return self._native_unavailable("CONFIGURATION")

    def estimate_bonds(self, parameters):
        return self._native_unavailable("ESTIMATE_BONDS")

    def observe_exit_round(self, *, elapsed_seconds, deadline_seconds, failures=0):
        return {"status": "BASELINE_NOT_MEASURED", "lower_suspended": None}

    def discard_exit_round_scope(self):
        return None

    def _validate_event(self, event: Any, *, after_cursor: int, producer_cursor: int,
                        now: datetime) -> int:
        if not isinstance(event, dict) or event.get("schema") != SCHEMA:
            raise GatewayEvidenceRejected("PPI_GATEWAY_EVENT_SCHEMA_INVALID")
        if (isinstance(event.get("sequence"), bool) or
            not isinstance(event.get("sequence"), int) or
            not after_cursor < event["sequence"] <= producer_cursor):
            raise GatewayEvidenceRejected("PPI_GATEWAY_EVENT_SEQUENCE_INVALID")
        body = {key: value for key, value in event.items() if key != "event_sha256"}
        if event.get("event_sha256") != _sha256(body):
            raise GatewayEvidenceRejected("PPI_GATEWAY_EVENT_HASH_MISMATCH")
        try:
            _five_key(event.get("ppi_five_key"))
        except ValueError as exc:
            raise GatewayEvidenceRejected("PPI_GATEWAY_FIVE_KEY_INVALID") from exc
        if event.get("ppi_catalog_source_digest") != self._source_digest:
            raise GatewayEvidenceRejected("PPI_GATEWAY_CATALOG_DIGEST_MISMATCH")
        if event.get("type_provenance") not in (
                "NATIVE_EVENT", "UNIQUE_ACTIVE_SUBSCRIPTION_NOT_NATIVE"):
            raise GatewayEvidenceRejected("PPI_GATEWAY_TYPE_PROVENANCE_INVALID")
        fingerprint = event.get("session_fingerprint")
        if fingerprint is not None and not _SHA.fullmatch(str(fingerprint)):
            raise GatewayEvidenceRejected("PPI_GATEWAY_SESSION_FINGERPRINT_INVALID")
        if (event.get("ppi_catalog_authority") != "NO_VERIFICADO" or
            event.get("paper_ready") is not False or
            event.get("order_route") != "NOT_PRESENT"):
            raise GatewayEvidenceRejected("PPI_GATEWAY_PAPER_AUTHORITY_FORGED")
        if event.get("kind") not in ("BOOK", "TRADE") or not isinstance(event.get("market_payload"), dict):
            raise GatewayEvidenceRejected("PPI_GATEWAY_EVENT_KIND_INVALID")
        born = _utc_time(event.get("native_event_time_utc"), "NATIVE_TIME")
        received = _utc_time(event.get("receive_time_utc"), "RECEIVED_TIME")
        if received < born:
            raise GatewayEvidenceRejected("PPI_GATEWAY_RECEIVE_CLOCK_INVERTED")
        if now < received or now < born:
            raise GatewayEvidenceRejected("PPI_GATEWAY_EVENT_FUTURE")
        if (now - received).total_seconds() > self._max_age:
            raise GatewayEvidenceRejected("PPI_GATEWAY_EVENT_STALE")
        if (isinstance(event.get("receive_monotonic_ns"), bool) or
            not isinstance(event.get("receive_monotonic_ns"), int) or
            event["receive_monotonic_ns"] <= 0):
            raise GatewayEvidenceRejected("PPI_GATEWAY_MONOTONIC_INVALID")
        if not _SHA.fullmatch(str(event.get("source_normalized_sha256"))):
            raise GatewayEvidenceRejected("PPI_GATEWAY_SOURCE_DIGEST_INVALID")
        return event["sequence"]

    def poll_observations(self) -> tuple[dict, ...]:
        """Pull normalized observations, never turn them into PAPER readiness.

        An empty filtered batch can advance to ``producer_cursor`` only when
        its cardinality is below ``batch_size``: the producer then scanned all
        retained events.  A full batch must resume from the last returned
        sequence rather than skip unreturned relevant records.
        """
        with self._lock:
            if self._closed:
                self._last_error = "PPI_GATEWAY_ADAPTER_CLOSED"
                raise GatewayEvidenceRejected(self._last_error)
            try:
                packet = self._client.pull(
                    self._consumer, after_sequence=self._cursor, limit=self._batch)
            except SharedFeedUnavailable as exc:
                self._state = "UNAVAILABLE"
                self._last_error = str(exc)
                raise
            if not isinstance(packet, dict) or packet.get("schema") != SCHEMA:
                self._state = "HOLD"
                self._last_error = "PPI_GATEWAY_PACKET_INVALID"
                raise GatewayEvidenceRejected(self._last_error)
            if packet.get("state") == "GAP":
                self._state = "GAP_HOLD"
                self._last_error = "PPI_GATEWAY_GAP_HOLD"
                raise SharedFeedUnavailable(self._last_error)
            if packet.get("state") != "OK":
                self._state = "HOLD"
                self._last_error = "PPI_GATEWAY_PACKET_STATE_INVALID"
                raise GatewayEvidenceRejected(self._last_error)
            cursor = packet.get("producer_cursor")
            items = packet.get("events")
            if (isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < self._cursor
                or not isinstance(items, list) or len(items) > self._batch):
                self._state = "HOLD"
                self._last_error = "PPI_GATEWAY_PACKET_CURSOR_INVALID"
                raise GatewayEvidenceRejected(self._last_error)
            now = self._now()
            if (not isinstance(now, datetime) or now.tzinfo is None or
                now.utcoffset() is None):
                self._state = "HOLD"
                self._last_error = "PPI_GATEWAY_LOCAL_CLOCK_UNVERIFIED"
                raise GatewayEvidenceRejected(self._last_error)
            now = now.astimezone(timezone.utc)
            observed = []
            last = self._cursor
            try:
                for event in items:
                    last = self._validate_event(event, after_cursor=last,
                                                producer_cursor=cursor, now=now)
                    observed.append(copy.deepcopy(event))
            except GatewayEvidenceRejected as exc:
                self._state = "HOLD"
                self._last_error = str(exc)
                raise
            self._cursor = cursor if len(items) < self._batch else last
            self._events = (self._events + observed)[-self._retain:]
            self._state = "OBSERVE_ONLY"
            self._last_error = None
            return tuple(copy.deepcopy(observed))

    def observations(self) -> tuple[dict, ...]:
        with self._lock:
            if self._closed:
                self._last_error = "PPI_GATEWAY_ADAPTER_CLOSED"
                raise GatewayEvidenceRejected(self._last_error)
            now = self._now()
            if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
                self._state = "HOLD"
                self._last_error = "PPI_GATEWAY_LOCAL_CLOCK_UNVERIFIED"
                raise GatewayEvidenceRejected(self._last_error)
            now = now.astimezone(timezone.utc)
            for event in self._events:
                received = _utc_time(event["receive_time_utc"], "RECEIVED_TIME")
                if now < received or (now - received).total_seconds() > self._max_age:
                    self._state = "HOLD"
                    self._last_error = "PPI_GATEWAY_EVENT_STALE"
                    raise GatewayEvidenceRejected(self._last_error)
            return tuple(copy.deepcopy(self._events))

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._state = "CLOSED"
            self._events.clear()

