"""A456: synthetic exact-contract LETRAS PAPER end-to-end (never issuer proof).

These issuer/settlement bytes are deliberately invented test fixtures. A live
PAPER BUY still needs a separately release-approved manifest and PPI quote;
this suite does not claim provider authorization or runtime readiness.
"""
from dataclasses import replace
from datetime import timedelta, timezone
from decimal import Decimal
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from be_paper_engine import PaperBroker, PaperStore
from bm_exit_supervisor import PositionExitSupervisor
from cf_sale_settlement import conservative_unconfirmed_availability
from rc6_fixed_income_event_ledger import apply_verified_receipt
from rc6_fixed_income_native_redemption import (
    REVIEW_AUTHORITY, REVIEW_SCHEMA, SCHEDULE_SCHEMA,
    apply_verified_redemption_review, normalized_issuer_schedule_sha256,
)
from rc6_fixed_income_portfolio_bridge import cash_effect_checked, position_at
from rc6_fixed_income_runtime import (
    FixedIncomeEvidenceError, MATURITY_RECEIPT_SCHEMA, contract_economics,
)
from tests.test_rc6_fixed_income_paper_strategy import AT, YESTERDAY
from tests.test_rc6_fixed_income_runtime import reviewed_bundle, repin


MATURITY = (AT + timedelta(days=4, hours=1)).isoformat()
MATURITY_ART = (AT + timedelta(days=4, hours=1)).astimezone(
    timezone(-timedelta(hours=3))).isoformat()
MATURITY_DIFFERENT_INSTANT = (AT + timedelta(days=4, hours=1, minutes=1)).isoformat()
PAID_AT = (AT + timedelta(days=4, hours=1, minutes=6)).isoformat()
BOOKED_AT = (AT + timedelta(days=4, hours=2)).isoformat()
SETTLEMENT_SOURCE = b"SYNTHETIC bill settlement proof, not a market receipt"


def _raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _bill_bundle(tmp_path, monkeypatch):
    q, root, _ = reviewed_bundle(tmp_path)
    q = replace(q, symbol="D30N6", asset_class="LETRAS",
                contract=replace(q.contract, symbol="D30N6", family="LETRAS"))
    repin(root, "terms.json", lambda d: d.update(
        identity=list(q.contract.key), currency="ARS", maturity_at=MATURITY,
        native_maturity_redemption_supported=True,
        redemption_basis="FIXED_KNOWN_FACE", indexation="NONE",
        redemption_face_official=True,
        redemption_source_at=d["source_at"], redemption_known_at=d["known_at"],
        maturity_redemption_receipt_contract="EXACT_NOMINAL_EVENT_REDUCER_V1"))
    repin(root, "validation.json", lambda d: d.update(
        identity=list(q.contract.key), instrument_family="LETRAS"))
    def maturity_receipt(d):
        d.update(schema=MATURITY_RECEIPT_SCHEMA, identity=list(q.contract.key),
                 contract_economics=contract_economics(q.contract),
                 exit_mode="MATURITY_REDEMPTION", maturity_at=MATURITY,
                 flows=[{
                     **d["flows"][0], "kind": "AMORTIZATION",
                     "due_at": MATURITY, "cash_per_vn": "1.15",
                     "obligation_id": "SYNTHETIC_BILL_FINAL",
                 }])
        del d["market_exit_horizon_minutes"]
        d["risk_limits"].update(max_holding_days=7, max_loss_budget="1000")
    repin(root, "receipt.json", maturity_receipt)
    pin = repin(root, "manifest.json", lambda d: d["receipts"][0].update(
        identity=list(q.contract.key)))
    monkeypatch.setenv("POROTA_FI_EVIDENCE_DIRECTORY", str(root))
    monkeypatch.setenv("POROTA_FI_EVIDENCE_MANIFEST_SHA256", pin)
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    store = PaperStore(str(tmp_path / "fi-bill-paper.sqlite"))
    broker = PaperBroker(store, initial_cash="100000", daily_loss_pct="10",
                         risk_pct="0.01", ai_mode="OFF", require_ai=False,
                         participation="0.10")
    store.add_quote(q)
    return q, root, pin, store, broker


def _bill_bundle_art_maturity(tmp_path, monkeypatch):
    """Same instant in issuer -03:00, later reviewed as canonical UTC."""
    q, root, _, store, broker = _bill_bundle(tmp_path, monkeypatch)
    repin(root, "terms.json", lambda d: d.update(maturity_at=MATURITY_ART))

    def offset_receipt(d):
        d["maturity_at"] = MATURITY_ART
        d["flows"][0]["due_at"] = MATURITY_ART

    pin = repin(root, "receipt.json", offset_receipt)
    monkeypatch.setenv("POROTA_FI_EVIDENCE_MANIFEST_SHA256", pin)
    return q, root, pin, store, broker


def _book_paid_maturity(store, paper_id, issuer_file, q, *, maturity_at=MATURITY):
    source_sha = _sha(SETTLEMENT_SOURCE)
    settled = conservative_unconfirmed_availability(q.settlement, q.observed_at)
    event = {
        "schema": "rc6.fixed-income-economic-receipt.v1",
        "paper_only": True, "real_routes_used": [],
        "review_authority": "RELEASE_REVIEWED_ECONOMIC_EVENT",
        "identity": list(q.contract.key),
        "event": {
            "event_id": "bill-final", "obligation_id": "SYNTHETIC_BILL_FINAL",
            "paper_id": paper_id, "kind": "AMORTIZATION",
            "effective_at": maturity_at, "recorded_at": PAID_AT,
            "source_at": PAID_AT, "source_kind": "SETTLEMENT_PROOF",
            "source_reference": "SYNTHETIC_PAYMENT_NOTICE",
            "source_sha256": source_sha, "payment_currency": "ARS",
            "cash_amount": "115", "nominal_base": "100", "nominal_redeemed": "100",
        },
        "cash_per_nominal": "1.15",
        "entitlement": {
            "rule": "TRADE_BEFORE_EX_AND_SETTLED_BY_RECORD",
            "source_sha256": source_sha,
            "source_reference": "SYNTHETIC_PAYMENT_NOTICE",
            "ex_at": (AT + timedelta(days=4)).isoformat(),
            "record_at": (AT + timedelta(days=4, minutes=30)).isoformat(),
            "paper_settled_at": settled.isoformat(),
            "settlement_basis": "PAPER_CONSERVATIVE_FULL_SETTLEMENT_DATE",
            "known_at": PAID_AT,
        },
    }
    receipt_bytes = _raw(event)
    snapshot = apply_verified_receipt(
        store, receipt_bytes=receipt_bytes,
        approved_sha256=_sha(receipt_bytes), source_bytes=SETTLEMENT_SOURCE,
        applied_at=BOOKED_AT)
    issuer_sha = _sha(issuer_file.read_bytes())
    issuer_reference = json.loads((issuer_file.parent / "terms.json").read_text())["source_reference"]
    issuer_at = YESTERDAY.isoformat()
    issuer_schedule = {
        "schema": SCHEDULE_SCHEMA, "review_authority": "INDEPENDENT_ISSUER_SCHEDULE_REVIEWED",
        "identity": list(q.contract.key), "issuer_id": "SYNTHETIC_ISSUER_NOT_LIVE",
        "series_id": "SYNTHETIC_BILL_NOT_LIVE", "instrument_class": "BILL",
        "bopreal_classification": "NOT_BOPREAL_INDEPENDENTLY_REVIEWED",
        "source_sha256": issuer_sha, "source_reference": issuer_reference,
        "source_at": issuer_at, "known_at": issuer_at,
        "maturity_at": maturity_at, "principal_currency": "ARS",
        "redemption_basis": "FIXED_KNOWN_FACE", "indexation": "NONE",
        "redemption_face_official": True,
        "final_obligation_id": "SYNTHETIC_BILL_FINAL",
        "obligations": [{field: event["event"][field] for field in (
            "obligation_id", "kind", "effective_at", "payment_currency",
            "cash_amount", "nominal_base", "nominal_redeemed")}],
    }
    review = {
        "schema": REVIEW_SCHEMA, "paper_only": True, "real_routes_used": [],
        "review_authority": REVIEW_AUTHORITY, "all_maturity_obligations_reviewed": True,
        "paper_id": paper_id, "identity": list(q.contract.key),
        "final_event_id": "bill-final", "paid_obligation_ids": ["SYNTHETIC_BILL_FINAL"],
        "final_principal_cash": "115", "final_nominal_redeemed": "100",
        "expected_principal_by_currency": [["ARS", "115"]],
        "expected_cash_by_currency": [["ARS", "115"]],
        "issuer_schedule": issuer_schedule,
    }
    review_bytes = _raw(review)
    checked = apply_verified_redemption_review(
        store, receipt_bytes=review_bytes, approved_sha256=_sha(review_bytes),
        issuer_bytes=issuer_file.read_bytes(), approved_issuer_sha256=issuer_sha,
        approved_schedule_sha256=normalized_issuer_schedule_sha256(issuer_schedule),
        applied_at=BOOKED_AT)
    return snapshot, checked


def test_release_pinned_native_letra_buy_lot_cash_and_no_sell_terminal(tmp_path, monkeypatch):
    q, root, pin, store, broker = _bill_bundle(tmp_path, monkeypatch)
    action, _, reason, features = broker._decide_fixed_income(q, AT.isoformat())
    assert (action, reason) == ("BUY", "FI_PAPER_CANDIDATE_VALIDATED")
    assert features["fixed_income_exit_plan"]["mode"] == "FI_NATIVE_MATURITY_EVENT_LEDGER_V1"
    assert features["fixed_income_runtime_evidence"]["manifest_sha256"] == pin
    broker.on_quote(q)
    opened = store.open_positions()
    with store.connect() as db:
        gates = [tuple(row) for row in db.execute(
            "SELECT final_result,reason FROM trade_gate_evaluations")]
        diag = [json.loads(row[0]) for row in db.execute("SELECT detail_json FROM trade_gate_evaluations")]
    assert len(opened) == 1, (gates, [{k:v for k,v in d.items() if 'qty_' in k or k in ('scenario_entry_cash','risk_budget','risk_capital')} for d in diag])
    paper_id = opened[0]["paper_id"]
    with store.connect() as db:
        native = position_at(db, paper_id, AT.isoformat())
        assert native.initial_nominal == Decimal(100)
        assert db.execute("SELECT count(*) FROM paper_fi_lots").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 1
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
    sup = PositionExitSupervisor(broker, clock_fn=lambda: AT.isoformat())
    eod = (AT + timedelta(hours=8)).isoformat()
    assert sup.supervise(opened[0], None, eod).state == "OPEN"
    assert sup.supervise(opened[0], None, MATURITY).state == "OPEN"
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0
    cash_before = broker._cash(as_of=BOOKED_AT, currency="ARS")
    _, review = _book_paid_maturity(store, paper_id, root / "issuer.bin", q)
    assert review["residual_nominal"] == "0"
    assert broker._cash(as_of=BOOKED_AT, currency="ARS") == cash_before + Decimal(115)
    # The normal worker tick, not an artificially injected quote, must be
    # able to close reviewed FI even when the BYMA session/book is absent.
    sup.clock_fn = lambda: BOOKED_AT
    assert any(v.paper_id == paper_id and v.state == "CLOSED"
               for v in sup.tick(quotes={}))
    assert sup.supervise(opened[0], None, BOOKED_AT).state == "CLOSED"
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM paper_fi_native_maturities").fetchone()[0] == 1
        assert db.execute("SELECT status FROM paper_positions").fetchone()[0] == "CLOSED"
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
        assert cash_effect_checked(db, "ARS", BOOKED_AT) == Decimal(115)
    assert broker._cash(as_of=BOOKED_AT, currency="ARS") == cash_before + Decimal(115)


def test_issuer_art_maturity_and_utc_review_same_instant_closes_paper_no_sell(
        tmp_path, monkeypatch):
    q, root, pin, store, broker = _bill_bundle_art_maturity(tmp_path, monkeypatch)
    assert MATURITY_ART != MATURITY
    assert broker._decide_fixed_income(q, AT.isoformat())[0] == "BUY"
    broker.on_quote(q)
    positions = store.open_positions()
    assert len(positions) == 1
    paper_id = positions[0]["paper_id"]
    plan = json.loads(positions[0]["features_json"])["fixed_income_exit_plan"]
    assert plan["exit_at"] == MATURITY_ART
    assert pin == json.loads(positions[0]["features_json"])[
        "fixed_income_runtime_evidence"]["manifest_sha256"]

    _, review = _book_paid_maturity(store, paper_id, root / "issuer.bin", q)
    assert review["maturity_at"] == MATURITY
    assert review["maturity_at"] != plan["exit_at"]
    assert review["residual_nominal"] == "0"
    sup = PositionExitSupervisor(broker, clock_fn=lambda: BOOKED_AT)
    assert any(v.paper_id == paper_id and v.state == "CLOSED"
               for v in sup.tick(quotes={}))
    with store.connect() as db:
        assert db.execute("SELECT status FROM paper_positions WHERE paper_id=?",
                          (paper_id,)).fetchone()[0] == "CLOSED"
        assert db.execute("SELECT count(*) FROM paper_fi_native_maturities").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_issuer_review_different_actual_maturity_remains_watch_despite_paid_cash(
        tmp_path, monkeypatch):
    q, root, _, store, broker = _bill_bundle_art_maturity(tmp_path, monkeypatch)
    broker.on_quote(q)
    positions = store.open_positions()
    assert len(positions) == 1
    paper_id = positions[0]["paper_id"]
    _, review = _book_paid_maturity(
        store, paper_id, root / "issuer.bin", q,
        maturity_at=MATURITY_DIFFERENT_INSTANT)
    assert review["maturity_at"] == MATURITY_DIFFERENT_INSTANT
    assert review["residual_nominal"] == "0"
    sup = PositionExitSupervisor(broker, clock_fn=lambda: BOOKED_AT)
    verdict = sup.supervise(positions[0], None, BOOKED_AT)
    assert verdict.state == "WATCH_FI_NATIVE_LEDGER_BLOCKED"
    assert verdict.reason == "FI_EXIT_REDEMPTION_PLAN_REVIEW_MISMATCH"
    with store.connect() as db:
        assert db.execute("SELECT status FROM paper_positions WHERE paper_id=?",
                          (paper_id,)).fetchone()[0] == "OPEN"
        assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM paper_fi_native_maturities").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_missing_release_pin_and_maturity_contract_fails_closed(tmp_path, monkeypatch):
    q, root, pin, store, broker = _bill_bundle(tmp_path, monkeypatch)
    monkeypatch.delenv("POROTA_FI_EVIDENCE_MANIFEST_SHA256")
    assert broker._decide_fixed_income(q, AT.isoformat())[3]["reason_code"] == \
        "FI_EVIDENCE_RELEASE_PIN_MISSING"
    assert not broker.fixed_income_native_exit_authorized(q, {}, AT.isoformat())
    monkeypatch.setenv("POROTA_FI_EVIDENCE_MANIFEST_SHA256", pin)
    receipt = json.loads((root / "receipt.json").read_text())
    receipt["exit_mode"] = "MARKET_EXIT"
    receipt["market_exit_horizon_minutes"] = 120
    repin(root, "receipt.json", lambda d: d.update(receipt))
    with pytest.raises(FixedIncomeEvidenceError, match="FI_EVIDENCE_HASH_MISMATCH"):
        # The original manifest pin cannot attest a changed receipt byte.
        from rc6_fixed_income_runtime import fixed_income_strategy_inputs
        fixed_income_strategy_inputs(store, q, AT.isoformat())
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_positions").fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_atomic_native_lot_insert_failure_rolls_back_entire_buy(tmp_path, monkeypatch):
    q, _, _, store, broker = _bill_bundle(tmp_path, monkeypatch)
    action, score, reason, features = broker._decide_fixed_income(q, AT.isoformat())
    assert action == "BUY"
    features["economics"] = broker._economic_diagnostics(q)
    with store.connect() as db:
        db.execute("""CREATE TRIGGER fi_synthetic_failed_buy BEFORE INSERT ON paper_fi_lots
            BEGIN SELECT RAISE(ABORT,'synthetic native lot failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic native lot failure"):
        opened, reason, paper_id = broker._open(q, score, features)
        assert False, (opened, reason, paper_id)
    with store.connect() as db:
        for table in ("paper_positions", "paper_fills", "paper_fi_lots", "paper_fi_cash_entries"):
            assert db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_full_principal_risk_cap_prevents_stop_distance_underreservation(tmp_path, monkeypatch):
    q, _, _, store, broker = _bill_bundle(tmp_path, monkeypatch)
    # A 100-VN lot costs roughly ARS 100. A percentage-based stop risk would
    # admit it under this budget; issuer default can lose the entire principal.
    broker.risk_pct = Decimal("0.0001")  # ARS 10 of 100k; no 100-VN lot fits.
    broker.on_quote(q)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_positions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM paper_fi_lots").fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_native_maturity_plan_change_after_buy_cannot_change_issuer_or_due(tmp_path, monkeypatch):
    q, _, _, store, broker = _bill_bundle(tmp_path, monkeypatch)
    broker.on_quote(q)
    opened = store.open_positions()
    assert len(opened) == 1
    paper_id = opened[0]["paper_id"]
    with store.connect() as db:
        features = json.loads(db.execute(
            "SELECT features_json FROM paper_positions WHERE paper_id=?", (paper_id,)
        ).fetchone()[0])
        features["fixed_income_exit_plan"]["issuer_source_sha256"] = "f" * 64
        db.execute("UPDATE paper_positions SET features_json=? WHERE paper_id=?",
                   (_raw(features).decode(), paper_id))
    sup = PositionExitSupervisor(broker, clock_fn=lambda: BOOKED_AT)
    assert sup.supervise(store.open_positions()[0], None, BOOKED_AT).state == \
        "WATCH_FI_NATIVE_LEDGER_BLOCKED"
    with store.connect() as db:
        assert db.execute("SELECT status FROM paper_positions").fetchone()[0] == "OPEN"
        assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0] == 0


def test_quote_mutation_and_live_counter_cannot_bypass_locked_fi_buy(tmp_path, monkeypatch):
    q, _, _, store, broker = _bill_bundle(tmp_path, monkeypatch)
    action, score, _, features = broker._decide_fixed_income(q, AT.isoformat())
    assert action == "BUY"
    features["economics"] = broker._economic_diagnostics(q)
    q_changed = replace(q, bid=q.bid - Decimal("0.001"))
    opened, reason, _ = broker._open(q_changed, score, dict(features))
    assert not opened and reason == "FI_NATIVE_MATURITY_RELEASE_OR_OWNER_UNVERIFIED"
    with store.connect() as db:
        db.execute("UPDATE observer_state SET real_orders_sent=1 WHERE id=1")
    opened, reason, _ = broker._open(q, score, dict(features))
    assert not opened and reason == "FI_NATIVE_MATURITY_RELEASE_OR_OWNER_UNVERIFIED"
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_positions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM paper_fi_lots").fetchone()[0] == 0


def test_missing_exact_contract_is_rejected_before_native_buy(tmp_path, monkeypatch):
    q, _, _, store, broker = _bill_bundle(tmp_path, monkeypatch)
    action, score, _, features = broker._decide_fixed_income(q, AT.isoformat())
    assert action == "BUY"
    with store.connect() as db:
        assert broker._specialized_decision_error(
            db, replace(q, contract=None), score, features
        ) == "FI_NATIVE_MATURITY_RELEASE_OR_OWNER_UNVERIFIED"
        assert db.execute("SELECT count(*) FROM paper_positions").fetchone()[0] == 0


@pytest.mark.parametrize("filename,mutate,blocker", [
    ("receipt.json", lambda d: d.update(schema="rc6.fixed-income-strategy-input.v1"),
     "FI_EVIDENCE_RECEIPT_SCHEMA_INVALID"),
    ("terms.json", lambda d: d.update(indexation="CER_FUTURE_FIXING"),
     "FI_EVIDENCE_MATURITY_CONTRACT_UNVERIFIED"),
    ("terms.json", lambda d: d.update(native_maturity_redemption_supported=False),
     "FI_EVIDENCE_MATURITY_CONTRACT_UNVERIFIED"),
    ("receipt.json", lambda d: d.update(flows=[]),
     "FI_EVIDENCE_MATURITY_HORIZON_OR_FLOWS_MISSING"),
    ("receipt.json", lambda d: d["flows"][0].update(
        due_at=(AT + timedelta(days=5)).isoformat()),
     "FI_EVIDENCE_MATURITY_FLOW_OUT_OF_HORIZON"),
    ("receipt.json", lambda d: d.update(maturity_at=(AT + timedelta(days=3)).isoformat()),
     "FI_EVIDENCE_MATURITY_CONTRACT_UNVERIFIED"),
    ("validation.json", lambda d: d.update(out_of_sample_closed_trades=0),
     "FI_OOS_SAMPLE_INSUFFICIENT"),
])
def test_unverified_issuer_terms_or_maturity_coverage_never_enters(
        tmp_path, monkeypatch, filename, mutate, blocker):
    q, root, _, store, broker = _bill_bundle(tmp_path, monkeypatch)
    new_pin = repin(root, filename, mutate)
    monkeypatch.setenv("POROTA_FI_EVIDENCE_MANIFEST_SHA256", new_pin)
    result = broker._decide_fixed_income(q, AT.isoformat())
    assert result[0] == "HOLD" and result[3]["reason_code"] == blocker
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_positions").fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
