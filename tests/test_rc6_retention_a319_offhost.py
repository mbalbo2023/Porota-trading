"""Executable negative/positive contract for A319: runner copies, never host."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/rc6_retention_a319_offhost.py"
SPEC = importlib.util.spec_from_file_location("a319_offhost", SCRIPT)
assert SPEC and SPEC.loader
a319 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(a319)


class SyntheticCapture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rc6-a319-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.as_of = "2026-11-01T18:00:00+00:00"
        self.records = {}
        self.source = self.root / "history-copy.sqlite"
        with sqlite3.connect(self.source) as db:
            db.executescript('''
                CREATE TABLE history_versions_v2 (
                    id INTEGER PRIMARY KEY, symbol TEXT, instrument_type TEXT,
                    market TEXT, settlement TEXT, date TEXT, source TEXT,
                    metadata_json TEXT);
                CREATE TABLE history_canonical_v2 (
                    version_id INTEGER REFERENCES history_versions_v2(id));
                CREATE TABLE local_receipts (receipt_json TEXT);
            ''')
            for version_id in range(1, 9):
                row = dict(id=version_id, symbol="X" + str(version_id),
                           instrument_type="BOND", market="BYMA",
                           settlement="T2", date="2026-10-31" if version_id == 7
                           else "2024-01-01", source="PPI",
                           metadata_json='{"provider":"PPI"}')
                self.records[version_id] = row
                db.execute('''INSERT INTO history_versions_v2 VALUES
                              (:id,:symbol,:instrument_type,:market,:settlement,
                               :date,:source,:metadata_json)''', row)
            db.execute("INSERT INTO history_canonical_v2 VALUES (2)")
            db.execute('INSERT INTO local_receipts VALUES (?)',
                       (json.dumps({"version_id": 2}),))
        self.refs_sqlite = self.root / "other-copy.sqlite"
        with sqlite3.connect(self.refs_sqlite) as db:
            db.execute("CREATE TABLE outside_pins (history_version_id INTEGER)")
            db.execute("INSERT INTO outside_pins VALUES (1)")
        self.snapshots = [
            {"kind": "sqlite", "path": self.refs_sqlite.name},
            {"kind": "json", "path": "plain.json"},
            {"kind": "artifact", "path": "artifact.json"},
            {"kind": "replay", "path": "replay.json"},
            {"kind": "provider_capture", "path": "issuer-capture.json"},
        ]
        (self.root / "plain.json").write_text(json.dumps({"version_id": 3}))
        (self.root / "artifact.json").write_text(json.dumps({
            "events": [{"history_version_id": 4}]}))
        (self.root / "replay.json").write_text(json.dumps({
            "replay_versions": [{"version_id": 5}]}))
        self.claim = {"version_id": 6,
                      "legacy_row_sha256": a319.row_digest(self.records[6]),
                      "currency": "ARS", "price_basis": "CLEAN",
                      "adjustment_basis": "NOMINAL",
                      "known_at_utc": "2024-01-01T22:00:00+00:00",
                      "provider": "PPI", "capture_path": "issuer-capture.json"}
        self.capture = {"schema": a319.CAPTURE_SCHEMA, "provider": "PPI",
                        "captured_at_utc": "2024-01-01T21:00:00+00:00",
                        "rows": [dict(self.claim)]}
        self.manifest = {"schema": a319.MANIFEST_SCHEMA,
                         "snapshots": self.snapshots,
                         "provenance": [self.claim]}
        self.update_files()

    def update_files(self):
        (self.root / "issuer-capture.json").write_text(json.dumps(self.capture))
        for spec in self.snapshots:
            spec["sha256"] = a319.digest_bytes((self.root / spec["path"]).read_bytes())
        self.manifest_path = self.root / "manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest, sort_keys=True))
        self.source_sha = a319.digest_bytes(self.source.read_bytes())
        self.manifest_sha = a319.digest_bytes(self.manifest_path.read_bytes())

    def run_audit(self, **opts):
        return a319.audit(self.root, self.source.name, self.source_sha,
                          self.manifest_path.name, self.manifest_sha,
                          self.as_of, **opts)

    def test_complete_manifest_rehearses_only_unpinned_old_copy_twice(self):
        original_source = self.source.read_bytes()
        result = self.run_audit(index_output="per-row-index.jsonl")
        self.assertEqual(result["state"], "OFFHOST_COPY_SIMULATION_VERIFIED_HOLD")
        self.assertEqual(result["total_versions"], 8)
        self.assertEqual(result["reference_pinned_versions"], 5)
        self.assertEqual(result["decision_counts"], {
            "COPY_SIMULATION_CANDIDATE": 1,
            "HOLD_PROVENANCE_PIT": 1,
            "RETAIN_365D": 1,
            "RETAIN_PINNED": 5})
        self.assertEqual(result["simulation"]["deleted_in_copies_only"], 1)
        self.assertTrue(result["simulation"]["copy_rollback_restored_twice"])
        self.assertFalse(result["host_purge_allowed"])
        self.assertFalse(result["external_consumer_universe_verified"])
        self.assertEqual(result["physical_host_delete_count"], 0)
        self.assertEqual(original_source, self.source.read_bytes())
        self.assertFalse(list(self.root.glob("rc6-a319-copy-only-*")))
        index = self.root / "per-row-index.jsonl"
        self.assertEqual(a319.digest_bytes(index.read_bytes()),
                         result["per_row_index"]["sha256"])
        records = [json.loads(line) for line in index.read_text().splitlines()]
        self.assertEqual(len(records), 8)
        self.assertEqual(records[5]["decision"], "COPY_SIMULATION_CANDIDATE")
        self.assertEqual(records[5]["point_in_time"], "STRUCTURAL_MATCH_ONLY")
        self.assertEqual(records[0]["pin_kinds"], ["sqlite_external"])
        self.assertTrue(all(entry["provider_authenticity"] == "NO_VERIFICADO"
                            for entry in records))
        self.assertNotIn("X1", index.read_text())

    def test_index_not_overwritten(self):
        preexisting = self.root / "ledger.jsonl"
        preexisting.write_text("preserve-me")
        with self.assertRaisesRegex(a319.EvidenceError, "INDEX_ALREADY_EXISTS"):
            self.run_audit(index_output=preexisting.name)
        self.assertEqual(preexisting.read_text(), "preserve-me")

    def test_index_filename_traversal_rejected(self):
        with self.assertRaisesRegex(a319.EvidenceError, "INDEX_FILENAME_INVALID"):
            self.run_audit(index_output="../overlap")

    def test_missing_replay_coverage_holds_unpinned_old_versions(self):
        self.snapshots.remove(next(s for s in self.snapshots if s["kind"] == "replay"))
        self.update_files()
        result = self.run_audit()
        self.assertEqual(result["state"], "OFFHOST_CENSUS_COMPLETE_HOLD")
        self.assertEqual(result["decision_counts"]["HOLD_MISSING_CROSS_STORE_COVERAGE"], 3)
        self.assertNotIn("simulation", result)

    def test_bad_per_row_provenance_never_creates_candidate(self):
        self.claim["legacy_row_sha256"] = "f" * 64
        self.update_files()
        result = self.run_audit()
        self.assertEqual(result["state"], "OFFHOST_CENSUS_COMPLETE_HOLD")
        self.assertEqual(result["decision_counts"]["HOLD_PROVENANCE_PIT"], 2)

    def test_capture_after_known_at_cannot_establish_pit(self):
        self.capture["captured_at_utc"] = "2024-01-02T00:00:00+00:00"
        self.update_files()
        self.assertEqual(self.run_audit()["state"], "OFFHOST_CENSUS_COMPLETE_HOLD")

    def test_legacy_metadata_economic_conflict_rejects_claim(self):
        with sqlite3.connect(self.source) as db:
            db.execute('UPDATE history_versions_v2 SET metadata_json=? WHERE id=6',
                       ('{"currency":"USD"}',))
        self.records[6]["metadata_json"] = '{"currency":"USD"}'
        self.claim["legacy_row_sha256"] = a319.row_digest(self.records[6])
        self.capture["rows"] = [dict(self.claim)]
        self.update_files()
        result = self.run_audit()
        self.assertEqual(result["state"], "OFFHOST_CENSUS_COMPLETE_HOLD")
        self.assertEqual(result["decision_counts"]["HOLD_PROVENANCE_PIT"], 2)

    def test_unverified_economic_placeholder_rejects_claim(self):
        self.claim["currency"] = "NO_VERIFICADO"
        self.capture["rows"] = [dict(self.claim)]
        self.update_files()
        self.assertEqual(self.run_audit()["state"], "OFFHOST_CENSUS_COMPLETE_HOLD")

    def test_hash_mismatch_fails_closed(self):
        (self.root / "artifact.json").write_text('[{"version_id":6}]')
        with self.assertRaisesRegex(a319.EvidenceError, "SNAPSHOT_HASH_MISMATCH"):
            self.run_audit()

    def test_symlink_rejected(self):
        alias = self.root / "alias.sqlite"
        alias.symlink_to(self.source)
        with self.assertRaisesRegex(a319.EvidenceError, "COPY_NOT_REGULAR"):
            a319.audit(self.root, alias.name, self.source_sha,
                       self.manifest_path.name, self.manifest_sha, self.as_of)

    def test_path_traversal_rejected(self):
        with self.assertRaisesRegex(a319.EvidenceError, "COPY_PATH_TRAVERSAL"):
            a319.audit(self.root, "../history-copy.sqlite", self.source_sha,
                       self.manifest_path.name, self.manifest_sha, self.as_of)

    def test_sqlite_wal_sidecar_rejected(self):
        sidecar = self.root / "history-copy.sqlite-wal"
        sidecar.touch()
        with self.assertRaisesRegex(a319.EvidenceError, "COPY_HAS_SQLITE_SIDECAR"):
            self.run_audit()

    def test_unclassifiable_payload_prevents_rehearsal(self):
        with sqlite3.connect(self.source) as db:
            db.execute('INSERT INTO local_receipts VALUES (?)', ('not-json',))
        self.update_files()
        with self.assertRaisesRegex(a319.EvidenceError, "JSON_COLUMN_UNCLASSIFIABLE"):
            self.run_audit()

    def test_orphan_claim_prevents_rehearsal(self):
        self.manifest["provenance"].append({"version_id": 999})
        self.update_files()
        with self.assertRaisesRegex(a319.EvidenceError, "PROVENANCE_FOREIGN_ID"):
            self.run_audit()

    def test_duplicate_json_keys_cannot_hide_pins(self):
        (self.root / "plain.json").write_text('{"version_id":3,"version_id":6}')
        self.update_files()
        with self.assertRaisesRegex(a319.EvidenceError, "JSON_DUPLICATE_KEY"):
            self.run_audit()

    def test_jsonl_nested_version_id_is_pin(self):
        replay = next(spec for spec in self.snapshots if spec["kind"] == "replay")
        replay["path"] = "replay.jsonl"
        (self.root / "replay.jsonl").write_text('''{"payload":{"versions":[{"id":5}]}}
{"other":{"not_a_version_id":44}}
''')
        self.update_files()
        report = self.run_audit()
        self.assertEqual(report["reference_pinned_versions"], 5)
        self.assertEqual(report["decision_counts"]["COPY_SIMULATION_CANDIDATE"], 1)

    def test_foreign_key_corruption_rejected_on_source(self):
        with sqlite3.connect(self.source) as db:
            db.execute("INSERT INTO history_canonical_v2 VALUES (99999)")
        self.update_files()
        with self.assertRaisesRegex(a319.EvidenceError, "SOURCE_SQLITE_FOREIGN_KEY_CHECK"):
            self.run_audit()

    def test_other_version_key_shapes_are_conservatively_pinned(self):
        with sqlite3.connect(self.refs_sqlite) as db:
            db.execute("ALTER TABLE outside_pins ADD COLUMN history_version_ref INTEGER")
            db.execute("UPDATE outside_pins SET history_version_ref=6")
        self.update_files()
        report = self.run_audit()
        self.assertEqual(report["state"], "OFFHOST_CENSUS_COMPLETE_HOLD")
        self.assertEqual(report["reference_pinned_versions"], 6)
        self.assertEqual(report["decision_counts"]["RETAIN_PINNED"], 6)

    def test_rehearsal_many_candidates_uses_bounded_sql_variables(self):
        with sqlite3.connect(self.source) as db:
            for version_id in range(9, 1030):
                db.execute('INSERT INTO history_versions_v2 VALUES (?,?,?,?,?,?,?,?)',
                           (version_id, "X", "BOND", "BYMA", "T2", "2024-01-01",
                            "PPI", "{}"))
        self.update_files()
        original = self.source.read_bytes()
        simulated = a319.copy_simulation(self.source, list(range(9, 1030)),
                                         self.root, self.source_sha,
                                         time.monotonic() + 60)
        self.assertEqual(simulated["deleted_in_copies_only"], 1021)
        self.assertEqual(self.source.read_bytes(), original)

    def test_rehearsal_rejects_trigger_side_effects_without_mutating_source(self):
        with sqlite3.connect(self.source) as db:
            db.execute("CREATE TABLE trigger_side_effects(id INTEGER)")
            db.executescript('''
                CREATE TRIGGER unwanted_mutation AFTER DELETE ON history_versions_v2
                BEGIN INSERT INTO trigger_side_effects VALUES (old.id); END;
            ''')
        self.update_files()
        original = self.source.read_bytes()
        with self.assertRaisesRegex(a319.EvidenceError,
                                    "DRY_RUN_CASCADE_OR_TRIGGER_MUTATION"):
            a319.copy_simulation(self.source, [6], self.root, self.source_sha,
                                 time.monotonic() + 30)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertFalse(list(self.root.glob("rc6-a319-copy-only-*")))

    def test_source_path_swap_after_fd_pin_fails_not_scans_new_database(self):
        legacy = a319.legacy_rows
        expected = self.source_sha

        def adversarial_swap(stable_path, deadline):
            self.source.rename(self.root / "original-history.sqlite")
            shutil.copyfile(self.refs_sqlite, self.source)
            return legacy(stable_path, deadline)

        with patch.object(a319, "legacy_rows", side_effect=adversarial_swap):
            with self.assertRaisesRegex(a319.EvidenceError,
                                        "COPY_PATH_REPLACED_DURING_AUDIT"):
                self.run_audit()
        self.assertEqual(a319.digest_bytes(
            (self.root / "original-history.sqlite").read_bytes()), expected)

    def test_json_snapshot_path_swap_after_fd_pin_fails_closed(self):
        scan = a319.scan_json

        def adversarial_swap(path, known_ids, deadline, *, original_name=""):
            if original_name == "plain.json":
                original = self.root / "plain.json"
                original.rename(self.root / "original-plain.json")
                original.write_text('{"version_id":6}')
            return scan(path, known_ids, deadline, original_name=original_name)

        with patch.object(a319, "scan_json", side_effect=adversarial_swap):
            with self.assertRaisesRegex(a319.EvidenceError,
                                        "COPY_PATH_REPLACED_DURING_AUDIT"):
                self.run_audit()

    def test_symlink_swap_between_path_check_and_secure_fd_open_fails(self):
        initial = a319.copy_inside

        def adversarial_swap(root, value, **kwargs):
            path = initial(root, value, **kwargs)
            if value == self.source.name:
                self.source.rename(self.root / "original-history.sqlite")
                self.source.symlink_to(self.root / "original-history.sqlite")
            return path

        with patch.object(a319, "copy_inside", side_effect=adversarial_swap):
            with self.assertRaisesRegex(a319.EvidenceError,
                                        "COPY_SECURE_OPEN_DENIED"):
                self.run_audit()

    def test_cli_failure_sanitized_no_host_action(self):
        cmd = [sys.executable, str(SCRIPT), "--runner-root", str(self.root),
               "--database-copy", self.source.name,
               "--database-sha256", "0" * 64,
               "--manifest", self.manifest_path.name,
               "--manifest-sha256", self.manifest_sha,
               "--as-of-utc", self.as_of]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["state"], "NO_VERIFICADO")
        self.assertFalse(receipt["host_purge_allowed"])
        self.assertEqual(receipt["failure_class"], "SNAPSHOT_HASH_MISMATCH")


if __name__ == "__main__":
    unittest.main()
