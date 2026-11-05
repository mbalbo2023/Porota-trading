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
                settlement_source TEXT,status TEXT,capability TEXT,metadata_json TEXT);
              CREATE TABLE contract_evidence_v2_current(
                family TEXT,ticker TEXT,market TEXT,currency TEXT,settlement TEXT,
                source_class TEXT,snapshot_id INTEGER,evidence_hash TEXT,observed_at TEXT);
              CREATE TABLE contract_evidence_v2_snapshots(
                snapshot_id INTEGER PRIMARY KEY,family TEXT,ticker TEXT,market TEXT,currency TEXT,
                settlement TEXT,source_class TEXT,evidence_hash TEXT,observed_at TEXT,evidence_json TEXT,
                source_ref TEXT NOT NULL DEFAULT 'synthetic:fixture',effective_at TEXT);
              CREATE TABLE complementary_contract_retry(
                ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,
                source TEXT,reason TEXT);
              CREATE TABLE contract_evidence_v2_changes(
                change_id INTEGER PRIMARY KEY,family TEXT,ticker TEXT,market TEXT,currency TEXT,
                settlement TEXT,source_class TEXT,previous_hash TEXT,current_hash TEXT,
                detected_at TEXT,status TEXT);
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
        conn.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?)",
                     (ticker, family, market, currency, settlement, "PPI_FIELD",
                      "AVAILABLE", cap, metadata))

    @staticmethod
    def snapshot(conn, ident, family, ticker, market="BYMA", currency="ARS", settlement="CI",
                 source="PPI_FIELD", override_snapshot=None, digest_override=None):
        payload = {"contract": "synthetic", "scope": "READ_ONLY"}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        copied = (family, ticker, market, currency, settlement)
        other = override_snapshot or copied
        conn.execute("""INSERT INTO contract_evidence_v2_snapshots
            (snapshot_id,family,ticker,market,currency,settlement,source_class,
             evidence_hash,observed_at,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (ident, *other, source, digest, STAMP, raw))
        conn.execute("INSERT INTO contract_evidence_v2_current VALUES(?,?,?,?,?,?,?,?,?)",
                     (*copied, source, ident, digest_override or digest, STAMP))

    @staticmethod
    def option_review(conn):
        conn.execute("""UPDATE financial_instrument_catalog SET capability=?,metadata_json=?
          WHERE ticker='XYZ' AND instrument_type='OPCIONES'""",
          ("CONTRACT_EVIDENCE_REVIEW_REQUIRED", json.dumps({"_contract_bridge": {
              "status": "BLOCKED", "gaps": ["CHANGE_REVIEW_REQUIRED"]}})))

    @staticmethod
    def v2(conn, *, source="PPI_STRUCTURED_API", previous=None, current=None,
           prev_id=30, current_id=31, change_id=99, event=STAMP, market="BYMA"):
        """Real canonical key+source hash join, no PPI/host calls."""
        key = ("OPCIONES", "XYZ", market, "ARS", "CI")
        def snapshot(ident, payload):
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            conn.execute("""INSERT INTO contract_evidence_v2_snapshots
              (snapshot_id,family,ticker,market,currency,settlement,source_class,evidence_hash,
               observed_at,evidence_json,source_ref) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (ident, *key, source, digest, STAMP, raw, "synthetic:fixture"))
            return digest
        before = snapshot(prev_id, previous) if previous is not None else None
        after = snapshot(current_id, current or {"lot": 100})
        conn.execute("INSERT INTO contract_evidence_v2_current VALUES(?,?,?,?,?,?,?,?,?)",
                     (*key, source, current_id, after, STAMP))
        if previous is not None:
            conn.execute("""INSERT INTO contract_evidence_v2_changes
              (change_id,family,ticker,market,currency,settlement,source_class,
               previous_hash,current_hash,detected_at,status)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (change_id, *key, source, before, after, event, "CHANGED_REVIEW_REQUIRED"))

    @staticmethod
    def roots(report):
        return {(row["flags"], row["source_class_bucket"]): row["count"]
                for row in report["option_review_root_hist"]}

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
                      (json.dumps({"_contract_bridge": {"status": "BLOCKED", "gaps": ["MISSING:quantity_step"]},
                                   "financial_contract_v17": {"cash_multiplier": 1}}),))
        report = self.probe()
        option = next(x for x in report["joint"] if x["family"] == "OPCIONES")
        self.assertEqual(option["bridge"], "BLOCKED")
        self.assertEqual(option["v17"], "V17_ATTACHED")

    def test_option_review_gap_and_iol_retry_source_are_joint_and_sanitized(self):
        with sqlite3.connect(self.db) as c:
            bridge = {"_contract_bridge": {"status": "BLOCKED", "gaps": [
                "CHANGE_REVIEW_REQUIRED", "MISSING:underlying"]}}
            c.execute("UPDATE financial_instrument_catalog SET capability=?,metadata_json=? WHERE ticker='XYZ' AND instrument_type='OPCIONES'",
                      ("CONTRACT_EVIDENCE_REVIEW_REQUIRED", json.dumps(bridge)))
            c.execute("""INSERT INTO contract_evidence_v2_changes
              (change_id,family,ticker,market,currency,settlement,source_class,
               current_hash,detected_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (11, "OPCIONES", "XYZ", "BYMA", "ARS", "CI", "PPI_STRUCTURED_API",
               "0" * 64, STAMP, "CHANGED_REVIEW_REQUIRED"))
        report = self.probe()
        option = next(x for x in report["joint"] if x["family"] == "OPCIONES")
        self.assertEqual(option["bridge_reasons"], "CHANGE_REVIEW_REQUIRED+MISSING_FIELDS")
        self.assertEqual(option["retry_source"], "IOL_ONLY")
        self.assertEqual(option["change_log"], "REVIEW_LOG_ENTRY_PRESENT")
        self.assertNotIn("underlying", json.dumps(report))
        raw = {**report, "private_ro_mount": True}
        self.assertEqual(module.sanitize(raw)["state"], "READ_ONLY_OBSERVED")

    def test_conflict_and_time_hold_preserve_categories_without_field_values(self):
        with sqlite3.connect(self.db) as c:
            bridge = {"_contract_bridge": {"status": "BLOCKED", "gaps": [
                "EVIDENCE_NOT_CURRENT", "CONFLICT:PRIVATE_ACCOUNT_987", "CONTRACT_INVALID:SECRET"]}}
            c.execute("UPDATE financial_instrument_catalog SET metadata_json=? WHERE ticker='XYZ' AND instrument_type='OPCIONES'",
                      (json.dumps(bridge),))
            # A different literal family (ON alias) is never a change-log match.
            c.execute("""INSERT INTO contract_evidence_v2_changes
              (change_id,family,ticker,market,currency,settlement,source_class,
               current_hash,detected_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (12, "OBLIGACIONES", "OLD", "BYMA", "USD", "48H", "PPI_STRUCTURED_API",
               "0" * 64, STAMP, "CHANGED_REVIEW_REQUIRED"))
        report = self.probe()
        option = next(x for x in report["joint"] if x["family"] == "OPCIONES")
        self.assertEqual(option["bridge_reasons"], "CONFLICT_TERM+EVIDENCE_TIME_HOLD+INVALID_CONTRACT")
        aliases = next(x for x in report["joint"] if x["family"] == "OBLIGACIONES")
        self.assertEqual(aliases["change_log"], "NO_REVIEW_LOG_ENTRY")
        self.assertNotIn("PRIVATE_ACCOUNT", json.dumps(report))
        self.assertNotIn("SECRET", json.dumps(report))

    def test_unknown_reason_is_bucketed_and_malformed_gaps_fail_closed(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE financial_instrument_catalog SET metadata_json=? WHERE ticker='XYZ' AND instrument_type='OPCIONES'",
                      (json.dumps({"_contract_bridge": {"status": "BLOCKED", "gaps": ["CONFIDENTIAL:secret_account"]}}),))
        report = self.probe()
        option = next(x for x in report["joint"] if x["family"] == "OPCIONES")
        self.assertEqual(option["bridge_reasons"], "OTHER_BLOCKED_GAP")
        self.assertNotIn("secret_account", json.dumps(report))
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE financial_instrument_catalog SET metadata_json=? WHERE ticker='XYZ' AND instrument_type='OPCIONES'",
                      (json.dumps({"_contract_bridge": {"status": "BLOCKED", "gaps": "not a list"}}),))
        self.assertEqual(self.probe()["state"], "NO_VERIFICADO")

    def test_runner_rejects_unallowlisted_bridge_or_source_forgery(self):
        report = self.probe()
        report["private_ro_mount"] = True
        report["joint"][0]["bridge_reasons"] = "ACCOUNTNUMBER"
        self.assertEqual(module.sanitize(report)["state"], "NO_VERIFICADO")
        report["joint"][0]["bridge_reasons"] = "NOT_BLOCKED"
        report["joint"][0]["retry_source"] = "private-identity"
        self.assertEqual(module.sanitize(report)["state"], "NO_VERIFICADO")

    def test_missing_change_table_fails_closed_no_invented_pending_status(self):
        with sqlite3.connect(self.db) as c:
            c.execute("DROP TABLE contract_evidence_v2_changes")
        report = self.probe()
        self.assertEqual(report["state"], "NO_VERIFICADO")
        self.assertEqual(report["reason"], "REQUIRED_SCHEMA_HOLD")
        self.assertEqual(report["joint"], [])

    def test_missing_retry_source_column_fails_closed(self):
        with sqlite3.connect(self.db) as c:
            c.execute("DROP TABLE complementary_contract_retry")
            c.execute("CREATE TABLE complementary_contract_retry(ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,reason TEXT)")
        report = self.probe()
        self.assertEqual(report["reason"], "REQUIRED_SCHEMA_HOLD")

    def test_129_distinct_reason_sets_fail_closed_group_budget(self):
        patterns = ["CHANGE_REVIEW_REQUIRED", "CONFLICT:private", "IDENTITY_CONFLICT",
                    "INVALID_PROVENANCE", "EVIDENCE_NOT_CURRENT",
                    "CONTRACT_V2_CURRENCY_MISMATCH", "INVALID_NUMERIC_TERM:cash_multiplier",
                    "OPTION_IOL_SERIES_CONTRACT_UNVERIFIED"]
        with sqlite3.connect(self.db) as c:
            c.execute("DELETE FROM financial_instrument_catalog WHERE instrument_type='OPCIONES'")
            for i in range(1, 130):
                reasons = [pattern for bit, pattern in enumerate(patterns) if i & (1 << bit)]
                metadata = {"_contract_bridge": {"status": "BLOCKED", "gaps": reasons}}
                self.catalog(c, "OPTION%03d" % i, "OPCIONES", "BYMA", "ARS", "CI",
                             "CONTRACT_EVIDENCE_REVIEW_REQUIRED", json.dumps(metadata))
        report = self.probe()
        self.assertEqual(report["state"], "NO_VERIFICADO")
        self.assertEqual(report["reason"], "GROUPS_LIMIT_HOLD")
        self.assertEqual(report["joint"], [])

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

    def test_a661_genuine_material_difference_is_pending_not_log_guess(self):
        with sqlite3.connect(self.db) as c:
            self.option_review(c)
            self.v2(c, previous={"lot": 100}, current={"lot": 200})
        result = self.probe()
        self.assertEqual(result["state"], "READ_ONLY_OBSERVED")
        self.assertEqual(result["option_review_root_total"], 1)
        self.assertEqual(self.roots(result), {("PENDING_MATERIAL", "PPI_ONLY"): 1})
        self.assertEqual(module.sanitize({**result, "private_ro_mount": True})["state"],
                         "READ_ONLY_OBSERVED")
        self.assertNotIn("XYZ", json.dumps(result))

    def test_a661_historical_provenance_only_log_is_not_material_pending(self):
        with sqlite3.connect(self.db) as c:
            self.option_review(c)
            self.v2(c, previous={"lot": 100, "provider_timestamp": "2026-11-04T18:00:00Z"},
                    current={"lot": 100, "provider_timestamp": "2026-11-04T19:00:00Z"})
        result = self.probe()
        self.assertEqual(result["state"], "READ_ONLY_OBSERVED")
        self.assertEqual(self.roots(result), {("UNEXPLAINED_PERSISTED_BRIDGE", "NONE"): 1})
        self.assertEqual(next(item for item in result["joint"] if item["family"] == "OPCIONES")
                         ["change_log"], "REVIEW_LOG_ENTRY_PRESENT")

    def test_a661_future_clock_is_integrity_not_clean_review_or_autoapproval(self):
        with sqlite3.connect(self.db) as c:
            self.option_review(c)
            self.v2(c, previous={"lot": 100, "provider_timestamp": "yesterday"},
                    current={"lot": 100, "provider_timestamp": "today"},
                    event="2039-01-01T00:00:00+00:00")
        result = self.probe()
        self.assertEqual(self.roots(result), {("EVIDENCE_INTEGRITY_HOLD", "PPI_ONLY"): 1})
        self.assertNotIn("PENDING_MATERIAL", json.dumps(result["option_review_root_hist"]))

    def test_a661_revoked_and_adjusted_independent_of_append_only_log(self):
        with sqlite3.connect(self.db) as c:
            self.option_review(c)
            self.v2(c, current={"lot": 100, "revoked": True,
                                "adjusted_series_unverified": True})
        result = self.probe()
        self.assertEqual(self.roots(result),
                         {("ADJUSTED_SERIES_UNVERIFIED+REVOKED", "PPI_ONLY"): 1})

    def test_a661_pending_iol_material_with_primary_ppi_and_revoked_ppi_is_mixed(self):
        with sqlite3.connect(self.db) as c:
            self.option_review(c)
            self.v2(c, current={"lot": 100, "revoked": True})
            self.v2(c, source="IOL_STRUCTURED_API", previous={"lot": 10}, current={"lot": 20},
                    prev_id=40, current_id=41, change_id=101)
        result = self.probe()
        self.assertEqual(self.roots(result),
                         {("PENDING_MATERIAL+REVOKED", "MIXED_SOURCES"): 1})

    def test_a661_iol_only_pending_material_can_block_exact_ppi_identity(self):
        with sqlite3.connect(self.db) as c:
            self.option_review(c)
            self.v2(c, current={"lot": 100})
            self.v2(c, source="IOL_STRUCTURED_API", previous={"lot": 10}, current={"lot": 20},
                    prev_id=40, current_id=41, change_id=101)
        result = self.probe()
        option = next(item for item in result["joint"] if item["family"] == "OPCIONES")
        self.assertEqual(option["current"], "EXACT_PPI_SOURCE_LABEL")
        self.assertEqual(self.roots(result), {("PENDING_MATERIAL", "IOL_ONLY"): 1})

    def test_a661_ignored_iol_current_revoked_flag_alone_does_not_block_ppi(self):
        with sqlite3.connect(self.db) as c:
            self.option_review(c)
            self.v2(c, current={"lot": 100})
            self.v2(c, source="IOL_STRUCTURED_API", current={"lot": 20, "revoked": True},
                    current_id=41)
        result = self.probe()
        self.assertEqual(self.roots(result),
                         {("UNEXPLAINED_PERSISTED_BRIDGE", "NONE"): 1})

    def test_a661_derived_official_pending_is_distinct_from_iol_and_ppi(self):
        with sqlite3.connect(self.db) as c:
            self.option_review(c)
            self.v2(c, current={"lot": 100})
            self.v2(c, source="DERIVED_OFFICIAL_RULE", previous={"lot": 10},
                    current={"lot": 20}, prev_id=40, current_id=41, change_id=101)
        result = self.probe()
        self.assertEqual(self.roots(result),
                         {("PENDING_MATERIAL", "DERIVED_OFFICIAL_ONLY"): 1})

    def test_a661_cross_market_change_never_marks_primary_option_key(self):
        with sqlite3.connect(self.db) as c:
            self.option_review(c)
            self.v2(c, current={"lot": 100})
            self.v2(c, source="IOL_STRUCTURED_API", previous={"lot": 10}, current={"lot": 20},
                    prev_id=40, current_id=41, change_id=101, market="A3")
        result = self.probe()
        self.assertEqual(self.roots(result), {("UNEXPLAINED_PERSISTED_BRIDGE", "NONE"): 1})

    def test_a666_missing_ppi_snapshot_fails_closed_without_field_histogram(self):
        with sqlite3.connect(self.db) as c:
            self.option_review(c)
            self.v2(c, current={"lot": 100})
            c.execute("""INSERT INTO contract_evidence_v2_changes
              (change_id,family,ticker,market,currency,settlement,source_class,
               previous_hash,current_hash,detected_at,status)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (111, "OPCIONES", "XYZ", "BYMA", "ARS", "CI", "PPI_STRUCTURED_API",
               "a" * 64, "b" * 64, STAMP, "CHANGED_REVIEW_REQUIRED"))
        result = self.probe()
        # A661 correctly diagnosed an integrity hold. A666 cannot safely
        # attribute field-level changes with missing prior/current snapshots,
        # and therefore refuses to publish a partial field histogram.
        self.assertEqual(result["state"], "NO_VERIFICADO")
        self.assertEqual(result["reason"], "A666_PPI_MATERIAL_INTEGRITY_HOLD")
        self.assertEqual(result["option_ppi_material_field_hist"], [])

    def test_a661_runner_rejects_forged_private_reason_and_denominator(self):
        with sqlite3.connect(self.db) as c:
            self.option_review(c)
            self.v2(c, previous={"lot": 100}, current={"lot": 200})
        result = {**self.probe(), "private_ro_mount": True}
        self.assertEqual(module.sanitize(result)["state"], "READ_ONLY_OBSERVED")
        result["option_review_root_hist"][0]["flags"] = "PRIVATE_ACCOUNT_NUMBER"
        self.assertEqual(module.sanitize(result)["state"], "NO_VERIFICADO")
        result["option_review_root_hist"][0]["flags"] = "PENDING_MATERIAL"
        result["option_review_root_total"] = 999
        self.assertEqual(module.sanitize(result)["state"], "NO_VERIFICADO")

    def test_a666_exact_ppi_material_field_operations_are_aggregate_only(self):
        with sqlite3.connect(self.db) as c:
            self.option_review(c)
            self.v2(c, previous={
                "ppi_identity_verified": False,
                "price_quote_unit": "100",
                "provider_catalog_status": "AVAILABLE",
                "provider_timestamp": "SECRET_OLD_CLOCK",
            }, current={
                "ppi_identity_verified": True,
                "ppi_nominal_in_price": "1",
                "provider_catalog_status": "AVAILABLE",
                "provider_timestamp": "SECRET_NEW_CLOCK",
            })
        raw = self.probe()
        self.assertEqual(raw["option_review_root_hist"], [
            {"flags": "PENDING_MATERIAL", "source_class_bucket": "PPI_ONLY", "count": 1}])
        self.assertEqual(raw["option_ppi_material_identity_total"], 1)
        self.assertEqual(raw["option_ppi_material_change_total"], 1)
        hist = {(r["field"], r["operation"]): (r["identities"], r["events"])
                for r in raw["option_ppi_material_field_hist"]}
        self.assertEqual(hist, {
            ("PPI_IDENTITY_VERIFIED", "CHANGED"): (1, 1),
            ("PRICE_QUOTE_UNIT", "REMOVED"): (1, 1),
            ("PPI_NOMINAL_IN_PRICE", "ADDED"): (1, 1),
        })
        self.assertEqual(raw["option_ppi_material_current_hist"],
                         [{"category": "LATEST_CURRENT", "count": 1}])
        self.assertEqual(raw["option_ppi_material_source_ref_hist"],
                         [{"category": "STABLE_OTHER_PPI_REF", "count": 1}])
        published = module.sanitize({**raw, "private_ro_mount": True})
        self.assertEqual(published["state"], "READ_ONLY_OBSERVED")
        receipt = json.dumps(published)
        for forbidden in ("XYZ", "SECRET_OLD_CLOCK", "SECRET_NEW_CLOCK", "100", "AVAILABLE"):
            # Neither private payload VALUES nor raw option identifiers may
            # appear in these explicit categorical diagnostics.
            self.assertNotIn('"' + forbidden + '"', receipt)

    def test_a666_private_or_unknown_json_field_name_is_never_exported(self):
        with sqlite3.connect(self.db) as c:
            self.option_review(c)
            self.v2(c, previous={
                "price_quote_unit": "10", "secret_api_key_private_ticker": "BEFORE_PRIVATE"
            }, current={
                "price_quote_unit": "20", "secret_api_key_private_ticker": "AFTER_PRIVATE"
            })
        receipt = module.sanitize({**self.probe(), "private_ro_mount": True})
        self.assertEqual(receipt["state"], "READ_ONLY_OBSERVED")
        field_hist = receipt["option_ppi_material_field_hist"]
        self.assertIn({"field": "UNKNOWN_FIELD", "operation": "CHANGED",
                       "identities": 1, "events": 1}, field_hist)
        printable = json.dumps(receipt)
        for secret in ("secret_api_key_private_ticker", "BEFORE_PRIVATE", "AFTER_PRIVATE"):
            self.assertNotIn(secret, printable)

    def test_a666_history_not_latest_is_not_silent_current_attestation(self):
        with sqlite3.connect(self.db) as c:
            self.option_review(c)
            self.v2(c, previous={"price_quote_unit": "100"},
                    current={"price_quote_unit": "10"})
            latest = {"price_quote_unit": "1"}
            encoded = json.dumps(latest, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            c.execute("""INSERT INTO contract_evidence_v2_snapshots
              (snapshot_id,family,ticker,market,currency,settlement,source_class,
               evidence_hash,observed_at,evidence_json,source_ref)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (50, "OPCIONES", "XYZ", "BYMA", "ARS", "CI", "PPI_STRUCTURED_API",
               digest, STAMP, encoded, "synthetic:fixture"))
            c.execute("""UPDATE contract_evidence_v2_current SET snapshot_id=?,evidence_hash=?
              WHERE family='OPCIONES' AND ticker='XYZ' AND source_class='PPI_STRUCTURED_API'""",
              (50, digest))
        raw = self.probe()
        self.assertEqual(raw["option_ppi_material_current_hist"],
                         [{"category": "HISTORICAL_NOT_LATEST", "count": 1}])
        self.assertEqual(raw["option_ppi_material_identity_total"], 1)

    def test_a666_corrupt_historical_previous_payload_fails_closed(self):
        with sqlite3.connect(self.db) as c:
            self.option_review(c)
            self.v2(c, previous={"price_quote_unit": "100"},
                    current={"price_quote_unit": "10"})
            c.execute("UPDATE contract_evidence_v2_snapshots SET evidence_json=? WHERE snapshot_id=30",
                      ('{"private":"TAMPERED"}',))
        raw = self.probe()
        self.assertEqual(raw["state"], "NO_VERIFICADO")
        self.assertIn("A666", raw["reason"])
        self.assertEqual(raw["option_ppi_material_field_hist"], [])

    def test_a666_sanitizer_rejects_forged_field_name_or_histogram_denominator(self):
        with sqlite3.connect(self.db) as c:
            self.option_review(c)
            self.v2(c, previous={"price_quote_unit": "100"},
                    current={"price_quote_unit": "10"})
        source = {**self.probe(), "private_ro_mount": True}
        self.assertEqual(module.sanitize(source)["state"], "READ_ONLY_OBSERVED")
        forged = json.loads(json.dumps(source))
        forged["option_ppi_material_field_hist"][0]["field"] = "SECRET_TICKER_KEY"
        self.assertEqual(module.sanitize(forged)["state"], "NO_VERIFICADO")
        forged = json.loads(json.dumps(source))
        forged["option_ppi_material_identity_total"] = 568
        self.assertEqual(module.sanitize(forged)["state"], "NO_VERIFICADO")

    @staticmethod
    def ppi_option_metadata(raw_nominal="1", *, include_settlement=True, discovery="PPI_PRIMARY"):
        value = dict(_discovery_source=discovery, ticker="XYZ", type="OPCIONES",
                     market="BYMA", currency="ARS", nominalInPrice=raw_nominal,
                     _contract_bridge={"status": "BLOCKED", "gaps": ["CHANGE_REVIEW_REQUIRED"]})
        if include_settlement:
            value["settlement"] = "CI"
        return json.dumps(value)

    def option_pair(self, conn, *, current=None, raw_nominal="1", settlement_source="PPI_FIELD",
                    include_settlement=True, discovery="PPI_PRIMARY"):
        self.option_review(conn)
        conn.execute("""UPDATE financial_instrument_catalog SET settlement_source=?, metadata_json=?
          WHERE ticker='XYZ' AND instrument_type='OPCIONES'""",
          (settlement_source, self.ppi_option_metadata(
              raw_nominal, include_settlement=include_settlement, discovery=discovery)))
        self.v2(conn, previous={"ppi_identity_verified": True},
                current=current or {"ppi_identity_verified": True,
                                   "ppi_nominal_in_price": "1", "price_quote_unit": "1"})

    def test_a667_verified_pair_and_literal_ppi_five_key_still_not_paper_ready(self):
        with sqlite3.connect(self.db) as c:
            self.option_pair(c)
        raw = self.probe()
        self.assertEqual(raw["option_ppi_quote_consistency_total"], 1)
        self.assertEqual(raw["option_ppi_quote_consistency_hist"], [
            {"pair": "PAIR_POSITIVE_EQUAL", "ppi_key": "PPI_PROVIDER_FIVE_KEY",
             "raw_nominal": "RAW_NOMINAL_MATCH", "history": "NO_RECORDED_REMOVAL",
             "count": 1}])
        safe = module.sanitize({**raw, "private_ro_mount": True})
        self.assertEqual(safe["state"], "READ_ONLY_OBSERVED")
        self.assertFalse(safe["paper_admission_certified"])
        self.assertNotIn("XYZ", json.dumps(safe))

    def test_a667_request_side_or_missing_provider_settlement_is_unattested(self):
        for kwargs in ({"settlement_source": "REQUEST_CANDIDATE"},
                       {"include_settlement": False},
                       {"discovery": "IOL_COMPLEMENTARY"}):
            with self.subTest(kwargs=kwargs):
                with sqlite3.connect(self.db) as c:
                    c.execute("DELETE FROM contract_evidence_v2_changes")
                    c.execute("DELETE FROM contract_evidence_v2_current")
                    c.execute("DELETE FROM contract_evidence_v2_snapshots")
                    self.option_pair(c, **kwargs)
                raw = self.probe()
                item = raw["option_ppi_quote_consistency_hist"][0]
                self.assertEqual(item["pair"], "PAIR_POSITIVE_EQUAL")
                self.assertEqual(item["ppi_key"], "PPI_FIVE_KEY_UNATTESTED")
                self.assertEqual(item["raw_nominal"], "RAW_NOMINAL_MATCH")

    def test_a667_mismatched_raw_nominal_or_pair_cannot_be_reported_consistent(self):
        for current, raw_nominal, pair, raw_expected in (
            ({"price_quote_unit": "1", "ppi_nominal_in_price": "2"}, "1",
             "PAIR_UNEQUAL", "RAW_NOMINAL_MISMATCH"),
            ({"price_quote_unit": "NaN", "ppi_nominal_in_price": "1"}, "1",
             "PAIR_INVALID", "RAW_NOMINAL_UNVERIFIABLE"),
            ({"price_quote_unit": "1"}, "1",
             "PAIR_MISSING", "RAW_NOMINAL_UNVERIFIABLE"),
            ({"price_quote_unit": "1", "ppi_nominal_in_price": "1"}, "10",
             "PAIR_POSITIVE_EQUAL", "RAW_NOMINAL_MISMATCH"),
        ):
            with self.subTest(pair=pair, raw=raw_expected):
                with sqlite3.connect(self.db) as c:
                    c.execute("DELETE FROM contract_evidence_v2_changes")
                    c.execute("DELETE FROM contract_evidence_v2_current")
                    c.execute("DELETE FROM contract_evidence_v2_snapshots")
                    self.option_pair(c, current=current, raw_nominal=raw_nominal)
                item = self.probe()["option_ppi_quote_consistency_hist"][0]
                self.assertEqual((item["pair"], item["raw_nominal"]), (pair, raw_expected))

    def test_a667_provider_five_key_collision_or_invalid_basis_remains_unattested(self):
        with sqlite3.connect(self.db) as c:
            self.option_pair(c, raw_nominal="1")
            meta = json.loads(c.execute("""SELECT metadata_json FROM financial_instrument_catalog
              WHERE instrument_type='OPCIONES' AND ticker='XYZ'""").fetchone()[0])
            meta["ticker"] = "SENSITIVE_DIFFERENT_SYMBOL"
            c.execute("""UPDATE financial_instrument_catalog SET metadata_json=?
              WHERE instrument_type='OPCIONES' AND ticker='XYZ'""", (json.dumps(meta),))
        receipt = module.sanitize({**self.probe(), "private_ro_mount": True})
        self.assertEqual(receipt["state"], "READ_ONLY_OBSERVED")
        self.assertEqual(receipt["option_ppi_quote_consistency_hist"][0]["ppi_key"],
                         "PPI_FIVE_KEY_UNATTESTED")
        self.assertNotIn("SENSITIVE_DIFFERENT_SYMBOL", json.dumps(receipt))

    def test_a667_conflicting_ppi_source_current_never_picks_first_arbitrarily(self):
        with sqlite3.connect(self.db) as c:
            self.option_pair(c)
            payload = {"ppi_nominal_in_price": "100", "price_quote_unit": "100"}
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(raw.encode()).hexdigest()
            c.execute("""INSERT INTO contract_evidence_v2_snapshots
              (snapshot_id,family,ticker,market,currency,settlement,source_class,evidence_hash,
               observed_at,evidence_json,source_ref) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (60, "OPCIONES", "XYZ", "BYMA", "ARS", "CI", "PPI_AUTHENTICATED_XHR",
               digest, STAMP, raw, "synthetic:fixture"))
            c.execute("INSERT INTO contract_evidence_v2_current VALUES(?,?,?,?,?,?,?,?,?)",
                      ("OPCIONES", "XYZ", "BYMA", "ARS", "CI", "PPI_AUTHENTICATED_XHR",
                       60, digest, STAMP))
        receipt = module.sanitize({**self.probe(), "private_ro_mount": True})
        self.assertEqual(receipt["state"], "READ_ONLY_OBSERVED")
        self.assertEqual(receipt["option_ppi_quote_consistency_hist"][0]["pair"],
                         "PPI_CURRENT_NOT_UNIQUE")
        self.assertFalse(receipt["paper_admission_certified"])

    def test_a667_historical_removal_separate_from_current_pair(self):
        with sqlite3.connect(self.db) as c:
            self.option_pair(c, current={"ppi_identity_verified": True})
            # The older transition removed both quote-basis fields.
            old = {"ppi_identity_verified": True, "price_quote_unit": "1",
                   "ppi_nominal_in_price": "1"}
            raw_old = json.dumps(old, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(raw_old.encode()).hexdigest()
            c.execute("""INSERT INTO contract_evidence_v2_snapshots
              (snapshot_id,family,ticker,market,currency,settlement,source_class,
               evidence_hash,observed_at,evidence_json,source_ref)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (41, "OPCIONES", "XYZ", "BYMA", "ARS", "CI", "PPI_STRUCTURED_API",
               digest, STAMP, raw_old, "synthetic:fixture"))
            c.execute("""INSERT INTO contract_evidence_v2_changes
              (change_id,family,ticker,market,currency,settlement,source_class,
               previous_hash,current_hash,detected_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (102, "OPCIONES", "XYZ", "BYMA", "ARS", "CI", "PPI_STRUCTURED_API",
               digest, c.execute("SELECT evidence_hash FROM contract_evidence_v2_snapshots WHERE snapshot_id=31").fetchone()[0],
               STAMP, "CHANGED_REVIEW_REQUIRED"))
        item = self.probe()["option_ppi_quote_consistency_hist"][0]
        self.assertEqual(item["history"], "RECORDED_PRIOR_REMOVAL")
        self.assertEqual(item["pair"], "PAIR_MISSING")

    def test_a667_sanitizer_blocks_unknown_status_and_false_denominator(self):
        with sqlite3.connect(self.db) as c:
            self.option_pair(c)
        raw = {**self.probe(), "private_ro_mount": True}
        self.assertEqual(module.sanitize(raw)["state"], "READ_ONLY_OBSERVED")
        forged = json.loads(json.dumps(raw))
        forged["option_ppi_quote_consistency_hist"][0]["ppi_key"] = "secret_ticker"
        self.assertEqual(module.sanitize(forged)["state"], "NO_VERIFICADO")
        forged = json.loads(json.dumps(raw))
        forged["option_ppi_quote_consistency_total"] = 568
        self.assertEqual(module.sanitize(forged)["state"], "NO_VERIFICADO")

    def test_a668_request_settlement_and_stored_absence_not_provider_absence(self):
        with sqlite3.connect(self.db) as c:
            self.option_pair(c, settlement_source="REQUEST_CANDIDATE",
                             include_settlement=False)
        raw = self.probe()
        self.assertEqual(raw["option_ppi_key_root_total"], 1)
        self.assertEqual(raw["option_ppi_key_root_hist"], [
            {"category": "REQUEST_SIDE_SETTLEMENT", "count": 1}])
        self.assertEqual(raw["option_ppi_key_source_hist"], [
            {"settlement_source": "REQUEST_CANDIDATE", "discovery": "PPI_PRIMARY",
             "metadata_shape": "FLAT_ONLY", "count": 1}])
        self.assertIn({"field": "SETTLEMENT", "status": "STORED_LITERAL_MISSING", "count": 1},
                      raw["option_ppi_key_field_hist"])
        self.assertNotIn("PROVIDER_MISSING", json.dumps(raw))
        safe = module.sanitize({**raw, "private_ro_mount": True})
        self.assertEqual(safe["state"], "READ_ONLY_OBSERVED")
        self.assertFalse(safe["paper_admission_certified"])

    def test_a668_raw_alias_is_distinguished_from_missing_or_mismatch(self):
        with sqlite3.connect(self.db) as c:
            self.option_pair(c)
            raw = json.loads(c.execute("""SELECT metadata_json FROM financial_instrument_catalog
              WHERE instrument_type='OPCIONES' AND ticker='XYZ'""").fetchone()[0])
            raw.update(type="OPCION", market="BCBA", currency="PESOS", settlement="T0")
            c.execute("""UPDATE financial_instrument_catalog SET metadata_json=?
              WHERE instrument_type='OPCIONES' AND ticker='XYZ'""", (json.dumps(raw),))
        result = self.probe()
        self.assertEqual(result["option_ppi_key_root_hist"], [
            {"category": "ATTESTED_PROVIDER_FIVE_KEY_IN_METADATA", "count": 1}])
        self.assertEqual(result["option_ppi_quote_consistency_hist"][0]["ppi_key"],
                         "PPI_PROVIDER_FIVE_KEY")
        aliases = {row["field"] for row in result["option_ppi_key_field_hist"]
                   if row["status"] == "STORED_ALIAS_EQUIVALENT"}
        self.assertEqual(aliases, {"TYPE", "MARKET", "CURRENCY", "SETTLEMENT"})

    def test_a668_stored_mismatched_fields_no_private_raw_leak(self):
        with sqlite3.connect(self.db) as c:
            self.option_pair(c)
            raw = json.loads(c.execute("""SELECT metadata_json FROM financial_instrument_catalog
              WHERE instrument_type='OPCIONES' AND ticker='XYZ'""").fetchone()[0])
            raw.update(ticker="PRIVATE_ACCOUNT_XYZ42", market="A3")
            c.execute("""UPDATE financial_instrument_catalog SET metadata_json=?
              WHERE instrument_type='OPCIONES' AND ticker='XYZ'""", (json.dumps(raw),))
        receipt = module.sanitize({**self.probe(), "private_ro_mount": True})
        self.assertEqual(receipt["state"], "READ_ONLY_OBSERVED")
        self.assertEqual(receipt["option_ppi_key_root_hist"], [
            {"category": "STORED_FIELD_MISMATCH", "count": 1}])
        for field in ("TICKER", "MARKET"):
            self.assertIn({"field": field, "status": "STORED_LITERAL_MISMATCH", "count": 1},
                          receipt["option_ppi_key_field_hist"])
        self.assertNotIn("PRIVATE_ACCOUNT_XYZ42", json.dumps(receipt))

    def test_a668_nested_metadata_is_not_automatically_provider_attested(self):
        with sqlite3.connect(self.db) as c:
            self.option_pair(c)
            value = {"_discovery_source": "PPI_PRIMARY", "nominalInPrice": "1",
                     "_contract_bridge": {"status": "BLOCKED", "gaps": ["CHANGE_REVIEW_REQUIRED"]},
                     "raw": json.loads(self.ppi_option_metadata())}
            c.execute("""UPDATE financial_instrument_catalog SET metadata_json=?
              WHERE instrument_type='OPCIONES' AND ticker='XYZ'""", (json.dumps(value),))
        raw = self.probe()
        self.assertEqual(raw["option_ppi_key_source_hist"][0]["metadata_shape"], "NESTED_ONLY")
        self.assertEqual(raw["option_ppi_key_root_hist"], [
            {"category": "STORED_FIELD_MISSING", "count": 1}])
        self.assertEqual(raw["option_ppi_quote_consistency_hist"][0]["ppi_key"],
                         "PPI_FIVE_KEY_UNATTESTED")

    def test_a668_sanitizer_rejects_forged_key_root_and_field_denominator(self):
        with sqlite3.connect(self.db) as c:
            self.option_pair(c, settlement_source="REQUEST_CANDIDATE")
        raw = {**self.probe(), "private_ro_mount": True}
        self.assertEqual(module.sanitize(raw)["state"], "READ_ONLY_OBSERVED")
        forged = json.loads(json.dumps(raw))
        forged["option_ppi_key_root_hist"][0]["category"] = "PRIVATE_TICKER"
        self.assertEqual(module.sanitize(forged)["state"], "NO_VERIFICADO")
        forged = json.loads(json.dumps(raw))
        forged["option_ppi_key_field_hist"][0]["count"] = 568
        self.assertEqual(module.sanitize(forged)["state"], "NO_VERIFICADO")
        forged = json.loads(json.dumps(raw))
        forged["option_ppi_key_root_total"] = 568
        self.assertEqual(module.sanitize(forged)["state"], "NO_VERIFICADO")


if __name__ == "__main__":
    unittest.main()
