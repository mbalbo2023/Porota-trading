"""OFFHOST composition contract for one authenticated PPI market-data owner.

Importing this module never imports/creates a PPI SDK, starts a socket, reads
a provider or grants quota. The isolated PAPER gateway injects exactly one
ProductionMarketReader and existing observed-budget/canonical-catalog guards.
There is no inferred PPI Watch entitlement or provider quota.

Only the guarded acquisition owner can GET. Consumers dispatch bounded native
and auxiliary requests to the one prioritized owner wire worker. The SDK's
unsubscribe/stop lifecycle is NOT proven; after starting a socket, this owner
cannot claim to release its process-wide HTTP guard safely.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import date, datetime, timezone
from contextlib import closing
from pathlib import Path
from math import ceil
from typing import Any, Callable

from rc6_ppi_shared_feed import (
    NATIVE_ACQUIRE_SCHEMA,
    _canonical_bytes,
    PPIReadOnlyNativeReceiptStore,
    PPISharedMarketFeed,
    ReadOnlyUnixFeedServer,
)
from rc6_ppi_shared_producer import PPISharedSDKProducer
from rc6_ppi_shared_rest_producer import MarketOnlyRESTProducer
from bd_ppi_readonly_guard import MARKET_CONFIGURATION_FIELDS
from rc6_ppi_global_budget import BudgetBackpressure, PAPER_DIRECT_WIRE_BACKPRESSURE


SCHEMA = "rc6.ppi.single-market-session.offhost.v1"
AUTH_COOLDOWN_SECONDS = 900
_GUARDED_HTTP_ERRORS = {f"PPI_HTTP_{status}": status for status in (401, 403, 429)}


def _complete_catalog_publication(connection):
    if connection.execute("""SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='candidate_refresh_state'""").fetchone() is None:
        return None
    row = connection.execute("""SELECT checked_at FROM candidate_refresh_state
        WHERE id=1 AND state='COMPLETE' AND typeof(checked_at)='text'
          AND length(checked_at) BETWEEN 1 AND 64""").fetchone()
    return row[0] if row else None


def complete_ppi_catalog_publication(database):
    """Poll the existing completed catalog marker without scanning identities."""
    from cg_paper_workspace import checked_path

    path = checked_path(database)
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                     timeout=.15)) as connection:
            return _complete_catalog_publication(connection)
    except (sqlite3.Error, OSError) as exc:
        raise ValueError("PPI_PRIMARY_CATALOG_UNAVAILABLE") from exc


def load_primary_ppi_catalog_snapshot(database, *, include_diagnostic_candidates=False,
                                      expected_publication=None):
    """Read PPI-primary identities without equating a GET request to a contract.

    Default preserves the existing PPI_FIELD-only certified-key contract.
    Gateway bootstrap may separately include unique REQUEST_CANDIDATE rows
    whose actual SearchInstrument response attests only the four PPI fields.
    The fifth coordinate is a local query parameter, NEVER PPI_FIELD/READY.
    SQLite is read-only, bounded and cannot create a database or permit orders.
    When diagnostic mode is requested, return (catalog4, query5, digest,
    provider_attested5); the last two sets remain deliberately distinct.
    """
    from cg_paper_workspace import checked_path, identity_from_connection
    from bu_instrument_catalog import (
        ppi_full_key_evidence_reason, canonical_family,
        canonical_market, canonical_settlement, cash_currency,
    )
    from rc6_ppi_shared_feed import CatalogIdentityError, _five

    path = checked_path(database)
    attested = []
    diagnostic = []
    request_aliases = {}
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                     timeout=.15)) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            if (expected_publication is not None
                    and expected_publication != _complete_catalog_publication(connection)):
                raise ValueError("PPI_PRIMARY_CATALOG_PUBLICATION_CHANGED")
            identity_from_connection(connection)
            state = connection.execute(
                "SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
            if state != ("PRODUCTION_PAPER", 0):
                raise ValueError("PPI_PRIMARY_PAPER_ZERO_UNVERIFIED")
            rows = connection.execute("""SELECT ticker,instrument_type,market,currency,
                settlement,settlement_source,status,
                CASE WHEN length(CAST(metadata_json AS BLOB))<=65536
                     THEN metadata_json ELSE NULL END
                FROM financial_instrument_catalog ORDER BY ticker,instrument_type,
                    market,currency,settlement LIMIT 20001""").fetchall()
            if len(rows) > 20000:
                raise ValueError("PPI_PRIMARY_CATALOG_TOO_LARGE")
            for row in rows:
                if row[6] not in ("AVAILABLE", "STALE"):
                    continue
                if any(value in (None, "") or len(str(value)) > 256
                       for value in row[:5]):
                    continue
                key = tuple(str(part).strip().upper() for part in row[:5])
                if any(part in ("", "UNKNOWN") for part in key):
                    continue
                if include_diagnostic_candidates:
                    request_aliases.setdefault(
                        (key[0], key[1], key[4]), set()).add(key)
                # Match the existing feed and wire contract before constructing
                # the single owner. Keep rejected rows in the alias census so
                # a collision cannot become a superficially unique request.
                try:
                    _five(key)
                except CatalogIdentityError:
                    continue
                record = {"ticker": row[0], "instrument_type": row[1],
                          "market": row[2], "currency": row[3],
                          "settlement": row[4], "settlement_source": row[5],
                          "metadata_json": row[7]}
                if (row[6] == "AVAILABLE" and row[5] == "PPI_FIELD"
                        and not ppi_full_key_evidence_reason(record)):
                    attested.append(key)
                    diagnostic.append(key)
                    continue
                if not include_diagnostic_candidates or row[5] != "REQUEST_CANDIDATE":
                    continue
                try:
                    provider = json.loads(row[7] or "{}")
                    if (not isinstance(provider, dict)
                            or provider.get("_discovery_source") != "PPI_PRIMARY"
                            or provider.get("settlement") not in (None, "")):
                        continue
                    observed_four = (
                        str(provider.get("ticker") or provider.get("symbol") or "").strip().upper(),
                        canonical_family(provider.get("type") or provider.get("instrumentType")),
                        canonical_market(provider.get("market")),
                        cash_currency(provider.get("currency")),
                    )
                    stored_four = (
                        str(row[0]).strip().upper(), canonical_family(row[1]),
                        canonical_market(row[2]), cash_currency(row[3]),
                    )
                    if (observed_four != stored_four or
                            canonical_settlement(row[4], row[1]) != key[4]):
                        continue
                except (ValueError, TypeError, OverflowError, RecursionError):
                    continue
                diagnostic.append(key)
    except (sqlite3.Error, OSError, TypeError) as exc:
        raise ValueError("PPI_PRIMARY_CATALOG_UNAVAILABLE") from exc
    keys = sorted(set(diagnostic if include_diagnostic_candidates else attested))
    if include_diagnostic_candidates:
        # SDK Current/Book requests contain only ticker, class and settlement.
        # Never select one currency/market from an ambiguous 3-key alias.
        keys = [key for key in keys if len(
            request_aliases.get((key[0], key[1], key[4]), ())) == 1]
    if not keys:
        raise ValueError("PPI_PRIMARY_CATALOG_EMPTY_OR_UNVERIFIED")
    four = sorted({key[:4] for key in keys})
    catalog_rows = [{"ticker": key[0], "type": key[1],
                     "market": key[2], "currency": key[3]} for key in four]
    provider_attested = sorted(set(attested).intersection(keys))
    digest_material = (
        {"source": "LOCAL_PPI_PRIMARY_DIAGNOSTIC_QUERY_ONLY",
         "query_keys": keys, "provider_attested_keys": provider_attested}
        if include_diagnostic_candidates else
        {"source": "LOCAL_PPI_PRIMARY_LITERAL_ONLY", "keys": keys}
    )
    digest = hashlib.sha256(json.dumps(digest_material, separators=(",", ":"),
                                     sort_keys=True).encode("utf-8")).hexdigest()
    if include_diagnostic_candidates:
        return catalog_rows, keys, digest, provider_attested
    return catalog_rows, keys, digest


class PrimaryPPICatalogAuthority:
    """Separate provider-attested contract keys from diagnostic GET identities.

    A query key may contain settlement from an explicit REQUEST, which is not
    PPI_FIELD evidence. Only the attested set may authorize the stronger
    PPI_FIVE_KEY_CATALOG_ATTESTED action. Neither action grants PAPER READY.
    """

    def __init__(self, five_keys, *, query_keys=None):
        self._keys = frozenset(tuple(key) for key in five_keys)
        self._query_keys = self._keys | frozenset(
            tuple(key) for key in (query_keys or ()))
        if not self._query_keys:
            raise ValueError("PPI_PRIMARY_CATALOG_EMPTY_OR_UNVERIFIED")

    def permits(self, action, *, five_key=None):
        if action == "PPI_FIVE_KEY_CATALOG_ATTESTED":
            allowed = self._keys
        elif action == "PPI_NATIVE_QUERY_IDENTITY_UNIQUE":
            allowed = self._query_keys
        else:
            return False
        return bool(allowed) if five_key is None else tuple(five_key) in allowed


class ObservedPaperCapacityAuthority:
    """Existing guarded PAPER-zero budget, never guessed PPI/Watch capacity.

    Login consumes the SDK's guarded authentication path. Each native GET is
    independently admitted by PaperDirectReadBudget.acquire/wire_scope/start/
    finish, whose real HTTP status (including 429) drives the breaker. This
    gate only confirms PAPER-zero and the correct existing guarded budget.
    """

    provider_quota = "UNKNOWN"

    def __init__(self, budget):
        from rc6_ppi_global_budget import PaperDirectReadBudget
        if not isinstance(budget, PaperDirectReadBudget):
            raise ValueError("PPI_OBSERVED_PAPER_BUDGET_REQUIRED")
        self.budget = budget

    def permits(self, action, *, five_key=None):
        if action not in {"OWNER_AUTHENTICATE", "REST_NATIVE_ACQUIRE",
                          "REST_AUXILIARY_ACQUIRE"}:
            return False  # STREAM entitlement must be separately established
        if action == "REST_NATIVE_ACQUIRE" and (
                not isinstance(five_key, (list, tuple)) or len(five_key) != 5):
            return False
        try:
            self.budget._require_paper()
            return True
        except Exception:
            return False

# A consumer role is not a permission to self-promote a wire request to EXIT.
# The owner, not the consumer, enforces the method/priority pair before GET.
_NATIVE_DEMAND_POLICY = {
    "scanner": {
        "current": frozenset({"OPENED_CRITICAL", "STRATEGY_HOT", "HOT", "WARM", "DISCOVERY"}),
        "book": frozenset({"OPENED_CRITICAL", "STRATEGY_HOT", "HOT", "WARM", "DISCOVERY"}),
    },
    "exit_reader": {"book": frozenset({"EXIT_CRITICAL"})},
    "scalping": {"intraday": frozenset({"OPENED_CRITICAL", "SCALPING_HOT", "WARM", "DISCOVERY"})},
}


class SessionOwnerBlocked(RuntimeError):
    """A missing independent authority or unsafe lifecycle denies operation."""


class _CompositeStreamAuthority:
    """Recheck all three independent gates at every async SDK callback.

    The SDK connect callback may happen after the original capacity decision.
    This wraps the unchanged A678 producer's permits(action, five_key) API.
    It creates no SDK/client/login/order/provider request.
    """

    def __init__(self, *, stream, capacity, catalog):
        self._stream = stream
        self._capacity = capacity
        self._catalog = catalog

    @staticmethod
    def _allow(authority, action, identity=None) -> bool:
        try:
            return authority.permits(action, five_key=identity) is True
        except Exception:
            return False

    def permits(self, action, *, five_key=None) -> bool:
        if not self._allow(self._stream, action, five_key):
            return False
        if action == "SUBSCRIBE_MARKET_DATA":
            return (five_key is not None
                    and self._allow(self._capacity, "STREAM_SUBSCRIPTION", five_key)
                    and self._allow(self._catalog, "PPI_FIVE_KEY_CATALOG_ATTESTED", five_key))
        if action in {"CONNECT_MARKET_DATA", "RECEIVE_MARKET_DATA"}:
            return (self._allow(self._capacity, "STREAM_CONNECT")
                    and self._allow(self._catalog, "PPI_FIVE_KEY_CATALOG_ATTESTED"))
        return False


class _CapacityCheckedStreamFeed:
    """Reject an already-wired symbol if its independent gate was revoked.

    The immutable A678 producer checks an unscoped RECEIVE authority BEFORE
    extracting a native five-key. This additional per-event gate re-resolves
    the bounded active plan (at most the local cap) before publication. The
    A678 producer separately requires an actually wired subscription, and
    the underlying G1 feed separately verifies native PPI catalog identity.
    """

    def __init__(self, *, feed, authority):
        self._feed = feed
        self._authority = authority

    def plan_subscription(self, five_key, *, priority):
        return self._feed.plan_subscription(five_key, priority=priority)

    def active_subscriptions(self):
        return self._feed.active_subscriptions()

    def publish_native_market_event(self, raw, **kwargs):
        if not isinstance(raw, dict):
            raise SessionOwnerBlocked("PPI_STREAM_EVENT_IDENTITY_UNVERIFIED")
        ticker, settlement, family = (raw.get("Ticker"), raw.get("Settlement"),
                                      raw.get("Type"))
        if (type(ticker) is not str or type(settlement) is not str
                or (family is not None and type(family) is not str)):
            raise SessionOwnerBlocked("PPI_STREAM_EVENT_IDENTITY_UNVERIFIED")
        t, s = ticker.strip().upper(), settlement.strip().upper()
        f = family.strip().upper() if family is not None else None
        matches = [key for key in self._feed.active_subscriptions()
                   if key[0] == t and key[4] == s
                   and (f is None or key[1] == f)]
        if len(matches) != 1:
            raise SessionOwnerBlocked("PPI_STREAM_EVENT_IDENTITY_AMBIGUOUS")
        if not self._authority.permits("SUBSCRIBE_MARKET_DATA", five_key=matches[0]):
            raise SessionOwnerBlocked("PPI_STREAM_EVENT_CAPACITY_REVOKED")
        return self._feed.publish_native_market_event(raw, **kwargs)


class PPISharedSessionOwner:
    """Process-local owner; account-wide uniqueness remains NO_VERIFICADO.

    `external_capacity_authority.permits` is a strict PAPER admission and
    guarded wire budget decision, never a claimed broker rate limit. Streaming
    retains an independent entitlement gate; the concrete PAPER budget only
    enables guarded REST. No PPI Watch account is consulted or modified.
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
                 socket_path: Path, max_subscriptions: int = 60,
                 settlement_signer=None):
        if not callable(reader_factory) or not callable(instrument_factory):
            raise SessionOwnerBlocked("PPI_SESSION_INJECTED_FACTORY_MISSING")
        if not callable(getattr(stream_authority, "permits", None)):
            raise SessionOwnerBlocked("PPI_STREAM_AUTHORITY_MISSING")
        # No provided external quota witness: fail closed BEFORE any SDK/login.
        if not callable(getattr(external_capacity_authority, "permits", None)):
            raise SessionOwnerBlocked("PPI_EXTERNAL_CAPACITY_AUTHORITY_MISSING")

        self._reader_factory = reader_factory
        self._instrument_factory = instrument_factory
        self._stream_authority = stream_authority
        self._capacity = external_capacity_authority
        self._socket_path = Path(socket_path)
        self._max_subscriptions = max_subscriptions
        self._settlement_signer = settlement_signer
        self._set_catalog(catalog_authority=catalog_authority,
                          ppi_catalog_rows=ppi_catalog_rows,
                          catalog_five_keys=catalog_five_keys,
                          catalog_source_digest=catalog_source_digest,
                          consumer_five_keys=consumer_five_keys)
        self._reader = None
        self._rest = None
        self._stream = None
        self._local_generation = None
        self._login_invocations = 0
        self._aux_hold_until = 0.0
        self._aux_retry_after = None
        self._aux_last_http_status = None
        self._native_hold_until = 0.0
        self._native_retry_after = None
        self._native_last_http_status = None
        self._session_recovery_at = None
        self._rate_limit_until = 0.0
        self._next_owner_refresh = 0.0
        self._refresh_attempted = False
        self._stream_invoked = False
        self._state = "NOT_OPEN"
        self._lock = threading.RLock()

    def _set_catalog(self, *, catalog_authority, ppi_catalog_rows,
                     catalog_five_keys, catalog_source_digest, consumer_five_keys):
        if not callable(getattr(catalog_authority, "permits", None)):
            raise SessionOwnerBlocked("PPI_PPI_CATALOG_AUTHORITY_MISSING")
        if not isinstance(consumer_five_keys, dict) or not consumer_five_keys:
            raise SessionOwnerBlocked("PPI_SESSION_CONSUMERS_MISSING")
        composite = _CompositeStreamAuthority(
            stream=self._stream_authority, capacity=self._capacity,
            catalog=catalog_authority)
        feed = PPISharedMarketFeed(
            ppi_catalog_rows=ppi_catalog_rows,
            catalog_source_digest=catalog_source_digest,
            max_subscriptions=self._max_subscriptions,
        )
        stream_feed = _CapacityCheckedStreamFeed(feed=feed, authority=composite)
        native = PPIReadOnlyNativeReceiptStore(feed)
        for consumer, keys in consumer_five_keys.items():
            feed.register_consumer(consumer, watched_five_keys=keys)
        rpc = ReadOnlyUnixFeedServer(
            feed, socket_path=self._socket_path, native_receipts=native,
            native_acquirer=self.dispatch_native_acquire, owner_status=self.status,
            auxiliary_acquirer=self.dispatch_auxiliary_acquire,
            exit_round_observer=self.observe_exit_round,
            exit_round_discarder=self.discard_exit_round_scope,
            supervisor_pid=os.getppid(),
        )
        self._catalog_authority = catalog_authority
        self._composite_stream_authority = composite
        self._feed = feed
        self._stream_feed = stream_feed
        self._native = native
        self._rpc = rpc
        self._catalog_five_keys = catalog_five_keys
        self._catalog_source_digest = catalog_source_digest

    @staticmethod
    def _aux_word(value):
        return (type(value) is str and 1 <= len(value) <= 64
                and all(32 <= ord(ch) <= 126 for ch in value)
                and not any(ch in value for ch in ("?", "&", "=", "\\")))

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

    def _permits_native_query(self, identity=None) -> bool:
        """Allow observe-only GET request identity; never infer a PPI contract.

        Existing injected authorities with only attested keys retain backward
        compatibility. The concrete runtime authority independently admits
        unique PPI-primary four-key / requested-settlement candidates.
        """
        try:
            if self._catalog_authority.permits(
                    "PPI_NATIVE_QUERY_IDENTITY_UNIQUE", five_key=identity) is True:
                return True
        except Exception:
            return False
        return self._permits_catalog(identity)

    def _permits_stream_connect(self) -> bool:
        try:
            return self._composite_stream_authority.permits("CONNECT_MARKET_DATA") is True
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
                raise SessionOwnerBlocked("PPI_PAPER_CAPACITY_OR_ENTITLEMENT_UNVERIFIED")
            if not self._permits_native_query():
                raise SessionOwnerBlocked("PPI_CATALOG_INDEPENDENT_AUTHORITY_UNVERIFIED")
            # Bind the process-wide slot to the base class itself.  Assigning
            # through type(self) would create a subclass-local _active_owner:
            # opening a subclass first could then let a base instance install
            # a second global HTTP guard and authenticated SDK reader.
            with PPISharedSessionOwner._registry_lock:
                if PPISharedSessionOwner._active_owner is not None:
                    raise SessionOwnerBlocked("PPI_PROCESS_ALREADY_HAS_MARKET_OWNER")
                PPISharedSessionOwner._active_owner = self
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
                self._bind_authenticated_reader(source)
                self._reader = source
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
                with PPISharedSessionOwner._registry_lock:
                    if PPISharedSessionOwner._active_owner is self:
                        PPISharedSessionOwner._active_owner = None
                raise

    def _bind_authenticated_reader(self, reader):
        transport = reader.market_only_sdk_transport(
            instrument_factory=self._instrument_factory)
        stream = PPISharedSDKProducer(
            feed=self._stream_feed, transport=transport,
            stream_authority=self._composite_stream_authority,
        )
        generation = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
        rest = MarketOnlyRESTProducer(
            guarded_source=reader, receipt_sink=self._native,
            catalog_five_keys=self._catalog_five_keys,
            catalog_source_digest=self._catalog_source_digest,
            session_fingerprint=generation,
        )
        self._stream = stream
        self._rest = rest
        self._local_generation = generation

    def begin_gateway_catalog_refresh(self) -> bool:
        """Wait for the current read, then stop admission on the old bundle."""
        with self._lock:
            self._require_open()
            if self._session_recovery_at is not None:
                return False  # a new guarded login will read the latest catalog
            if self._stream_invoked or self._feed.active_subscriptions():
                raise SessionOwnerBlocked("PPI_STREAM_CATALOG_REFRESH_UNVERIFIED")
            self._state = "CATALOG_REFRESH"
            return True

    def bind_refreshed_gateway_catalog(self, **catalog):
        """Replace all catalog-bound state after the old socket has drained."""
        with self._lock:
            if self._state != "CATALOG_REFRESH" or self._rpc.socket_bound:
                raise SessionOwnerBlocked("PPI_CATALOG_GATEWAY_DRAIN_UNVERIFIED")
            self._set_catalog(**catalog)
            if not self._permits_native_query():
                raise SessionOwnerBlocked("PPI_CATALOG_INDEPENDENT_AUTHORITY_UNVERIFIED")
            # Still the same authenticated SDK reader. Reset the AF_UNIX pin,
            # role registry and receipt namespace together with the digest.
            self._bind_authenticated_reader(self._reader)
            self._state = "OPEN"

    def _require_open(self):
        if self._state != "OPEN" or self._reader is None:
            raise SessionOwnerBlocked("PPI_SINGLE_SESSION_NOT_OPEN")

    @staticmethod
    def _observed_http_error(error):
        """Extract only status+bounded Retry-After; never return SDK body."""
        from requests import Response
        from bd_ppi_readonly_guard import ppi_retry_after_seconds

        current = error
        for _ in range(6):
            response = getattr(current, "response", None)
            if (response is None and len(current.args) == 1
                    and isinstance(current.args[0], Response)):
                # The SDK can raise Exception(response) in non-2xx responses.
                response = current.args[0]
            status = getattr(response, "status_code", None)
            if type(status) is int and 100 <= status <= 599:
                return status, ppi_retry_after_seconds(getattr(response, "headers", {}))
            current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
            if current is None:
                break
        return None, None

    def _require_active_rest_session(self) -> None:
        if self._session_recovery_at is not None:
            raise SessionOwnerBlocked("PPI_GATEWAY_SESSION_RECOVERY_PENDING")

    def session_recovery_due(self) -> bool:
        """The runtime may drain and replace this REST owner after its holds."""
        recover_at = self._session_recovery_at
        # Read-only and lock-free like status(). No new provider request is
        # admitted once authentication is invalidated under the owner lock.
        return (recover_at is not None and not self._stream_invoked
                and time.monotonic() >= max(recover_at, self._native_hold_until,
                                            self._aux_hold_until, self._rate_limit_until))

    def refresh_invalid_session(self):
        """Try one refresh on the current reader after a guarded 401."""
        with self._lock:
            now = time.monotonic()
            if (self._session_recovery_at is None or self._stream_invoked or self._refresh_attempted
                    or now < max(self._next_owner_refresh, self._rate_limit_until)):
                return None
            self._refresh_attempted = True
            refresh = getattr(self._reader, "refresh_market_session_once", None)
            if not callable(refresh) or not self._permits_capacity("OWNER_AUTHENTICATE"):
                return "PPI_OWNER_REFRESH_UNAVAILABLE"
            recovered, status, retry_after = refresh()
            if recovered:
                self._session_recovery_at = None
                if self._native_last_http_status == 401:
                    self._native_hold_until = 0.0
                if self._aux_last_http_status == 401:
                    self._aux_hold_until = 0.0
                self._next_owner_refresh = now + AUTH_COOLDOWN_SECONDS
                self._refresh_attempted = False
                return "PPI_OWNER_REFRESH_SUCCESS"
            if status == 429:
                self._provider_hold(status, retry_after)
            return f"PPI_OWNER_REFRESH_HTTP_{status}" if status in {401, 403, 429} else "PPI_OWNER_REFRESH_UNVERIFIED"

    def _provider_hold(self, status, retry_after):
        deadline = time.monotonic() + max(
            AUTH_COOLDOWN_SECONDS if status in {401, 403} else 60, retry_after or 0)
        if status == 401:
            self._session_recovery_at = deadline
        if status == 429:
            self._rate_limit_until = max(self._rate_limit_until, deadline)
        return deadline

    def _guarded_wire_advanced(self, before):
        after = getattr(self._reader, "metrics", {}).get("http_allowed")
        return type(before) is int and type(after) is int and after > before

    def _guarded_provider_error(self, wire_calls):
        if self._guarded_wire_advanced(wire_calls):
            outcome = getattr(self._reader, "last_provider_response", None)
            if isinstance(outcome, tuple) and len(outcome) == 2 and outcome[0] in {401, 403, 429}:
                return outcome
        return None

    def capture_native(self, method: str, five_key: Any, *, priority: str):
        """Only owner path to guarded PPI REST; no consumer-facing GET."""
        with self._lock:
            self._require_open()
            self._require_active_rest_session()
            if not self._permits_native_query(five_key):
                raise SessionOwnerBlocked("PPI_CATALOG_INDEPENDENT_AUTHORITY_UNVERIFIED")
            if not self._permits_capacity("REST_NATIVE_ACQUIRE", five_key):
                raise SessionOwnerBlocked("PPI_PAPER_CAPACITY_OR_ENTITLEMENT_UNVERIFIED")
            if time.monotonic() < max(self._native_hold_until, self._rate_limit_until):
                raise SessionOwnerBlocked("PPI_NATIVE_PROVIDER_BACKOFF_ACTIVE")
            wire_calls = getattr(self._reader, "metrics", {}).get("http_allowed")
            try:
                return self._rest.capture(method, five_key, priority=priority)
            except Exception as exc:
                status, retry_after = self._observed_http_error(exc)
                guarded = self._guarded_provider_error(wire_calls)
                if guarded is not None:
                    status, retry_after = guarded
                # SDK retry exceptions can hide the first HTTP response. The
                # guard records that status on this GET worker's thread.
                elif self._guarded_wire_advanced(wire_calls):
                    guarded_status = _GUARDED_HTTP_ERRORS.get(
                        getattr(self._reader, "last_read_error_code", None))
                    if guarded_status is not None and guarded_status != status:
                        status, retry_after = guarded_status, None
                if status in {401, 403, 429}:
                    self._native_last_http_status = status
                    self._native_retry_after = retry_after if status == 429 else None
                    self._native_hold_until = self._provider_hold(status, retry_after)
                    raise SessionOwnerBlocked(f"PPI_NATIVE_HTTP_{status}") from None
                raise

    def dispatch_native_acquire(self, *, consumer: str, method: str,
                                five_key: Any, priority: str) -> dict:
        """One explicitly authorized native demand, executed in this owner.

        The existing native_read/pull RPCs remain strictly cache-only. This
        method is the acquisition side of a future authenticated AF_UNIX
        boundary; it never trusts a claimed consumer priority or invents
        provider quota. An absent/revoked independent capacity gate denies
        before touching the guarded PPI SDK.
        """
        with self._lock:
            self._require_open()
            policy = _NATIVE_DEMAND_POLICY.get(consumer) if type(consumer) is str else None
            if policy is None or method not in policy:
                raise SessionOwnerBlocked("PPI_NATIVE_DEMAND_METHOD_DENIED")
            if type(priority) is not str or priority not in policy[method]:
                raise SessionOwnerBlocked("PPI_NATIVE_DEMAND_ROLE_DENIED")
            if (not isinstance(five_key, (list, tuple)) or len(five_key) != 5
                    or any(type(part) is not str for part in five_key)):
                raise SessionOwnerBlocked("PPI_NATIVE_DEMAND_IDENTITY_INVALID")
            identity = tuple(five_key)
            allowed = self._feed._consumers.get(consumer, False)
            if allowed is False or (allowed is not None and identity not in allowed):
                raise SessionOwnerBlocked("PPI_NATIVE_DEMAND_IDENTITY_DENIED")
            response = self.capture_native(method, identity, priority=priority)
            proof = None
            if (self._settlement_signer is not None and consumer == "scanner"
                    and priority == "DISCOVERY" and not self._permits_catalog(identity)):
                proof = self._settlement_signer.observe(
                    method, identity, response, generation=self._local_generation,
                    catalog_digest=self._catalog_source_digest)
            return {"schema": NATIVE_ACQUIRE_SCHEMA,
                    "state": "OK", "receipt": response["native_receipt"],
                    "guard_wire_receipt": response["guard_wire_receipt"],
                    **({"settlement_proof": proof} if proof else {}),
                    "owner_pid": os.getpid(),
                    "session_generation": self._local_generation,
                    "catalog_source_digest": self._catalog_source_digest,
                    "provider_calls_by_rpc": 1,
                    "paper_ready": False, "order_route": "NOT_PRESENT"}

    def dispatch_auxiliary_acquire(self, *, consumer: str, method: str,
                                   args: Any) -> dict:
        """Guard-listed Search/History/Configuration on the SAME SDK owner.

        These legacy read-only endpoints do not borrow native REST wire
        receipts or a numeric provider allowance. Results are bounded local
        observations and never PAPER-ready; only scanner can demand them.
        """
        with self._lock:
            self._require_open()
            if consumer != "scanner":
                raise SessionOwnerBlocked("PPI_AUX_ROLE_DENIED")
            self._require_active_rest_session()
            if method not in {"search_instruments", "history", "market_configuration"}:
                raise SessionOwnerBlocked("PPI_AUX_METHOD_DENIED")
            if not isinstance(args, dict):
                raise SessionOwnerBlocked("PPI_AUX_ARGUMENTS_INVALID")
            if method == "market_configuration":
                if args and (set(args) != {"field"}
                             or args["field"] not in MARKET_CONFIGURATION_FIELDS):
                    raise SessionOwnerBlocked("PPI_AUX_ARGUMENTS_INVALID")
                identity = None
            elif method == "search_instruments":
                if (set(args) != {"ticker", "instrument_type", "name", "market"}
                        or any(not self._aux_word(value) for value in args.values())):
                    raise SessionOwnerBlocked("PPI_AUX_ARGUMENTS_INVALID")
                if (args["ticker"].strip().upper() == "CAUCION"
                        and args["instrument_type"].strip().upper() == "CAUCIONES"
                        and args["name"].strip().upper() == "CAUCION"):
                    # ProductionMarketReader expands this legacy alias to
                    # 2 x N provider GETs. A single queued RPC must never
                    # hide that batch from EXIT priority arbitration; the
                    # scanner adapter splits it into unit search RPCs.
                    raise SessionOwnerBlocked("PPI_AUX_CAUCION_BULK_DENIED")
                identity = None  # discovery never certifies a five-key
            else:
                if set(args) != {"ticker", "instrument_type", "settlement", "date_from", "date_to"}:
                    raise SessionOwnerBlocked("PPI_AUX_ARGUMENTS_INVALID")
                if any(not self._aux_word(args[key]) for key in (
                        "ticker", "instrument_type", "settlement")):
                    raise SessionOwnerBlocked("PPI_AUX_ARGUMENTS_INVALID")
                try:
                    first = date.fromisoformat(args["date_from"])
                    last = date.fromisoformat(args["date_to"])
                except (ValueError, TypeError):
                    raise SessionOwnerBlocked("PPI_AUX_HISTORY_DATES_INVALID") from None
                if not 0 <= (last - first).days <= 366:
                    raise SessionOwnerBlocked("PPI_AUX_HISTORY_RANGE_UNBOUNDED")
                candidates = [key for key in self._catalog_five_keys
                              if (key[0], key[1], key[4]) == (
                                  args["ticker"], args["instrument_type"],
                                  args["settlement"])]
                if len(candidates) != 1 or not self._permits_catalog(candidates[0]):
                    raise SessionOwnerBlocked("PPI_AUX_HISTORY_IDENTITY_AMBIGUOUS")
                identity = candidates[0]
            if not self._permits_capacity("REST_AUXILIARY_ACQUIRE", identity):
                raise SessionOwnerBlocked("PPI_AUX_PAPER_CAPACITY_UNVERIFIED")
            if time.monotonic() < max(self._aux_hold_until, self._rate_limit_until):
                raise SessionOwnerBlocked("PPI_AUX_PROVIDER_BACKOFF_ACTIVE")
            wire_calls = getattr(self._reader, "metrics", {}).get("http_allowed")
            prior_guard_code = getattr(self._reader, "last_read_error_code", None)
            try:
                if method == "market_configuration":
                    result = self._reader.market_configuration(**args)
                elif method == "search_instruments":
                    result = self._reader.search_instruments(
                        args["ticker"], args["instrument_type"],
                        name=args["name"], market=args["market"])
                else:
                    result = self._reader.history(
                        args["ticker"], args["instrument_type"],
                        args["settlement"], first, last)
            except Exception as exc:
                status, retry_after = self._observed_http_error(exc)
                guarded = self._guarded_provider_error(wire_calls)
                if guarded is not None:
                    status, retry_after = guarded
                elif status is None and self._guarded_wire_advanced(wire_calls):
                    guard_code = getattr(self._reader, "last_read_error_code", None)
                    # The auxiliary SDK methods do not clear this diagnostic.
                    # An unchanged HTTP code may belong to another RPC.
                    if guard_code != prior_guard_code:
                        status = _GUARDED_HTTP_ERRORS.get(guard_code)
                    if (guard_code is None and type(exc) is Exception
                            and exc.args == ("Unauthorized",)):
                        # PPIClient.get in SDK 1.3.0 uses this exact exception
                        # for an empty 401. Never override a guarded HTTP code.
                        status = 401
                if status in {401, 403, 429}:
                    self._aux_last_http_status = status
                    self._aux_retry_after = retry_after if status == 429 else None
                    self._aux_hold_until = self._provider_hold(status, retry_after)
                    raise SessionOwnerBlocked(f"PPI_AUX_HTTP_{status}") from None
                if isinstance(exc, BudgetBackpressure):
                    reason = str(exc)
                    if reason in PAPER_DIRECT_WIRE_BACKPRESSURE:
                        raise SessionOwnerBlocked(reason) from None
                # SDK exceptions can embed provider text/credentials.
                raise SessionOwnerBlocked("PPI_AUX_PROVIDER_FAILURE_UNVERIFIED") from None
            try:
                body = _canonical_bytes(result)
            except (ValueError, TypeError, OverflowError) as exc:
                raise SessionOwnerBlocked("PPI_AUX_RESPONSE_INVALID") from exc
            if not 1 <= len(body) <= 100_000:
                raise SessionOwnerBlocked("PPI_AUX_RESPONSE_UNBOUNDED")
            return {"schema": NATIVE_ACQUIRE_SCHEMA, "state": "OK",
                    "method": method, "result": json.loads(body),
                    "result_sha256": hashlib.sha256(body).hexdigest(),
                    "owner_pid": os.getpid(),
                    "session_generation": self._local_generation,
                    "provider_calls_by_rpc": "UNKNOWN",
                    "provider_quota": "UNKNOWN", "paper_ready": False,
                    "order_route": "NOT_PRESENT"}

    def start_gateway(self) -> None:
        """Bind a private data-only Unix socket after one authenticated login."""
        with self._lock:
            self._require_open()
            self._rpc.start()

    def observe_exit_round(self, *, consumer, elapsed_seconds, deadline_seconds,
                           failures=0) -> dict:
        """Same SDK reader + same router thread as EXIT Book's budget scope.

        `PaperDirectReadBudget` owns the COMPLETE/DEGRADED verdict. No local
        fake COMPLETE, second budget, account query or provider GET occurs.
        """
        with self._lock:
            self._require_open()
            if consumer != "exit_reader":
                raise SessionOwnerBlocked("PPI_EXIT_ROUND_ROLE_DENIED")
            if not self._permits_capacity("OWNER_AUTHENTICATE"):
                raise SessionOwnerBlocked("PPI_EXIT_ROUND_PAPER_ZERO_UNVERIFIED")
            report = getattr(self._reader, "observe_exit_round", None)
            if not callable(report):
                raise SessionOwnerBlocked("PPI_EXIT_ROUND_BUDGET_UNAVAILABLE")
            return report(elapsed_seconds=elapsed_seconds,
                          deadline_seconds=deadline_seconds, failures=failures)

    def discard_exit_round_scope(self, *, consumer) -> None:
        """Reset owner's temporal EXIT budget scope on its GET worker thread."""
        with self._lock:
            self._require_open()
            if consumer != "exit_reader":
                raise SessionOwnerBlocked("PPI_EXIT_ROUND_ROLE_DENIED")
            discard = getattr(self._reader, "discard_exit_round_scope", None)
            if not callable(discard):
                raise SessionOwnerBlocked("PPI_EXIT_ROUND_BUDGET_UNAVAILABLE")
            discard()

    def serve_gateway_forever(self) -> None:
        self._rpc.serve_forever()

    def shutdown_gateway(self) -> None:
        self._rpc.shutdown()

    def close_gateway(self) -> None:
        self._rpc.close()

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
                raise SessionOwnerBlocked("PPI_PAPER_CAPACITY_OR_ENTITLEMENT_UNVERIFIED")
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
        # Status is telemetry, not admission. It must not acquire the owner
        # RLock held through an SDK GET (connect/read timeout can be 40s), or
        # the parent 400ms registration probe and child PID census will fail
        # while the only owner is healthy. Snapshot immutable references under
        # the GIL and detect a concurrent lifecycle transition fail-closed.
        state = self._state
        reader = self._reader
        stream = self._stream
        generation = self._local_generation
        catalog_digest = self._catalog_source_digest
        try:
            guard_metrics = getattr(reader, "metrics", None) if reader else None
        except Exception:
            guard_metrics = None
        guard_metrics = guard_metrics if isinstance(guard_metrics, dict) else {}

        def observed_count(name):
            value = guard_metrics.get(name)
            return value if type(value) is int and value >= 0 else None

        stable = (state == self._state and reader is self._reader
                  and stream is self._stream and generation == self._local_generation
                  and catalog_digest == self._catalog_source_digest)
        if not stable:
            state = "TRANSITION_UNVERIFIED"
        signer = self._settlement_signer if state == "OPEN" else None
        native_backoff = max(0, ceil(max(self._native_hold_until, self._rate_limit_until)
                                     - time.monotonic()))
        wire_backoff = getattr(reader, "wire_backoff_remaining_seconds", 0)
        return {"schema": SCHEMA, "state": state,
                "owner_pid": os.getpid(),
                **({"settlement_signer_public_key": signer.public_key,
                    "settlement_signer_release": signer.release} if signer else {}),
                "session_generation": generation if stable else None,
                "catalog_source_digest": catalog_digest if stable else None,
                "aux_retry_after_seconds_observed": self._aux_retry_after,
                "aux_last_http_status": self._aux_last_http_status,
                "native_retry_after_seconds_observed": self._native_retry_after,
                "native_last_http_status": self._native_last_http_status,
                "native_backoff_remaining_seconds": (
                    max(native_backoff, wire_backoff if type(wire_backoff) is int else 0)
                    if stable else None),
                "session_recovery_pending": self._session_recovery_at is not None,
                "login_invocations_by_owner": self._login_invocations,
                # Guard counts come from the single real HTTP transport,
                # not one-per-RPC guesses or consumer-local zeros. The
                # login counter denotes HTTPAdapter.send observations.
                "guard_login_wire_attempts": observed_count("login_calls") if stable else None,
                "guard_http_allowed": observed_count("http_allowed") if stable else None,
                "guard_http_blocked": observed_count("http_blocked") if stable else None,
                "stream_invoked": self._stream_invoked,
                "stream_state": stream.status() if stream else "NOT_BOUND",
                "provider_quota": "UNKNOWN",
                "provider_rate_limit_response_observed": (
                    getattr(reader, "last_rate_limit_observation", None) if stable else None),
                "watch_usage": "NO_VERIFICADO",
                "catalog_independent_attestation": "EXTERNAL_GATE_ONLY_UNVERIFIED",
                "same_reader_rest_stream_binding": stable and state == "OPEN",
                "provider_signed_session": False,
                "paper_ready": False, "real_order_routes_exposed": False}

    def close(self) -> None:
        with self._lock:
            if self._state not in {"OPEN", "CATALOG_REFRESH"}:
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
            with PPISharedSessionOwner._registry_lock:
                if PPISharedSessionOwner._active_owner is self:
                    PPISharedSessionOwner._active_owner = None


__all__ = ["PPISharedSessionOwner", "SessionOwnerBlocked", "SCHEMA"]
