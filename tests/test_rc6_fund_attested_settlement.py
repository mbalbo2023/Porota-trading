"""Canonical PAPER FCI settlement with native-shaped synthetic signed receipts."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
import sqlite3

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import bu_instrument_catalog as catalog
import rc6_paper_family_lifecycle as lifecycle
from be_paper_engine import PaperBroker, PaperStore
from rc6_fund_paper_allocator import (
    FundAllocationBudget, FundAllocationEvidence, FundNavMark,
    assess_fund_cash_parking, simulate_fund_subscription,
)
from rc6_fund_paper_reconciler import FundClassEvidence, FundReceipt, fund_evidence_digest
from rc6_fund_source_quarantine import (
    SETTLEMENT_AUTHORITY_FILE, SETTLEMENT_AUTHORITY_SCHEMA, SETTLEMENT_SCHEMA,
    settlement_attestation_bytes,
)
from scripts import rc6_operator_paper_rearm as operator
from tests.test_rc6_a234_cafci_fci_reference import parse, reference_row, synthetic_xlsx


SYMBOL = "FCI.FIXTURE.B"
KNOWN = "2026-11-23T16:00:00-03:00"
OPEN = "2026-11-24T13:00:00-03:00"
SETTLEMENT = "2026-11-25T12:00:00-03:00"
REDEMPTION = "2026-11-25T13:00:00-03:00"
END = "2026-11-27T12:00:00-03:00"
DIGEST = hashlib.sha256(b"offline evidence example").hexdigest()
ACCOUNT = hashlib.sha256(b"offline account scope").hexdigest()


@pytest.fixture
def paper_fund(tmp_path, monkeypatch):
    return make_paper_fund(tmp_path, monkeypatch, SYMBOL, "900001")


def make_paper_fund(tmp_path, monkeypatch, symbol, class_id, *, admission_guard=None):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    store = PaperStore(str(tmp_path / "fund.db"))
    broker = PaperBroker(store, initial_cash="2000")
    raw = {"ticker": symbol, "type": "FCI", "market": "BYMA", "currency": "Pesos",
           "settlement": "INMEDIATA"}
    identity = catalog.normalize_record(raw, None, OPEN, "SYNTHETIC_PRIMARY")
    assert not catalog.ppi_full_key_evidence_reason(identity)
    terms = lifecycle.PaperFundTerms(symbol, "FCI", "ARS", "BYMA", "INMEDIATA",
                                    "SYNTHETIC_TERMS", subscription_min="1000",
                                    subscription_step="0.01")
    evidence = FundClassEvidence(terms, symbol, "SYNTHETIC_REGULATION", DIGEST, KNOWN)
    # Use the real CAFCI parser and allocation guard on local, explicitly
    # synthetic inputs. The class binding, cutoff and fees are fixture data.
    reference, = parse(synthetic_xlsx(reference_row(name="Fondo de prueba - Clase " + symbol.rsplit(".", 1)[-1],
        effective="20/11/26", nav_mil="10000", cafci_id=class_id, cnv_id="9000")),
        captured_at=KNOWN, http_last_modified_at="Fri, 20 Nov 2026 23:20:00 GMT",
        source_filename="20261120_Planilla_Diaria_A.xlsx").classes
    assert reference.cafci_class_id == int(class_id)
    assert reference.runtime_entry_authority is False and reference.currency_raw == terms.currency
    assert reference.minimum_subscription_reported == terms.subscription_min
    nav = FundNavMark(
        reference.nav_per_unit, reference.nav_effective_date.isoformat() + "T17:30:00-03:00",
        "2026-11-20T20:20:00-03:00", reference.known_at.isoformat(),
        f"SYNTHETIC_CAFCI_CLASS:{reference.cafci_class_id}", reference.source_sha256)
    allocation_evidence = FundAllocationEvidence(
        evidence, "SYNTHETIC_TEST_ONLY", OPEN[:10], "SYNTHETIC_CLASS_CALENDAR", DIGEST,
        KNOWN, "15:00", "America/Argentina/Buenos_Aires", 2, Decimal(0),
        "SYNTHETIC_ACCOUNT_FEES", DIGEST, KNOWN, nav)
    budget = FundAllocationBudget(Decimal("2000"), Decimal("250"), Decimal("1500"),
                                  Decimal("1001.01"), 4, 5)
    external = assess_fund_cash_parking(replace(allocation_evidence, provenance="UNVERIFIED_EXTERNAL"),
                                        budget, as_of=OPEN)
    assert external.status == "HOLD" and "FCI_EXTERNAL_PROVENANCE_NOT_AUTHENTICATED" in external.reason_codes
    late = assess_fund_cash_parking(allocation_evidence, budget, as_of=OPEN.replace("T13:", "T15:"))
    assert late.status == "HOLD" and "FCI_CLASS_CUTOFF_PASSED" in late.reason_codes
    result = simulate_fund_subscription(broker.family_paper, evidence=allocation_evidence,
        budget=budget, lifecycle_id="fund-1", event_id="e0", occurred_at=OPEN,
        cash_guard=lambda db: broker._cash(OPEN, "ARS", connection=db, for_execution=True),
        admission_guard=admission_guard if admission_guard is not None else lambda _db: "")
    assert result["state"] == "SUBSCRIBE_REQUESTED" and broker._cash(OPEN, "ARS") == Decimal("998.99")
    monkeypatch.setenv("PAPER_V17_DB_PATH", store.path)
    def trusted_operator_fixture(directory, *, receipt_name):
        assert directory == operator.OPERATOR_DIR_RUNTIME
        assert receipt_name == SETTLEMENT_AUTHORITY_FILE
        return {"schema": SETTLEMENT_AUTHORITY_SCHEMA,
                "signer_sha256": hashlib.sha256(public).hexdigest(), "account_scope_sha256": ACCOUNT}
    monkeypatch.setattr(operator, "_root_owned_receipt", trusted_operator_fixture)
    # The documented numeric movement fields alone do not authenticate fees,
    # the exact fund class or the NAV publication.
    allocation = {"ticker": symbol, "price": 10.0101, "quantity": 100, "amount": 1001.01}
    payment = {"ticker": symbol, "price": 10.30, "quantity": 100, "amount": 1029.00}
    nav_at = "2026-11-24T17:30:00-03:00"
    receipts = [
        FundReceipt("e2", "NAV_APPLIED", symbol, "ARS", "FUND_ADMINISTRATOR_NAV",
                    "SYNTHETIC_ADMIN_NAV", DIGEST, "2026-11-24T18:00:00-03:00",
                    "2026-11-24T18:01:00-03:00", nav_effective_at=nav_at, nav_per_unit="10.0101"),
        FundReceipt("e3", "SETTLED", allocation["ticker"], "ARS", "BROKER_FUND_ALLOCATION",
                    "SYNTHETIC_ALLOCATION", DIGEST, "2026-11-25T11:58:00-03:00",
                    "2026-11-25T11:59:00-03:00", nav_effective_at=nav_at,
                    nav_reference="SYNTHETIC_ADMIN_NAV", nav_per_unit=allocation["price"],
                    units=allocation["quantity"], explicit_fee=0, cash_amount=allocation["amount"]),
        FundReceipt("e5", "REDEEMED", payment["ticker"], "ARS", "BROKER_FUND_PAYMENT",
                    "SYNTHETIC_PAYMENT", DIGEST, "2026-11-27T11:58:00-03:00",
                    "2026-11-27T11:59:00-03:00", nav_effective_at="2026-11-26T17:30:00-03:00",
                    nav_reference="SYNTHETIC_PAYOUT_NAV", nav_per_unit=payment["price"],
                    units=payment["quantity"], explicit_fee="1.00", cash_amount=payment["amount"]),
    ]
    return store, broker, evidence, key, public, receipts


def attestation(fund, receipts, transitions, at, **changes):
    _, _, evidence, key, public, _ = fund
    time = datetime.fromisoformat(at)
    claims = {
        "schema": SETTLEMENT_SCHEMA, "account_scope_sha256": ACCOUNT,
        "identity": [evidence.terms.symbol, "FCI", "BYMA", "ARS", "INMEDIATA"], "lifecycle_id": "fund-1",
        "transitions": transitions, "class_evidence_sha256": fund_evidence_digest(evidence),
        "receipt_hashes": {receipt.event_id: fund_evidence_digest(receipt) for receipt in receipts},
        "payout_ars": "1029.00" if transitions[-1][-1] == "REDEEMED" else "0",
        "known_at": (time - timedelta(minutes=1)).isoformat(),
        "valid_until": (time + timedelta(minutes=1)).isoformat(),
        **changes,
    }
    return {"claims": claims, "public_key_hex": public.hex(),
            "signature_hex": key.sign(settlement_attestation_bytes(claims)).hex()}


def states(store):
    with store.connect() as db:
        assert tuple(db.execute("SELECT mode,real_orders_sent FROM observer_state").fetchone()) == (
            "PRODUCTION_PAPER", 0)
        return [row[0] for row in db.execute(
            "SELECT to_state FROM paper_family_lifecycle_events ORDER BY rowid")]


def apply(fund, receipts, proof):
    _, broker, evidence, _, _, _ = fund
    return broker.family_paper.record_fund_settlement(
        evidence, lifecycle_id="fund-1", receipts=receipts, proof=proof)


ALLOCATION = [["e1", "PENDING"], ["e2", "NAV_APPLIED"], ["e3", "SETTLED"]]
REQUEST = [["e4", "REDEEM_REQUESTED"]]
PAYMENT = [["e5", "REDEEMED"]]


def test_runtime_settlement_applies_signed_receipts_once_and_credits_exact_payment(paper_fund, monkeypatch):
    replay_attested_fund_settlement(paper_fund, monkeypatch)


def test_runtime_settlement_accepts_each_new_receipt_after_ledger_reopen(paper_fund, monkeypatch):
    store, _, evidence, key, public, receipts = paper_fund
    nav_time = "2026-11-24T18:02:00-03:00"
    monkeypatch.setattr(lifecycle, "_now", lambda: nav_time)
    nav_proof = attestation(paper_fund, receipts[:1], ALLOCATION[:2], nav_time)
    assert apply(paper_fund, receipts[:1], nav_proof).state == "NAV_APPLIED"

    reopened = PaperStore(store.path)
    broker = PaperBroker(reopened, initial_cash="2000")
    paper_fund = (reopened, broker, evidence, key, public, receipts)
    monkeypatch.setattr(lifecycle, "_now", lambda: SETTLEMENT)
    allocation = attestation(paper_fund, receipts[1:2], ALLOCATION[2:], SETTLEMENT)
    assert apply(paper_fund, receipts[1:2], allocation).units == Decimal("100")
    monkeypatch.setattr(lifecycle, "_now", lambda: REDEMPTION)
    redemption = attestation(paper_fund, [], REQUEST, REDEMPTION)
    assert apply(paper_fund, [], redemption).state == "REDEEM_REQUESTED"
    monkeypatch.setattr(lifecycle, "_now", lambda: END)
    payment = attestation(paper_fund, receipts[2:], PAYMENT, END)
    paid = apply(paper_fund, receipts[2:], payment)

    monkeypatch.setattr(lifecycle, "_now", lambda: "2026-11-27T15:00:00-03:00")
    assert apply(paper_fund, receipts[2:], payment) == paid
    assert paid.state == "REDEEMED" and paid.redemption_cash_ars == Decimal("1029.00")
    assert broker._cash(END, "ARS") == Decimal("2027.99")
    assert states(reopened) == ["SUBSCRIBE_REQUESTED", "PENDING", "NAV_APPLIED",
                                "SETTLED", "REDEEM_REQUESTED", "REDEEMED"]


def test_incremental_fund_settlement_rejects_rehashed_prior_nav(paper_fund, monkeypatch):
    store, _, _, _, _, receipts = paper_fund
    monkeypatch.setattr(lifecycle, "_now", lambda: SETTLEMENT)
    assert apply(paper_fund, receipts[:2], attestation(
        paper_fund, receipts[:2], ALLOCATION, SETTLEMENT)).state == "SETTLED"
    with store.connect() as db:
        event = db.execute("SELECT detail_json FROM paper_family_lifecycle_events WHERE event_id='e2'").fetchone()
        detail = json.loads(event["detail_json"])
        detail["fci_receipt_v1"]["nav_per_unit"] = "11"
        detail["fci_receipt_sha256"] = fund_evidence_digest(FundReceipt(**detail["fci_receipt_v1"]))
        db.execute("UPDATE paper_family_lifecycle_events SET detail_json=? WHERE event_id='e2'",
                   (json.dumps(detail),))

    monkeypatch.setattr(lifecycle, "_now", lambda: REDEMPTION)
    with pytest.raises(ValueError, match="FCI_SETTLEMENT_PRIOR_RECEIPT_UNVERIFIED"):
        apply(paper_fund, [], attestation(paper_fund, [], REQUEST, REDEMPTION))
    assert states(store) == ["SUBSCRIBE_REQUESTED", "PENDING", "NAV_APPLIED", "SETTLED"]


def test_runtime_fund_settlement_keeps_hash_only_ledger_compatible(paper_fund, monkeypatch):
    store, _, _, _, _, receipts = paper_fund
    monkeypatch.setattr(lifecycle, "_now", lambda: SETTLEMENT)
    assert apply(paper_fund, receipts[:2], attestation(
        paper_fund, receipts[:2], ALLOCATION, SETTLEMENT)).state == "SETTLED"
    with store.connect() as db:
        rows = db.execute("SELECT event_id, detail_json FROM paper_family_lifecycle_events "
                          "WHERE to_state IN ('NAV_APPLIED','SETTLED')").fetchall()
        for row in rows:
            detail = json.loads(row["detail_json"])
            del detail["fci_receipt_v1"], detail["fci_receipt_attestation"]
            db.execute("UPDATE paper_family_lifecycle_events SET detail_json=? WHERE event_id=?",
                       (json.dumps(detail), row["event_id"]))

    monkeypatch.setattr(lifecycle, "_now", lambda: REDEMPTION)
    with pytest.raises(ValueError, match="FCI_SETTLEMENT_PRIOR_RECEIPT_REQUIRED"):
        apply(paper_fund, [], attestation(paper_fund, [], REQUEST, REDEMPTION))
    assert states(store) == ["SUBSCRIBE_REQUESTED", "PENDING", "NAV_APPLIED", "SETTLED"]
    assert apply(paper_fund, receipts[:2], attestation(
        paper_fund, receipts[:2], REQUEST, REDEMPTION)).state == "REDEEM_REQUESTED"


def replay_attested_fund_settlement(paper_fund, monkeypatch):
    store, broker, evidence, _, _, receipts = paper_fund
    monkeypatch.setattr(lifecycle, "_now", lambda: SETTLEMENT)
    proof = attestation(paper_fund, receipts[:2], ALLOCATION, SETTLEMENT)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: apply(paper_fund, receipts[:2], proof), range(2)))
    assert results[0] == results[1] and results[0].state == "SETTLED"
    assert results[0].units == Decimal("100")
    assert broker._cash(SETTLEMENT, "ARS") == Decimal("998.99")
    # The same signed native receipt can reappear with equivalent decimal text.
    receipts[1] = replace(receipts[1], nav_per_unit="10.010100", cash_amount="1001.010", units="100.0")
    monkeypatch.setattr(lifecycle, "_now", lambda: REDEMPTION)
    assert apply(paper_fund, receipts[:2], attestation(
        paper_fund, receipts[:2], REQUEST, REDEMPTION)).state == "REDEEM_REQUESTED"
    monkeypatch.setattr(lifecycle, "_now", lambda: END)
    payout = attestation(paper_fund, receipts, PAYMENT, END)
    result = apply(paper_fund, receipts, payout)
    assert result.state == "REDEEMED" and not result.entry_authority
    assert result.redemption_cash_ars == Decimal("1029.00") and result.net_pnl_ars == Decimal("27.99")
    # A retry after the source authorization expired changes no financial event.
    monkeypatch.setattr(lifecycle, "_now", lambda: "2026-11-27T15:00:00-03:00")
    assert apply(paper_fund, receipts, payout) == result
    assert broker._cash(END, "ARS") == Decimal("2027.99")
    assert states(store) == ["SUBSCRIBE_REQUESTED", "PENDING", "NAV_APPLIED",
                             "SETTLED", "REDEEM_REQUESTED", "REDEEMED"]
    with store.connect() as db:
        assert [row[0] for row in db.execute(
            "SELECT DISTINCT lifecycle.instrument FROM paper_family_lifecycle_events events "
            "JOIN paper_family_lifecycle lifecycle USING (lifecycle_id)"
        )] == [evidence.terms.symbol]
        db.execute("UPDATE paper_family_lifecycle_events SET amount='1030' WHERE event_id='e5'")
    with pytest.raises(ValueError, match="FCI_RUNTIME_REDEMPTION_SOURCE_UNVERIFIED"):
        broker._cash(END, "ARS")


@pytest.mark.parametrize("reason", [
    "FCI_RUNTIME_SETTLEMENT_SOURCE_PIN_MISSING",
    "FCI_SETTLEMENT_ATTESTATION_INVALID",
    "FCI_SETTLEMENT_ATTESTATION_SCOPE_MISMATCH",
    "FCI_SETTLEMENT_ATTESTATION_NOT_CURRENT",
    "FCI_SETTLEMENT_ATTESTED_BEFORE_RECEIPT",
    "FCI_SETTLEMENT_EVIDENCE_MISMATCH",
    "FCI_RECEIPT_IDENTITY_OR_SOURCE_MISMATCH",
    "FCI_NAV_EFFECTIVE_TIME_INVALID",
    "FCI_SETTLEMENT_TRANSITIONS_INVALID",
])
def test_runtime_settlement_rejects_missing_or_mismatched_proof_without_writes(
        paper_fund, monkeypatch, reason):
    store, broker, _, _, _, receipts = paper_fund
    monkeypatch.setattr(lifecycle, "_now", lambda: SETTLEMENT)
    current = receipts[:2]
    changes = {}
    if reason == "FCI_RUNTIME_SETTLEMENT_SOURCE_PIN_MISSING":
        def missing_authority(*args, **kwargs):
            raise FileNotFoundError
        monkeypatch.setattr(operator, "_root_owned_receipt", missing_authority)
        monkeypatch.setenv("POROTA_FCI_SETTLEMENT_SIGNER_SHA256", hashlib.sha256(paper_fund[4]).hexdigest())
        monkeypatch.setenv("POROTA_FCI_SETTLEMENT_ACCOUNT_SHA256", ACCOUNT)
    elif reason == "FCI_SETTLEMENT_ATTESTATION_SCOPE_MISMATCH":
        changes["account_scope_sha256"] = DIGEST
    elif reason == "FCI_SETTLEMENT_ATTESTATION_NOT_CURRENT":
        changes["valid_until"] = SETTLEMENT
    elif reason == "FCI_SETTLEMENT_ATTESTED_BEFORE_RECEIPT":
        changes["known_at"] = KNOWN
    elif reason == "FCI_SETTLEMENT_EVIDENCE_MISMATCH":
        changes["identity"] = [SYMBOL, "FCI", "FCI", "ARS", "INMEDIATA"]
    elif reason == "FCI_RECEIPT_IDENTITY_OR_SOURCE_MISMATCH":
        current.pop()
    elif reason == "FCI_NAV_EFFECTIVE_TIME_INVALID":
        current[0] = replace(current[0], nav_effective_at=KNOWN)
    request = [["new", "SUBSCRIBE_REQUESTED"]] if reason == "FCI_SETTLEMENT_TRANSITIONS_INVALID" else ALLOCATION
    proof = attestation(paper_fund, current, request, SETTLEMENT, **changes)
    if reason == "FCI_SETTLEMENT_ATTESTATION_INVALID":
        proof["signature_hex"] = "ab" * 64
    with pytest.raises(ValueError, match=reason):
        apply(paper_fund, current, proof)
    assert states(store) == ["SUBSCRIBE_REQUESTED"]
    assert broker._cash(SETTLEMENT, "ARS") == Decimal("998.99")


def test_runtime_settlement_rolls_back_all_new_events_after_a_write_failure(paper_fund, monkeypatch):
    store, _, _, _, _, receipts = paper_fund
    monkeypatch.setattr(lifecycle, "_now", lambda: SETTLEMENT)
    with store.connect() as db:
        db.execute("""CREATE TRIGGER fail_fund_settlement BEFORE INSERT ON paper_family_lifecycle_events
                      WHEN NEW.to_state='SETTLED' BEGIN SELECT RAISE(ABORT,'TEST_WRITE_FAILED'); END""")
    proof = attestation(paper_fund, receipts[:2], ALLOCATION, SETTLEMENT)
    with pytest.raises(sqlite3.IntegrityError, match="TEST_WRITE_FAILED"):
        apply(paper_fund, receipts[:2], proof)
    assert states(store) == ["SUBSCRIBE_REQUESTED"]
    with store.connect() as db:
        db.execute("DROP TRIGGER fail_fund_settlement")
    assert apply(paper_fund, receipts[:2], proof).state == "SETTLED"


def test_signed_fund_payment_cannot_create_its_own_prior_redemption_request(paper_fund, monkeypatch):
    store, broker, _, _, _, receipts = paper_fund
    monkeypatch.setattr(lifecycle, "_now", lambda: SETTLEMENT)
    assert apply(paper_fund, receipts[:2], attestation(
        paper_fund, receipts[:2], ALLOCATION, SETTLEMENT)).state == "SETTLED"
    monkeypatch.setattr(lifecycle, "_now", lambda: END)
    proof = attestation(paper_fund, receipts, REQUEST + PAYMENT, END)
    with pytest.raises(ValueError, match="FCI_SETTLEMENT_REDEMPTION_REQUEST_REQUIRED"):
        apply(paper_fund, receipts, proof)
    assert states(store) == ["SUBSCRIBE_REQUESTED", "PENDING", "NAV_APPLIED", "SETTLED"]
    assert broker._cash(END, "ARS") == Decimal("998.99")
