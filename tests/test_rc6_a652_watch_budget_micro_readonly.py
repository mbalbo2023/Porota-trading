"""Offline A652 regressions: fail closed, no secrets, no remote execution."""

from __future__ import annotations

from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import rc6_a652_watch_budget_micro_readonly as a652
from scripts import rc6_a617_ppi_origin_selector_readonly as a617
from scripts import rc6_a516_host_budget_metadata_ro as a516


def fake_systemctl(args, **kwargs):
    if "list-unit-files" in args:
        output = "ppi-watch.service enabled enabled\nrc6-market-watch.service enabled enabled\n"
    else:
        output = "ppi-watch.service loaded active running example\n"
    return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")


def fixture_db(path):
    with closing(sqlite3.connect(path)) as c:
        c.executescript("""
            CREATE TABLE paper_workspace(
                id INTEGER PRIMARY KEY,namespace TEXT,origin TEXT,
                imported_legacy INTEGER,dataset_id TEXT);
            CREATE TABLE observer_state(
                id INTEGER PRIMARY KEY, mode TEXT, process_state TEXT,
                session_state TEXT,ppi_auth TEXT,http_allowed INTEGER,
                http_blocked INTEGER,real_orders_sent INTEGER);
            CREATE TABLE universe_cycle_metrics(
                id INTEGER PRIMARY KEY,started_at TEXT,finished_at TEXT,
                eligible_total INTEGER,selected_count INTEGER,
                successful_count INTEGER,failed_count INTEGER,detail TEXT);
            CREATE TABLE paper_events(
                id INTEGER PRIMARY KEY,event_at TEXT,event_type TEXT,detail TEXT);
        """)
        c.execute("INSERT INTO paper_workspace VALUES(1,?,?,?,?)", (
            "POROTA_PAPER_V17_FRESH", "FRESH_EMPTY", 0, str(uuid.uuid4())))
        c.execute("INSERT INTO observer_state VALUES(1,?,?,?,?,?,?,?)", (
            "PRODUCTION_PAPER", "DEGRADED", "MARKET_OPEN", "OK", 8413, 0, 0))
        c.execute("INSERT INTO universe_cycle_metrics VALUES(1,?,?,?,?,?,?,?)", (
            "2026-11-04T19:30:00+00:00", "2026-11-04T19:35:00+00:00",
            8821, 50, 0, 32, "Límite=60; foco; factibilidad={\"active_plan\":{\"cold\":[]}}"))
        for detail in ("secret-symbol: PPI_BUDGET_BACKPRESSURE",
                       "secret-symbol: PPI_HTTP_429",
                       "secret-symbol: PPI_HTTP_503",
                       "private_symbol: PPI_ACCESSKEY_SECRETXYZ",
                       "private_symbol: NOT_A_PPI_REASON"):
            c.execute("INSERT INTO paper_events(event_at,event_type,detail) VALUES(?,?,?)", (
                "2026-11-04T19:31:00+00:00", "DATA_ERROR", detail))
        c.commit()


class TestA652MicroReadOnly(unittest.TestCase):
    def test_pytest_suite_short_alias_collision_keeps_namespaced_guards(self):
        """RCA CI: unrelated tests may load a second A617 short module alias."""
        shadow_a617 = types.ModuleType("rc6_a617_ppi_origin_selector_readonly")
        shadow_a617._mount_is_readonly = lambda _: False
        shadow_a516 = types.ModuleType("rc6_a516_host_budget_metadata_ro")
        shadow_a516.sidecar_kind = lambda _: "UNSAFE_OR_ALIAS"
        with patch.dict(sys.modules, {
                "rc6_a617_ppi_origin_selector_readonly": shadow_a617,
                "rc6_a516_host_budget_metadata_ro": shadow_a516}):
            self.assertIs(a652._a617(), a617)
            self.assertIs(a652._a516(), a516)
            with tempfile.TemporaryDirectory() as directory:
                db = Path(directory) / "observer.db"
                fixture_db(db)
                with patch.object(a617, "_mount_is_readonly", return_value=True):
                    result = a652.host_probe(db=db, runner=fake_systemctl)
                self.assertEqual(result["scanner"]["state"], "OBSERVED")
                self.assertEqual(result["budget"]["sidecar_kind"], "ABSENT")

    def test_watch_enumeration_is_unregistered_even_if_active(self):
        report = a652._watch_probe(fake_systemctl)
        self.assertEqual(report["state"], "ENUMERATED_UNREGISTERED_CANDIDATE")
        self.assertEqual(report["candidate_units"], 1)
        self.assertEqual(report["active_candidates"], 1)
        self.assertEqual(report["identity"], "NO_VERIFICADO")
        self.assertNotIn("ppi-watch.service", json.dumps(report))

    def test_systemctl_failure_is_unknown_not_absent(self):
        def unavailable(args, **kwargs):
            raise subprocess.TimeoutExpired(args, 3)
        self.assertEqual(a652._watch_probe(unavailable)["state"], "ENUMERATION_UNAVAILABLE")

    def test_multiple_watch_candidates_are_ambiguous(self):
        def multiple(args, **kwargs):
            if "list-unit-files" in args:
                output = "ppi-watch.service enabled\nppi_api_watch.service enabled\n"
            else:
                output = "ppi-watch.service loaded active running desc\n"
            return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")
        report = a652._watch_probe(multiple)
        self.assertEqual(report["state"], "ENUMERATED_AMBIGUOUS")
        self.assertEqual(report["candidate_units"], 2)

    def test_safe_error_labels_never_echo_symbol_or_secret_class(self):
        self.assertEqual(a652._error_bucket("AA: PPI_HTTP_403"), "PPI_HTTP_4XX")
        self.assertEqual(a652._error_bucket("AA: PPI_HTTP_503"), "PPI_HTTP_5XX")
        self.assertEqual(a652._error_bucket("AA: PPI_ACCESSKEY_SECRETXYZ"), "PPI_OTHER_CLASSIFIED")
        self.assertEqual(a652._error_bucket("AA: x/tokens/here"), "OTHER")

    def test_host_reports_last_cycle_reasons_only_and_no_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            fixture_db(db)
            with patch.object(a617, "_mount_is_readonly", return_value=True):
                report = a652.host_probe(db=db, runner=fake_systemctl)
            self.assertEqual(report["scanner"]["state"], "OBSERVED")
            self.assertEqual(report["scanner"]["observer"]["process_state"], "DEGRADED")
            self.assertEqual(report["scanner"]["observer"]["http_allowed_counter"], 8413)
            self.assertEqual(report["budget"]["sidecar_kind"], "ABSENT")
            self.assertEqual(report["budget"]["diagnosis"], "SIDECAR_ABSENT")
            self.assertEqual(report["scanner"]["events"]["counts"]["DATA_ERROR"], 5)
            self.assertEqual(report["scanner"]["events"]["data_error_reasons"]["PPI_HTTP_4XX"], 1)
            self.assertEqual(report["scanner"]["events"]["data_error_reasons"]["PPI_OTHER_CLASSIFIED"], 1)
            raw = json.dumps(report)
            self.assertNotIn("secret-symbol", raw)
            self.assertNotIn("SECRETXYZ", raw)
            self.assertNotIn("/opt/porota-trading", raw)
            self.assertNotIn("ppi-watch.service", raw)

    def test_no_mount_means_no_sqlite_query(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            fixture_db(db)
            with patch.object(a617, "_mount_is_readonly", return_value=False):
                report = a652.host_probe(db=db, runner=fake_systemctl)
            self.assertEqual(report["scanner"]["state"], "NO_VERIFICADO")
            self.assertFalse(report["scanner"]["paper_db_identity_verified"])
            self.assertEqual(report["budget"]["state"], "NO_VERIFICADO")

    def test_invalid_paper_workspace_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            fixture_db(db)
            with closing(sqlite3.connect(db)) as c:
                c.execute("UPDATE paper_workspace SET namespace='FAKE'")
                c.commit()
            with patch.object(a617, "_mount_is_readonly", return_value=True):
                report = a652.host_probe(db=db, runner=fake_systemctl)
            self.assertEqual(report["scanner"]["state"], "NO_VERIFICADO")

    def test_data_error_cannot_mean_order_or_quota(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            fixture_db(db)
            with patch.object(a617, "_mount_is_readonly", return_value=True):
                raw = a652.host_probe(db=db, runner=fake_systemctl)
            receipt = a652.build_receipt(raw,
                run_sha="9e099bc1d2cab1183872475f4e2bf8b13e28730c", run_id="42")
            self.assertEqual(receipt["state"], "DIAGNOSTIC_OBSERVED_NO_PAPER_GO")
            self.assertEqual(receipt["budget"]["diagnosis"], "SIDECAR_ABSENT")
            self.assertEqual(receipt["ppi_watch_identity"], "NO_VERIFICADO")
            self.assertEqual(receipt["ppi_watch_external_quota_usage"], "NO_VERIFICADO")
            self.assertFalse(receipt["paper_ready"])
            self.assertEqual(receipt["probe_real_orders"], 0)
            self.assertLess(len(json.dumps(receipt).encode()), 16384)

    def test_remote_extra_secret_fields_are_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            fixture_db(db)
            with patch.object(a617, "_mount_is_readonly", return_value=True):
                raw = a652.host_probe(db=db, runner=fake_systemctl)
            raw["malicious_secret"] = "POSTGRES_PASSWORD=secret"
            raw["watch"]["ExecStart"] = "/secret/path token=private"
            raw["scanner"]["events"]["data_error_reasons"]["token"] = 1
            sanitized = json.dumps(a652.build_receipt(raw))
            for secret in ("POSTGRES", "token=private", "ExecStart", "/secret/path"):
                self.assertNotIn(secret, sanitized)

    def test_unexpected_budget_state_fails_closed(self):
        receipt = a652.build_receipt({"budget": {"state": "EVIDENCE_ONLY_NO_GO",
                                                   "sidecar_kind": "ABSENT"}})
        self.assertEqual(receipt["budget"]["state"], "NO_VERIFICADO")
        self.assertEqual(receipt["state"], "INCOMPLETE_NO_GO")

    def test_overflow_event_counts_and_cold_staleness_cannot_go(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            fixture_db(db)
            with patch.object(a617, "_mount_is_readonly", return_value=True):
                raw = a652.host_probe(db=db, runner=fake_systemctl)
            raw["scanner"]["events"]["counts"]["DATA_ERROR"] = 999999999
            receipt = a652.build_receipt(raw)
            self.assertEqual(receipt["host"]["state"], "NO_VERIFICADO")
            self.assertFalse(receipt["paper_ready"])

    def test_host_guard_rejects_claimed_paper_order_or_provider_get(self):
        raw = {"schema": a652.HOST_SCHEMA, "read_only": True,
               "provider_gets": 1, "budget_leases": 0, "orders_called": False,
               "ppi_watch_mutations": 0, "host_file_writes": 0}
        self.assertEqual(a652.build_receipt(raw)["host"]["state"], "NO_VERIFICADO")

    def test_bool_forgery_does_not_pass_zero_count_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            fixture_db(db)
            with patch.object(a617, "_mount_is_readonly", return_value=True):
                raw = a652.host_probe(db=db, runner=fake_systemctl)
            raw["provider_gets"] = False  # Python False == 0: strict type required.
            self.assertEqual(a652.build_receipt(raw)["host"]["state"], "NO_VERIFICADO")
            raw["provider_gets"] = 0
            raw["watch"]["candidate_units"] = True
            self.assertEqual(a652.build_receipt(raw)["host"]["state"], "NO_VERIFICADO")

    def test_runner_rejects_duplicate_json_keys_and_redacts(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "untrusted-remote.json"
            output = Path(directory) / "sanitized.json"
            source.write_text('{"schema":"' + a652.HOST_SCHEMA +
                              '","provider_gets":1,"provider_gets":0}', encoding="utf-8")
            rc = a652.main(["--sanitize", str(source), str(output)])
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(rc, 2)
            self.assertEqual(result["state"], "INCOMPLETE_NO_GO")
            self.assertEqual(result["host"]["state"], "NO_VERIFICADO")

    def test_runner_rejects_oversized_raw_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "untrusted-remote.json"
            output = Path(directory) / "sanitized.json"
            source.write_text('X' * 16385, encoding="utf-8")
            rc = a652.main(["--sanitize", str(source), str(output)])
            self.assertEqual(rc, 2)
            self.assertLess(output.stat().st_size, 16384)
            self.assertEqual(json.loads(output.read_text())["state"], "INCOMPLETE_NO_GO")

    def test_transport_does_not_install_write_or_import_product(self):
        result = subprocess.run([sys.executable, "-B",
                                 str(ROOT / "scripts/rc6_a652_watch_budget_micro_readonly.py"),
                                 "--prepare-transport"], check=True,
                                capture_output=True, text=True, timeout=3)
        self.assertIn("a617-in-memory", result.stdout)
        self.assertIn("a516-in-memory", result.stdout)
        self.assertIn("a652-in-memory", result.stdout)
        self.assertNotIn("sudo", result.stdout)
        self.assertNotIn("sudo -n docker", result.stdout)
        compile(result.stdout, "transport-in-memory", "exec")


if __name__ == "__main__":
    unittest.main()
