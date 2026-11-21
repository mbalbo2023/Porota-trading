"""A729 negative and positive fixtures: there is NO deletion entry point."""
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import tracemalloc
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch
import sys
import time

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


class ArchiveProvenanceReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dbpath = self.root / "market_history.db"
        self.writer = sqlite3.connect(self.dbpath)
        self.addCleanup(self.writer.close)
        self.writer.execute("PRAGMA journal_mode=WAL")
        self.writer.execute("""CREATE TABLE historical_raw_archive(
            id INTEGER PRIMARY KEY AUTOINCREMENT, origin TEXT NOT NULL,
            row_key TEXT NOT NULL, body_hash TEXT NOT NULL,
            recorded_at TEXT NOT NULL, quality TEXT NOT NULL,
            body_json TEXT NOT NULL, UNIQUE(origin,row_key,body_hash))""")
        self.writer.commit()
        self.writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def add_archive(self, row_id, *, origin="PPI_HISTORY", quality="UNVALIDATED",
                    body=None):
        body = body if body is not None else '{"fixture":' + str(row_id) + '}'
        self.writer.execute("""INSERT INTO historical_raw_archive
            (id,origin,row_key,body_hash,recorded_at,quality,body_json)
            VALUES(?,?,?,?,?,?,?)""", (row_id, origin, f"fixture-{row_id}",
            hashlib.sha256(body.encode()).hexdigest(),
            "2026-11-19T10:43:47+00:00", quality, body))
        self.writer.commit()

    def secondary_archive(self):
        """Model the host's DISTINCT HISTORY and OBSERVER source inodes."""
        path = self.root / "observer_archive.db"
        destination = sqlite3.connect(path)
        self.addCleanup(destination.close)
        self.writer.backup(destination)
        destination.execute("PRAGMA journal_mode=WAL")
        return path

    def test_reader_sees_committed_wal_row_without_checkpoint_and_changes_no_base(self):
        self.add_archive(1)
        wal_path = Path(str(self.dbpath) + "-wal")
        self.assertGreater(wal_path.stat().st_size, 0)
        base_before = self.dbpath.stat().st_mtime_ns
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
            result = inv.archive_provenance_db("HISTORY", self.dbpath, 2 * 1024**2)
        self.assertEqual(result["status"], "OBSERVED_BOUNDED", result)
        self.assertEqual(result["wal_scope"], "WAL_SNAPSHOT_CONSISTENT")
        self.assertEqual(result["sample_rows"], 1)
        self.assertEqual(result["origin_ppi_history_labeled"], 1)
        self.assertEqual(result["quality_unvalidated_labeled"], 1)
        self.assertEqual(result["hash_matched"], 1)
        self.assertIsNone(result["rows_total"])
        self.assertEqual(self.dbpath.stat().st_mtime_ns, base_before)

    def test_ro_mount_is_blocking_even_when_sqlite_is_present(self):
        self.add_archive(1)
        with patch.object(inv, "archive_readonly_mount_attested", return_value=False):
            result = inv.archive_provenance_db("HISTORY", self.dbpath, 2 * 1024**2)
        self.assertEqual(result["status"], "RO_MOUNT_UNVERIFIED")
        self.assertEqual(result["sample_rows"], 0)

    def test_latest_64_rows_bounded_no_false_total(self):
        for idx in range(1, 70):
            self.add_archive(idx, origin="OTHER_PROVIDER" if idx % 2 else "PPI_HISTORY")
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
            result = inv.archive_provenance_db("OBSERVER", self.dbpath, 2 * 1024**2)
        self.assertEqual(result["sample_rows"], 64)
        self.assertTrue(result["truncated"])
        self.assertIsNone(result["rows_total"])
        self.assertEqual(result["origin_ppi_history_labeled"], 32)
        self.assertEqual(result["origin_other_labeled"], 32)

    def test_sanitizer_drops_private_values_and_preserves_no_promotion(self):
        self.add_archive(1)
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
            raw_db = inv.archive_provenance_db("HISTORY", self.dbpath, 2 * 1024**2)
        injected = dict(raw_db, secret="PRIVATE_ACCOUNT_TOKEN")
        raw = {"schema": inv.ARCHIVE_SCHEMA, "state": "ARCHIVE_PROVENANCE_ONLY",
               "generated_at_utc": "2026-11-19T10:43:47+00:00",
               "db_inventory": [injected, dict(raw_db, role="OBSERVER")],
               "read_only": True, "os_ro_mount": True, "host_mutations": 0,
               "provider_calls": 0, "ppi_watch_touched": False,
               "real_orders_sent_by_action": 0, "archive_source_selected": False,
               "ppi_provider_contract_verified": False}
        clean = inv.archive_public_receipt(raw, 0)
        self.assertEqual(clean["state"], "ARCHIVE_PROVENANCE_ONLY", clean)
        self.assertNotIn("PRIVATE_ACCOUNT_TOKEN", json.dumps(clean))
        self.assertFalse(clean["archive_source_selected"])
        self.assertFalse(clean["ppi_provider_contract_verified"])

    def test_host_archive_mode_keeps_both_roles_and_never_selects_source(self):
        self.add_archive(1)
        second = self.secondary_archive()
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True), \
                patch.object(inv, "DB_TARGETS", (("HISTORY", self.dbpath, 2 * 1024**2),
                                                 ("OBSERVER", second, 2 * 1024**2))):
            raw = inv.host_archive_provenance()
        self.assertEqual(raw["state"], "ARCHIVE_PROVENANCE_ONLY")
        self.assertEqual([x["role"] for x in raw["db_inventory"]],
                         ["HISTORY", "OBSERVER"])
        self.assertEqual(raw["provider_calls"], 0)
        self.assertEqual(raw["host_mutations"], 0)
        self.assertFalse(raw["archive_source_selected"])
        clean = inv.archive_public_receipt(raw, 0)
        self.assertEqual(clean["state"], "ARCHIVE_PROVENANCE_ONLY", clean)

    def test_archive_rejects_symlink_and_missing_table(self):
        db_link = self.root / "symlink.db"
        db_link.symlink_to(self.dbpath)
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
            self.assertEqual(inv.archive_provenance_db(
                "OBSERVER", db_link, 2 * 1024**2)["status"], "FILE_UNSAFE")
        bad_wal = Path(str(self.dbpath) + "-wal")
        self.writer.close()
        if bad_wal.exists():
            bad_wal.unlink()
        bad_wal.symlink_to(self.dbpath)
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
            self.assertEqual(inv.archive_provenance_db(
                "HISTORY", self.dbpath, 2 * 1024**2)["status"], "FILE_UNSAFE")

    def test_archive_oversize_body_skips_hash_and_invalid_hash_is_counted(self):
        self.add_archive(1, quality="VALID_PAYLOAD", body="x" * (128 * 1024 + 1))
        self.add_archive(2, quality="VALID_PAYLOAD")
        self.writer.execute("UPDATE historical_raw_archive SET body_hash='not-a-sha' WHERE id=2")
        self.writer.commit()
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
            result = inv.archive_provenance_db("HISTORY", self.dbpath, 2 * 1024**2)
        self.assertEqual(result["status"], "OBSERVED_BOUNDED", result)
        self.assertEqual(result["quality_valid_payload_labeled"], 2)
        self.assertEqual(result["oversize_bodies"], 1)
        self.assertEqual(result["hash_unchecked"], 1)
        self.assertEqual(result["hash_mismatched"], 1)
        self.assertEqual(result["hash_matched"], 0)

    def test_archive_oversize_text_columns_do_not_materialize_host_memory(self):
        # The SQLite row is larger than the probe's total 2 MiB byte budget.
        # A SELECT returning arbitrary origin/quality/hash strings would copy
        # multiple MiB into Python before any body limit can run.
        big_origin = "O" * (8 * 1024 * 1024)
        self.add_archive(1, origin=big_origin, body="x" * (8 * 1024 * 1024))
        del big_origin
        tracemalloc.start()
        try:
            with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
                result = inv.archive_provenance_db("HISTORY", self.dbpath, 64 * 1024**2)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(result["status"], "OBSERVED_BOUNDED", result)
        self.assertEqual(result["origin_other_labeled"], 1)
        self.assertEqual(result["oversize_bodies"], 1)
        self.assertLess(peak, 4 * 1024 * 1024)

    def test_archive_schema_ddl_comment_is_never_materialized(self):
        source = self.root / "oversize_ddl.db"
        db = sqlite3.connect(source)
        self.addCleanup(db.close)
        db.execute("PRAGMA journal_mode=WAL")
        ddl = """CREATE TABLE historical_raw_archive(
            id INTEGER PRIMARY KEY AUTOINCREMENT, origin TEXT NOT NULL,
            row_key TEXT NOT NULL, body_hash TEXT NOT NULL,
            recorded_at TEXT NOT NULL, quality TEXT NOT NULL,
            body_json TEXT NOT NULL, UNIQUE(origin,row_key,body_hash))"""
        db.execute(ddl.replace("historical_raw_archive(",
                               "historical_raw_archive /*" +
                               "Z" * (8 * 1024 * 1024) + "*/ ("))
        db.commit()
        self.assertGreater(db.execute("""SELECT length(sql) FROM sqlite_schema
            WHERE name='historical_raw_archive'""").fetchone()[0], 8 * 1024 * 1024)
        tracemalloc.start()
        try:
            with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
                result = inv.archive_provenance_db("HISTORY", source, 64 * 1024**2)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(result["status"], "SQLITE_UNAVAILABLE", result)
        self.assertEqual(result["sample_rows"], 0)
        self.assertLess(peak, 4 * 1024 * 1024)

    def test_archive_schema_oversize_quoted_column_fails_without_allocating(self):
        source = self.root / "oversize_column.db"
        db = sqlite3.connect(source)
        self.addCleanup(db.close)
        db.execute("PRAGMA journal_mode=WAL")
        ddl = ("CREATE TABLE historical_raw_archive (id INTEGER PRIMARY KEY, "
               "origin TEXT, row_key TEXT, body_hash TEXT, recorded_at TEXT, "
               "quality TEXT, body_json TEXT, \"" + "Q" * (4 * 1024 * 1024) +
               "\" TEXT)")
        db.execute(ddl)
        db.commit()
        tracemalloc.start()
        try:
            with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
                result = inv.archive_provenance_db("HISTORY", source, 64 * 1024**2)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertIn(result["status"], ("SCHEMA_INVALID", "SQLITE_UNAVAILABLE"))
        self.assertEqual(result["sample_rows"], 0)
        self.assertLess(peak, 4 * 1024 * 1024)

    def test_archive_deadline_expires_closed_without_partial_counts(self):
        self.add_archive(1)
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True), \
                patch.object(inv, "ARCHIVE_SCAN_SECONDS", 0):
            result = inv.archive_provenance_db("HISTORY", self.dbpath, 2 * 1024**2)
        self.assertEqual(result["status"], "TIME_BUDGET")
        self.assertEqual(result["sample_rows"], 0)

    def test_archive_connection_must_bind_to_pinned_source_inode(self):
        # Holding fd A is not enough if SQLite is redirected to B by a path
        # swap during connect and the original pathname is restored.
        self.add_archive(1)
        alternate = self.root / "alternate.db"
        alternate_writer = sqlite3.connect(alternate)
        self.addCleanup(alternate_writer.close)
        alternate_writer.execute("PRAGMA journal_mode=WAL")
        alternate_writer.execute("""CREATE TABLE historical_raw_archive(
            id INTEGER PRIMARY KEY AUTOINCREMENT, origin TEXT NOT NULL,
            row_key TEXT NOT NULL, body_hash TEXT NOT NULL,
            recorded_at TEXT NOT NULL, quality TEXT NOT NULL,
            body_json TEXT NOT NULL, UNIQUE(origin,row_key,body_hash))""")
        body = '{"fixture":42}'
        for index in (1, 2):
            alternate_writer.execute("INSERT INTO historical_raw_archive VALUES(?,?,?,?,?,?,?)",
                                     (index, "PPI_HISTORY", f"other-{index}",
                                      hashlib.sha256(body.encode()).hexdigest(),
                                      "2026-11-19T10:43:47+00:00", "VALID_PAYLOAD", body))
        alternate_writer.commit()
        original_connect = sqlite3.connect

        def swapped_connection(path, *args, **kwargs):
            if isinstance(path, str) and path.endswith("?mode=ro"):
                return original_connect("file:" + str(alternate) + "?mode=ro",
                                        *args, **kwargs)
            return original_connect(path, *args, **kwargs)

        with patch.object(inv, "archive_readonly_mount_attested", return_value=True), \
                patch.object(inv.sqlite3, "connect", side_effect=swapped_connection):
            result = inv.archive_provenance_db("HISTORY", self.dbpath, 2 * 1024**2)
        self.assertEqual(result["status"], "SOURCE_BINDING_UNVERIFIED", result)
        self.assertEqual(result["sample_rows"], 0)

    def test_archive_unsupported_table_list_cannot_claim_table_absent(self):
        self.add_archive(1)

        class OldSQLitePragma:
            def __init__(self, conn):
                self.conn = conn

            def __getattr__(self, name):
                return getattr(self.conn, name)

            def execute(self, query, *args):
                if query.startswith("PRAGMA table_list"):
                    class EmptyPragma:
                        def fetchall(self):
                            return []
                    return EmptyPragma()
                return self.conn.execute(query, *args)

        result = inv._archive_empty("HISTORY")
        inv._archive_sample(OldSQLitePragma(self.writer),
                            time.monotonic() + 2, result)
        self.assertEqual(result["status"], "SCHEMA_INVALID", result)
        self.assertEqual(result["sample_rows"], 0)

    def test_archive_physically_absent_table_remains_distinct(self):
        absent = self.root / "no-archive.db"
        db = sqlite3.connect(absent)
        self.addCleanup(db.close)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE other_table(id INTEGER PRIMARY KEY)")
        db.commit()
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
            result = inv.archive_provenance_db("HISTORY", absent, 2 * 1024**2)
        self.assertEqual(result["status"], "TABLE_ABSENT", result)
        self.assertEqual(result["sample_rows"], 0)

    def test_archive_sanitizer_fail_closed_on_forged_success_and_counters(self):
        self.add_archive(1)
        second = self.secondary_archive()
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True), \
                patch.object(inv, "DB_TARGETS", (("HISTORY", self.dbpath, 2 * 1024**2),
                                                 ("OBSERVER", second, 2 * 1024**2))):
            raw = inv.host_archive_provenance()
        raw["db_inventory"][0]["sample_rows"] = 65
        self.assertEqual(inv.archive_public_receipt(raw, 0)["state"], "NO_VERIFICADO")
        raw["db_inventory"][0]["sample_rows"] = 1
        raw["ppi_provider_contract_verified"] = True
        self.assertEqual(inv.archive_public_receipt(raw, 0)["state"], "NO_VERIFICADO")

    def test_archive_cli_separate_mode_and_public_sanitization(self):
        self.add_archive(1)
        second = self.secondary_archive()
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True), \
                patch.object(inv, "DB_TARGETS", (("HISTORY", self.dbpath, 2 * 1024**2),
                                                 ("OBSERVER", second, 2 * 1024**2))):
            raw = inv.host_archive_provenance()
            output = io.StringIO()
            with patch.object(sys, "argv", ["inventory", "host-archive-provenance"]), \
                    redirect_stdout(output):
                self.assertEqual(inv.main(), 0)
            observed = json.loads(output.getvalue())
            self.assertEqual(observed["schema"], inv.ARCHIVE_SCHEMA)
        private = self.root / "private-archive.json"
        private.write_text(json.dumps(raw))
        public = self.root / "public-archive.json"
        with patch.object(sys, "argv", ["inventory", "sanitize-archive", "--raw",
                                       str(private), "--out", str(public), "--remote-rc", "0"]):
            self.assertEqual(inv.main(), 0)
        receipt = json.loads(public.read_text())
        self.assertEqual(receipt["state"], "ARCHIVE_PROVENANCE_ONLY")
        self.assertFalse(receipt["archive_source_selected"])

    def test_archive_action_is_opt_in_and_host_data_mount_os_ro(self):
        workflow = (Path(inv.__file__).resolve().parents[1] / ".github/workflows/"
                    "rc6-a729-db-backup-inventory-readonly.yml").read_text()
        self.assertIn("options: [INVENTORY_ONLY, ARCHIVE_PROVENANCE_ONLY, "
                      "SOURCE_LOCATOR_ONLY, EXTERNAL_EXACT_INTEGRITY_ONLY]", workflow)
        self.assertIn("inputs.mode == 'INVENTORY_ONLY'", workflow)
        self.assertIn("inputs.mode == 'ARCHIVE_PROVENANCE_ONLY'", workflow)
        self.assertIn("group: rc6-unified-paper-deploy", workflow)
        self.assertIn("unshare --mount --propagation private", workflow)
        self.assertIn("mount -o remount,bind,ro /opt/porota-trading/data", workflow)
        self.assertIn("host-archive-provenance", workflow)
        self.assertIn("sanitize-archive", workflow)
        self.assertNotIn("docker exec", workflow)
        self.assertNotIn("PPI_API_KEY", workflow)


class HistoricalSourceLocatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.history = self.root / "market_history.db"
        self.history_writer = sqlite3.connect(self.history)
        self.addCleanup(self.history_writer.close)
        self.history_writer.execute("PRAGMA journal_mode=WAL")
        self.history_writer.execute("""CREATE TABLE history_versions_v2(
            id INTEGER PRIMARY KEY, instrument_type TEXT, date TEXT, source TEXT)""")
        self.history_writer.execute("""CREATE TABLE history_canonical_v2(
            instrument_type TEXT, date TEXT, source TEXT)""")
        self.history_writer.execute("""CREATE TABLE market_historical_ohlcv(
            asset_class TEXT, date TEXT, source TEXT)""")
        self.history_writer.commit()
        self.history_writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def test_existing_history_table_detects_committed_wal_row_without_payload(self):
        self.history_writer.execute("INSERT INTO history_versions_v2 VALUES(1,?,?,?)",
                                    ("ACCIONES", "2025-09-22", "SECRET_PPI_TICKER"))
        self.history_writer.commit()
        base_before = self.history.stat().st_mtime_ns
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
            result = inv.source_locator_db(
                "HISTORY", self.history, 2 * 1024**2,
                ("history_versions_v2", "history_canonical_v2", "market_historical_ohlcv"))
        self.assertEqual(result["status"], "OBSERVED_BOUNDED", result)
        self.assertEqual(result["wal_scope"], "WAL_SNAPSHOT_CONSISTENT")
        table = {item["name"]: item for item in result["tables"]}
        self.assertEqual(table["history_versions_v2"]["status"], "PRESENT_COLUMNS_OK")
        self.assertEqual(table["history_versions_v2"]["sample_rows"], 1)
        self.assertEqual(table["history_canonical_v2"]["sample_rows"], 0)
        self.assertIsNone(table["history_versions_v2"]["rows_total"])
        self.assertNotIn("SECRET_PPI_TICKER", json.dumps(result))
        self.assertEqual(self.history.stat().st_mtime_ns, base_before)

    def external_manifest(self):
        path = self.root / "evidence/ppi_history_exact_v1/manifest_exact_v1.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        writer = sqlite3.connect(path)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("""CREATE TABLE exact_attempts_v1(
            origin TEXT, quality TEXT, inner_sha256 TEXT, wrapper_json TEXT)""")
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return path, writer

    def test_external_exact_manifest_labels_are_not_provider_proof(self):
        path, writer = self.external_manifest()
        writer.executemany("INSERT INTO exact_attempts_v1 VALUES (?,?,?,?)", [
            ("PPI_HISTORY", "UNVALIDATED", "a" * 64, "PRIVATE_TICKER_A"),
            ("OTHER_SOURCE", "VALID_PAYLOAD", "b" * 64, "PRIVATE_TICKER_B"),
        ])
        writer.commit()
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
            result = inv.source_locator_db("EXTERNAL_EXACT", path, 2 * 1024**2,
                                           ("exact_attempts_v1",))
        self.assertEqual(result["status"], "OBSERVED_BOUNDED", result)
        self.assertEqual(result["wal_scope"], "WAL_SNAPSHOT_CONSISTENT")
        table, = result["tables"]
        self.assertEqual(table["status"], "PRESENT_COLUMNS_OK")
        self.assertEqual(table["sample_rows"], 2)
        self.assertEqual(table["origin_ppi_history_labeled"], 1)
        self.assertEqual(table["quality_unvalidated_labeled"], 1)
        self.assertEqual(table["quality_valid_payload_labeled"], 1)
        self.assertIsNone(table["rows_total"])
        self.assertNotIn("PRIVATE_TICKER", json.dumps(result))

    def test_external_integrity_checks_inner_and_rebuilt_body_with_no_raw_export(self):
        path, writer = self.external_manifest()
        writer.executescript("""ALTER TABLE exact_attempts_v1 ADD COLUMN body_bytes INTEGER;
            ALTER TABLE exact_attempts_v1 ADD COLUMN body_sha256 TEXT;
            ALTER TABLE exact_attempts_v1 ADD COLUMN body_hash TEXT;""")
        objects = path.parent / "objects/sha256"
        for variant in ("match", "full_fields", "empty", "mixed", "wrong_body", "wrong_inner", "symlink", "oversize",
                        "bad_digest", "unvalidated", "other_source"):
            bar = {"price": 1, "symbol": f"PRIVATE_TICKER_{variant}"}
            full = {**bar, "quoteCurrency": "ARS", "priceBasis": "RAW",
                    "adjustmentBasis": "RAW_NO_ADJUSTMENT", "adjusted": False}
            bars = {"full_fields": [full], "wrong_body": [full], "empty": [],
                    "mixed": [full, bar]}.get(variant, [bar])
            payload = json.dumps(bars).encode("utf-8")
            digest = hashlib.sha256(payload).hexdigest()
            wrapper = json.dumps({"symbol": "PRIVATE_TICKER_Ñ", "currency": "ARS",
                                  "priceBasis": "RAW", "adjustmentBasis": "RAW_NO_ADJUSTMENT",
                                  "adjusted": False}, ensure_ascii=False)
            body = json.dumps({**json.loads(wrapper), "payload_json": payload.decode()},
                              sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
            body_sha = hashlib.sha256(body).hexdigest()
            obj = objects / digest[:2] / f"{digest}.json.gz"
            obj.parent.mkdir(parents=True, exist_ok=True)
            if variant == "symlink":
                obj.symlink_to(self.history)
            else:
                obj.write_bytes(gzip.compress(payload if variant != "wrong_inner" else b"x" * 4096))
            writer.execute("INSERT INTO exact_attempts_v1 VALUES(?,?,?,?,?,?,?)", (
                "OTHER" if variant == "other_source" else "PPI_HISTORY",
                "UNVALIDATED" if variant == "unvalidated" else "VALID_PAYLOAD",
                "../invalid" if variant == "bad_digest" else digest, wrapper,
                inv.EXACT_MAX_BODY_BYTES + 1 if variant == "oversize" else len(body),
                body_sha, "a" * 64 if variant == "wrong_body" else body_sha,
            ))
        writer.commit()
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True), \
                patch.object(inv.os, "fstatvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)), \
                patch.object(inv, "LOCATOR_OBJECTS_ROOT", objects), \
                patch.object(inv, "LOCATOR_TARGETS",
                             (("EXTERNAL_EXACT", path, 2 * 1024**2),)):
            raw = inv.host_source_locator(verify_external_exact=True)
            part = raw["source_inventory"][0]["tables"][0]
            counters = part["exact_integrity"]
            self.assertEqual(part["sample_rows"], 11)
            self.assertEqual(counters, {
                "eligible_rows": 9, "matched": 4, "invalid_manifest": 1, "unreadable_object": 1,
                "inner_hash_mismatch": 1, "body_hash_mismatch": 1, "byte_budget_exceeded": 1,
            })
            fields = part["exact_payload_fields"]
            self.assertEqual(fields, dict(zip(inv.EXACT_PAYLOAD_FIELDS, (3, 1, 1, 1))))
            counters["secret"] = "PRIVATE_CREDENTIAL"
            fields["secret"] = "PRIVATE_CREDENTIAL"
            clean = inv.source_locator_public_receipt(raw, 0, verify_external_exact=True)
            self.assertEqual(clean["state"], inv.EXACT_INTEGRITY_MODE)
            self.assertFalse(clean["ppi_provider_contract_verified"])
            self.assertFalse(clean["history_source_selected"])
            self.assertNotIn("PRIVATE_", json.dumps(clean))
            fields["all_bars_have_both"] = 2
            self.assertEqual(inv.source_locator_public_receipt(
                raw, 0, verify_external_exact=True)["state"], "NO_VERIFICADO")
            fields["all_bars_have_both"] = 1
            counters["matched"] = 6
            self.assertEqual(inv.source_locator_public_receipt(
                raw, 0, verify_external_exact=True)["state"], "NO_VERIFICADO")

    def test_absent_manifest_is_not_falsely_reported_empty_table(self):
        missing = self.root / "not-created/manifest_exact_v1.db"
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
            result = inv.source_locator_db("EXTERNAL_EXACT", missing, 2 * 1024**2,
                                           ("exact_attempts_v1",))
        self.assertEqual(result["status"], "MISSING")
        self.assertEqual(result["tables"][0]["status"], "NO_VERIFICADO")
        self.assertEqual(result["tables"][0]["sample_rows"], 0)

    def test_missing_external_ancestors_use_pinned_ro_mount_not_false_hold(self):
        class VFS:
            f_flag = os.ST_RDONLY

        missing = self.root / "missing-parent/evidence/manifest_exact_v1.db"
        objects = self.root / "missing-parent/evidence/objects/sha256"
        with patch.object(inv, "DATA", self.root), \
                patch.object(inv, "archive_readonly_mount_attested", return_value=False), \
                patch.object(inv.os, "fstatvfs", return_value=VFS()):
            self.assertTrue(inv._locator_readonly_mount_attested(missing))
            self.assertEqual(inv.source_locator_db("EXTERNAL_EXACT", missing,
                2 * 1024**2, ("exact_attempts_v1",))["status"], "MISSING")
            self.assertEqual(inv._locator_objects_root_state(objects), "MISSING")
        class RwVFS:
            f_flag = 0
        with patch.object(inv, "DATA", self.root), \
                patch.object(inv, "archive_readonly_mount_attested", return_value=False), \
                patch.object(inv.os, "fstatvfs", return_value=RwVFS()):
            self.assertFalse(inv._locator_readonly_mount_attested(missing))
            self.assertEqual(inv.source_locator_db("EXTERNAL_EXACT", missing,
                2 * 1024**2, ("exact_attempts_v1",))["status"],
                "RO_MOUNT_UNVERIFIED")

    def test_host_locator_reads_three_fixed_sources_and_never_selects_any(self):
        manifest, writer = self.external_manifest()
        writer.execute("INSERT INTO exact_attempts_v1 VALUES(?,?,?,?)",
                       ("PPI_HISTORY", "UNVALIDATED", "a" * 64, "HIDDEN"))
        writer.commit()
        objects = manifest.parent / "objects/sha256"
        objects.mkdir(parents=True)
        missing_observer = self.root / "missing-observer.db"
        targets = (("HISTORY", self.history, 2 * 1024**2),
                   ("OBSERVER", missing_observer, 2 * 1024**2),
                   ("EXTERNAL_EXACT", manifest, 2 * 1024**2))
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True), \
                patch.object(inv, "LOCATOR_TARGETS", targets), \
                patch.object(inv, "LOCATOR_OBJECTS_ROOT", objects):
            raw = inv.host_source_locator()
        self.assertEqual(raw["state"], "SOURCE_LOCATOR_ONLY")
        self.assertEqual([x["role"] for x in raw["source_inventory"]],
                         ["HISTORY", "OBSERVER", "EXTERNAL_EXACT"])
        self.assertEqual(raw["source_inventory"][1]["status"], "MISSING")
        self.assertEqual(raw["objects_root_state"], "PRESENT")
        self.assertFalse(raw["history_source_selected"])
        self.assertFalse(raw["ppi_provider_contract_verified"])
        self.assertEqual(raw["provider_calls"], 0)
        self.assertEqual(raw["host_mutations"], 0)

    def test_source_locator_sanitizer_strips_host_secrets_and_rejects_forgery(self):
        manifest, writer = self.external_manifest()
        writer.execute("INSERT INTO exact_attempts_v1 VALUES(?,?,?,?)",
                       ("PPI_HISTORY", "UNVALIDATED", "a" * 64, "PRIVATE"))
        writer.commit()
        targets = (("HISTORY", self.history, 2 * 1024**2),
                   ("OBSERVER", self.root / "missing.db", 2 * 1024**2),
                   ("EXTERNAL_EXACT", manifest, 2 * 1024**2))
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True), \
                patch.object(inv, "LOCATOR_TARGETS", targets):
            raw = inv.host_source_locator()
        raw["secret"] = "PRIVATE_CREDENTIAL"
        raw["source_inventory"][2]["secret"] = "PRIVATE_ACCOUNT"
        clean = inv.source_locator_public_receipt(raw, 0)
        self.assertEqual(clean["state"], "SOURCE_LOCATOR_ONLY", clean)
        self.assertNotIn("PRIVATE_", json.dumps(clean))
        self.assertFalse(clean["history_source_selected"])
        raw["history_source_selected"] = True
        self.assertEqual(inv.source_locator_public_receipt(raw, 0)["state"],
                         "NO_VERIFICADO")
        self.assertEqual(inv.source_locator_public_receipt(raw, 255)["state"],
                         "NO_VERIFICADO")

    def test_locator_reports_sqlite_phase_without_exporting_db_errors(self):
        class JournalModeFailure(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if sql == "PRAGMA journal_mode":
                    error = sqlite3.OperationalError("PRIVATE_DB_PATH")
                    error.sqlite_errorname = "SQLITE_READONLY_CANTLOCK"
                    error.sqlite_errorcode = sqlite3.SQLITE_READONLY_CANTLOCK
                    raise error
                return super().execute(sql, parameters)

        connect = sqlite3.connect
        targets = tuple((role, self.history if role == "HISTORY" else self.root / role,
                         2 * 1024**2) for role in inv.LOCATOR_TABLES)
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True), \
                patch.object(inv, "LOCATOR_TARGETS", targets), \
                patch.object(inv.sqlite3, "connect",
                             side_effect=lambda *args, **kwargs: connect(
                                 *args, factory=JournalModeFailure, **kwargs)):
            raw = inv.host_source_locator()
        history = raw["source_inventory"][0]
        self.assertEqual(history["status"], "SQLITE_UNAVAILABLE")
        self.assertEqual(history["sqlite_failure"], {
            "phase": "READ_JOURNAL_MODE", "wal_file": "EMPTY", "shm_present": True,
            "error_code": sqlite3.SQLITE_READONLY_CANTLOCK, "error_name": "SQLITE_READONLY_CANTLOCK",
        })
        history["sqlite_failure"]["debug"] = "PRIVATE_DB_PATH"
        clean = inv.source_locator_public_receipt(raw, 0)
        self.assertEqual(clean["state"], "NO_VERIFICADO")
        self.assertFalse(clean["history_source_selected"])
        self.assertNotIn("PRIVATE_DB_PATH", json.dumps(clean))
        self.assertEqual(clean["source_inventory"][0]["sqlite_failure"]["phase"], "READ_JOURNAL_MODE")
        history["sqlite_failure"]["error_name"] = "SQLITE_UNKNOWN_PRIVATE_LABEL"
        unverified = inv.source_locator_public_receipt(raw, 0)
        self.assertNotIn("error_name", unverified["source_inventory"][0]["sqlite_failure"])

    def test_external_manifest_latest_64_rowids_and_long_text_memory_budget(self):
        manifest, writer = self.external_manifest()
        writer.executemany("INSERT INTO exact_attempts_v1 VALUES(?,?,?,?)", [
            ("PPI_HISTORY", "UNVALIDATED", "a" * 64, "SECRET_TICKER")
            for _ in range(68)])
        writer.execute("INSERT INTO exact_attempts_v1 VALUES(?,?,?,?)",
                       ("O" * (8 * 1024 * 1024), "Q" * (8 * 1024 * 1024),
                        "a" * 64, "W" * (8 * 1024 * 1024)))
        writer.commit()
        tracemalloc.start()
        try:
            with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
                result = inv.source_locator_db("EXTERNAL_EXACT", manifest,
                                               64 * 1024**2, ("exact_attempts_v1",))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(result["status"], "OBSERVED_BOUNDED", result)
        table, = result["tables"]
        self.assertEqual(table["sample_rows"], 64)
        self.assertTrue(table["truncated"])
        self.assertEqual(table["origin_ppi_history_labeled"], 63)
        self.assertEqual(table["quality_unvalidated_labeled"], 63)
        self.assertEqual(table["quality_other_labeled"], 1)
        self.assertLess(peak, 4 * 1024 * 1024)
        self.assertNotIn("SECRET_TICKER", json.dumps(result))

    def test_locator_rejects_symlink_and_wrong_physical_sqlite_inode(self):
        link = self.root / "history-link.db"
        link.symlink_to(self.history)
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
            self.assertEqual(inv.source_locator_db("HISTORY", link,
                2 * 1024**2, inv.LOCATOR_TABLES["HISTORY"])["status"], "FILE_UNSAFE")
        alternate = self.root / "foreign.db"
        writer = sqlite3.connect(alternate)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE history_versions_v2(id INTEGER PRIMARY KEY)")
        writer.commit()
        original_connect = sqlite3.connect

        def redirect(path, *args, **kwargs):
            if isinstance(path, str) and path.endswith("?mode=ro"):
                return original_connect("file:" + str(alternate) + "?mode=ro",
                                        *args, **kwargs)
            return original_connect(path, *args, **kwargs)

        with patch.object(inv, "archive_readonly_mount_attested", return_value=True), \
                patch.object(inv.sqlite3, "connect", side_effect=redirect):
            result = inv.source_locator_db("HISTORY", self.history, 2 * 1024**2,
                                           inv.LOCATOR_TABLES["HISTORY"])
        self.assertEqual(result["status"], "SOURCE_BINDING_UNVERIFIED")
        self.assertTrue(all(x["sample_rows"] == 0 for x in result["tables"]))

    def test_locator_unsupported_table_list_cannot_claim_absent(self):
        class UnsupportedPragma:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, sql, *args):
                if sql.startswith("PRAGMA table_list"):
                    class Empty:
                        def fetchall(self):
                            return []
                    return Empty()
                return self.connection.execute(sql, *args)

        result = inv._locator_empty("HISTORY")
        inv._locator_scan(UnsupportedPragma(self.history_writer), "HISTORY",
                          time.monotonic() + 2, result)
        self.assertEqual(result["tables"][0]["status"], "SCHEMA_INVALID")
        self.assertEqual(result["tables"][0]["sample_rows"], 0)

    def test_locator_unsupported_table_xinfo_cannot_probe_any_rows(self):
        class UnsupportedXinfo:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, sql, *args):
                if sql.startswith("PRAGMA table_xinfo"):
                    class Empty:
                        def fetchall(self):
                            return []
                    return Empty()
                return self.connection.execute(sql, *args)

        result = inv._locator_empty("HISTORY")
        inv._locator_scan(UnsupportedXinfo(self.history_writer), "HISTORY",
                          time.monotonic() + 2, result)
        self.assertEqual(result["tables"][0]["status"], "COLUMNS_MISSING")
        self.assertEqual(result["tables"][0]["sample_rows"], 0)

    def test_locator_refuses_shadowed_rowid_that_could_force_full_sort(self):
        for alias in ("rowid", "_rowid_", "oid"):
            with self.subTest(alias=alias):
                manifest = self.root / f"shadowed-{alias}.db"
                with sqlite3.connect(manifest) as writer:
                    writer.execute("PRAGMA journal_mode=WAL")
                    writer.execute(f"""CREATE TABLE exact_attempts_v1(
                        {alias} INTEGER, origin TEXT, quality TEXT,
                        inner_sha256 TEXT, wrapper_json TEXT)""")
                    writer.execute("INSERT INTO exact_attempts_v1 VALUES (?,?,?,?,?)",
                                   (1, "PPI_HISTORY", "UNVALIDATED",
                                    "a" * 64, "SECRET_BODY"))
                with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
                    result = inv.source_locator_db("EXTERNAL_EXACT", manifest,
                                                   2 * 1024**2, ("exact_attempts_v1",))
                self.assertEqual(result["status"], "OBSERVED_BOUNDED", result)
                self.assertEqual(result["tables"][0]["status"], "SCHEMA_INVALID")
                self.assertEqual(result["tables"][0]["sample_rows"], 0)
                self.assertNotIn("SECRET", json.dumps(result))

    def test_locator_rejects_generated_rowid_shadow_hidden_from_table_info(self):
        manifest = self.root / "generated-shadow.db"
        with sqlite3.connect(manifest) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("""CREATE TABLE exact_attempts_v1(
                origin TEXT, quality TEXT, inner_sha256 TEXT, wrapper_json TEXT,
                rowid TEXT GENERATED ALWAYS AS (substr(wrapper_json,1,16)) VIRTUAL)""")
            writer.execute("INSERT INTO exact_attempts_v1(origin,quality,inner_sha256,wrapper_json)"
                           " VALUES (?,?,?,?)",
                           ("PPI_HISTORY", "UNVALIDATED", "a" * 64, "SECRET_BODY"))
            self.assertNotIn("rowid", [c[1] for c in
                             writer.execute("PRAGMA table_info(exact_attempts_v1)")])
            self.assertIn("rowid", [c[1] for c in
                          writer.execute("PRAGMA table_xinfo(exact_attempts_v1)")])
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
            result = inv.source_locator_db("EXTERNAL_EXACT", manifest,
                                           2 * 1024**2, ("exact_attempts_v1",))
        self.assertEqual(result["status"], "OBSERVED_BOUNDED", result)
        self.assertEqual(result["tables"][0]["status"], "SCHEMA_INVALID")
        self.assertEqual(result["tables"][0]["sample_rows"], 0)

    def test_locator_big_sqlite_ddl_is_safe_rejection_without_heap_growth(self):
        manifest = self.root / "large-schema.db"
        db = sqlite3.connect(manifest)
        self.addCleanup(db.close)
        db.execute("PRAGMA journal_mode=WAL")
        ddl = """CREATE TABLE exact_attempts_v1(
            origin TEXT,quality TEXT,inner_sha256 TEXT,wrapper_json TEXT)"""
        db.execute(ddl.replace("exact_attempts_v1(",
                               "exact_attempts_v1 /*" +
                               "X" * (4 * 1024 * 1024) + "*/ ("))
        db.commit()
        tracemalloc.start()
        try:
            with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
                result = inv.source_locator_db("EXTERNAL_EXACT", manifest,
                                               64 * 1024**2, ("exact_attempts_v1",))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(result["status"], "SQLITE_UNAVAILABLE")
        self.assertEqual(result["tables"][0]["sample_rows"], 0)
        self.assertLess(peak, 4 * 1024 * 1024)

    def test_external_objects_directory_symlink_is_not_followed(self):
        link = self.root / "object-link"
        link.symlink_to(self.root, target_is_directory=True)
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True):
            self.assertEqual(inv._locator_objects_root_state(link), "PATH_UNSAFE")

    def test_source_locator_workflow_manual_ro_and_two_existing_modes_stable(self):
        workflow = (Path(inv.__file__).resolve().parents[1] / ".github/workflows/"
                    "rc6-a729-db-backup-inventory-readonly.yml").read_text()
        self.assertIn("options: [INVENTORY_ONLY, ARCHIVE_PROVENANCE_ONLY, "
                      "SOURCE_LOCATOR_ONLY, EXTERNAL_EXACT_INTEGRITY_ONLY]", workflow)
        self.assertIn("inputs.mode == 'SOURCE_LOCATOR_ONLY'", workflow)
        self.assertIn("host-source-locator", workflow)
        self.assertIn("sanitize-source", workflow)
        self.assertIn("host_mode=host-exact-integrity", workflow)
        self.assertIn("sanitize_mode=sanitize-exact-integrity", workflow)
        self.assertIn("unshare --mount --propagation private", workflow)
        self.assertIn("mount -o remount,bind,ro /opt/porota-trading/data", workflow)
        self.assertIn("group: rc6-unified-paper-deploy", workflow)
        self.assertIn("inputs.mode == 'INVENTORY_ONLY'", workflow)
        self.assertIn("inputs.mode == 'ARCHIVE_PROVENANCE_ONLY'", workflow)
        self.assertNotIn("docker exec", workflow)
        new_job = workflow.split("\n  host_source_locator:\n", 1)
        self.assertEqual(len(new_job), 2)
        self.assertIn("github.actor == 'mbalbo2023'", new_job[1])
        self.assertIn("github.triggering_actor == 'mbalbo2023'", new_job[1])
        self.assertNotIn("PPI_API_KEY", new_job[1])
        self.assertNotIn("docker ", new_job[1])

    def test_source_locator_cli_has_separate_private_and_sanitized_modes(self):
        manifest, _ = self.external_manifest()
        targets = (("HISTORY", self.history, 2 * 1024**2),
                   ("OBSERVER", self.root / "missing.db", 2 * 1024**2),
                   ("EXTERNAL_EXACT", manifest, 2 * 1024**2))
        raw_output = io.StringIO()
        with patch.object(inv, "archive_readonly_mount_attested", return_value=True), \
                patch.object(inv, "LOCATOR_TARGETS", targets), \
                patch.object(sys, "argv", ["inventory", "host-source-locator"]), \
                redirect_stdout(raw_output):
            self.assertEqual(inv.main(), 0)
        raw = json.loads(raw_output.getvalue())
        self.assertEqual(raw["schema"], inv.LOCATOR_SCHEMA)
        private = self.root / "private-locator.json"
        private.write_text(json.dumps(raw))
        public = self.root / "public-locator.json"
        with patch.object(sys, "argv", ["inventory", "sanitize-source",
                                      "--raw", str(private), "--out", str(public),
                                      "--remote-rc", "0"]):
            self.assertEqual(inv.main(), 0)
        clean = json.loads(public.read_text())
        self.assertEqual(clean["state"], "SOURCE_LOCATOR_ONLY")
        self.assertFalse(clean["history_source_selected"])


if __name__ == "__main__":
    unittest.main()
