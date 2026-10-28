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
            paper_id TEXT, request_id TEXT, request_fingerprint TEXT,
            principal TEXT,gross_interest TEXT,total_fees TEXT,
            status TEXT,terms_json TEXT,interest_days INTEGER)""")
        for n in range(6):
            terms = {"fee_authority": LEGACY_AUTHORITY,
                     "annual_rate_fraction": "0.365",
                     "day_count_basis": 365,
                     "paper_fill_policy": "CONSERVATIVE_NOTIONAL_CAP",
                     "metadata_source": "CONTRACT_EVIDENCE_V2:SYNTHETIC_TEST_ONLY"}
            raw = json.dumps(terms, sort_keys=True)
            signature = hashlib.sha256((raw + "|1000").encode()).hexdigest()
            conn.execute("INSERT INTO paper_cauciones VALUES(?,?,?,?,?,?,?,?,?)",
                         (f"PAPER-CAUCION-{n}", f"req-{n}", signature,
                          "1000", "3.00", "0.20", "MATURED", raw, 3))
    return path


def test_six_old_matured_rows_replay_original_model_without_book_mutation(tmp_path):
    path = _six_legacy_matured(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = historical_public_fee_provenance(path)
    assert receipt["status"] == "GREEN"
    assert receipt["rows"] == receipt["legacy_rows"] == receipt["legacy_matured"] == 6
    assert receipt["legacy_original_model_matches"] == 6
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
        conn.execute("UPDATE paper_cauciones SET total_fees='900' WHERE request_id='req-0'")
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
