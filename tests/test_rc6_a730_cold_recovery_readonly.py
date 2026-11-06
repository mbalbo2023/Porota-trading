"""A730 hermetic tests; synthetic temporary fixtures only, zero host access."""

from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rc6_a730_cold_recovery_readonly as d


R1, R2, CAND = "a" * 40, "b" * 40, "c" * 40
GEN, SNAP = "d" * 64, "e" * 32


@contextmanager
def readonly_fs():
    ro = SimpleNamespace(f_flag=os.ST_RDONLY)
    with mock.patch.object(d.os, "fstatvfs", return_value=ro), \
         mock.patch.object(d.os, "statvfs", return_value=ro):
        yield


def envelope(payload, *, cold=False):
    key = "sha256" if cold else "digest"
    return json.dumps({"payload": payload,
        key: hashlib.sha256(d._canonical(payload)).hexdigest()}).encode()


def full_receipt():
    return {"schema": d.SCHEMA, "state": "DIAGNOSTIC_ONLY", "read_only": True,
            "ro_mount_verified": True, "mutations": 0, "provider_gets": 0,
            "routes_called": False, "runtime_validated": False,
            "marker": {"state": "OBSERVED", "relation": "VALIDATED_R1",
                       "paper_zero": True, "routes_blocked": True},
            "current": {"source_state": "RECEIPT_FAILED", "cut_hash_bound": True,
                        "reference_bound": True, "failed_queue": True,
                        "computed_report_state": "BLOCKED_FAILED_QUEUE",
                        "computed_report_hash_verified": False,
                        "successor_recovery_proven": False},
            "legacy_original": "PACK_METADATA_BOUND_NOT_HASHED",
            "legacy_cold2": "PACK_OWNER"}


class A730Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix="rc6-a730-test-")
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "porota_data"
        self.data.mkdir()
        self.deploy = self.data / "deploy"
        self.deploy.mkdir()
        self.shadow = self.data / "paper_v17/artifacts/observer_v17.db/dynamic-shadow"
        self.shadow.mkdir(parents=True)
        for key, value in (("DATA", self.data), ("DEPLOY", self.deploy),
                           ("SHADOW", self.shadow),
                           ("MARKER", self.deploy / "CURRENT_STATE_V2.json")):
            patcher = mock.patch.object(d, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, path, payload, *, cold=False):
        path.write_bytes(envelope(payload, cold=cold))
        path.chmod(0o600)

    def fixture_current(self, *, queue_state="FAILED", receipt_state="FAILED",
                        report=b'{"hello":"world"}'):
        source = self.shadow / "cold-audit"
        source.mkdir()
        source.chmod(0o700)
        manifest = {"snapshot_id": SNAP, "pack": SNAP + ".pack",
                    "epoch": "rc6.audit-actual-values.v1"}
        sha = hashlib.sha256(d._canonical(manifest)).hexdigest()
        self.write(source / ("manifest-" + SNAP + ".json"), manifest, cold=True)
        self.write(source / "queue.json", {"state": queue_state,
            "snapshot_id": SNAP, "manifest_sha256": sha}, cold=True)
        receipt = {"state": receipt_state, "snapshot_id": SNAP,
                   "manifest_sha256": sha}
        if receipt_state == "COMPLETE":
            digest = hashlib.sha256(report).hexdigest()
            path = self.shadow / ("computed-" + digest + ".json.gz")
            path.write_bytes(gzip.compress(report))
            path.chmod(0o600)
            receipt["computed_report"] = {"file": path.name, "sha256": digest,
                 "raw_bytes": len(report), "compressed_bytes": path.stat().st_size}
            receipt["computed_identity"] = list(d._identity(path.stat()))
        self.write(source / ("receipt-" + SNAP + ".json"), receipt, cold=True)
        cut_manifest = {"generation_id": GEN, "sequence": 7}
        self.write(self.shadow / "CURRENT.json", {"generation_id": GEN,
            "sequence": 7, "manifest_sha256": hashlib.sha256(
                d._canonical(cut_manifest)).hexdigest(),
            "file": "cut-" + GEN + ".json.gz"})
        cut = {"manifest": cut_manifest, "report": {"computed_report": {
            "schema": "rc6.computed-audit-reference.v2", "state": "PENDING",
            "snapshot_id": SNAP, "manifest_sha256": sha}}}
        cut_path = self.shadow / ("cut-" + GEN + ".json.gz")
        cut_path.write_bytes(gzip.compress(envelope(cut)))
        cut_path.chmod(0o600)
        return source

    def test_failed_queue_cannot_materialize_computed_report(self):
        self.fixture_current()
        with readonly_fs(), mock.patch.object(d, "_verify_computed") as verify:
            observed = d._current(self.shadow)
        self.assertEqual(observed["source_state"], "RECEIPT_FAILED")
        self.assertEqual(observed["computed_report_state"], "BLOCKED_FAILED_QUEUE")
        self.assertTrue(observed["failed_queue"])
        self.assertFalse(observed["computed_report_hash_verified"])
        verify.assert_not_called()

    def test_complete_receipt_only_with_hashed_report(self):
        self.fixture_current(queue_state="COMPLETE", receipt_state="COMPLETE")
        with readonly_fs():
            observed = d._current(self.shadow)
        self.assertEqual(observed["source_state"], "RECEIPT_COMPLETE")
        self.assertEqual(observed["computed_report_state"], "REPORT_HASH_MATCH")
        self.assertTrue(observed["computed_report_hash_verified"])
        self.assertFalse(observed["successor_recovery_proven"])

    def test_complete_receipt_corrupt_report_fails(self):
        self.fixture_current(queue_state="COMPLETE", receipt_state="COMPLETE")
        report = next(self.shadow.glob("computed-*.json.gz"))
        with gzip.open(report, "wb") as stream:
            stream.write(b"changed")
        with readonly_fs():
            observed = d._current(self.shadow)
        self.assertIn(observed["computed_report_state"],
                      {"FILE_METADATA_MISMATCH", "REPORT_HASH_MISMATCH"})
        self.assertFalse(observed["computed_report_hash_verified"])

    def test_complete_receipt_with_failed_queue_not_hashed(self):
        self.fixture_current(queue_state="FAILED", receipt_state="COMPLETE")
        with readonly_fs(), mock.patch.object(d, "_verify_computed") as verify:
            observed = d._current(self.shadow)
        self.assertEqual(observed["computed_report_state"], "RECEIPT_NOT_COMPLETE")
        verify.assert_not_called()

    def test_cut_hash_modified_blocks_queue(self):
        self.fixture_current()
        pointer = self.shadow / "CURRENT.json"
        obj = json.loads(pointer.read_text())
        obj["digest"] = "0" * 64
        pointer.write_text(json.dumps(obj))
        with readonly_fs():
            observed = d._current(self.shadow)
        self.assertEqual(observed["source_state"], "CURRENT_UNAVAILABLE")

    def test_missing_queue_classified_without_guess(self):
        source = self.fixture_current()
        (source / "queue.json").unlink()  # temporary fixture only
        with readonly_fs():
            observed = d._current(self.shadow)
        self.assertEqual(observed["source_state"], "QUEUE_UNAVAILABLE")
        self.assertFalse(observed["computed_report_hash_verified"])

    def test_legacy_archive_absence_is_not_computed_proof(self):
        with readonly_fs():
            state = d._archive(d.OLD_ID, d.OLD_MANIFEST, False)
        self.assertEqual(state, "ABSENT")

    def test_historical_archive_pack_owner_conservatively_reported(self):
        ident = d.HIST_ID
        archive = self.deploy / ("rc6-failed-cold-" + ident)
        archive.mkdir()
        archive.chmod(0o700)
        pack = archive / (ident + ".pack")
        pack.write_bytes(b"PRIVATE_PACK_NOT_READ")
        pack.chmod(0o600)
        info = pack.stat()
        manifest = {"snapshot_id": ident, "pack": ident + ".pack",
                    "pack_sha256": hashlib.sha256(b"PRIVATE_PACK_NOT_READ").hexdigest(),
                    "pack_bytes": info.st_size, "pack_identity": list(d._identity(info))}
        sha = hashlib.sha256(d._canonical(manifest)).hexdigest()
        self.write(archive / ("manifest-" + ident + ".json"), manifest, cold=True)
        self.write(archive / ("receipt-" + ident + ".json"), {
            "state": "FAILED", "snapshot_id": ident, "manifest_sha256": sha}, cold=True)
        self.write(archive / "queue.json", {"state": "FAILED", "snapshot_id": ident,
                                            "manifest_sha256": sha}, cold=True)
        self.write(archive / "quarantine.json", {"state": "QUARANTINED_FAILED",
            "snapshot_id": ident, "manifest_sha256": sha,
            "pack_sha256": manifest["pack_sha256"], "real_orders_sent": 0,
            "old_cut_revoked": True,
            "revoked_cut": d.HIST_CUT})
        # Trust only the test's exact synthetic manifest, and no pack data read.
        with readonly_fs(), mock.patch.object(d, "_regular", wraps=d._regular) as reader:
            state = d._archive(ident, sha, True)
        self.assertEqual(state, "PACK_METADATA_BOUND_NOT_HASHED")
        self.assertFalse(any(".pack" in str(call.args[0]) for call in reader.call_args_list))

    def test_archive_namespace_extra_is_not_trusted(self):
        archive = self.deploy / ("rc6-failed-cold-" + d.OLD_ID)
        archive.mkdir()
        archive.chmod(0o700)
        (archive / "suspicious.secret").write_text("PRIVATE")
        with readonly_fs():
            state = d._archive(d.OLD_ID, d.OLD_MANIFEST, False)
        self.assertEqual(state, "NAMESPACE_UNEXPECTED")

    def test_report_filename_injection_rejected(self):
        with readonly_fs():
            state = d._verify_computed(self.shadow / "safe", {
                "file": "../../PRIVATEKEY", "sha256": "a" * 64,
                "raw_bytes": 1, "compressed_bytes": 1})
        self.assertEqual(state, "COMPLETE_RECORD_INVALID")

    def test_report_rejects_size_above_512m_before_open(self):
        record = {"file": "computed-" + "a" * 64 + ".json.gz",
                  "sha256": "a" * 64,
                  "raw_bytes": d.MAX_RAW_REPORT + 1, "compressed_bytes": 1}
        with readonly_fs(), mock.patch.object(d.os, "open") as opener:
            state = d._verify_computed(self.shadow / record["file"], record)
        self.assertEqual(state, "COMPLETE_RECORD_INVALID")
        opener.assert_not_called()

    def test_observe_requires_readonly_mount(self):
        with mock.patch.object(d.os, "statvfs", return_value=SimpleNamespace(f_flag=0)), \
             mock.patch.object(d, "_current") as current:
            observed = d.observe(R1, R2, CAND)
        self.assertEqual(observed["state"], "NO_VERIFICADO")
        current.assert_not_called()

    def test_sanitize_good_but_never_claim_recovery(self):
        result = d.sanitize(json.dumps(full_receipt()), R1, R2, CAND)
        self.assertEqual(result["state"], "DIAGNOSTIC_ONLY")
        self.assertFalse(result["current"]["successor_recovery_proven"])

    def test_sanitize_private_keys_in_all_layers_rejected(self):
        for path in ("root", "marker", "current"):
            obj = full_receipt()
            target = obj if path == "root" else obj[path]
            target["secret_token"] = "PRIVATEKEY123"
            with self.subTest(path=path):
                result = d.sanitize(json.dumps(obj), R1, R2, CAND)
                self.assertEqual(result["state"], "NO_VERIFICADO")
                self.assertNotIn("PRIVATEKEY123", json.dumps(result))

    def test_sanitize_private_string_in_all_allowlisted_enums_rejected(self):
        for field in ("source_state", "computed_report_state"):
            obj = full_receipt()
            obj["current"][field] = "PRIVATEKEY123"
            self.assertEqual(d.sanitize(json.dumps(obj), R1, R2, CAND)["state"],
                             "NO_VERIFICADO")

    def test_sanitize_invented_full_recovery_true_rejected(self):
        obj = full_receipt()
        obj["current"]["successor_recovery_proven"] = True
        self.assertEqual(d.sanitize(json.dumps(obj), R1, R2, CAND)["state"],
                         "NO_VERIFICADO")

    def test_sanitize_invented_computed_proof_true_rejected(self):
        obj = full_receipt()
        obj["current"]["computed_report_hash_verified"] = True
        self.assertEqual(d.sanitize(json.dumps(obj), R1, R2, CAND)["state"],
                         "NO_VERIFICADO")

    def test_sanitize_nonzero_mutations_rejected(self):
        obj = full_receipt()
        obj["mutations"] = 1
        self.assertEqual(d.sanitize(json.dumps(obj), R1, R2, CAND)["state"],
                         "NO_VERIFICADO")

    def test_sanitize_unknown_historical_value_rejected(self):
        obj = full_receipt()
        obj["legacy_cold2"] = "PRIVATE_TOKEN"
        self.assertEqual(d.sanitize(json.dumps(obj), R1, R2, CAND)["state"],
                         "NO_VERIFICADO")

    def test_sanitize_oversize_fails_closed(self):
        self.assertEqual(d.sanitize("X" * 4097, R1, R2, CAND)["state"],
                         "NO_VERIFICADO")


if __name__ == "__main__":
    unittest.main()
