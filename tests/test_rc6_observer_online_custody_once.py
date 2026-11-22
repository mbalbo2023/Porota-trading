"""Real SQLite/WAL and fail-closed contracts for one-shot OBSERVER custody."""
from __future__ import annotations

import errno
import base64
import importlib.util
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import yaml


SOURCE = Path(__file__).resolve().parents[1] / "scripts/rc6_observer_online_custody_once.py"
spec = importlib.util.spec_from_file_location("rc6_observer_online_custody_once", SOURCE)
custody = importlib.util.module_from_spec(spec)
spec.loader.exec_module(custody)


class LiveWalFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "data/paper_v17/observer_v17.db"
        self.db.parent.mkdir(parents=True)
        self.marker = self.root / "data/deploy/CURRENT_STATE_V2.json"
        self.marker.parent.mkdir(parents=True)
        self.custody_root = self.root / "data/rc6_observer_custody"
        self.marker.write_text(json.dumps({
            "candidate_sha": custody.EXPECTED_SOURCE_SHA,
            "candidate_tree_sha": custody.EXPECTED_TREE_SHA,
            "image_id": custody.EXPECTED_IMAGE_ID,
            "validation_status": "VALIDATED_RUNTIME", "mode": "PRODUCTION_PAPER",
            "real_orders_sent": 0, "real_order_capability": "BLOCKED",
            "real_order_routes": "NOT_CALLED",
            "deployment_backend": "ROOTLESS_SPLIT",
        }))
        self.writer = sqlite3.connect(self.db)
        self.addCleanup(self.writer.close)
        self.writer.execute("PRAGMA journal_mode=WAL")
        self.writer.execute("PRAGMA wal_autocheckpoint=0")
        self.writer.execute("CREATE TABLE observer_state (id INTEGER PRIMARY KEY, mode TEXT, real_orders_sent INTEGER, process_state TEXT, session_state TEXT)")
        self.writer.execute("INSERT INTO observer_state VALUES (1,'PRODUCTION_PAPER',0,'RUNNING','MARKET_CLOSED')")
        self.writer.execute("CREATE TABLE paper_exit_reader_state (id INTEGER PRIMARY KEY, state TEXT)")
        self.writer.execute("INSERT INTO paper_exit_reader_state VALUES (1,'WAITING_MARKET')")
        self.writer.execute("CREATE TABLE paper_positions (status TEXT)")
        self.writer.execute("CREATE TABLE paper_future_positions (status TEXT)")
        self.writer.execute("CREATE TABLE paper_cauciones (status TEXT)")
        self.writer.execute("CREATE TABLE paper_family_lifecycle (family TEXT, state TEXT)")
        self.writer.execute("CREATE TABLE historical_rows (id INTEGER PRIMARY KEY, price INTEGER)")
        self.writer.execute("INSERT INTO historical_rows VALUES (1,11)")
        self.writer.commit()
        # This is committed in the live WAL; immutable=1 would miss it.
        self.writer.execute("INSERT INTO historical_rows VALUES (2,22)")
        self.writer.commit()
        self.assertGreater(Path(str(self.db) + "-wal").stat().st_size, 0)

    def _file_metadata(self):
        return [(p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes())
                for p in (self.db, Path(str(self.db) + "-wal"),
                          Path(str(self.db) + "-shm"))]


class PreflightContracts(LiveWalFixture):
    def test_metadata_only_does_not_open_sqlite_or_create_destination(self):
        before = self._file_metadata()
        result = custody.host_preflight(self.db, self.marker, self.custody_root)
        self.assertEqual(result["state"], "PREFLIGHT_METADATA_PASS")
        self.assertEqual(result["source_wal_state"], "PRESENT_UNRECONCILED")
        self.assertEqual(result["paper_zero"], "NO_VERIFICADO")
        self.assertEqual(result["host_mutations"], 0)
        self.assertGreaterEqual(result["free_bytes"], result["required_bytes"])
        self.assertFalse(self.custody_root.exists())
        self.assertEqual(before, self._file_metadata())

    def test_bad_release_identity_is_rejected_before_any_write(self):
        marker = json.loads(self.marker.read_text())
        marker["real_order_capability"] = "LIVE"
        self.marker.write_text(json.dumps(marker))
        with self.assertRaises(custody.Hold) as raised:
            custody.host_preflight(self.db, self.marker, self.custody_root)
        self.assertEqual(str(raised.exception), "RELEASE_PAPER_IDENTITY_UNVERIFIED")
        self.assertFalse(self.custody_root.exists())

    def test_missing_or_spoofed_image_or_real_order_route_fails_closed(self):
        original = json.loads(self.marker.read_text())
        for field, value in (("image_id", None),
                             ("image_id", "sha256:" + "f" * 64),
                             ("real_order_routes", None),
                             ("real_order_routes", "CALLED")):
            with self.subTest(field=field, value=value):
                marker = dict(original)
                if value is None:
                    del marker[field]
                else:
                    marker[field] = value
                self.marker.write_text(json.dumps(marker))
                with self.assertRaises(custody.Hold) as raised:
                    custody.host_preflight(self.db, self.marker, self.custody_root)
                self.assertEqual(str(raised.exception), "RELEASE_PAPER_IDENTITY_UNVERIFIED")
                self.assertFalse(self.custody_root.exists())
        self.marker.write_text(json.dumps(original))

    def test_marker_read_is_anchored_and_never_follows_symlink(self):
        actual_open = os.open
        calls = []
        def read_provenance(name, flags, *args, **kwargs):
            if name == "CURRENT_STATE_V2.json":
                calls.append((flags, kwargs))
            return actual_open(name, flags, *args, **kwargs)
        with patch.object(custody.os, "open", side_effect=read_provenance):
            self.assertEqual(custody.host_preflight(
                self.db, self.marker, self.custody_root)["state"],
                "PREFLIGHT_METADATA_PASS")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0] & os.O_NOFOLLOW)
        self.assertIn("dir_fd", calls[0][1])

    def test_symlinked_database_is_rejected_without_following(self):
        original = self.root / "source-original.db"
        self.db.rename(original)
        self.db.symlink_to(original)
        with self.assertRaises(custody.Hold) as raised:
            custody.host_preflight(self.db, self.marker, self.custody_root)
        self.assertEqual(str(raised.exception), "SOURCE_FILE_UNSAFE")
        self.assertFalse(self.custody_root.exists())

    def test_disk_reserve_is_a_blocking_gate_before_directory_creation(self):
        with patch.object(custody.shutil, "disk_usage") as usage:
            usage.return_value.free = 2 * 1024**3 - 1
            with self.assertRaises(custody.Hold) as raised:
                custody.host_preflight(self.db, self.marker, self.custody_root)
        self.assertEqual(str(raised.exception), "CUSTODY_DISK_RESERVE_BLOCKED")
        self.assertFalse(self.custody_root.exists())

    def test_capacity_check_probes_private_destination_not_live_db_filesystem(self):
        with patch.object(custody.shutil, "disk_usage") as usage:
            usage.return_value.free = 123 * 1024**3
            custody.host_preflight(self.db, self.marker, self.custody_root)
        usage.assert_called_once_with(self.custody_root.parent)

    def test_writable_custody_parent_is_not_trusted_by_root_writer(self):
        self.custody_root.parent.chmod(0o777)
        with self.assertRaises(custody.Hold) as raised:
            custody.host_preflight(self.db, self.marker, self.custody_root)
        self.assertEqual(str(raised.exception), "CUSTODY_DIRECTORY_UNSAFE")
        self.assertFalse(self.custody_root.exists())


class OneShotCustodyContracts(LiveWalFixture):
    RUN_ID = "44508298821"

    def _custody(self):
        # Real host binding is separately tested; these SQLite fixtures do not
        # have the private rootless socket/current.json of the droplet.
        with patch.object(custody, "_rootless_runtime_binding", return_value="BOTH_ROOTLESS_CIDS_DATA_DB_INODE_MATCHED"):
            return custody.host_custody_once(self.db, self.marker, self.custody_root,
                                             run_id=self.RUN_ID)

    def test_closed_paper_zero_exit_flat_admits_custody_even_at_14_art(self):
        # Thursday 14:00 ART (17:00 UTC) is not a storage-consistency signal.
        class ClockAt14ART:
            @staticmethod
            def now(tz=None):
                instant = datetime(2026, 11, 19, 17, 0, tzinfo=timezone.utc)
                return instant.astimezone(tz) if tz is not None else instant

        with patch.object(custody, "datetime", ClockAt14ART):
            receipt = self._custody()
        self.assertEqual(receipt["state"], "PRIVATE_SNAPSHOT_RESTORE_OK")
        self.assertEqual(receipt["paper_zero"], "OBSERVED")
        self.assertEqual(receipt["exit_quiescence"],
                         "EXIT_WAITING_MARKET_CLOSED_FOUR_LEDGERS_FLAT_SAMPLED")
        self.assertNotIn("offhours_gate", receipt)

    def test_exit_activity_or_open_positions_holds_before_destination(self):
        for update in (
            "UPDATE paper_exit_reader_state SET state='RUNNING' WHERE id=1",
            "INSERT INTO paper_positions VALUES ('OPEN')",
            "UPDATE observer_state SET session_state='OPEN' WHERE id=1",
        ):
            with self.subTest(update=update):
                self.writer.execute(update)
                self.writer.commit()
                with self.assertRaises(custody.Hold) as raised:
                    self._custody()
                self.assertEqual(str(raised.exception), "EXIT_QUIESCENCE_UNVERIFIED")
                self.assertFalse(self.custody_root.exists())
                if update.startswith("INSERT"):
                    self.writer.execute("DELETE FROM paper_positions")
                elif "paper_exit_reader_state" in update:
                    self.writer.execute("UPDATE paper_exit_reader_state SET state='WAITING_MARKET'")
                else:
                    self.writer.execute("UPDATE observer_state SET session_state='MARKET_CLOSED'")
                self.writer.commit()

    def test_missing_exit_witness_is_never_assumed_safe(self):
        self.writer.execute("DROP TABLE paper_exit_reader_state")
        self.writer.commit()
        with self.assertRaises(custody.Hold) as raised:
            self._custody()
        self.assertEqual(str(raised.exception), "EXIT_QUIESCENCE_UNVERIFIED")
        self.assertFalse(self.custody_root.exists())

    def test_exit_degraded_during_market_closed_holds_without_clock_override(self):
        self.writer.execute("UPDATE paper_exit_reader_state SET state='DEGRADED' WHERE id=1")
        self.writer.commit()
        with self.assertRaises(custody.Hold) as raised:
            self._custody()
        self.assertEqual(str(raised.exception), "EXIT_QUIESCENCE_UNVERIFIED")
        self.assertFalse(self.custody_root.exists())

    def test_online_snapshot_contains_committed_wal_and_restores_privately(self):
        self.assertNotIn(str(custody.CUSTODY), str(custody.ROOT))
        self.assertTrue(str(custody.CUSTODY).startswith(
            "/home/porotaadmin/.local/share/"))
        before_source = [(p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes())
                         for p in (self.db, Path(str(self.db) + "-wal"))]
        receipt = self._custody()
        self.assertEqual(receipt["state"], "PRIVATE_SNAPSHOT_RESTORE_OK")
        self.assertEqual(receipt["paper_zero"], "OBSERVED")
        self.assertEqual(receipt["source_wal_state"], "PRESENT_RECONCILED_BY_SQLITE_BACKUP")
        self.assertEqual(receipt["source_sql_writes"], 0)
        self.assertIs(receipt["shm_locking_possible"], True)
        self.assertNotIn("source_mutations", receipt)
        private = self.custody_root / ("observer_" + self.RUN_ID)
        snapshot = private / "snapshot.sqlite"
        restored = private / "restore-check.sqlite"
        self.assertTrue(snapshot.is_file())
        self.assertTrue(restored.is_file())
        self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o600)
        self.assertEqual(hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                         receipt["snapshot_sha256"])
        self.assertEqual(hashlib.sha256(restored.read_bytes()).hexdigest(),
                         receipt["restore_sha256"])
        self.assertEqual(receipt["snapshot_sha256"], receipt["restore_sha256"])
        for path in (snapshot, restored):
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM historical_rows").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT price FROM historical_rows WHERE id=2").fetchone()[0], 22)
                self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
        self.assertEqual(before_source,
                         [(p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes())
                          for p in (self.db, Path(str(self.db) + "-wal"))])

    def test_online_backup_pins_one_wal_snapshot_while_writer_advances(self):
        self.writer.execute("CREATE TABLE backup_pages (body BLOB)")
        self.writer.execute("INSERT INTO backup_pages VALUES (zeroblob(2097152))")
        self.writer.commit()
        writer = self.writer
        remaining_pages = []
        connect = sqlite3.connect

        class ActiveWalSource(sqlite3.Connection):
            def backup(self, target, *, pages, sleep, progress):
                def write_between_chunks(status, remaining, total):
                    remaining_pages.append(remaining)
                    if status == sqlite3.SQLITE_OK and len(remaining_pages) <= 3:
                        writer.execute("UPDATE historical_rows SET price=price+1 WHERE id=2")
                        writer.commit()
                    progress(status, remaining, total)

                super().backup(target, pages=pages, sleep=sleep, progress=write_between_chunks)

        def connect_with_active_writer(database, *args, **kwargs):
            if kwargs.get("uri") and str(database).endswith(f"/{self.db.name}?mode=ro"):
                kwargs["factory"] = ActiveWalSource
            return connect(database, *args, **kwargs)

        with patch.object(custody.sqlite3, "connect", side_effect=connect_with_active_writer):
            receipt = self._custody()
        self.assertTrue(all(before > after for before, after in
                            zip(remaining_pages, remaining_pages[1:])), remaining_pages)
        self.assertGreater(len(remaining_pages), 2)
        self.assertEqual(receipt["state"], "PRIVATE_SNAPSHOT_RESTORE_OK")
        self.assertEqual(receipt["paper_zero"], "OBSERVED")
        self.assertTrue(receipt["source_identity_continuity"].startswith(
            "DB_IDENTITY_STABLE_WAL_CHANGED_SHM_"))
        restored = self.custody_root / ("observer_" + self.RUN_ID) / "restore-check.sqlite"
        with sqlite3.connect(restored.as_uri() + "?mode=ro", uri=True) as restored_db:
            self.assertEqual(restored_db.execute(
                "SELECT price FROM historical_rows WHERE id=2").fetchone(), (22,))
        self.assertGreater(writer.execute(
            "SELECT price FROM historical_rows WHERE id=2").fetchone()[0], 22)

    def test_history_uses_same_wal_restore_and_independent_paper_witness(self):
        path = self.db.parent.parent / "market_history.db"
        with sqlite3.connect(path) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.executescript(
                "CREATE TABLE history_versions_v2(id INTEGER PRIMARY KEY);"
                "CREATE TABLE history_canonical_v2(id INTEGER PRIMARY KEY);"
                "CREATE TABLE market_historical_ohlcv(id INTEGER PRIMARY KEY);"
                "INSERT INTO history_canonical_v2 VALUES(1);"
            )
            writer.commit()
            wal = Path(str(path) + "-wal")
            self.assertGreater(wal.stat().st_size, 0)
            before = (path.read_bytes(), wal.read_bytes())
            with patch.object(custody, "_rootless_runtime_binding",
                              return_value="BOTH_ROOTLESS_CIDS_DATA_DB_INODE_MATCHED"):
                self.writer.execute("UPDATE observer_state SET real_orders_sent=1 WHERE id=1")
                self.writer.commit()
                with self.assertRaisesRegex(custody.Hold, "PAPER_ORDERS_UNVERIFIED"):
                    custody.host_custody_once(path, self.marker, self.custody_root,
                                              run_id=self.RUN_ID, paper_db_path=self.db)
                self.assertFalse(self.custody_root.exists())
                self.writer.execute("UPDATE observer_state SET real_orders_sent=0 WHERE id=1")
                self.writer.commit()
                receipt = custody.host_custody_once(path, self.marker, self.custody_root,
                                                    run_id=self.RUN_ID, paper_db_path=self.db)
            self.assertEqual(receipt["state"], "PRIVATE_SNAPSHOT_RESTORE_OK")
            self.assertEqual(receipt["database_role"], "HISTORY")
            self.assertEqual(receipt["snapshot_sha256"], receipt["restore_sha256"])
            self.assertEqual(receipt["source_wal_state"], "PRESENT_RECONCILED_BY_SQLITE_BACKUP")
            restored = self.custody_root / ("history_" + self.RUN_ID) / "restore-check.sqlite"
            with sqlite3.connect(restored.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
                self.assertEqual(db.execute("SELECT id FROM history_canonical_v2").fetchall(), [(1,)])
                self.assertEqual(db.execute("PRAGMA quick_check").fetchone(), ("ok",))
            self.assertEqual((path.read_bytes(), wal.read_bytes()), before)
            self.assertEqual(custody.sanitize_receipt(
                "CUSTODY_ONCE", receipt, remote_rc=0, expected_run_id=self.RUN_ID,
                database_role="HISTORY"), receipt)
            self.assertNotEqual(custody.sanitize_receipt(
                "CUSTODY_ONCE", receipt, remote_rc=0, expected_run_id=self.RUN_ID)["state"],
                "PRIVATE_SNAPSHOT_RESTORE_OK")

    def test_exit_revoked_after_backup_still_blocks_final_custody(self):
        original_sync = custody._fsync_private_file
        observed = []

        def revoke_after_snapshot(directory_fd, basename):
            original_sync(directory_fd, basename)
            if basename == "snapshot.sqlite":
                observed.append(basename)
                self.writer.execute(
                    "UPDATE paper_exit_reader_state SET state='DEGRADED' WHERE id=1")
                self.writer.commit()

        with patch.object(custody, "_fsync_private_file",
                          side_effect=revoke_after_snapshot):
            with self.assertRaises(custody.Hold) as raised:
                self._custody()
        self.assertEqual(observed, ["snapshot.sqlite"])
        self.assertEqual(str(raised.exception), "EXIT_QUIESCENCE_UNVERIFIED")
        self.assertFalse((self.custody_root / ("observer_" + self.RUN_ID)).exists())
        self.assertTrue((self.custody_root / (".partial_" + self.RUN_ID)).exists())

    def test_paper_zero_revoked_after_backup_still_blocks_final_custody(self):
        original_sync = custody._fsync_private_file
        observed = []

        def revoke_after_snapshot(directory_fd, basename):
            original_sync(directory_fd, basename)
            if basename == "snapshot.sqlite":
                observed.append(basename)
                self.writer.execute(
                    "UPDATE observer_state SET real_orders_sent=1 WHERE id=1")
                self.writer.commit()

        with patch.object(custody, "_fsync_private_file",
                          side_effect=revoke_after_snapshot):
            with self.assertRaises(custody.Hold) as raised:
                self._custody()
        self.assertEqual(observed, ["snapshot.sqlite"])
        self.assertEqual(str(raised.exception), "PAPER_ORDERS_UNVERIFIED")
        self.assertFalse((self.custody_root / ("observer_" + self.RUN_ID)).exists())
        self.assertTrue((self.custody_root / (".partial_" + self.RUN_ID)).exists())

    def test_private_snapshot_restore_manifest_and_parent_are_fsynced(self):
        actual_fsync = os.fsync
        touched = []
        def sync(fd):
            touched.append(os.fstat(fd).st_mode)
            return actual_fsync(fd)
        with patch.object(custody.os, "fsync", side_effect=sync):
            receipt = self._custody()
        self.assertEqual(receipt["state"], "PRIVATE_SNAPSHOT_RESTORE_OK")
        self.assertGreaterEqual(sum(stat.S_ISREG(mode) for mode in touched), 3)
        self.assertGreaterEqual(sum(stat.S_ISDIR(mode) for mode in touched), 2)

    def test_fsync_failure_never_publishes_complete_custody(self):
        actual_fsync = os.fsync
        calls = 0
        def fail_after_snapshot(fd):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("injected durability failure")
            return actual_fsync(fd)
        with patch.object(custody.os, "fsync", side_effect=fail_after_snapshot):
            with self.assertRaises(custody.Hold) as raised:
                self._custody()
        self.assertEqual(str(raised.exception), "CUSTODY_DURABILITY_UNVERIFIED")
        self.assertFalse((self.custody_root / ("observer_" + self.RUN_ID)).exists())

    def test_anchored_readonly_source_observes_committed_wal_without_source_write(self):
        sources = (self.db, Path(str(self.db) + "-wal"))
        before = [(p.stat().st_mtime_ns, p.read_bytes()) for p in sources]
        with custody._anchored_source_sqlite(self.db) as source:
            self.assertEqual(source.execute(
                "SELECT COUNT(*) FROM historical_rows").fetchone()[0], 2)
        # SHM is allowed to move during WAL reader locking; DB and WAL bytes
        # must remain unmodified by this source-only connection.
        self.assertEqual(before,
                         [(p.stat().st_mtime_ns, p.read_bytes()) for p in sources])

    def test_source_replace_during_sqlite_open_does_not_pass_privileged_inode_gate(self):
        original_connect = sqlite3.connect
        original = self.root / "orphaned-source.db"
        def replacement(dbname, *args, **kwargs):
            if isinstance(dbname, str) and dbname.startswith("file:/proc/self/fd/"):
                self.db.rename(original)
                self.db.symlink_to(original)
            return original_connect(dbname, *args, **kwargs)
        with patch.object(custody.sqlite3, "connect", side_effect=replacement):
            with self.assertRaises(custody.Hold):
                with custody._anchored_source_sqlite(self.db):
                    pass

    def test_paper_counter_one_prevents_any_custody_file(self):
        self.writer.execute("UPDATE observer_state SET real_orders_sent=1 WHERE id=1")
        self.writer.commit()
        with self.assertRaises(custody.Hold) as raised:
            self._custody()
        self.assertEqual(str(raised.exception), "PAPER_ORDERS_UNVERIFIED")
        self.assertFalse(self.custody_root.exists())

    def test_same_run_id_never_overwrites_existing_snapshot(self):
        prior = self._custody()
        with self.assertRaises(custody.Hold) as raised:
            self._custody()
        self.assertEqual(str(raised.exception), "CUSTODY_RUN_ALREADY_EXISTS")
        digest = hashlib.sha256((self.custody_root / ("observer_" + self.RUN_ID)
                                 / "snapshot.sqlite").read_bytes()).hexdigest()
        self.assertEqual(digest, prior["snapshot_sha256"])

    def test_invalid_run_id_rejected_before_creating_any_file(self):
        with self.assertRaises(custody.Hold) as raised:
            custody.host_custody_once(self.db, self.marker, self.custody_root,
                                     run_id="../PRIVATE")
        self.assertEqual(str(raised.exception), "CUSTODY_RUN_ID_INVALID")
        self.assertFalse(self.custody_root.exists())


class RuntimeBindingContracts(LiveWalFixture):
    def _attestation(self):
        cid_observer, cid_dashboard = "a" * 64, "b" * 64
        marker = json.loads(self.marker.read_text())
        current = {"schema": "porota.rootless.deploy.v1", "state": "VALIDADO_RUNTIME",
                   "source_sha": marker["candidate_sha"],
                   "source_tree": marker["candidate_tree_sha"],
                   "image_id": marker["image_id"],
                   "operating_mode": "PRODUCTION_PAPER", "execution_mode": "SIMULATION",
                   "real_orders_sent": 0, "attempt": "p0-rc6-validated",
                   "daemon_id": "rootless-verified-daemon",
                   "roles": {"observer": cid_observer, "dashboard": cid_dashboard}}
        daemon = {"ID": "rootless-verified-daemon",
                  "SecurityOptions": ["name=rootless"], "CgroupVersion": "2",
                  "CgroupDriver": "systemd",
                  "DockerRootDir": "/home/porotaadmin/.local/share/porota-rc6/docker"}
        containers = {}
        for role, cid in (("observer", cid_observer), ("dashboard", cid_dashboard)):
            cmd = "bv_paper_runtime.py" if role == "observer" else "o_dashboard.py"
            binds = [{"Type": "bind", "Destination": "/app/data",
                      "Source": str(self.root / "data"), "RW": True},
                     {"Type": "bind", "Destination": "/app/operator",
                      "Source": "/home/porotaadmin/.config/porota-rc6/operator",
                      "RW": False}]
            if role == "observer":
                binds.append({"Type": "bind", "Destination": "/run/secrets/ppi_production.json",
                              "Source": "/opt/porota-trading/.secrets/ppi_production.json",
                              "RW": False})
            containers[role] = {
                "Id": cid, "Name": f"/porota-rc6-{role}-p0-rc6-validated",
                "Image": marker["image_id"], "State": {"Status": "running", "OOMKilled": False},
                "Config": {"Image": marker["image_id"], "User": "0:0", "Entrypoint": None,
                           "WorkingDir": "/app", "Cmd": ["python", cmd],
                           "Labels": {"io.porota.project": "rc6", "io.porota.role": role,
                                      "io.porota.attempt": "p0-rc6-validated"},
                           "Env": ["POROTA_BUILD_SHA=" + marker["candidate_sha"],
                                   "POROTA_CANDIDATE_TREE_SHA=" + marker["candidate_tree_sha"],
                                   "PAPER_V17_DB_PATH=/app/data/paper_v17/observer_v17.db",
                                   "DASHBOARD_OPERATION_MODE=PRODUCTION_PAPER",
                                   "EXECUTION_MODE=SIMULATION", "REAL_ORDER_CAPABILITY=BLOCKED"]},
                "HostConfig": {"ReadonlyRootfs": True, "Privileged": False,
                               "CapDrop": ["ALL"], "CapAdd": [], "SecurityOpt": ["no-new-privileges"],
                               "NetworkMode": "bridge", "PidMode": "",
                               "Memory": 512 * 1024**2, "NanoCpus": 1_000_000_000,
                               "PidsLimit": 256}, "Mounts": binds}
        return current, daemon, containers, marker

    def test_both_rootless_cids_image_volume_and_inode_match(self):
        current, daemon, containers, marker = self._attestation()
        self.assertEqual(custody._verify_rootless_attestation(
            current, daemon, containers, marker, self.db,
            data_root=self.root / "data", operator_home=Path("/home/porotaadmin")),
            "BOTH_ROOTLESS_CIDS_DATA_DB_INODE_MATCHED")

        history = self.root / "data/market_history.db"
        with sqlite3.connect(history) as db:
            db.execute("CREATE TABLE history_canonical_v2(id INTEGER)")
        self.assertEqual(custody._verify_rootless_attestation(
            current, daemon, containers, marker, history, data_root=self.root / "data"),
            "BOTH_ROOTLESS_CIDS_DATA_DB_INODE_MATCHED")
        containers["observer"]["Config"]["Env"].append("HIST_DB_PATH=/app/data/another.db")
        with self.assertRaisesRegex(custody.Hold, "RUNTIME_DB_BINDING_NO_VERIFICADO"):
            custody._verify_rootless_attestation(
                current, daemon, containers, marker, history, data_root=self.root / "data")

    def test_spoofed_current_or_container_cid_mount_or_image_refuses_binding(self):
        for field in ("source_sha", "source_tree", "image_id", "daemon_id"):
            with self.subTest(field=field):
                current, daemon, containers, marker = self._attestation()
                current[field] = "WRONG"
                with self.assertRaises(custody.Hold) as raised:
                    custody._verify_rootless_attestation(current, daemon, containers,
                        marker, self.db, data_root=self.root / "data",
                        operator_home=Path("/home/porotaadmin"))
                self.assertEqual(str(raised.exception), "RUNTIME_DB_BINDING_NO_VERIFICADO")
        for field, value in (("Id", "f" * 64), ("Image", "sha256:" + "f" * 64)):
            with self.subTest(field=field):
                current, daemon, containers, marker = self._attestation()
                containers["observer"][field] = value
                with self.assertRaises(custody.Hold):
                    custody._verify_rootless_attestation(current, daemon, containers,
                        marker, self.db, data_root=self.root / "data",
                        operator_home=Path("/home/porotaadmin"))
        for label, edited in (("wrong-data-mount", {"Source": "/tmp/spoofed"}),
                              ("readonly-data-mount", {"RW": False})):
            with self.subTest(label=label):
                current, daemon, containers, marker = self._attestation()
                containers["observer"]["Mounts"][0].update(edited)
                with self.assertRaises(custody.Hold):
                    custody._verify_rootless_attestation(current, daemon, containers,
                        marker, self.db, data_root=self.root / "data",
                        operator_home=Path("/home/porotaadmin"))

    def test_db_file_inode_replacement_fails_before_runtime_attestation(self):
        current, daemon, containers, marker = self._attestation()
        target = self.root / "orphan.sqlite"
        self.db.rename(target)
        self.db.symlink_to(target)
        with self.assertRaises(custody.Hold) as raised:
            custody._verify_rootless_attestation(current, daemon, containers, marker,
                self.db, data_root=self.root / "data",
                operator_home=Path("/home/porotaadmin"))
        self.assertEqual(str(raised.exception), "RUNTIME_DB_BINDING_NO_VERIFICADO")

    def test_root_or_foreign_uid_cannot_run_host_operator_writer(self):
        operator = SimpleNamespace(pw_name="porotaadmin", pw_uid=1000, pw_gid=1000,
                                   pw_dir="/home/porotaadmin")
        with patch.object(custody.pwd, "getpwnam", return_value=operator), \
             patch.object(custody.os, "getuid", return_value=0), \
             patch.object(custody.os, "geteuid", return_value=0), \
             patch.object(custody.os, "getgid", return_value=0), \
             patch.object(custody.os, "getegid", return_value=0):
            with self.assertRaises(custody.Hold) as raised:
                custody._operator_identity_guard()
        self.assertEqual(str(raised.exception), "ROOTLESS_SSH_IDENTITY_REQUIRED")

    def test_operator_uid_requires_exact_username_home_and_group(self):
        operator = SimpleNamespace(pw_name="porotaadmin", pw_uid=1000, pw_gid=1000,
                                   pw_dir="/home/porotaadmin")
        with patch.object(custody.pwd, "getpwnam", return_value=operator), \
             patch.object(custody.os, "getuid", return_value=1000), \
             patch.object(custody.os, "geteuid", return_value=1000), \
             patch.object(custody.os, "getgid", return_value=1000), \
             patch.object(custody.os, "getegid", return_value=1000):
            self.assertEqual(custody._operator_identity_guard().pw_dir,
                             "/home/porotaadmin")
        operator.pw_dir = "/tmp/spoofed-home"
        with patch.object(custody.pwd, "getpwnam", return_value=operator), \
             patch.object(custody.os, "getuid", return_value=1000), \
             patch.object(custody.os, "geteuid", return_value=1000), \
             patch.object(custody.os, "getgid", return_value=1000), \
             patch.object(custody.os, "getegid", return_value=1000):
            with self.assertRaises(custody.Hold):
                custody._operator_identity_guard()

    def test_legacy_group_write_is_allowed_only_for_the_private_operator_group(self):
        operator = SimpleNamespace(pw_name="porotaadmin", pw_uid=1000, pw_gid=1000)
        group = SimpleNamespace(gr_name=operator.pw_name, gr_mem=[operator.pw_name])
        info = SimpleNamespace(st_uid=1000, st_gid=1000)
        with patch.object(custody.grp, "getgrgid", return_value=group), \
             patch.object(custody.pwd, "getpwall", return_value=[operator]), \
             patch.object(custody.os, "getxattr", side_effect=OSError(errno.ENODATA, "no ACL")) as acl:
            custody._verify_private_state_group(7, info, operator)
        acl.assert_called_once_with(7, "system.posix_acl_access")

    def test_rootless_state_opens_acl_on_pinned_readable_descriptor(self):
        home = self.root / "home"
        state = home / ".local/state/porota-rc6"
        state.mkdir(parents=True)
        home.chmod(0o755)
        state.parent.chmod(0o770)
        state.chmod(0o700)
        current = state / "current.json"
        current.write_text("{}")
        current.chmod(0o600)
        operator = SimpleNamespace(pw_name="porotaadmin", pw_uid=1000, pw_gid=1000,
                                   pw_dir=str(home))
        group = SimpleNamespace(gr_name=operator.pw_name, gr_mem=[])
        fstat = os.fstat

        def owned_fstat(fd):
            info = fstat(fd)
            return SimpleNamespace(st_uid=1000, st_gid=1000, st_mode=info.st_mode,
                                   st_size=info.st_size, st_nlink=info.st_nlink)

        with patch.object(custody, "_operator_identity_guard", return_value=operator), \
             patch.object(custody.os, "fstat", side_effect=owned_fstat), \
             patch.object(custody.grp, "getgrgid", return_value=group), \
             patch.object(custody.pwd, "getpwall", return_value=[operator]):
            self.assertEqual(custody._rootless_current_state(), (operator, {}))
            state.parent.chmod(0o772)
            with self.assertRaisesRegex(custody.Hold, "^ROOTLESS_STATE_PARENT_UNSAFE$"):
                custody._rootless_current_state()

    def test_group_membership_primary_group_and_extended_acl_are_each_fail_closed(self):
        operator = SimpleNamespace(pw_name="porotaadmin", pw_uid=1000, pw_gid=1000)
        foreign = SimpleNamespace(pw_name="foreign", pw_uid=1001, pw_gid=1000)
        for members, users, acl_value, gid in (
            (["foreign"], [operator], None, 1000),
            ([], [operator, foreign], None, 1000),
            ([], [operator], b"extended ACL", 1000),
            ([], [operator], None, 1001),
        ):
            with self.subTest(members=members, users=users, acl_value=acl_value, gid=gid):
                info = SimpleNamespace(st_uid=1000, st_gid=gid)
                group = SimpleNamespace(gr_name=operator.pw_name, gr_mem=members)
                with patch.object(custody.grp, "getgrgid", return_value=group), \
                     patch.object(custody.pwd, "getpwall", return_value=users), \
                     patch.object(custody.os, "getxattr", return_value=acl_value), \
                     self.assertRaisesRegex(custody.Hold, "^ROOTLESS_STATE_PRIVATE_GROUP_UNVERIFIED$"):
                    custody._verify_private_state_group(7, info, operator)


class RunnerReceiptAndOwnerContracts(unittest.TestCase):
    def test_preflight_sanitizer_never_emits_raw_secret_or_path(self):
        raw = {"schema": "rc6.observer-online-custody-once.v1",
               "state": "PREFLIGHT_METADATA_PASS", "read_only": True,
               "host_mutations": 0, "paper_zero": "NO_VERIFICADO",
               "source_bytes": 2804916224, "wal_bytes": 61367432, "shm_bytes": 131072,
               "source_wal_state": "PRESENT_UNRECONCILED",
               "runtime_release_marker": "PINNED_P0_MARKER_ONLY",
               "free_bytes": 34900000000, "required_bytes": 10700000000,
               "ppi_requests": 0, "real_orders_sent_by_action": 0,
               "ppi_watch_touched": False,
               "PRIVATE_USER_PATH": "/opt/secret-client/db"}
        receipt = custody.sanitize_receipt("PREFLIGHT_ONLY", raw, remote_rc=0)
        self.assertEqual(receipt["state"], "NO_VERIFICADO_RECEIPT_UNSAFE")
        self.assertEqual(receipt["read_only"], True)
        self.assertNotIn("secret", json.dumps(receipt).lower())

    def test_sanitizer_verifies_private_snapshot_digest_and_paper_zero(self):
        digest = "ab" * 32
        raw = {"schema": "rc6.observer-online-custody-once.v1",
               "state": "PRIVATE_SNAPSHOT_RESTORE_OK", "read_only": False,
               "source_sql_writes": 0, "shm_locking_possible": True,
               "runtime_db_binding": "BOTH_ROOTLESS_CIDS_DATA_DB_INODE_MATCHED",
               "exit_quiescence": "EXIT_WAITING_MARKET_CLOSED_FOUR_LEDGERS_FLAT_SAMPLED",
               "paper_zero": "OBSERVED",
               "run_id": "44508298821", "source_bytes": 2804916224,
               "wal_bytes_before": 61367432,
               "source_wal_state": "PRESENT_RECONCILED_BY_SQLITE_BACKUP",
               "source_identity_continuity": "DB_IDENTITY_STABLE_WAL_UNCHANGED_SHM_UNCHANGED",
               "snapshot_bytes": 2804916224, "snapshot_sha256": digest,
               "restore_bytes": 2804916224, "restore_sha256": digest,
               "restore_quick_check": "ok", "generated_at_utc": "2026-11-19T10:43:47+00:00",
               "host_mutations": 1, "ppi_requests": 0,
               "real_orders_sent_by_action": 0, "ppi_watch_touched": False}
        receipt = custody.sanitize_receipt("CUSTODY_ONCE", raw, remote_rc=0,
                                          expected_run_id="44508298821")
        self.assertEqual(receipt["state"], "PRIVATE_SNAPSHOT_RESTORE_OK")
        self.assertEqual(receipt["snapshot_sha256"], digest)
        self.assertEqual(receipt["paper_zero"], "OBSERVED")
        stale_clock_only = dict(raw, offhours_gate="OFFHOURS_ART_SAFE")
        self.assertEqual(custody.sanitize_receipt("CUSTODY_ONCE", stale_clock_only,
                         remote_rc=0, expected_run_id="44508298821")["state"],
                         "NO_VERIFICADO_RECEIPT_UNSAFE")
        raw["restore_sha256"] = "ef" * 32
        rejected = custody.sanitize_receipt("CUSTODY_ONCE", raw, remote_rc=0,
                                            expected_run_id="44508298821")
        self.assertEqual(rejected["state"], "NO_VERIFICADO_RECEIPT_UNSAFE")

    def test_valid_but_foreign_custody_run_is_rejected_even_with_matching_digest(self):
        digest = "cb" * 32
        raw = {"schema": custody.SCHEMA, "state": "PRIVATE_SNAPSHOT_RESTORE_OK",
               "read_only": False, "source_sql_writes": 0, "shm_locking_possible": True,
               "paper_zero": "OBSERVED", "run_id": "44508298821",
               "runtime_db_binding": "BOTH_ROOTLESS_CIDS_DATA_DB_INODE_MATCHED",
               "exit_quiescence": "EXIT_WAITING_MARKET_CLOSED_FOUR_LEDGERS_FLAT_SAMPLED",
               "source_bytes": 1000, "wal_bytes_before": 1,
               "source_wal_state": "PRESENT_RECONCILED_BY_SQLITE_BACKUP",
               "source_identity_continuity": "DB_IDENTITY_STABLE_WAL_UNCHANGED_SHM_CHANGED",
               "snapshot_bytes": 1000, "restore_bytes": 1000,
               "snapshot_sha256": digest, "restore_sha256": digest,
               "restore_quick_check": "ok", "generated_at_utc": "2026-11-19T20:00:00+00:00",
               "host_mutations": 1, "ppi_requests": 0,
               "real_orders_sent_by_action": 0, "ppi_watch_touched": False}
        self.assertEqual(custody.sanitize_receipt("CUSTODY_ONCE", raw, remote_rc=0,
                         expected_run_id="44508298822")["state"],
                         "NO_VERIFICADO_RECEIPT_UNSAFE")
        self.assertEqual(custody.sanitize_receipt("CUSTODY_ONCE", raw, remote_rc=0,
                         expected_run_id="44508298821")["state"],
                         "PRIVATE_SNAPSHOT_RESTORE_OK")

    def test_runner_cli_requires_own_github_run_id_not_attacker_receipt_id(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            raw = directory / "raw.json"
            output = directory / "sanitized.json"
            raw.write_text(json.dumps({"schema": custody.SCHEMA,
                                       "state": "PRIVATE_SNAPSHOT_RESTORE_OK",
                                       "run_id": "44508298821"}))
            with patch.dict(os.environ, {"GITHUB_RUN_ID": "44508298822"}):
                done = subprocess.run([sys.executable, str(SOURCE), "sanitize", "CUSTODY_ONCE",
                                       str(raw), str(output), "0"],
                                      text=True, capture_output=True, timeout=5,
                                      env=os.environ)
            self.assertEqual(done.returncode, 0)
            self.assertEqual(json.loads(output.read_text())["state"],
                             "NO_VERIFICADO_RECEIPT_UNSAFE")

    def test_unreachable_host_receipt_does_not_claim_backup(self):
        receipt = custody.sanitize_receipt("CUSTODY_ONCE", {}, remote_rc=255)
        self.assertEqual(receipt["state"], "NO_VERIFICADO_HOST_COMMAND")
        self.assertIsNone(receipt.get("snapshot_sha256"))

    def test_failed_host_receipt_discloses_only_a_whitelisted_reason(self):
        reason = "ROOTLESS_STATE_PRIVATE_GROUP_UNVERIFIED"
        raw = {"schema": custody.SCHEMA, "failure_reason": reason}
        receipt = custody.sanitize_receipt("CUSTODY_ONCE", raw, remote_rc=2,
                                          database_role="HISTORY")
        self.assertEqual(receipt["state"], "NO_VERIFICADO_HOST_COMMAND")
        self.assertEqual(receipt["failure_reason"], reason)
        self.assertIsNone(receipt.get("snapshot_sha256"))
        for unsafe in ({**raw, "secret_path": "/private/path"},
                       {**raw, "failure_reason": "/private/path"}):
            receipt = custody.sanitize_receipt("CUSTODY_ONCE", unsafe, remote_rc=2)
            self.assertNotIn("failure_reason", receipt)

    def test_deploy_owner_receipt_must_be_current_and_exclusive(self):
        body = ("DEPLOY_OWNER — RC6 observer one-shot custody\n"
                "Workstream ID: RC6_OBSERVER_CUSTODY_DEPLOY_20261119\n"
                "Ownership role: DEPLOY_OWNER\n"
                "Ownership status: ACTIVE\n"
                "Host scope: RC6_OBSERVER_ONLINE_CUSTODY_ONCE\n")
        original = {"id": 1001,
                    "issue_url": "https://api.github.com/repos/mbalbo2023/Porota-trading/issues/980",
                    "html_url": "https://github.com/mbalbo2023/Porota-trading/issues/980#issuecomment-1001",
                    "user": {"login": "mbalbo2023"}, "body": body}
        self.assertEqual(custody.validate_deploy_owner(original, [original]),
                         "RC6_OBSERVER_CUSTODY_DEPLOY_20261119")
        later = dict(original, id=1002,
                     html_url="https://github.com/mbalbo2023/Porota-trading/issues/980#issuecomment-1002",
                     body=body.replace("ACTIVE", "RELEASED"))
        with self.assertRaises(custody.Hold) as raised:
            custody.validate_deploy_owner(original, [original, later])
        self.assertEqual(str(raised.exception), "DEPLOY_OWNER_NOT_CURRENT")
        other = dict(original, id=1003,
                     html_url="https://github.com/mbalbo2023/Porota-trading/issues/980#issuecomment-1003",
                     body=body.replace("RC6_OBSERVER_CUSTODY_DEPLOY_20261119",
                                       "RC6_OTHER_OWNER_20261119"))
        with self.assertRaises(custody.Hold) as raised:
            custody.validate_deploy_owner(original, [original, other])
        self.assertEqual(str(raised.exception), "DEPLOY_OWNER_OVERLAP")

    def test_released_prior_deploy_owner_is_not_a_live_overlap(self):
        current_body = ("DEPLOY_OWNER one-shot\n"
                        "Workstream ID: RC6_OBSERVER_CUSTODY_DEPLOY_20261119\n"
                        "Ownership role: DEPLOY_OWNER\nOwnership status: ACTIVE\n"
                        "Host scope: RC6_OBSERVER_ONLINE_CUSTODY_ONCE\n")
        old_body = ("DEPLOY_OWNER prior\nWorkstream ID: RC6_P0_DEPLOY\n"
                    "Ownership role: DEPLOY_OWNER\nOwnership status: ACTIVE\n")
        def row(number, body):
            return {"id": number,
                    "issue_url": "https://api.github.com/repos/mbalbo2023/Porota-trading/issues/980",
                    "html_url": "https://github.com/mbalbo2023/Porota-trading/issues/980#issuecomment-" + str(number),
                    "user": {"login": "mbalbo2023"}, "body": body}
        prior = row(30, old_body)
        released = row(40, old_body.replace("ACTIVE", "RELEASED"))
        current = row(50, current_body)
        self.assertEqual(custody.validate_deploy_owner(current,
                         [prior, released, current]),
                         "RC6_OBSERVER_CUSTODY_DEPLOY_20261119")

    def test_runner_cli_sanitize_is_bounded_and_never_echoes_raw_values(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            raw = directory / "private-raw.json"
            public = directory / "public.json"
            raw.write_text(json.dumps({"SECRET_ACCOUNT": "PRIVATE_ACCOUNT_VALUE"}))
            proc = subprocess.run([sys.executable, str(SOURCE), "sanitize",
                                   "PREFLIGHT_ONLY", str(raw), str(public), "0"],
                                  text=True, capture_output=True, timeout=5)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            receipt = json.loads(public.read_text())
            self.assertEqual(receipt["state"], "NO_VERIFICADO_RECEIPT_UNSAFE")
            self.assertNotIn("PRIVATE_ACCOUNT_VALUE", proc.stdout + proc.stderr
                             + public.read_text())


class AttributionContracts(unittest.TestCase):
    def test_replaced_source_file_is_not_attested_and_wal_growth_is_reported(self):
        before = {"db": (10, 11, 1000, 15), "wal": (10, 17, 100, 15),
                  "shm": (10, 19, 32768, 15)}
        after = {"db": (10, 12, 1000, 15), "wal": (10, 17, 100, 15),
                 "shm": (10, 19, 32768, 15)}
        with self.assertRaises(custody.Hold) as raised:
            custody.source_continuity(before, after)
        self.assertEqual(str(raised.exception), "SOURCE_IDENTITY_CHANGED")
        after["db"] = before["db"]
        after["wal"] = (10, 17, 250, 16)
        self.assertEqual(custody.source_continuity(before, after),
                         "DB_IDENTITY_STABLE_WAL_CHANGED_SHM_UNCHANGED")
        after["wal"] = before["wal"]
        self.assertEqual(custody.source_continuity(before, after),
                         "DB_IDENTITY_STABLE_WAL_UNCHANGED_SHM_UNCHANGED")
        after["shm"] = (10, 19, 32768, 20)
        self.assertEqual(custody.source_continuity(before, after),
                         "DB_IDENTITY_STABLE_WAL_UNCHANGED_SHM_CHANGED")

    def test_sidecar_inode_replacement_is_not_merely_labeled_wal_changed(self):
        before = {"db": (10, 11, 1000, 15), "wal": (10, 17, 100, 15),
                  "shm": (10, 19, 32768, 15)}
        for label in ("wal", "shm"):
            with self.subTest(label=label):
                after = dict(before)
                meta = list(before[label])
                meta[1] += 1
                after[label] = tuple(meta)
                with self.assertRaises(custody.Hold) as raised:
                    custody.source_continuity(before, after)
                self.assertEqual(str(raised.exception), "SOURCE_IDENTITY_CHANGED")


class RestoredHistoryInventoryContracts(unittest.TestCase):
    OBSERVER_RUN = "44918906718"
    HISTORY_RUN = "44917242971"
    MASK = "0" * (custody.INVENTORY_LENGTH - 4) + "2111"

    def restore(self, root, role, run_id, sql):
        slot = root / f"{role.lower()}_{run_id}"
        slot.mkdir(mode=0o700)
        restored = slot / "restore-check.sqlite"
        with sqlite3.connect(restored) as db:
            db.executescript(sql)
        restored.chmod(0o600)
        size = restored.stat().st_size
        sha = hashlib.sha256(restored.read_bytes()).hexdigest()
        receipt = {"schema": custody.SCHEMA, "state": "PRIVATE_SNAPSHOT_RESTORE_OK",
                   "read_only": False, "source_sql_writes": 0, "shm_locking_possible": True,
                   "runtime_db_binding": "BOTH_ROOTLESS_CIDS_DATA_DB_INODE_MATCHED",
                   "exit_quiescence": "EXIT_WAITING_MARKET_CLOSED_FOUR_LEDGERS_FLAT_SAMPLED",
                   "paper_zero": "OBSERVED", "run_id": run_id, "source_bytes": size,
                   "wal_bytes_before": 0, "source_wal_state": "SOURCE_WAL_ABSENT",
                   "source_identity_continuity": "DB_IDENTITY_STABLE_WAL_UNCHANGED_SHM_UNCHANGED",
                   "snapshot_bytes": size, "snapshot_sha256": sha, "restore_bytes": size,
                   "restore_sha256": sha, "restore_quick_check": "ok",
                   "generated_at_utc": "2026-11-21T15:05:51+00:00", "host_mutations": 1,
                   "ppi_requests": 0, "real_orders_sent_by_action": 0, "ppi_watch_touched": False}
        if role == "HISTORY":
            receipt["database_role"] = role
        manifest = slot / "manifest.json"
        manifest.write_text(json.dumps(receipt))
        manifest.chmod(0o600)
        return restored, sha

    def test_exports_only_the_bound_history_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history, digest = self.restore(root, "HISTORY", self.HISTORY_RUN,
                                           "CREATE TABLE history_versions_v2(id);")
            original = history.read_bytes()
            output = io.BytesIO()
            custody.stream_restored_history(self.HISTORY_RUN, digest, output, custody_root=root)
            self.assertEqual(output.getvalue(), original)
            self.assertEqual(history.read_bytes(), original)
            with self.assertRaisesRegex(custody.Hold, "RESTORED_INVENTORY_DIGEST_MISMATCH"):
                custody.stream_restored_history(self.HISTORY_RUN, "f" * 64, output, custody_root=root)
            self.assertEqual(output.getvalue(), original)

    def test_runner_wires_expected_restore_digest_into_existing_migrator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history, digest = self.restore(root, "HISTORY", self.HISTORY_RUN,
                                           "CREATE TABLE history_versions_v2(id);")
            proof = {"schema": custody.MIGRATION_SCHEMA, "source_sha256": digest,
                     "source_bytes": history.stat().st_size,
                     "migrated_copy_sha256": "b" * 64, "legacy_versions": 4,
                     "mapped_versions": 1, "quarantined_versions": 3,
                     "original_source_mutated": False, "temporary_copy_removed": True,
                     "host_promotion_allowed": False, "physical_purge_allowed": False,
                     "private_path": "/private/not-public"}
            result = subprocess.CompletedProcess([], 0, json.dumps(proof), "")
            with patch.dict(os.environ, GITHUB_ACTIONS="true", RUNNER_TEMP=directory), \
                    patch.object(custody.subprocess, "run", return_value=result) as run:
                public = custody.rehearse_restored_history(history, root, self.HISTORY_RUN, digest)
            self.assertEqual(public["state"], "RESTORED_HISTORY_MIGRATION_REHEARSED")
            self.assertEqual(public["max_source_bytes"], 2 * 1024**3)
            self.assertNotIn("private_path", public)
            command = run.call_args.args[0]
            self.assertIn("scripts.rc6_history_offhost_migration_rehearsal", command)
            capture = Path(command[command.index("--capture-report") + 1])
            self.assertEqual(json.loads(capture.read_text()),
                             {"source_sha256": digest, "source_bytes": history.stat().st_size})

    def test_exact_identity_join_reports_only_counts_and_refuses_changed_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observer, observer_sha = self.restore(root, "OBSERVER", self.OBSERVER_RUN, """
                CREATE TABLE observer_state(id,mode,real_orders_sent);
                INSERT INTO observer_state VALUES (1,'PRODUCTION_PAPER',0);
                CREATE TABLE financial_instrument_catalog(ticker,instrument_type,market,currency,settlement,status);
                INSERT INTO financial_instrument_catalog VALUES
                    ('TEST_A','ACCIONES','BYMA','ARS','A-24HS','AVAILABLE'),
                    ('TEST_C','CEDEARS','BYMA','ARS','T1','AVAILABLE'),
                    ('TEST_A','ACCIONES','BYMA','USD','A-24HS','AVAILABLE');
            """)
            history, history_sha = self.restore(root, "HISTORY", self.HISTORY_RUN, """
                CREATE TABLE history_versions_v2(id,symbol,instrument_type,market,currency,settlement,
                                                  date,price_basis,adjustment_basis,adjusted);
                INSERT INTO history_versions_v2 VALUES
                    (1,'TEST_A','ACCIONES','BYMA','ARS','A-24HS','2026-09-21','RAW','RAW_NO_ADJUSTMENT',0),
                    (2,'TEST_A','ACCIONES','BYMA','USD','A-24HS','2026-09-21','RAW','RAW_NO_ADJUSTMENT',0),
                    (3,'TEST_C','CEDEARS','BYMA','ARS','T1','2026-09-18','RAW','RAW_NO_ADJUSTMENT',0),
                    (4,'TEST_A','ACCIONES','BYMA','ARS','A-24HS','2026-09-21','RAW','RAW_NO_ADJUSTMENT',0),
                    (5,'TEST_A','ACCIONES','BYMA','ARS','A-24HS','2026-09-21T18:00:00Z','RAW','RAW_NO_ADJUSTMENT',0);
                CREATE TABLE history_canonical_v2 AS
                    SELECT id AS version_id,symbol,instrument_type,market,currency,settlement,
                           date,price_basis,adjustment_basis,adjusted FROM history_versions_v2 WHERE id<>4;
                INSERT INTO history_canonical_v2 VALUES
                    (99,'TEST_A','ACCIONES','BYMA','ARS','A-24HS','2026-09-19','RAW','RAW_NO_ADJUSTMENT',0);
                CREATE TABLE history_source_conflicts_v2(current_version_id,contender_version_id);
                INSERT INTO history_source_conflicts_v2 VALUES (1,4);
            """)
            args = (self.OBSERVER_RUN, self.HISTORY_RUN, self.MASK)
            raw = custody.host_restored_history_inventory(*args, custody_root=root)
            self.assertEqual(raw["observer_restore_sha256"], observer_sha)
            self.assertEqual(raw["history_restore_sha256"], history_sha)
            self.assertEqual(hashlib.sha256(observer.read_bytes()).hexdigest(), observer_sha)
            self.assertEqual(hashlib.sha256(history.read_bytes()).hexdigest(), history_sha)
            self.assertEqual(raw["cohorts"]["ACCIONES"], dict(zip(custody.INVENTORY_METRICS,
                (1, 1, 3, 1, 2, 0, 0, 3, 1, 2, 1, 0))))
            self.assertEqual(raw["cohorts"]["CEDEARS"], dict(zip(custody.INVENTORY_METRICS,
                (1, 1, 3, 0, 3, 1, 0, 1, 0, 0, 0, 0))))
            self.assertTrue(raw["conflict_ledger_present"])
            clean = custody.sanitize_restored_inventory(raw, remote_rc=0, observer_run=args[0],
                                                        history_run=args[1], mask=args[2])
            self.assertEqual(clean, raw)
            self.assertNotIn("TEST_", json.dumps(clean))
            contaminated = dict(raw, ticker="TEST_A")
            self.assertEqual(custody.sanitize_restored_inventory(contaminated, remote_rc=0,
                observer_run=args[0], history_run=args[1], mask=args[2])["state"], "NO_VERIFICADO")
            with history.open("r+b") as target:
                target.write(b"changed")
            with self.assertRaisesRegex(custody.Hold, "RESTORED_INVENTORY_DIGEST_MISMATCH"):
                custody.host_restored_history_inventory(*args, custody_root=root)

    def test_legacy_restore_with_incomplete_identity_reports_unresolved_exact_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.restore(root, "OBSERVER", self.OBSERVER_RUN, """
                CREATE TABLE observer_state(id,mode,real_orders_sent);
                INSERT INTO observer_state VALUES (1,'PRODUCTION_PAPER',0);
                CREATE TABLE financial_instrument_catalog(ticker,instrument_type,market,currency,settlement,status);
                INSERT INTO financial_instrument_catalog VALUES
                    ('TEST_A','ACCIONES','BYMA','ARS','A-24HS','AVAILABLE');
            """)
            self.restore(root, "HISTORY", self.HISTORY_RUN, """
                CREATE TABLE history_versions_v2(id,symbol,instrument_type,market,settlement,date);
                CREATE TABLE history_canonical_v2(symbol,instrument_type,market,settlement,date);
                INSERT INTO history_canonical_v2 VALUES ('TEST_A','ACCIONES','BYMA','A-24HS','2026-09-21');
            """)
            raw = custody.host_restored_history_inventory(self.OBSERVER_RUN, self.HISTORY_RUN,
                                                          self.MASK, custody_root=root)
            self.assertIn('currency', raw['missing_exact_columns']['history_canonical_v2'])
            counts = raw['cohorts']['ACCIONES']
            self.assertEqual(counts['legacy_candidates_on_certified_slots'], 1)
            self.assertIsNone(counts['canonical_on_certified_slots'])
            self.assertIsNone(counts['absent_on_certified_slots'])
            self.assertEqual(custody.sanitize_restored_inventory(raw, remote_rc=0,
                observer_run=self.OBSERVER_RUN, history_run=self.HISTORY_RUN, mask=self.MASK), raw)


class ActionsBoundaryContracts(unittest.TestCase):
    @staticmethod
    def _host_steps():
        workflow_path = SOURCE.parents[1] / ".github/workflows/rc6-observer-online-custody-once.yml"
        workflow = yaml.load(workflow_path.read_text(), Loader=yaml.BaseLoader)
        return workflow["jobs"]["host_custody"]["steps"]

    def test_sanitized_receipt_must_be_verified_before_any_cleanup(self):
        steps = self._host_steps()
        def find(fragment):
            return next(i for i, step in enumerate(steps)
                        if fragment in step.get("name", ""))
        sanitize = find("Sanitize strictly bounded public receipt")
        verify = find("Never classify failed host custody as success")
        upload = find("Upload JSON metadata only")
        cleanup = find("Destroy only ephemeral runner credentials")
        # Regress #44522352950: cleanup shredded the receipt before verifier.
        self.assertLess(sanitize, verify)
        self.assertLess(verify, upload)
        self.assertLess(upload, cleanup)
        self.assertEqual(cleanup, len(steps) - 1)
        for index in (sanitize, verify, upload, cleanup):
            self.assertEqual(steps[index].get("if"), "always()")
        self.assertIn('"$private/a-custody-public.json"', steps[cleanup]["run"])

    def test_failed_receipt_still_runs_runner_only_cleanup_after_check(self):
        steps = self._host_steps()
        check = next(s for s in steps if "Never classify failed host custody" in s.get("name", ""))
        cleanup = next(s for s in steps if "Destroy only ephemeral" in s.get("name", ""))
        with tempfile.TemporaryDirectory() as parent:
            private = Path(parent) / "rc6-custody-private-test"
            private.mkdir(mode=0o700)
            receipt = private / "a-custody-public.json"
            receipt.write_text(json.dumps({"schema": custody.SCHEMA,
                                           "state": "NO_VERIFICADO_RECEIPT_UNSAFE"}))
            for name in ("id_key", "known_hosts", ".raw.json"):
                (private / name).write_text("ONLY_SYNTHETIC_TEST_DATA")
            env = dict(os.environ, RUNNER_TEMP=parent, RC6_CUSTODY_PRIVATE=str(private),
                       RC6_CUSTODY_REMOTE_RC="0", REQUEST_MODE="PREFLIGHT_ONLY")
            rejected = subprocess.run(["bash", "-e"], input=check["run"],
                                      text=True, capture_output=True, env=env, timeout=5)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("RC6_CUSTODY_SANITIZED_HOLD", rejected.stderr)
            self.assertTrue(receipt.is_file())  # Check precedes cleanup.
            cleaned = subprocess.run(["bash", "-e"], input=cleanup["run"],
                                     text=True, capture_output=True, env=env, timeout=5)
            self.assertEqual(cleaned.returncode, 0, cleaned.stderr)
            self.assertFalse(private.exists())  # Cleanup still always follows failure.

    def test_pr_runs_only_tests_and_writer_requires_manual_main_owner(self):
        workflow_path = SOURCE.parents[1] / ".github/workflows/rc6-observer-online-custody-once.yml"
        workflow = yaml.load(workflow_path.read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow["concurrency"]["group"], "rc6-unified-paper-deploy")
        self.assertEqual(workflow["concurrency"]["cancel-in-progress"], "false")
        self.assertNotIn("push", workflow["on"])
        self.assertNotIn("schedule", workflow["on"])
        options = workflow["on"]["workflow_dispatch"]["inputs"]["mode"]["options"]
        self.assertEqual(options, ["PREFLIGHT_ONLY", "CUSTODY_ONCE", custody.INVENTORY_MODE,
                                   "RESTORED_HISTORY_MIGRATION_REHEARSAL", custody.HISTORY_UPGRADE_REHEARSAL,
                                   "HISTORY_REFERENCE_PREPARE", "HISTORY_REFERENCE_APPLY"])
        roles = workflow["on"]["workflow_dispatch"]["inputs"]["database_role"]
        self.assertEqual(roles["options"], ["OBSERVER", "HISTORY"])
        self.assertEqual(roles["default"], "OBSERVER")
        host = workflow["jobs"]["host_custody"]
        gate = host["if"]
        self.assertIn("github.event_name == 'workflow_dispatch'", gate)
        self.assertIn("github.ref == 'refs/heads/main'", gate)
        steps = host["steps"]
        owner = next(step for step in steps if step.get("id") == "owner_gate")
        self.assertIn("inputs.mode == 'CUSTODY_ONCE'", owner["if"])
        self.assertIn("inputs.mode == 'HISTORY_REFERENCE_PREPARE'", owner["if"])
        self.assertIn("inputs.mode == 'HISTORY_REFERENCE_APPLY'", owner["if"])
        self.assertIn("github.triggering_actor == 'mbalbo2023'", gate)
        self.assertIn("verify-owner", owner["run"])
        remote = next(step for step in steps if step.get("id") == "remote")
        self.assertIn("needs", host)
        self.assertIn("StrictHostKeyChecking=yes", remote["run"])
        self.assertIn("host-custody", remote["run"])
        self.assertIn("host-preflight", remote["run"])
        self.assertIn("timeout 790 python3 -B - host-preflight", remote["run"])
        self.assertIn("timeout 790 python3 -B - host-custody", remote["run"])
        self.assertIn("timeout 155 python3 -B - host-restored-inventory", remote["run"])
        self.assertIn("host-restored-history-export", remote["run"])
        self.assertNotIn("sudo", remote["run"])
        uploads = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact")]
        self.assertEqual(len(uploads), 1)
        self.assertTrue(uploads[0]["with"]["path"].endswith("/a-custody-public.json"))
        result = next(step for step in steps if "Never classify failed host custody" in step.get("name", ""))
        self.assertIn("PREFLIGHT_METADATA_PASS", result["run"])
        self.assertIn("PRIVATE_SNAPSHOT_RESTORE_OK", result["run"])
        self.assertIn("a-custody-public.json", result["run"])

    def test_sealed_history_download_validates_content_and_preserves_other_files(self):
        tree, part = "a"*40, "b"*40
        encrypted = b"synthetic sealed data"
        index = {"sha": tree, "truncated": False,
                 "tree": [{"path": "part-000.der", "type": "blob", "mode": "100644",
                           "sha": part, "size": len(encrypted)}]}
        blob = {"sha": part, "encoding": "base64", "content": base64.b64encode(encrypted).decode()}

        def fake_response(request, timeout):
            return io.BytesIO(json.dumps(index if request.full_url.endswith(tree) else blob).encode())

        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"GH_TOKEN": "test"}):
            output = Path(folder) / "sealed.cms"
            digest = hashlib.sha256(encrypted).hexdigest()
            with patch.object(custody.urllib.request, "urlopen", fake_response):
                custody.fetch_sealed_history(tree, digest, output)
                self.assertEqual(output.read_bytes(), encrypted)
                with self.assertRaises(FileExistsError):
                    custody.fetch_sealed_history(tree, digest, output)
                self.assertTrue(output.is_file())
                with self.assertRaisesRegex(custody.Hold, "HISTORY_IMPORT_CAPSULE_DIGEST_MISMATCH"):
                    custody.fetch_sealed_history(tree, "f"*64, Path(folder) / "invalid.cms")
                self.assertFalse((Path(folder) / "invalid.cms").exists())

    def test_history_reference_receipt_requires_the_exact_count(self):
        evidence = {"schema": "rc6.history-reference-transfer.v1", "state": "HISTORY_REFERENCE_APPLIED",
                    "run_id": "44917242971", "restore_sha256": "a"*64,
                    "source_reported_committed_rows": 152052, "legacy_versions_preserved": 596196,
                    "canonical_updates": 152052, "versions_appended": 152052,
                    "quick_check": "ok", "preparation": "SCHEMA_UPGRADED", "private": "never expose"}
        verified = custody.history_reference_receipt(evidence, "HISTORY_REFERENCE_APPLY", 0)
        self.assertEqual(verified["state"], "HISTORY_REFERENCE_APPLIED")
        self.assertNotIn("private", verified)
        evidence["source_reported_committed_rows"] -= 1
        self.assertEqual(custody.history_reference_receipt(evidence, "HISTORY_REFERENCE_APPLY", 0)["state"],
                         "NO_VERIFICADO")


if __name__ == "__main__":
    unittest.main()
