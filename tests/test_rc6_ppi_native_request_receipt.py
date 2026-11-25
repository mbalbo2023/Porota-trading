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


def wire(endpoint, key=KEY, *, literal=None, elapsed=None, nonce=None, priority="DISCOVERY"):
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
        "consumer": "SCANNER", "priority": priority, "identity_source": "CALLER_ONLY_UNVERIFIED",
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


@pytest.mark.parametrize("literal", ["INMEDIATA", None, "A-24HS"])
def test_exact_caucion_placing_book_can_bind_its_literal_without_current(tmp_path, literal):
    key = ("PESOS1", "CAUCIONES", "BYMA", "ARS", "INMEDIATA")
    signer = SettlementReceiptSigner(RELEASE)
    captured = wire("book", key, literal=literal, priority="WARM")
    proof = signer.observe("book", key, captured, generation=GENERATION,
                           catalog_digest=DIGEST, priority="WARM")
    if literal != "INMEDIATA":
        assert proof is None
        return
    assert "current" not in proof and proof["five_key"] == list(key)
    database = tmp_path / "paper.db"
    pin(database, signer)
    assert verified_receipt(proof, key, database=database, now=AT + timedelta(seconds=3))
    assert not verified_receipt(proof, ("PESOS2", *key[1:]), database=database,
                                now=AT + timedelta(seconds=3))
    assert not verified_receipt({**proof, "schema": "rc6.ppi.native-settlement-receipt.v1"},
                                key, database=database, now=AT + timedelta(seconds=3))


def test_caucion_collector_publishes_the_signed_book_via_existing_catalog(tmp_path, monkeypatch):
    import dj_caucion_live_ppi_rc6 as live

    store = PaperStore(str(tmp_path / "paper.db"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    _support_schema(store)
    key = ("PESOS1", "CAUCIONES", "BYMA", "ARS", "INMEDIATA")
    record = catalog.normalize_record(
        {"ticker": key[0], "type": key[1], "market": key[2], "currency": key[3]},
        key[4], AT.isoformat(), "OFFHOST")
    with store.connect() as connection:
        catalog.persist(connection, record)
        connection.commit()
        catalog.sync_candidate_universe(connection, AT.isoformat(), bounded_batch=64)
    signer = SettlementReceiptSigner(RELEASE)
    pin(store.path, signer)
    captured = wire("book", key, literal="INMEDIATA", priority="WARM")
    calls = []

    class Reader:
        def book(self, *_):
            pytest.fail("The same guarded Book must be reused")

        def capture_guarded_native(self, endpoint, identity, *, priority):
            calls.append((endpoint, identity, priority))
            return {
                "native_payload": captured["native_receipt"]["raw_payload"],
                "settlement_proof": signer.observe(
                    endpoint, identity, captured, generation=GENERATION,
                    catalog_digest=DIGEST, priority=priority),
            }

    monkeypatch.setenv("PPI_CAUCION_DISCOVERY_DAYS", "1")
    monkeypatch.setattr(live, "collection_window", lambda _at: True)
    at = AT + timedelta(seconds=2)
    result = live.refresh(Reader(), store, now=at, clock_fn=lambda: at)
    assert calls == [("book", key, "WARM")]
    assert result["ready"] == result["settlement_proofs_published"] == 1
    with store.connect() as connection:
        raw = json.loads(connection.execute("SELECT metadata_json FROM financial_instrument_catalog").fetchone()[0])
    assert verified_receipt(raw[FIELD], key, database=store.path, now=at)
def test_release_rotation_retains_only_preexisting_identity_authority(tmp_path, monkeypatch):
    import rc6_ppi_native_request_receipt as receipts

    class RotationClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (AT + timedelta(seconds=3)).astimezone(tz)

    monkeypatch.setattr(receipts, "datetime", RotationClock)
    database = str(tmp_path / "paper.db")
    old = SettlementReceiptSigner(RELEASE)
    pin(database, old)
    proof = sign_pair(old)
    new_release = ["e" * 40, "f" * 40]
    new = SettlementReceiptSigner(new_release)
    status = {"state": "OPEN", "owner_pid": os.getpid(),
              "settlement_signer_public_key": new.public_key, "settlement_signer_release": new_release}
    pin_gateway_signer(database, status, expected_pid=os.getpid(), release=new_release)
    monkeypatch.setenv("POROTA_BUILD_SHA", new_release[0])
    monkeypatch.setenv("POROTA_CANDIDATE_TREE_SHA", new_release[1])
    at = AT + timedelta(seconds=10)
    assert verified_receipt(proof, KEY, database=database, now=at)
    assert not verified_receipt(proof, KEY, database=database, now=at, require_active=True)
    assert not verified_receipt(proof, KEY, database=database, now=at + timedelta(days=15))
    old.observe("current", KEY, wire("current", elapsed=4), generation=GENERATION, catalog_digest=DIGEST)
    late = old.observe("book", KEY, wire("book", elapsed=5), generation=GENERATION, catalog_digest=DIGEST)
    assert late and not verified_receipt(late, KEY, database=database, now=at)


def test_book_failure_materializes_sampled_candles_without_a_market_snapshot(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import bf_production_paper_observer as observer
    from bl_candle_engine import CandleArchive, SampleMaterializer, Series
    from rc6_ppi_gateway_reader_adapter import SharedFeedUnavailable, SharedPPIReaderAdapter
    from scripts.rc6_candle_inmarket_evidence import build_summary, collect_inputs

    store = PaperStore(str(tmp_path / "paper.db"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    _support_schema(store)
    record = catalog.normalize_record(
        {"ticker": KEY[0], "type": KEY[1], "market": KEY[2], "currency": KEY[3],
         "settlement": KEY[4]}, KEY[4], AT.isoformat(), "OFFHOST")
    with store.connect() as connection:
        catalog.persist(connection, record)
    archive = CandleArchive(store)
    captured = wire("current")
    capture = {"native_payload": captured["native_receipt"]["raw_payload"],
               "guard_wire_receipt": captured["guard_wire_receipt"]}
    known = (AT + timedelta(seconds=10)).isoformat()
    reader = SharedPPIReaderAdapter(client=SimpleNamespace(pull=lambda *_a, **_kw: None),
                                    consumer="scanner", catalog_source_digest=DIGEST)
    monkeypatch.setattr(reader, "current_with_optional_native_capture", lambda *_a, **_kw: capture)
    monkeypatch.setattr(observer, "now_iso", lambda: known)

    def missing_book(*_):
        raise SharedFeedUnavailable("PPI_NATIVE_HTTP_429")

    monkeypatch.setattr(reader, "book", missing_book)
    for _ in range(2):
        with pytest.raises(SharedFeedUnavailable, match="PPI_NATIVE_HTTP_429"):
            observer._read_scanner_quote(reader, store, KEY[0], KEY[1], KEY[4], identity=KEY)
    end = (AT + timedelta(minutes=5)).isoformat()
    result = SampleMaterializer(store).tick(end)
    assert result["versions"] == 2 and result["cursor"] == 0
    for resolution in ("1m", "5m"):
        series = Series(*KEY, resolution, "PPI_GUARDED_CURRENT", "RAW",
                        "OBSERVED_NOMINAL_PRICE", "TRADE_SAMPLES", "UNKNOWN")
        bar, = archive.read(series, as_of=end, qualities=("SAMPLED",))
        assert bar["close"] == "100" and bar["samples"] == 1 and bar["volume"] is None
    inputs = collect_inputs(db_path=store.path, observed_at=end)
    summary = build_summary(baseline_cursor=0, observed_at=end, **inputs)
    assert summary["status"] == "GREEN" and summary["current_cursor"] == 0
    assert summary["guarded_current_sampled_progress"] == {
        "identity": list(KEY), "known_at_by_resolution": {"1m": end, "5m": end},
    }
    late = (AT + timedelta(minutes=25)).isoformat()
    expired = build_summary(
        baseline_cursor=0, observed_at=late, **collect_inputs(db_path=store.path, observed_at=late))
    assert expired["status"] == "RED"
    with store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM market_snapshots").fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM historical_raw_archive WHERE origin='PPI_GUARDED_CURRENT'").fetchone()[0] == 1


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
