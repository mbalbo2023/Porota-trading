"""Synthetic A330 security contracts. Never SSH or touch live host/PPI Watch."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
import os
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
HISTORY_WORKFLOW = ROOT / ".github/workflows/rc6-a500-retention-history-copy-only.yml"
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

    def test_a500_history_gate_checks_both_writers_and_masked_timers(self):
        calls = []

        def quiet(args, **_):
            calls.append(tuple(args))
            if args[1] == "show":
                return SimpleNamespace(returncode=0,
                                       stdout="ActiveState=inactive\nUnitFileState=masked\n")
            return SimpleNamespace(returncode=3, stdout="inactive\n")

        with (patch.object(a330, "available_memory", return_value=8 * 1024**3),
              patch.object(a330.shutil, "disk_usage",
                           return_value=SimpleNamespace(free=8 * 1024**3)),
              patch.object(a330.subprocess, "run", side_effect=quiet),
              patch.object(a330.sqlite3, "connect",
                           side_effect=AssertionError("observer must not open"))):
            gate = a330.host_history_gate(self.source)
        self.assertEqual(gate["paper_runtime_state"], "NO_VERIFICADO")
        self.assertTrue(gate["both_history_writers_inactive"])
        self.assertEqual(len([call for call in calls if call[1] == "show"]), 3)
        writer_calls = [call[2] for call in calls if call[1] == "is-active"]
        self.assertEqual(writer_calls, [
            "porota-local-eod-history-rc6.service",
            "porota-history-postclose-rc6.service"])

    def test_a500_any_active_or_unavailable_history_writer_fails_closed(self):
        for failing in ("porota-local-eod-history-rc6.service",
                        "porota-history-postclose-rc6.service"):
            for bad_state, code in (("active", 0), ("failed", 3),
                                    ("unknown", 4)):
                with self.subTest(failing=failing, state=bad_state):
                    def commands(args, **_):
                        if args[1] == "show":
                            return SimpleNamespace(returncode=0,
                                                   stdout="ActiveState=inactive\nUnitFileState=masked\n")
                        if args[2] == failing:
                            return SimpleNamespace(returncode=code,
                                                   stdout=bad_state + "\n")
                        return SimpleNamespace(returncode=3, stdout="inactive\n")

                    with (patch.object(a330, "available_memory",
                                       return_value=8 * 1024**3),
                          patch.object(a330.shutil, "disk_usage",
                                       return_value=SimpleNamespace(free=8 * 1024**3)),
                          patch.object(a330.subprocess, "run", side_effect=commands)):
                        with self.assertRaisesRegex(a330.GuardError,
                                                    "A500_HISTORY_WRITER_NOT_INACTIVE"):
                            a330.host_history_gate(self.source)

    def test_a500_history_source_wal_sidecar_and_timer_not_masked_fail(self):
        def valid(args, **_):
            if args[1] == "show":
                return SimpleNamespace(returncode=0,
                                       stdout="ActiveState=inactive\nUnitFileState=masked\n")
            return SimpleNamespace(returncode=3, stdout="inactive\n")

        with (patch.object(a330, "available_memory", return_value=8 * 1024**3),
              patch.object(a330.shutil, "disk_usage",
                           return_value=SimpleNamespace(free=8 * 1024**3)),
              patch.object(a330.subprocess, "run", side_effect=valid)):
            (self.root / "market_history.db-wal").touch()
            with self.assertRaisesRegex(a330.GuardError,
                                        "A330_SQLITE_SIDECAR_PRESENT"):
                a330.host_history_gate(self.source)
            (self.root / "market_history.db-wal").unlink()

        def bad_timer(args, **_):
            return SimpleNamespace(returncode=0,
                                   stdout="ActiveState=active\nUnitFileState=enabled\n")
        with (patch.object(a330, "available_memory", return_value=8 * 1024**3),
              patch.object(a330.shutil, "disk_usage",
                           return_value=SimpleNamespace(free=8 * 1024**3)),
              patch.object(a330.subprocess, "run", side_effect=bad_timer)):
            with self.assertRaisesRegex(a330.GuardError,
                                        "A330_A3_TIMER_NOT_QUARANTINED"):
                a330.host_history_gate(self.source)

    def test_a500_history_gate_resource_thresholds_fail_before_host_read(self):
        with (patch.object(a330.shutil, "disk_usage",
                           return_value=SimpleNamespace(free=4 * 1024**3 - 1)),
              patch.object(a330.subprocess, "run",
                           side_effect=AssertionError("no systemd calls expected"))):
            with self.assertRaisesRegex(a330.GuardError,
                                        "A330_HOST_DISK_RESERVE"):
                a330.host_history_gate(self.source)
        with (patch.object(a330.shutil, "disk_usage",
                           return_value=SimpleNamespace(free=4 * 1024**3)),
              patch.object(a330, "available_memory",
                           return_value=512 * 1024**2 - 1),
              patch.object(a330.subprocess, "run",
                           side_effect=AssertionError("no systemd calls expected"))):
            with self.assertRaisesRegex(a330.GuardError,
                                        "A330_HOST_MEMORY_RESERVE"):
                a330.host_history_gate(self.source)

    def make_a500_bundle(self):
        archive = io.BytesIO()
        receipt = a330.export_history_bundle(self.source, archive,
                                             time.monotonic() + 30, self.gate)
        bundle = self.private / "a330-private.tar"
        bundle.write_bytes(archive.getvalue())
        log = self.private / "a330-private.log"
        log.write_text("A330_CAPTURE_RECEIPT=" +
                       json.dumps(receipt, sort_keys=True) + "\n")
        return bundle, log, receipt

    def test_a500_live_wal_observer_never_opened_or_exported(self):
        observer_connection = sqlite3.connect(self.observer)
        try:
            self.assertEqual(observer_connection.execute(
                "PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            observer_connection.execute("INSERT INTO external_event VALUES (42)")
            observer_connection.commit()
            self.assertTrue((self.root / "observer_v17.db-wal").exists())
            self.assertTrue((self.root / "observer_v17.db-shm").exists())
            original_history = self.source.read_bytes()
            with patch.object(a330.sqlite3, "connect",
                              side_effect=AssertionError("live observer opened")):
                bundle, log, receipt = self.make_a500_bundle()
            self.assertFalse(receipt["observer_database_opened"])
            self.assertEqual(receipt["paper_runtime_state"], "NO_VERIFICADO")
            self.assertEqual(receipt["observer_pins_state"], "UNAVAILABLE")
            self.assertFalse(receipt["host_purge_allowed"])
            self.assertEqual(original_history, self.source.read_bytes())
            self.assertTrue((self.root / "observer_v17.db-wal").exists())
            self.assertTrue((self.root / "observer_v17.db-shm").exists())
            with tarfile.open(bundle) as tar:
                self.assertEqual(tar.getnames(), [a330.SOURCE_MEMBER, a330.PINS_MEMBER])
                self.assertEqual(tar.extractfile(a330.SOURCE_MEMBER).read(), original_history)
                pins = json.load(tar.extractfile(a330.PINS_MEMBER))
                self.assertEqual(pins["schema"], a330.HISTORY_PIN_SCHEMA)
                for kind in ("sqlite", "json", "artifact", "replay"):
                    self.assertEqual(pins[kind], {"state": "UNAVAILABLE", "pins": []})
            self.assertEqual(a330.read_receipt(log, history_only=True), receipt)
            with self.assertRaisesRegex(a330.GuardError,
                                        "A330_CAPTURE_RECEIPT_INVALID"):
                a330.read_receipt(log)  # A500 cannot impersonate A330
        finally:
            observer_connection.close()

    def test_a500_source_changed_during_stream_fails_closed(self):
        original_read = a330.DigestReader.read
        changed = False

        def concurrent_write(reader, size=-1):
            nonlocal changed
            block = original_read(reader, size)
            if block and not changed:
                changed = True
                with sqlite3.connect(self.source) as db:
                    db.execute("UPDATE history_versions_v2 SET symbol='WRITER' WHERE id=7")
            return block

        with patch.object(a330.DigestReader, "read", concurrent_write):
            with self.assertRaisesRegex(a330.GuardError,
                                        "A330_SOURCE_CHANGED_AFTER_STREAM"):
                a330.export_history_bundle(self.source, io.BytesIO(),
                                           time.monotonic() + 30, self.gate)

    def test_a500_runner_real_a319_census_keeps_missing_pins_on_hold(self):
        bundle, log, _ = self.make_a500_bundle()
        report = a330.runner_rehearsal(bundle, log, self.private, ROOT,
                                       deadline=time.monotonic() + 30,
                                       history_only=True)
        self.assertEqual(report["schema"], a330.HISTORY_SCHEMA)
        self.assertEqual(report["state"], "NO_VERIFICADO")
        self.assertEqual(report["paper_runtime_state"], "NO_VERIFICADO")
        self.assertEqual(report["observer_pins_state"], "UNAVAILABLE")
        self.assertEqual(report["indexed_versions"], 8)
        self.assertEqual(report["crossstore_scanned_kinds"], [])
        self.assertEqual(report["a319_decision_counts"].get("RETAIN_PINNED"), 1)
        self.assertEqual(report["a319_decision_counts"].get(
            "HOLD_MISSING_CROSS_STORE_COVERAGE"), 7)
        self.assertFalse(report["host_purge_allowed"])
        self.assertEqual(report["physical_host_delete_count"], 0)

    def test_a500_receipt_cannot_claim_paper_or_pins(self):
        _, log, receipt = self.make_a500_bundle()
        for key, value in (("paper_runtime_state", "PRODUCTION_PAPER"),
                           ("observer_pins_state", "SCANNED"),
                           ("observer_database_opened", True),
                           ("provider_capture_pit_authenticated", True)):
            with self.subTest(key=key):
                mutated = dict(receipt)
                mutated[key] = value
                log.write_text("A330_CAPTURE_RECEIPT=" + json.dumps(mutated) + "\n")
                with self.assertRaisesRegex(a330.GuardError,
                                            "A330_CAPTURE_RECEIPT_INVALID"):
                    a330.read_receipt(log, history_only=True)

    def test_a500_failure_classifier_private_redaction_and_no_host_purge(self):
        receipt = a330.sanitized_capture_failure(
            2, b"private-host-path\nA330_REJECTED=A500_HISTORY_WRITER_NOT_INACTIVE\n",
            0, history_only=True)
        self.assertEqual(receipt["schema"], a330.HISTORY_SCHEMA)
        self.assertEqual(receipt["failure_class"],
                         "REMOTE_GUARD_A500_HISTORY_WRITER_NOT_INACTIVE")
        self.assertEqual(receipt["paper_runtime_state"], "NO_VERIFICADO")
        self.assertFalse(receipt["host_purge_allowed"])
        self.assertNotIn("private-host-path", json.dumps(receipt))

    def test_a500_host_cli_no_observer_access_or_paper_claim(self):
        stdout_bytes = io.BytesIO()
        stdout = SimpleNamespace(buffer=stdout_bytes)
        stderr = io.StringIO()
        argv = ["a500", "--host-history-export"]
        with (patch.object(a330, "HOST_SOURCE", self.source),
              patch.object(a330, "host_history_gate", return_value=self.gate),
              patch.object(a330.sqlite3, "connect",
                           side_effect=AssertionError("unexpected sqlite open")),
              patch.object(sys, "stdout", stdout),
              patch.object(sys, "stderr", stderr),
              patch.object(sys, "argv", argv)):
            a330.main()
        receipts = [line.removeprefix("A330_CAPTURE_RECEIPT=")
                    for line in stderr.getvalue().splitlines()
                    if line.startswith("A330_CAPTURE_RECEIPT=")]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(json.loads(receipts[0])["schema"], a330.HISTORY_SCHEMA)
        with tarfile.open(fileobj=io.BytesIO(stdout_bytes.getvalue())) as tar:
            self.assertEqual(tar.getnames(), [a330.SOURCE_MEMBER, a330.PINS_MEMBER])

    def test_a500_runner_and_classifier_cli_mode_receipts(self):
        bundle, log, _ = self.make_a500_bundle()
        result_path = self.private / "a330-sanitized.json"
        argv = ["a500", "--runner-history-rehearsal", "--bundle", str(bundle),
                "--capture-log", str(log), "--runner-root", str(self.private),
                "--repo-root", str(ROOT), "--sanitized-output", str(result_path)]
        with (patch.object(sys, "argv", argv), redirect_stdout(io.StringIO())):
            a330.main()
        result = json.loads(result_path.read_text())
        self.assertEqual(result["schema"], a330.HISTORY_SCHEMA)
        self.assertFalse(result["host_purge_allowed"])

        # Separate fresh destination: the classifier must not overwrite a receipt.
        result_path.unlink()
        log.write_text("sensitive path\nA330_REJECTED=A500_HISTORY_WRITER_NOT_INACTIVE\n")
        argv = ["a500", "--classify-history-capture-failure",
                "--capture-exit-code", "2", "--bundle", str(bundle),
                "--capture-log", str(log), "--runner-root", str(self.private),
                "--sanitized-output", str(result_path)]
        with (patch.object(sys, "argv", argv), redirect_stdout(io.StringIO())):
            a330.main()
        result = json.loads(result_path.read_text())
        self.assertEqual(result["schema"], a330.HISTORY_SCHEMA)
        self.assertEqual(result["failure_class"],
                         "REMOTE_GUARD_A500_HISTORY_WRITER_NOT_INACTIVE")
        self.assertNotIn("sensitive path", result_path.read_text())

    def test_a500_new_workflow_manual_host_only_and_pr_contracts_only(self):
        body = HISTORY_WORKFLOW.read_text()
        self.assertIn("pull_request:", body)
        self.assertIn("workflow_dispatch:", body)
        self.assertNotIn("\n  push:", body)
        self.assertNotIn("\n  schedule:", body)
        self.assertIn("github.event_name == 'workflow_dispatch'", body)
        self.assertIn("github.ref == 'refs/heads/main'", body)
        self.assertIn("inputs.mode == 'HISTORY_COPY_ONLY'", body)
        self.assertIn("--host-history-export", body)
        self.assertIn("python3 -B - --host-history-export", body)
        self.assertIn("--runner-history-rehearsal", body)
        self.assertIn("--classify-history-capture-failure", body)
        self.assertIn("permissions:\n  contents: read", body)
        self.assertIn("path: ${{ env.RC6_A500_DIR }}/a330-sanitized.json", body)
        self.assertNotIn("path: ${{ env.RC6_A500_DIR }}/a330-private.tar", body)
        for forbidden in ("sudo systemctl", "docker compose", "rm -rf",
                          "--host-export'", "PPI Watch"):
            if forbidden != "PPI Watch":
                self.assertNotIn(forbidden, body)

    def test_a502_source_counts_only_with_live_observer_wal(self):
        observer = sqlite3.connect(self.observer)
        try:
            observer.execute("PRAGMA journal_mode=WAL")
            observer.execute("INSERT INTO external_event VALUES (88)")
            observer.commit()
            self.assertTrue(Path(str(self.observer) + "-wal").exists())
            before = self.source.read_bytes()
            gate = dict(self.gate, both_history_writers_inactive=True,
                        a3_timers_masked=True)
            report = a330.history_aggregate(self.source, gate=gate,
                                            deadline=time.monotonic() + 30)
            self.assertEqual(report["schema"], a330.AGGREGATE_SCHEMA)
            self.assertEqual(report["state"], "NO_VERIFICADO")
            self.assertEqual(report["total_versions"], 8)
            self.assertEqual(report["older_than_365d"], 8)
            self.assertEqual(report["within_365d"], 0)
            self.assertEqual(report["invalid_date"], 0)
            self.assertEqual(report["future_date"], 0)
            self.assertFalse(report["host_purge_allowed"])
            self.assertFalse(report["observer_database_opened"])
            self.assertFalse(report["provider_pit_authenticated"])
            self.assertEqual(report["real_orders_sent_by_action"], 0)
            self.assertEqual(self.source.read_bytes(), before)
            self.assertTrue(Path(str(self.observer) + "-wal").exists())
            public = json.dumps(report)
            for sensitive in ("ABC", "PPI", "2024-01-01", "observer_v17.db",
                              "market_history.db"):
                self.assertNotIn(sensitive, public)
        finally:
            observer.close()

    def test_a502_365_art_date_boundary_and_bad_dates(self):
        values = ("2026-11-03", "2025-11-04", "2025-11-03", "2026-11-04",
                  "unknown", None, "2020-01-01", "2026-11-02")
        with sqlite3.connect(self.source) as db:
            for index, value in enumerate(values, start=1):
                db.execute("UPDATE history_versions_v2 SET date=? WHERE id=?",
                           (value, index))
        gate = {"gated_at_utc": "2026-11-03T12:00:00Z",
                "source_stat": a330.check_quiet_file(self.source, 1024**3,
                                                      sqlite_file=True),
                "both_history_writers_inactive": True,
                "a3_timers_masked": True}
        result = a330.history_aggregate(self.source, gate=gate,
                                        deadline=time.monotonic() + 30)
        self.assertEqual({k: result[k] for k in
                          ("within_365d", "older_than_365d", "invalid_date",
                           "future_date")},
                         {"within_365d": 3, "older_than_365d": 2,
                          "invalid_date": 2, "future_date": 1})

    def test_a502_dangling_wal_and_unquiet_source_are_rejected(self):
        gate = dict(self.gate, both_history_writers_inactive=True,
                    a3_timers_masked=True)
        Path(str(self.source) + "-wal").symlink_to(self.root / "absent.db")
        with self.assertRaisesRegex(a330.GuardError,
                                    "A502_HISTORY_SIDECAR_PRESENT"):
            a330.history_aggregate(self.source, gate=gate,
                                   deadline=time.monotonic() + 5)
        Path(str(self.source) + "-wal").unlink()
        self.source.rename(self.root / "original.db")
        self.source.symlink_to(self.root / "original.db")
        with self.assertRaisesRegex(a330.GuardError, "A330_SOURCE_ALIAS"):
            a330.history_aggregate(self.source, gate=gate,
                                   deadline=time.monotonic() + 5)

    def test_a502_budget_writer_gate_and_source_metadata_change(self):
        gate = dict(self.gate, both_history_writers_inactive=True,
                    a3_timers_masked=True)
        with patch.object(a330, "AGGREGATE_MAX_ROWS", 7):
            with self.assertRaisesRegex(a330.GuardError, "A502_ROW_BUDGET"):
                a330.history_aggregate(self.source, gate=gate,
                                       deadline=time.monotonic() + 20)
        with self.assertRaisesRegex(a330.GuardError, "A502_TIME_BUDGET"):
            a330.history_aggregate(self.source, gate=gate,
                                   deadline=time.monotonic() - 1)
        with self.assertRaisesRegex(a330.GuardError,
                                    "A500_HISTORY_WRITER_NOT_INACTIVE"):
            a330.history_aggregate(self.source,
                                   gate=dict(gate, both_history_writers_inactive=False),
                                   deadline=time.monotonic() + 5)
        with self.assertRaisesRegex(a330.GuardError,
                                    "A502_GATE_CLOCK_INVALID"):
            a330.history_aggregate(self.source,
                                   gate=dict(gate, gated_at_utc="not a time"),
                                   deadline=time.monotonic() + 5)

        first = True
        class MetadataChange:
            @staticmethod
            def fromisoformat(value):
                nonlocal first
                if first:
                    first = False
                    info = self.source.stat()
                    os.utime(self.source, ns=(info.st_atime_ns,
                                              info.st_mtime_ns + 10_000_000))
                return date.fromisoformat(value)
        with patch.object(a330, "date", MetadataChange):
            with self.assertRaisesRegex(a330.GuardError,
                                        "A502_SOURCE_CHANGED_DURING_SCAN"):
                a330.history_aggregate(self.source, gate=gate,
                                       deadline=time.monotonic() + 20)

    def test_a502_history_gate_rejects_duplicate_or_contradictory_systemd(self):
        def misleading(args, **_):
            if args[1] == "show":
                return SimpleNamespace(
                    returncode=0,
                    stdout="ActiveState=active\nActiveState=inactive\nUnitFileState=masked\n")
            return SimpleNamespace(returncode=3, stdout="inactive\n")
        with (patch.object(a330, "available_memory", return_value=8 * 1024**3),
              patch.object(a330.shutil, "disk_usage",
                           return_value=SimpleNamespace(free=8 * 1024**3)),
              patch.object(a330.subprocess, "run", side_effect=misleading)):
            with self.assertRaisesRegex(a330.GuardError,
                                        "A330_A3_TIMER_NOT_QUARANTINED"):
                a330.host_history_gate(self.source)

    def test_a502_sanitizer_rebuilds_only_counts_and_rejects_tampering(self):
        gate = dict(self.gate, both_history_writers_inactive=True,
                    a3_timers_masked=True)
        receipt = a330.history_aggregate(self.source, gate=gate,
                                         deadline=time.monotonic() + 30)
        raw = self.private / "a502-private.json"
        log = self.private / "a502-private.log"
        log.write_text("path:/secret/customer\n")
        raw.write_text(json.dumps(receipt))
        verified = a330.sanitize_history_aggregate(raw, log, self.private, 0)
        self.assertEqual(verified["aggregate_status"], "SOURCE_COUNTS_ONLY")
        self.assertEqual(verified["total_versions"], 8)
        self.assertNotIn("secret/customer", json.dumps(verified))

        for mutator in (
                lambda x: {**x, "symbol": "PRIVATE"},
                lambda x: {**x, "host_purge_allowed": True},
                lambda x: {**x, "total_versions": 999},
                lambda x: {**x, "observer_database_opened": True},
                lambda x: {**x, "older_than_365d": -1},
                lambda x: {**x, "total_versions": True},
                lambda x: {**x, "state": "VALIDADO_RUNTIME"}):
            with self.subTest(mutator=mutator):
                raw.write_text(json.dumps(mutator(receipt)))
                rejected = a330.sanitize_history_aggregate(raw, log, self.private, 0)
                self.assertEqual(rejected["failure_class"], "REMOTE_RECEIPT_INVALID")
                self.assertNotIn("PRIVATE", json.dumps(rejected))
        raw.write_text('{"total_versions":1,"total_versions":2}')
        self.assertEqual(a330.sanitize_history_aggregate(raw, log, self.private, 0)
                         ["failure_class"], "REMOTE_RECEIPT_INVALID")
        raw.write_text("PRIVATE" * 3000)
        self.assertEqual(a330.sanitize_history_aggregate(raw, log, self.private, 0)
                         ["failure_class"], "REMOTE_RECEIPT_INVALID")
        raw.unlink()
        raw.symlink_to(self.source)
        self.assertEqual(a330.sanitize_history_aggregate(raw, log, self.private, 0)
                         ["failure_class"], "REMOTE_RECEIPT_INVALID")

    def test_a502_private_rejection_and_cli_isolation(self):
        raw = self.private / "a502-private.json"
        raw.touch()
        log = self.private / "a502-private.log"
        log.write_text("SECRET\nA330_REJECTED=A502_HISTORY_SIDECAR_PRESENT\n")
        failed = a330.sanitize_history_aggregate(raw, log, self.private, 2)
        self.assertEqual(failed["failure_class"],
                         "REMOTE_GUARD_A502_HISTORY_SIDECAR_PRESENT")
        self.assertNotIn("SECRET", json.dumps(failed))
        self.assertEqual(a330.sanitize_history_aggregate(raw, log, self.private, 255)
                         ["failure_class"], "SSH_EXIT_255_TRANSPORT_OR_REMOTE")

        gate = dict(self.gate, both_history_writers_inactive=True,
                    a3_timers_masked=True)
        with (patch.object(a330, "HOST_SOURCE", self.source),
              patch.object(a330, "host_history_gate", return_value=gate),
              patch.object(sys, "argv", ["a502", "--host-history-aggregate"]),
              redirect_stdout(io.StringIO()) as output):
            a330.main()
        result = json.loads(output.getvalue())
        self.assertEqual(result["total_versions"], 8)
        self.assertFalse(result["observer_database_opened"])
        self.assertNotIn("A330_CAPTURE_RECEIPT", output.getvalue())

        # Runner CLI must validate and reconstruct the public receipt itself.
        raw.write_text(json.dumps(result))
        sanitized = self.private / "a502-sanitized.json"
        argv = ["a502", "--sanitize-history-aggregate",
                "--capture-exit-code", "0", "--raw", str(raw),
                "--capture-log", str(log), "--runner-root", str(self.private),
                "--sanitized-output", str(sanitized)]
        with patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()) as status:
            a330.main()
        published = json.loads(sanitized.read_text())
        self.assertEqual(published["total_versions"], 8)
        self.assertEqual(status.getvalue().strip(),
                         "A502_AGGREGATE_STATUS=SOURCE_COUNTS_ONLY")
        self.assertNotIn("SECRET", sanitized.read_text())

        with (patch.object(sys, "argv", ["a502", "--host-history-aggregate",
                                          "--raw", str(raw)]),
              redirect_stderr(io.StringIO()) as rejected):
            with self.assertRaises(SystemExit):
                a330.main()
        self.assertIn("A330_HOST_MODE_INPUTS_FORBIDDEN", rejected.getvalue())

    def test_a502_workflow_manual_separate_mode_no_raw_artifact(self):
        body = HISTORY_WORKFLOW.read_text()
        self.assertNotIn("\n  push:", body)
        self.assertNotIn("\n  schedule:", body)
        self.assertIn("inputs.mode == 'HISTORY_AGGREGATE_ONLY'", body)
        self.assertIn("inputs.mode == 'HISTORY_COPY_ONLY'", body)
        self.assertIn("--host-history-aggregate", body)
        self.assertIn("--sanitize-history-aggregate", body)
        self.assertIn("private-history-aggregate:", body)
        self.assertIn("private-history-census:", body)
        self.assertIn("github.ref == 'refs/heads/main'", body)
        self.assertIn("path: ${{ env.RC6_A502_DIR }}/a502-sanitized.json", body)
        self.assertNotIn("path: ${{ env.RC6_A502_DIR }}/a502-private.json", body)
        self.assertNotIn("python3 -B - --host-export'", body)

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
