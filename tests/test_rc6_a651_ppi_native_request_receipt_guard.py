"""A651-R42 adversarial OFFHOST fixtures; never genuine PPI observations."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

import rc6_ppi_native_request_receipt_guard as guard


IDENTITY = ("AL30", "BONOS", "BYMA", "ARS", "A-24HS")
GIT_SHA = "a" * 40
ARTIFACT_SHA256 = "b" * 64
ORIGIN = "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/"


def _when(base, seconds):
    return (base + timedelta(seconds=seconds)).isoformat()


def _raw(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _fixture(monkeypatch, *, base=None, pins=True):
    """A fully fabricated capturer/HTTP bundle for negative tests ONLY."""
    base = base or datetime(2026, 11, 4, 15, 0, 0, tzinfo=timezone.utc)
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    signer_sha = hashlib.sha256(public).hexdigest()
    if pins:
        monkeypatch.setattr(guard, "PINNED_CAPTURE_SIGNER_KEY_SHA256", signer_sha)
        monkeypatch.setattr(guard, "PINNED_CAPTURE_PRODUCER_GIT_SHA", GIT_SHA)
        monkeypatch.setattr(guard, "PINNED_CAPTURE_ARTIFACT_DIGEST_SHA256", ARTIFACT_SHA256)
    else:
        monkeypatch.setattr(guard, "PINNED_CAPTURE_SIGNER_KEY_SHA256", None)
        monkeypatch.setattr(guard, "PINNED_CAPTURE_PRODUCER_GIT_SHA", None)
        monkeypatch.setattr(guard, "PINNED_CAPTURE_ARTIFACT_DIGEST_SHA256", None)
    search = [{"ticker": "AL30", "type": "BONOS", "market": "BYMA",
               "currency": "ARS", "description": "synthetic-only"}]
    current = {"date": _when(base, 3), "price": 88000, "volume": 100}
    book1 = {"date": _when(base, 9), "bids": [{"position": 0, "price": 87900,
              "quantity": 15}], "offers": [{"position": 0, "price": 88000,
              "quantity": 20}]}
    book2 = {"date": _when(base, 19), "bids": [{"position": 0, "price": 87850,
              "quantity": 14}], "offers": [{"position": 0, "price": 88100,
              "quantity": 18}]}
    bodies = dict(zip(("search", "current", "book1", "book2"),
                      map(_raw, (search, current, book1, book2))))
    bundles = {}
    for index, (name, sent, received) in enumerate((
            ("search", 0, 1), ("current", 5, 6),
            ("book1", 10, 11), ("book2", 20, 21)), start=1):
        operation = "SearchInstrument" if name == "search" else (
            "Current" if name == "current" else "Book")
        query = "Ticker=AL30&Type=BONOS"
        if name != "search":
            query += "&Settlement=A-24HS"
        bundles[name] = {
            "method": "GET", "url": ORIGIN + operation + "?" + query,
            "http_status": 200,
            "sent_at_utc": _when(base, sent),
            "received_at_utc": _when(base, received),
            "body_bytes": len(bodies[name]),
            "body_sha256": hashlib.sha256(bodies[name]).hexdigest(),
            "lease": {
                "authority": "RC6_GLOBAL_RUNTIME_READONLY_BUDGET",
                "instance_sha256": "c" * 64,
                "lease_id": str(UUID(int=index, version=4)),
                "state": "COMPLETED",
                "acquired_at_utc": _when(base, sent - 2),
                "started_at_utc": _when(base, sent - 1),
                "finished_at_utc": _when(base, received + 1),
            },
        }
    bundle = {
        "schema": guard.SCHEMA,
        "source": guard.SOURCE,
        "issuer_role": guard.ISSUER_ROLE,
        "capture_id": str(UUID(int=98765, version=4)),
        "producer_git_sha": GIT_SHA,
        "artifact_digest_sha256_claim": ARTIFACT_SHA256,
        "signer_key_sha256": signer_sha,
        "expected_identity": list(IDENTITY),
        "mode": "PRODUCTION_PAPER",
        "orders_sent_by_capture": 0,
        "writes_by_capture": 0,
        **bundles,
        "decision_claim": {"decision_at_utc": _when(base, 15),
                           "decision_known_at_utc": _when(base, 16),
                           "latency_ms": 0,
                           "decision_sha256_claim": "d" * 64},
        "signature_base64": "",
    }
    _sign(bundle, private)
    return bundle, bodies, private, public


def _sign(bundle, private):
    unsigned = {k: v for k, v in bundle.items() if k != "signature_base64"}
    bundle["signature_base64"] = base64.b64encode(
        private.sign(guard._canonical(unsigned))).decode("ascii")


def _body_replace(bundle, bodies, private, name, data):
    bodies[name] = _raw(data)
    bundle[name]["body_sha256"] = hashlib.sha256(bodies[name]).hexdigest()
    bundle[name]["body_bytes"] = len(bodies[name])
    _sign(bundle, private)


def _inspect(bundle, bodies, public):
    return guard.inspect_native_request_bundle(
        bundle, bodies, expected_identity=IDENTITY,
        trusted_capture_public_key_raw=public,
    )


def test_signed_synthetic_pair_never_grants_ppi_or_paper(monkeypatch):
    bundle, bodies, _, pub = _fixture(monkeypatch)
    verdict = _inspect(bundle, bodies, pub)
    assert verdict["state"] == "EVIDENCE_ONLY_NO_GO", verdict
    assert verdict["capturer_signature_verified"] is True
    assert verdict["provider_signature_verified"] is False
    assert verdict["settlement_origin"] == "CAPTURER_SIGNED_PPI_REQUEST_QUERY_NOT_RESPONSE_FIELD"
    assert verdict["budget_sidecar_independently_verified"] is False
    assert verdict["source_pit_verified"] is False
    assert verdict["decision_signature_verified"] is False
    assert verdict["economic_contract_verified"] is False
    assert verdict["instrument_ready"] is False
    assert verdict["can_simulate"] is False
    assert verdict["paper_fills_accredited"] == 0
    assert verdict["runtime_real_orders_sent_verified"] is False
    assert verdict["provider_gets_by_guard"] == verdict["host_writes_by_guard"] == 0


def test_missing_physical_trust_pin_is_no_verificado(monkeypatch):
    bundle, bodies, _, pub = _fixture(monkeypatch, pins=False)
    verdict = _inspect(bundle, bodies, pub)
    assert verdict["state"] == "NO_VERIFICADO"
    assert verdict["reason"] == "PPI_CAPTURE_TRUST_PINS_NOT_ACCREDITED"
    assert verdict["can_simulate"] is False


@pytest.mark.parametrize("mutation, expected_reason", [
    (lambda b, p: b.update(producer_git_sha="a" * 64), "PPI_CAPTURE_SCHEMA_OR_FIVE_KEY_INVALID"),
    (lambda b, p: b.update(artifact_digest_sha256_claim="b" * 40), "PPI_CAPTURE_SCHEMA_OR_FIVE_KEY_INVALID"),
    (lambda b, p: b.update(expected_identity=["AL30", "BONOS", "ROFEX", "ARS", "A-24HS"]), "PPI_CAPTURE_SCHEMA_OR_FIVE_KEY_INVALID"),
    (lambda b, p: b["current"].update(http_status=403), "PPI_WIRE_TARGET_OR_HTTP_STATUS_INVALID"),
    (lambda b, p: b["book1"].update(url=ORIGIN + "Book?Ticker=AL30&Type=BONOS&Settlement=INMEDIATA"), "PPI_WIRE_TARGET_OR_HTTP_STATUS_INVALID"),
    (lambda b, p: b["book2"].update(url=ORIGIN + "Book?Ticker=AL30&Type=BONOS&Settlement=A-24HS&Settlement=A-48HS"), "PPI_WIRE_TARGET_OR_HTTP_STATUS_INVALID"),
    (lambda b, p: b["book1"].update(url="https://evil.example/api/1.0/MarketData/Book?Ticker=AL30&Type=BONOS&Settlement=A-24HS"), "PPI_WIRE_TARGET_OR_HTTP_STATUS_INVALID"),
    (lambda b, p: b["search"].update(url=ORIGIN + "SearchInstrument?Ticker=AL30&Type=BONOS&Market=BYMA"), "PPI_WIRE_TARGET_OR_HTTP_STATUS_INVALID"),
    (lambda b, p: b["current"].update(url=ORIGIN + "Current?Ticker=AL30&Type=ACCIONES&Settlement=A-24HS"), "PPI_WIRE_TARGET_OR_HTTP_STATUS_INVALID"),
    (lambda b, p: b.update(source="IOL_MCP"), "PPI_CAPTURE_SCHEMA_OR_FIVE_KEY_INVALID"),
    (lambda b, p: b["book1"]["lease"].update(lease_id=b["current"]["lease"]["lease_id"]), "PPI_BUDGET_LEASE_REUSED_OR_UNRELATED"),
    (lambda b, p: b["book2"]["lease"].update(instance_sha256="f" * 64), "PPI_BUDGET_LEASE_REUSED_OR_UNRELATED"),
    (lambda b, p: b["book2"]["lease"].update(started_at_utc="2026-11-04T15:00:22+00:00"), "PPI_BUDGET_LEASE_CLOCK_INVALID"),
    (lambda b, p: b["book1"].update(received_at_utc="2026-11-04T15:00:09+00:00"), "PPI_WIRE_CLOCK_INVALID"),
    (lambda b, p: b["current"].update(sent_at_utc="2026-11-04T15:00:05"), "PPI_CLOCK_INVALID"),
    (lambda b, p: b["decision_claim"].update(decision_known_at_utc="2026-11-04T15:00:22+00:00"), "PPI_BOOK1_DECISION_BOOK2_CAUSAL_CUT_INVALID"),
    (lambda b, p: b["decision_claim"].update(latency_ms=True), "PPI_DECISION_CLAIM_SHAPE_INVALID"),
    (lambda b, p: b["book2"].update(sent_at_utc="2026-11-04T15:00:12+00:00"), "PPI_BUDGET_LEASE_CLOCK_INVALID"),
])
def test_signed_adversarial_metadata_stays_closed(monkeypatch, mutation, expected_reason):
    bundle, bodies, private, pub = _fixture(monkeypatch)
    mutation(bundle, private)
    _sign(bundle, private)
    verdict = _inspect(bundle, bodies, pub)
    assert verdict["state"] == "BLOQUEADO", verdict
    assert verdict["reason"] == expected_reason, verdict
    assert not verdict["can_simulate"]


def test_capturer_signature_cannot_be_forged_or_self_pinned(monkeypatch):
    bundle, bodies, private, pub = _fixture(monkeypatch)
    bundle["search"]["http_status"] = 403  # Do not re-sign after tampering.
    assert _inspect(bundle, bodies, pub)["reason"] == "PPI_CAPTURE_SIGNATURE_INVALID"
    other_key = Ed25519PrivateKey.generate()
    other_public = other_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    assert _inspect(bundle, bodies, other_public)["reason"] == "PPI_CAPTURE_SIGNER_OR_ARTIFACT_UNTRUSTED"


def test_bodies_missing_or_digests_changed_fail_closed(monkeypatch):
    bundle, bodies, private, pub = _fixture(monkeypatch)
    missing = deepcopy(bodies)
    missing.pop("book2")
    assert _inspect(bundle, missing, pub)["reason"] == "PPI_CAPTURE_BODIES_MISSING"
    tampered = deepcopy(bodies)
    tampered["current"] = tampered["current"].replace(b"88000", b"99000")
    assert _inspect(bundle, tampered, pub)["reason"] == "PPI_WIRE_BODY_DIGEST_INVALID"
    duplicate = b'{"date":"2026-11-04T15:00:03+00:00","price":88000,"price":99000}'
    updated = deepcopy(bodies)
    new_bundle = deepcopy(bundle)
    updated["current"] = duplicate
    new_bundle["current"]["body_sha256"] = hashlib.sha256(duplicate).hexdigest()
    new_bundle["current"]["body_bytes"] = len(duplicate)
    _sign(new_bundle, private)
    assert _inspect(new_bundle, updated, pub)["reason"] == "PPI_HTTP_DUPLICATE_JSON_KEY"


@pytest.mark.parametrize("raw, reason", [
    (b"<html>failed</html>", "PPI_HTTP_JSON_INVALID"),
    (b'{"date":"2026-11-04T15:00:03+00:00","price":NaN}',
     "PPI_HTTP_NONFINITE_JSON"),
    (b"[" * 20000 + b"0" + b"]" * 20000, "PPI_HTTP_JSON_INVALID"),
    (b"x" * (guard.MAX_BODY + 1), "PPI_WIRE_BODY_DIGEST_INVALID"),
])
def test_malformed_http_payload_never_authorized(monkeypatch, raw, reason):
    bundle, bodies, private, pub = _fixture(monkeypatch)
    bodies["current"] = raw
    bundle["current"]["body_sha256"] = hashlib.sha256(raw).hexdigest()
    bundle["current"]["body_bytes"] = len(raw)
    _sign(bundle, private)
    verdict = _inspect(bundle, bodies, pub)
    assert verdict["reason"] == reason, verdict
    assert verdict["can_simulate"] is False


@pytest.mark.parametrize("name, data, expected", [
    ("search", [{"ticker": "AL30", "type": "BONOS", "market": "BYMA", "currency": "ARS"},
                {"ticker": "AL30", "type": "BONOS", "market": "ROFEX", "currency": "ARS"}],
     "PPI_SEARCH_FOUR_KEY_AMBIGUOUS_OR_NOT_NATIVE"),
    ("search", [{"ticker": "AL30", "type": "BONOS", "market": "BYMA", "currency": "ARS",
                 "settlement": "A-24HS"}], "PPI_SEARCH_FOUR_KEY_AMBIGUOUS_OR_NOT_NATIVE"),
    ("search", [{"ticker": "AL30", "type": "BONOS", "market": "BYMA", "currency": "USD"}],
     "PPI_SEARCH_FOUR_KEY_AMBIGUOUS_OR_NOT_NATIVE"),
    ("current", {"date": "2026-11-04T15:10:00+00:00", "price": 88000},
     "PPI_NATIVE_DATE_STALE_OR_FUTURE"),
    ("current", {"date": "2026-11-04T14:55:00+00:00", "price": 88000},
     "PPI_NATIVE_DATE_STALE_OR_FUTURE"),
    ("current", {"date": "2026-11-04T15:00:03+00:00", "price": True},
     "PPI_CURRENT_PRICE_UNUSABLE"),
    ("current", {"date": "2026-11-04T15:00:03+00:00", "price": "Infinity"},
     "PPI_CURRENT_PRICE_UNUSABLE"),
    ("book1", {"date": "2026-11-04T15:00:09+00:00", "bids": [], "offers": []},
     "PPI_BOOK_TOP_DEPTH_UNUSABLE"),
    ("book1", {"date": "2026-11-04T15:00:09+00:00", "bids": [
        {"position": 3, "price": 88000, "quantity": 5}], "offers": []},
     "PPI_BOOK_TOP_DEPTH_UNUSABLE"),
    ("book2", {"date": "2026-11-04T15:00:14+00:00", "bids": [{"position": 0, "price": 1, "quantity": 1}],
               "offers": []}, "PPI_BOOK1_DECISION_BOOK2_CAUSAL_CUT_INVALID"),
])
def test_signed_adversarial_raw_response_stays_closed(monkeypatch, name, data, expected):
    bundle, bodies, private, pub = _fixture(monkeypatch)
    _body_replace(bundle, bodies, private, name, data)
    verdict = _inspect(bundle, bodies, pub)
    assert verdict["state"] == "BLOQUEADO", verdict
    assert verdict["reason"] == expected, verdict


def test_art_session_boundary_rejects_crossing_even_with_fresh_native_book(monkeypatch):
    base = datetime(2026, 11, 5, 2, 59, 40, tzinfo=timezone.utc)  # 23:59:40 ART.
    bundle, bodies, private, pub = _fixture(monkeypatch, base=base)
    # The default Book2 is at 02:59:59 UTC, still 23:59 ART. Push it to 03:00:01.
    body = json.loads(bodies["book2"])
    body["date"] = _when(base, 21)
    bundle["book2"]["sent_at_utc"] = _when(base, 21)
    bundle["book2"]["received_at_utc"] = _when(base, 22)
    bundle["book2"]["lease"]["finished_at_utc"] = _when(base, 23)
    _body_replace(bundle, bodies, private, "book2", body)
    verdict = _inspect(bundle, bodies, pub)
    assert verdict["reason"] == "PPI_ART_SESSION_CROSSING_INVALID", verdict
    assert not verdict["can_simulate"]
