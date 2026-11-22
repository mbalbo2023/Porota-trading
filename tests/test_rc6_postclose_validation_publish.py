"""Regression coverage for the existing rootless postclose campaign."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from be_paper_engine import PaperStore
from cg_paper_workspace import artifact_root
from em_validation_campaign_rc6 import ledger_path, load_records
import rc6_action4_audit as action4
import rc6_scheduler_snapshot as scheduler
import rc6_validation_projection as projection
import rc6_validation_publish as publisher
from rc6_validation_runtime import PROOF_SOURCE


RELEASE = {
    "candidate_sha": "a" * 40,
    "candidate_tree_sha": "b" * 40,
    "deployment_sha": "c" * 40,
    "image_id": "sha256:" + "d" * 64,
    "download_receipt_sha256": "e" * 64,
}


class Campaign(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / "data"
        self.db = self.data / "paper_v17" / "observer_v17.db"
        self.store = PaperStore(str(self.db))
        self.root = self.data / "validation"
        self.env = patch.dict("os.environ", {"POROTA_BUILD_SHA": RELEASE["candidate_sha"],
                                             "POROTA_CANDIDATE_TREE_SHA": RELEASE["candidate_tree_sha"],
                                             "POROTA_PAPER_DB": str(self.db),
                                             "PAPER_V17_DB_PATH": str(self.db)}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.now = datetime(2026, 11, 20, 21, 10, tzinfo=timezone.utc)
        self.write_json(self.data / "deploy" / "CURRENT_STATE_V2.json", {
            **RELEASE, "recorded_at": (self.now - timedelta(hours=2)).isoformat(),
            "validation_status": "VALIDATED_RUNTIME", "deployment_backend": "ROOTLESS_SPLIT",
            "mode": "PRODUCTION_PAPER", "real_orders_sent": 0, "real_order_capability": "BLOCKED",
            "real_order_routes": "NOT_CALLED",
        })
        self.write_json(self.data / "operation_mode.json", {
            "mode": "PRODUCTION_PAPER", "execution": "SIMULATED", "apis": {"PPI_ORDERS": "BLOCKED"},
        })
        self.mark_session(self.now)

    @staticmethod
    def write_json(path, document):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document))

    def mark_session(self, now, *, real_orders=0, heartbeat=None):
        with self.store.connect() as db:
            db.execute("""INSERT OR REPLACE INTO observer_state
                       (id,mode,process_state,session_state,ppi_auth,heartbeat_at,real_orders_sent)
                       VALUES (1,'PRODUCTION_PAPER','WAITING_MARKET','MARKET_CLOSED','OK',?,?)""",
                       ((heartbeat or now).isoformat(), real_orders))
        self.write_json(self.data / "scheduler" / "systemd_timers.json", {
            "schema": "porota-scheduler-systemd-v1", "state": "OK", "recorded_at": now.isoformat(),
            "timers": [{"unit": unit, "enabled_state": "enabled", "active_state": "active",
                        "last_result": "success", "last_trigger": "t", "exec_main_status": "0"}
                       for unit in (scheduler.EXPORT_UNIT, "porota-snapshot-postclose-rc6.timer")],
        })

    def publish(self, now, **fields):
        proof = {**RELEASE, "source": PROOF_SOURCE, "observed_at": now.isoformat(),
                 "rootless_pair": True, "startup_verified": True, "orders_guard": "BLOCKED",
                 "data_free_bytes": 20 * 2**30, "docker_free_bytes": 20 * 2**30, **fields}
        return publisher.publish(proof, now=now, db_path=self.db, root=self.root)

    def test_two_audited_sessions_progress_and_retries(self):
        first = self.publish(self.now)
        self.assertEqual(first, {"state": "PUBLISHED", "session_date": "2026-11-20", "records": 12})
        latest = projection.read(self.root, now=self.now)
        self.assertEqual(latest["milestones"]["M0"]["compliance_pct"], 80)
        self.assertEqual(latest["milestones"]["M1"]["claim_status"], "VERIFIED_CURRENT")
        self.assertEqual(latest["milestones"]["M11"]["claim_status"], "BLOCKED_BY_POLICY")
        before = ledger_path(self.root).read_bytes()
        latest_bytes = projection.snapshot_path(self.root).read_bytes()
        self.assertEqual(self.publish(self.now + timedelta(minutes=2))["state"], "UNCHANGED")
        self.assertEqual(before, ledger_path(self.root).read_bytes())
        self.assertEqual(latest_bytes, projection.snapshot_path(self.root).read_bytes())

        # Reconstruct a missing latest artifact from the archived Action 4
        # and the batch already committed to the campaign ledger.
        projection.snapshot_path(self.root).unlink()
        self.assertEqual(self.publish(self.now + timedelta(minutes=3))["state"], "PUBLISHED")
        self.assertEqual(before, ledger_path(self.root).read_bytes())
        self.assertEqual(self.publish(self.now + timedelta(days=3))["state"], "NOT_DUE")
        self.assertEqual(projection.read(self.root, now=self.now + timedelta(days=3))["milestones"]["M1"]["claim_status"], "STALE")

        next_session = self.now + timedelta(days=4)
        self.mark_session(next_session)
        result = self.publish(next_session, orders_guard=None)
        self.assertEqual(result["session_date"], "2026-11-24")
        latest = projection.read(self.root, now=next_session)
        self.assertEqual(latest["milestones"]["M1"]["compliance_pct"], 75)
        self.assertEqual(latest["milestones"]["M1"]["claim_status"], "PENDING")
        self.assertEqual(len(load_records(self.root)), 24)
        self.assertEqual([day["session_date"] for day in latest["campaign_days"]], ["2026-11-24", "2026-11-20"])
        reports = artifact_root(self.db) / "reports"
        self.assertEqual(len(list((reports / "validation-action4").glob("*/*.json"))), 2)
        self.assertEqual(json.loads((reports / action4.FILENAME).read_text())["origin"]["deployment_sha"],
                         RELEASE["deployment_sha"])

    def test_unbound_and_nonzero_runtime_do_not_publish(self):
        with self.assertRaisesRegex(ValueError, "CAMPAIGN_DEPLOY_RECEIPT_UNVERIFIED"):
            self.publish(self.now, deployment_sha="f" * 40)
        self.mark_session(self.now, real_orders=1)
        with self.assertRaisesRegex(ValueError, "CAMPAIGN_PAPER_POSTCLOSE_UNVERIFIED"):
            self.publish(self.now)
        self.assertFalse(projection.snapshot_path(self.root).exists())

    def test_stale_heartbeat_and_new_release_deny_current_safety(self):
        self.mark_session(self.now, heartbeat=self.now - timedelta(minutes=10))
        self.publish(self.now)
        latest = projection.read(self.root, now=self.now)
        self.assertEqual(latest["milestones"]["M1"]["compliance_pct"], 0)

        path = projection.snapshot_path(self.root)
        legacy = json.loads(path.read_text())
        legacy["milestones"]["M4"].update(claim_status="VERIFIED_CURRENT", state="GREEN", evidence_status="CURRENT")
        self.write_json(path, legacy)
        manifest_path = self.data / "deploy" / "CURRENT_STATE_V2.json"
        manifest = json.loads(manifest_path.read_text())
        self.write_json(manifest_path, {**manifest, "deployment_sha": "f" * 40})
        shown = projection.read(self.root, now=self.now)
        self.assertEqual(shown["milestones"]["M4"]["claim_status"], "STALE")
        self.assertEqual(shown["summary"]["milestones_green"], 0)


class Scheduler(unittest.TestCase):
    def test_actual_cadence_and_closed_reuse(self):
        now = datetime(2026, 11, 22, 19, 25, tzinfo=timezone.utc)
        captured = now - timedelta(seconds=400)
        self.assertEqual(scheduler.freshness_budget(captured, now, 32), 855)
        self.assertEqual(scheduler.freshness_budget(captured, now, 2), 315)
        open_at = datetime(2026, 11, 20, 15, 25, tzinfo=timezone.utc)
        self.assertEqual(scheduler.freshness_budget(open_at, open_at, 32), 315)

    def test_monotonic_deadlines_never_invent_a_clock(self):
        now = datetime(2026, 11, 22, 19, 25, tzinfo=timezone.utc)
        self.assertEqual(scheduler.monotonic_target("1min 15s 500ms", wall=now, monotonic=70),
                         now + timedelta(seconds=5.5))
        for value in ("infinity", "0", "11s invalid", "1s", None):
            self.assertIsNone(scheduler.monotonic_target(value, wall=now, monotonic=70))
