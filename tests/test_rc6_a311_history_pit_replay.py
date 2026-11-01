"""A311: all fixtures are SYNTHETIC; no market-data/fill certification."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.rc6_a311_history_pit_replay import (
    FAMILIES, review, _atomic_receipt, absent_source_receipt,
)


DECISION = "2026-10-30T20:00:00+00:00"
LATE = "2026-11-01T03:46:34.668+00:00"
EARLY = "2026-10-30T18:00:00+00:00"


def row(symbol, family, *, source="PPI", publication=EARLY,
        knowledge=EARLY, basis=True):
    sample = {
        "date": "2026-10-30", "symbol": symbol,
        "instrumentType": family, "market": "BYMA", "currency": "ARS",
        "settlementTerm": "A-24HS",
        "priceBasis": "RAW", "adjustmentBasis": "RAW_NO_ADJUSTMENT",
        "adjusted": False, "published_at_utc": publication,
        "known_at_utc": knowledge, "bar_kind": "EOD",
        "session_close_at_utc": "2026-10-30T17:00:00Z",
        "schedule_valid_from": "2026-10-01",
        "schedule_valid_through": "2026-11-01",
        # Deterministic fixture marker, not real COM19024/COM19031 proof.
        "schedule_source_sha256": "0" * 64,
    }
    if family == "CAUCIONES":
        sample.update({"tna_pct": "25.50", "principal": "100000",
                       "term_days": 1, "maturity_date": "2026-11-02"})
    elif family == "FCI":
        sample.update({"nav_per_unit": "113.50",
                       "fund_class_id": "SYNTHETIC_CLASS_A",
                       "fund_cutoff_at_utc": "2026-10-30T20:00:00Z"})
    elif family == "FUTUROS":
        sample.update({"settlementPrice": "1250", "contractMultiplier": "1000",
                       "expirationDate": "2026-11-30"})
    elif family == "OPCIONES":
        sample.update({"premium": "12", "strike": "5000",
                       "contractMultiplier": "100",
                       "expirationDate": "2026-11-30"})
    elif source == "PPI":
        sample.update({"openingPrice": "100", "max": "105", "min": "99",
                       "price": "102", "volume": "2000"})
    else:
        sample.update({"open": "100", "high": "105", "low": "99",
                       "close": "102", "volume": "2000"})
    if not basis:
        sample.pop("priceBasis")
        sample.pop("adjustmentBasis")
        sample.pop("currency")
    return sample


def sidecar(path, captures):
    """Minimal private fixture following existing sidecar DDL field names."""
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE fresh_captures_rc6(
          id INTEGER PRIMARY KEY,source TEXT,symbol TEXT,instrument_type TEXT,
          market TEXT,currency TEXT,settlement TEXT,requested_from TEXT,
          requested_to TEXT,first_known_at TEXT,retained_rows_json TEXT,
          retained_rows INTEGER,provider_currency_status TEXT,
          price_basis_status TEXT,execution_allowed INTEGER);
        CREATE TABLE fresh_row_revisions_rc6(
          capture_id INTEGER,retained_ordinal INTEGER,economic_day TEXT,
          canonical_row_sha256 TEXT,first_known_at TEXT);
        """)
        for cid, (symbol, family, observed, source, rows) in enumerate(captures, 1):
            conn.execute("INSERT INTO fresh_captures_rc6 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, source, symbol, family, "BYMA", "ARS", "A-24HS",
                 "2026-10-30", "2026-10-30", observed,
                 json.dumps(rows, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")), len(rows),
                 "EXPLICIT_ROWS", "RAW", 0))
            for i, item in enumerate(rows):
                digest = sha256(json.dumps(item, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")).encode()).hexdigest()
                conn.execute("INSERT INTO fresh_row_revisions_rc6 VALUES(?,?,?,?,?)",
                             (cid, i, "2026-10-30", digest, observed))


class TestA311PitReplay(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rc6-a311-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "private.sqlite"

    def test_ten_families_retrospective_but_no_lookahead(self):
        captures = [
            (f"TEST{i:02d}", family, LATE, "PPI",
             [row(f"TEST{i:02d}", family)])
            for i, family in enumerate(FAMILIES)
        ]
        sidecar(self.path, captures)
        before = sha256(self.path.read_bytes()).hexdigest()
        result = review(self.path, decision_at=DECISION)
        self.assertEqual(result["family_denominator"], 10)
        self.assertEqual(result["retrospective_counterfactual"]["rows_validated"], 10)
        self.assertEqual(result["causal_pit"]["rows_verified"], 0)
        self.assertEqual(result["causal_pit"]["temporal_contract_candidate_rows"], 0)
        self.assertEqual(result["causal_pit"]["fills"], 0)
        self.assertEqual(result["causal_pit"]["real_orders_sent"], 0)
        self.assertFalse(result["host_access"])
        self.assertEqual(result["history_v2_writes"], 0)
        self.assertEqual(before, sha256(self.path.read_bytes()).hexdigest())
        for family in FAMILIES:
            self.assertEqual(result["families"][family]["captures"], 1)
            self.assertEqual(result["families"][family]["retrospective_rows"], 1)
            self.assertEqual(result["families"][family]["causal_pit_rows"], 0)
            self.assertIn("RECEIPT_AFTER_HISTORICAL_DECISION",
                          result["families"][family]["reasons"])

    def test_contemporaneous_synthetic_row_still_not_a_fill(self):
        sidecar(self.path, [("GGAL", "ACCIONES", EARLY, "PPI",
                             [row("GGAL", "ACCIONES")])])
        result = review(self.path, decision_at=DECISION)
        self.assertEqual(result["causal_pit"]["rows_verified"], 0)
        self.assertEqual(result["causal_pit"]["temporal_contract_candidate_rows"], 1)
        self.assertEqual(result["causal_pit"]["fills"], 0)
        self.assertFalse(result["families"]["ACCIONES"]["paper_ready_inferred"])
        self.assertEqual(result["families"]["FUTUROS"]["reasons"],
                         {"FAMILY_SOURCE_ABSENT": 1})

    def test_request_metadata_cannot_supply_row_currency_or_basis(self):
        sidecar(self.path, [("D30N6", "LETRAS", EARLY, "PPI",
                             [row("D30N6", "LETRAS", basis=False)])])
        result = review(self.path, decision_at=DECISION)
        self.assertEqual(result["families"]["LETRAS"]["retrospective_rows"], 1)
        self.assertEqual(result["families"]["LETRAS"]["causal_pit_rows"], 0)
        self.assertIn("PER_ROW_MONETARY_AND_CONTRACT_UNVERIFIED",
                      result["families"]["LETRAS"]["reasons"])

    def test_eod_published_after_decision_cannot_be_pit(self):
        source_publish = "2026-10-30T22:00:00+00:00"
        sidecar(self.path, [("AL30", "BONOS", LATE, "PPI",
                             [row("AL30", "BONOS", publication=source_publish,
                                  knowledge=source_publish)])])
        result = review(self.path, decision_at=DECISION)
        self.assertEqual(result["families"]["BONOS"]["causal_pit_rows"], 0)
        self.assertIn("RECEIPT_AFTER_HISTORICAL_DECISION",
                      result["families"]["BONOS"]["reasons"])

    def test_monday_schedule_cannot_be_back_applied_to_friday(self):
        bad = row("GGAL", "ACCIONES")
        bad["schedule_valid_from"] = "2026-11-02"
        bad["schedule_valid_through"] = "2026-12-31"
        sidecar(self.path, [("GGAL", "ACCIONES", EARLY, "PPI", [bad])])
        result = review(self.path, decision_at=DECISION)
        self.assertIn("EOD_SEGMENT_SCHEDULE_EFFECTIVITY_MISMATCH",
                      result["families"]["ACCIONES"]["reasons"])
        self.assertEqual(result["causal_pit"]["rows_verified"], 0)

    def test_prepublish_clock_before_segment_close_is_rejected(self):
        bad = row("GGAL", "ACCIONES")
        bad["session_close_at_utc"] = "2026-10-30T19:00:00Z"
        sidecar(self.path, [("GGAL", "ACCIONES", EARLY, "PPI", [bad])])
        result = review(self.path, decision_at=DECISION)
        self.assertIn("EOD_PUBLISHED_BEFORE_SEGMENT_CLOSE",
                      result["families"]["ACCIONES"]["reasons"])

    def test_etf_cedear_does_not_become_autonomous_etf(self):
        sidecar(self.path, [("SPY", "CEDEARS", EARLY, "PPI",
                             [row("SPY", "CEDEARS")])])
        result = review(self.path, decision_at=DECISION)
        self.assertEqual(result["families"]["CEDEARS"]["captures"], 1)
        self.assertEqual(result["families"]["ETFS"]["captures"], 0)

    def test_invalid_historical_ohlcv_fails_without_counterfactual(self):
        invalid = row("GGAL", "ACCIONES")
        invalid["max"] = "90"
        sidecar(self.path, [("GGAL", "ACCIONES", EARLY, "PPI", [invalid])])
        result = review(self.path, decision_at=DECISION)
        self.assertEqual(result["families"]["ACCIONES"]["retrospective_rows"], 0)
        self.assertIn("HISTORICAL_VALUE_UNVERIFIED",
                      result["families"]["ACCIONES"]["reasons"])

    def test_caucion_aggregate_index_cannot_replace_term_and_principal(self):
        invalid = row("CAUCION_IDX", "CAUCIONES")
        invalid.pop("term_days")
        invalid.pop("principal")
        sidecar(self.path, [("CAUCION_IDX", "CAUCIONES", EARLY, "PPI", [invalid])])
        result = review(self.path, decision_at=DECISION)
        self.assertEqual(result["families"]["CAUCIONES"]["retrospective_rows"], 0)
        self.assertEqual(result["causal_pit"]["rows_verified"], 0)

    def test_fci_nav_requires_exact_class_and_cutoff(self):
        invalid = row("FONDO", "FCI")
        invalid.pop("fund_class_id")
        sidecar(self.path, [("FONDO", "FCI", EARLY, "PPI", [invalid])])
        result = review(self.path, decision_at=DECISION)
        self.assertEqual(result["families"]["FCI"]["retrospective_rows"], 0)

    def test_expired_future_not_counted_as_historical_valid_contract(self):
        invalid = row("DLR", "FUTUROS")
        invalid["expirationDate"] = "2026-10-29"
        sidecar(self.path, [("DLR", "FUTUROS", EARLY, "PPI", [invalid])])
        result = review(self.path, decision_at=DECISION)
        self.assertEqual(result["families"]["FUTUROS"]["retrospective_rows"], 0)

    def test_future_ohlcv_cannot_substitute_for_multiplier_and_expiry(self):
        invalid = row("DLR", "FUTUROS")
        for field in ("settlementPrice", "contractMultiplier", "expirationDate"):
            invalid.pop(field)
        invalid.update({"openingPrice": "100", "max": "105", "min": "99",
                        "price": "102", "volume": "2000"})
        sidecar(self.path, [("DLR", "FUTUROS", EARLY, "PPI", [invalid])])
        result = review(self.path, decision_at=DECISION)
        self.assertEqual(result["families"]["FUTUROS"]["retrospective_rows"], 0)

    def test_option_ohlcv_cannot_substitute_for_strike_multiplier_expiry(self):
        invalid = row("GGALC", "OPCIONES")
        for field in ("premium", "strike", "contractMultiplier", "expirationDate"):
            invalid.pop(field)
        invalid.update({"openingPrice": "100", "max": "105", "min": "99",
                        "price": "102", "volume": "2000"})
        sidecar(self.path, [("GGALC", "OPCIONES", EARLY, "PPI", [invalid])])
        result = review(self.path, decision_at=DECISION)
        self.assertEqual(result["families"]["OPCIONES"]["retrospective_rows"], 0)

    def test_revision_hash_mismatch_is_hard_error(self):
        sidecar(self.path, [("GGAL", "ACCIONES", EARLY, "PPI",
                             [row("GGAL", "ACCIONES")])])
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE fresh_row_revisions_rc6 SET canonical_row_sha256=?",
                         ("0" * 64,))
        with self.assertRaisesRegex(ValueError, "REVISION_HASH"):
            review(self.path, decision_at=DECISION)

    def test_no_source_is_explicit_ten_family_denominator(self):
        sidecar(self.path, [])
        result = review(self.path, decision_at=DECISION)
        self.assertEqual(result["capture_count"], 0)
        self.assertEqual(len(result["families"]), 10)
        self.assertTrue(all(x["reasons"] == {"FAMILY_SOURCE_ABSENT": 1}
                            for x in result["families"].values()))

    def test_receipt_atomic_and_repeatable_without_writes(self):
        sidecar(self.path, [("GGAL", "ACCIONES", EARLY, "PPI",
                             [row("GGAL", "ACCIONES")])])
        first = review(self.path, decision_at=DECISION)
        second = review(self.path, decision_at=DECISION)
        self.assertEqual(first, second)
        target = Path(self.temporary.name) / "receipt.json"
        _atomic_receipt(target, first)
        self.assertEqual(json.loads(target.read_text()), first)
        with self.assertRaisesRegex(ValueError, "DESTINATION_UNSAFE"):
            _atomic_receipt(target, second)

    def test_naive_decision_clock_rejected(self):
        sidecar(self.path, [])
        with self.assertRaisesRegex(ValueError, "DECISION_CLOCK_UNVERIFIED"):
            review(self.path, decision_at="2026-10-30T17:00:00")

    def test_absent_entitlement_has_all_ten_explicit_not_ready(self):
        result = absent_source_receipt(decision_at=DECISION,
                                       reason="READONLY_CREDENTIAL_PAIR_ABSENT")
        self.assertEqual(result["capture_count"], 0)
        self.assertEqual(result["family_denominator"], 10)
        self.assertTrue(all(not value["paper_ready_inferred"]
                            and "READONLY_CREDENTIAL_PAIR_ABSENT" in value["reasons"]
                            for value in result["families"].values()))
        self.assertEqual(result["history_v2_writes"], 0)


if __name__ == "__main__":
    unittest.main()
