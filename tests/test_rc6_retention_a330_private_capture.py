"""Synthetic A330 security contracts. Never SSH or touch live host/PPI Watch."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/rc6_retention_a330_private_capture.py"
WORKFLOW = ROOT / ".github/workflows/rc6-retention-private-rehearsal.yml"
SPEC = importlib.util.spec_from_file_location("a330_private", SCRIPT)
assert SPEC and SPEC.loader
a330 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(a330)


class SyntheticPrivateCapture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rc6-a330-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "market_history.db"
        self.observer = self.root / "observer_v17.db"
        self.data = self.root / "data"
        self.private = self.root / "private"
        self.private.mkdir(mode=0o700)
        for sub in ("receipts", "artifacts", "replays"):
            (self.data / "paper_v17" / sub).mkdir(parents=True)
        with sqlite3.connect(self.source) as db:
            db.execute('''CREATE TABLE history_versions_v2 (
                id INTEGER PRIMARY KEY, symbol TEXT, instrument_type TEXT,
                market TEXT, settlement TEXT, date TEXT, source TEXT,
                metadata_json TEXT)''')
            db.execute("CREATE TABLE history_canonical_v2(version_id INTEGER)")
            for ident in range(1, 9):
                db.execute('INSERT INTO history_versions_v2 VALUES (?,?,?,?,?,?,?,?)',
                           (ident, "ABC", "ACCIONES", "BYMA", "T2", "2024-01-01",
                            "PPI", "{}"))
            db.execute("INSERT INTO history_canonical_v2 VALUES (2)")
        with sqlite3.connect(self.observer) as db:
            db.execute("CREATE TABLE external_event (history_version_id INTEGER)")
            db.execute("INSERT INTO external_event VALUES (1)")
        (self.data / "paper_v17/receipts/r.json").write_text(
            json.dumps({"history_version_id": 3}))
        (self.data / "paper_v17/artifacts/a.json").write_text(
            json.dumps({"rows": [{"version_id": 4}]}))
        (self.data / "paper_v17/replays/replay.json").write_text(
            json.dumps({"replay": {"history_version_id": 5}}))
        self.gate = {"gated_at_utc": "2026-11-01T18:00:00+00:00",
                     "source_stat": a330.check_quiet_file(self.source, 1024**3,
                                                          sqlite_file=True)}

    def make_bundle(self):
        archive = io.BytesIO()
        receipt = a330.export_bundle(self.source, self.observer, self.data,
                                     archive, time.monotonic() + 30, self.gate)
        path = self.private / "bundle.tar"
        path.write_bytes(archive.getvalue())
        log = self.private / "capture.log"
        log.write_text("A330_CAPTURE_RECEIPT=" +
                       json.dumps(receipt, sort_keys=True) + "\n")
        return path, log, receipt

    def test_host_export_uses_readonly_source_and_no_false_pit(self):
        original = self.source.read_bytes()
        bundle, _, receipt = self.make_bundle()
        self.assertEqual(receipt["source_sha256"], hashlib.sha256(original).hexdigest())
        self.assertTrue(receipt["second_full_hash_equal"])
        self.assertFalse(receipt["host_purge_allowed"])
        self.assertEqual(original, self.source.read_bytes())
        with tarfile.open(bundle) as tar:
            self.assertEqual(set(tar.getnames()), set(a330.ALLOWED_MEMBERS))
            pins = json.load(tar.extractfile(a330.PINS_MEMBER))
            self.assertEqual(pins["sqlite"]["pins"], [1])
            self.assertEqual(pins["json"]["pins"], [3])
            self.assertEqual(pins["artifact"]["pins"], [4])
            self.assertEqual(pins["replay"]["pins"], [5])
            self.assertFalse(pins["provider_capture_pit_authenticated"])
            self.assertFalse(pins["external_consumer_universe_verified"])

    def test_runner_missing_a319_fails_closed_and_keeps_private(self):
        bundle, log, _ = self.make_bundle()
        with self.assertRaisesRegex(a330.GuardError, "A319_NOT_REGISTERED_IN_MAIN"):
            a330.runner_rehearsal(bundle, log, self.private, self.root,
                                  deadline=time.monotonic() + 30)
        self.assertTrue((self.private / a330.SOURCE_MEMBER).exists())

    def test_runner_builds_manifest_and_delegates_copy_only_rehearsal(self):
        bundle, log, _ = self.make_bundle()
        class FakeA319:
            @staticmethod
            def audit(root, source, source_sha, manifest, manifest_sha,
                      as_of, *, simulate, budget_seconds, index_output):
                document = json.loads((root / manifest).read_text())
                assert document["schema"] == a330.MANIFEST_SCHEMA
                assert [spec["kind"] for spec in document["snapshots"]] == [
                    "sqlite", "json", "artifact", "replay"]
                assert document["provenance"] == []
                assert hashlib.sha256((root / source).read_bytes()).hexdigest() == source_sha
                assert hashlib.sha256((root / manifest).read_bytes()).hexdigest() == manifest_sha
                assert simulate and index_output.endswith(".private.jsonl")
                return {"host_purge_allowed": False, "physical_host_delete_count": 0,
                        "state": "OFFHOST_CENSUS_COMPLETE_HOLD",
                        "decision_counts": {"HOLD_PROVENANCE_PIT": 6},
                        "per_row_index": {"sha256": "f" * 64, "rows": 8}}
        with patch.object(a330, "a319_module", return_value=FakeA319):
            summary = a330.runner_rehearsal(bundle, log, self.private, self.root,
                                           deadline=time.monotonic() + 30)
        self.assertEqual(summary["state"], "NO_VERIFICADO")
        self.assertEqual(summary["indexed_versions"], 8)
        self.assertEqual(summary["crossstore_scanned_kinds"], [
            "artifact", "json", "replay", "sqlite"])
        self.assertFalse(summary["host_purge_allowed"])
        self.assertFalse(summary["provenance_pit_authenticated"])
        self.assertEqual(self.source.read_bytes(), (self.private / a330.SOURCE_MEMBER).read_bytes())

    def test_receipt_hash_tamper_fails_closed(self):
        bundle, log, _ = self.make_bundle()
        log.write_text(log.read_text().replace('"source_sha256": "',
                                               '"source_sha256": "a'))
        with self.assertRaisesRegex(a330.GuardError, "A330_CAPTURE_RECEIPT_INVALID"):
            a330.runner_rehearsal(bundle, log, self.private, self.root,
                                  deadline=time.monotonic() + 30)

    def test_valid_shape_wrong_source_hash_rejected_after_extraction(self):
        bundle, log, receipt = self.make_bundle()
        receipt["source_sha256"] = "a" * 64
        log.write_text("A330_CAPTURE_RECEIPT=" + json.dumps(receipt) + "\n")
        with self.assertRaisesRegex(a330.GuardError,
                                    "A330_HOST_SOURCE_SHA_NOT_REPRODUCED"):
            a330.runner_rehearsal(bundle, log, self.private, self.root,
                                  deadline=time.monotonic() + 30)

    def test_source_symlink_and_wal_are_host_export_hard_stops(self):
        (self.root / "market_history.db-wal").touch()
        with self.assertRaisesRegex(a330.GuardError, "A330_SQLITE_SIDECAR_PRESENT"):
            a330.export_bundle(self.source, self.observer, self.data, io.BytesIO(),
                               time.monotonic() + 30, self.gate)
        (self.root / "market_history.db-wal").unlink()
        self.source.rename(self.root / "original-history.db")
        self.source.symlink_to(self.root / "original-history.db")
        with self.assertRaisesRegex(a330.GuardError, "A330_SOURCE_ALIAS"):
            a330.export_bundle(self.source, self.observer, self.data, io.BytesIO(),
                               time.monotonic() + 30, self.gate)

    def test_source_inplace_write_during_tar_stream_detected(self):
        original_read = a330.DigestReader.read
        mutated = False

        def concurrent_write(reader, size=-1):
            nonlocal mutated
            data = original_read(reader, size)
            if data and not mutated:
                mutated = True
                with sqlite3.connect(self.source) as db:
                    db.execute("UPDATE history_versions_v2 SET symbol='CHANGED' WHERE id=6")
            return data

        with patch.object(a330.DigestReader, "read", concurrent_write):
            with self.assertRaisesRegex(a330.GuardError,
                                        "A330_SOURCE_CHANGED_AFTER_STREAM"):
                a330.export_bundle(self.source, self.observer, self.data,
                                   io.BytesIO(), time.monotonic() + 30, self.gate)

    def test_bad_tar_traversal_member_fails_before_write(self):
        malicious = self.private / "malicious.tar"
        with tarfile.open(malicious, "w") as tar:
            data = b"bad"
            item = tarfile.TarInfo("../../outside.sqlite")
            item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
        with self.assertRaisesRegex(a330.GuardError, "A330_TAR_UNEXPECTED_MEMBER"):
            a330.extract_bundle(malicious, self.private, time.monotonic() + 30)
        self.assertFalse((self.root / "outside.sqlite").exists())

    def test_duplicate_tar_member_rejected(self):
        malicious = self.private / "duplicate.tar"
        with tarfile.open(malicious, "w") as tar:
            for _ in range(2):
                data = b"some private manifest"
                item = tarfile.TarInfo(a330.PINS_MEMBER)
                item.size = len(data)
                tar.addfile(item, io.BytesIO(data))
        with self.assertRaisesRegex(a330.GuardError, "A330_TAR_UNEXPECTED_MEMBER"):
            a330.extract_bundle(malicious, self.private, time.monotonic() + 30)

    def test_tar_symlink_member_rejected(self):
        malicious = self.private / "symlink.tar"
        with tarfile.open(malicious, "w") as tar:
            item = tarfile.TarInfo(a330.SOURCE_MEMBER)
            item.type = tarfile.SYMTYPE
            item.linkname = "/opt/porota-trading/data/market_history.db"
            tar.addfile(item)
        with self.assertRaisesRegex(a330.GuardError, "A330_TAR_UNEXPECTED_MEMBER"):
            a330.extract_bundle(malicious, self.private, time.monotonic() + 30)

    def test_json_duplicate_key_and_ambiguous_id_fail_closed(self):
        with self.assertRaisesRegex(a330.GuardError, "A330_JSON_DUPLICATE_KEY"):
            a330.decode_json(b'{"version_id":4,"version_id":6}')
        with self.assertRaisesRegex(a330.GuardError, "A330_VERSION_REFERENCE_AMBIGUOUS"):
            a330.json_pin_ids({"version_id": "not-an-integer"}, time.monotonic() + 3)
        with self.assertRaisesRegex(a330.GuardError, "A330_VERSION_REFERENCE_AMBIGUOUS"):
            a330.version_id("9999999999999999999")
        self.assertEqual(a330.json_pin_ids({"versions": [8]},
                                           time.monotonic() + 3), {8})

    def test_unavailable_replay_is_distinguished_from_empty_pinset(self):
        self.assertEqual(a330.scan_json_group(self.root / "missing", time.monotonic() + 3),
                         {"state": "UNAVAILABLE", "files": 0, "pins": []})

    def test_external_observer_wal_guard(self):
        (self.root / "observer_v17.db-wal").touch()
        with self.assertRaisesRegex(a330.GuardError, "A330_SQLITE_SIDECAR_PRESENT"):
            a330.scan_observer_refs(self.observer, time.monotonic() + 30)

    def test_host_gate_closed_paper_zero_timer_and_heartbeat_negatives(self):
        with sqlite3.connect(self.observer) as db:
            db.execute('''CREATE TABLE observer_state (
                id INTEGER PRIMARY KEY, mode TEXT, real_orders_sent INTEGER,
                session_state TEXT, heartbeat_at TEXT)''')
            db.execute('INSERT INTO observer_state VALUES (1,?,?,?,?)',
                       ("PRODUCTION_PAPER", 0, "MARKET_CLOSED",
                        datetime.now(timezone.utc).isoformat()))

        def quiet_systemctl(args, **_):
            if args[1] == "show":
                return SimpleNamespace(returncode=0,
                                       stdout="ActiveState=inactive\nUnitFileState=masked\n")
            self.assertEqual(args[:2], ["systemctl", "is-active"])
            return SimpleNamespace(returncode=3, stdout="inactive\n")

        with (patch.object(a330, "available_memory", return_value=8 * 1024**3),
              patch.object(a330.shutil, "disk_usage",
                           return_value=SimpleNamespace(free=8 * 1024**3)),
              patch.object(a330.subprocess, "run", side_effect=quiet_systemctl)):
            gate = a330.host_gate(self.source, self.observer)
            self.assertTrue(gate["paper_zero_closed"])
            with sqlite3.connect(self.observer) as db:
                db.execute("UPDATE observer_state SET real_orders_sent=1")
            with self.assertRaisesRegex(a330.GuardError,
                                        "A330_CLOSED_PAPER_ZERO_REQUIRED"):
                a330.host_gate(self.source, self.observer)
            with sqlite3.connect(self.observer) as db:
                db.execute("UPDATE observer_state SET real_orders_sent=0, heartbeat_at=?",
                           ("2026-10-01T00:00:00+00:00",))
            with self.assertRaisesRegex(a330.GuardError, "A330_HEARTBEAT_STALE"):
                a330.host_gate(self.source, self.observer)

        def bad_timer(args, **_):
            return SimpleNamespace(returncode=0,
                                   stdout="ActiveState=active\nUnitFileState=enabled\n")

        with (patch.object(a330, "available_memory", return_value=8 * 1024**3),
              patch.object(a330.shutil, "disk_usage",
                           return_value=SimpleNamespace(free=8 * 1024**3)),
              patch.object(a330.subprocess, "run", side_effect=bad_timer)):
            with self.assertRaisesRegex(a330.GuardError,
                                        "A330_A3_TIMER_NOT_QUARANTINED"):
                a330.host_gate(self.source, self.observer)

    def test_ssh_capture_failure_classes_are_bounded_and_not_a_purge_go(self):
        valid = b"private diagnostic\nA330_REJECTED=A330_SQLITE_SIDECAR_PRESENT\n"
        scenarios = [
            (124, valid, 0, "SSH_TIMEOUT_124"),
            (255, valid, 0, "SSH_EXIT_255_TRANSPORT_OR_REMOTE"),
            (2, valid, 0, "REMOTE_GUARD_A330_SQLITE_SIDECAR_PRESENT"),
            (2, b"A330_REJECTED=OSError\n", 0, "REMOTE_EXCEPTION_OSERROR"),
            (2, b"transient private error\n", 0, "REMOTE_EXIT_UNCLASSIFIED"),
            (2, None, 0, "REMOTE_LOG_UNAVAILABLE"),
            (2, valid * 2, 0, "REMOTE_REJECTION_AMBIGUOUS"),
            (2, b"A330_REJECTED=NOT_A_GUARD\n", 0,
             "REMOTE_REJECTION_UNALLOWLISTED"),
            (2, b"prefix A330_REJECTED=A330_SQLITE_SIDECAR_PRESENT\n", 0,
             "REMOTE_REJECTION_MALFORMED"),
            (1, valid, 0, "REMOTE_REJECTION_EXIT_MISMATCH"),
            (2, b"A330_REJECTED=A330_SQLITE_SIDECAR_PRESENT\x00", 0,
             "REMOTE_LOG_NON_TEXT"),
            (2, b"\xff", 0, "REMOTE_LOG_NON_UTF8"),
            (2, b"x" * (a330.PRIVATE_FAILURE_LOG_BUDGET + 1), 0,
             "REMOTE_LOG_OVERSIZED"),
            (0, b"", 0, "ARTIFACT_EMPTY"),
            (0, b"", 1, "CLASSIFIER_NOT_APPLICABLE"),
        ]
        for code, private, size, expected in scenarios:
            with self.subTest(code=code, expected=expected):
                receipt = a330.sanitized_capture_failure(code, private, size)
                self.assertEqual(receipt["failure_class"], expected)
                self.assertEqual(receipt["state"], "NO_VERIFICADO")
                self.assertFalse(receipt["host_purge_allowed"])
                self.assertEqual(receipt["physical_host_delete_count"], 0)
                self.assertEqual(receipt["real_orders_sent_by_action"], 0)
                self.assertNotIn("private diagnostic", json.dumps(receipt))

    def test_private_failure_log_strict_path_nofollow_and_size_limit(self):
        log = self.private / "a330-private.log"
        log.write_bytes(b"A330_REJECTED=A330_HOST_MEMORY_RESERVE\n")
        self.assertEqual(a330.bounded_private_failure_log(log, self.private),
                         log.read_bytes())
        log.unlink()
        self.assertIsNone(a330.bounded_private_failure_log(log, self.private))
        outside = self.root / "not-private.log"
        outside.write_text("not safe")
        log.symlink_to(outside)
        self.assertIsNone(a330.bounded_private_failure_log(log, self.private))
        log.unlink()
        log.write_bytes(b"S" * (a330.PRIVATE_FAILURE_LOG_BUDGET + 1000))
        bounded = a330.bounded_private_failure_log(log, self.private)
        self.assertEqual(len(bounded), a330.PRIVATE_FAILURE_LOG_BUDGET + 1)
        self.assertEqual(a330.classify_capture_failure(2, bounded, 0),
                         "REMOTE_LOG_OVERSIZED")
        with self.assertRaisesRegex(a330.GuardError,
                                    "A330_CLASSIFIER_PRIVATE_PATH_INVALID"):
            a330.bounded_private_failure_log(outside, self.private)

    def test_failure_classifier_cli_writes_only_sanitized_enum(self):
        log = self.private / "a330-private.log"
        secret = "PRIVATE_HOST_DIAGNOSTIC_SECRET_445"
        log.write_text(secret + "\nA330_REJECTED=A330_HOST_DISK_RESERVE\n")
        bundle = self.private / "a330-private.tar"
        bundle.touch()
        receipt = self.private / "a330-sanitized.json"
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["a330", "--classify-capture-failure", "--capture-exit-code", "2",
                "--bundle", str(bundle), "--capture-log", str(log),
                "--runner-root", str(self.private),
                "--sanitized-output", str(receipt)]
        with patch.object(sys, "argv", argv), redirect_stdout(stdout), redirect_stderr(stderr):
            a330.main()
        result = json.loads(receipt.read_text())
        self.assertEqual(result["failure_class"],
                         "REMOTE_GUARD_A330_HOST_DISK_RESERVE")
        combined = stdout.getvalue() + stderr.getvalue() + receipt.read_text()
        self.assertNotIn(secret, combined)
        self.assertNotIn(str(log), combined)
        self.assertFalse(result["host_purge_allowed"])
        self.assertEqual(result["real_orders_sent_by_action"], 0)

    def test_failure_classifier_cli_empty_archive_and_missing_log(self):
        bundle = self.private / "a330-private.tar"
        bundle.touch()
        receipt = self.private / "a330-sanitized.json"
        argv = ["a330", "--classify-capture-failure", "--capture-exit-code", "0",
                "--bundle", str(bundle), "--capture-log",
                str(self.private / "a330-private.log"),
                "--runner-root", str(self.private),
                "--sanitized-output", str(receipt)]
        with patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
            a330.main()
        result = json.loads(receipt.read_text())
        self.assertEqual(result["failure_class"], "ARTIFACT_EMPTY")
        self.assertEqual(result["state"], "NO_VERIFICADO")
        self.assertFalse(result["host_purge_allowed"])
        self.assertEqual(result["physical_host_delete_count"], 0)

    def test_workflow_is_manual_main_runner_only_without_deploy(self):
        body = WORKFLOW.read_text()
        self.assertIn("workflow_dispatch:", body)
        self.assertNotIn("\n  push:", body)
        self.assertNotIn("\n  schedule:", body)
        self.assertIn("github.ref == 'refs/heads/main'", body)
        self.assertIn("inputs.mode == 'REHEARSAL_ONLY'", body)
        self.assertIn("scripts/rc6_retention_a319_offhost.py", body)
        self.assertIn("--host-export", body)
        self.assertIn("--runner-rehearsal", body)
        self.assertIn("--classify-capture-failure", body)
        self.assertIn("--capture-exit-code \"$capture_rc\"", body)
        self.assertNotIn('"failure_class":"HOST_CAPTURE_FAILED"', body)
        self.assertIn('2> "$root/a330-private.log"', body)
        self.assertIn("StrictHostKeyChecking=yes", body)
        self.assertIn("permissions:\n  contents: read", body)
        self.assertIn("if: always()", body)
        self.assertNotIn("sudo systemctl", body)
        self.assertNotIn("git push", body)
        self.assertNotIn("docker compose", body)
        self.assertNotIn("rm -rf", body)


if __name__ == "__main__":
    unittest.main()
