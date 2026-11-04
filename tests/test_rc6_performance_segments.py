"""Offline, synthetic crash/continuity regressions for 128-MiB evidence rollover."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rc6_performance.capture import BoundedCapture, EvidenceStore, ExitTelemetry
from rc6_performance.common import canonical, digest
from rc6_performance.report import evidence_report
from rc6_performance.segments import evidence_paths, manifest_path


AT = "2026-11-02T16:25:00-03:00"


def source_db(path):
    with closing(sqlite3.connect(path)) as c:
        c.execute("PRAGMA journal_mode=WAL")
        with c:
            c.execute("CREATE TABLE observer_state(id INTEGER PRIMARY KEY, mode TEXT, real_orders_sent INTEGER)")
            c.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
            c.execute("CREATE TABLE market_snapshots(value TEXT)")


def source_row(path, value):
    with closing(sqlite3.connect(path)) as c, c:
        c.execute("INSERT INTO market_snapshots VALUES(?)", (value,))


def fill_to_limit(store, connection_factory=None):
    open_connection = connection_factory or store.connect
    for ordinal in range(100):
        with closing(open_connection()) as c:
            payload = canonical({"_rowid": -ordinal-1, "value": "x"*15000})
            c.execute("INSERT INTO events VALUES(NULL,?,?,?,?,?,?)",
                      ("synthetic-old-" + str(ordinal), "market_snapshots", -ordinal-1,
                       AT, hashlib.sha256(payload.encode()).hexdigest(), payload))
            c.commit()
        if store.at_capacity():
            return
    raise AssertionError("test evidence quota not reached")


class EvidenceSegmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.db"
        self.evidence = self.root / "evidence.db"
        source_db(self.source)
        self.store = EvidenceStore(self.evidence, maximum_bytes=262144)
        self.capture = BoundedCapture(self.source, self.store)
        self.assertEqual(self.capture.read_batch()["rows"], 0)

    def test_rollover_keeps_original_wal_cursors_and_reports_all_events(self):
        source_row(self.source, "first")
        self.assertEqual(self.capture.read_batch()["rows"], 1)
        telemetry = ExitTelemetry(self.evidence)
        telemetry.record("paper-1", "CONDITION", AT, "OLD_SHARD")
        fill_to_limit(self.store)
        with closing(sqlite3.connect(self.evidence)) as c:
            old_count = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        old_bytes = self.evidence.stat().st_size
        source_row(self.source, "second")
        self.assertEqual(self.capture.read_batch()["rows"], 1)
        shards = evidence_paths(self.evidence)
        self.assertEqual(len(shards), 2)
        self.assertTrue(self.evidence.exists())
        self.assertGreaterEqual(self.evidence.stat().st_size, old_bytes)
        with closing(self.store.connect()) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            self.assertEqual(c.execute(
                "SELECT last_rowid FROM cursors WHERE source_table='market_snapshots'"
            ).fetchone()[0], 2)
        report = evidence_report(self.evidence)
        self.assertEqual(report["selection"]["captured_events"], old_count+1)
        self.assertEqual(report["selection"]["evidence_segments"], 2)
        self.assertEqual(self.capture.read_batch()["rows"], 0)
        restarted = BoundedCapture(self.source, EvidenceStore(self.evidence,
                                 maximum_bytes=262144))
        self.assertEqual(restarted.read_batch()["rows"], 0)
        source_row(self.source, "third")
        self.assertEqual(restarted.read_batch()["rows"], 1)
        telemetry.record("paper-1", "INTENT_COMMITTED", AT, "NEW_SHARD")
        with closing(sqlite3.connect(shards[0])) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM exit_probes").fetchone()[0], 1)
        with closing(sqlite3.connect(shards[1])) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM exit_probes").fetchone()[0], 1)

    def test_atomic_manifest_failure_keeps_previous_evidence_readable(self):
        fill_to_limit(self.store)
        with patch("rc6_performance.segments.os.replace", side_effect=OSError("simulated crash")):
            with self.assertRaisesRegex(OSError, "simulated crash"):
                self.store.ensure_writable()
        self.assertEqual(evidence_paths(self.evidence), (self.evidence,))
        self.assertFalse(manifest_path(self.evidence).exists())
        self.assertGreater(evidence_report(self.evidence)["selection"]["captured_events"], 0)
        # The abandoned empty shard must not be deleted or mistaken for active.
        self.assertTrue(Path(str(self.evidence) + ".segment.000001.sqlite").is_file())
        self.assertTrue(self.store.ensure_writable())
        shards = evidence_paths(self.evidence)
        self.assertEqual(len(shards), 2)
        self.assertTrue(shards[-1].name.endswith(".segment.000002.sqlite"))

    def test_late_old_shard_probe_and_committed_wal_remain_visible(self):
        old_writer = self.store.connect()
        self.addCleanup(old_writer.close)
        old_writer.execute("PRAGMA wal_autocheckpoint=0")
        payload = canonical({"_rowid": -77, "value": "committed_in_WAL"})
        old_writer.execute("INSERT INTO events VALUES(NULL,?,?,?,?,?,?)",
                           ("old-wal-key", "market_snapshots", -77, AT,
                            hashlib.sha256(payload.encode()).hexdigest(), payload))
        old_writer.commit()
        fill_to_limit(self.store)
        self.assertTrue(self.store.ensure_writable())
        self.assertEqual(len(evidence_paths(self.evidence)), 2)
        # A writer that opened its connection before handoff may finish later.
        old_writer.execute("INSERT INTO exit_probes VALUES(?,?,?,?)",
                           ("paper-late", "CONDITION", AT, canonical({"from": "OLD"})))
        old_writer.commit()
        ExitTelemetry(self.evidence).record("paper-late", "CONDITION",
                                           "2026-11-02T16:26:00-03:00", "NEW")
        with patch("rc6_performance.report.exit_latencies", return_value={}) as metrics:
            report = evidence_report(self.evidence)
        self.assertGreater(report["selection"]["captured_events"], 0)
        self.assertEqual(report["selection"]["cross_segment_probe_duplicates"], 1)
        visible = metrics.call_args.args[0]
        selected = [p for p in visible if p["paper_id"] == "paper-late"]
        self.assertEqual(len(selected), 1)
        self.assertEqual(json.loads(selected[0]["detail_json"])["from"], "OLD")
        # Old WAL must not be read with immutable=1, which hides live commits.
        self.assertEqual(sum(
            sqlite3.connect(p).execute(
                "SELECT COUNT(*) FROM events WHERE event_key='old-wal-key'"
            ).fetchone()[0] for p in evidence_paths(self.evidence)), 1)

    def test_cross_segment_duplicate_capture_key_fails_closed(self):
        source_row(self.source, "first")
        self.assertEqual(self.capture.read_batch()["rows"], 1)
        fill_to_limit(self.store)
        self.assertTrue(self.store.ensure_writable())
        with closing(sqlite3.connect(self.evidence)) as old:
            row = old.execute("SELECT event_key,source_table,source_rowid,recorded_at,"
                              "payload_sha256,payload_json FROM events ORDER BY id LIMIT 1").fetchone()
        with closing(self.store.connect()) as latest, latest:
            latest.execute("INSERT INTO events VALUES(NULL,?,?,?,?,?,?)", row)
        with self.assertRaisesRegex(ValueError, "DUPLICATE_EVENT_KEY"):
            evidence_report(self.evidence)

    def test_missing_or_ambiguous_manifest_shards_fail_closed(self):
        manifest = manifest_path(self.evidence)
        manifest.write_text(json.dumps({
            "schema": "rc6.performance-segments.v1",
            "segments": [self.evidence.name, "../untrusted.db"],
            "active": "../untrusted.db"
        }))
        with self.assertRaisesRegex(ValueError, "MANIFEST"):
            evidence_report(self.evidence)
        manifest.write_text("{bad-json")
        with self.assertRaisesRegex(ValueError, "MANIFEST"):
            self.store.connect()
        manifest.unlink()
        target = self.root / "other.json"
        target.write_text("{}")
        manifest.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "SYMLINK"):
            evidence_paths(self.evidence)

    def test_manifest_switch_during_report_is_rejected(self):
        legitimate = evidence_paths(self.evidence)
        with patch("rc6_performance.report.evidence_paths",
                   side_effect=[legitimate, legitimate + legitimate]):
            with self.assertRaisesRegex(ValueError, "CHANGED_DURING_REPORT"):
                evidence_report(self.evidence)

    def test_missing_manifest_listed_segment_is_never_silently_skipped(self):
        successor = self.evidence.name + ".segment.000001.sqlite"
        manifest_path(self.evidence).write_text(json.dumps({
            "schema": "rc6.performance-segments.v1",
            "segments": [self.evidence.name, successor],
            "active": successor,
        }))
        with self.assertRaisesRegex(ValueError, "MISSING_OR_SYMLINK"):
            evidence_report(self.evidence)
        with self.assertRaisesRegex(ValueError, "MISSING_OR_SYMLINK"):
            EvidenceStore(self.evidence)

    def test_rollover_between_source_read_and_capture_commit_keeps_cursor(self):
        source_row(self.source, "first")
        from rc6_performance import capture as capture_module
        guarded = capture_module.capture_write_guard

        @contextmanager
        def concurrent_telemetry_rollover(base):
            # Simulate telemetry deciding to rotate after source cursor was
            # read, but before the capture's guarded target commit.
            fill_to_limit(self.store)
            self.assertTrue(self.store.ensure_writable())
            with guarded(base):
                yield

        with patch.object(capture_module, "capture_write_guard",
                          side_effect=concurrent_telemetry_rollover):
            self.assertEqual(self.capture.read_batch()["rows"], 1)
        self.assertEqual(len(evidence_paths(self.evidence)), 2)
        self.assertEqual(self.capture.read_batch()["rows"], 0)
        with closing(self.store.connect()) as c:
            self.assertEqual(c.execute(
                "SELECT last_rowid FROM cursors WHERE source_table='market_snapshots'"
            ).fetchone()[0], 1)

    def test_stale_target_connection_cannot_commit_capture_cursor(self):
        source_row(self.source, "first")
        fill_to_limit(self.store)
        self.assertTrue(self.store.ensure_writable())
        stale = sqlite3.connect(self.evidence)
        self.addCleanup(stale.close)
        genuine_connect = self.store.connect
        attempts = 0

        def late_old_target():
            nonlocal attempts
            attempts += 1
            return stale if attempts == 2 else genuine_connect()

        with patch.object(self.store, "connect", side_effect=late_old_target):
            with self.assertRaisesRegex(ValueError, "STALE_SEGMENT"):
                self.capture.read_batch()
        self.assertEqual(self.capture.read_batch()["rows"], 1)
        self.assertEqual(self.capture.read_batch()["rows"], 0)

    def test_late_unguarded_legacy_capture_is_detected_not_double_counted(self):
        source_row(self.source, "first")
        old_writer = sqlite3.connect(self.evidence)
        self.addCleanup(old_writer.close)
        fill_to_limit(self.store)
        self.assertTrue(self.store.ensure_writable())
        # Simulate a legacy/uncoordinated writer (NOT the new guarded capture)
        # completing after handoff with both event and stale cursor.
        payload = canonical({"_rowid": 1, "value": "first"})
        event_key = digest([self.capture.source_key, "market_snapshots", 1])
        old_writer.execute("INSERT INTO events VALUES(NULL,?,?,?,?,?,?)",
                           (event_key, "market_snapshots", 1, AT,
                            hashlib.sha256(payload.encode()).hexdigest(), payload))
        old_writer.execute("INSERT OR REPLACE INTO cursors VALUES(?,?,?)",
                           (self.capture.source_key, "market_snapshots", 1))
        old_writer.commit()
        self.assertEqual(self.capture.read_batch()["rows"], 1)
        with self.assertRaisesRegex(ValueError, "DUPLICATE_EVENT_KEY"):
            evidence_report(self.evidence)

    def test_disk_low_water_alarms_without_evidence_purge(self):
        fill_to_limit(self.store)
        source_row(self.source, "pending")
        with patch("rc6_performance.segments.shutil.disk_usage",
                   return_value=SimpleNamespace(free=512*1024*1024)):
            report = self.capture.read_batch()
        self.assertEqual(report["status"], "EVIDENCE_DISK_RESERVE_REQUIRED")
        self.assertFalse(manifest_path(self.evidence).exists())
        with closing(sqlite3.connect(self.evidence)) as c:
            count = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertGreater(count, 0)
        self.assertEqual(self.capture.read_batch()["rows"], 1)
        self.assertEqual(evidence_report(self.evidence)["selection"]["captured_events"], count+1)

    def test_tiny_quota_stays_fail_closed_without_creating_empty_shards(self):
        tiny = EvidenceStore(self.root/"tiny.db", maximum_bytes=1)
        self.assertFalse(tiny.ensure_writable())
        self.assertEqual(evidence_paths(tiny.path), (tiny.path,))


if __name__ == "__main__":
    unittest.main()
