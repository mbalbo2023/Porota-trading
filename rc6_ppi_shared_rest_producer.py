"""A680: fail-closed, read-only PPI REST receipt producer contract.

No PPI SDK import, login, requests, network, threads or order/account API.
Only the single acquisition-worker owner may inject an existing PPI read-only
guard source that emits an observed LOCAL wire receipt from HTTPAdapter.send.
The receipt is not signed by PPI, does not prove catalog identity/market and
does not certify PAPER readiness. The integration is separately gated.

The source, not this producer nor a Unix RPC consumer, owns precisely one
RuntimePPIBudget.acquire/start/wire_scope/finish per real HTTP wire attempt.
No consumer can re-fetch or claim budget authority through the receipt sink.
Search/history/market_configuration are intentionally blocked until an exact
PPI endpoint budget/read-only contract is separately proved.

Official SDK reference: https://itatppi.github.io/ppi-official-api-docs/api/documentacionPython/
Product source: bd_ppi_readonly_guard.ProductionMarketReader and
ReadOnlyTransportGuard.guarded_send (A678 architecture issue #909).
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any


SCHEMA = "rc6.ppi.rest-producer.offhost.v1"
WIRE_WITNESS_SCHEMA = "RC6_PPI_NATIVE_WIRE_RECEIPT_V1"
ALLOWED_METHODS = frozenset({"current", "book", "intraday"})
UNBUDGETED_METHODS = frozenset({"search_instruments", "history", "market_configuration"})
_PRIORITIES = frozenset({"EXIT_CRITICAL", "WATCH_CRITICAL", "OPENED_CRITICAL",
                         "SCALPING_HOT", "STRATEGY_HOT", "HOT", "WARM", "DISCOVERY"})
_TEXT = re.compile(r"[A-Z0-9][A-Z0-9./_-]{0,63}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MAX_RAW_BYTES = 65_536
_SENSITIVE_TOP_LEVEL = frozenset({"account", "accounts", "orders", "transfers",
                                  "credentials", "api_key", "api_secret", "token",
                                  "authorization", "password", "access_token"})


class RESTProducerBlocked(RuntimeError):
    """No read or native receipt publication permitted under current evidence."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _timestamp(value: Any, *, name: str) -> datetime:
    try:
        if isinstance(value, datetime):
            result = value
        elif isinstance(value, str) and len(value) <= 64:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            raise ValueError("type")
    except (ValueError, TypeError) as exc:
        raise RESTProducerBlocked(f"PPI_REST_{name}_INVALID") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise RESTProducerBlocked(f"PPI_REST_{name}_NAIVE")
    return result.astimezone(timezone.utc)


def _five_key(value: Any) -> tuple[str, str, str, str, str]:
    if not isinstance(value, (list, tuple)) or len(value) != 5:
        raise RESTProducerBlocked("PPI_REST_FIVE_KEY_INVALID")
    items = []
    for item in value:
        if not isinstance(item, str) or not _TEXT.fullmatch(item.strip().upper()):
            raise RESTProducerBlocked("PPI_REST_FIVE_KEY_INVALID")
        items.append(item.strip().upper())
    return tuple(items)


def _native_timestamp(method: str, raw: Any) -> datetime:
    """Use documented original REST `date`; never substitute received time.

    PPI's REST SDK examples use lower-case `date`, `bids`/`offers` whereas the
    streaming callback uses capitalized `Date`/`Bids`/`Offers`. Intraday is a
    LIST of rows. This contract preserves both native shapes, without trying
    to turn an intraday tick into a closed candle.
    """
    if method == "intraday":
        if not isinstance(raw, list) or not 1 <= len(raw) <= 512:
            raise RESTProducerBlocked("PPI_REST_INTRADAY_NATIVE_LIST_REQUIRED")
        times = []
        for row in raw:
            if not isinstance(row, dict):
                raise RESTProducerBlocked("PPI_REST_INTRADAY_NATIVE_ROW_INVALID")
            times.append(_timestamp(row.get("date", row.get("Date")), name="NATIVE_TIME"))
        return max(times)
    if not isinstance(raw, dict):
        raise RESTProducerBlocked("PPI_REST_NATIVE_OBJECT_REQUIRED")
    return _timestamp(raw.get("date", raw.get("Date")), name="NATIVE_TIME")


def _validate_guard_witness(witness: Any, *, method: str, priority: str,
                            five_key: tuple[str, ...], raw_sha256: str) -> tuple[str, datetime, int]:
    """Validate an observed guard-owned attempt; this is NOT PPI-signed data.

    Only the guarded source in the SAME worker can supply the receipt.  Its
    binding to the transport and a real wire lease is a G3 integration gate,
    not implied by this OFFHOST structural check. No second signature or key.
    """
    if (not isinstance(witness, dict) or set(witness) != {
            "schema", "endpoint", "consumer", "priority",
            "request_identity_3key", "claimed_five_key", "adapter_send_invoked",
            "adapter_response_completed", "http_status_code", "raw_body_sha256",
            "parsed_body_sha256", "received_at_utc", "receive_monotonic_ns",
            "budget_lease_sha256", "wire_attempt_id", "guard_budget_receipt_sha256",
            "http_method", "identity_source"}):
        raise RESTProducerBlocked("PPI_REST_GUARD_WITNESS_MISSING")
    if (witness["schema"] != WIRE_WITNESS_SCHEMA or witness["endpoint"] != method
            or not isinstance(witness["consumer"], str) or not witness["consumer"]
            or witness["priority"] != priority
            or witness["http_method"] != "GET"
            or witness["identity_source"] != "CALLER_ONLY_UNVERIFIED"
            or not isinstance(witness["request_identity_3key"], (list, tuple))
            or tuple(witness["request_identity_3key"]) != (five_key[0], five_key[1], five_key[4])
            or not isinstance(witness["claimed_five_key"], (list, tuple))
            or tuple(witness["claimed_five_key"]) != five_key
            or witness["adapter_send_invoked"] is not True
            or witness["adapter_response_completed"] is not True
            or type(witness["http_status_code"]) is not int
            or witness["http_status_code"] != 200
            or witness["parsed_body_sha256"] != raw_sha256):
        raise RESTProducerBlocked("PPI_REST_GUARD_WITNESS_CONTEXT_MISMATCH")
    if not (all(isinstance(witness[field], str) and _SHA.fullmatch(witness[field])
                for field in ("raw_body_sha256", "budget_lease_sha256", "guard_budget_receipt_sha256"))
            and isinstance(witness["wire_attempt_id"], str)
            and 1 <= len(witness["wire_attempt_id"]) <= 128):
        raise RESTProducerBlocked("PPI_REST_BUDGET_RECEIPT_UNVERIFIED")
    received = _timestamp(witness["received_at_utc"], name="RECEIVED_TIME")
    monotonic = witness["receive_monotonic_ns"]
    if type(monotonic) is not int or monotonic <= 0:
        raise RESTProducerBlocked("PPI_REST_MONOTONIC_INVALID")
    return witness["guard_budget_receipt_sha256"], received, monotonic


class MarketOnlyRESTProducer:
    """One-reader acquisition-owner adapter; publishes no synthetic PAPER proof.

    ``guarded_source.capture_guarded_native`` performs GET only through
    ProductionMarketReader/ReadOnlyTransportGuard, returning a LOCAL per-wire
    receipt. It does not establish a PPI signature or authentic PIT evidence.

    Calls to this class never allocate budget leases. No REST catch-up fallback
    occurs in scanner, EXIT, scalping or the native reader IPC.
    """

    def __init__(self, *, guarded_source: Any, receipt_sink: Any,
                 catalog_five_keys: list[tuple[str, ...]],
                 catalog_source_digest: str, session_fingerprint: str):
        if (not isinstance(catalog_source_digest, str)
                or not _SHA.fullmatch(catalog_source_digest)):
            raise RESTProducerBlocked("PPI_REST_CATALOG_DIGEST_INVALID")
        if not isinstance(catalog_five_keys, list) or not 1 <= len(catalog_five_keys) <= 20_000:
            raise RESTProducerBlocked("PPI_REST_CATALOG_UNBOUNDED_OR_EMPTY")
        catalog = [_five_key(item) for item in catalog_five_keys]
        if len(catalog) != len(set(catalog)):
            raise RESTProducerBlocked("PPI_REST_CATALOG_DUPLICATE_IDENTITY")
        if not callable(getattr(receipt_sink, "publish_native_rest_receipt", None)):
            raise RESTProducerBlocked("PPI_REST_RECEIPT_SINK_MISSING")
        if not isinstance(session_fingerprint, str) or not _SHA.fullmatch(session_fingerprint):
            raise RESTProducerBlocked("PPI_REST_SESSION_FINGERPRINT_UNVERIFIED")
        self._source = guarded_source
        self._sink = receipt_sink
        self._catalog = frozenset(catalog)
        self._catalog_digest = catalog_source_digest
        self._session = session_fingerprint  # Local owner generation, NOT PPI-signed.

    def capture(self, method: str, five_key: Any, *, priority: str) -> dict:
        """Fail closed BEFORE any source call if endpoint or guard proof absent.

        Only an external guard-owned source may issue a real read.  The source
        returns native PPI data + a witness, and only then can a separate
        bounded receipt sink publish immutable market observations.
        """
        if method in UNBUDGETED_METHODS:
            raise RESTProducerBlocked("PPI_REST_ENDPOINT_NO_BUDGET_AUTHORITY")
        if method not in ALLOWED_METHODS:
            raise RESTProducerBlocked("PPI_REST_ENDPOINT_DENIED_READ_ONLY")
        identity = _five_key(five_key)
        if identity not in self._catalog:
            raise RESTProducerBlocked("PPI_REST_CATALOG_FIVE_KEY_NOT_PROVEN")
        if priority not in _PRIORITIES:
            raise RESTProducerBlocked("PPI_REST_PRIORITY_INVALID")
        capture = getattr(self._source, "capture_guarded_native", None)
        if not callable(capture):
            raise RESTProducerBlocked("PPI_REST_GUARDED_NATIVE_SOURCE_UNAVAILABLE")

        source = capture(method, identity, priority=priority)
        if not isinstance(source, dict) or set(source) != {
                "native_payload", "guard_wire_receipt"}:
            raise RESTProducerBlocked("PPI_REST_SOURCE_RECEIPT_CONTRACT_INVALID")
        raw = source["native_payload"]
        if ((method != "intraday" and not isinstance(raw, dict))
                or (method == "intraday" and not isinstance(raw, list))
                or (isinstance(raw, dict) and any(str(key).lower() in _SENSITIVE_TOP_LEVEL for key in raw))):
            raise RESTProducerBlocked("PPI_REST_NATIVE_PAYLOAD_INVALID_OR_SENSITIVE")
        try:
            encoded = _canonical_bytes(raw)
        except (ValueError, TypeError, OverflowError) as exc:
            raise RESTProducerBlocked("PPI_REST_NATIVE_PAYLOAD_NOT_CANONICAL") from exc
        if not 2 <= len(encoded) <= _MAX_RAW_BYTES:
            raise RESTProducerBlocked("PPI_REST_NATIVE_PAYLOAD_UNBOUNDED")
        native = _native_timestamp(method, raw)
        raw_sha = hashlib.sha256(encoded).hexdigest()
        guard_receipt, received, sequence = _validate_guard_witness(
            source["guard_wire_receipt"], method=method, priority=priority,
            five_key=identity, raw_sha256=raw_sha)
        if native > received:
            raise RESTProducerBlocked("PPI_REST_NATIVE_TIME_AFTER_RECEIVE")
        # The native receipt store is the ONLY fan-out publisher. This object
        # exposes no extra GET, budget or order method; no double lease.
        result = self._sink.publish_native_rest_receipt(
            method=method, five_key=identity, raw_payload=raw,
            native_at=native, received_at=received,
            received_monotonic_ns=sequence, session_fingerprint=self._session,
            guard_budget_receipt_sha256=guard_receipt,
            capture_type={"current": "REST_CURRENT", "book": "REST_BOOK",
                          "intraday": "REST_INTRADAY"}[method],
        )
        return {"schema": SCHEMA, "state": "OBSERVE_ONLY",
                "ppi_catalog_authority": "NO_VERIFICADO",
                "guard_budget_receipt_authority": "NO_VERIFICADO",
                "session_authority": "LOCAL_ONLY_NO_PPI_SIGNATURE",
                "real_orders_sent_by_producer": 0,
                "extra_provider_calls_outside_guard": 0,
                "native_receipt": result,
                "raw_sha256": raw_sha, "catalog_source_digest": self._catalog_digest}


__all__ = ["MarketOnlyRESTProducer", "RESTProducerBlocked", "WIRE_WITNESS_SCHEMA"]
