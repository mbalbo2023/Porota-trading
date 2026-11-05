"""A668 test-first isolated SQLite; never opens provider, host, or PAPER writer."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rc6_a668_nonoption_native_fivekey_readonly.py"
spec = importlib.util.spec_from_file_location("a668_fivekey", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
STAMP = "2026-11-04T20:20:17+00:00"
DATASET = "e73581d2-4c28-4ff3-abdc-b4f1f906ef92"


class NativeFiveKeyReadonly(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="rc6_a668_ro_")
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "paper.db"
        with sqlite3.connect(self.path) as c:
            c.executescript("""
              PRAGMA journal_mode=WAL;
              CREATE TABLE paper_workspace(id INTEGER,namespace TEXT,origin TEXT,
                imported_legacy INTEGER,dataset_id TEXT);
              CREATE TABLE observer_state(id INTEGER,real_orders_sent INTEGER);
              CREATE TABLE financial_instrument_catalog(
                ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,
                settlement TEXT,settlement_source TEXT,status TEXT,
                capability TEXT,metadata_json TEXT,last_seen_at TEXT);
              CREATE TABLE contract_evidence_v2_current(
                family TEXT,ticker TEXT,market TEXT,currency TEXT,settlement TEXT,
                source_class TEXT);
              INSERT INTO paper_workspace VALUES(1,'POROTA_PAPER_V17_FRESH','FRESH_EMPTY',0,
                'e73581d2-4c28-4ff3-abdc-b4f1f906ef92');
              INSERT INTO observer_state VALUES(1,0);
            """)
            self.row(c, "B1", "BONOS", native=True)
            self.row(c, "O1", "OBLIGACIONES", native=True,
                     family_literal="ON")
            self.row(c, "F1", "FCI", discovery="IOL_COMPLEMENTARY")

    @staticmethod
    def row(conn, ticker, family, *, native=False, discovery="PPI_PRIMARY",
            settlement_source="PPI_FIELD", settlement="A-24HS", market="BYMA",
            family_literal=None, raw_update=None, stamp=STAMP, status="AVAILABLE"):
        raw = {"_discovery_source": discovery}
        if native:
            raw.update(ticker=ticker, type=family_literal or family, market=market,
                       currency="ARS", settlement=settlement)
        raw.update(raw_update or {})
        conn.execute("""INSERT INTO financial_instrument_catalog VALUES(
          ?,?,?,?,?,?,?,?,?,?)""", (ticker, family, market, "ARS", settlement,
                                    settlement_source, status, "NEEDS_NOMINAL_UNITS"
                                    if family != "FCI" else "NEEDS_FUND_SETTLEMENT",
                                    json.dumps(raw), stamp))

    def probe(self):
        value = module.diagnose(self.path, enforce_ro_mount=False)
        self.assertFalse(value["private_ro_mount"])
        return value

    @staticmethod
    def classes(value, family):
        return {(x["class"], x["claim"]): x["count"] for x in value["joint"]
                if x["family"] == family}

    def test_mount_mandatory_on_real_entrypoint(self):
        value = module.diagnose(self.path)
        self.assertEqual(value["state"], "NO_VERIFICADO")
        self.assertEqual(value["reason"], "PRIVATE_RO_MOUNT_REQUIRED")

    def test_exact_five_literal_ppi_claim_is_structural_not_paper_admission(self):
        value = self.probe()
        self.assertEqual(value["state"], "READ_ONLY_OBSERVED")
        self.assertEqual(value["target_total"], 3)
        self.assertEqual(self.classes(value, "BONOS"),
                         {("NATIVE_FIVE_STRUCTURAL_MATCH", "PPI_PRIMARY"): 1})
        self.assertEqual(self.classes(value, "OBLIGACIONES"),
                         {("NATIVE_FIVE_STRUCTURAL_MATCH", "PPI_PRIMARY"): 1})
        self.assertEqual(self.classes(value, "FCI"),
                         {("NOT_PPI_PRIMARY", "OTHER_OR_ABSENT"): 1})
        self.assertFalse(value["paper_admission_certified"])
        self.assertFalse(value["runtime_sha_verified"])

    def test_request_candidate_is_not_provider_settlement_even_with_literals(self):
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE financial_instrument_catalog SET settlement_source='REQUEST_CANDIDATE' WHERE ticker='O1'")
        value = self.probe()
        self.assertEqual(self.classes(value, "OBLIGACIONES"),
                         {("SETTLEMENT_NOT_PROVIDER_ATTESTED", "PPI_PRIMARY"): 1})

    def test_mixed_case_spoofed_provider_labels_never_attest_five_native_literals(self):
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE financial_instrument_catalog SET metadata_json=? WHERE ticker='B1'",
                      (json.dumps({"_discovery_source": "ppi_primary", "ticker": "B1",
                                   "type": "BONOS", "market": "BYMA", "currency": "ARS",
                                   "settlement": "A-24HS"}),))
            c.execute("UPDATE financial_instrument_catalog SET settlement_source='ppi_field' WHERE ticker='O1'")
        value = self.probe()
        self.assertEqual(self.classes(value, "BONOS"),
                         {("NOT_PPI_PRIMARY", "OTHER_OR_ABSENT"): 1})
        self.assertEqual(self.classes(value, "OBLIGACIONES"),
                         {("SETTLEMENT_NOT_PROVIDER_ATTESTED", "PPI_PRIMARY"): 1})

    def test_missing_literal_and_native_mismatch_stay_distinct(self):
        with sqlite3.connect(self.path) as c:
            self.row(c, "O2", "OBLIGACIONES", raw_update={"ticker": "O2", "type": "ON",
                     "market": "BYMA", "currency": "ARS"})
            self.row(c, "F2", "FCI", native=True, raw_update={"market": "A3"})
        value = self.probe()
        self.assertEqual(self.classes(value, "OBLIGACIONES"),
                         {("NATIVE_FIVE_STRUCTURAL_MATCH", "PPI_PRIMARY"): 1,
                          ("NATIVE_FIELD_MISSING", "PPI_PRIMARY"): 1})
        self.assertEqual(self.classes(value, "FCI"),
                         {("NOT_PPI_PRIMARY", "OTHER_OR_ABSENT"): 1,
                          ("NATIVE_FIELD_MISMATCH", "PPI_PRIMARY"): 1})
        self.assertEqual(value["missing_literals_by_family"]["OBLIGACIONES"]["settlement"], 1)

    def test_current_join_requires_five_literal_dimensions_not_alias_or_ticker(self):
        with sqlite3.connect(self.path) as c:
            # Historical legacy ON must not suppress the actual OBLIGACIONES gap.
            c.execute("INSERT INTO contract_evidence_v2_current VALUES('ON','O1','BYMA','ARS','A-24HS','PPI_STRUCTURED_API')")
            # A genuinely exact BONOS current excludes only that exact row.
            c.execute("INSERT INTO contract_evidence_v2_current VALUES('BONOS','B1','BYMA','ARS','A-24HS','PPI_STRUCTURED_API')")
        value = self.probe()
        self.assertEqual(value["target_by_family"], {"BONOS": 0, "FCI": 1, "OBLIGACIONES": 1})
        self.assertEqual(self.classes(value, "OBLIGACIONES"),
                         {("NATIVE_FIVE_STRUCTURAL_MATCH", "PPI_PRIMARY"): 1})

    def test_future_observed_clock_or_missing_record_is_not_structurally_ready(self):
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE financial_instrument_catalog SET last_seen_at='2040-01-01T00:00:00Z' WHERE ticker='B1'")
        value = self.probe()
        self.assertEqual(self.classes(value, "BONOS"),
                         {("NATIVE_FIVE_CLOCK_HOLD", "PPI_PRIMARY"): 1})

    def test_unrecognized_currency_and_bogus_names_never_leave_receipt(self):
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE financial_instrument_catalog SET metadata_json=? WHERE ticker='B1'",
                      (json.dumps({"_discovery_source": "PPI_PRIMARY", "ticker": "B1",
                                   "type": "BONOS", "market": "BYMA", "currency": "SECRET_ACCT",
                                   "settlement": "A-24HS"}),))
        value = self.probe()
        self.assertEqual(self.classes(value, "BONOS"),
                         {("NATIVE_CURRENCY_UNRECOGNIZED", "PPI_PRIMARY"): 1})
        self.assertNotIn("SECRET_ACCT", json.dumps(value))
        self.assertNotIn("B1", json.dumps(value))

    def test_false_zero_in_unsupported_status_fails_closed(self):
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE financial_instrument_catalog SET status='PAUSED' ")
        value = self.probe()
        self.assertEqual(value["state"], "NO_VERIFICADO")
        self.assertEqual(value["reason"], "ACTIVE_GAP_DENOMINATOR_HOLD")

    def test_observer_orders_positive_fails_closed(self):
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE observer_state SET real_orders_sent=1")
        self.assertEqual(self.probe()["reason"], "PAPER_ORDER_COUNTER_HOLD")

    def test_no_raw_keys_or_forged_classes_in_sanitized_receipt(self):
        value = {**self.probe(), "private_ro_mount": True,
                 "sensitive_cookie": "DO_NOT_PRINT"}
        safe = module.sanitize(value)
        self.assertEqual(safe["state"], "READ_ONLY_OBSERVED")
        self.assertNotIn("sensitive_cookie", json.dumps(safe))
        value["joint"][0]["class"] = "PRIVATE_PROVIDER:ACCOUNT"
        self.assertEqual(module.sanitize(value)["state"], "NO_VERIFICADO")

    def test_progress_budget_or_sqlite_error_yields_no_partial_counts(self):
        with sqlite3.connect(self.path) as c:
            c.execute("DROP TABLE contract_evidence_v2_current")
        value = self.probe()
        self.assertEqual(value["state"], "NO_VERIFICADO")
        self.assertEqual(value["joint"], [])


if __name__ == "__main__":
    unittest.main()
