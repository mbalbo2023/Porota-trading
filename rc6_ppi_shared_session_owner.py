"""OFFHOST composition contract for one authenticated PPI market-data owner.

This module deliberately never imports/creates the PPI SDK, starts a socket,
reads a provider, or grants quota. A future isolated worker injects exactly
one ProductionMarketReader factory. External Watch/account capacity authority
is required for any actual REST acquisition or stream start; no default grant.

Only the guarded acquisition owner can GET. All consumer requests dispatch to
bounded, read-only cached receipts/events. The SDK's documented realtime
unsubscribe/stop lifecycle is NOT proven; after starting a socket, this owner
cannot claim to release its process-wide HTTP guard safely.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from pathlib import Path
from typing import Any, Callable

from rc6_ppi_shared_feed import (
    PPIReadOnlyNativeReceiptStore,
    PPISharedMarketFeed,
    ReadOnlyUnixFeedServer,
)
from rc6_ppi_shared_producer import PPISharedSDKProducer
from rc6_ppi_shared_rest_producer import MarketOnlyRESTProducer


SCHEMA = "rc6.ppi.single-market-session.offhost.v1"


class SessionOwnerBlocked(RuntimeError):
    """A missing independent authority or unsafe lifecycle denies operation."""


class PPISharedSessionOwner:
    """Process-local owner; does not claim account-wide/Watch uniqueness.

    `external_capacity_authority.permits(action, five_key=...) is True` is an
    EXTERNAL decision, not a guessed broker rate limit. The mandatory adapter
    is not implemented in this module. Until the real Watch/capacity witness
    and entitlement exist, the live product must not instantiate this owner.
    """

    _registry_lock = threading.Lock()
    _active_owner = None

    def __init__(self, *, reader_factory: Callable[[], Any],
                 instrument_factory: Callable[..., Any],
                 stream_authority: Any, external_capacity_authority: Any,
                 catalog_authority: Any,
                 ppi_catalog_rows: list[dict[str, Any]],
                 catalog_five_keys: list[tuple[str, ...]],
                 catalog_source_digest: str,
                 consumer_five_keys: dict[str, list[tuple[str, ...]]],
                 socket_path: Path, max_subscriptions: int = 60):
        if not callable(reader_factory) or not callable(instrument_factory):
            raise SessionOwnerBlocked("PPI_SESSION_INJECTED_FACTORY_MISSING")
        if not callable(getattr(stream_authority, "permits", None)):
            raise SessionOwnerBlocked("PPI_STREAM_AUTHORITY_MISSING")
        # No provided external quota witness: fail closed BEFORE any SDK/login.
        if not callable(getattr(external_capacity_authority, "permits", None)):
            raise SessionOwnerBlocked("PPI_EXTERNAL_CAPACITY_AUTHORITY_MISSING")
        if not callable(getattr(catalog_authority, "permits", None)):
            raise SessionOwnerBlocked("PPI_PPI_CATALOG_AUTHORITY_MISSING")
        if not isinstance(consumer_five_keys, dict) or not consumer_five_keys:
            raise SessionOwnerBlocked("PPI_SESSION_CONSUMERS_MISSING")

        self._reader_factory = reader_factory
        self._instrument_factory = instrument_factory
        self._stream_authority = stream_authority
        self._capacity = external_capacity_authority
        self._catalog_authority = catalog_authority
        self._feed = PPISharedMarketFeed(
            ppi_catalog_rows=ppi_catalog_rows,
            catalog_source_digest=catalog_source_digest,
            max_subscriptions=max_subscriptions,
        )
        self._native = PPIReadOnlyNativeReceiptStore(self._feed)
        for consumer, keys in consumer_five_keys.items():
            self._feed.register_consumer(consumer, watched_five_keys=keys)
        self._rpc = ReadOnlyUnixFeedServer(
            self._feed, socket_path=Path(socket_path), native_receipts=self._native,
        )
        self._catalog_five_keys = catalog_five_keys
        self._catalog_source_digest = catalog_source_digest
        self._reader = None
        self._rest = None
        self._stream = None
        self._local_generation = None
        self._login_invocations = 0
        self._stream_invoked = False
        self._state = "NOT_OPEN"
        self._lock = threading.RLock()

    def _permits_capacity(self, action: str, identity=None) -> bool:
        try:
            return self._capacity.permits(action, five_key=identity) is True
        except Exception:
            return False

    def _permits_catalog(self, identity=None) -> bool:
        try:
            return self._catalog_authority.permits(
                "PPI_FIVE_KEY_CATALOG_ATTESTED", five_key=identity) is True
        except Exception:
            return False

    def _permits_stream_connect(self) -> bool:
        try:
            return self._stream_authority.permits("CONNECT_MARKET_DATA") is True
        except Exception:
            return False

    def open_once(self) -> "PPISharedSessionOwner":
        """Create/login the single reader only after external OWNER gate.

        `ProductionMarketReader.__init__` installs a global requests guard;
        this owner reserves the process-local slot *before* constructing it.
        If a reader is built but authentication/binding fails, restore guard.
        """
        with self._lock:
            if self._state == "OPEN":
                return self
            if self._state != "NOT_OPEN":
                raise SessionOwnerBlocked("PPI_SESSION_OWNER_NOT_RESTARTABLE")
            if not self._permits_capacity("OWNER_AUTHENTICATE"):
                raise SessionOwnerBlocked("PPI_ACCOUNT_WATCH_CAPACITY_UNVERIFIED")
            if not self._permits_catalog():
                raise SessionOwnerBlocked("PPI_CATALOG_INDEPENDENT_AUTHORITY_UNVERIFIED")
            with type(self)._registry_lock:
                if type(self)._active_owner is not None:
                    raise SessionOwnerBlocked("PPI_PROCESS_ALREADY_HAS_MARKET_OWNER")
                type(self)._active_owner = self
            source = None
            try:
                source = self._reader_factory()
                needed = ("login_once", "market_only_sdk_transport",
                          "capture_guarded_native", "close")
                if (not all(callable(getattr(source, name, None)) for name in needed)
                        or getattr(source, "budget_enabled", False) is not True):
                    raise SessionOwnerBlocked("PPI_GUARDED_BUDGETED_READER_REQUIRED")
                self._login_invocations += 1
                source.login_once()  # exactly once, never by consumers/stream
                transport = source.market_only_sdk_transport(
                    instrument_factory=self._instrument_factory)
                stream = PPISharedSDKProducer(
                    feed=self._feed, transport=transport,
                    stream_authority=self._stream_authority,
                )
                local_generation = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
                rest = MarketOnlyRESTProducer(
                    guarded_source=source, receipt_sink=self._native,
                    catalog_five_keys=self._catalog_five_keys,
                    catalog_source_digest=self._catalog_source_digest,
                    session_fingerprint=local_generation,
                )
                self._reader = source
                self._stream = stream
                self._rest = rest
                self._local_generation = local_generation
                self._state = "OPEN"
                return self
            except BaseException:
                if source is not None:
                    try:
                        source.close()
                    except BaseException:
                        # Do NOT let a second owner install atop a guard that
                        # may still intercept global HTTP requests.
                        self._state = "GUARD_RESTORE_UNVERIFIED"
                        raise
                self._state = "NOT_OPEN"
                with type(self)._registry_lock:
                    if type(self)._active_owner is self:
                        type(self)._active_owner = None
                raise

    def _require_open(self):
        if self._state != "OPEN" or self._reader is None:
            raise SessionOwnerBlocked("PPI_SINGLE_SESSION_NOT_OPEN")

    def capture_native(self, method: str, five_key: Any, *, priority: str):
        """Only owner path to guarded PPI REST; no consumer-facing GET."""
        with self._lock:
            self._require_open()
            if not self._permits_catalog(five_key):
                raise SessionOwnerBlocked("PPI_CATALOG_INDEPENDENT_AUTHORITY_UNVERIFIED")
            if not self._permits_capacity("REST_NATIVE_ACQUIRE", five_key):
                raise SessionOwnerBlocked("PPI_ACCOUNT_WATCH_CAPACITY_UNVERIFIED")
            return self._rest.capture(method, five_key, priority=priority)

    def dispatch_read_only(self, request: Any) -> dict:
        """Pure cached reader/EXIT/scalping RPC dispatch; zero PPI I/O."""
        with self._lock:
            if self._state != "OPEN":
                return {"state": "PPI_SESSION_OWNER_UNAVAILABLE", "provider_calls_by_rpc": 0}
        return self._rpc.dispatch(request)

    def plan_focus(self, five_key: Any, *, priority: str) -> str:
        """Plan before socket start only; HOT rebinding needs proven reconnect."""
        with self._lock:
            self._require_open()
            if self._stream_invoked:
                raise SessionOwnerBlocked("PPI_STREAM_FOCUS_RECONNECT_NOT_AUTHORIZED")
            if not self._permits_catalog(five_key):
                raise SessionOwnerBlocked("PPI_CATALOG_INDEPENDENT_AUTHORITY_UNVERIFIED")
            if not self._permits_capacity("STREAM_SUBSCRIPTION", five_key):
                raise SessionOwnerBlocked("PPI_ACCOUNT_WATCH_CAPACITY_UNVERIFIED")
            return self._stream.plan(five_key, priority=priority)

    def run_market_stream(self) -> None:
        """Explicit/foreground API; never called by constructor/open.

        SDK start_connections may block forever, and a safe stop/unsubscribe
        method is NOT documented. Isolated process ownership is required by
        the future release integrator, not implemented here.
        """
        with self._lock:
            self._require_open()
            if self._stream_invoked:
                raise SessionOwnerBlocked("PPI_STREAM_DOUBLE_START_BLOCKED")
            planned = self._feed.active_subscriptions()
            if not planned:
                raise SessionOwnerBlocked("PPI_STREAM_EMPTY_FOCUS_BLOCKED")
            if (not self._permits_capacity("STREAM_CONNECT") or
                    not self._permits_stream_connect()):
                raise SessionOwnerBlocked("PPI_STREAM_EXTERNAL_AUTHORITY_NOT_VERIFIED")
            # Even a previously admitted HOT/EXIT focus can lose external
            # capacity or catalog provenance before the socket starts.
            if any(not self._permits_capacity("STREAM_SUBSCRIPTION", key)
                   or not self._permits_catalog(key) for key in planned):
                raise SessionOwnerBlocked("PPI_STREAM_SUBSCRIPTION_CAPACITY_REVOKED")
            self._stream_invoked = True
        self._stream.run_market_only()

    def status(self) -> dict:
        with self._lock:
            return {"schema": SCHEMA, "state": self._state,
                    "login_invocations_by_owner": self._login_invocations,
                    "stream_invoked": self._stream_invoked,
                    "stream_state": self._stream.status() if self._stream else "NOT_BOUND",
                    "provider_quota": "NO_VERIFICADO",
                    "watch_usage": "NO_VERIFICADO",
                    "catalog_independent_attestation": "EXTERNAL_GATE_ONLY_UNVERIFIED",
                    "same_reader_rest_stream_binding": self._state == "OPEN",
                    "provider_signed_session": False,
                    "paper_ready": False, "real_order_routes_exposed": False}

    def close(self) -> None:
        with self._lock:
            if self._state != "OPEN":
                raise SessionOwnerBlocked("PPI_SESSION_OWNER_NOT_OPEN")
            if self._stream_invoked:
                # SDK lacks proven stop/unsubscribe. Closing the process-wide
                # HTTP guard while a socket may reconnect is not safe.
                raise SessionOwnerBlocked("PPI_STREAM_SAFE_STOP_NOT_VERIFIED")
            self._reader.close()
            self._reader = None
            self._rest = None
            self._stream = None
            self._local_generation = None
            self._state = "CLOSED"
            with type(self)._registry_lock:
                if type(self)._active_owner is self:
                    type(self)._active_owner = None


__all__ = ["PPISharedSessionOwner", "SessionOwnerBlocked", "SCHEMA"]
