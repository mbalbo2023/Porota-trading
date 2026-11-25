"""Adversarial tests for the local signing anchor and exact PPI settlement."""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from be_paper_engine import PaperStore
from bf_production_paper_observer import _support_schema
import bu_instrument_catalog as catalog
from rc6_ppi_native_request_receipt import FIELD, SettlementReceiptSigner, pin_gateway_signer, verified_receipt
from rc6_ppi_shared_feed import _canonical_bytes, _sha


KEY = ("AL30", "BONOS", "BYMA", "ARS", "A-24HS")
PARTS = ("ticker", "instrument_type", "market", "currency", "settlement")
RELEASE = ["a" * 40, "b" * 40]
GENERATION = "c" * 64
DIGEST = "d" * 64
AT = datetime(2026, 11, 24, 14, tzinfo=timezone.utc)


def wire(endpoint, key=KEY, *, literal=None, elapsed=None, nonce=None):
    received = AT + timedelta(seconds=elapsed if elapsed is not None else endpoint == "book")
    price = {"date": received.isoformat(), "price": 100, "volume": 20,
             "openingPrice": 99, "max": 101, "min": 99}
    book = {"date": received.isoformat(),
            "bids": [{"position": 0, "price": 99, "quantity": 20}],
            "offers": [{"position": 0, "price": 101, "quantity": 20}]}
    body = price if endpoint == "current" else book
    if literal is not None:
        body["settlement"] = literal
    parsed_hash = _sha(body)
    guard = {
        "schema": "RC6_PPI_NATIVE_WIRE_RECEIPT_V1", "endpoint": endpoint, "http_method": "GET",
        "consumer": "SCANNER", "priority": "DISCOVERY", "identity_source": "CALLER_ONLY_UNVERIFIED",
        "request_identity_3key": [key[0], key[1], key[4]], "claimed_five_key": list(key),
        "adapter_send_invoked": True, "adapter_response_completed": True, "http_status_code": 200,
        "raw_body_sha256": parsed_hash, "parsed_body_sha256": parsed_hash,
        "received_at_utc": received.isoformat(), "receive_monotonic_ns": 11 if endpoint == "current" else 22,
        "budget_lease_sha256": _sha(nonce or endpoint), "wire_attempt_id": "QA-" + endpoint,
    }
    guard["guard_budget_receipt_sha256"] = hashlib.sha256(_canonical_bytes(guard)).hexdigest()
    native = {"ppi_five_key": list(key), "native_at_utc": body["date"], "raw_payload": body,
              "raw_sha256": parsed_hash, "receive_monotonic_ns": guard["receive_monotonic_ns"],
              "guard_budget_receipt_sha256": guard["guard_budget_receipt_sha256"],
              "received_at_utc": received.isoformat()}
    return {"native_receipt": native, "guard_wire_receipt": guard}


def sign_pair(signer, key=KEY):
    assert signer.observe("current", key, wire("current", key),
                          generation=GENERATION, catalog_digest=DIGEST) is None
    return signer.observe("book", key, wire("book", key),
                          generation=GENERATION, catalog_digest=DIGEST)


def pin(database, signer):
    from cg_paper_workspace import artifact_root

    (artifact_root(database) / "ppi-gateway").mkdir(parents=True, mode=0o700)
    status = {"state": "OPEN", "owner_pid": os.getpid(),
              "settlement_signer_public_key": signer.public_key,
              "settlement_signer_release": RELEASE}
    with pytest.raises(ValueError, match="SIGNER_UNPINNED"):
        pin_gateway_signer(database, status, expected_pid=os.getpid() + 1, release=RELEASE)
    pin_gateway_signer(database, status, expected_pid=os.getpid(), release=RELEASE)


@pytest.fixture(autouse=True)
def fixture_release_coordinates(monkeypatch):
    monkeypatch.setenv("POROTA_BUILD_SHA", RELEASE[0])
    monkeypatch.setenv("POROTA_CANDIDATE_TREE_SHA", RELEASE[1])


def test_only_controller_pinned_signature_matches_the_exact_instrument(tmp_path, monkeypatch):
    database = tmp_path / "paper.db"
    signer = SettlementReceiptSigner(RELEASE)
    proof = sign_pair(signer)
    at = AT + timedelta(seconds=3)
    assert not verified_receipt(proof, KEY, database=database, now=at)
    pin(database, signer)
    assert verified_receipt(proof, KEY, database=database, now=at)
    assert not verified_receipt(proof, (*KEY[:4], "INMEDIATA"), database=database, now=at)
    assert not verified_receipt(proof, KEY, database=database, now=AT)
    assert not verified_receipt(proof, KEY, database=database, now=at + timedelta(days=15))
    assert not verified_receipt({**proof, "catalog_sha256": "e" * 64}, KEY, database=database, now=at)
    assert not verified_receipt(sign_pair(SettlementReceiptSigner(RELEASE)), KEY,
                                database=database, now=at)
    monkeypatch.setenv("POROTA_BUILD_SHA", "e" * 40)
    assert not verified_receipt(proof, KEY, database=database, now=at)
    monkeypatch.setenv("POROTA_BUILD_SHA", RELEASE[0])
    record = dict(zip(PARTS, KEY), settlement_source="REQUEST_CANDIDATE",
                  raw={"ticker": "AL30", "type": "BONOS", "market": "BYMA", "currency": "ARS",
                       "_discovery_source": "IOL_COMPLEMENTARY", FIELD: proof})
    assert catalog.ppi_full_key_evidence_reason(record, database=database, now=at)
    record["raw"]["_discovery_source"] = "PPI_PRIMARY"
    assert not catalog.ppi_full_key_evidence_reason(record, database=database, now=at)


@pytest.mark.parametrize("defect", ["no_current", "different_key", "wrong_literal", "wrong_body",
                                    "different_generation", "same_lease", "stale_pair"])
def test_incomplete_or_mismatched_wire_cannot_be_signed(defect):
    signer = SettlementReceiptSigner(RELEASE)
    if defect != "no_current":
        signer.observe("current", KEY, wire("current"), generation=GENERATION, catalog_digest=DIGEST)
    key = (*KEY[:4], "INMEDIATA") if defect == "different_key" else KEY
    capture = wire("book", key, literal="INMEDIATA" if defect == "wrong_literal" else None,
                   nonce="current" if defect == "same_lease" else None,
                   elapsed=121 if defect == "stale_pair" else None)
    if defect == "wrong_body":
        capture["native_receipt"]["raw_payload"]["offers"][0]["price"] = 102
    generation = "e" * 64 if defect == "different_generation" else GENERATION
    assert signer.observe("book", key, capture, generation=generation, catalog_digest=DIGEST) is None


def test_catalog_rejects_a_second_market_or_currency_even_on_another_term(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    _support_schema(store)
    signer = SettlementReceiptSigner(RELEASE)
    pin(store.path, signer)
    with store.connect() as connection:
        for currency, term in (("ARS", KEY[4]), ("USD", "INMEDIATA")):
            row = catalog.normalize_record(dict(ticker="AL30", type="BONOS",
                                               market="BYMA", currency=currency), term,
                                           AT.isoformat(), "OFFHOST")
            catalog.persist(connection, row)
        connection.commit()
        catalog.sync_candidate_universe(connection, AT.isoformat(), bounded_batch=128)
    assert not catalog.publish_native_settlement_receipt(
        store, KEY, sign_pair(signer), (AT + timedelta(seconds=3)).isoformat())
    with store.connect() as connection:
        rows = connection.execute("""SELECT metadata_json FROM financial_instrument_catalog""").fetchall()
        assert all(FIELD not in json.loads(row[0]) for row in rows)
