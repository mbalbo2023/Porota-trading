"""A652 EOD receipt OFFHOST; synthetic SQLite, zero provider or host access."""

import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rc6_a652_catalog_eod_readonly.py"
SPEC = importlib.util.spec_from_file_location("a652_eod_readonly", SCRIPT)
eod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(eod)
EVENT_AT = "2026-11-04T18:08:15-03:00"
MARKER_AT = "2026-11-04T18:00:01-03:00"
DETAIL = ("promoted_this_run=1; inserted_this_run=0; refreshed_this_run=2; "
          "ready_total=1; pending_total=2; pending_contract_retry=2; "
          'ready_by_family={"OPCIONES":1}; sources={"SECRET_UNTRUSTED":2}; '
          "precedence=PPI>IOL>BYMA; real_orders=blocked")


def fixture(db):
    with sqlite3.connect(db) as c:
        c.executescript("""
          CREATE TABLE paper_workspace(id INTEGER,namespace TEXT,origin TEXT,
            imported_legacy INTEGER,dataset_id TEXT);
          CREATE TABLE observer_state(id INTEGER,real_orders_sent INTEGER,detail TEXT);
          CREATE TABLE paper_events(id INTEGER PRIMARY KEY,event_at TEXT,event_type TEXT,detail TEXT);
          CREATE TABLE candidate_refresh_state(id INTEGER,state TEXT,checked_at TEXT);
          CREATE TABLE financial_instrument_catalog(instrument_type TEXT,status TEXT,
            capability TEXT,metadata_json TEXT);
        """)
        c.execute("INSERT INTO paper_workspace VALUES(1,?,?,0,?)",
                  ("POROTA_PAPER_V17_FRESH", "FRESH_EMPTY", str(uuid.uuid4())))
        c.execute("INSERT INTO observer_state VALUES(1,0,?)",
                  ("CAUCION_LEDGER_INVALID: INTERNAL_PRIVATE_ACCOUNT",))
        c.execute("INSERT INTO paper_events(event_at,event_type,detail) VALUES(?,?,?)",
                  (EVENT_AT, eod.EVENT, DETAIL))
        c.execute("INSERT INTO candidate_refresh_state VALUES(1,'COMPLETE',?)", (MARKER_AT,))
        c.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?)", [
            ("OPCIONES", "AVAILABLE", "READY_PAPER_OPTION",
             json.dumps({"financial_contract_v17": {"SECRET_ECONOMIC_TERMS": "PRIVATE"}})),
            ("OPCIONES", "PENDING", "NEEDS_CONTRACT", "{}"),
            ("BONOS", "PENDING", "NEEDS_IDENTITY", "{}"),
        ])


class A652CatalogEODReadOnlyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="a652-eod-offhost-")
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "observer_v17.db"
        fixture(self.db)

    def query(self, statement, args=()):
        with sqlite3.connect(self.db) as c:
            c.execute(statement, args)

    def read(self):
        return eod.diagnose(self.db, enforce_ro_mount=False)

    def test_postclose_complete_aggregate_observed_not_paper_go(self):
        report = self.read()
        self.assertEqual(report["state"], "READ_ONLY_OBSERVED")
        # Code contract: RUNNING checked_at starts *before* EOD event, and
        # COMPLETE updates state without overwriting that earlier timestamp.
        self.assertLess(report["projection"]["local_at"], report["event"]["local_at"])
        self.assertEqual(report["event"]["promoted_this_run"], 1)
        self.assertEqual(report["event"]["ready_by_family"], {"OPCIONES": 1})
        self.assertEqual(report["catalog"]["totals"],
                         {"total": 3, "ready": 1, "v17_declared": 1})
        self.assertEqual(report["caucion_hold_latest"], "OBSERVED")
        self.assertFalse(report["paper_admission_certified"])
        serialized = json.dumps(eod.sanitize({**report, "private_key": "SECRET"}))
        for forbidden in ("SECRET", "PRIVATE", "ACCOUNT", "observer_v17.db"):
            self.assertNotIn(forbidden, serialized)

    def test_preclose_event_is_not_eod_receipt(self):
        self.query("UPDATE paper_events SET event_at='2026-11-04T17:46:00-03:00'")
        self.assertEqual(self.read()["state"], "NO_VERIFICADO")

    def test_missing_or_unverified_event_is_not_success(self):
        self.query("DELETE FROM paper_events")
        self.assertEqual(self.read()["event"]["state"], "NO_VERIFICADO")
        self.assertEqual(self.read()["state"], "NO_VERIFICADO")

    def test_marker_running_or_failed_is_not_complete(self):
        for state in ("RUNNING", "FAILED"):
            self.query("UPDATE candidate_refresh_state SET state=?", (state,))
            self.assertEqual(self.read()["state"], "NO_VERIFICADO")

    def test_stale_complete_marker_cannot_attest_today_event(self):
        self.query("UPDATE candidate_refresh_state SET checked_at='2026-11-03T18:10:00-03:00'")
        report = self.read()
        self.assertEqual(report["state"], "NO_VERIFICADO")
        self.assertEqual(report["reason"], "PROJECTION_MARKER_NOT_EOD")
        report["private_ro_mount"] = True
        self.assertEqual(eod.sanitize(report)["state"], "NO_VERIFICADO")

    def test_preclose_marker_cannot_attest_postclose_event(self):
        self.query("UPDATE candidate_refresh_state SET checked_at='2026-11-04T17:59:59-03:00'")
        report = self.read()
        self.assertEqual(report["state"], "NO_VERIFICADO")
        self.assertEqual(report["reason"], "PROJECTION_MARKER_NOT_EOD")

    def test_marker_after_event_is_not_same_publication(self):
        self.query("UPDATE candidate_refresh_state SET checked_at='2026-11-04T18:08:16-03:00'")
        report = self.read()
        self.assertEqual(report["state"], "NO_VERIFICADO")
        self.assertEqual(report["reason"], "PROJECTION_MARKER_NOT_EOD")
        report["private_ro_mount"] = True
        self.assertEqual(eod.sanitize(report)["state"], "NO_VERIFICADO")

    def test_nonzero_orders_fails_closed(self):
        self.query("UPDATE observer_state SET real_orders_sent=1")
        report = self.read()
        self.assertEqual(report["state"], "NO_VERIFICADO")
        self.assertEqual(report["reason"], "NONZERO_REAL_ORDERS_HOLD")

    def test_bad_workspace_marker_is_not_host_evidence(self):
        self.query("UPDATE paper_workspace SET namespace='LEGACY'")
        self.assertEqual(self.read()["reason"], "PAPER_WORKSPACE_OR_SCHEMA_HOLD")

    def test_sqlite_ro_mount_required_even_with_uri_mode_ro(self):
        report = eod.diagnose(self.db)
        self.assertEqual(report["state"], "NO_VERIFICADO")
        self.assertEqual(report["reason"], "PRIVATE_SQLITE_RO_MOUNT_REQUIRED")
        self.assertFalse(report["db_opened"])

    def test_event_malformed_or_unknown_family_fails_closed(self):
        self.query("UPDATE paper_events SET detail=?", (DETAIL.replace("OPCIONES", "PRIVATE_SYMBOL"),))
        report = self.read()
        self.assertEqual(report["state"], "NO_VERIFICADO")
        self.assertEqual(report["event"]["reason"], "EOD_EVENT_FAMILY_HOLD")

    def test_runner_sanitizer_refuses_untrusted_host_blob_and_extra_keys(self):
        report = self.read()
        report["symbols"] = ["AL30_PRIVATE"]
        report["path"] = "/run/secrets/ppi"
        sanitized = eod.sanitize(report)
        dumped = json.dumps(sanitized)
        self.assertEqual(sanitized["state"], "NO_VERIFICADO")  # no RO mount asserted
        self.assertNotIn("AL30_PRIVATE", dumped)
        self.assertNotIn("/run/secrets", dumped)
        self.assertEqual(eod.sanitize({"schema": eod.SCHEMA, "token": "SECRET"})["state"],
                         "NO_VERIFICADO")

    def test_strict_sanitizer_only_attests_ro_mount_and_completed_projection(self):
        report = self.read()
        report["private_ro_mount"] = True  # simulate *only* after private RO mount gate
        self.assertEqual(eod.sanitize(report)["state"], "READ_ONLY_OBSERVED")
        report["projection"]["state"] = "FAILED"
        self.assertEqual(eod.sanitize(report)["state"], "NO_VERIFICADO")


if __name__ == "__main__":
    unittest.main()

