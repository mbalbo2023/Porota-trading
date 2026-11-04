"""A655 synthetic SQLite fixtures ONLY; never contacts live host/PPI/Watch."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rc6_a655_gap_gate_histogram_readonly.py"
spec = importlib.util.spec_from_file_location("a655_hist", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

DATASET = "a5749bc1-04ee-4383-a1f4-b0bcedde93f2"
STAMP = "2026-11-04T19:20:17+00:00"


class A655Histogram(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="rc6_a655_fixture_")
        self.addCleanup(self.folder.cleanup)
        self.db = Path(self.folder.name) / "paper.db"
        with sqlite3.connect(self.db) as c:
            c.executescript("""
              PRAGMA journal_mode=WAL;
              CREATE TABLE paper_workspace(id INTEGER,namespace TEXT,origin TEXT,imported_legacy INTEGER,dataset_id TEXT);
              CREATE TABLE observer_state(id INTEGER,real_orders_sent INTEGER);
              CREATE TABLE financial_instrument_catalog(
                ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,
                status TEXT,capability TEXT,metadata_json TEXT);
              CREATE TABLE contract_evidence_v2_current(
                family TEXT,ticker TEXT,market TEXT,currency TEXT,settlement TEXT,
                source_class TEXT,snapshot_id INTEGER,evidence_hash TEXT,observed_at TEXT);
              CREATE TABLE contract_evidence_v2_snapshots(
                snapshot_id INTEGER PRIMARY KEY,family TEXT,ticker TEXT,market TEXT,currency TEXT,
                settlement TEXT,source_class TEXT,evidence_hash TEXT,observed_at TEXT,evidence_json TEXT);
              CREATE TABLE complementary_contract_retry(
                ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,
                source TEXT,reason TEXT);
              INSERT INTO observer_state VALUES(1,0);
            """)
            c.execute("INSERT INTO paper_workspace VALUES(?,?,?,?,?)",
                      (1, "POROTA_PAPER_V17_FRESH", "FRESH_EMPTY", 0, DATASET))
            self.catalog(c, "XYZ", "OPCIONES", "BYMA", "ARS", "CI", "NEEDS_OPTION_CONTRACT")
            self.catalog(c, "XYZ", "BONOS", "BYMA", "ARS", "CI", "NEEDS_NOMINAL_UNITS")
            self.catalog(c, "OLD", "ON", "BYMA", "USD", "48H", "NEEDS_NOMINAL_UNITS")
            # Remaining three families exist but are READY in this synthetic
            # fixture. A655 fails closed when a family denominator vanishes.
            self.catalog(c, "FUT1", "FUTUROS", "A3", "ARS", "CI", "READY_PAPER_FUTURES")
            self.catalog(c, "CAU1", "CAUCIONES", "BYMA", "ARS", "CI", "READY_PAPER_CAUCION_PLACING")
            self.catalog(c, "FCI1", "FCI", "BYMA", "ARS", "CI", "READY_PAPER_FCI_SUBSCRIPTION")
            c.execute("INSERT INTO complementary_contract_retry VALUES(?,?,?,?,?,?,?)",
                      ("XYZ", "OPCIONES", "BYMA", "ARS", "CI", "IOL_COMPLEMENTARY",
                       "COMPLEMENTARY_IDENTITY_AMBIGUOUS:matches=2"))

    @staticmethod
    def catalog(conn, ticker, family, market, currency, settlement, cap, metadata="{}"):
        conn.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?)",
                     (ticker, family, market, currency, settlement, "AVAILABLE", cap, metadata))

    @staticmethod
    def snapshot(conn, ident, family, ticker, market="BYMA", currency="ARS", settlement="CI",
                 source="PPI_FIELD", override_snapshot=None, digest_override=None):
        payload = {"contract": "synthetic", "scope": "READ_ONLY"}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        copied = (family, ticker, market, currency, settlement)
        other = override_snapshot or copied
        conn.execute("INSERT INTO contract_evidence_v2_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (ident, *other, source, digest, STAMP, raw))
        conn.execute("INSERT INTO contract_evidence_v2_current VALUES(?,?,?,?,?,?,?,?,?)",
                     (*copied, source, ident, digest_override or digest, STAMP))

    def probe(self):
        raw = module.diagnose(self.db, enforce_ro_mount=False)
        # Local-only test bypass has no right to be laundered into a public receipt.
        self.assertFalse(raw["private_ro_mount"])
        return raw

    def test_no_host_mount_fails_closed_by_default(self):
        report = module.diagnose(self.db)
        self.assertEqual(report["state"], "NO_VERIFICADO")
        self.assertEqual(report["reason"], "PRIVATE_RO_MOUNT_REQUIRED")

    def test_raw_alias_and_ticker_collisions_never_confer_current(self):
        with sqlite3.connect(self.db) as c:
            self.snapshot(c, 1, "BONOS", "XYZ")
            self.snapshot(c, 2, "OPCIONES", "XYZ", market="A3")
            self.snapshot(c, 3, "OBLIGACIONES", "OLD", currency="USD", settlement="48H")
        report = self.probe()
        self.assertEqual(report["state"], "READ_ONLY_OBSERVED")
        self.assertEqual(report["catalog_active_gap_total"], 3)
        options = next(x for x in report["joint"] if x["family"] == "OPCIONES")
        self.assertEqual(options["current"], "NO_EXACT_CURRENT")
        self.assertEqual(options["retry"], "AMBIGUOUS_IDENTITY")
        aliases = next(x for x in report["joint"] if x["family"] == "OBLIGACIONES")
        self.assertEqual(aliases["current"], "NO_EXACT_CURRENT")
        bond = next(x for x in report["joint"] if x["family"] == "BONOS")
        self.assertEqual(bond["current"], "EXACT_PPI_SOURCE_LABEL")
        self.assertEqual(report["a626_historical_row_attribution"], "NOT_VERIFIED")

    def test_genuine_current_label_is_correlative_not_paper_admission(self):
        with sqlite3.connect(self.db) as c:
            self.snapshot(c, 1, "OPCIONES", "XYZ")
        report = self.probe()
        item = next(x for x in report["joint"] if x["family"] == "OPCIONES")
        self.assertEqual(item["current"], "EXACT_PPI_SOURCE_LABEL")
        self.assertEqual(item["v17"], "V17_ABSENT")
        self.assertEqual(report["diagnostic_class"], "CORRELATIVE_DIAGNOSTIC_ONLY")
        self.assertFalse(report["paper_admission_certified"])
        self.assertEqual(report["provider_gets"], 0)
        self.assertFalse(report["ppi_watch_touched"])

    def test_orphan_snapshot_fails_closed_no_partial_histogram(self):
        with sqlite3.connect(self.db) as c:
            self.snapshot(c, 1, "OPCIONES", "XYZ", override_snapshot=("BONOS", "XYZ", "BYMA", "ARS", "CI"))
        report = self.probe()
        self.assertEqual(report["state"], "NO_VERIFICADO")
        self.assertEqual(report["reason"], "V2_SNAPSHOT_IDENTITY_HOLD")
        self.assertEqual(report["joint"], [])

    def test_evidence_hash_mismatch_fails_closed(self):
        with sqlite3.connect(self.db) as c:
            self.snapshot(c, 1, "OPCIONES", "XYZ", digest_override="a" * 64)
        report = self.probe()
        self.assertEqual(report["reason"], "V2_SNAPSHOT_IDENTITY_HOLD")

    def test_time_order_reversal_fails_closed(self):
        with sqlite3.connect(self.db) as c:
            self.snapshot(c, 1, "OPCIONES", "XYZ")
            c.execute("UPDATE contract_evidence_v2_current SET observed_at='2026-11-03T00:00:00+00:00'")
        report = self.probe()
        self.assertEqual(report["reason"], "V2_SNAPSHOT_CLOCK_HOLD")

    def test_metadata_status_and_absence_distinguished(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE financial_instrument_catalog SET metadata_json=? WHERE ticker='XYZ' AND instrument_type='OPCIONES'",
                      (json.dumps({"_contract_bridge": {"status": "BLOCKED"},
                                   "financial_contract_v17": {"cash_multiplier": 1}}),))
        report = self.probe()
        option = next(x for x in report["joint"] if x["family"] == "OPCIONES")
        self.assertEqual(option["bridge"], "BLOCKED")
        self.assertEqual(option["v17"], "V17_ATTACHED")

    def test_nonactive_status_has_explicit_denominator_not_silent_exclusion(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE financial_instrument_catalog SET status='PAUSED_EXPLICIT' WHERE ticker='OLD'")
        report = self.probe()
        self.assertEqual(report["catalog_active_gap_total"], 2)
        self.assertEqual(report["catalog_six_family_total"], 6)
        self.assertEqual(report["status_coverage"]["OBLIGACIONES"],
                         {"active": 0, "active_gap": 0, "all": 1, "nonactive": 1, "ready": 0})

    def test_all_nonactive_gap_statuses_are_not_false_zero_success(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE financial_instrument_catalog SET status='PAUSED_EXPLICIT' WHERE capability LIKE 'NEEDS_%'")
        report = self.probe()
        self.assertEqual(report["state"], "NO_VERIFICADO")
        self.assertEqual(report["reason"], "ACTIVE_GAP_DENOMINATOR_HOLD")

    def test_unknown_capability_and_retry_details_never_leave_receipt(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE financial_instrument_catalog SET capability='SECRET_ACCOUNT_X' WHERE ticker='XYZ' AND instrument_type='OPCIONES'")
            c.execute("UPDATE complementary_contract_retry SET reason='private-account-123' WHERE ticker='XYZ'")
        report = self.probe()
        rendered = json.dumps(report)
        self.assertNotIn("SECRET_ACCOUNT", rendered)
        self.assertNotIn("private-account", rendered)
        option = next(x for x in report["joint"] if x["family"] == "OPCIONES")
        self.assertEqual(option["capability"], "OTHER_CAPABILITY_CODE")
        self.assertEqual(option["retry"], "OTHER_RETRY")

    def test_single_snapshot_when_wal_writer_changes_catalog_mid_scan(self):
        with sqlite3.connect(self.db) as c:
            c.execute("PRAGMA wal_checkpoint(PASSIVE)")
        old = module._current_labels

        def switch_after_begin(reader):
            out = old(reader)
            with sqlite3.connect(self.db, timeout=1.0) as writer:
                writer.execute("UPDATE financial_instrument_catalog SET capability='READY_PAPER_OPTION_LONG' WHERE ticker='XYZ' AND instrument_type='OPCIONES'")
            return out

        with patch.object(module, "_current_labels", side_effect=switch_after_begin):
            report = self.probe()
        self.assertEqual(report["state"], "READ_ONLY_OBSERVED")
        self.assertEqual(report["catalog_active_gap_total"], 3)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT capability FROM financial_instrument_catalog WHERE ticker='XYZ' AND instrument_type='OPCIONES'").fetchone()[0],
                             "READY_PAPER_OPTION_LONG")

    def test_runner_sanitizer_strips_private_keys_and_requires_mount_receipt(self):
        raw = self.probe()
        raw["private_ro_mount"] = True  # Simulated trusted host in fixture; not a real host claim.
        raw["surprise_private_key"] = "DO_NOT_EXPOSE"
        safe = module.sanitize(raw)
        self.assertEqual(safe["state"], "READ_ONLY_OBSERVED")
        self.assertNotIn("surprise_private_key", json.dumps(safe))
        self.assertEqual(safe["by_family"], {"BONOS": 1, "OBLIGACIONES": 1, "OPCIONES": 1})
        raw["catalog_active_gap_total"] = 999
        self.assertEqual(module.sanitize(raw)["state"], "NO_VERIFICADO")

    def test_runner_rejects_admission_or_real_order_claim(self):
        raw = self.probe()
        raw["private_ro_mount"] = True
        raw["paper_admission_certified"] = True
        self.assertEqual(module.sanitize(raw)["state"], "NO_VERIFICADO")
        raw["paper_admission_certified"] = False
        raw["real_orders_sent"] = 1
        self.assertEqual(module.sanitize(raw)["state"], "NO_VERIFICADO")


if __name__ == "__main__":
    unittest.main()
