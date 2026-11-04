"""Offline adversarial contracts for A665 private queue diagnostic; no host access."""
import hashlib
import gzip
import importlib.util
import json
from datetime import datetime, timedelta
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "scripts/rc6_a665_cold_queue_readonly.py"
WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/rc6-a665-cold-queue-readonly.yml"
spec = importlib.util.spec_from_file_location("rc6_a665_cold_queue_readonly", SOURCE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
PRODUCT = "7e1e744d6ca9ab30c51b5747dd4467af5ece0145"
IDENT = "a" * 32
MANIFEST = "b" * 64


def envelope(value):
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"),
                           allow_nan=False, default=str).encode()
    return json.dumps({"payload": value, "sha256": hashlib.sha256(canonical).hexdigest()})


class ColdQueueReadOnly(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        self.data.mkdir()
        self.cold = self.data.joinpath(*probe.COLD_PARTS)
        self.cold.mkdir(parents=True, mode=0o700)
        self.cold.chmod(0o700)
        provenance = self.data / "deploy"
        provenance.mkdir()
        self.ledger = provenance / "CURRENT_STATE_V2.json"
        self.ledger.write_text(json.dumps({"deploy_sha": PRODUCT,
                                          "runtime_password": "SHOULD_NOT_ESCAPE"}))

    def write_private(self, name, value):
        path = self.cold / name
        path.write_text(envelope(value))
        path.chmod(0o600)
        return path

    def read(self):
        return probe.inspect(self.data, readonly=False)

    def assert_no_counts(self, result):
        self.assertEqual(result["state"], "NO_VERIFICADO")
        self.assertIsNone(result["receipt_count"])
        self.assertIsNone(result["failed_receipt_count"])
        self.assertEqual(result["queue_state"], "NO_VERIFICADO")
        self.assertNotIn("SHOULD_NOT_ESCAPE", json.dumps(result))

    def test_zero_is_only_reported_from_existing_verified_empty_folder(self):
        record = self.read()
        self.assertEqual(record["state"], "READ_ONLY_OBSERVED")
        self.assertEqual(record["reason"], "COUNTS_AND_CAUSE_ONLY_NO_REPAIR")
        self.assertEqual(record["receipt_count"], 0)
        self.assertEqual(record["failed_receipt_count"], 0)
        self.assertEqual(record["queue_state"], "ABSENT")
        self.assertEqual(record["observed_deploy_sha"], PRODUCT)
        self.assertFalse(record["paper_admission_verified"])
        self.assertFalse(record["causal_fix_verified"])

    def test_receipt_clock_is_utc_and_art_without_disclosing_private_root(self):
        record = self.read()
        utc = datetime.fromisoformat(record["generated_at_utc"])
        art = datetime.fromisoformat(record["generated_at_art"])
        self.assertEqual(utc.utcoffset(), timedelta(0))
        self.assertEqual(art.utcoffset(), timedelta(hours=-3))
        self.assertLessEqual(abs((utc - art).total_seconds()), 1)
        self.assertNotIn(str(self.data), json.dumps(record))

    def test_one_failed_receipt_and_bound_queue_are_structurally_observed(self):
        self.write_private("receipt-" + IDENT + ".json", {
            "state": "FAILED", "snapshot_id": IDENT,
            "manifest_sha256": MANIFEST, "sensitive_values": "secret-ticker-account"})
        self.write_private("queue.json", {
            "state": "FAILED", "snapshot_id": IDENT, "manifest_sha256": MANIFEST})
        record = self.read()
        self.assertEqual(record["state"], "READ_ONLY_OBSERVED")
        self.assertEqual(record["receipt_count"], 1)
        self.assertEqual(record["failed_receipt_count"], 1)
        self.assertEqual(record["queue_state"], "FAILED")
        self.assertEqual(record["queue_failed_binding"], "MATCHES_FAILED_RECEIPT")
        self.assertEqual(record["folder_entries_checked"], 2)
        self.assertTrue(record["envelopes_hash_verified"])
        self.assertTrue(record["metadata_snapshot_stable"])
        self.assertNotIn(IDENT, json.dumps(record))
        self.assertNotIn(MANIFEST, json.dumps(record))
        self.assertNotIn("secret-ticker-account", json.dumps(record))

    def write_failure_with_current(self, *, code="SHADOW_EVIDENCE_CAPACITY_REACHED",
                                   phase="REPORT_WRITE", current_ident=IDENT):
        pack = self.cold / (IDENT + ".pack")
        pack.write_bytes(b"safe-synthetic-original-commit")
        pack.chmod(0o600)
        info = pack.stat()
        manifest = {"epoch": "rc6.audit-actual-values.v1",
                    "snapshot_id": IDENT, "pack": pack.name,
                    "pack_bytes": info.st_size,
                    "pack_identity": [info.st_dev, info.st_ino, info.st_size,
                                      info.st_mtime_ns, info.st_ctime_ns]}
        sha = hashlib.sha256(json.dumps(manifest, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        self.write_private("manifest-" + IDENT + ".json", manifest)
        self.write_private("receipt-" + IDENT + ".json", {
            "state": "FAILED", "snapshot_id": IDENT, "manifest_sha256": sha,
            "error_class": "ValueError", "error_code": code, "error_phase": phase,
            "sensitive_login": "PRIVATE-ACCOUNT-TICKER"})
        self.write_private("queue.json", {
            "state": "FAILED", "snapshot_id": IDENT, "manifest_sha256": sha})
        generation = "c" * 64
        pointer = {"generation_id": generation,
                   "file": "cut-" + generation + ".json.gz"}
        shadow = self.cold.parent
        pointer_bytes = json.dumps({"payload": pointer, "digest": hashlib.sha256(
            json.dumps(pointer, sort_keys=True, separators=(",", ":")).encode()).hexdigest()})
        (shadow / "CURRENT.json").write_text(pointer_bytes)
        (shadow / "CURRENT.json").chmod(0o600)
        cut = {"report": {"computed_report": {
            "schema": "rc6.computed-audit-reference.v2", "state": "PENDING",
            "snapshot_id": current_ident, "manifest_sha256": sha}}}
        wrapper = json.dumps({"payload": cut, "digest": hashlib.sha256(
            json.dumps(cut, sort_keys=True, separators=(",", ":")).encode()).hexdigest()})
        (shadow / pointer["file"]).write_bytes(gzip.compress(wrapper.encode()))
        (shadow / pointer["file"]).chmod(0o600)
        return pack

    def test_runtime_failure_category_and_current_failed_cut_are_bounded(self):
        self.write_failure_with_current()
        record = self.read()
        self.assertEqual(record["state"], "READ_ONLY_OBSERVED")
        self.assertEqual(record["failed_error_code"], "SHADOW_EVIDENCE_CAPACITY_REACHED")
        self.assertEqual(record["failed_error_phase"], "REPORT_WRITE")
        self.assertEqual(record["failed_error_class"], "ValueError")
        self.assertEqual(record["failed_manifest_relation"],
                         "MANIFEST_AND_PACK_METADATA_BOUND_NOT_HASHED")
        self.assertEqual(record["current_failed_relation"],
                         "CURRENT_FAILED_PENDING_REFERENCE_BOUND")
        self.assertNotIn(IDENT, json.dumps(record))
        self.assertNotIn("PRIVATE-ACCOUNT-TICKER", json.dumps(record))

    def test_current_pointer_to_other_cut_is_distinguished_not_rehabilitated(self):
        self.write_failure_with_current(current_ident="d" * 32)
        record = self.read()
        self.assertEqual(record["failed_receipt_count"], 1)
        self.assertEqual(record["current_failed_relation"],
                         "CURRENT_OTHER_OR_NO_FAILED_REFERENCE")
        self.assertFalse(record["causal_fix_verified"])

    def test_invalid_failure_labels_are_redacted_and_keep_backlog_visible(self):
        self.write_failure_with_current(code="SHADOW_PRIVATE_TICKER_ACCOUNT",
                                        phase="/private/source.py")
        record = self.read()
        self.assertEqual(record["failed_receipt_count"], 1)
        self.assertEqual(record["failed_error_code"], "OTHER_REDACTED")
        self.assertEqual(record["failed_error_phase"], "OTHER_REDACTED")
        self.assertNotIn("PRIVATE_TICKER", json.dumps(record))

    def test_modified_pack_and_current_digest_downgrade_only_their_evidence(self):
        pack = self.write_failure_with_current()
        pack.write_bytes(b"modified-pack")
        record = self.read()
        self.assertEqual(record["failed_manifest_relation"], "PACK_METADATA_MISMATCH")
        self.assertEqual(record["failed_receipt_count"], 1)
        current = self.cold.parent / "CURRENT.json"
        current.write_text(current.read_text().replace("digest", "danger", 1))
        record = self.read()
        self.assertEqual(record["current_failed_relation"], "NO_VERIFICADO")
        self.assertEqual(record["failed_receipt_count"], 1)

    def test_missing_manifest_never_erases_failed_receipt_or_exposes_private_id(self):
        self.write_failure_with_current()
        (self.cold / ("manifest-" + IDENT + ".json")).unlink()
        record = self.read()
        self.assertEqual(record["failed_receipt_count"], 1)
        self.assertEqual(record["failed_manifest_relation"], "NO_VERIFICADO")
        self.assertEqual(record["current_failed_relation"],
                         "CURRENT_FAILED_PENDING_REFERENCE_BOUND")
        self.assertNotIn(IDENT, json.dumps(record))

    def test_gzip_bomb_is_bounded_and_does_not_hide_failed_queue(self):
        self.write_failure_with_current()
        cut = self.cold.parent / ("cut-" + "c" * 64 + ".json.gz")
        cut.write_bytes(gzip.compress(b"Z" * (probe.MAX_DECOMPRESSED_CUT_BYTES + 1)))
        record = self.read()
        self.assertEqual(record["failed_receipt_count"], 1)
        self.assertEqual(record["current_failed_relation"],
                         "CURRENT_CUT_SIZE_UNVERIFIED")

    def test_current_symlink_is_never_followed_and_not_reported_as_verified(self):
        self.write_failure_with_current()
        current = self.cold.parent / "CURRENT.json"
        current.unlink()
        current.symlink_to(self.ledger)
        record = self.read()
        self.assertEqual(record["failed_receipt_count"], 1)
        self.assertEqual(record["current_failed_relation"], "NO_VERIFICADO")
        self.assertNotIn("SHOULD_NOT_ESCAPE", json.dumps(record))

    def test_failed_receipt_not_bound_to_queue_is_not_hidden(self):
        self.write_private("receipt-" + IDENT + ".json", {
            "state": "FAILED", "snapshot_id": IDENT, "manifest_sha256": MANIFEST})
        self.write_private("queue.json", {
            "state": "COMPLETE", "snapshot_id": IDENT, "manifest_sha256": MANIFEST})
        record = self.read()
        self.assertEqual(record["state"], "READ_ONLY_OBSERVED")
        self.assertEqual(record["failed_receipt_count"], 1)
        self.assertEqual(record["queue_failed_binding"], "FAILED_RECEIPT_QUEUE_NOT_BOUND")

    def test_multiple_failed_receipts_preserve_guard_signal(self):
        for ident in ("a" * 32, "c" * 32):
            self.write_private("receipt-" + ident + ".json", {
                "state": "FAILED", "snapshot_id": ident, "manifest_sha256": MANIFEST})
        record = self.read()
        self.assertEqual(record["failed_receipt_count"], 2)
        self.assertEqual(record["queue_failed_binding"], "MULTIPLE_FAILED_RECEIPTS")

    def test_failed_queue_without_failed_receipt_is_inconsistency_not_automatic_go(self):
        self.write_private("queue.json", {"state": "FAILED",
                                          "snapshot_id": IDENT, "manifest_sha256": MANIFEST})
        record = self.read()
        self.assertEqual(record["failed_receipt_count"], 0)
        self.assertEqual(record["queue_failed_binding"], "FAILED_QUEUE_WITHOUT_FAILED_RECEIPT")
        self.assertFalse(record["causal_fix_verified"])

    def test_missing_private_folder_never_masquerades_as_zero(self):
        other = self.data / "other"
        other.mkdir()
        self.assert_no_counts(probe.inspect(other, readonly=False))

    def test_invalid_sha_or_payload_type_never_exposes_any_count(self):
        path = self.write_private("receipt-" + IDENT + ".json", {
            "state": "FAILED", "snapshot_id": IDENT, "manifest_sha256": MANIFEST})
        path.write_text(path.read_text().replace('"sha256": "', '"sha256": "f', 1))
        self.assert_no_counts(self.read())
        path.write_text(json.dumps({"payload": "FAILED", "sha256": "0" * 64}))
        self.assert_no_counts(self.read())

    def test_filename_receipt_binding_prevents_forged_failed_signal(self):
        self.write_private("receipt-" + IDENT + ".json", {
            "state": "FAILED", "snapshot_id": "c" * 32,
            "manifest_sha256": MANIFEST})
        self.assert_no_counts(self.read())

    def test_unrecognized_receipt_or_queue_state_is_no_verificado(self):
        receipt = self.write_private("receipt-" + IDENT + ".json", {
            "state": "UNTRUSTED", "snapshot_id": IDENT, "manifest_sha256": MANIFEST})
        self.assert_no_counts(self.read())
        receipt.write_text(envelope({"state": "COMPLETE"}))
        self.write_private("queue.json", {"state": "UNTRUSTED"})
        self.assert_no_counts(self.read())

    def test_private_symlink_and_oversized_file_fail_closed(self):
        other = self.data / "deploy" / "CURRENT_STATE_V2.json"
        (self.cold / "queue.json").symlink_to(other)
        self.assert_no_counts(self.read())
        (self.cold / "queue.json").unlink()
        path = self.cold / "queue.json"
        path.write_bytes(b"x" * (probe.MAX_FILE_BYTES + 1))
        path.chmod(0o600)
        self.assert_no_counts(self.read())

    def test_private_directory_and_receipt_must_retain_mode_700_600(self):
        receipt = self.write_private("receipt-" + IDENT + ".json", {"state": "COMPLETE"})
        self.cold.chmod(0o755)
        self.assert_no_counts(self.read())
        self.cold.chmod(0o700)
        receipt.chmod(0o644)
        self.assert_no_counts(self.read())

    def test_missing_invalid_provenance_never_reports_positive_counts(self):
        self.ledger.write_text(json.dumps({"deploy_sha": "malformed-private"}))
        self.assert_no_counts(self.read())

    def test_unbounded_directory_fails_before_reading_names(self):
        for i in range(probe.MAX_ENTRIES + 1):
            (self.cold / (f"{i:032x}.pack")).touch()
        self.assert_no_counts(self.read())

    def test_normal_reader_never_opens_files_for_writes_or_mutates_payload(self):
        path = self.write_private("receipt-" + IDENT + ".json", {"state": "COMPLETE"})
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        real_open = os.open
        flags = []
        def tracked(*args, **kwargs):
            flags.append(args[1])
            return real_open(*args, **kwargs)
        with patch.object(probe.os, "open", side_effect=tracked):
            record = self.read()
        self.assertEqual(record["state"], "READ_ONLY_OBSERVED")
        self.assertTrue(flags)
        for mode in flags:
            self.assertEqual(mode & (os.O_RDWR | os.O_WRONLY | os.O_CREAT | os.O_TRUNC), 0)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_ro_mount_mandatory_on_host_but_offhost_fixture_stays_writable(self):
        self.assert_no_counts(probe.inspect(self.data, readonly=True))

    def test_workflow_cannot_touch_host_on_pr_and_has_no_cron(self):
        content = WORKFLOW.read_text()
        self.assertIn("workflow_dispatch:", content)
        self.assertIn("pull_request:", content)
        self.assertNotIn("\n  schedule:", content)
        self.assertIn("github.ref == 'refs/heads/main' && github.event_name == 'workflow_dispatch'", content)
        self.assertIn("group: rc6-unified-paper-deploy", content)
        self.assertIn("mount -o remount,bind,ro /opt/porota-trading/data", content)
        self.assertIn("exec python3 -B -", content)
        self.assertIn("EXPECTED_DEPLOY_SHA", content)
        self.assertIn("failed_receipt_count", content)
        self.assertIn("if: always()", content)
        self.assertNotIn("ensure_idle(", content)
        self.assertNotIn("resolve(", content)
        self.assertNotIn("docker exec", content)
        self.assertNotIn("rm -rf", content)

    def test_runner_sanitizer_cross_checks_count_and_queue_binding(self):
        content = WORKFLOW.read_text()
        self.assertIn("if not (safe['failed_receipt_count']<=safe['receipt_count']", content)
        self.assertIn("valid_binding=(", content)
        self.assertIn("COUNT_BINDING_CONTRADICTS_QUEUE", content)
        self.assertIn("A667_RUNTIME_COLD_CAUSE_NO_VERIFICADO", content)
        self.assertIn("safe=fallback", content)

    def test_runner_has_exact_static_error_labels_and_never_exposes_raw_receipt(self):
        content = WORKFLOW.read_text()
        self.assertIn("categories.FAILURE_CODES", content)
        self.assertIn("categories.FAILURE_PHASES", content)
        self.assertIn("categories.FAILURE_CLASSES", content)
        self.assertIn("CURRENT_RELATION_INVALID", content)
        self.assertIn("UNBOUND_FAILED_DETAIL_EXPORTED", content)
        self.assertIn("HOST_RESPONSE_INVALID_OR_UNAVAILABLE", content)
        self.assertNotIn("print(obj)", content)
        self.assertNotIn("print(raw)", content)


if __name__ == "__main__":
    unittest.main()
