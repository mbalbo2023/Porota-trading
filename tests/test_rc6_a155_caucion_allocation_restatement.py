"""A151: allocation history keeps original decisions and verified PAPER overlay.

No broker connection, market feed, order route, or real-order permission.
"""

from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from bl_candle_engine import fingerprint
from au_fee_schedule import CAUCION_PAPER_FEE_AUTHORITY
from bt_caucion_paper import CaucionBook, CaucionOffer, offer_payload
from ca_caucion_allocator import CaucionPolicy, encoded
from cb_caucion_audit import allocation_history
from scripts.rc6_caucion_fee_restatement_apply import apply_restatement
from test_rc6_a150_caucion_ro_provenance import _six_legacy_matured, LEGACY_AUTHORITY


@pytest.fixture
def six_historical_allocations(tmp_path):
    path = _six_legacy_matured(tmp_path)
    policy = CaucionPolicy(
        frozen_at="2026-08-28T10:00:00-03:00", currency="ARS",
        reserve_cash=Decimal("1000"), maximum_cash_fraction=Decimal("0.5"),
        maximum_principal=Decimal("3000"),
        liquidity_deadline="2026-09-01T15:00:00-03:00",
        maximum_quote_age_seconds=Decimal("60"), participation=Decimal("0.10"),
        minimum_net_profit=Decimal("0"), ranking="NET_PROFIT",
        session_open_at="2026-08-28T11:00:00-03:00",
        session_close_at="2026-08-28T16:00:00-03:00",
        session_source="SYNTHETIC_TEST_ONLY")
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE paper_workspace (
                id INTEGER PRIMARY KEY, namespace TEXT, dataset_id TEXT,
                created_at TEXT, origin TEXT, imported_legacy INTEGER,
                initial_capital_json TEXT);
            CREATE TABLE observer_state (
                id INTEGER PRIMARY KEY, mode TEXT, real_orders_sent INTEGER);
            CREATE TABLE paper_positions (paper_id TEXT, status TEXT);
            CREATE TABLE paper_caucion_allocations (
                paper_id TEXT, request_id TEXT, request_hash TEXT,
                evaluated_at TEXT, decision_json TEXT);
        """)
        db.execute("INSERT INTO paper_workspace VALUES(1,?,?,?,?,0,NULL)",
                   ("POROTA_PAPER_V17_FRESH",
                    "123e4567-e89b-12d3-a456-426614174000",
                    "2026-08-28T10:00:00+00:00", "FRESH_EMPTY"))
        db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
        rows = db.execute("SELECT paper_id,request_id,terms_json FROM paper_cauciones").fetchall()
        for paper_id, request_id, raw in rows:
            terms = json.loads(raw)
            assert terms["fee_authority"] == LEGACY_AUTHORITY
            offer = CaucionOffer(**(terms | {"fee_authority": None}))
            terms = offer_payload(offer) | {"fee_authority": LEGACY_AUTHORITY}
            raw = json.dumps(terms, sort_keys=True)
            db.execute("UPDATE paper_cauciones SET terms_json=?,request_fingerprint=? WHERE paper_id=?",
                       (raw, hashlib.sha256((raw + "|1000").encode()).hexdigest(), paper_id))
            candidate_id = fingerprint(terms)
            candidate = encoded({
                "candidate_id": candidate_id, "instrument_id": offer.instrument_id,
                "code": "ELIGIBLE", "principal": Decimal("1000"),
                "gross_interest": Decimal("3"), "fees": Decimal("0.20"),
                "net_profit": Decimal("2.80"), "cash_debit": Decimal("1000"),
                "net_return_per_day": Decimal("2.80") / Decimal("3000"),
                "interest_days": 3, "maturity_at": offer.maturity_at})
            manifest = {"policy": encoded(asdict(policy)), "offers": [terms]}
            plan = encoded({
                "mode": "PAPER_CAUCION_PLAN", "promotion_allowed": False,
                "data_certified": False, "at": offer.quoted_at,
                "policy_version": policy.version, "currency": "ARS",
                "cash": Decimal("10000"), "cash_budget": Decimal("4500"),
                "participation": Decimal("0.10"), "daily_risk": {"state": "READY"},
                "selected": candidate, "candidates": [candidate], "manifest": manifest,
                "code": "CANDIDATE_SELECTED"})
            decision = plan | {"plan_id": fingerprint(plan), "paper_id": paper_id,
                               "status": "PLACED_SIMULATED"}
            db.execute("INSERT INTO paper_caucion_allocations VALUES(?,?,?,?,?)",
                       (paper_id, request_id, fingerprint(manifest), offer.quoted_at,
                        json.dumps(decision, sort_keys=True)))
    return path


def test_six_verified_receipts_overlay_without_rewriting_historical_decisions(
        six_historical_allocations, monkeypatch):
    import bg_paper_dashboard as dashboard

    path = six_historical_allocations
    before = allocation_history(path)
    assert before["state"] == "PARTIAL"
    assert all(r["decision"] is r["placement"] is None for r in before["records"])
    monkeypatch.setattr(dashboard, "DB_PATH", path)
    hidden = dashboard._caucion_allocations_panel()
    assert "No se muestran importes ni se confirma una colocación" in hidden
    assert "Capital colocado:" not in hidden
    with sqlite3.connect(path) as db:
        originals = db.execute("SELECT paper_id,total_fees FROM paper_cauciones ORDER BY paper_id").fetchall()
        decisions = db.execute("SELECT decision_json FROM paper_caucion_allocations ORDER BY paper_id").fetchall()

    applied = apply_restatement(path, "a" * 40)
    assert applied["receipts"] == applied["inserted"] == 6
    history = allocation_history(path)
    assert history["state"] == "READABLE" and history["total"] == 6
    for record in history["records"]:
        assert record["state"] == "CONSISTENT"
        assert record["decision"]["promotion_allowed"] is False
        assert record["decision"]["data_certified"] is False
        assert Decimal(record["decision"]["selected"]["fees"]) == Decimal("0.20")
        overlay = record["placement"]
        assert overlay["status"] == "MATURED"
        assert overlay["fee_evidence_scope"] == "PUBLIC_MODEL_PAPER_ONLY_ACCOUNT_UNVERIFIED"
        assert overlay["broker_account_fees_verified"] is False
        assert Decimal(overlay["effective_total_fees"]) > Decimal(overlay["original_total_fees"])
        assert Decimal(overlay["effective_net_profit"]) == Decimal("3") - Decimal(overlay["effective_total_fees"])
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT paper_id,total_fees FROM paper_cauciones ORDER BY paper_id").fetchall() == originals
        assert db.execute("SELECT decision_json FROM paper_caucion_allocations ORDER BY paper_id").fetchall() == decisions

    page = dashboard._caucion_allocations_panel()
    assert "Costo público PAPER revisado" in page
    assert "Costo de la cuenta PPI: NO VERIFICADO" in page
    assert "Decisión histórica preservada" in page


def test_partial_receipt_or_changed_original_suppresses_all_amounts(
        six_historical_allocations):
    path = six_historical_allocations
    apply_restatement(path, "a" * 40)
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER paper_caucion_fee_restatements_no_delete")
        db.execute("DELETE FROM paper_caucion_fee_restatements WHERE paper_id='PAPER-CAUCION-0'")
    partial = allocation_history(path)
    assert partial["state"] == "PARTIAL"
    assert all(r["state"] == "INCONSISTENT" and r["decision"] is r["placement"] is None
               for r in partial["records"])

    # An altered original row remains suppressed as well.
    with sqlite3.connect(path) as db:
        db.execute("UPDATE paper_cauciones SET total_fees='0.19' WHERE paper_id='PAPER-CAUCION-1'")
    changed = allocation_history(path)
    assert changed["state"] == "PARTIAL"
    assert all(r["decision"] is r["placement"] is None for r in changed["records"])


def _prospective_offer(path, *, live_depth=False):
    with sqlite3.connect(path) as db:
        terms = json.loads(db.execute("SELECT terms_json FROM paper_cauciones "
                                      "WHERE paper_id='PAPER-CAUCION-0'").fetchone()[0])
    terms.update(fee_authority=CAUCION_PAPER_FEE_AUTHORITY,
                 quoted_at="2026-08-28T14:00:30.000000+00:00",
                 available_principal="1000",
                 paper_fill_policy=("LIVE_PPI_BID_PARTICIPATION_CAP" if live_depth
                                    else "CONSERVATIVE_NOTIONAL_CAP"),
                 metadata_source=("PPI_PRIMARY_LIVE_BOOK+PAPER_POLICY:SYNTHETIC_TEST_ONLY"
                                  if live_depth else "CONTRACT_EVIDENCE_V2:SYNTHETIC_TEST_ONLY"))
    return CaucionOffer(**terms)


def test_prospective_paper_depth_and_cash_gates_survive_legacy_overlay(
        six_historical_allocations):
    path = six_historical_allocations
    book = CaucionBook(SimpleNamespace(path=path))
    offer = _prospective_offer(path)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        with pytest.raises(ValueError, match="RESTATEMENT_TABLE_MISSING"):
            book.used_principal(offer, connection=db)

    apply_restatement(path, "a" * 40)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        assert book.used_principal(offer, connection=db) == 0
        # Synthetic PAPER depth, no provider call. Only the versioned current
        # authority can price the new offer; a legacy source never authorizes it.
        narrow = _prospective_offer(path, live_depth=True)
        with pytest.raises(ValueError, match="Capital supera la participación"):
            book.place(narrow, "500", "limited-depth", narrow.quoted_at,
                       lambda *_: Decimal("10000"), participation=Decimal("0.1"),
                       connection=db)
        with pytest.raises(ValueError, match="Caja liquidada insuficiente"):
            book.place(offer, "1000", "limited-cash", offer.quoted_at,
                       lambda *_: Decimal("900"), reserve=Decimal("100"),
                       participation=Decimal("1"), connection=db)
        assert db.execute("SELECT COUNT(*) FROM paper_cauciones").fetchone()[0] == 6
        db.rollback()

    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER paper_caucion_fee_restatements_no_delete")
        db.execute("DELETE FROM paper_caucion_fee_restatements WHERE paper_id='PAPER-CAUCION-0'")
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        with pytest.raises(ValueError, match="CAUCION_LEDGER_INVALID.*RESTATEMENT_INCOMPLETE"):
            book.used_principal(offer, connection=db)
