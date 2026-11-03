"""A623 negative-first offhost retention copy guard; never host I/O."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.rc6_retention_offhost_batch_guard import (
    OffhostBatchReader, _parse_object, guarded_review, main,
)
from rc6_retention_batch_dryrun import SCHEMA as BATCH_SCHEMA


AS_OF = "2026-10-31T20:00:00Z"
KNOWN = "2026-10-31T19:00:00Z"
A = "a" * 64
B = "b" * 64
C = "c" * 64


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _version(identity: int, day="2025-09-30", raw=C, previous=0) -> dict:
    return {"id": identity, "symbol": "DO_NOT_PRINT_PRIVATE_SYMBOL",
            "instrument_type": "ACCIONES", "market": "BYMA",
            "currency": "ARS", "settlement": "A-24HS", "date": day,
            "price_basis": "PER_SHARE", "adjustment_basis": "UNADJUSTED",
            "payload_hash": B, "previous_version_id": previous,
            "source": "PPI_API", "event_at": "2025-09-30T20:00:00Z",
            "provider_at": "2025-09-30T20:01:00Z",
            "observed_at": "2025-09-30T20:02:00Z",
            "version_known_at": "2025-09-30T20:02:00Z",
            "raw_sha256": raw}


def _manifest(versions, references=None):
    return {"snapshot_sha256": A, "snapshot_known_at": KNOWN,
            # Adversarial complete=true remains untrusted per partition.
            "inventory_complete": True,
            "pin_scopes_verified": ["history_canonical", "active_paper_positions"],
            "versions": versions, "references": references or [], "exports": []}


class RetentionOffhostGuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rc6-retention-a623-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.capture = self.root / "capture_001"
        self.capture.mkdir()

    def write_case(self, groups=None):
        groups = groups or [_manifest([_version(1), _version(2)])]
        desc = []
        for ordinal, group in enumerate(groups):
            name = f"batch-{ordinal:06d}.json"
            raw = json.dumps(group, sort_keys=True).encode("utf-8")
            (self.capture / name).write_bytes(raw)
            desc.append({"name": name, "sha256": _sha(raw),
                         "versions_count": len(group["versions"]),
                         "first_version_id": group["versions"][0]["id"],
                         "last_version_id": group["versions"][-1]["id"]})
        index = {"schema": BATCH_SCHEMA, "snapshot_sha256": A,
                 "snapshot_known_at": KNOWN,
                 "total_versions": sum(len(group["versions"]) for group in groups),
                 "batches": desc}
        raw = json.dumps(index, sort_keys=True).encode("utf-8")
        (self.capture / "index.json").write_bytes(raw)
        return _sha(raw), index

    def read(self, expected, **kwargs):
        with OffhostBatchReader(self.root, self.capture.name) as reader:
            return guarded_review(reader, index_sha256=expected,
                                  as_of=AS_OF, **kwargs)

    def test_valid_old_pinned_and_in_window_hold_non_authoritative(self):
        pinned = {"scope": "active_paper_positions", "version_id": 1,
                  "reference": "SANITIZED_PAPER_LEDGER_REF"}
        expected, _ = self.write_case([_manifest([
            _version(1), _version(2, day="2025-11-01")], [pinned])])
        files_before = {path.name: path.read_bytes() for path in self.capture.iterdir()}
        receipt = self.read(expected)
        review = receipt["review"]
        self.assertEqual(review["cutoff_inclusive_art"], "2025-11-01")
        self.assertEqual(review["states"], {"HOLD_UNVERIFIED_OR_PINNED": 1,
                                            "RETAIN_IN_WINDOW": 1})
        self.assertEqual(review["pinned_versions"], 1)
        self.assertFalse(receipt["host_purge_allowed"])
        self.assertFalse(receipt["source_mutated"])
        self.assertFalse(receipt["offhost_files_mutated"])
        self.assertFalse(receipt["consumer_universe_independently_verified"])
        self.assertFalse(review["snapshot_identity_independently_verified"])
        self.assertEqual(receipt["retention_calendar_days_including_today_art"], 365)
        self.assertEqual(receipt["real_orders_sent"], 0)
        self.assertEqual(files_before,
                         {path.name: path.read_bytes() for path in self.capture.iterdir()})

    def test_legacy_census_is_review_only_batchable_not_deletable(self):
        # Synthetic census *cardinality* only; never claim real rows verified.
        expected, _ = self.write_case([
            _manifest([_version(1)]),
            _manifest([_version(2, previous=1)])])
        first = self.read(expected, batch_limit=1)
        self.assertEqual(first["review"]["new_batches_checked"], 1)
        cursor = first["review"]["next_checkpoint_template"]
        raw = json.dumps(cursor, sort_keys=True).encode("utf-8")
        (self.capture / "checkpoint.json").write_bytes(raw)
        # The capture directory is opened afresh after checkpoint creation.
        second = self.read(expected, batch_limit=1, checkpoint_sha256=_sha(raw))
        self.assertEqual(second["review"]["versions_checked"], 2)
        self.assertEqual(second["review"]["status"], "OFFHOST_REVIEW_COMPLETE_HOLD")
        self.assertFalse(second["review"]["host_purge_allowed"])
        self.assertEqual(second["review"]["cross_batch_raw_sharing_verification"],
                         "NO_VERIFICADO")

    def test_altered_content_and_digest_or_index_fail(self):
        expected, _ = self.write_case()
        with self.assertRaisesRegex(ValueError, "DOCUMENT_SHA_MISMATCH"):
            self.read(B)
        target = self.capture / "batch-000000.json"
        target.write_bytes(target.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "DOCUMENT_SHA_MISMATCH"):
            self.read(expected)

    def test_reject_live_observer_wal_shm_and_journal_without_reading(self):
        expected, _ = self.write_case()
        for extension in ("-wal", "-shm", "-journal"):
            sidecar = self.capture / ("observer_v17.db" + extension)
            sidecar.write_bytes(b"live")
            with self.subTest(extension=extension), self.assertRaisesRegex(
                    ValueError, "LIVE_SQLITE_SIDECAR_PRESENT"):
                self.read(expected)
            sidecar.unlink()

    def test_hardlinks_symlinks_and_capture_directory_aliases_rejected(self):
        expected, _ = self.write_case()
        target = self.capture / "batch-000000.json"
        alias = self.capture / "alias.json"
        alias.symlink_to(target)
        target.unlink()
        target.symlink_to(alias)
        with self.assertRaisesRegex(ValueError, "REGULAR_BOUNDED_COPY_REQUIRED"):
            self.read(expected)
        target.unlink()
        alias.unlink()
        self.write_case()
        hardlink = self.capture / "hardlink.txt"
        os.link(self.capture / "index.json", hardlink)
        with self.assertRaisesRegex(ValueError, "REGULAR_BOUNDED_COPY_REQUIRED"):
            self.read(expected)
        hardlink.unlink()
        sibling = self.root / "alias_dir"
        sibling.symlink_to(self.capture, target_is_directory=True)
        with self.assertRaises(OSError):
            with OffhostBatchReader(self.root, sibling.name):
                pass

    def test_scoped_names_no_parent_traversal_and_no_ambiguous_json(self):
        self.write_case()
        for candidate in ("../escape", "/absolute", "two/nested", "..", "foo.json"):
            with self.subTest(candidate=candidate), self.assertRaisesRegex(
                    ValueError, "CAPTURE_DIRECTORY_NAME_INVALID"):
                OffhostBatchReader(self.root, candidate)
        with OffhostBatchReader(self.root, self.capture.name) as reader:
            for filename in ("../index.json", "/tmp/index.json", "other.json"):
                with self.subTest(filename=filename), self.assertRaisesRegex(
                        ValueError, "DOCUMENT_NAME_INVALID"):
                    reader.read_json(filename, A)
        for bad in (b'{"a": 1, "a": 2}', b'{"x": NaN}', b'{"x": Infinity}',
                    b'{"x": 1.3}', b'[]', b'{"x": -Infinity}'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _parse_object(bad)

    def test_directory_swap_after_pin_is_fail_closed(self):
        expected, _ = self.write_case()
        with OffhostBatchReader(self.root, self.capture.name) as reader:
            moved = self.root / "moved_capture"
            self.capture.rename(moved)
            self.capture.mkdir()
            with self.assertRaisesRegex(ValueError, "DIRECTORY_CHANGED"):
                reader.read_json("index.json", expected)

    def test_document_replacement_after_pin_is_fail_closed(self):
        expected, _ = self.write_case()
        with OffhostBatchReader(self.root, self.capture.name) as reader:
            original = reader._check_sidecars
            called = 0
            def adversarial():
                nonlocal called
                original()
                called += 1
                if called == 1:
                    target = self.capture / "index.json"
                    target.rename(self.capture / "old_index.json")
                    target.write_bytes(b"{}")
            with patch.object(reader, "_check_sidecars", side_effect=adversarial):
                with self.assertRaisesRegex(ValueError, "DOCUMENT_SHA_MISMATCH"):
                    reader.read_json("index.json", expected)

    def test_mid_scan_content_race_and_capacity_are_fail_closed(self):
        expected, _ = self.write_case()
        target = self.capture / "index.json"
        import scripts.rc6_retention_offhost_batch_guard as module
        original_read = os.read
        modified = False
        def racing_read(fd, n):
            nonlocal modified
            chunk = original_read(fd, n)
            if not modified and chunk:
                modified = True
                with target.open("r+b") as handle:
                    handle.seek(0)
                    handle.write(b"{")
            return chunk
        with patch.object(module.os, "read", side_effect=racing_read):
            with self.assertRaisesRegex(ValueError, "DOCUMENT_(SHA_MISMATCH|CHANGED)"):
                self.read(expected)
        self.write_case()
        with patch.object(module, "MAX_INDEX_BYTES", 4):
            with self.assertRaisesRegex(ValueError, "REGULAR_BOUNDED_COPY_REQUIRED"):
                self.read(expected)

    def test_sidecar_arriving_after_initial_check_is_detected(self):
        expected, _ = self.write_case()
        with OffhostBatchReader(self.root, self.capture.name) as reader:
            original = reader._check_sidecars
            calls = 0
            def sidecar_after_first():
                nonlocal calls
                original()
                calls += 1
                if calls == 1:
                    (self.capture / "observer_v17.db-shm").write_bytes(b"live")
            with patch.object(reader, "_check_sidecars", side_effect=sidecar_after_first):
                with self.assertRaisesRegex(ValueError, "DIRECTORY_CHANGED"):
                    reader.read_json("index.json", expected)

    def test_cli_refuses_non_action_and_never_emits_private_symbol(self):
        expected, _ = self.write_case()
        args = ["--runner-root", str(self.root), "--capture-dir", self.capture.name,
                "--index-sha256", expected, "--as-of", AS_OF]
        import sys
        with patch.object(sys, "argv", ["rc6_retention_offhost_batch_guard.py", *args]):
            with patch.dict(os.environ, {"GITHUB_ACTIONS": "false", "RUNNER_TEMP": str(self.root)}):
                with self.assertRaisesRegex(ValueError, "GITHUB_ACTIONS_RUNNER_REQUIRED"):
                    main()
            with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "RUNNER_TEMP": str(self.root)}):
                with redirect_stdout(result := StringIO()):
                    main()
        raw = result.getvalue()
        self.assertNotIn("DO_NOT_PRINT_PRIVATE_SYMBOL", raw)
        receipt = json.loads(raw)
        self.assertEqual(receipt["schema"], "RC6_RETENTION_OFFHOST_BATCH_GUARD_A623_V1")
        self.assertFalse(receipt["host_purge_allowed"])
        self.assertEqual(receipt["provider_calls"], 0)


if __name__ == "__main__":
    unittest.main()
