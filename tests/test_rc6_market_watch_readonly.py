"""Adversarial offline contracts for the remote READ_ONLY RC6 market monitor."""
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import sys
import types
import unittest
import zipfile
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "scripts/rc6_market_watch_readonly.py"
spec = importlib.util.spec_from_file_location("rc6_market_watch_readonly", SOURCE)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
SHA = "a" * 40
IMAGE = "sha256:" + "b" * 64
NOW = datetime(2026, 10, 14, 14, 0, tzinfo=timezone.utc)


class MonitorContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        db = self.root / monitor.DB_REL
        db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db) as connection:
            connection.execute("""CREATE TABLE observer_state
                (id INTEGER PRIMARY KEY, mode TEXT, process_state TEXT,
                session_state TEXT, ppi_auth TEXT, real_orders_sent INTEGER,
                heartbeat_at TEXT, last_market_data_at TEXT)""")
            connection.execute("INSERT INTO observer_state VALUES(1,?,?,?,?,?,?,?)", (
                "PRODUCTION_PAPER", "RUNNING", "MARKET_OPEN", "OK", 0,
                NOW.isoformat(), NOW.isoformat()))
            connection.execute("""CREATE TABLE candidate_identity_v2
                (can_simulate INTEGER, status TEXT)""")
            connection.executemany("INSERT INTO candidate_identity_v2 VALUES(?,?)",
                                   [(1, "AVAILABLE"), (0, "PAUSED_EXPLICIT")])
            for name in monitor.TRACKED:
                connection.execute(f'CREATE TABLE "{name}" (id INTEGER PRIMARY KEY)')
                connection.execute(f'INSERT INTO "{name}" VALUES(1)')
        self.db = db
        self.provenance_path = self.root / "data/deploy/CURRENT_STATE_V2.json"
        self.provenance_path.parent.mkdir(parents=True, exist_ok=True)
        self.provenance_path.write_text(json.dumps({
            "deploy_sha": SHA, "candidate_sha": "c" * 40,
            "image_id": IMAGE, "mode": "PRODUCTION_PAPER",
            "real_orders_sent": 0, "real_order_routes": "NOT_CALLED",
            "validation_status": "VALIDATED_RUNTIME"}))
        self.shadow_path = self.root / (
            "data/paper_v17/artifacts/observer_v17.db/dynamic-shadow/status.json")
        self.shadow_path.parent.mkdir(parents=True, exist_ok=True)
        self.write_shadow()
        self.performance_path = self.root / (
            "data/paper_v17/observer_v17.db.performance.sqlite.latest.json")
        self.performance_path.write_text(json.dumps({
            "generated_at": NOW.isoformat(), "economic_edge_validated": False,
            "forward_signal_evaluation": {"status": "NO_VERIFICADO"}}))
        self.inspect = {name: {"state": "running", "restarts": 0, "oom": False,
                               "image": IMAGE} for name in monitor.CONTAINERS}
        self.resources = {"disk_free_bytes": 10 * 1024**3,
                          "inode_free_pct": 70, "mem_available_bytes": 700 * 1024**2}

    def write_shadow(self, status="SHADOW_OBSERVING", as_of=None, real=0):
        payload = {"as_of": as_of or NOW.isoformat(), "status": status,
                   "real_orders_sent": real, "real_routes": "NOT_CALLED",
                   "entry_signal_lab_status": "NO_VERIFICADO",
                   "operational_funnel_status": "NO_VERIFICADO"}
        self.shadow_path.write_text(json.dumps({
            "payload": payload, "digest": monitor.canonical_digest(payload)}))

    def capture(self, at=NOW):
        with patch.object(monitor, "_resources", return_value=self.resources), patch.object(
                monitor, "_host_control_state", return_value={
                    "legacy_health_timer": "inactive", "fast_health_timer": "active",
                    "observer_container": {"Status": "running"}}):
            return monitor.probe(root=self.root, at=at, expected_sha=SHA,
                                 inspect=self.inspect)

    def codes(self, record, severity=None):
        return {f["code"] for f in record["findings"]
                if severity is None or f["severity"] == severity}

    def test_healthy_with_no_trades_or_economic_edge(self):
        observation = self.capture()
        self.assertEqual(observation["severity"], "GREEN")
        self.assertEqual(observation["readiness"], {"total": 2, "ready": 1})
        self.assertEqual(observation["cursors"]["paper_fills"], 1)
        self.assertFalse(observation["performance"]["edge_validated"])
        self.assertFalse(observation["ppi_watch_accessed"])
        self.assertEqual(observation["broker_requests"], 0)
        self.assertEqual(observation["real_order_routes_called"], 0)
        self.assertEqual(observation["host_control_state"]["legacy_health_timer"], "inactive")

    def test_shadow_sqlite_context_is_allowlisted(self):
        payload = {"as_of": NOW.isoformat(), "status": "FAIL_CLOSED",
                   "real_orders_sent": 0, "real_routes": "NOT_CALLED",
                   "error_class": "OperationalError", "sqlite_errorcode": 9,
                   "sqlite_errorname": "SQLITE_INTERRUPT",
                   "error_module": "runtime", "error_line": 76}
        self.shadow_path.write_text(json.dumps({
            "payload": payload, "digest": monitor.canonical_digest(payload)}))
        record = self.capture()
        self.assertEqual(record["shadow"]["sqlite_errorname"], "SQLITE_INTERRUPT")
        self.assertEqual(record["shadow"]["error_module"], "runtime")
        self.assertEqual(record["shadow"]["error_line"], 76)
        self.assertIn("DYNAMIC_SHADOW_FAIL_CLOSED", self.codes(record, "P1"))

        payload.update(sqlite_errorname="SQLITE_INTERRUPT secret",
                       error_module={"path": "/app/private"},
                       error_line="76;secret")
        self.shadow_path.write_text(json.dumps({
            "payload": payload, "digest": monitor.canonical_digest(payload)}))
        rejected = self.capture()["shadow"]
        self.assertNotIn("sqlite_errorname", rejected)
        self.assertNotIn("error_module", rejected)
        self.assertNotIn("error_line", rejected)

    def test_shadow_value_error_keeps_only_reviewed_site(self):
        payload = {"as_of": NOW.isoformat(), "status": "FAIL_CLOSED",
                   "real_orders_sent": 0, "real_routes": "NOT_CALLED",
                   "error_class": "ValueError", "error_module": "stages",
                   "error_line": 42}
        self.shadow_path.write_text(json.dumps({
            "payload": payload, "digest": monitor.canonical_digest(payload)}))
        record = self.capture()
        self.assertEqual(record["shadow"]["error_module"], "stages")
        self.assertEqual(record["shadow"]["error_line"], 42)
        self.assertIn("DYNAMIC_SHADOW_FAIL_CLOSED", self.codes(record, "P1"))

        payload["error_module"] = "stages/private-token"
        self.shadow_path.write_text(json.dumps({
            "payload": payload, "digest": monitor.canonical_digest(payload)}))
        self.assertNotIn("error_module", self.capture()["shadow"])

    def test_shadow_payload_context_is_strictly_allowlisted(self):
        payload = {"as_of": NOW.isoformat(), "status": "FAIL_CLOSED",
                   "real_orders_sent": 0, "real_routes": "NOT_CALLED",
                   "error_class": "EvidencePayloadLimit",
                   "evidence_file": "latest.json.gz",
                   "evidence_measured_bytes": 70 * 1024**2,
                   "evidence_limit_bytes": 64 * 1024**2}
        self.shadow_path.write_text(json.dumps({
            "payload": payload, "digest": monitor.canonical_digest(payload)}))
        record = self.capture()
        self.assertEqual(record["shadow"]["evidence_file"], "latest.json.gz")
        self.assertEqual(record["shadow"]["evidence_measured_bytes"], 70 * 1024**2)
        self.assertIn("DYNAMIC_SHADOW_FAIL_CLOSED", self.codes(record, "P1"))
        payload.update(evidence_file="preopen-private-token",
                       evidence_measured_bytes="private-token",
                       evidence_limit_bytes=0)
        self.shadow_path.write_text(json.dumps({
            "payload": payload, "digest": monitor.canonical_digest(payload)}))
        rejected = self.capture()["shadow"]
        self.assertNotIn("evidence_file", rejected)
        self.assertNotIn("evidence_measured_bytes", rejected)
        self.assertNotIn("evidence_limit_bytes", rejected)
        self.assertNotIn("private-token", str(rejected))

    def test_manual_shadow_diagnostic_redacts_sqlite_failure_and_never_ticks(self):
        runtime = types.ModuleType("rc6_dynamic_universe.runtime")
        shadow = types.ModuleType("rc6_shadow_runtime.worker")
        def failing_read(*args, **kwargs):
            raise sqlite3.OperationalError("secret SQL text and account data")
        class Reader:
            @staticmethod
            def _metadata(obj, at, since):
                return "readonly", []
            def tick(self, *args):
                self.fail("diagnostic must never invoke a writer")
        runtime.read_runtime = failing_read
        shadow.ShadowRuntime = Reader
        with patch.dict(sys.modules, {
                "rc6_dynamic_universe.runtime": runtime,
                "rc6_shadow_runtime.worker": shadow}), patch.dict(
                "os.environ", {"PAPER_V17_DB_PATH": str(self.db)}):
            record = monitor.shadow_diagnostic()
        self.assertEqual([x["stage"] for x in record["stages"]],
                         ["RUNTIME_INPUT", "METADATA"])
        self.assertEqual([x["status"] for x in record["stages"]],
                         ["FAIL_CLOSED", "OK"])
        self.assertEqual(record["stages"][0]["error_class"], "OperationalError")
        self.assertNotIn("secret", json.dumps(record))
        self.assertTrue(record["read_only"])

    def test_provenance_divergence_is_critical(self):
        payload = json.loads(self.provenance_path.read_text())
        payload["deploy_sha"] = "d" * 40
        self.provenance_path.write_text(json.dumps(payload))
        self.assertIn("DEPLOY_PROVENANCE_INVALID", self.codes(self.capture(), "P0"))

    def test_sql_counter_is_fail_closed(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE observer_state SET real_orders_sent=NULL")
        self.assertIn("REAL_ORDER_COUNTER_INVALID", self.codes(self.capture(), "P0"))

    def test_boolean_counter_is_not_exact_zero(self):
        original = monitor._db_snapshot
        def malformed(path):
            record = original(path)
            record["observer"]["real_orders_sent"] = False
            return record
        with patch.object(monitor, "_db_snapshot", side_effect=malformed):
            self.assertIn("REAL_ORDER_COUNTER_INVALID", self.codes(self.capture(), "P0"))

    def test_heartbeat_future_is_not_healthy(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE observer_state SET heartbeat_at=?",
                               ((NOW + timedelta(minutes=3)).isoformat(),))
        self.assertIn("OBSERVER_HEARTBEAT_INVALID", self.codes(self.capture(), "P0"))

    def test_explicit_fail_closed_is_p1_even_in_preopen(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE observer_state SET session_state='PREOPEN'")
        envelope = json.loads(self.shadow_path.read_text())
        envelope["payload"]["status"] = "FAIL_CLOSED"
        envelope["payload"]["error_class"] = "ValueError"
        envelope["digest"] = monitor.canonical_digest(envelope["payload"])
        self.shadow_path.write_text(json.dumps(envelope))
        observation = self.capture()
        self.assertIn("DYNAMIC_SHADOW_FAIL_CLOSED", self.codes(observation, "P1"))
        self.assertEqual(observation["shadow"]["error_class"], "ValueError")

    def test_shadow_hash_corruption_is_detected(self):
        envelope = json.loads(self.shadow_path.read_text())
        envelope["payload"]["status"] = "SHADOW_OBSERVING_MODIFIED"
        self.shadow_path.write_text(json.dumps(envelope))
        self.assertIn("DYNAMIC_SHADOW_STATUS_MISSING_OR_INVALID",
                      self.codes(self.capture(), "P1"))

    def test_shadow_stale_when_market_open(self):
        self.write_shadow(as_of=(NOW - timedelta(minutes=10)).isoformat())
        self.assertIn("DYNAMIC_SHADOW_NOT_PROGRESSING", self.codes(self.capture(), "P1"))

    def test_source_stale_is_not_replaced_by_synthetic_data(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE observer_state SET last_market_data_at=?",
                               ((NOW - timedelta(minutes=30)).isoformat(),))
        current = self.capture()
        self.assertIn("PPI_MARKETDATA_NOT_FRESH", self.codes(current, "P1"))
        self.assertEqual(current["broker_requests"], 0)

    def test_closed_market_does_not_require_fills_or_live_quotes(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE observer_state SET session_state='MARKET_CLOSED',"
                               "last_market_data_at=?", ((NOW - timedelta(days=1)).isoformat(),))
        self.assertEqual(self.capture()["severity"], "GREEN")

    def test_memory_shortage_is_not_hidden(self):
        self.resources["mem_available_bytes"] = 20 * 1024**2
        self.assertIn("MEMORY_PRESSURE", self.codes(self.capture(), "P1"))

    def test_docker_oom_is_critical(self):
        self.inspect["porota_production_observer"]["oom"] = True
        self.assertIn("CONTAINER_NOT_HEALTHY:porota_production_observer",
                      self.codes(self.capture(), "P0"))

    def test_p0_immediate_but_p1_needs_two_samples(self):
        first = self.capture()
        first["findings"] = [monitor.finding("ENGINE_STALE", "P1")]
        first["severity"] = "P1"
        one = monitor.classify(first)
        self.assertEqual(one["pending_confirmation"], ["ENGINE_STALE"])
        self.assertEqual(one["new_alerts"], [])
        second = dict(first, generated_at=(NOW + timedelta(minutes=10)).isoformat())
        two = monitor.classify(second, first, one)
        self.assertEqual(two["new_alerts"], ["ENGINE_STALE"])
        two = monitor.finalize_delivery(two, {"status": "SENT", "message_id": "123"})
        third = dict(second, generated_at=(NOW + timedelta(minutes=20)).isoformat())
        three = monitor.classify(third, second, two)
        self.assertEqual(three["new_alerts"], [])
        three = monitor.finalize_delivery(three, {"status": "NO_TRANSITION"})
        recovered = dict(third, findings=[], severity="GREEN",
                         generated_at=(NOW + timedelta(minutes=30)).isoformat())
        four = monitor.classify(recovered, third, three)
        self.assertEqual(four["recovered"], ["ENGINE_STALE"])
        p0 = dict(first, findings=[monitor.finding("PAPER_INVALID", "P0")], severity="P0")
        self.assertEqual(monitor.classify(p0)["new_alerts"], ["PAPER_INVALID"])

    def test_unacknowledged_transition_retries_and_recovery_requires_ack(self):
        first = self.capture()
        first["findings"] = [monitor.finding("ENGINE_STALE", "P1")]
        first["severity"] = "P1"
        one = monitor.classify(first)
        second = dict(first, generated_at=(NOW + timedelta(minutes=10)).isoformat())
        two = monitor.finalize_delivery(monitor.classify(second, first, one),
                                        {"status": "NOT_CONFIGURED"})
        self.assertEqual(two["acknowledged_codes"], [])
        third = dict(second, generated_at=(NOW + timedelta(minutes=20)).isoformat())
        retry = monitor.classify(third, second, two)
        self.assertEqual(retry["new_alerts"], ["ENGINE_STALE"])
        acknowledged = monitor.finalize_delivery(retry, {"status": "SENT", "message_id": "7"})
        self.assertEqual(acknowledged["acknowledged_codes"], ["ENGINE_STALE"])
        recovered = dict(third, findings=[], severity="GREEN",
                         generated_at=(NOW + timedelta(minutes=30)).isoformat())
        recovery = monitor.classify(recovered, third, acknowledged)
        self.assertEqual(recovery["recovered"], ["ENGINE_STALE"])
        failed = monitor.finalize_delivery(recovery, {"status": "FAILED"})
        self.assertEqual(failed["acknowledged_codes"], ["ENGINE_STALE"])
        assert monitor.prepare_alert(recovery)

    def test_same_run_prior_attempt_confirms_p1_and_requires_telegram_ack(self):
        prior = self.capture()
        prior["generated_at"] = (NOW - timedelta(minutes=10)).isoformat()
        prior["findings"] = [monitor.finding("DYNAMIC_SHADOW_FAIL_CLOSED", "P1")]
        prior["severity"] = "P1"
        earlier = monitor.classify(prior)
        with io.BytesIO() as stream:
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("snapshot.json", json.dumps(prior))
                archive.writestr("decision.json", json.dumps(earlier))
            raw = stream.getvalue()
        artifact = {"id": 123, "name": "rc6-market-watch-88", "expired": False,
                    "size_in_bytes": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                    "created_at": prior["generated_at"], "workflow_run": {"id": 88}}
        current = dict(prior, generated_at=NOW.isoformat())
        calls = []
        def get(url, _limit):
            calls.append(url)
            if url.endswith("/runs/88/artifacts?per_page=100"):
                return json.dumps({"artifacts": [artifact]}).encode()
            if "workflows/384354765/runs" in url:
                return json.dumps({"workflow_runs": []}).encode()
            if url.endswith("/artifacts/123/zip"):
                return raw
            self.fail("unexpected API request")
        receipt = monitor.read_previous_artifact(current, "mbalbo2023/Porota-trading",
            384354765, 88, self.root / "prior", get=get)
        self.assertEqual(receipt["run_id"], 88)
        self.assertEqual(receipt["artifact_sha256"], artifact["digest"])
        self.assertTrue(any("runs/88/artifacts" in url for url in calls))
        previous = json.loads((self.root / "prior/snapshot.json").read_text())
        decision = monitor.classify(current, previous, earlier)
        self.assertTrue(decision["previous_comparable"])
        self.assertEqual(decision["new_alerts"], ["DYNAMIC_SHADOW_FAIL_CLOSED"])
        self.assertEqual(monitor.finalize_delivery(decision, {"status": "UNVERIFIED"})[
            "acknowledged_codes"], [])
        self.assertEqual(monitor.finalize_delivery(decision, {"status": "SENT",
            "message_id": "909"})["acknowledged_codes"], ["DYNAMIC_SHADOW_FAIL_CLOSED"])
        corrupted = dict(artifact, digest="sha256:" + "0" * 64)
        self.assertIsNone(monitor.verified_previous_zip(raw, corrupted, current))
        wrong_sha = dict(current, expected_deploy_sha="d" * 40)
        self.assertIsNone(monitor.verified_previous_zip(raw, artifact, wrong_sha))
        stale = dict(current, generated_at=(NOW + timedelta(minutes=31)).isoformat())
        self.assertIsNone(monitor.verified_previous_zip(raw, artifact, stale))

    def test_other_run_lookup_stops_at_first_verified_artifact(self):
        current = self.capture()
        earlier = dict(current, generated_at=(NOW - timedelta(minutes=3)).isoformat())
        prior_decision = monitor.classify(earlier)
        with io.BytesIO() as stream:
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("snapshot.json", json.dumps(earlier))
                archive.writestr("decision.json", json.dumps(prior_decision))
            raw = stream.getvalue()
        artifact = {"id": 321, "name": "rc6-market-watch-87", "expired": False,
            "size_in_bytes": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "created_at": earlier["generated_at"], "workflow_run": {"id": 87}}
        calls = []
        def get(url, _limit):
            calls.append(url)
            if "/runs/88/artifacts" in url:
                return b'{"artifacts":[]}'
            if "/workflows/384354765/runs" in url:
                return json.dumps({"workflow_runs": [
                    {"id": 88, "status": "in_progress"},
                    {"id": 87, "status": "completed"},
                    {"id": 86, "status": "completed"}]}).encode()
            if "/runs/87/artifacts" in url:
                return json.dumps({"artifacts": [artifact]}).encode()
            if "/artifacts/321/zip" in url:
                return raw
            self.fail("older or unexpected run fetched")
        receipt = monitor.read_previous_artifact(current, "mbalbo2023/Porota-trading",
            384354765, 88, self.root / "lazy", get=get)
        self.assertEqual(receipt["run_id"], 87)
        self.assertEqual(len(calls), 4)
        self.assertFalse(any("/runs/86/" in url for url in calls))

    def test_default_lookup_uses_runner_gh_without_token_on_argv(self):
        current = self.capture()
        calls = []
        def invoke(command, **options):
            calls.append(command)
            options["stdout"].write(b'{"artifacts":[]}' if "/runs/88/" in command[-1]
                                    else b'{"workflow_runs":[]}')
            return subprocess.CompletedProcess(command, 0)
        with patch.object(monitor.subprocess, "run", side_effect=invoke), patch.dict(
                monitor.os.environ, {"GH_TOKEN": "private-test-token"}):
            self.assertIsNone(monitor.read_previous_artifact(current,
                "mbalbo2023/Porota-trading", 384354765, 88, self.root / "none"))
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(command[:2] == ["gh", "api"] for command in calls))
        self.assertNotIn("private-test-token", str(calls))

    def test_telegram_host_transport_acks_only_message_id_and_never_exposes_token(self):
        body = "POROTA RC6 | ALERT\nOBSERVER_HEARTBEAT_INVALID"
        encoded = monitor.base64.urlsafe_b64encode(body.encode()).decode()
        with patch.object(monitor.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout='{"status":"SENT","message_id":"34"}', stderr="token=secret")) as run:
            self.assertEqual(monitor.alert_host(encoded), {"status": "SENT", "message_id": "34"})
        args, options = run.call_args
        self.assertEqual(args[0][:3], ["docker", "exec", "porota_production_observer"])
        self.assertEqual(options["timeout"], 19)
        compile(args[0][-2], "<telegram-child>", "exec")
        with patch.object(monitor.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout='{"status":"SENT"}', stderr="token=secret")):
            self.assertEqual(monitor.alert_host(encoded)["status"], "FAILED")

    def test_incompatible_old_sha_cannot_confirm_p1(self):
        current = self.capture()
        current["findings"] = [monitor.finding("NO_QUOTES", "P1")]
        previous = dict(current, expected_deploy_sha="b" * 40,
                        generated_at=(NOW - timedelta(minutes=10)).isoformat())
        self.assertEqual(monitor.classify(current, previous)["new_alerts"], [])

    def test_expired_previous_sample_cannot_confirm_p1(self):
        current = self.capture()
        current["findings"] = [monitor.finding("NO_QUOTES", "P1")]
        previous = dict(current, generated_at=(NOW - timedelta(minutes=35)).isoformat())
        self.assertEqual(monitor.classify(current, previous)["new_alerts"], [])

    def test_transport_failure_does_not_claim_zero_orders_verified(self):
        result = monitor.transport_error()
        decision = monitor.classify(result)
        self.assertEqual(decision["new_alerts"], ["REMOTE_PROBE_UNAVAILABLE"])
        self.assertFalse(decision["real_orders_sent_verified"])

    def test_schedule_clock_is_local_and_conservative(self):
        self.assertTrue(monitor.active_hours(NOW))
        self.assertFalse(monitor.active_hours(datetime(2026, 10, 14, 12, tzinfo=timezone.utc)))
        self.assertFalse(monitor.active_hours(datetime(2026, 10, 18, 14, tzinfo=timezone.utc)))

    def test_host_diagnostic_uses_bounded_container_read_and_redacts_stderr(self):
        payload = {"schema": "rc6.shadow-readonly-diagnostic.v1", "read_only": True,
                   "stages": [{"stage": "RUNTIME_INPUT", "status": "OK"}]}
        with patch.object(monitor.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(payload), stderr="secret")) as run:
            result = monitor.shadow_diagnostic_host()
            self.assertEqual(result["stages"], payload["stages"])
        args, options = run.call_args_list[0]
        self.assertEqual(args[0][:4], ["docker", "exec", "porota_production_observer", "python"])
        self.assertEqual(options["timeout"], 9)
        self.assertTrue(options["capture_output"])
        compile(args[0][-1], "<container-diagnostic>", "exec")
        self.assertIn("EXPLAIN QUERY PLAN", args[0][-1])
        self.assertIn("NOT INDEXED", args[0][-1])
        self.assertIn('result["query_stages"]', args[0][-1])
        self.assertIn('observation_limit=3001', args[0][-1])
        self.assertIn('result["observation_source_limit_with_sentinel"]', args[0][-1])
        self.assertIn('c.set_progress_handler(expired,1000)', args[0][-1])
        self.assertNotIn('read_runtime(db,as_of=at', args[0][-1])
        self.assertIn('NOT_RUN_STAGE_ONLY', args[0][-1])
        with patch.object(monitor.subprocess, "run", side_effect=subprocess.TimeoutExpired(
                cmd="docker", timeout=9, stderr="secret")):
            failure = monitor.shadow_diagnostic_host()
        self.assertEqual(failure["status"], "CONTAINER_PROBE_TIMEOUT")
        self.assertNotIn("secret", json.dumps(failure))

    def test_host_state_survives_optional_container_diagnostic_timeout(self):
        def read(command, **kwargs):
            if command[:2] == ["systemctl", "is-active"]:
                status = "inactive" if command[-1] == "porota-functional-health-rc6.timer" else "active"
                return subprocess.CompletedProcess(command, 0, stdout=status + "\n", stderr="secret")
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({
                "Status": "running", "StartedAt": "2026-10-14T15:30:00Z",
                "ExitCode": 0, "OOMKilled": False, "Env": "secret"}), stderr="secret")
        with patch.object(monitor.subprocess, "run", side_effect=read):
            result = monitor._host_control_state()
        self.assertEqual(result["legacy_health_timer"], "inactive")
        self.assertEqual(result["fast_health_timer"], "active")
        self.assertEqual(result["observer_container"]["Status"], "running")
        self.assertNotIn("secret", json.dumps(result))

    def test_manual_public_schedule_api_probe_is_bounded_and_redacted(self):
        payload = {"status": "API_OK", "total_count": 0,
                   "latest_event": None, "latest_path": None}
        with patch.object(monitor.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(payload), stderr="secret")) as call:
            result = monitor.schedule_api_host()
        self.assertEqual(result["total_count"], 0)
        self.assertTrue(result["read_only"])
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(call.call_args.kwargs["timeout"], 7)
        self.assertIn("event=schedule", call.call_args.args[0][-1])

    def test_manual_lifecycle_events_report_only_action_clock_and_exit(self):
        rows = [
            {"Action": "kill", "time": 1791990000,
             "Actor": {"Attributes": {"signal": "15", "env": "secret"}}},
            {"Action": "die", "time": 1791990001,
             "Actor": {"Attributes": {"exitCode": "1", "token": "secret"}}},
            {"Action": "start", "time": 1791990002},
        ]
        output = "\n".join(map(json.dumps, rows))
        with patch.object(monitor.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 0, stdout=output, stderr="secret"),
                subprocess.CompletedProcess([], 0, stdout="Traceback (most recent call last):\nValueError: secret", stderr="")]) as call:
            result = monitor.observer_lifecycle_host()
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["events"], [
            {"action": "kill", "epoch_seconds": 1791990000, "signal": 15},
            {"action": "die", "epoch_seconds": 1791990001, "exit_code": 1},
            {"action": "start", "epoch_seconds": 1791990002}])
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(result["proximate_log"]["exception_classes"], ["ValueError"])
        self.assertEqual(result["proximate_log"]["traceback_count"], 1)
        self.assertEqual(call.call_args_list[0].kwargs["timeout"], 6)
        self.assertEqual(call.call_args_list[0].args[0][0:2], ["docker", "events"])
        self.assertEqual(call.call_args_list[1].kwargs["timeout"], 5)
        self.assertEqual(call.call_args_list[1].args[0][0:2], ["docker", "logs"])

    def test_sqlite_exit_reason_is_categorical_and_redacted(self):
        event = json.dumps({"Action": "die", "time": 1791998273,
                            "Actor": {"Attributes": {"exitCode": "1"}}})
        secret = ('Traceback (most recent call last):\n'
                  '  File "/app/bf_production_paper_observer.py", line 1859, in run\n'
                  'sqlite3.OperationalError: database is locked; credential=secret')
        with patch.object(monitor.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 0, stdout=event, stderr=""),
                subprocess.CompletedProcess([], 0, stdout=secret, stderr="")]):
            result = monitor.observer_lifecycle_host()
        self.assertEqual(result["proximate_log"]["sqlite_hints"],
                         ["SQLITE_BUSY_LOCKED"])
        self.assertEqual(result["proximate_log"]["tracebacks"], [{
            "exception_class": "sqlite3.OperationalError",
            "last_app_frame": {"module": "bf_production_paper_observer.py",
                               "line": 1859, "function": "run"}}])
        self.assertNotIn("secret", json.dumps(result))

    def test_recent_critical_signatures_are_bounded_and_redacted(self):
        log = ('Traceback (most recent call last):\n'
               '  File "/app/rc6_shadow_runtime/worker.py", line 91, in run\n'
               'sqlite3.OperationalError: database is locked; token=secret\n'
               'REAL_ORDER_CAPABILITY=ENABLED token=secret\n')
        responses = [subprocess.CompletedProcess([], 0, stdout=log, stderr=""),
                     subprocess.CompletedProcess([], 0, stdout="healthy", stderr="")]
        with patch.object(monitor.subprocess, "run", side_effect=responses) as call:
            result = monitor.recent_critical_host()
        observer = result["containers"]["porota_production_observer"]
        self.assertEqual(observer["traceback_count"], 1)
        self.assertEqual(observer["tracebacks"], [{
            "exception_class": "sqlite3.OperationalError",
            "last_app_frame": {"module": "worker.py", "line": 91, "function": "run"},
            "sqlite_hint": "SQLITE_BUSY_LOCKED", "observed_utc": None}])
        self.assertEqual(observer["capability_signature_count"], 1)
        self.assertEqual(result["containers"]["porota_production_dashboard"]["traceback_count"], 0)
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(call.call_count, 2)
        for entry in call.call_args_list:
            self.assertEqual(entry.kwargs["timeout"], 6)
            self.assertEqual(entry.args[0][:5],
                             ["docker", "logs", "--timestamps", "--since", "30m"])

    def test_recent_critical_timing_identifies_current_errors(self):
        log = ('2026-10-14T18:20:02.123456789Z Traceback (most recent call last):\n'
               '2026-10-14T18:20:02.123456789Z sqlite3.OperationalError: database is locked\n'
               '2026-10-14T18:10:02.123456789Z ValueError: secret\n')
        with patch.object(monitor, "now_utc", return_value=datetime(
                2026, 10, 14, 18, 22, tzinfo=timezone.utc)), patch.object(
                monitor.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0, stdout=log, stderr=""),
                    subprocess.CompletedProcess([], 0, stdout="", stderr="")]):
            result = monitor.recent_critical_host()
        current = result["containers"]["porota_production_observer"]
        self.assertEqual(current["exception_lines_last_5m"], 1)
        self.assertEqual(current["tracebacks"][0]["observed_utc"],
                         "2026-10-14T18:20:02Z")
        self.assertNotIn("secret", json.dumps(result))

    def test_recent_critical_unavailable_does_not_export_stderr(self):
        with patch.object(monitor.subprocess, "run", side_effect=[
                subprocess.TimeoutExpired(["docker"], 6, stderr="secret"),
                subprocess.CompletedProcess([], 1, stdout="", stderr="secret")]):
            result = monitor.recent_critical_host()
        self.assertEqual([item["status"] for item in result["containers"].values()],
                         ["UNAVAILABLE", "UNAVAILABLE"])
        self.assertNotIn("secret", json.dumps(result))



if __name__ == "__main__":
    unittest.main()

