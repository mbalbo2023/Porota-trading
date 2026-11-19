"""Real SQLite/WAL and fail-closed contracts for one-shot OBSERVER custody."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
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
            "candidate_sha": "b3cbe57a5b4b82016a65a0e253b251abbb84a04c",
            "candidate_tree_sha": "7370fea6c9c6daa802fcdd8ce6fc20c5cf8e31b0",
            "image_id": "sha256:62c649ac94c586f488c516f53a030b75529edd7529425254ff33b2a70bc4008d",
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
        with patch.object(custody, "_rootless_runtime_binding", return_value="BOTH_ROOTLESS_CIDS_DATA_DB_INODE_MATCHED"), \
             patch.object(custody, "_market_offhours_guard", return_value="OFFHOURS_ART_SAFE"):
            return custody.host_custody_once(self.db, self.marker, self.custody_root,
                                             run_id=self.RUN_ID)

    def test_no_custody_during_market_window_before_opening_sqlite(self):
        with patch.object(custody, "_market_offhours_guard",
                          side_effect=custody.Hold("CUSTODY_MARKET_WINDOW_UNSAFE")), \
             patch.object(custody.sqlite3, "connect", side_effect=AssertionError("SQLITE_OPEN")):
            with self.assertRaises(custody.Hold) as raised:
                custody.host_custody_once(self.db, self.marker, self.custody_root,
                                         run_id=self.RUN_ID)
        self.assertEqual(str(raised.exception), "CUSTODY_MARKET_WINDOW_UNSAFE")
        self.assertFalse(self.custody_root.exists())

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

    def test_market_window_guard_uses_argentina_local_time(self):
        with self.assertRaises(custody.Hold) as raised:
            custody._market_offhours_guard(datetime(2026, 11, 19, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(str(raised.exception), "CUSTODY_MARKET_WINDOW_UNSAFE")
        self.assertEqual(custody._market_offhours_guard(
                         datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc)),
                         "OFFHOURS_ART_SAFE")

    def test_online_snapshot_contains_committed_wal_and_restores_privately(self):
        self.assertNotIn(str(custody.CUSTODY), str(custody.ROOT))
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
               "offhours_gate": "OFFHOURS_ART_SAFE",
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
        raw["restore_sha256"] = "ef" * 32
        rejected = custody.sanitize_receipt("CUSTODY_ONCE", raw, remote_rc=0,
                                            expected_run_id="44508298821")
        self.assertEqual(rejected["state"], "NO_VERIFICADO_RECEIPT_UNSAFE")

    def test_valid_but_foreign_custody_run_is_rejected_even_with_matching_digest(self):
        digest = "cb" * 32
        raw = {"schema": custody.SCHEMA, "state": "PRIVATE_SNAPSHOT_RESTORE_OK",
               "read_only": False, "source_sql_writes": 0, "shm_locking_possible": True,
               "paper_zero": "OBSERVED", "run_id": "44508298821",
               "offhours_gate": "OFFHOURS_ART_SAFE",
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


class ActionsBoundaryContracts(unittest.TestCase):
    def test_pr_runs_only_tests_and_writer_requires_manual_main_owner(self):
        workflow_path = SOURCE.parents[1] / ".github/workflows/rc6-observer-online-custody-once.yml"
        workflow = yaml.load(workflow_path.read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow["concurrency"]["group"], "rc6-unified-paper-deploy")
        self.assertEqual(workflow["concurrency"]["cancel-in-progress"], "false")
        self.assertNotIn("push", workflow["on"])
        self.assertNotIn("schedule", workflow["on"])
        options = workflow["on"]["workflow_dispatch"]["inputs"]["mode"]["options"]
        self.assertEqual(options, ["PREFLIGHT_ONLY", "CUSTODY_ONCE"])
        host = workflow["jobs"]["host_custody"]
        gate = host["if"]
        self.assertIn("github.event_name == 'workflow_dispatch'", gate)
        self.assertIn("github.ref == 'refs/heads/main'", gate)
        steps = host["steps"]
        owner = next(step for step in steps if step.get("id") == "owner_gate")
        self.assertIn("inputs.mode == 'CUSTODY_ONCE'", owner["if"])
        self.assertIn("verify-owner", owner["run"])
        remote = next(step for step in steps if step.get("id") == "remote")
        self.assertIn("needs", host)
        self.assertIn("StrictHostKeyChecking=yes", remote["run"])
        self.assertIn("host-custody", remote["run"])
        self.assertIn("host-preflight", remote["run"])
        uploads = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact")]
        self.assertEqual(len(uploads), 1)
        self.assertTrue(uploads[0]["with"]["path"].endswith("/a-custody-public.json"))
        result = next(step for step in steps if "Never classify failed host custody" in step.get("name", ""))
        self.assertIn("PREFLIGHT_METADATA_PASS", result["run"])
        self.assertIn("PRIVATE_SNAPSHOT_RESTORE_OK", result["run"])
        self.assertIn("a-custody-public.json", result["run"])


if __name__ == "__main__":
    unittest.main()
