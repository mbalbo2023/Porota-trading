"""A651-R42 OFFHOST-only PPI request/response evidence guard.

PPI SearchInstrument returns four identity fields, *not* settlement. A
Current/Book HTTPS query carries settlement, but its response does not echo
instrument identity. This diagnostic may verify a capturer-signed receipt; it
never authorizes PAPER, supplies a broker credential, or calls a provider.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import re
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


SCHEMA = "rc6.ppi-native-request-causal-offhost.v1"
SOURCE = "PPI_SDK_HTTPS_CAPTURE_NOT_PROVIDER_SIGNED"
ISSUER_ROLE = "RC6_PPI_READONLY_CAPTURE"
HOST = "clientapi.portfoliopersonal.com"
MAX_BODY = 128 * 1024
MAX_LEVELS = 200
MAX_AGE_S = 120
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_GIT = re.compile(r"[0-9a-f]{40}\Z")
_UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
_ART = ZoneInfo("America/Argentina/Buenos_Aires")
_TYPES = {"ACCIONES": "ACCIONES", "CEDEARS": "CEDEARS", "BONOS": "BONOS",
          "LETRAS": "LETRAS", "OBLIGACIONES": "ON", "OPCIONES": "OPCIONES",
          "FUTUROS": "FUTUROS", "CAUCIONES": "CAUCIONES"}
_PATHS = {"search": "searchinstrument", "current": "current",
          "book1": "book", "book2": "book"}
_WIRE_FIELDS = {"method", "url", "http_status", "sent_at_utc", "received_at_utc",
                "body_sha256", "body_bytes", "lease"}
_LEASE_FIELDS = {"authority", "instance_sha256", "lease_id", "state",
                 "acquired_at_utc", "started_at_utc", "finished_at_utc"}
_BUNDLE_FIELDS = {"schema", "source", "issuer_role", "capture_id",
                  "producer_git_sha", "artifact_digest_sha256_claim",
                  "signer_key_sha256", "expected_identity",
                  "mode", "orders_sent_by_capture", "writes_by_capture",
                  "search", "current", "book1", "book2", "decision_claim",
                  "signature_base64"}
_DECISION_FIELDS = {"decision_at_utc", "decision_known_at_utc", "latency_ms",
                    "decision_sha256_claim"}

# Intentionally unset. A capture payload or local environment cannot choose
# its own trust anchor. A later protected release must pin a DIFFERENT producer
# release, avoiding a self-referential release hash.
PINNED_CAPTURE_SIGNER_KEY_SHA256 = None
PINNED_CAPTURE_PRODUCER_GIT_SHA = None
# A 64-hex claim is NOT the 40-hex POROTA_BUILD_SHA. Its pin must be verified
# against an independent manifest of the exact capturer artifact/release.
PINNED_CAPTURE_ARTIFACT_DIGEST_SHA256 = None


def _result(reason, state="NO_VERIFICADO"):
    return {"schema": SCHEMA, "state": state, "reason": reason,
            "identity_authority": "NO_VERIFICADO",
            "settlement_origin": "REQUEST_QUERY_NOT_PROVIDER_FIELD",
            "provider_signature_verified": False,
            "capturer_signature_verified": False,
            "budget_sidecar_independently_verified": False,
            "decision_signature_verified": False,
            "source_pit_verified": False,
            "economic_contract_verified": False,
            "can_simulate": False, "instrument_ready": False,
            "paper_fills_accredited": 0,
            "runtime_real_orders_sent_verified": False,
            "provider_gets_by_guard": 0, "host_writes_by_guard": 0}


def _utc(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        raise ValueError("PPI_CLOCK_INVALID")
    try:
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("PPI_CLOCK_INVALID") from None
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("PPI_CLOCK_INVALID")
    return value.astimezone(timezone.utc)


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError("PPI_RECEIPT_CANONICAL_INVALID") from None


def _strict_body(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BODY:
        raise ValueError("PPI_HTTP_BODY_CAPACITY_INVALID")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("PPI_HTTP_DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def nonfinite(_):
        raise ValueError("PPI_HTTP_NONFINITE_JSON")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=nonfinite)
    except (UnicodeError, TypeError, json.JSONDecodeError, RecursionError):
        raise ValueError("PPI_HTTP_JSON_INVALID") from None


def _number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return False
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return number.is_finite() and (number > 0 if positive else number >= 0)


def _url_matches(value, name, identity):
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        return False
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname != HOST
                or parsed.port not in (None, 443) or parsed.username is not None
                or parsed.password is not None or parsed.fragment
                or parsed.path.lower().rstrip("/") !=
                "/api/1.0/marketdata/" + _PATHS[name]):
            return False
        pairs = [(k.lower(), v) for k, v in
                 parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)]
    except (TypeError, ValueError):
        return False
    expected = {"ticker": identity[0], "type": _TYPES[identity[1]]}
    if name != "search":
        expected["settlement"] = identity[4]
    return (len(pairs) == len(expected)
            and len(set(k for k, _ in pairs)) == len(expected)
            and dict(pairs) == expected)


def _wire(name, record, raw_body, identity):
    if not isinstance(record, dict) or set(record) != _WIRE_FIELDS:
        raise ValueError("PPI_WIRE_SCHEMA_INVALID")
    if (record["method"] != "GET" or type(record["http_status"]) is not int
            or record["http_status"] != 200
            or not _url_matches(record["url"], name, identity)):
        raise ValueError("PPI_WIRE_TARGET_OR_HTTP_STATUS_INVALID")
    if (not isinstance(raw_body, bytes) or type(record["body_bytes"]) is not int
            or record["body_bytes"] != len(raw_body)
            or not 0 < len(raw_body) <= MAX_BODY
            or not isinstance(record["body_sha256"], str)
            or not _SHA.fullmatch(record["body_sha256"])
            or not hmac.compare_digest(hashlib.sha256(raw_body).hexdigest(),
                                       record["body_sha256"])):
        raise ValueError("PPI_WIRE_BODY_DIGEST_INVALID")
    sent, received = _utc(record["sent_at_utc"]), _utc(record["received_at_utc"])
    if not 0 <= (received - sent).total_seconds() <= 60:
        raise ValueError("PPI_WIRE_CLOCK_INVALID")
    lease = record["lease"]
    if (not isinstance(lease, dict) or set(lease) != _LEASE_FIELDS
            or lease["authority"] != "RC6_GLOBAL_RUNTIME_READONLY_BUDGET"
            or lease["state"] != "COMPLETED"
            or not isinstance(lease["instance_sha256"], str)
            or not _SHA.fullmatch(lease["instance_sha256"])
            or not isinstance(lease["lease_id"], str)
            or not _UUID4.fullmatch(lease["lease_id"])):
        raise ValueError("PPI_BUDGET_LEASE_SHAPE_INVALID")
    acquired, started, finished = (_utc(lease[k]) for k in
                                   ("acquired_at_utc", "started_at_utc", "finished_at_utc"))
    if not acquired <= started <= sent <= received <= finished:
        raise ValueError("PPI_BUDGET_LEASE_CLOCK_INVALID")
    return (_strict_body(raw_body), sent, received,
            lease["lease_id"], lease["instance_sha256"])


def _native(body, received, role):
    if not isinstance(body, dict) or not isinstance(body.get("date"), str):
        raise ValueError("PPI_NATIVE_DATE_MISSING")
    native = _utc(body["date"])
    if not 0 <= (received - native).total_seconds() <= MAX_AGE_S:
        raise ValueError("PPI_NATIVE_DATE_STALE_OR_FUTURE")
    if role == "current":
        if not _number(body.get("price"), positive=True):
            raise ValueError("PPI_CURRENT_PRICE_UNUSABLE")
    else:
        if (not isinstance(body.get("bids"), list)
                or not isinstance(body.get("offers"), list)):
            raise ValueError("PPI_BOOK_SHAPE_INVALID")
        bids, offers = body["bids"], body["offers"]
        if len(bids) > MAX_LEVELS or len(offers) > MAX_LEVELS:
            raise ValueError("PPI_BOOK_CAPACITY_INVALID")
        levels = bids + offers
        if (not levels
                or any(not isinstance(level, dict)
                       or not _number(level.get("price"))
                       or not _number(level.get("quantity")) for level in levels)
                or any(type(level.get("position")) is not int or level["position"] != pos
                       for side in (bids, offers) for pos, level in enumerate(side))
                or not any(_number(level["price"], positive=True)
                           and _number(level["quantity"], positive=True)
                           for level in bids[:1] + offers[:1])):
            raise ValueError("PPI_BOOK_TOP_DEPTH_UNUSABLE")
    return native


def _capture_signature(bundle, trusted_public_key):
    # A caller-provided key cannot self-validate an untrusted signed payload.
    # All three pins are absent until independent release/signature evidence.
    if (not isinstance(PINNED_CAPTURE_SIGNER_KEY_SHA256, str)
            or not _SHA.fullmatch(PINNED_CAPTURE_SIGNER_KEY_SHA256)
            or not isinstance(PINNED_CAPTURE_PRODUCER_GIT_SHA, str)
            or not _GIT.fullmatch(PINNED_CAPTURE_PRODUCER_GIT_SHA)
            or not isinstance(PINNED_CAPTURE_ARTIFACT_DIGEST_SHA256, str)
            or not _SHA.fullmatch(PINNED_CAPTURE_ARTIFACT_DIGEST_SHA256)):
        raise ValueError("PPI_CAPTURE_TRUST_PINS_NOT_ACCREDITED")
    if (not isinstance(trusted_public_key, bytes) or len(trusted_public_key) != 32
            or not isinstance(bundle["signer_key_sha256"], str)
            or not hmac.compare_digest(bundle["signer_key_sha256"],
                                       PINNED_CAPTURE_SIGNER_KEY_SHA256)
            or not hmac.compare_digest(hashlib.sha256(trusted_public_key).hexdigest(),
                                       PINNED_CAPTURE_SIGNER_KEY_SHA256)
            or bundle["producer_git_sha"] != PINNED_CAPTURE_PRODUCER_GIT_SHA
            or bundle["artifact_digest_sha256_claim"] != PINNED_CAPTURE_ARTIFACT_DIGEST_SHA256):
        raise ValueError("PPI_CAPTURE_SIGNER_OR_ARTIFACT_UNTRUSTED")
    try:
        signature = base64.b64decode(bundle["signature_base64"], validate=True)
        if len(signature) != 64:
            raise ValueError("PPI_CAPTURE_SIGNATURE_INVALID")
        unsigned = {key: value for key, value in bundle.items()
                    if key != "signature_base64"}
        Ed25519PublicKey.from_public_bytes(trusted_public_key).verify(
            signature, _canonical(unsigned))
    except (InvalidSignature, ValueError, TypeError, binascii.Error, RecursionError):
        raise ValueError("PPI_CAPTURE_SIGNATURE_INVALID") from None


def inspect_native_request_bundle(bundle, bodies, *, expected_identity,
                                  trusted_capture_public_key_raw=None):
    """Check only signed capture consistency; NEVER admit a PAPER instrument.

    Result EVIDENCE_ONLY_NO_GO is the strongest possible assertion: the signer
    is the local HTTP capturer, not PPI; budget sidecar and decision/release
    witnesses require separate physical trust/consumer reviews. An ordinary
    test fixture cannot turn these flags true.
    """
    if (not isinstance(expected_identity, tuple) or len(expected_identity) != 5
            or any(not isinstance(part, str) or not part or part != part.strip()
                   or part != part.upper() for part in expected_identity)
            or expected_identity[1] not in _TYPES
            or expected_identity[2] not in {"BYMA", "ROFEX", "OTC", "NYSE"}
            or expected_identity[3] not in {"ARS", "USD"}
            or expected_identity[4] not in
               {"INMEDIATA", "A-24HS", "A-48HS", "A-72HS"}):
        return _result("PPI_EXPECTED_FIVE_KEY_INVALID", "BLOQUEADO")
    if (not isinstance(bundle, dict) or set(bundle) != _BUNDLE_FIELDS
            or bundle.get("schema") != SCHEMA or bundle.get("source") != SOURCE
            or bundle.get("issuer_role") != ISSUER_ROLE
            or bundle.get("mode") != "PRODUCTION_PAPER"
            or not isinstance(bundle.get("expected_identity"), list)
            or tuple(bundle["expected_identity"]) != expected_identity
            or type(bundle.get("orders_sent_by_capture")) is not int
            or bundle["orders_sent_by_capture"] != 0
            or type(bundle.get("writes_by_capture")) is not int
            or bundle["writes_by_capture"] != 0
            or not isinstance(bundle.get("capture_id"), str)
            or not _UUID4.fullmatch(bundle["capture_id"])
            or not isinstance(bundle.get("producer_git_sha"), str)
            or not _GIT.fullmatch(bundle["producer_git_sha"])
            or not isinstance(bundle.get("artifact_digest_sha256_claim"), str)
            or not _SHA.fullmatch(bundle["artifact_digest_sha256_claim"])):
        return _result("PPI_CAPTURE_SCHEMA_OR_FIVE_KEY_INVALID", "BLOQUEADO")
    if not isinstance(bodies, dict) or set(bodies) != set(_PATHS):
        return _result("PPI_CAPTURE_BODIES_MISSING", "BLOQUEADO")
    try:
        _capture_signature(bundle, trusted_capture_public_key_raw)
        observed = {name: _wire(name, bundle[name], bodies[name], expected_identity)
                    for name in ("search", "current", "book1", "book2")}
        if (len({row[3] for row in observed.values()}) != 4
                or len({row[4] for row in observed.values()}) != 1):
            raise ValueError("PPI_BUDGET_LEASE_REUSED_OR_UNRELATED")
        search, current, book1, book2 = (observed[name][0] for name in
                                         ("search", "current", "book1", "book2"))
        if not isinstance(search, list) or not 0 < len(search) <= MAX_LEVELS:
            raise ValueError("PPI_SEARCH_RESPONSE_INVALID")
        matching = [row for row in search if isinstance(row, dict)
                    and row.get("ticker") == expected_identity[0]
                    and row.get("type") == _TYPES[expected_identity[1]]]
        if (len(matching) != 1
                or matching[0].get("market") != expected_identity[2]
                or matching[0].get("currency") != expected_identity[3]
                or "settlement" in matching[0]):
            raise ValueError("PPI_SEARCH_FOUR_KEY_AMBIGUOUS_OR_NOT_NATIVE")
        search_received = observed["search"][2]
        current_sent, current_received = observed["current"][1:3]
        book1_sent, book1_received = observed["book1"][1:3]
        book2_sent, book2_received = observed["book2"][1:3]
        if (not search_received <= current_sent <= current_received
                <= book1_sent <= book1_received <= book2_sent <= book2_received
                or (book2_received - search_received).total_seconds() > 600):
            raise ValueError("PPI_CAPTURE_GET_SEQUENCE_INVALID")
        current_at = _native(current, current_received, "current")
        book1_at = _native(book1, book1_received, "book")
        book2_at = _native(book2, book2_received, "book")
        decision_claim = bundle["decision_claim"]
        if (not isinstance(decision_claim, dict)
                or set(decision_claim) != _DECISION_FIELDS
                or not isinstance(decision_claim["decision_sha256_claim"], str)
                or not _SHA.fullmatch(decision_claim["decision_sha256_claim"])
                or type(decision_claim["latency_ms"]) is not int
                or not 0 <= decision_claim["latency_ms"] <= 300_000):
            raise ValueError("PPI_DECISION_CLAIM_SHAPE_INVALID")
        decision_at = _utc(decision_claim["decision_at_utc"])
        known_at = _utc(decision_claim["decision_known_at_utc"])
        if (not book1_received <= decision_at <= known_at <= book2_sent
                or not book1_at <= decision_at
                or not book2_at > known_at +
                   timedelta(milliseconds=decision_claim["latency_ms"])):
            raise ValueError("PPI_BOOK1_DECISION_BOOK2_CAUSAL_CUT_INVALID")
        session_art = decision_at.astimezone(_ART).date()
        if any(moment.astimezone(_ART).date() != session_art for moment in (
                search_received, current_received, book1_received, book2_received,
                current_at, book1_at, book2_at, known_at)):
            raise ValueError("PPI_ART_SESSION_CROSSING_INVALID")
    except ValueError as error:
        reason = str(error)
        return _result(reason, "NO_VERIFICADO" if reason ==
                       "PPI_CAPTURE_TRUST_PINS_NOT_ACCREDITED" else "BLOQUEADO")
    return {
        **_result("CAPTURER_SIGNED_QUERY_RECEIPT_NOT_PROVIDER_OR_DECISION_AUTHORITY",
                  "EVIDENCE_ONLY_NO_GO"),
        "identity_authority": "PPI_SEARCH_FOUR_KEY_PLUS_GET_QUERY_STRUCTURAL_ONLY",
        "settlement_origin": "CAPTURER_SIGNED_PPI_REQUEST_QUERY_NOT_RESPONSE_FIELD",
        "capturer_signature_verified": True,
    }
