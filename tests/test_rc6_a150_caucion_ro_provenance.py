"""The historical public-fee review cannot certify account fees or book cash."""

import hashlib
import json
import sqlite3

from scripts.rc6_caucion_fee_ro_provenance import (
    LEGACY_AUTHORITY, historical_public_fee_provenance,
)


def _six_legacy_matured(tmp_path):
    path = tmp_path / "paper.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE paper_cauciones(
            paper_id TEXT, request_id TEXT, request_fingerprint TEXT, source TEXT,
            instrument_id TEXT,currency TEXT,status TEXT,principal TEXT,
            annual_rate_fraction TEXT,interest_days INTEGER,day_count_basis INTEGER,
            gross_interest TEXT,total_fees TEXT,fee_payment TEXT,opened_at TEXT,
            maturity_at TEXT,settled_at TEXT,terms_json TEXT)""")
        for n in range(6):
            terms = {"fee_authority": LEGACY_AUTHORITY,
                     "annual_rate_fraction": "0.365",
                     "day_count_basis": 365,
                     "instrument_id": f"CAUCION-PUBLIC-PAPER-{n}",
                     "currency": "ARS", "fee_payment": "MATURITY",
                     "start_date": "2026-08-28",
                     "quoted_at": "2026-08-28T11:00:00-03:00",
                     "maturity_at": "2026-08-31T15:00:00-03:00",
                     "quoted_total_fees": None, "fee_quote_principal": None,
                     "minimum_principal": "100", "available_principal": "100000",
                     "principal_step": "1",
                     "paper_fill_policy": "CONSERVATIVE_NOTIONAL_CAP",
                     "metadata_source": "CONTRACT_EVIDENCE_V2:SYNTHETIC_TEST_ONLY"}
            raw = json.dumps(terms, sort_keys=True)
            signature = hashlib.sha256((raw + "|1000").encode()).hexdigest()
            conn.execute("INSERT INTO paper_cauciones VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (f"PAPER-CAUCION-{n}", f"req-{n}", signature,
                          "PRODUCTION_PAPER", terms["instrument_id"], "ARS", "MATURED",
                          "1000", "0.365", 3, 365, "3.00", "0.20", "MATURITY",
                          terms["quoted_at"], terms["maturity_at"],
                          terms["maturity_at"], raw))
    return path


def test_six_old_matured_rows_replay_original_model_without_book_mutation(tmp_path):
    path = _six_legacy_matured(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = historical_public_fee_provenance(path)
    assert receipt["status"] == "GREEN"
    assert receipt["rows"] == receipt["legacy_rows"] == receipt["legacy_matured"] == 6
    assert receipt["legacy_original_model_matches"] == 6
    assert receipt["strict_contracts_checked"] == 6
    assert receipt["allocation_absent"] == 6
    assert receipt["public_model_nonpositive_net"] == 0
    assert receipt["public_model_higher_than_saved"] == 6
    assert receipt["legacy_original_model_mismatches"] == receipt["unsupported_terms"] == 0
    assert receipt["broker_account_fees_verified"] is False
    assert receipt["accounting_reconciled"] is False
    assert receipt["data_mutation_attempted"] is False
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert "req-" not in json.dumps(receipt)


def test_changed_legacy_cost_fails_replay_and_does_not_erase_record(tmp_path):
    path = _six_legacy_matured(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE paper_cauciones SET total_fees='0.19' WHERE request_id='req-0'")
    before = path.read_bytes()
    receipt = historical_public_fee_provenance(path)
    assert receipt["status"] == "RED"
    assert receipt["legacy_original_model_mismatches"] == 1
    assert receipt["legacy_original_model_matches"] == 5
    assert path.read_bytes() == before


def test_missing_ledger_fails_closed_without_creating_it(tmp_path):
    path = tmp_path / "absent.db"
    receipt = historical_public_fee_provenance(path)
    assert receipt["status"] == "RED"
    assert receipt["reason"] == "DATABASE_UNAVAILABLE"
    assert not path.exists()


def test_changed_chronology_fails_even_with_renewed_request_fingerprint(tmp_path):
    path = _six_legacy_matured(tmp_path)
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT terms_json FROM paper_cauciones WHERE request_id='req-0'").fetchone()
        terms = json.loads(row[0])
        terms["quoted_at"] = "2026-08-28T12:00:00-03:00"
        raw = json.dumps(terms, sort_keys=True)
        sig = hashlib.sha256((raw + "|1000").encode()).hexdigest()
        conn.execute("UPDATE paper_cauciones SET terms_json=?,request_fingerprint=? "
                     "WHERE request_id='req-0'", (raw, sig))
    receipt = historical_public_fee_provenance(path)
    assert receipt["status"] == "RED"
    assert receipt["unsupported_terms"] == 1
    assert receipt["strict_contracts_checked"] == 5


def test_inconsistent_allocation_never_gets_recast_as_fee_reconciliation(tmp_path):
    path = _six_legacy_matured(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE paper_caucion_allocations(
            paper_id TEXT,request_id TEXT,decision_json TEXT)""")
        conn.execute("INSERT INTO paper_caucion_allocations VALUES(?,?,?)",
                     ("PAPER-CAUCION-0", "forged-request", "{}"))
    receipt = historical_public_fee_provenance(path)
    assert receipt["status"] == "RED"
    assert receipt["allocation_inconsistent"] == 1
    assert receipt["allocation_absent"] == 5
