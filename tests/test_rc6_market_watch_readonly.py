"""Adversarial offline contracts for the remote READ_ONLY RC6 market monitor."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
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
        with patch.object(monitor, "_resources", return_value=self.resources):
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
        third = dict(second, generated_at=(NOW + timedelta(minutes=20)).isoformat())
        three = monitor.classify(third, second, two)
        self.assertEqual(three["new_alerts"], [])
        recovered = dict(third, findings=[], severity="GREEN",
                         generated_at=(NOW + timedelta(minutes=30)).isoformat())
        four = monitor.classify(recovered, third, three)
        self.assertEqual(four["recovered"], ["ENGINE_STALE"])
        p0 = dict(first, findings=[monitor.finding("PAPER_INVALID", "P0")], severity="P0")
        self.assertEqual(monitor.classify(p0)["new_alerts"], ["PAPER_INVALID"])

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


if __name__ == "__main__":
    unittest.main()
