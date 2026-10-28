"""A151: one consistent PUBLIC PAPER fee receipt across economic readers.

The historical booked positions and allocations are immutable.  A missing,
partial or altered restatement must fail closed rather than display the old
cost alongside an apparently green PAPER PnL.
"""

from datetime import datetime, timezone
from decimal import Decimal
import json
import sqlite3

import pytest

from bt_caucion_paper import CaucionBook, paper_caucion_economic_projection
from scripts.rc6_caucion_fee_restatement_apply import apply_restatement, preflight_restatement
from test_rc6_a150_caucion_ro_provenance import _six_legacy_matured, LEGACY_AUTHORITY


CANDIDATE = "a" * 40
KNOWN_AT = datetime(2026, 10, 28, 5, 0, tzinfo=timezone.utc)
ORIGIN = {"candidate_sha": CANDIDATE, "candidate_tree": "c" * 40,
          "deploy_sha": "d" * 40, "run_id": "40700000000", "run_attempt": "1",
          "owner_approval_comment_id": 1001, "owner_deferred_comment_id": 1002}


@pytest.fixture
def six_positions(tmp_path):
    path = _six_legacy_matured(tmp_path)
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE paper_workspace (
                id INTEGER PRIMARY KEY, namespace TEXT, dataset_id TEXT,
                origin TEXT, imported_legacy INTEGER);
            CREATE TABLE observer_state (
                id INTEGER PRIMARY KEY, mode TEXT, real_orders_sent INTEGER);
            CREATE TABLE paper_positions (paper_id TEXT, status TEXT);
            CREATE TABLE paper_caucion_allocations (
                paper_id TEXT, request_id TEXT, decision_json TEXT);
        """)
        db.execute("INSERT INTO paper_workspace VALUES(1,?,?,?,0)",
                   ("POROTA_PAPER_V17_FRESH",
                    "123e4567-e89b-12d3-a456-426614174000", "FRESH_EMPTY"))
        db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
        for paper_id, request_id, terms_raw, principal, interest, fees in db.execute(
            "SELECT paper_id,request_id,terms_json,principal,gross_interest,total_fees "
            "FROM paper_cauciones").fetchall():
            terms = json.loads(terms_raw)
            decision = {
                "paper_id": paper_id, "mode": "PAPER_CAUCION_PLAN",
                "promotion_allowed": False, "data_certified": False,
                "status": "PLACED_SIMULATED",
                "selected": {"principal": principal, "gross_interest": interest,
                             "fees": fees},
                "manifest": {"offers": [
                    {"instrument_id": terms["instrument_id"],
                     "fee_authority": LEGACY_AUTHORITY}]},
            }
            db.execute("INSERT INTO paper_caucion_allocations VALUES(?,?,?)",
                       (paper_id, request_id, json.dumps(decision, sort_keys=True)))
    return path


def _read(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("BEGIN")
    return db


def test_no_receipt_is_fail_closed_and_does_not_relabel_legacy_cost(six_positions):
    with _read(six_positions) as db:
        before = db.execute("SELECT paper_id,total_fees FROM paper_cauciones ORDER BY paper_id").fetchall()
        with pytest.raises(ValueError, match="CAUCION_LEDGER_INVALID.*RESTATEMENT_TABLE_MISSING"):
            paper_caucion_economic_projection(db, db.execute("SELECT * FROM paper_cauciones"))
        after = db.execute("SELECT paper_id,total_fees FROM paper_cauciones ORDER BY paper_id").fetchall()
        assert [tuple(x) for x in before] == [tuple(x) for x in after]


def test_exact_six_append_only_receipts_unify_cash_pnl_and_provenance(six_positions):
    with sqlite3.connect(six_positions) as db:
        original = db.execute("SELECT paper_id,total_fees FROM paper_cauciones ORDER BY paper_id").fetchall()
    assert preflight_restatement(six_positions, CANDIDATE, now=KNOWN_AT)["receipts_expected"] == 6
    applied = apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT, origin=ORIGIN)
    assert applied["inserted"] == applied["receipts"] == 6
    assert applied["broker_account_fees_verified"] is False
    assert apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT)["inserted"] == 0
    assert preflight_restatement(six_positions, CANDIDATE, now=KNOWN_AT)["previously_applied"] is True

    with _read(six_positions) as db:
        rows = CaucionBook(None).positions(status="MATURED", connection=db)
        assert len(rows) == 6
        assert all(row["fee_evidence_scope"] ==
                   "PUBLIC_MODEL_PAPER_ONLY_ACCOUNT_UNVERIFIED" for row in rows)
        assert all(row["broker_account_fees_verified"] is False for row in rows)
        assert all(Decimal(row["total_fees"]) >= Decimal(row["original_total_fees"])
                   for row in rows)
        expected = sum(Decimal(row["gross_interest"]) - Decimal(row["total_fees"])
                       for row in rows)
        book = CaucionBook(None)
        cash = book.cash_effect("ARS", "2026-10-28T06:00:00+00:00", connection=db)
        value = book.valuation("2026-10-28T06:00:00+00:00", connection=db)
        assert cash == value["realized"] == expected
        historical = book.cash_effect("ARS", "2026-09-01T06:00:00+00:00", connection=db)
        booked = sum(Decimal(row["gross_interest"]) - Decimal(row["original_total_fees"])
                     for row in rows)
        assert historical == booked
        assert historical >= cash  # No hindsight from the October restatement.
        assert all(row["fee_known_at_utc"] == KNOWN_AT.isoformat(timespec="seconds")
                   for row in rows)
        assert [tuple(x) for x in db.execute(
            "SELECT paper_id,total_fees FROM paper_cauciones ORDER BY paper_id")] == original


def test_external_autocommit_reader_pins_and_releases_one_receipt_snapshot(six_positions):
    apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT, origin=ORIGIN)
    with sqlite3.connect(six_positions, isolation_level=None) as db:
        db.row_factory = sqlite3.Row
        assert not db.in_transaction
        rows = CaucionBook(None).positions(connection=db)
        assert len(rows) == 6
        assert all(row['fee_evidence_scope'] ==
                   'PUBLIC_MODEL_PAPER_ONLY_ACCOUNT_UNVERIFIED' for row in rows)
        # A local SAVEPOINT is read-only and does not leak a transaction to
        # consumers that supplied an autocommit connection.
        assert not db.in_transaction


def test_partial_or_tampered_receipts_fail_before_any_financial_projection(six_positions):
    apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT, origin=ORIGIN)
    with sqlite3.connect(six_positions) as db:
        # Immutable no-update trigger really executes, rather than relying on
        # a test-only hypothetical failure.
        with pytest.raises(sqlite3.IntegrityError, match="APPEND_ONLY"):
            db.execute("UPDATE paper_caucion_fee_restatements SET effective_total_fees='0'")
        with pytest.raises(sqlite3.IntegrityError, match="APPEND_ONLY"):
            db.execute("DELETE FROM paper_caucion_fee_restatements")
        db.execute("DROP TRIGGER paper_caucion_fee_restatements_no_delete")
        db.execute("DELETE FROM paper_caucion_fee_restatements WHERE paper_id=(SELECT paper_id FROM paper_caucion_fee_restatements LIMIT 1)")
    with _read(six_positions) as db:
        with pytest.raises(ValueError, match="CAUCION_LEDGER_INVALID"):
            CaucionBook(None).positions(connection=db)


def test_restated_fee_does_not_hide_changed_historical_row(six_positions):
    apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT, origin=ORIGIN)
    with sqlite3.connect(six_positions) as db:
        db.execute("UPDATE paper_cauciones SET total_fees='0.19' WHERE paper_id=(SELECT paper_id FROM paper_cauciones LIMIT 1)")
    with _read(six_positions) as db:
        with pytest.raises(ValueError, match="CAUCION_LEDGER_INVALID"):
            paper_caucion_economic_projection(db, db.execute("SELECT * FROM paper_cauciones"))
    with pytest.raises(ValueError, match="PAPER_RESTATEMENT_HISTORICAL_REPLAY_RED"):
        preflight_restatement(six_positions, CANDIDATE, now=KNOWN_AT)


def test_another_candidate_cannot_silently_adopt_existing_fee_receipts(six_positions):
    apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT, origin=ORIGIN)
    with pytest.raises(ValueError, match="PAPER_RESTATEMENT_PRIOR_FAILED_DEPLOY_PROOF_RED"):
        preflight_restatement(six_positions, "b" * 40, now=KNOWN_AT)
    with pytest.raises(ValueError, match="PAPER_RESTATEMENT_PRIOR_FAILED_DEPLOY_PROOF_RED"):
        apply_restatement(six_positions, "b" * 40, now=KNOWN_AT)


@pytest.mark.parametrize("table,ddl,insert,reason", [
    ("paper_positions", "", "INSERT INTO paper_positions VALUES('SPOT-1','OPEN')",
     "PAPER_RESTATEMENT_SPOT_NOT_FLAT"),
    ("paper_cauciones", "", "UPDATE paper_cauciones SET status='OPEN' WHERE paper_id="
     "(SELECT paper_id FROM paper_cauciones LIMIT 1)", "PAPER_RESTATEMENT_CAUCION_NOT_FLAT"),
    ("paper_future_positions", "CREATE TABLE paper_future_positions(status TEXT)",
     "INSERT INTO paper_future_positions VALUES('ACTIVE')", "PAPER_RESTATEMENT_FUTURES_NOT_FLAT"),
])
def test_any_open_financial_position_rejects_plan_without_mutating_book(
        six_positions, table, ddl, insert, reason):
    with sqlite3.connect(six_positions) as db:
        if ddl:
            db.execute(ddl)
        db.execute(insert)
        before = db.execute("SELECT COUNT(*) FROM paper_cauciones").fetchone()[0]
    with pytest.raises(ValueError, match=reason):
        preflight_restatement(six_positions, CANDIDATE, now=KNOWN_AT)
    with sqlite3.connect(six_positions) as db:
        assert db.execute("SELECT COUNT(*) FROM paper_cauciones").fetchone()[0] == before
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='paper_caucion_fee_restatements'").fetchone() is None
