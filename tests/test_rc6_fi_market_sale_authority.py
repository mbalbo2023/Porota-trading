"""Documented PPI REST shape, independent ephemeral signer, PAPER-only ledger."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import sqlite3
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from be_paper_engine import PaperBroker, Quote
from bs_instrument_contracts import contract_from_metadata
from rc6_fixed_income_event_ledger import snapshot_at
from rc6_fixed_income_paper_market_sale import apply_paper_market_sale
import rc6_fixed_income_sale_authority as authority
import rc6_paper_causal_decision_receipt as causal
from rc6_performance.costs import paper_cost_contract
from rc6_ppi_shared_feed import PPIReadOnlyNativeReceiptStore, PPISharedMarketFeed
from test_rc6_fixed_income_event_ledger import apply, lot, receipt as issuer_receipt


AT = datetime(2026, 10, 29, 14, tzinfo=timezone.utc)
MILLISECOND = timedelta(milliseconds=1)
RELEASE = "a" * 64
SESSION = "b" * 64


def setup(tmp_path, monkeypatch, *, ticker, family, settlement="A-24HS", bid_qty=1_000):
    store, _ = lot(tmp_path)
    paper_id = "PAPER-AL30"
    identity = (ticker, family, "BYMA", "ARS", settlement)
    contract = contract_from_metadata(ticker, family, {
        "market": "BYMA", "currency": "ARS", "settlement": settlement, "cash_multiplier": "0.01",
        "quantity_step": "1", "minimum_quantity": "1", "metadata_source": "SYNTHETIC_PPI_UNITS",
    })
    with store.connect() as db:
        features = json.loads(db.execute("SELECT features_json FROM paper_positions").fetchone()[0])
        features.update(financial_contract=asdict(contract), contract_cash_multiplier="0.01")
        db.execute("UPDATE paper_positions SET symbol=?,asset_class=?,settlement=?,features_json=?",
                  (ticker, family, settlement, json.dumps(features, default=str)))
    signer = Ed25519PrivateKey.generate()
    public = signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key = tmp_path / "synthetic-ed25519.key"
    key.write_bytes(signer.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                        serialization.NoEncryption()))
    key.chmod(0o600)
    monkeypatch.setattr(causal, "PINNED_DECISION_SIGNER_KEY_SHA256", hashlib.sha256(public).hexdigest())
    monkeypatch.setattr(causal, "PINNED_RELEASE_SHA256", RELEASE)
    monkeypatch.setenv("POROTA_RC6_RELEASE_SHA256", RELEASE)
    monkeypatch.setenv("POROTA_FI_EVIDENCE_DIRECTORY", str(tmp_path))
    costs = paper_cost_contract(family, "ARS", AT, symbol=ticker, market="BYMA", settlement=settlement)
    policy = {"schema": authority.POLICY_SCHEMA, "release_sha256": RELEASE, "paper_only": True,
              "review_authority": "RELEASE_REVIEWED_PPI_UNITS_AND_PAPER_COST", "instruments": [
                  {"identity": list(identity), "known_at": (AT - timedelta(days=1)).isoformat(),
                   "valid_until": (AT + timedelta(days=14)).isoformat(), "quantity_unit": "VN",
                   "fee_currency": "ARS", "settlement_model": "PAPER_CONSERVATIVE_BYMA_SPOT",
                   "cash_multiplier": "0.01", "minimum_quantity": "1", "quantity_step": "1",
                   "fee_policy_sha256": costs.diagnostic()["policy_sha256"], "minimum_post_ack_ms": 1,
                   "ppi_economics_source_sha256": hashlib.sha256(b"Synthetic PPI units only").hexdigest()}]}
    raw = json.dumps(policy).encode()
    (tmp_path / "market-sale-policy.json").write_bytes(raw)
    monkeypatch.setenv(authority.POLICY_PIN, hashlib.sha256(raw).hexdigest())
    feed = PPISharedMarketFeed(ppi_catalog_rows=[{"ticker": ticker, "type": family,
        "market": "BYMA", "currency": "ARS"}], catalog_source_digest="c" * 64)
    feed.register_consumer("exit_reader", watched_five_keys=[identity])
    clock = {"ms": 50}
    cache = PPIReadOnlyNativeReceiptStore(feed, max_receipts=8, max_age_seconds=5,
        clock=lambda: AT + clock["ms"] * MILLISECOND,
        monotonic_ns=lambda: 1_000_000_000 + clock["ms"] * 1_000_000)

    def book(ms, price, *, generation=SESSION, quantity=bid_qty):
        at = AT + ms * MILLISECOND
        return cache.publish_native_rest_receipt(method="book", five_key=identity,
            raw_payload={"date": at.isoformat(), "bids": [{"position": 0, "price": price, "quantity": quantity}],
                         "offers": [{"position": 0, "price": price + 1, "quantity": 1000}]},
            native_at=at, received_at=at, received_monotonic_ns=1_000_000_000 + ms * 1_000_000,
            session_fingerprint=generation, guard_budget_receipt_sha256="f" * 64, capture_type="REST_BOOK")

    first = book(0, 99)
    quote = Quote(ticker, family, settlement, Decimal(0), contract=contract, currency="ARS", market="BYMA",
                  ppi_native_rest_book=first, **authority.native_book_fields(first, identity))
    store.add_quote(quote)
    position, = store.open_positions()
    assert store.latest_quote(position).ppi_native_rest_book == first

    def read_book2(path, intent, cutoff, clock_ns):
        return cache.read_native(consumer="exit_reader", method="book", five_key=intent["identity"],
            book1_receipt_sha256=intent["book1"]["receipt_sha256"], commit_at=cutoff,
            commit_monotonic_ns=clock_ns)

    monkeypatch.setattr(authority, "_read_first_book2", read_book2)
    monkeypatch.setattr(causal, "_now", lambda: (AT + 20 * MILLISECOND).isoformat())
    monkeypatch.setattr(causal, "time", SimpleNamespace(monotonic_ns=lambda: 1_020_000_000))
    decision = authority.prepare_market_sale(store, position, store.latest_quote(position), "MAX_HOLD_PAPER",
                                             AT + 10 * MILLISECOND)
    ack = causal.attest_after_commit(store.path, decision_key=decision, signing_key_path=key,
                                    expected_release_sha256=RELEASE)
    assert ack["signature_created"]
    return store, paper_id, key, decision, book, cache, clock


@pytest.mark.parametrize("ticker,family,settlement", [("AL30", "BONOS", "INMEDIATA"),
    ("YMCJO", "OBLIGACIONES", "A-24HS")])
def test_signed_first_post_ack_book_settles_paper_once(tmp_path, monkeypatch, ticker, family, settlement):
    store, paper_id, key, decision, book, _, _ = setup(
        tmp_path, monkeypatch, ticker=ticker, family=family, settlement=settlement)
    book(20, 101)  # At the ACK. A fixture with the best price cannot be chosen.
    expected = book(30, 100)
    book(40, 130)
    witness = authority.attest_market_sale_book2(store.path, decision_key=decision,
                                                signing_key_path=key, release=RELEASE)
    assert witness["payload"]["book2"] == expected
    broker = PaperBroker(store, initial_cash="1000")
    at = (AT + 50 * MILLISECOND).isoformat()
    before = broker._cash(as_of=AT, currency="ARS")
    receipt = apply_paper_market_sale(store, broker, paper_id=paper_id, applied_at=at, witness=witness)
    assert receipt["closed_paper"] and receipt["nominal"] == "100"
    assert receipt == apply_paper_market_sale(store, broker, paper_id=paper_id, applied_at=at, witness=witness)
    assert receipt["causal_sale"]["book2_sha256"] == expected["receipt_sha256"]
    with store.connect() as db:
        state = snapshot_at(db, paper_id, at)
        assert state.residual_nominal == 0 and state.cash_by_currency == ()
        assert db.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 1
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
    assert store.open_positions() == []
    assert broker._cash(as_of=at, currency="ARS") == before + (
        Decimal(receipt["net_proceeds"]) if settlement == "INMEDIATA" else 0)
    if settlement == "A-24HS":
        monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "false")
        assert broker._cash(as_of=AT + timedelta(days=3), currency="ARS") == before
        monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
        assert broker._cash(as_of=at, currency="ARS") == before
    assert broker._cash(as_of=AT + timedelta(days=3), currency="ARS") == before + Decimal(receipt["net_proceeds"])


def test_observer_distinguishes_missing_and_changed_generation(tmp_path, monkeypatch):
    store, paper_id, key, decision, book, _, _ = setup(tmp_path, monkeypatch, ticker="AL30", family="BONOS")
    def attest():
        return authority.attest_market_sale_book2(store.path, decision_key=decision,
                                                  signing_key_path=key, release=RELEASE)

    with pytest.raises(ValueError, match="NO_AUTHENTIC_NATIVE_RECEIPT"):
        attest()
    book(30, 100, generation="d" * 64)
    with pytest.raises(ValueError, match="PPI_PIT_BOOK2_SESSION_CHANGED"):
        attest()
    with pytest.raises(ValueError, match="INDEPENDENT_AUTHORITY_UNBOUND"):
        apply_paper_market_sale(store, PaperBroker(store), paper_id=paper_id,
                               applied_at=AT + 50 * MILLISECOND, book_bytes=b"caller-self-attested")
    assert store.open_positions()[0]["paper_id"] == paper_id


def test_observer_refuses_evicted_book1_instead_of_substituting_the_latest_book(tmp_path, monkeypatch):
    store, _, key, decision, book, _, clock = setup(tmp_path, monkeypatch, ticker="AL30", family="BONOS")
    for ms in range(30, 42):
        book(ms, 100)
    clock["ms"] = 50
    with pytest.raises(ValueError, match="PPI_PIT_BOOK1_NOT_RETAINED"):
        authority.attest_market_sale_book2(store.path, decision_key=decision,
                                           signing_key_path=key, release=RELEASE)


def test_witness_scope_tampering_and_atomic_failure_leave_the_held_lot_intact(tmp_path, monkeypatch):
    store, paper_id, key, decision, book, _, _ = setup(tmp_path, monkeypatch, ticker="YMCJO", family="OBLIGACIONES")
    book(30, 100)
    witness = authority.attest_market_sale_book2(store.path, decision_key=decision,
                                                signing_key_path=key, release=RELEASE)
    broker = PaperBroker(store)
    at = AT + 50 * MILLISECOND
    with pytest.raises(ValueError, match="FI_NATIVE_SALE_SIGNED_REQUEST_MISMATCH"):
        apply_paper_market_sale(store, broker, paper_id="PAPER-OTHER", applied_at=at, witness=witness)
    with pytest.raises(ValueError, match="FI_NATIVE_SALE_SIGNED_VN_EXCEEDED"):
        apply_paper_market_sale(store, broker, paper_id=paper_id, applied_at=at, nominal="101", witness=witness)
    tampered = json.loads(json.dumps(witness))
    tampered["payload"]["book2"]["raw_payload"]["bids"][0]["price"] = 500
    with pytest.raises(ValueError, match="FI_MARKET_SALE_BOOK2_SIGNATURE_INVALID"):
        apply_paper_market_sale(store, broker, paper_id=paper_id, applied_at=at, witness=tampered)
    with store.connect() as db:
        db.execute("""CREATE TRIGGER reject_native_sale BEFORE INSERT ON paper_fi_native_sales
            BEGIN SELECT RAISE(ABORT,'SYNTHETIC_BOOKING_FAILURE'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="SYNTHETIC_BOOKING_FAILURE"):
        apply_paper_market_sale(store, broker, paper_id=paper_id, applied_at=at, witness=witness)
    with store.connect() as db:
        for table in ("paper_fi_native_sales", "paper_fi_economic_events", "paper_fi_lots", "paper_fi_cash_entries"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
    assert store.open_positions()[0]["quantity"] == "100"


def test_sale_allocates_only_executable_vn_and_reopens_the_same_residual_after_restart(tmp_path, monkeypatch):
    store, paper_id, key, decision, book, _, _ = setup(
        tmp_path, monkeypatch, ticker="AL30", family="BONOS", settlement="INMEDIATA", bid_qty=320)
    book(30, 100)
    witness = authority.attest_market_sale_book2(store.path, decision_key=decision,
                                                signing_key_path=key, release=RELEASE)
    at = AT + 50 * MILLISECOND
    receipt = apply_paper_market_sale(store, PaperBroker(store), paper_id=paper_id, applied_at=at, witness=witness)
    assert (receipt["nominal"], receipt["residual_after"], receipt["closed_paper"]) == ("32", "68", False)
    assert PaperBroker(type(store)(store.path)).store.open_positions()[0]["quantity"] == "68"
    with store.connect() as db:
        assert snapshot_at(db, paper_id, at).residual_nominal == 68
        assert db.execute("SELECT COUNT(*) FROM paper_fi_cash_entries").fetchone()[0] == 0


def test_existing_exit_supervisor_applies_authenticated_sale_without_generic_spot_close(tmp_path, monkeypatch):
    from bm_exit_supervisor import PositionExitSupervisor

    store, paper_id, key, decision, book, _, _ = setup(
        tmp_path, monkeypatch, ticker="AL30", family="BONOS", settlement="A-24HS")
    book(30, 100)
    witness = authority.attest_market_sale_book2(store.path, decision_key=decision,
                                                signing_key_path=key, release=RELEASE)
    at = (AT + 50 * MILLISECOND).isoformat()
    def witness_for(persisted_decision):
        assert persisted_decision == decision
        return witness
    def fail_generic(*args, **kwargs):
        pytest.fail("FI never uses generic spot close")
    broker = PaperBroker(store, clock_fn=lambda: at, market_sale_witness=witness_for)
    sup = PositionExitSupervisor(broker, clock_fn=lambda: at, close_fn=fail_generic)
    position, = store.open_positions()
    result = sup.supervise(position, store.latest_quote(position), at)
    assert (result.state, result.paper_id) == ("CLOSED", paper_id)


def test_coupon_earned_before_sale_stays_in_its_payout_currency(tmp_path, monkeypatch):
    from cf_sale_settlement import conservative_unconfirmed_availability

    store, paper_id, key, decision, book, _, _ = setup(tmp_path, monkeypatch, ticker="AL30", family="BONOS")
    opened = store.open_positions()[0]["opened_at"]
    book(30, 100)
    witness = authority.attest_market_sale_book2(store.path, decision_key=decision,
                                                signing_key_path=key, release=RELEASE)
    broker = PaperBroker(store)
    at = AT + 50 * MILLISECOND
    apply_paper_market_sale(store, broker, paper_id=paper_id, applied_at=at, witness=witness)
    receipt = issuer_receipt(conservative_unconfirmed_availability("A-24HS", opened).isoformat())
    assert apply(store, receipt).cash_by_currency == (("USD", Decimal(2)),)
    assert broker._cash(as_of=AT + timedelta(hours=3), currency="USD") == 2
    assert not store.open_positions()
