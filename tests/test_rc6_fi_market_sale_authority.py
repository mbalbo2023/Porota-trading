"""Documented PPI REST shape, independent ephemeral signer, PAPER-only ledger."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from be_paper_engine import PaperBroker, PaperStore, Quote
from bs_instrument_contracts import contract_from_metadata
from bu_instrument_catalog import init_schema as init_catalogue, normalize_record, persist
from rc6_fixed_income_event_ledger import snapshot_at
from rc6_fixed_income_paper_market_sale import apply_paper_market_sale
import rc6_fixed_income_sale_authority as authority
import rc6_paper_causal_decision_receipt as causal
from rc6_ppi_shared_feed import PPIReadOnlyNativeReceiptStore, PPISharedMarketFeed
from test_rc6_fixed_income_event_ledger import apply, lot, receipt as issuer_receipt


AT = datetime(2026, 10, 29, 14, tzinfo=timezone.utc)
MILLISECOND = timedelta(milliseconds=1)
RELEASE = "a" * 64
SESSION = "b" * 64
ARCHIVE = json.loads((Path(__file__).resolve().parents[1]
    / "docs/evidence/rc6-fi-paper-sale-sources.json").read_text())


def native_sale_fixture(tmp_path, monkeypatch, *, contract, scenario_at, bid_qty=1000):
    identity = contract.key
    ticker, family, market, currency, settlement = identity
    provider = {"ticker": ticker, "type": "ON" if family == "OBLIGACIONES" else family,
                "market": market, "currency": currency, "settlement": settlement,
                "_discovery_source": "PPI_PRIMARY"}
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
    source = {"schema": authority.ECONOMICS_SCHEMA, "identity": list(identity),
              "ppi_identity": provider, "known_at": (scenario_at - timedelta(days=1)).isoformat(),
              "quantity_unit": "VN", "bid_size_unit": "VN",
              "cash_multiplier": str(contract.cash_multiplier),
              "minimum_quantity": str(contract.minimum_quantity), "quantity_step": str(contract.quantity_step),
              "ppi_capture_sha256": hashlib.sha256(b"Synthetic PPI units only").hexdigest()}
    source_bytes = causal._json(source).encode()
    (tmp_path / f"ppi-economics-{causal._sha(source_bytes)}.json").write_bytes(source_bytes)
    raw = authority.build_market_sale_policy([source_bytes], release_sha256=RELEASE,
        at=scenario_at, valid_until=scenario_at + timedelta(days=14))
    (tmp_path / "market-sale-policy.json").write_bytes(raw)
    monkeypatch.setenv(authority.POLICY_PIN, hashlib.sha256(raw).hexdigest())
    feed = PPISharedMarketFeed(ppi_catalog_rows=[{"ticker": ticker, "type": family,
        "market": market, "currency": currency}], catalog_source_digest="c" * 64)
    feed.register_consumer("exit_reader", watched_five_keys=[identity])
    clock = {"ms": 50}
    cache = PPIReadOnlyNativeReceiptStore(feed, max_receipts=8, max_age_seconds=5,
        clock=lambda: scenario_at + clock["ms"] * MILLISECOND,
        monotonic_ns=lambda: 1_000_000_000 + clock["ms"] * 1_000_000)

    def book(ms, price, *, generation=SESSION, quantity=bid_qty, ask=None, offer_quantity=1000):
        at = scenario_at + ms * MILLISECOND
        return cache.publish_native_rest_receipt(method="book", five_key=identity,
            raw_payload={"date": at.isoformat(), "bids": [{"position": 0, "price": price, "quantity": quantity}],
                         "offers": [{"position": 0, "price": ask if ask is not None else price + 1,
                                     "quantity": offer_quantity}]},
            native_at=at, received_at=at, received_monotonic_ns=1_000_000_000 + ms * 1_000_000,
            session_fingerprint=generation, guard_budget_receipt_sha256="f" * 64, capture_type="REST_BOOK")

    def read_book2(path, intent, cutoff, clock_ns):
        return cache.read_native(consumer="exit_reader", method="book", five_key=intent["identity"],
            book1_receipt_sha256=intent["book1"]["receipt_sha256"], commit_at=cutoff,
            commit_monotonic_ns=clock_ns)

    monkeypatch.setattr(authority, "_read_first_book2", read_book2)
    monkeypatch.setattr(causal, "_now", lambda: (scenario_at + 20 * MILLISECOND).isoformat())
    monkeypatch.setattr(causal, "time", SimpleNamespace(monotonic_ns=lambda: 1_020_000_000))
    return key, book, cache, clock


def setup(tmp_path, monkeypatch, *, ticker, family, settlement="A-24HS",
          currency="ARS", bid_qty=1_000, initial=None, prepare=True, scenario_at=AT):
    store, _ = lot(tmp_path)
    paper_id = "PAPER-AL30"
    contract = contract_from_metadata(ticker, family, {
        "market": "BYMA", "currency": currency, "settlement": settlement, "cash_multiplier": "0.01",
        "quantity_step": "1", "minimum_quantity": "1", "metadata_source": "SYNTHETIC_PPI_UNITS",
    })
    provider = {"ticker": ticker, "type": "ON" if family == "OBLIGACIONES" else family,
                "market": "BYMA", "currency": currency, "settlement": settlement,
                "_discovery_source": "PPI_PRIMARY"}
    record = normalize_record(provider, None, scenario_at.isoformat(), "SYNTHETIC_PPI_FIXTURE")
    init_catalogue(store)
    with store.connect() as db:
        features = json.loads(db.execute("SELECT features_json FROM paper_positions").fetchone()[0])
        features.update(financial_contract=asdict(contract), contract_cash_multiplier="0.01")
        db.execute("UPDATE paper_positions SET symbol=?,asset_class=?,settlement=?,currency=?,features_json=?",
                  (ticker, family, settlement, currency, json.dumps(features, default=str)))
        record["raw"]["financial_contract_v17"] = json.loads(json.dumps(asdict(contract), default=str))
        persist(db, record)
    key, book, cache, clock = native_sale_fixture(
        tmp_path, monkeypatch, contract=contract, scenario_at=scenario_at, bid_qty=bid_qty)
    first = book(0, 99) if initial is None else book(0, initial["bid"], quantity=initial["bid_size"],
        ask=initial["ask"], offer_quantity=initial["ask_size"])
    quote = Quote(ticker, family, settlement, Decimal(0), contract=contract, currency=currency, market="BYMA",
                  ppi_native_rest_book=first, **authority.native_book_fields(first, contract.key))
    store.add_quote(quote)
    position, = store.open_positions()
    assert store.latest_quote(position).ppi_native_rest_book == first
    if not prepare:
        return store, paper_id, key, None, book, cache, clock
    decision = authority.prepare_market_sale(store, position, store.latest_quote(position), "MAX_HOLD_PAPER",
                                             scenario_at + 10 * MILLISECOND)
    ack = causal.attest_after_commit(store.path, decision_key=decision, signing_key_path=key,
                                    expected_release_sha256=RELEASE)
    assert ack["signature_created"]
    return store, paper_id, key, decision, book, cache, clock


def reviewed_market_entry(tmp_path, monkeypatch, family):
    from test_rc6_fixed_income_runtime import AT as ENTRY_AT, YESTERDAY, reviewed_bundle

    opening, evidence, manifest = reviewed_bundle(tmp_path, family, settlement="INMEDIATA")
    monkeypatch.setenv("POROTA_FI_EVIDENCE_MANIFEST_SHA256", manifest)
    sector_map = tmp_path / "reviewed-sectors.csv"
    sector_map.write_text(
        "ticker,family,market,currency,settlement,sector,source,author,effective_at,reviewed\n"
        + ",".join((*opening.contract.key, "SYNTHETIC_SECTOR", "fixture:reviewed-sector",
                    "SYNTHETIC_TEST", YESTERDAY.isoformat(), "true")) + "\n",
        encoding="utf-8")
    monkeypatch.setenv("POROTA_SECTOR_MAP_PATH", str(sector_map))
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "BINDING")
    key, book, _, clock = native_sale_fixture(
        evidence, monkeypatch, contract=opening.contract, scenario_at=ENTRY_AT)
    return opening, key, book, clock


@pytest.mark.parametrize("family", ["BONOS", "OBLIGACIONES"])
def test_reviewed_market_lot_buys_then_sells_after_signer_ack(tmp_path, monkeypatch, family):
    from bm_exit_supervisor import PositionExitSupervisor
    from dh_paper_dynamic_risk_gate_hf6 import open_stop_risk
    from rc6_fixed_income_sale_witness import ExitReaderSaleWitness, read_witness

    opening, key, book, clock = reviewed_market_entry(tmp_path, monkeypatch, family)
    entry_at = datetime.fromisoformat(opening.observed_at)
    store = PaperStore(str(tmp_path / "roundtrip.sqlite"))
    at = {"now": entry_at}
    broker = PaperBroker(store, initial_cash="1000000", clock_fn=lambda: at["now"].isoformat())
    store.add_quote(opening)
    with monkeypatch.context() as missing_signer:
        missing_signer.setattr(causal, "PINNED_DECISION_SIGNER_KEY_SHA256", None)
        action, _, _, facts = broker.decide(opening)
        assert (action, facts["reason_code"]) == ("HOLD", "FI_NATIVE_EVENT_EXIT_ENGINE_NOT_BOUND")
    broker.on_quote(opening)
    broker.on_quote(opening)
    position, = store.open_positions()
    paper_id = position["paper_id"]
    features = json.loads(position["features_json"])
    assert Decimal(position["quantity"]) == Decimal(100)
    assert features["economics_mode"] == "BINDING"
    assert features["exit_policy"]["execution"] == "SUPERVISED_SIGNED_NATIVE_BOOK2"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_fi_lots").fetchone()[0] == 1
        assert open_stop_risk(broker, "ARS", entry_at, connection=db) == Decimal(
            features["candidate_stop_risk"])

    # Restart the broker. The reviewed horizon triggers exit with no stop/target crossing.
    horizon = datetime.fromisoformat(features["exit_policy"]["exit_at"])
    offset = (horizon - entry_at) // MILLISECOND
    clock["ms"] = offset
    first = book(offset, float(opening.bid), ask=float(opening.ask))
    exit_quote = replace(opening, last=Decimal(0), trade_at=None, last_kind="BOOK_ONLY",
                         ppi_native_rest_book=first, **authority.native_book_fields(first, opening.contract.key))
    at["now"] = horizon
    store = PaperStore(store.path)
    broker = PaperBroker(store, initial_cash="1000000", clock_fn=lambda: at["now"].isoformat())
    supervisor = PositionExitSupervisor(broker, max_hold_minutes=360,
                                        clock_fn=lambda: at["now"].isoformat())
    pending = supervisor.supervise(position, exit_quote, horizon.isoformat())
    assert pending.cause == "MAX_HOLD_PAPER" and pending.reason == "FI_NATIVE_SIGNED_BOOK2_PENDING"
    witness = ExitReaderSaleWitness(store, key)
    monkeypatch.setattr(causal, "_now", lambda: (horizon + 20 * MILLISECOND).isoformat())
    monkeypatch.setattr(causal, "time", SimpleNamespace(
        monotonic_ns=lambda: 1_000_000_000 + (offset + 20) * 1_000_000))
    decision = witness.acknowledge(paper_id)
    clock["ms"] = offset + 40
    book(offset + 20, 104)
    first_after_ack = book(offset + 30, 101)
    book(offset + 40, 110)
    witness.witness_first_later_book(decision)
    at["now"] = horizon + 40 * MILLISECOND
    final = supervisor.supervise(position, exit_quote, at["now"].isoformat())
    assert final.state == "CLOSED"
    assert not store.open_positions()
    with store.connect() as db:
        assert [row[0] for row in db.execute("SELECT side FROM paper_fills ORDER BY id")] == [
            "BUY_SIMULATED", "SELL_SIMULATED"]
        assert snapshot_at(db, paper_id, at["now"]).residual_nominal == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
    assert read_witness(store, decision)["payload"]["book2"] == first_after_ack


def test_market_buy_freeze_cannot_admit_after_exit_signer_is_revoked(tmp_path, monkeypatch):
    opening, _, _, _ = reviewed_market_entry(tmp_path, monkeypatch, "BONOS")
    store = PaperStore(str(tmp_path / "no-signer.sqlite"))
    broker = PaperBroker(store, initial_cash="1000000", clock_fn=lambda: opening.observed_at)
    action, score, reason, features = broker.decide(opening)
    assert action == "BUY"
    store.add_quote(opening)
    key = (f"{features['strategy']}:{opening.symbol}:{opening.asset_class}:{opening.settlement}:"
           f"{opening.currency}:{opening.market}:{opening.observed_at[:16]}:BUY")
    features["native_decision_key"] = key
    assert store.record_decision(key, opening, action, score, reason, features)
    monkeypatch.setattr(causal, "PINNED_DECISION_SIGNER_KEY_SHA256", None)
    assert broker.admit_paper_candidate(opening, score, features) == (
        False, "FI_NATIVE_EVENT_EXIT_ENGINE_NOT_BOUND", None)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM paper_fi_lots").fetchone()[0] == 0


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


@pytest.mark.parametrize("ticker", tuple(ARCHIVE["variants"]))
def test_exit_reader_and_supervisor_replay_archived_wheel_books(tmp_path, monkeypatch, ticker):
    from bm_exit_supervisor import PositionExitSupervisor
    from bq_exit_policy import PaperSessionPolicy
    from bv_paper_runtime import collect_exit_books
    from rc6_fixed_income_sale_witness import ExitReaderSaleWitness, read_witness

    variant = ARCHIVE["variants"][ticker]
    identity = tuple(variant["identity"])
    _, family, _, currency, term = identity
    first, later = variant["archived_books"]
    assert variant["normalized_model"] == {"currency": currency, "settlement": term,
        "market": "BYMA", "family": family, "minimum_quantity": "1",
        "quantity_step": "1", "cash_multiplier": "0.01"}
    assert first["has_native_receipt"] is later["has_native_receipt"] is False
    at = datetime(2026, 11, 24, 14, tzinfo=timezone.utc)
    store, paper_id, key, _, book, _, _ = setup(tmp_path, monkeypatch, ticker=ticker,
        family=family, currency=currency, settlement=term, initial=first, prepare=False, scenario_at=at)
    model = configure_activated_model(monkeypatch)
    monkeypatch.setattr(causal, "PINNED_RELEASE_SHA256", model["release"])
    monkeypatch.delenv("POROTA_RC6_RELEASE_SHA256")
    policy = PaperSessionPolicy()
    clock = {"at": (at + 10 * MILLISECOND).isoformat()}
    broker = PaperBroker(store, clock_fn=lambda: clock["at"], session_policy=policy, slippage_bps=0)
    supervisor = PositionExitSupervisor(broker, clock_fn=lambda: clock["at"], session_policy=policy)
    position, = store.exit_positions()[0]
    verdict = supervisor.supervise(position, store.latest_quote(position), clock["at"])
    assert verdict.reason == "FI_NATIVE_SIGNED_BOOK2_PENDING"
    with store.connect() as db:
        decision, = db.execute("SELECT decision_key FROM paper_fi_sale_witness_handoff").fetchone()
        assert db.execute("SELECT COUNT(*) FROM paper_causal_decision_receipts").fetchone()[0] == 0
    assert read_witness(store, decision) is None

    def capture_once(symbol, observed_family, settlement):
        assert (symbol, observed_family, settlement) == (ticker, family, term)
        with store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM paper_causal_decision_receipts").fetchone()[0] == 1
        # Re-create PPI's documented payload shape with the retained prices/sizes.
        # The replay clocks and an extra later, better quote are synthetic.
        book(20, 98)
        captured = book(30, later["bid"], ask=later["ask"], quantity=later["bid_size"],
                        offer_quantity=later["ask_size"])
        roles["first_eligible"] = captured
        return book(40, str(Decimal(later["bid"]) + 1), ask=str(Decimal(later["ask"]) + 1),
                    quantity=1000)

    roles = {}
    clock["at"] = (at + 50 * MILLISECOND).isoformat()
    monkeypatch.setattr("bf_production_paper_observer.now_iso", lambda: clock["at"])
    reader = SimpleNamespace(book_with_native_receipt=capture_once)
    test_signer = Ed25519PrivateKey.from_private_bytes(key.read_bytes())
    assert collect_exit_books(reader, store, policy, clock["at"],
                              sale_witness=ExitReaderSaleWitness(store, exit_signer=test_signer)) == 0
    witness = read_witness(store, decision)
    assert witness["payload"]["book2"] == roles["first_eligible"]
    next_quote = store.latest_quote(position)
    assert next_quote.bid > Decimal(later["bid"])
    result = supervisor.supervise(position, next_quote, clock["at"])
    nominal = min(int(Decimal(later["bid_size"]) * broker.participation), 100)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_causal_decision_freezes").fetchone()[0] == 1
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
        sell = db.execute("SELECT quantity,price FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()
        if nominal:
            assert tuple(map(Decimal, sell)) == (nominal, Decimal(later["bid"]))
            assert result.state == ("CLOSED" if nominal == 100 else "EXIT_PARTIAL")
            saved = json.loads(db.execute("SELECT result_json FROM paper_fi_native_sales").fetchone()[0])
            assert saved["causal_sale"]["economic_policy_authority"] == "NORMALIZED_HISTORICAL_PPI_PAPER_ASSUMPTIONS"
        else:
            assert sell is None
            assert result.reason == "FI_NATIVE_SALE_INSUFFICIENT_EXECUTABLE_DEPTH"


def configure_activated_model(monkeypatch):
    deployment = {"POROTA_FI_HISTORICAL_PAPER_SALE": "1", "POROTA_BUILD_SHA": "3" * 40,
                  "POROTA_CANDIDATE_TREE_SHA": "4" * 40, "DASHBOARD_OPERATION_MODE": "PRODUCTION_PAPER",
                  "EXECUTION_MODE": "SIMULATION", "REAL_ORDER_CAPABILITY": "BLOCKED",
                  "ALLOW_REAL_ORDERS": "false"}
    for key, value in deployment.items():
        monkeypatch.setenv(key, value)
    return authority.deployed_sale_model()


def test_paper_activation_pins_only_the_spawned_reader_and_revokes_on_exit(monkeypatch):
    from rc6_fixed_income_sale_witness import ExitReaderKeyChannel

    model = configure_activated_model(monkeypatch)
    monkeypatch.setattr(causal, "PINNED_RELEASE_SHA256", None)
    monkeypatch.setattr(causal, "PINNED_DECISION_SIGNER_KEY_SHA256", None)
    channel = ExitReaderKeyChannel(model)
    assert channel.status()["state"] == "SIGNER_PENDING"
    root = Path(authority.__file__).resolve().parent
    previous_pin = None
    for _ in range(2):
        command = [sys.executable, "-c", (
            "from rc6_fixed_income_sale_witness import exit_reader_witness_from_environment\n"
            "import sys\n"
            "witness = exit_reader_witness_from_environment(None)\n"
            "assert witness.key_file is None and witness.exit_signer is not None\n"
            "sys.stdin.buffer.read(1)\n")]
        child = channel.launch(lambda *a, **k: subprocess.Popen(*a, **k, stdin=subprocess.PIPE), command, cwd=root)
        try:
            deadline = time.monotonic() + 5
            while channel.status()["state"] == "SIGNER_PENDING" and time.monotonic() < deadline:
                channel.poll(child)
                time.sleep(.01)
            status = channel.status()
            assert status["state"] == "ACTIVE_PAPER" and status["variants"] == 6
            assert status["signer_pid"] == child.pid and status["signer_pid"] != os.getpid()
            assert status["signer_key_sha256"] == causal.PINNED_DECISION_SIGNER_KEY_SHA256
            assert status["signer_key_sha256"] != previous_pin
            previous_pin = status["signer_key_sha256"]
        finally:
            child.communicate(input=b"x", timeout=5)
            channel.poll(child)
        assert child.returncode == 0 and causal.PINNED_DECISION_SIGNER_KEY_SHA256 is None
        assert causal.PINNED_RELEASE_SHA256 is None
    monkeypatch.setenv("REAL_ORDER_CAPABILITY", "OPEN")
    with pytest.raises(ValueError, match="FI_PAPER_HISTORICAL_MODEL_MODE_BLOCKED"):
        authority.deployed_sale_model()


def test_release_policy_rejects_request_only_term_and_cannot_self_attest_source(tmp_path, monkeypatch):
    store, _, _, _, _, _, _ = setup(tmp_path, monkeypatch, ticker="GD30", family="BONOS", prepare=False)
    position, = store.exit_positions()[0]
    quote = store.latest_quote(position)
    path, = tmp_path.glob("ppi-economics-*.json")
    raw = json.loads(path.read_bytes())
    raw["ppi_identity"].pop("settlement")
    altered = causal._json(raw).encode()
    with pytest.raises(ValueError, match="FI_MARKET_SALE_PPI_VARIANT_OR_TERM_UNVERIFIED"):
        authority.build_market_sale_policy([altered], release_sha256=RELEASE,
            at=AT, valid_until=AT + timedelta(days=1))
    path.write_bytes(altered)
    with pytest.raises(ValueError, match="FI_EVIDENCE_HASH_MISMATCH"):
        authority.market_sale_policy(store, quote.contract.key, quote.contract, AT)


def test_signer_rotation_replans_without_rewriting_the_previous_ack(tmp_path, monkeypatch):
    from rc6_fixed_income_sale_witness import ExitReaderSaleWitness

    store, paper_id, _, decision, book, _, _ = setup(
        tmp_path, monkeypatch, ticker="GD30", family="BONOS")
    signer = Ed25519PrivateKey.generate()
    public = signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(causal, "PINNED_DECISION_SIGNER_KEY_SHA256", causal._sha(public))
    reader = ExitReaderSaleWitness(store, exit_signer=signer)
    assert reader.acknowledge(paper_id) is None
    later = book(30, 100)
    position, = store.open_positions()
    old = store.latest_quote(position)
    from dataclasses import replace
    new = replace(old, ppi_native_rest_book=later, **authority.native_book_fields(later, old.contract.key))
    new_decision = authority.prepare_market_sale(store, position, new, "MAX_HOLD_PAPER",
                                                 AT + 40 * MILLISECOND)
    assert new_decision != decision
    with store.connect() as db:
        assert db.execute("SELECT decision_key FROM paper_causal_decision_receipts").fetchone()[0] == decision
        assert db.execute("SELECT decision_key FROM paper_fi_sale_witness_handoff").fetchone()[0] == new_decision


def test_fi_exit_during_holiday_and_next_preopen_stays_read_only(tmp_path, monkeypatch):
    from bm_exit_supervisor import PositionExitSupervisor
    from bq_exit_policy import PaperSessionPolicy
    from bv_paper_runtime import collect_exit_books

    store, _, _, _, _, _, _ = setup(tmp_path, monkeypatch, ticker="GD30", family="BONOS", prepare=False)
    policy = PaperSessionPolicy()
    position, = store.exit_positions()[0]
    def unexpected_book(*args):
        pytest.fail("A market closed by the PAPER calendar must not need a book")

    reader = SimpleNamespace(book_with_native_receipt=unexpected_book)
    for at, reason in [("2026-11-23T11:00:00-03:00", "MARKET_CLOSED_OR_CALENDAR_UNKNOWN"),
                       ("2026-11-24T10:16:00-03:00", "OUTSIDE_PAPER_EXECUTION_WINDOW")]:
        broker = PaperBroker(store, session_policy=policy, clock_fn=lambda: at)
        supervisor = PositionExitSupervisor(broker, session_policy=policy, clock_fn=lambda: at)
        assert collect_exit_books(reader, store, policy, at) == 0
        verdict = supervisor.supervise(position, store.latest_quote(position), at)
        assert verdict.state == "EXIT_PENDING_MARKET_CLOSED"
        assert verdict.reason == reason
    assert policy.execution_error(position, "2026-11-24T11:00:00-03:00") == ""
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_causal_decision_freezes").fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


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
