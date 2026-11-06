"""A729 negative and positive fixtures: there is NO deletion entry point."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_a729_db_backup_inventory_ro as inv


class DBBackupInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dbpath = self.root / "observer_v17.db"
        with sqlite3.connect(self.dbpath) as db:
            db.execute("CREATE TABLE history_versions_v2(id INTEGER PRIMARY KEY,date TEXT,value TEXT)")
            db.execute("CREATE TABLE audit_receipts(id INTEGER PRIMARY KEY,payload TEXT)")
            db.executemany("INSERT INTO history_versions_v2 VALUES (?,?,?)", [
                (1, "2025-10-01", "old immutable evidence"),
                (2, "2026-10-30", "new immutable evidence"),
                (3, "invalid", "bad date retained"),
            ])
            db.execute("INSERT INTO audit_receipts VALUES (1,'never delete')")

    def test_db_table_by_table_and_cutoff_no_mutation(self):
        source_bytes = self.dbpath.read_bytes()
        receipt = inv.inventory_db("OBSERVER", self.dbpath, 2 * 1024**2, "2025-11-07")
        self.assertEqual(receipt["status"], "SCANNED", receipt)
        self.assertEqual(receipt["tables_inspected"], 2)
        tables = {row["name"]: row for row in receipt["tables"]}
        self.assertEqual(tables["history_versions_v2"]["status"], "METADATA_ONLY")
        self.assertEqual(tables["history_versions_v2"]["columns_count"], 3)
        self.assertTrue(tables["history_versions_v2"]["has_date_column"])
        self.assertIsNone(tables["history_versions_v2"]["rows"])
        self.assertIsNone(tables["history_versions_v2"]["older_than_365_art_dates"])
        other = [item for name, item in tables.items() if name.startswith("OTHER_TABLE_")]
        self.assertEqual(len(other), 1)
        self.assertIsNone(other[0]["rows"])
        self.assertEqual(receipt["schema_scope"], "IMMUTABLE_BASE_STABLE")
        contracts = {row["name"]: row for row in receipt["book_contract"]}
        self.assertEqual(contracts["observer_state"]["state"], "TABLE_ABSENT")
        self.assertEqual(self.dbpath.read_bytes(), source_bytes)
        self.assertFalse(receipt["retention_delete_allowed"])
        self.assertIsNone(receipt["physical_reclaim_estimate_bytes"])
        self.assertFalse((self.root / "observer_v17.db-wal").exists())

    def test_existing_nonzero_wal_reports_base_only_not_current_schema(self):
        (self.root / "observer_v17.db-wal").write_bytes(b"untrusted WAL")
        receipt = inv.inventory_db("OBSERVER", self.dbpath, 2 * 1024**2, "2025-11-07")
        self.assertEqual(receipt["status"], "WAL_BASE_ONLY")
        self.assertEqual(receipt["schema_scope"], "BASE_FILE_ONLY_WAL_UNRECONCILED")
        self.assertEqual(receipt["wal_bytes"], len(b"untrusted WAL"))
        self.assertEqual(receipt["wal_state"], "REGULAR")
        self.assertEqual(receipt["shm_state"], "MISSING")
        self.assertFalse(receipt["retention_delete_allowed"])

    def test_wal_shm_permissions_and_book_column_contract(self):
        with sqlite3.connect(self.dbpath) as db:
            db.execute("CREATE TABLE observer_state (id INT, mode TEXT, real_orders_sent INT)")
            db.execute("CREATE TABLE paper_positions(status TEXT)")
        wal = self.root / "observer_v17.db-wal"
        shm = self.root / "observer_v17.db-shm"
        wal.write_bytes(b"test wal")
        shm.write_bytes(b"test shm")
        os.chmod(wal, 0o600)
        os.chmod(shm, 0o600)
        receipt = inv.inventory_db("OBSERVER", self.dbpath, 2 * 1024**2, "2025-11-07")
        self.assertEqual(receipt["status"], "WAL_BASE_ONLY")
        self.assertTrue(receipt["wal_owner_matches_db"])
        self.assertTrue(receipt["shm_same_group_as_db"])
        self.assertTrue(receipt["shm_owner_readable"])
        self.assertEqual(receipt["shm_bytes"], 8)
        contracts = {row["name"]: row for row in receipt["book_contract"]}
        self.assertEqual(contracts["observer_state"]["state"], "MISSING_COLUMNS")
        self.assertEqual(contracts["observer_state"]["missing_required_columns"],
                         ["process_state", "session_state"])
        self.assertEqual(contracts["paper_positions"]["state"], "PRESENT")

    def test_existing_nonzero_rollback_journal_blocks_sql(self):
        (self.root / "observer_v17.db-journal").write_bytes(b"active transaction")
        receipt = inv.inventory_db("OBSERVER", self.dbpath, 2 * 1024**2, "2025-11-07")
        self.assertEqual(receipt["status"], "ROLLBACK_JOURNAL_ACTIVE")

    def test_shm_symlink_reports_unsafe_and_never_follows(self):
        (self.root / "observer_v17.db-shm").symlink_to(self.dbpath)
        receipt = inv.inventory_db("OBSERVER", self.dbpath, 2 * 1024**2, "2025-11-07")
        self.assertEqual(receipt["status"], "FILE_UNSAFE")
        self.assertEqual(receipt["shm_state"], "FILE_UNSAFE")
        self.assertEqual(receipt["tables"], [])

    def test_symlink_database_rejected(self):
        link = self.root / "link.db"
        link.symlink_to(self.dbpath)
        self.assertEqual(inv.inventory_db("OBSERVER", link, 2 * 1024**2,
                                          "2025-11-07")["status"], "FILE_UNSAFE")

    def test_symlink_parent_rejected(self):
        link = self.root / "linked-dir"
        link.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(inv.inventory_db("OBSERVER", link / "observer_v17.db",
                                          2 * 1024**2, "2025-11-07")["status"],
                         "PATH_UNSAFE")

    def test_missing_db_and_oversized_db(self):
        self.assertEqual(inv.inventory_db("HISTORY", self.root / "none.db",
                                          2 * 1024**2, "2025-11-07")["status"], "MISSING")
        self.assertEqual(inv.inventory_db("OBSERVER", self.dbpath, 1,
                                          "2025-11-07")["status"], "SIZE_LIMIT")

    def test_corrupted_db_remains_unmodified(self):
        path = self.root / "corrupt.db"
        path.write_bytes(b"broken" * 1024)
        original = path.read_bytes()
        receipt = inv.inventory_db("HISTORY", path, 2 * 1024**2, "2025-11-07")
        self.assertEqual(receipt["status"], "SCHEMA_UNAVAILABLE", receipt)
        self.assertEqual(path.read_bytes(), original)

    def test_bounded_large_schema_reports_partial_without_delete(self):
        with patch.object(inv, "DB_SCAN_SECONDS", 0):
            receipt = inv.inventory_db("OBSERVER", self.dbpath, 2 * 1024**2, "2025-11-07")
        self.assertEqual(receipt["status"], "SQL_PARTIAL_BOUNDED")
        self.assertEqual(receipt["tables_inspected"], 0)
        self.assertTrue(all(row["status"] == "TIME_BUDGET" for row in receipt["tables"]))

    def test_backup_directories_count_only_known_regular_files(self):
        backups = self.root / "backups"
        backups.mkdir()
        path = backups / "observer_2025-01-01.db.gz"
        path.write_bytes(b"archival preservation")
        os.utime(path, (1600000000, 1600000000))
        (backups / "unmatched.txt").write_text("keep")
        (backups / "observer_bad.db.gz").symlink_to(self.dbpath)
        inventory = inv.inventory_backups("PAPER", backups, inv.BACKUP_TARGETS[1][2],
                                         datetime(2025, 11, 7, tzinfo=timezone.utc))
        self.assertEqual(inventory["status"], "SCANNED")
        self.assertEqual(inventory["matched_files"], 1)
        self.assertEqual(inventory["matched_bytes"], len(b"archival preservation"))
        self.assertEqual(inventory["older_365_file_mtime_count"], 1)
        self.assertEqual(inventory["unexpected_entries"], 1)
        self.assertEqual(inventory["unsafe_entries"], 1)
        self.assertFalse(inventory["delete_allowed"])
        self.assertTrue(path.exists())

    def test_backup_directory_symlink_and_missing_do_not_follow(self):
        missing = self.root / "none"
        self.assertEqual(inv.inventory_backups("HISTORY", missing, inv.BACKUP_TARGETS[2][2],
                                               datetime.now(timezone.utc))["status"], "MISSING")
        link = self.root / "linkdir"
        link.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(inv.inventory_backups("PAPER", link, inv.BACKUP_TARGETS[1][2],
                                               datetime.now(timezone.utc))["status"], "PATH_UNSAFE")

    def test_backup_max_entries_fail_closed(self):
        (self.root / "observer_1.db.gz").touch()
        (self.root / "observer_2.db.gz").touch()
        with patch.object(inv, "MAX_BACKUP_ENTRIES", 1):
            receipt = inv.inventory_backups("PAPER", self.root, inv.BACKUP_TARGETS[1][2],
                                            datetime.now(timezone.utc))
        self.assertEqual(receipt["status"], "BOUNDED")
        self.assertFalse(receipt["delete_allowed"])

    def test_public_sanitizer_strips_unknown_host_values_and_names(self):
        with patch.object(inv, "DB_TARGETS", (("HISTORY", self.dbpath, 2 * 1024**2),
                                             ("OBSERVER", self.dbpath, 2 * 1024**2))):
            with patch.object(inv, "BACKUP_TARGETS", (
                ("GENERAL", self.root, inv.BACKUP_TARGETS[0][2]),
                ("PAPER", self.root, inv.BACKUP_TARGETS[1][2]),
                ("HISTORY", self.root, inv.BACKUP_TARGETS[2][2]),
            )):
                raw = inv.host_inventory()
        raw["secret"] = "sensitive-content-should-not-publish"
        raw["db_inventory"][0]["unexpected"] = "sensitive"
        receipt = inv.public_receipt(raw, 0)
        self.assertEqual(receipt["state"], "INVENTORY_ONLY", receipt)
        self.assertNotIn("sensitive", json.dumps(receipt))
        self.assertFalse(receipt["purge_allowed"])
        self.assertEqual(receipt["backup_files_deleted"], 0)
        self.assertFalse(receipt["backup_delete_authorized"])

    def test_public_sanitizer_rejects_claimed_mutation_and_malformed(self):
        self.assertEqual(inv.public_receipt({"host_mutations": 1}, 0)["state"], "NO_VERIFICADO")
        self.assertEqual(inv.public_receipt({}, 255)["state"], "NO_VERIFICADO")
        self.assertFalse(inv.public_receipt({}, 255)["purge_allowed"])

    def test_public_sanitizer_rejects_unallowlisted_table_name(self):
        with patch.object(inv, "DB_TARGETS", (("HISTORY", self.dbpath, 2 * 1024**2),
                                             ("OBSERVER", self.dbpath, 2 * 1024**2))):
            with patch.object(inv, "BACKUP_TARGETS", (
                ("GENERAL", self.root, inv.BACKUP_TARGETS[0][2]),
                ("PAPER", self.root, inv.BACKUP_TARGETS[1][2]),
                ("HISTORY", self.root, inv.BACKUP_TARGETS[2][2]),
            )):
                raw = inv.host_inventory()
        raw["db_inventory"][0]["tables"][0]["name"] = "private_account_data"
        sanitized = inv.public_receipt(raw, 0)
        self.assertEqual(sanitized["state"], "NO_VERIFICADO")
        self.assertNotIn("private_account_data", json.dumps(sanitized))

    def test_no_apply_route_exposed(self):
        script = Path(inv.__file__).read_text()
        self.assertNotIn("os.unlink(", script)
        self.assertNotIn("os.remove(", script)
        self.assertNotIn("shutil.rmtree(", script)
        self.assertNotIn("DELETE FROM", script)
        self.assertNotIn("VACUUM", script)


if __name__ == "__main__":
    unittest.main()
