"""A726 off-host hermetic and adversarial guard tests. Never opens a host."""

from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import stat
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rc6_a726_postfailure_deep_ro as d


R1 = "a" * 40
R2 = "b" * 40
CAND = "c" * 40
GEN = "d" * 64
SNAP = "e" * 32
IMAGE = "sha256:" + "f" * 64


@contextmanager
def ro_statvfs():
    read_only = SimpleNamespace(f_flag=os.ST_RDONLY)
    with mock.patch.object(d.os, "fstatvfs", return_value=read_only), \
         mock.patch.object(d.os, "statvfs", return_value=read_only):
        yield


def receipt():
    return {
        "schema": d.SCHEMA, "state": "DIAGNOSTIC_ONLY", "read_only": True,
        "ro_mount_verified": True, "mutations": 0, "provider_gets": 0,
        "routes_called": False, "runtime_validated": False,
        "marker": {"state": "OBSERVED", "relation": "EXPECTED_R1_VALIDATED",
                   "paper_zero": True, "routes_not_called": True},
        "book": {"state": "READ_ONLY_OPENED", "observer": {
            "state": "OBSERVED", "mode_paper": True, "orders_zero": True,
            "process": "STOPPED", "session": "MARKET_CLOSED"},
            "ledgers": {name: {"state": "OBSERVED", "count": 0}
                        for name in d.TABLES},
            "three_ledgers_flat": True, "four_ledgers_flat": True,
            "quiescence_verified": False},
        "cold": {"phase": "PACK_CLASSIFIED", "current_hash_bound": True,
                 "current_paper_zero": True, "current_routes_blocked": True,
                 "reference_bound": True, "source_receipts_bound": True,
                 "pack_state": "HASH_MATCH", "pack_hash_verified": True,
                 "full_computed_report_verified": False,
                 "historical_archive_tested": False},
        "disk": {"state": "OBSERVED", "root_free_bytes": 4_500_000_000,
                 "docker_free_bytes": 4_500_000_000, "same_filesystem": True,
                 "historical_reserve_bytes": d.REFERENCE_RESERVE_BYTES,
                 "historical_reserve_met": True,
                 "r3_pretransfer_sufficient": "NO_VERIFICADO"},
    }


def pack_envelope(value: dict, *, cold: bool = False) -> bytes:
    key = "sha256" if cold else "digest"
    return json.dumps({"payload": value,
                       key: hashlib.sha256(d._canonical(value)).hexdigest()}).encode()


class A726Test(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix="a726-offhost-")
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "porota_data"
        self.data.mkdir()
        self.data.chmod(0o700)
        self.patcher = mock.patch.object(d, "DATA", self.data)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def mkdb(self, *, future=True, caucion=True, fci=True):
        base = self.data / "paper_v17"
        base.mkdir(exist_ok=True)
        path = base / "observer_v17.db"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE observer_state(id INT,mode TEXT,real_orders_sent INT,"
                       "process_state TEXT,session_state TEXT)")
            db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0,"
                       "'STOPPED','MARKET_CLOSED')")
            db.execute("CREATE TABLE paper_positions(id INT,status TEXT)")
            if future:
                db.execute("CREATE TABLE paper_future_positions(id INT,status TEXT)")
            if caucion:
                db.execute("CREATE TABLE paper_cauciones(id INT,status TEXT)")
            if fci:
                db.execute("CREATE TABLE paper_family_lifecycle(id INT,family TEXT,state TEXT)")
        return path

    def mkpack(self, content=b"A726-pack-evidence", *, snapshot=SNAP):
        shadow = self.data / "paper_v17/artifacts/observer_v17.db/dynamic-shadow"
        shadow.mkdir(parents=True)
        source = shadow / "cold-audit"
        source.mkdir()
        source.chmod(0o700)
        pack = source / (snapshot + ".pack")
        pack.write_bytes(content)
        pack.chmod(0o600)
        info = pack.stat()
        manifest = {"snapshot_id": snapshot, "epoch": "rc6.audit-actual-values.v1",
                    "pack": snapshot + ".pack", "pack_bytes": info.st_size,
                    "pack_sha256": hashlib.sha256(content).hexdigest(),
                    "pack_identity": list(d._identity(info))}
        sha = hashlib.sha256(d._canonical(manifest)).hexdigest()
        for name, data in (
            ("manifest-" + snapshot + ".json", manifest),
            ("receipt-" + snapshot + ".json", {
                "state": "FAILED", "snapshot_id": snapshot, "manifest_sha256": sha}),
            ("queue.json", {"state": "FAILED", "snapshot_id": snapshot,
                            "manifest_sha256": sha})):
            path = source / name
            path.write_bytes(pack_envelope(data, cold=True))
            path.chmod(0o600)
        cut_manifest = {"generation_id": GEN, "sequence": 42}
        pointer = {"generation_id": GEN, "sequence": 42,
                   "manifest_sha256": hashlib.sha256(d._canonical(cut_manifest)).hexdigest(),
                   "file": "cut-" + GEN + ".json.gz"}
        current = shadow / "CURRENT.json"
        current.write_bytes(pack_envelope(pointer))
        current.chmod(0o600)
        cut = {"manifest": cut_manifest, "status": {
            "real_orders_sent": 0, "real_routes": "NOT_CALLED"},
            "report": {"computed_report": {
                "schema": "rc6.computed-audit-reference.v2", "state": "PENDING",
                "snapshot_id": snapshot, "manifest_sha256": sha}}}
        path = shadow / pointer["file"]
        path.write_bytes(gzip.compress(pack_envelope(cut)))
        path.chmod(0o600)
        return shadow, source, manifest, pack

    def test_book_four_ledgers_flat(self):
        with ro_statvfs():
            row = d._book(self.mkdb())
        self.assertEqual(row["state"], "READ_ONLY_OPENED")
        self.assertTrue(row["observer"]["mode_paper"])
        self.assertTrue(row["three_ledgers_flat"])
        self.assertTrue(row["four_ledgers_flat"])
        self.assertFalse(row["quiescence_verified"])

    def test_book_keeps_observer_and_spot_on_one_wal_snapshot(self):
        path = self.mkdb()
        writer = sqlite3.connect(path)
        self.addCleanup(writer.close)
        self.assertEqual(writer.execute("PRAGMA journal_mode=WAL").fetchone(), ("wal",))
        read_ledger = d._ledger

        def read_after_commit(db, table, predicate, required):
            if table == "paper_positions":
                with writer:
                    writer.execute("UPDATE observer_state SET real_orders_sent=1 WHERE id=1")
                    writer.execute("INSERT INTO paper_positions VALUES (1,'OPEN')")
            return read_ledger(db, table, predicate, required)

        with ro_statvfs():
            with mock.patch.object(d, "_ledger", read_after_commit):
                before = d._book(path)
            after = d._book(path)

        self.assertEqual((before["observer"]["orders_zero"], before["ledgers"]["spot"]["count"]),
                         (True, 0))
        self.assertEqual((after["observer"]["orders_zero"], after["ledgers"]["spot"]["count"]),
                         (False, 1))

    def test_book_missing_fci_not_falsely_flat(self):
        with ro_statvfs():
            row = d._book(self.mkdb(fci=False))
        self.assertTrue(row["three_ledgers_flat"])
        self.assertIsNone(row["four_ledgers_flat"])
        self.assertEqual(row["ledgers"]["fci"], {
            "state": "TABLE_ABSENT", "count": None})
        self.assertTrue(all(row["ledgers"][key]["count"] == 0 for key in
                            ("spot", "futures", "caucion")))

    def test_book_missing_futures_independent_counts(self):
        with ro_statvfs():
            row = d._book(self.mkdb(future=False))
        self.assertIsNone(row["three_ledgers_flat"])
        self.assertEqual(row["ledgers"]["futures"]["state"], "TABLE_ABSENT")
        self.assertEqual(row["ledgers"]["spot"]["count"], 0)
        self.assertEqual(row["ledgers"]["caucion"]["count"], 0)

    def test_book_positive_spot_is_definite_not_flat(self):
        path = self.mkdb()
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO paper_positions VALUES (1,'OPEN')")
        with ro_statvfs():
            row = d._book(path)
        self.assertFalse(row["three_ledgers_flat"])
        self.assertFalse(row["four_ledgers_flat"])

    def test_book_positive_spot_even_with_unknown_fci_false(self):
        path = self.mkdb(fci=False)
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO paper_positions VALUES (1,'OPEN')")
        with ro_statvfs():
            row = d._book(path)
        self.assertFalse(row["four_ledgers_flat"])

    def test_book_schema_missing_status_bounded(self):
        path = self.mkdb()
        with sqlite3.connect(path) as db:
            db.execute("DROP TABLE paper_cauciones")
            db.execute("CREATE TABLE paper_cauciones(id INT,ticker TEXT)")
        with ro_statvfs():
            row = d._book(path)
        self.assertEqual(row["ledgers"]["caucion"]["state"], "SCHEMA_UNEXPECTED")
        self.assertIsNone(row["ledgers"]["caucion"]["count"])

    def test_book_db_not_readonly_fails_closed(self):
        row = d._book(self.mkdb())
        self.assertEqual(row["state"], "NO_VERIFICADO")
        self.assertIsNone(row["four_ledgers_flat"])

    def test_book_symlink_to_foreign_tree_refused(self):
        self.mkdb()
        external = Path(self.tmp.name) / "external-app"
        external.mkdir()
        (self.data / "bad").symlink_to(external, target_is_directory=True)
        with self.assertRaises(d.Hold):
            d._safe_parent(self.data / "bad/secret.json")

    def test_pack_hash_exact_match(self):
        _, source, manifest, pack = self.mkpack()
        with ro_statvfs():
            self.assertEqual(d._pack_hash(pack, manifest, source.stat().st_uid), "HASH_MATCH")

    def test_pack_hash_mismatch(self):
        _, source, manifest, pack = self.mkpack()
        manifest["pack_sha256"] = "0" * 64
        with ro_statvfs():
            self.assertEqual(d._pack_hash(pack, manifest, source.stat().st_uid), "HASH_MISMATCH")

    def test_pack_hash_refuses_invalid_expected(self):
        _, source, manifest, pack = self.mkpack()
        manifest["pack_sha256"] = "TOKEN_PRIVATE"
        with ro_statvfs():
            self.assertEqual(d._pack_hash(pack, manifest, source.stat().st_uid),
                             "NO_VALID_EXPECTED_HASH")

    def test_pack_hash_owner_mismatch(self):
        _, source, manifest, pack = self.mkpack()
        with ro_statvfs():
            self.assertEqual(d._pack_hash(pack, manifest, os.getuid() + 1),
                             "METADATA_UNSAFE")

    def test_pack_hash_metadata_changed(self):
        _, source, manifest, pack = self.mkpack()
        manifest["pack_bytes"] += 1
        with ro_statvfs():
            self.assertEqual(d._pack_hash(pack, manifest, source.stat().st_uid),
                             "METADATA_UNSAFE")

    def test_pack_hash_oversize_fails_closed(self):
        _, source, manifest, pack = self.mkpack()
        with ro_statvfs():
            self.assertEqual(d._pack_hash(pack, manifest, source.stat().st_uid,
                                          max_bytes=4), "OVERSIZE")

    def test_pack_symlink_rejected(self):
        _, source, manifest, pack = self.mkpack()
        symlink = pack.with_suffix(".symlink")
        symlink.symlink_to(pack)
        with ro_statvfs():
            self.assertEqual(d._pack_hash(symlink, manifest, source.stat().st_uid),
                             "READ_UNAVAILABLE")

    def test_cold_current_pack_full_hash_bound(self):
        shadow, *_ = self.mkpack()
        with ro_statvfs():
            result = d._cold(shadow)
        self.assertEqual(result["phase"], "PACK_CLASSIFIED")
        self.assertEqual(result["pack_state"], "HASH_MATCH")
        self.assertTrue(result["pack_hash_verified"])
        self.assertTrue(result["source_receipts_bound"])
        self.assertTrue(result["reference_bound"])
        self.assertFalse(result["historical_archive_tested"])
        self.assertFalse(result["full_computed_report_verified"])

    def test_cold_current_digest_edited_fail_closed(self):
        shadow, *_ = self.mkpack()
        pointer = shadow / "CURRENT.json"
        wrapped = json.loads(pointer.read_text())
        wrapped["digest"] = "0" * 64
        pointer.write_text(json.dumps(wrapped))
        with ro_statvfs():
            result = d._cold(shadow)
        self.assertEqual(result["phase"], "CURRENT_UNAVAILABLE")
        self.assertFalse(result["pack_hash_verified"])

    def test_cold_false_is_not_integer_zero_orders(self):
        shadow, *_ = self.mkpack()
        cut = shadow / ("cut-" + GEN + ".json.gz")
        obj = json.loads(gzip.decompress(cut.read_bytes()))["payload"]
        obj["status"]["real_orders_sent"] = False
        cut.write_bytes(gzip.compress(pack_envelope(obj)))
        with ro_statvfs():
            result = d._cold(shadow)
        self.assertFalse(result["current_paper_zero"])

    def test_cold_cut_reference_mismatch_prevents_pack_read(self):
        shadow, _, _, pack = self.mkpack()
        cut = shadow / ("cut-" + GEN + ".json.gz")
        obj = json.loads(gzip.decompress(cut.read_bytes()))["payload"]
        obj["report"]["computed_report"]["manifest_sha256"] = "0" * 64
        cut.write_bytes(gzip.compress(pack_envelope(obj)))
        with ro_statvfs(), mock.patch.object(d, "_pack_hash") as hashed:
            result = d._cold(shadow)
        self.assertEqual(result["phase"], "RECEIPTS_UNBOUND")
        hashed.assert_not_called()

    def test_cold_missing_queue_never_opens_pack(self):
        shadow, source, *_ = self.mkpack()
        (source / "queue.json").unlink()  # synthetic fixture only
        with ro_statvfs(), mock.patch.object(d, "_pack_hash") as hashed:
            result = d._cold(shadow)
        self.assertEqual(result["phase"], "QUEUE_UNAVAILABLE")
        hashed.assert_not_called()

    def test_cold_missing_receipt_never_opens_pack(self):
        shadow, source, *_ = self.mkpack()
        (source / ("receipt-" + SNAP + ".json")).unlink()  # fixture only
        with ro_statvfs(), mock.patch.object(d, "_pack_hash") as hashed:
            result = d._cold(shadow)
        self.assertEqual(result["phase"], "RECEIPTS_UNAVAILABLE")
        hashed.assert_not_called()

    def test_cold_historical_snapshot_refused(self):
        historical = "7dc99ccd8f7c47849ca3705ae1cb2c49"
        shadow, *_ = self.mkpack(snapshot=historical)
        with ro_statvfs(), mock.patch.object(d, "_pack_hash") as hashed:
            result = d._cold(shadow)
        self.assertEqual(result["phase"], "REVOKED_OR_UNKNOWN")
        hashed.assert_not_called()

    def test_disk_observed_arithmetic_no_transfer_claim(self):
        class FakeStat:
            f_bavail = 900_000
            f_frsize = 4096
        with mock.patch.object(d.os, "statvfs", return_value=FakeStat()), \
             mock.patch.object(d.os.path, "isdir", return_value=True), \
             mock.patch.object(d.subprocess, "run", return_value=SimpleNamespace(
                 returncode=0, stdout="/var/lib/docker\n")), \
             mock.patch.object(d.os, "stat", return_value=SimpleNamespace(st_dev=1)):
            result = d._disk()
        self.assertEqual(result["root_free_bytes"], 3_686_400_000)
        self.assertFalse(result["historical_reserve_met"])
        self.assertEqual(result["r3_pretransfer_sufficient"], "NO_VERIFICADO")

    def test_sanitize_exact_good_receipt(self):
        out = d.sanitize(json.dumps(receipt()), R1, R2, CAND)
        self.assertEqual(out["state"], "DIAGNOSTIC_ONLY")
        self.assertEqual(out["cold"]["pack_state"], "HASH_MATCH")
        self.assertFalse(out["book"]["quiescence_verified"])
        self.assertEqual(out["expected_previous_sha"], R1)

    def test_sanitize_missing_fci_never_flat_all(self):
        obj = receipt()
        obj["book"]["ledgers"]["fci"] = {"state": "TABLE_ABSENT", "count": None}
        obj["book"]["four_ledgers_flat"] = None
        out = d.sanitize(json.dumps(obj), R1, R2, CAND)
        self.assertEqual(out["state"], "DIAGNOSTIC_ONLY")
        self.assertIsNone(out["book"]["four_ledgers_flat"])

    def test_sanitize_secret_in_all_nested_shapes_rejected(self):
        for path in ("marker", "book", "observer", "ledger", "cold", "disk"):
            obj = receipt()
            target = (obj["book"]["observer"] if path == "observer" else
                      obj["book"]["ledgers"]["spot"] if path == "ledger" else
                      obj[path])
            target["secret_token"] = "PRIVATEKEY123"
            with self.subTest(path=path):
                out = d.sanitize(json.dumps(obj), R1, R2, CAND)
                self.assertEqual(out["state"], "NO_VERIFICADO")
                self.assertNotIn("PRIVATEKEY123", json.dumps(out))

    def test_sanitize_secret_in_allowlisted_values_rejected(self):
        for path, key in (("marker", "relation"), ("cold", "pack_state"),
                          ("disk", "r3_pretransfer_sufficient")):
            obj = receipt()
            obj[path][key] = "PRIVATEKEY123"
            with self.subTest(path=path):
                out = d.sanitize(json.dumps(obj), R1, R2, CAND)
                self.assertEqual(out["state"], "NO_VERIFICADO")
                self.assertNotIn("PRIVATEKEY123", json.dumps(out))

    def test_sanitize_secret_in_observer_state_rejected(self):
        obj = receipt()
        obj["book"]["observer"]["process"] = "PRIVATEKEY123"
        self.assertEqual(d.sanitize(json.dumps(obj), R1, R2, CAND)["state"],
                         "NO_VERIFICADO")

    def test_sanitize_count_boolean_is_not_int(self):
        obj = receipt()
        obj["book"]["ledgers"]["spot"]["count"] = False
        self.assertEqual(d.sanitize(json.dumps(obj), R1, R2, CAND)["state"],
                         "NO_VERIFICADO")

    def test_sanitize_count_negative_rejected(self):
        obj = receipt()
        obj["book"]["ledgers"]["spot"]["count"] = -1
        self.assertEqual(d.sanitize(json.dumps(obj), R1, R2, CAND)["state"],
                         "NO_VERIFICADO")

    def test_sanitize_flat_contradiction_rejected(self):
        obj = receipt()
        obj["book"]["four_ledgers_flat"] = False
        self.assertEqual(d.sanitize(json.dumps(obj), R1, R2, CAND)["state"],
                         "NO_VERIFICADO")

    def test_sanitize_pack_hash_false_proof_rejected(self):
        obj = receipt()
        obj["cold"]["pack_hash_verified"] = False
        self.assertEqual(d.sanitize(json.dumps(obj), R1, R2, CAND)["state"],
                         "NO_VERIFICADO")

    def test_sanitize_disk_false_arithmetic_rejected(self):
        obj = receipt()
        obj["disk"]["root_free_bytes"] = 1
        self.assertEqual(d.sanitize(json.dumps(obj), R1, R2, CAND)["state"],
                         "NO_VERIFICADO")

    def test_sanitize_true_runtime_claim_rejected(self):
        obj = receipt()
        obj["runtime_validated"] = True
        self.assertEqual(d.sanitize(json.dumps(obj), R1, R2, CAND)["state"],
                         "NO_VERIFICADO")

    def test_sanitize_attempted_mutation_rejected(self):
        obj = receipt()
        obj["mutations"] = 1
        self.assertEqual(d.sanitize(json.dumps(obj), R1, R2, CAND)["state"],
                         "NO_VERIFICADO")

    def test_sanitize_does_not_echo_raw_host_sha(self):
        obj = receipt()
        obj["marker"]["raw_image_id"] = "sha256:" + "f" * 64
        out = d.sanitize(json.dumps(obj), R1, R2, CAND)
        self.assertEqual(out["state"], "NO_VERIFICADO")
        self.assertNotIn("sha256:", json.dumps(out))

    def test_sanitize_invalid_expected_sha_fails_closed(self):
        self.assertEqual(d.sanitize(json.dumps(receipt()), "PRIVATE_TOKEN", R2, CAND)
                         ["state"], "NO_VERIFICADO")

    def test_sanitize_oversize_fails_closed(self):
        self.assertEqual(d.sanitize(" " * 8193, R1, R2, CAND)["state"],
                         "NO_VERIFICADO")

    def test_readonly_mount_negative_prevents_host_inspection(self):
        with mock.patch.object(d, "_ro_mount", return_value=False), \
             mock.patch.object(d, "_book") as book:
            value = d.observe(R1, R2, CAND)
        self.assertEqual(value["state"], "NO_VERIFICADO")
        book.assert_not_called()


if __name__ == "__main__":
    unittest.main()
