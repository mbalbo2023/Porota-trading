"""A511: off-host WAL image, without opening the live source via SQLite."""
from __future__ import annotations

import io
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest

from scripts import rc6_history_wal_offhost as a511


class HistoryWalTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "market_history.db"
        self.connection = sqlite3.connect(self.source)
        self.addCleanup(self.connection.close)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA wal_autocheckpoint=0")
        self.connection.execute(
            "CREATE TABLE history_versions_v2("
            "id INTEGER PRIMARY KEY,symbol TEXT,instrument_type TEXT,"
            "market TEXT,settlement TEXT,date TEXT,source TEXT,"
            "metadata_json TEXT)"
        )
        self.connection.execute(
            "INSERT INTO history_versions_v2 VALUES("
            "1,'PRIVATE_SYMBOL_101','BONOS','BYMA','A-24HS',"
            "'2026-11-05','PPI','{}')"
        )
        self.connection.commit()
        self.assertTrue(Path(str(self.source) + "-wal").exists())

    def capture(self, **kwargs):
        output = io.BytesIO()
        receipt = a511.capture_verified(
            self.source, output, writer_proof=lambda: True,
            time_budget_seconds=20, **kwargs,
        )
        return output, receipt

    def repack(self, changes):
        """Repack a valid transport after a hostile private-hop alteration."""
        output, _ = self.capture()
        output.seek(0)
        forged = io.BytesIO()
        with tarfile.open(fileobj=output, mode="r:") as original:
            with tarfile.open(fileobj=forged, mode="w") as archive:
                for member in original:
                    payload = original.extractfile(member).read()
                    if member.name == a511.MANIFEST:
                        manifest = json.loads(payload)
                        payload = json.dumps(changes(manifest), sort_keys=True).encode()
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
        forged.seek(0)
        return forged

    def test_uncheckpointed_wal_record_is_visible_only_on_private_runner(self):
        output, host = self.capture()
        self.assertEqual(host["state"], "VERIFIED_STREAM")
        self.assertGreater(host["wal_bytes"], 0)
        output.seek(0)
        private_root = self.root / "runner-only"
        private_root.mkdir(mode=0o700)
        report = a511.verify_private_bundle(
            output, private_root, time_budget_seconds=20,
        )
        self.assertEqual(report["state"], "OBSERVED_HISTORY_ONLY")
        self.assertEqual(report["observed_fivekey_count"], "NO_VERIFICADO")
        self.assertFalse(report["fivekey_count_verified"])
        self.assertEqual(report["cohort_scope"], "HISTORY_VERSION_ROWS_ONLY")
        self.assertEqual(report["history_rows"], 1)
        self.assertEqual(report["global_universe_coverage"], "NO_VERIFICADO")
        self.assertEqual(report["readiness_implication"], "NONE")
        self.assertNotIn("PRIVATE_SYMBOL_101", str(report))
        self.assertEqual(list(private_root.iterdir()), [])

    def test_capture_refuses_ambiguous_writer_scope_without_streaming_bytes(self):
        sink = io.BytesIO()
        with self.assertRaises(a511.HistoryHold) as hold:
            a511.capture_verified(
                self.source, sink, writer_proof=lambda: False,
                time_budget_seconds=20,
            )
        self.assertEqual(hold.exception.code, "WRITER_COVERAGE_UNVERIFIED")
        self.assertEqual(sink.getvalue(), b"")

    def test_capture_refuses_source_symlink_even_with_writer_proof(self):
        alias = self.root / "alias.db"
        alias.symlink_to(self.source)
        with self.assertRaises(a511.HistoryHold) as hold:
            a511.capture_verified(
                alias, io.BytesIO(), writer_proof=lambda: True,
                time_budget_seconds=20,
            )
        self.assertEqual(hold.exception.code, "SOURCE_PATH_UNSAFE")

    def test_capture_refuses_wal_above_explicit_cap(self):
        with self.assertRaises(a511.HistoryHold) as hold:
            self.capture(max_wal_bytes=1)
        self.assertEqual(hold.exception.code, "SOURCE_BYTE_LIMIT")

    def test_host_source_atime_is_unchanged(self):
        before = self.source.stat().st_atime_ns
        wal = Path(str(self.source) + "-wal")
        wal_before = wal.stat().st_atime_ns
        self.capture()
        self.assertEqual(self.source.stat().st_atime_ns, before)
        self.assertEqual(wal.stat().st_atime_ns, wal_before)

    def test_runner_rejects_truncated_stream_and_leaves_no_private_copy(self):
        output, _ = self.capture()
        raw = output.getvalue()
        private_root = self.root / "runner-only"
        private_root.mkdir(mode=0o700)
        with self.assertRaises(a511.HistoryHold):
            a511.verify_private_bundle(
                io.BytesIO(raw[:len(raw)//2]), private_root,
                time_budget_seconds=20,
            )
        self.assertEqual(list(private_root.iterdir()), [])

    def test_wal_commit_during_tar_stream_is_detected_and_never_certified(self):
        test = self

        class MutatingSink(io.BytesIO):
            mutated = False

            def write(self, raw):
                if not self.mutated and len(raw) > 512:
                    self.mutated = True
                    test.connection.execute(
                        "INSERT INTO history_versions_v2 VALUES("
                        "2,'PRIVATE_SYMBOL_102','BONOS','BYMA',"
                        "'A-24HS','2026-11-06','PPI','{}')"
                    )
                    test.connection.commit()
                return super().write(raw)

        with self.assertRaises(a511.HistoryHold) as hold:
            a511.capture_verified(
                self.source, MutatingSink(), writer_proof=lambda: True,
                time_budget_seconds=20,
            )
        self.assertEqual(hold.exception.code, "SOURCE_CHANGED")

    def test_changed_shm_metadata_is_detected_during_stream(self):
        shm = Path(str(self.source) + "-shm")
        self.assertTrue(shm.exists())

        class MutatingSink(io.BytesIO):
            changed = False

            def write(self, raw):
                if not self.changed and len(raw) > 512:
                    self.changed = True
                    os.utime(shm, None)
                return super().write(raw)

        with self.assertRaises(a511.HistoryHold) as hold:
            a511.capture_verified(
                self.source, MutatingSink(), writer_proof=lambda: True,
                time_budget_seconds=20,
            )
        self.assertEqual(hold.exception.code, "SOURCE_CHANGED")

    def test_runner_rejects_tampered_bytes_and_does_not_keep_private_copy(self):
        output, _ = self.capture()
        raw = bytearray(output.getvalue())
        raw[1024] ^= 0x01
        private_root = self.root / "runner-only"
        private_root.mkdir(mode=0o700)
        with self.assertRaises(a511.HistoryHold):
            a511.verify_private_bundle(
                io.BytesIO(raw), private_root,
                time_budget_seconds=20,
            )
        self.assertEqual(list(private_root.iterdir()), [])

    def test_runner_rejects_manifest_without_writer_exclusion_witness(self):
        for changed in (lambda m: {**m, "writer_exclusion": "NONE"},
                        lambda m: {k: v for k, v in m.items()
                                   if k != "writer_exclusion"}):
            with self.subTest(changed=changed):
                forged = self.repack(changed)
                private_root = self.root / "runner-only"
                private_root.mkdir(mode=0o700, exist_ok=True)
                with self.assertRaises(a511.HistoryHold) as hold:
                    a511.verify_private_bundle(forged, private_root,
                                               time_budget_seconds=20)
                self.assertEqual(hold.exception.code, "BUNDLE_MANIFEST_INVALID")
                self.assertEqual(list(private_root.iterdir()), [])

    def test_runner_rejects_manifest_with_unverifiable_shm_observation(self):
        forged = self.repack(lambda m: {**m, "shm_observed": "UNKNOWN"})
        private_root = self.root / "runner-only"
        private_root.mkdir(mode=0o700)
        with self.assertRaises(a511.HistoryHold) as hold:
            a511.verify_private_bundle(forged, private_root,
                                       time_budget_seconds=20)
        self.assertEqual(hold.exception.code, "BUNDLE_MANIFEST_INVALID")
        self.assertEqual(list(private_root.iterdir()), [])

    def test_runner_rejects_zero_wal_even_when_base_db_is_valid(self):
        # Base has valid history table and row; a second committed row is WAL-only.
        self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.connection.execute(
            "INSERT INTO history_versions_v2 VALUES("
            "2,'PRIVATE_SYMBOL_102','BONOS','BYMA','A-24HS',"
            "'2026-11-06','PPI','{}')")
        self.connection.commit()

        output, _ = self.capture()
        output.seek(0)
        forged = io.BytesIO()
        with tarfile.open(fileobj=output, mode="r:") as original:
            with tarfile.open(fileobj=forged, mode="w") as archive:
                for member in original:
                    payload = original.extractfile(member).read()
                    if member.name == a511.FILES["-wal"]:
                        payload = b""
                    elif member.name == a511.MANIFEST:
                        manifest = json.loads(payload)
                        for part in manifest["source_parts"]:
                            if part["name"] == a511.FILES["-wal"]:
                                part["size"] = 0
                                part["sha256"] = hashlib.sha256(b"").hexdigest()
                        payload = json.dumps(manifest, sort_keys=True).encode()
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
        forged.seek(0)
        private_root = self.root / "runner-only"
        private_root.mkdir(mode=0o700)
        with self.assertRaises(a511.HistoryHold) as hold:
            a511.verify_private_bundle(forged, private_root,
                                       time_budget_seconds=20)
        self.assertEqual(hold.exception.code, "BUNDLE_MANIFEST_INVALID")
        self.assertEqual(list(private_root.iterdir()), [])

    def test_runner_rejects_nonempty_wal_that_sqlite_would_ignore(self):
        # The base already has row1; row2 is a committed WAL-only record.
        self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.connection.execute(
            "INSERT INTO history_versions_v2 VALUES("
            "2,'PRIVATE_SYMBOL_102','BONOS','BYMA','A-24HS',"
            "'2026-11-06','PPI','{}')")
        self.connection.commit()
        output, _ = self.capture()
        output.seek(0)
        with tarfile.open(fileobj=output, mode="r:") as source_tar:
            original_parts = [
                (member.name, source_tar.extractfile(member).read())
                for member in source_tar
            ]

        cases = {
            "zeroed_header": lambda wal: b"\0" * len(wal),
            "truncated_frame": lambda wal: wal[:-1],
            "corrupt_frame_checksum": lambda wal: (
                wal[:80] + bytes([wal[80] ^ 0x01]) + wal[81:]
            ),
        }
        for case, transform in cases.items():
            with self.subTest(case=case):
                altered_wal = transform(dict(original_parts)[a511.FILES["-wal"]])
                self.assertGreater(len(altered_wal), 0)
                forged = io.BytesIO()
                with tarfile.open(fileobj=forged, mode="w",
                                  format=tarfile.PAX_FORMAT) as archive:
                    for name, original in original_parts:
                        data = original
                        if name == a511.FILES["-wal"]:
                            data = altered_wal
                        elif name == a511.MANIFEST:
                            manifest = json.loads(original)
                            for part in manifest["source_parts"]:
                                if part["name"] == a511.FILES["-wal"]:
                                    part["size"] = len(altered_wal)
                                    part["sha256"] = hashlib.sha256(
                                        altered_wal).hexdigest()
                            data = json.dumps(manifest, sort_keys=True).encode()
                        member = tarfile.TarInfo(name)
                        member.mode = 0o600
                        member.mtime = 0
                        member.size = len(data)
                        archive.addfile(member, io.BytesIO(data))
                forged.seek(0)
                private_root = self.root / "runner-only"
                private_root.mkdir(mode=0o700, exist_ok=True)
                with self.assertRaises(a511.HistoryHold) as hold:
                    a511.verify_private_bundle(forged, private_root,
                                               time_budget_seconds=20)
                self.assertEqual(hold.exception.code, "BUNDLE_WAL_INVALID")
                self.assertEqual(list(private_root.iterdir()), [])

    def test_runner_rejects_nonzero_trailing_payload_after_tar_end(self):
        output, _ = self.capture()
        forged = io.BytesIO(output.getvalue() + b"PRIVATE_TRAILING_PAYLOAD")
        private_root = self.root / "runner-only"
        private_root.mkdir(mode=0o700)
        with self.assertRaises(a511.HistoryHold) as hold:
            a511.verify_private_bundle(forged, private_root,
                                       time_budget_seconds=20)
        self.assertEqual(hold.exception.code, "BUNDLE_MEMBER_UNSAFE")
        self.assertEqual(list(private_root.iterdir()), [])

    def test_runner_rejects_data_hidden_inside_canonical_tar_padding(self):
        output, _ = self.capture()
        forged = bytearray(output.getvalue())
        self.assertEqual(forged[-1], 0)
        forged[-1] = 0x41
        private_root = self.root / "runner-only"
        private_root.mkdir(mode=0o700)
        with self.assertRaises(a511.HistoryHold) as hold:
            a511.verify_private_bundle(io.BytesIO(forged), private_root,
                                       time_budget_seconds=20)
        self.assertEqual(hold.exception.code, "BUNDLE_MEMBER_UNSAFE")
        self.assertEqual(list(private_root.iterdir()), [])

    def test_runner_rejects_nonzero_padding_between_wal_and_manifest(self):
        output, _ = self.capture()
        forged = bytearray(output.getvalue())
        with tarfile.open(fileobj=io.BytesIO(forged), mode="r:") as archive:
            wal = archive.getmember(a511.FILES["-wal"])
            padding_start = wal.offset_data + wal.size
            next_header = ((padding_start + 511) // 512) * 512
        self.assertLess(padding_start, next_header)
        self.assertEqual(forged[padding_start], 0)
        forged[padding_start] = 0x41
        private_root = self.root / "runner-only"
        private_root.mkdir(mode=0o700)
        with self.assertRaises(a511.HistoryHold) as hold:
            a511.verify_private_bundle(io.BytesIO(forged), private_root,
                                       time_budget_seconds=20)
        self.assertEqual(hold.exception.code, "BUNDLE_MEMBER_UNSAFE")
        self.assertEqual(list(private_root.iterdir()), [])

    def test_runner_rejects_hidden_metadata_in_valid_tar_header(self):
        output, _ = self.capture()
        output.seek(0)
        forged = io.BytesIO()
        with tarfile.open(fileobj=output, mode="r:") as original:
            with tarfile.open(fileobj=forged, mode="w",
                              format=tarfile.PAX_FORMAT) as archive:
                for member in original:
                    payload = original.extractfile(member).read()
                    if member.name == a511.FILES[""]:
                        member.uname = "PRIVATE_HEADER_SENTINEL"
                    archive.addfile(member, io.BytesIO(payload))
        forged.seek(0)
        private_root = self.root / "runner-only"
        private_root.mkdir(mode=0o700)
        with self.assertRaises(a511.HistoryHold) as hold:
            a511.verify_private_bundle(forged, private_root,
                                       time_budget_seconds=20)
        self.assertEqual(hold.exception.code, "BUNDLE_MEMBER_UNSAFE")
        self.assertEqual(list(private_root.iterdir()), [])

    def test_runner_rejects_canonical_members_in_wrong_tar_order(self):
        output, _ = self.capture()
        output.seek(0)
        forged = io.BytesIO()
        with tarfile.open(fileobj=output, mode="r:") as original:
            parts = [(member, original.extractfile(member).read())
                     for member in original]
        with tarfile.open(fileobj=forged, mode="w",
                          format=tarfile.PAX_FORMAT) as archive:
            for member, payload in reversed(parts):
                archive.addfile(member, io.BytesIO(payload))
        forged.seek(0)
        private_root = self.root / "runner-only"
        private_root.mkdir(mode=0o700)
        with self.assertRaises(a511.HistoryHold) as hold:
            a511.verify_private_bundle(forged, private_root,
                                       time_budget_seconds=20)
        self.assertEqual(hold.exception.code, "BUNDLE_MEMBER_UNSAFE")
        self.assertEqual(list(private_root.iterdir()), [])

    def test_runner_requires_private_directory(self):
        output, _ = self.capture()
        public = self.root / "public"
        public.mkdir(mode=0o755)
        # CI uses umask 077, so mkdir(mode=0755) can create mode 0700.
        # Make the test's intentionally unsafe permission explicit.
        public.chmod(0o755)
        self.assertEqual(public.stat().st_mode & 0o777, 0o755)
        with self.assertRaises(a511.HistoryHold) as hold:
            a511.verify_private_bundle(
                io.BytesIO(output.getvalue()), public,
                time_budget_seconds=20,
            )
        self.assertEqual(hold.exception.code, "RUNNER_PRIVATE_DIRECTORY_REQUIRED")

    def test_host_cli_produces_only_typed_hold_without_writer_witness(self):
        output = io.BytesIO()
        errors = io.StringIO()
        code = a511.main(["--host-stream"], source=self.source,
                         stdout=output, stderr=errors)
        self.assertEqual(code, 2)
        self.assertEqual(output.getvalue(), b"")
        self.assertEqual(errors.getvalue(), "A511_REJECTED=WRITER_COVERAGE_UNVERIFIED\n")

    def test_failure_classifier_does_not_echo_private_stderr(self):
        self.assertEqual(
            a511.classify_host_failure(
                2, b"A511_REJECTED=WRITER_COVERAGE_UNVERIFIED\n"
            ), "REMOTE_GUARD_WRITER_COVERAGE_UNVERIFIED"
        )
        self.assertEqual(
            a511.classify_host_failure(
                2, b"SECRET=PRIVATE_TOKEN\n"
            ), "REMOTE_FAILURE_UNVERIFIED"
        )

    def test_control_plane_mode_is_manual_main_only_with_deploy_mutex(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows"
                    / "rc6-a500-retention-history-copy-only.yml").read_text()
        self.assertIn("- HISTORY_WAL_COPY_ONLY", workflow)
        self.assertIn("private-history-wal-copy:", workflow)
        self.assertIn("inputs.mode == 'HISTORY_WAL_COPY_ONLY'", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("group: rc6-unified-paper-deploy", workflow)
        self.assertIn("cancel-in-progress: false", workflow)

    def test_new_capture_uses_current_deployed_source_not_stale_a500_pin(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows"
                    / "rc6-a500-retention-history-copy-only.yml").read_text()
        new_job = workflow.split("  private-history-wal-copy:", 1)[1]
        self.assertIn("57d5e8cdd6619dcbf967ed3e4901534fb5a7286e", new_job)
        self.assertIn("159d34172b928a17a4b0106fc9def6783d139f6c", new_job)
        self.assertNotIn("7e1e744d6ca9ab30c51b5747dd4467af5ece0145", new_job)
        self.assertIn("rc6_history_wal_offhost.py", new_job)
        self.assertNotIn("--host-history-export", new_job)
        self.assertNotIn("ssh ", new_job)
        self.assertNotIn("secrets.DO_", new_job)
        self.assertIn("A511_HOLD=WRITER_COVERAGE_UNVERIFIED", new_job)


if __name__ == "__main__":
    unittest.main()
