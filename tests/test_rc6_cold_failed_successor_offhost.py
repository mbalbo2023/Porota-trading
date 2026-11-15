"""No-host regression for the real DISK_RESERVE/REPORT_WRITE COLD failure.

The test constructs genuine bounded envelopes, pack bytes and an immutable
current cut. It never contacts Docker, a provider, or the production filesystem.
"""

import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_cold2_quarantine as old
from scripts import rc6_cold2_successor as successor
from scripts import rc6_failed_cold_fix_forward as recovery


SNAPSHOT = "e7f8eb9dfc954b4c99346a14ed280e4f"
CUT = "b3d10eb98e51b04c2d11039dd3fd98394348b6078c1369f99e3a1371903a07eb"
SEALED = 1792485190.2664664


def envelope(path, payload, *, cold=False, zipped=False):
    outer = {"payload": payload,
             "sha256" if cold else "digest": old.digest(payload)}
    raw = json.dumps(outer, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(gzip.compress(raw) if zipped else raw)
    path.chmod(0o600)


def failed_successor(root, *, code="SHADOW_COLD_DISK_RESERVE_BREACHED",
                     phase="REPORT_WRITE"):
    shadow = root / "data/paper_v17/artifacts/observer_v17.db/dynamic-shadow"
    source = shadow / "cold-audit"
    source.mkdir(parents=True, mode=0o700)
    (root / "data/deploy").mkdir(parents=True)
    source.chmod(0o700)
    shadow.chmod(0o700)
    pack = source / (SNAPSHOT + ".pack")
    raw = b"fully sealed failed COLD input" * 128
    pack.write_bytes(raw)
    pack.chmod(0o600)
    stat = pack.stat()
    manifest = {
        "epoch": "rc6.audit-actual-values.v1", "snapshot_id": SNAPSHOT,
        "pack": pack.name, "pack_bytes": len(raw),
        "pack_sha256": hashlib.sha256(raw).hexdigest(),
        "pack_identity": [stat.st_dev, stat.st_ino, stat.st_size,
                          stat.st_mtime_ns, stat.st_ctime_ns],
        "source_as_of": "2026-10-20T08:32:29.850494+00:00",
        "configuration_fingerprint": "c" * 64,
        "sealed_at": SEALED, "deadline_at": SEALED + 30.0,
    }
    manifest_sha = old.digest(manifest)
    envelope(source / ("manifest-" + SNAPSHOT + ".json"), manifest, cold=True)
    envelope(source / ("receipt-" + SNAPSHOT + ".json"), {
        "state": "FAILED", "snapshot_id": SNAPSHOT,
        "manifest_sha256": manifest_sha, "error_class": "ValueError",
        "error_code": code, "error_phase": phase,
        "failed_at": SEALED + 30.04,
    }, cold=True)
    envelope(source / "queue.json", {
        "snapshot_id": SNAPSHOT, "manifest_sha256": manifest_sha,
        "state": "FAILED"}, cold=True)
    envelope(shadow / "CURRENT.json", {
        "generation_id": CUT, "file": "cut-" + CUT + ".json.gz"})
    envelope(shadow / ("cut-" + CUT + ".json.gz"), {
        "report": {"computed_report": {
            "schema": "rc6.computed-audit-reference.v2",
            "state": "PENDING", "snapshot_id": SNAPSHOT,
            "manifest_sha256": manifest_sha, "deadline_at": SEALED + 30.0,
            "source_as_of": manifest["source_as_of"],
            "configuration_fingerprint": manifest["configuration_fingerprint"],
        }},
        "status": {"real_orders_sent": 0, "real_routes": "NOT_CALLED"},
    }, zipped=True)
    envelope(shadow / "status.json", {
        "status": "FAIL_CLOSED", "real_orders_sent": 0,
        "real_routes": "NOT_CALLED"})
    return source


def old_failed_archive(root):
    """An actual original failed archive, constructed under a temporary root."""
    archive = root / "data/deploy" / ("rc6-failed-cold-" + recovery.IDENT)
    archive.mkdir(mode=0o700)
    pack = archive / (recovery.IDENT + ".pack")
    raw = b"previous failed COLD generation" * 32
    pack.write_bytes(raw)
    pack.chmod(0o600)
    info = pack.stat()
    manifest = {
        "snapshot_id": recovery.IDENT, "pack": pack.name,
        "pack_bytes": len(raw), "pack_sha256": hashlib.sha256(raw).hexdigest(),
        "pack_identity": [info.st_dev, info.st_ino, info.st_size,
                          info.st_mtime_ns, info.st_ctime_ns],
        "deadline_at": SEALED - 10.0,
    }
    digest = old.digest(manifest)
    envelope(archive / ("manifest-" + recovery.IDENT + ".json"), manifest, cold=True)
    envelope(archive / ("receipt-" + recovery.IDENT + ".json"), {
        "snapshot_id": recovery.IDENT, "state": "FAILED", "error_class": "ValueError",
        "manifest_sha256": digest, "failed_at": manifest["deadline_at"] + 0.001,
    }, cold=True)
    envelope(archive / "queue.json", {
        "snapshot_id": recovery.IDENT, "manifest_sha256": digest, "state": "FAILED",
    }, cold=True)
    return archive, digest


class ColdDiskFailureContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = failed_successor(self.root)

    def archive_argument(self):
        return self.root / "data/deploy" / ("rc6-failed-cold-" + recovery.IDENT)

    def test_disk_reserve_report_write_is_inspectable_only_as_failed(self):
        evidence = successor.inspect(self.root, hash_pack=True)
        self.assertEqual(evidence["snapshot_id"], SNAPSHOT)
        self.assertTrue(evidence["queue_failed"])
        self.assertIsNone(evidence["failed_computed_report"])

    def test_failed_report_write_successor_is_proven_without_synthetic_report(self):
        self.assertTrue(recovery.proven_distinct_failed_successor(
            self.archive_argument(), CUT, SNAPSHOT))

    def test_deadline_report_write_stays_unproven_without_report(self):
        path = self.source / ("receipt-" + SNAPSHOT + ".json")
        receipt = old.envelope(path, cold=True)
        receipt["error_code"] = "SHADOW_COLD_DEADLINE_EXPIRED_OR_CLOCK_REGRESSED"
        envelope(path, receipt, cold=True)
        self.assertFalse(recovery.proven_distinct_failed_successor(
            self.archive_argument(), CUT, SNAPSHOT))

    def test_checked_integrates_old_archived_failure_with_distinct_failed_successor(self):
        archive, original_digest = old_failed_archive(self.root)
        current_manifest = {"generation_id": CUT, "sequence": 7}
        shadow = self.source.parent
        envelope(shadow / "CURRENT.json", {
            "generation_id": CUT, "file": "cut-" + CUT + ".json.gz",
            "manifest_sha256": old.digest(current_manifest), "sequence": 7})
        envelope(shadow / ("cut-" + CUT + ".json.gz"), {
            "manifest": current_manifest,
            "report": {"computed_report": {
                "schema": "rc6.computed-audit-reference.v2", "state": "PENDING",
                "snapshot_id": SNAPSHOT,
                "manifest_sha256": old.digest(old.envelope(
                    self.source / ("manifest-" + SNAPSHOT + ".json"), cold=True)),
                "deadline_at": SEALED + 30.0,
                "source_as_of": "2026-10-20T08:32:29.850494+00:00",
                "configuration_fingerprint": "c" * 64,
            }}, "status": {"real_orders_sent": 0, "real_routes": "NOT_CALLED"},
        }, zipped=True)
        with patch.object(recovery, "MANIFEST_SHA", original_digest):
            evidence = recovery.checked(archive, shadow, archived_recovery=True)
        self.assertEqual(evidence["current_cut"], CUT)
        self.assertEqual(evidence["snapshot_id"], recovery.IDENT)
        self.assertTrue(evidence["old_cut_gc_complete"])

    def test_tampered_failed_pack_cannot_prove_recovery(self):
        with (self.source / (SNAPSHOT + ".pack")).open("r+b") as stream:
            stream.write(b"TAMPER")
        self.assertFalse(recovery.proven_distinct_failed_successor(
            self.archive_argument(), CUT, SNAPSHOT))

    def test_failed_successor_never_proves_real_order_route(self):
        envelope(self.source.parent / "status.json", {
            "status": "FAIL_CLOSED", "real_orders_sent": 1,
            "real_routes": "NOT_CALLED"})
        self.assertFalse(recovery.proven_distinct_failed_successor(
            self.archive_argument(), CUT, SNAPSHOT))

    def test_missing_failed_queue_and_unsealed_archive_does_not_prove(self):
        (self.source / "queue.json").unlink()
        self.assertFalse(recovery.proven_distinct_failed_successor(
            self.archive_argument(), CUT, SNAPSHOT))

    def test_report_write_has_no_report_before_or_after_failed_quarantine(self):
        before = successor.inspect(self.root, hash_pack=True)
        self.assertIsNone(before["failed_computed_report"])
        with patch.object(old, "_admit", return_value=None):
            result = successor.quarantine(self.root, admission=None,
                                          expected_owned_ids=None)
            self.assertEqual(result["state"], "QUARANTINED_FAILED")
            self.assertEqual(successor.quarantine(self.root, admission=None,
                                                  expected_owned_ids=None)["state"],
                             "ALREADY_QUARANTINED")
        after = successor.inspect(self.root, hash_pack=True)
        self.assertTrue(after["archive_complete"])
        self.assertFalse(after["queue_failed"])
        self.assertIsNone(after["failed_computed_report"])
        self.assertTrue(recovery.proven_distinct_failed_successor(
            self.archive_argument(), CUT, SNAPSHOT))
        record = old.envelope(self.root / "data/deploy" /
                              ("rc6-failed-cold-" + SNAPSHOT) / "quarantine.json")
        self.assertEqual(record["state"], "QUARANTINED_FAILED")
        self.assertIsNone(record["failed_computed_report"])
        self.assertEqual(old.envelope(self.source.parent / "status.json")["status"],
                         "FAIL_CLOSED")

    def test_rejected_receipt_error_code_remains_closed(self):
        path = self.source / ("receipt-" + SNAPSHOT + ".json")
        receipt = old.envelope(path, cold=True)
        receipt["error_code"] = "SHADOW_COLD_FICTITIOUS_RESERVE"
        envelope(path, receipt, cold=True)
        self.assertFalse(recovery.proven_distinct_failed_successor(
            self.archive_argument(), CUT, SNAPSHOT))

    def test_report_verify_failure_without_exact_report_remains_closed(self):
        path = self.source / ("receipt-" + SNAPSHOT + ".json")
        receipt = old.envelope(path, cold=True)
        receipt["error_code"] = "SHADOW_COLD_HOST_MEMORY_RESERVE_BREACHED"
        receipt["error_phase"] = "REPORT_VERIFY"
        envelope(path, receipt, cold=True)
        self.assertFalse(recovery.proven_distinct_failed_successor(
            self.archive_argument(), CUT, SNAPSHOT))

    def test_pack_alias_remains_closed(self):
        pack = self.source / (SNAPSHOT + ".pack")
        link = self.source.parent / "extra-hardlink"
        link.hardlink_to(pack)
        self.assertFalse(recovery.proven_distinct_failed_successor(
            self.archive_argument(), CUT, SNAPSHOT))

    def test_pack_writable_by_others_remains_closed(self):
        pack = self.source / (SNAPSHOT + ".pack")
        pack.chmod(0o666)
        self.assertFalse(recovery.proven_distinct_failed_successor(
            self.archive_argument(), CUT, SNAPSHOT))

    def test_unmatched_current_snapshot_is_not_adopted(self):
        self.assertFalse(recovery.proven_distinct_failed_successor(
            self.archive_argument(), CUT, "f" * 32))

    def test_wrong_error_phase_pack_hash_remains_unproven(self):
        path = self.source / ("receipt-" + SNAPSHOT + ".json")
        receipt = old.envelope(path, cold=True)
        receipt["error_code"] = "SHADOW_COLD_HOST_MEMORY_RESERVE_BREACHED"
        receipt["error_phase"] = "PACK_HASH"
        envelope(path, receipt, cold=True)
        self.assertFalse(recovery.proven_distinct_failed_successor(
            self.archive_argument(), CUT, SNAPSHOT))

    def test_report_write_with_unverified_new_computed_file_stays_closed(self):
        # A write may fail after a file appears but before its integrity is
        # verified. A new computed file cannot be silently treated as absent.
        raw = gzip.compress(b"plausible but unverified computed report")
        report = self.source.parent / (
            "computed-" + hashlib.sha256(raw).hexdigest() + ".json.gz")
        report.write_bytes(raw)
        report.chmod(0o600)
        stamp = int((SEALED + 10) * 1_000_000_000)
        os.utime(report, ns=(stamp, stamp))
        self.assertFalse(recovery.proven_distinct_failed_successor(
            self.archive_argument(), CUT, SNAPSHOT))

    def test_called_real_routes_cannot_prove_successor(self):
        envelope(self.source.parent / "status.json", {
            "status": "FAIL_CLOSED", "real_orders_sent": 0,
            "real_routes": "CALLED"})
        self.assertFalse(recovery.proven_distinct_failed_successor(
            self.archive_argument(), CUT, SNAPSHOT))

    def test_unexpected_source_file_cannot_prove_successor(self):
        (self.source / "unexpected.txt").write_text("not part of failed pack")
        self.assertFalse(recovery.proven_distinct_failed_successor(
            self.archive_argument(), CUT, SNAPSHOT))

    def test_truncated_pack_cannot_prove_successor(self):
        with (self.source / (SNAPSHOT + ".pack")).open("r+b") as stream:
            stream.truncate(7)
        self.assertFalse(recovery.proven_distinct_failed_successor(
            self.archive_argument(), CUT, SNAPSHOT))


if __name__ == "__main__":
    unittest.main()
