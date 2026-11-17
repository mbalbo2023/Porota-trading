"""Offline A652 regressions: fail closed, no secrets, no remote execution."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
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


def fixture_auth_health(path, *, detail, checked_at="2026-11-15T23:45:27Z"):
    """Product api_health shape; synthetic detail must never cross host sanitizer."""
    with closing(sqlite3.connect(path)) as c:
        c.execute("""CREATE TABLE api_health(
            component TEXT PRIMARY KEY, state TEXT NOT NULL,
            detail TEXT NOT NULL, checked_at TEXT NOT NULL,
            last_success_at TEXT, source TEXT NOT NULL)""")
        c.execute("INSERT INTO api_health VALUES(?,?,?,?,?,?)", (
            "PPI_PRODUCTION_AUTH", "ROJO", detail, checked_at, None, "ppi_observer"))
        c.commit()


def fixture_candidate_readiness(path, rows, *, refresh_state="COMPLETE"):
    """Mirror the deployed bu_instrument_catalog candidate v2/refresh schemas."""
    with closing(sqlite3.connect(path)) as c:
        c.executescript("""
            CREATE TABLE candidate_identity_v2(
                ticker TEXT NOT NULL,instrument_type TEXT NOT NULL,market TEXT NOT NULL,
                currency TEXT NOT NULL,settlement TEXT NOT NULL,can_simulate INTEGER NOT NULL,
                status TEXT NOT NULL,detail TEXT NOT NULL,checked_at TEXT NOT NULL,
                PRIMARY KEY(ticker,instrument_type,market,currency,settlement));
            CREATE TABLE candidate_refresh_state(
                id INTEGER PRIMARY KEY CHECK(id=1),state TEXT NOT NULL,
                checked_at TEXT NOT NULL);
        """)
        c.execute("INSERT INTO candidate_refresh_state VALUES(1,?,?)", (
            refresh_state, "2026-11-16T23:53:15+00:00"))
        c.executemany("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)", rows)
        c.commit()


class TestA652MicroReadOnly(unittest.TestCase):
    def test_v2_readiness_reasons_per_family_are_aggregate_and_secret_free(self):
        """Catches a silent 0 READY readout or raw ticker/detail disclosure."""
        checked = "2026-11-16T23:53:15+00:00"
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            fixture_db(db)
            fixture_candidate_readiness(db, [
                ("SECRET_A1", "ACCIONES", "BCBA", "ARS", "CI", 0, "STALE",
                 "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED;PPI_FRESHNESS_STALE", checked),
                ("SECRET_A2", "ACCIONES", "BCBA", "ARS", "24HS", 0, "STALE",
                 "CAPABILITY:VERY_PRIVATE_TOKEN;CATALOG_STATUS:PRIVATE_SECRET", checked),
                ("SECRET_C1", "CEDEARS", "BCBA", "ARS", "CI", 1, "AVAILABLE",
                 "READY_PAPER_PRICE", checked),
                ("SECRET_O1", "OPCIONES", "BCBA", "ARS", "CI", 0,
                 "PAUSED_EXPLICIT", "PAUSED_EXPLICIT:OPTION_CONTRACT_UNRESOLVED;"
                 "PPI_FULL_KEY_NOT_PROVIDER_ATTESTED;PPI_FRESHNESS_STALE", checked),
                ("SECRET_X1", "SECRET_FAMILY", "BCBA", "ARS", "CI", 0, "STALE",
                 "PRIVATE_ACCOUNT_ACCESS_TOKEN", checked),
            ])
            original = db.read_bytes()
            with patch.object(a617, "_mount_is_readonly", return_value=True):
                raw = a652.host_probe(db=db, runner=fake_systemctl)
            report = a652.build_receipt(raw)
            self.assertEqual(db.read_bytes(), original)
        found = report["host"]["candidate_readiness"]
        self.assertEqual((found["state"], found["refresh_state"]), ("OBSERVED", "COMPLETE"))
        self.assertEqual((found["total"], found["ready"], found["blocked"]), (5, 1, 4))
        self.assertEqual(found["families"]["ACCIONES"]["ready"], 0)
        self.assertEqual(found["families"]["ACCIONES"]["blocked_reasons"], {
            "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED": 1,
            "PPI_FRESHNESS_STALE": 1,
            "CAPABILITY_NOT_READY": 1,
            "CATALOG_STATUS_BLOCKED": 1,
        })
        self.assertEqual(found["families"]["OPCIONES"]["blocked_reasons"], {
            "PPI_FULL_KEY_NOT_PROVIDER_ATTESTED": 1,
            "PPI_FRESHNESS_STALE": 1,
        })
        self.assertEqual(found["families"]["OTRAS"]["blocked_reasons"], {
            "OTHER_REDACTED": 1,
        })
        self.assertEqual(found["reason_totals"]["PPI_FRESHNESS_STALE"], 2)
        self.assertFalse(found["paper_admission_authority"])
        self.assertFalse(report["paper_ready"])
        self.assertEqual(report["probe_provider_gets"], 0)
        self.assertEqual(report["probe_budget_leases"], 0)
        exposed = json.dumps(raw) + json.dumps(report)
        for private in ("SECRET_A1", "SECRET_C1", "SECRET_FAMILY", "PRIVATE_SECRET",
                        "VERY_PRIVATE_TOKEN", "PRIVATE_ACCOUNT_ACCESS_TOKEN"):
            self.assertNotIn(private, exposed)
        self.assertLess(len(json.dumps(report).encode()), 16384)

    def test_readiness_refresh_running_is_not_a_completed_generation(self):
        """Catches incorrectly certifying a bounded in-progress reproject."""
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            fixture_db(db)
            fixture_candidate_readiness(db, [
                ("SENSITIVE", "BONOS", "BCBA", "ARS", "CI", 0, "STALE",
                 "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED", "2026-11-16T23:53:15Z")
            ], refresh_state="RUNNING")
            with patch.object(a617, "_mount_is_readonly", return_value=True):
                result = a652.build_receipt(a652.host_probe(db=db, runner=fake_systemctl))
        found = result["host"]["candidate_readiness"]
        self.assertEqual(found["state"], "OBSERVED")
        self.assertEqual(found["refresh_state"], "RUNNING")
        self.assertFalse(found["generation_complete"])
        self.assertFalse(found["paper_admission_authority"])

    def test_readiness_absent_table_fails_closed_without_invalidating_scanner(self):
        """Catches treating a missing candidate v2 projection as zero READY."""
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            fixture_db(db)
            with patch.object(a617, "_mount_is_readonly", return_value=True):
                result = a652.build_receipt(a652.host_probe(db=db, runner=fake_systemctl))
        self.assertEqual(result["host"]["state"], "OBSERVED")
        self.assertEqual(result["host"]["candidate_readiness"], {
            "state": "NO_VERIFICADO", "reason": "TABLE_ABSENT",
            "paper_admission_authority": False,
        })

    def test_readiness_all_17211_blocked_are_counted_without_raw_rows(self):
        """Catches a partial histogram silently misreported as a full generation."""
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            fixture_db(db)
            fixture_candidate_readiness(db, (
                (f"PRIVATE_{index:05d}", "BONOS", "BCBA", "ARS", "CI", 0, "STALE",
                 "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED", "2026-11-16T23:53:15Z")
                for index in range(17211)
            ))
            with patch.object(a617, "_mount_is_readonly", return_value=True):
                result = a652.build_receipt(a652.host_probe(db=db, runner=fake_systemctl))
        found = result["host"]["candidate_readiness"]
        self.assertEqual((found["total"], found["ready"], found["blocked"]), (17211, 0, 17211))
        self.assertEqual(found["reason_totals"], {
            "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED": 17211,
        })
        self.assertEqual(found["families"]["BONOS"]["total"], 17211)
        self.assertNotIn("PRIVATE_00001", json.dumps(result))
        self.assertLess(len(json.dumps(result).encode()), 16384)

    def test_readiness_row_budget_and_secret_injection_fail_closed(self):
        """Catches truncation falsely deemed complete and secret exfiltration."""
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            fixture_db(db)
            fixture_candidate_readiness(db, (
                (f"PRIVATE_{index:05d}", "BONOS", "BCBA", "ARS", "CI", 0, "STALE",
                 "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED", "2026-11-16T23:53:15Z")
                for index in range(25001)
            ))
            with patch.object(a617, "_mount_is_readonly", return_value=True):
                raw = a652.host_probe(db=db, runner=fake_systemctl)
        result = a652.build_receipt(raw)
        self.assertEqual(result["host"]["candidate_readiness"], {
            "state": "NO_VERIFICADO", "reason": "ROW_LIMIT_HOLD",
            "paper_admission_authority": False,
        })
        raw["scanner"]["candidate_readiness"]["PRIVATE_ACCESS_TOKEN"] = "PRIVATE_VALUE"
        poisoned = a652.build_receipt(raw)
        self.assertEqual(poisoned["host"]["state"], "NO_VERIFICADO")
        self.assertNotIn("PRIVATE_ACCESS_TOKEN", json.dumps(poisoned))
        self.assertNotIn("PRIVATE_VALUE", json.dumps(poisoned))

    def test_physical_cooldown_not_attempted_and_boot_calendar_are_not_laundered(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            fixture_db(db)
            for auth, session in (("COOLDOWN", "BOOT_CALENDAR"),
                                  ("NOT_ATTEMPTED", "MARKET_CLOSED"),
                                  ("INVALID_SECRET", "INVALID_SESSION")):
                with self.subTest(auth=auth, session=session):
                    with closing(sqlite3.connect(db)) as c:
                        c.execute("UPDATE observer_state SET ppi_auth=?,session_state=?",
                                  (auth, session))
                        c.commit()
                    with patch.object(a617, "_mount_is_readonly", return_value=True):
                        report = a652.build_receipt(a652.host_probe(db=db, runner=fake_systemctl))
                    observed = report["host"]["observer"]
                    self.assertEqual(observed["ppi_auth"],
                                     auth if auth != "INVALID_SECRET" else "UNKNOWN")
                    self.assertEqual(observed["session_state"],
                                     session if session != "INVALID_SESSION" else "UNKNOWN")
                    self.assertFalse(report["paper_ready"])

    def test_exact_api_health_guard_reports_cause_and_age_without_raw_detail(self):
        detail = "ReadOnlyPolicyViolation: PPI_AUTHENTICATION_AUTHORITY_UNVERIFIED"
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            fixture_db(db)
            fixture_auth_health(db, detail=detail)
            fixed_now = datetime(2026, 11, 15, 23, 55, 27, tzinfo=timezone.utc)
            with (patch.object(a617, "_mount_is_readonly", return_value=True),
                  patch.object(a652, "_now_utc", return_value=fixed_now, create=True)):
                raw = a652.host_probe(db=db, runner=fake_systemctl)
            receipt = a652.build_receipt(raw)
            observed = receipt["host"]["auth_health"]
            self.assertEqual(observed, {
                "state": "OBSERVED", "cause": "LOCAL_AUTH_GUARD",
                "checked_at_utc": "2026-11-15T23:45:27+00:00", "age_seconds": 600})
            self.assertNotIn(detail, json.dumps(raw))
            self.assertNotIn(detail, json.dumps(receipt))
            self.assertFalse(receipt["paper_ready"])
            self.assertEqual(receipt["probe_provider_gets"], 0)
            self.assertEqual(receipt["probe_host_file_writes"], 0)

    def test_api_health_non_exact_details_are_other_redacted(self):
        for detail in (
                "ReadOnlyPolicyViolation: PPI_AUTHENTICATION_AUTHORITY_UNVERIFIED suffix",
                "ConnectionError: private-token private-account",
                "ReadOnlyPolicyViolation: PPI_AUTHENTICATION_AUTHORITY_UNVERIFIED\nsecret"):
            with self.subTest(detail=detail), tempfile.TemporaryDirectory() as directory:
                db = Path(directory) / "observer.db"
                fixture_db(db)
                fixture_auth_health(db, detail=detail)
                with patch.object(a617, "_mount_is_readonly", return_value=True):
                    raw = a652.host_probe(db=db, runner=fake_systemctl)
                receipt = a652.build_receipt(raw)
                self.assertEqual(receipt["host"]["auth_health"]["cause"], "OTHER_REDACTED")
                self.assertNotIn(detail, json.dumps(raw))
                self.assertNotIn(detail, json.dumps(receipt))

    def test_api_health_missing_table_or_row_remains_unverified(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            fixture_db(db)
            with patch.object(a617, "_mount_is_readonly", return_value=True):
                missing_table = a652.build_receipt(
                    a652.host_probe(db=db, runner=fake_systemctl))
            self.assertEqual(missing_table["host"]["auth_health"]["state"], "NO_VERIFICADO")
            fixture_auth_health(db, detail="never-disclosed")
            with closing(sqlite3.connect(db)) as c:
                c.execute("DELETE FROM api_health")
                c.commit()
            with patch.object(a617, "_mount_is_readonly", return_value=True):
                missing_row = a652.build_receipt(
                    a652.host_probe(db=db, runner=fake_systemctl))
            self.assertEqual(missing_row["host"]["auth_health"]["cause"], "NO_VERIFICADO")

    def test_api_health_uses_existing_sqlite_read_only_connection_without_http(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            fixture_db(db)
            fixture_auth_health(db, detail="synthetic-private-token")
            original_connect = sqlite3.connect
            seen = []
            sql_trace = []

            def guarded_connect(*args, **kwargs):
                seen.append((args, kwargs))
                self.assertIn("?mode=ro", args[0])
                self.assertTrue(kwargs.get("uri"))
                conn = original_connect(*args, **kwargs)
                conn.set_trace_callback(sql_trace.append)
                return conn

            with (patch.object(a617, "_mount_is_readonly", return_value=True),
                  patch.object(a652.sqlite3, "connect", side_effect=guarded_connect),
                  patch("urllib.request.urlopen", side_effect=AssertionError("HTTP prohibited"))):
                receipt = a652.build_receipt(a652.host_probe(db=db, runner=fake_systemctl))
            self.assertEqual(len(seen), 1)
            self.assertTrue(sql_trace)
            self.assertTrue(all(statement.lstrip().split(None, 1)[0].upper() in
                                {"SELECT", "PRAGMA", "WITH"} for statement in sql_trace))
            self.assertEqual(receipt["host"]["auth_health"]["cause"], "OTHER_REDACTED")
            self.assertEqual(receipt["probe_provider_gets"], 0)
            self.assertEqual(receipt["probe_host_file_writes"], 0)

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

    def test_exact_generic_exception_taxonomy_only_for_future_cycles(self):
        examples = {
            "PPI_TIMEOUTERROR": "PPI_TIMEOUT_EXCEPTION",
            "PPI_READTIMEOUT": "PPI_TIMEOUT_EXCEPTION",
            "PPI_CONNECTTIMEOUT": "PPI_TIMEOUT_EXCEPTION",
            "PPI_CONNECTIONERROR": "PPI_NETWORK_EXCEPTION",
            "PPI_REMOTEDISCONNECTED": "PPI_NETWORK_EXCEPTION",
            "PPI_PROXYERROR": "PPI_NETWORK_EXCEPTION",
            "PPI_KEYERROR": "PPI_SCHEMA_OR_TYPE_EXCEPTION",
            "PPI_ATTRIBUTEERROR": "PPI_SCHEMA_OR_TYPE_EXCEPTION",
            "PPI_TYPEERROR": "PPI_SCHEMA_OR_TYPE_EXCEPTION",
            "PPI_FILENOTFOUNDERROR": "PPI_LOCAL_OS_EXCEPTION",
            "PPI_PERMISSIONERROR": "PPI_LOCAL_OS_EXCEPTION",
        }
        for class_label, expected in examples.items():
            with self.subTest(class_label=class_label):
                self.assertEqual(a652._error_bucket("private-symbol: " + class_label), expected)
        self.assertTrue(set(examples.values()) <= set(a652.ERROR_BUCKETS))

    def test_unrecognized_class_is_not_guessed_or_emitted(self):
        for raw in ("PPI_VALUEERROR", "PPI_OPERATIONALERROR", "PPI_RUNTIMEERROR",
                    "PPI_SECRET_TIMEOUTERROR", "PPI_TIMEOUTERROR_SECRET",
                    "PPI_CONNECTIONERROR_PASSWORD", "PPI_UNKNOWN_BROKER_SDK_CLASS"):
            with self.subTest(raw=raw):
                bucket = a652._error_bucket("sensitive-ticker: " + raw)
                self.assertEqual(bucket, "PPI_OTHER_CLASSIFIED")
                self.assertNotIn(raw, bucket)
        self.assertEqual(a652._error_bucket("sensitive-ticker: PPI_🔥"), "OTHER")

    def test_v2_schema_never_reinterprets_historical_v1_evidence(self):
        self.assertTrue(a652.HOST_SCHEMA.endswith(".v2"))
        self.assertTrue(a652.RECEIPT_SCHEMA.endswith(".v2"))
        legacy = {"schema": "rc6.a652.watch-budget-scanner-host-ro.v1",
                  "read_only": True, "provider_gets": 0,
                  "budget_leases": 0, "orders_called": False,
                  "ppi_watch_mutations": 0, "host_file_writes": 0,
                  "scanner": {"state": "OBSERVED"}, "budget": {}, "watch": {}}
        report = a652.build_receipt(legacy)
        self.assertEqual(report["state"], "INCOMPLETE_NO_GO")
        self.assertEqual(report["host"]["state"], "NO_VERIFICADO")

    def test_future_cycle_new_buckets_only_aggregate_no_raw_names(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            fixture_db(db)
            with closing(sqlite3.connect(db)) as c:
                for suffix in ("PPI_TIMEOUTERROR", "PPI_CONNECTIONERROR",
                               "PPI_KEYERROR", "PPI_FILENOTFOUNDERROR"):
                    c.execute("INSERT INTO paper_events(event_at,event_type,detail) VALUES(?,?,?)",
                              ("2026-11-04T19:31:00+00:00", "DATA_ERROR",
                               "private-symbol-token: " + suffix))
                c.commit()
            with patch.object(a617, "_mount_is_readonly", return_value=True):
                raw = a652.host_probe(db=db, runner=fake_systemctl)
            counts = raw["scanner"]["events"]["data_error_reasons"]
            self.assertEqual(tuple(counts[k] for k in (
                "PPI_TIMEOUT_EXCEPTION", "PPI_NETWORK_EXCEPTION",
                "PPI_SCHEMA_OR_TYPE_EXCEPTION", "PPI_LOCAL_OS_EXCEPTION")), (1, 1, 1, 1))
            output = json.dumps(a652.build_receipt(raw))
            self.assertNotIn("private-symbol", output)
            self.assertNotIn("PPI_CONNECTIONERROR", output)
            self.assertLess(len(output.encode("utf-8")), 16384)

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
