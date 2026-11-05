"""Offline adversarial contracts for A665 private queue diagnostic; no host access."""
import ast
import hashlib
import gzip
import importlib.util
import json
from datetime import datetime, timedelta
import os
from pathlib import Path
import re
import stat
import tempfile
import textwrap
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
                                   phase="REPORT_WRITE", current_ident=None, ident=IDENT):
        if current_ident is None:
            current_ident = ident
        pack = self.cold / (ident + ".pack")
        pack.write_bytes(b"safe-synthetic-original-commit")
        pack.chmod(0o600)
        info = pack.stat()
        manifest = {"epoch": "rc6.audit-actual-values.v1",
                    "snapshot_id": ident, "pack": pack.name,
                    "pack_bytes": info.st_size,
                    "pack_identity": [info.st_dev, info.st_ino, info.st_size,
                                      info.st_mtime_ns, info.st_ctime_ns]}
        sha = hashlib.sha256(json.dumps(manifest, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        self.write_private("manifest-" + ident + ".json", manifest)
        self.write_private("receipt-" + ident + ".json", {
            "state": "FAILED", "snapshot_id": ident, "manifest_sha256": sha,
            "error_class": "ValueError", "error_code": code, "error_phase": phase,
            "sensitive_login": "PRIVATE-ACCOUNT-TICKER"})
        self.write_private("queue.json", {
            "state": "FAILED", "snapshot_id": ident, "manifest_sha256": sha})
        generation = "c" * 64
        cut_manifest = {"generation_id": generation, "sequence": 1}
        pointer = {"generation_id": generation, "sequence": 1,
                   "manifest_sha256": hashlib.sha256(json.dumps(cut_manifest,
                       sort_keys=True,separators=(",", ":")).encode()).hexdigest(),
                   "file": "cut-" + generation + ".json.gz"}
        shadow = self.cold.parent
        pointer_bytes = json.dumps({"payload": pointer, "digest": hashlib.sha256(
            json.dumps(pointer, sort_keys=True, separators=(",", ":")).encode()).hexdigest()})
        (shadow / "CURRENT.json").write_text(pointer_bytes)
        (shadow / "CURRENT.json").chmod(0o600)
        cut = {"manifest": cut_manifest, "report": {"computed_report": {
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
        self.assertTrue(record["unrecognized_failed_id_bound"])
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
        self.assertFalse(record["unrecognized_failed_id_bound"])
        current = self.cold.parent / "CURRENT.json"
        current.write_text(current.read_text().replace("digest", "danger", 1))
        record = self.read()
        self.assertEqual(record["current_failed_relation"], "NO_VERIFICADO")
        self.assertIsNone(record["unrecognized_failed_id_bound"])
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

    def test_v2_later_successor_is_not_mistaken_for_first_cold2(self):
        self.write_failure_with_current(ident=probe.KNOWN_SUCCESSOR_ID)
        record = self.read()
        self.assertEqual(record["current_snapshot_class"], "KNOWN_SUCCESSOR")
        self.assertEqual(record["current_generation_class"], "OTHER")
        self.assertFalse(record["known_successor_manifest_exact"])
        self.assertEqual(record["original_old_cut_presence"], "ABSENT")
        self.assertNotIn(probe.KNOWN_SUCCESSOR_ID, json.dumps(record))

    def test_v2_first_failed_cold2_is_distinct_historical_generation(self):
        self.write_failure_with_current(ident=probe.HISTORICAL_COLD2_ID)
        record = self.read()
        self.assertEqual(record["current_snapshot_class"], "HISTORICAL_COLD2")
        self.assertIsNone(record["known_successor_manifest_exact"])
        self.assertNotIn(probe.HISTORICAL_COLD2_ID, json.dumps(record))

    def test_v2_original_failed_id_is_not_a_later_successor(self):
        self.write_failure_with_current(ident=probe.ORIGINAL_ID)
        record = self.read()
        self.assertEqual(record["current_snapshot_class"], "ORIGINAL_FAILED")
        self.assertNotIn(probe.ORIGINAL_ID, json.dumps(record))

    def test_v2_original_cut_dangling_alias_cannot_masquerade_as_absent(self):
        self.write_failure_with_current(ident=probe.KNOWN_SUCCESSOR_ID)
        (self.cold.parent / ("cut-" + probe.ORIGINAL_CUT + ".json.gz")).symlink_to(
            self.data / "nonexistent-private")
        record = self.read()
        self.assertEqual(record["original_old_cut_presence"], "UNSAFE_FILE")
        self.assertFalse(record["causal_fix_verified"])

    def test_v2_current_manifest_must_bind_generation_and_sequence(self):
        self.write_failure_with_current(ident=probe.KNOWN_SUCCESSOR_ID)
        pointer_path = self.cold.parent / "CURRENT.json"
        wrapper = json.loads(pointer_path.read_text())
        pointer = wrapper["payload"]
        self.assertEqual(self.read()["current_cut_manifest_binding"], "BOUND")
        pointer["sequence"] = 2
        canonical = json.dumps(pointer,sort_keys=True,separators=(",", ":")).encode()
        pointer_path.write_text(json.dumps({"payload":pointer,
            "digest":hashlib.sha256(canonical).hexdigest()}))
        self.assertEqual(self.read()["current_cut_manifest_binding"], "UNBOUND")
        pointer["sequence"] = 1
        cut_path = self.cold.parent / pointer["file"]
        cut_wrapper = json.loads(gzip.decompress(cut_path.read_bytes()))
        cut_payload = cut_wrapper["payload"]
        cut_payload["manifest"]["generation_id"] = "f" * 64
        pointer["manifest_sha256"] = hashlib.sha256(json.dumps(
            cut_payload["manifest"],sort_keys=True,separators=(",", ":")).encode()).hexdigest()
        cut_canonical = json.dumps(cut_payload,sort_keys=True,
                                   separators=(",", ":")).encode()
        cut_path.write_bytes(gzip.compress(json.dumps({"payload":cut_payload,
            "digest":hashlib.sha256(cut_canonical).hexdigest()}).encode()))
        pointer_canonical = json.dumps(pointer,sort_keys=True,
                                       separators=(",", ":")).encode()
        pointer_path.write_text(json.dumps({"payload":pointer,
            "digest":hashlib.sha256(pointer_canonical).hexdigest()}))
        self.assertEqual(self.read()["current_cut_manifest_binding"], "UNBOUND")

    def test_v2_live_status_differentiates_386_without_proving_stop(self):
        self.write_failure_with_current(ident=probe.KNOWN_SUCCESSOR_ID)
        status = {"status": "FAIL_CLOSED", "real_orders_sent": 0,
                  "real_routes": "NOT_CALLED", "error_class": "ValueError",
                  "error_module": "cold_audit", "error_line": 386,
                  "secret_account": "CANNOT_EXPORT_PRIVATE_SECRET"}
        canonical = json.dumps(status,sort_keys=True,separators=(",", ":")).encode()
        wrapper = {"payload": status,
                   "digest": hashlib.sha256(canonical).hexdigest()}
        path = self.cold.parent / "status.json"
        path.write_text(json.dumps(wrapper))
        path.chmod(0o600)
        record = self.read()
        self.assertEqual(record["live_status_class"], "FAIL_CLOSED")
        self.assertEqual(record["live_cancel_site_class"],
                         "SEAL_386_NOT_WHITELISTED")
        self.assertEqual(record["live_error_origin_class"],
                         "CANCEL_SITE_265_360_386")
        self.assertTrue(record["live_paper_zero_routes_blocked"])
        self.assertFalse(record["quiescence_verified"])
        self.assertNotIn("CANNOT_EXPORT_PRIVATE_SECRET", json.dumps(record))
        status["real_routes"] = "CALLED"
        canonical = json.dumps(status,sort_keys=True,separators=(",", ":")).encode()
        wrapper = {"payload": status,
                   "digest": hashlib.sha256(canonical).hexdigest()}
        path.write_text(json.dumps(wrapper))
        self.assertFalse(self.read()["live_paper_zero_routes_blocked"])

    def test_v3_exact_memory_site_is_not_mislabelled_as_clean_stop(self):
        self.write_failure_with_current(code="SHADOW_COLD_HOST_MEMORY_RESERVE_BREACHED",
                                        phase="REPORT_VERIFY")
        status = {"status":"FAIL_CLOSED","real_orders_sent":0,
                  "real_routes":"NOT_CALLED","error_class":"ValueError",
                  "error_module":"cold_audit","error_line":55,
                  "private_account":"never-export"}
        canonical = json.dumps(status,sort_keys=True,
                               separators=(",", ":")).encode()
        path = self.cold.parent / "status.json"
        path.write_text(json.dumps({"payload":status,"digest":
            hashlib.sha256(canonical).hexdigest()}))
        path.chmod(0o600)
        result = self.read()
        self.assertEqual(result["live_status_class"],"FAIL_CLOSED")
        self.assertEqual(result["live_cancel_site_class"],"OTHER")
        self.assertEqual(result["live_error_origin_class"],"MEMORY_RESERVE_GUARD_55")
        self.assertFalse(result["quiescence_verified"])
        self.assertFalse(result["causal_fix_verified"])
        self.assertTrue(result["unrecognized_failed_id_bound"])
        self.assertNotIn("never-export",json.dumps(result))

    def test_v3_backlog_and_cancel_sites_remain_distinct(self):
        self.write_failure_with_current()
        status = {"status":"FAIL_CLOSED","real_orders_sent":0,
                  "real_routes":"NOT_CALLED","error_class":"ValueError",
                  "error_module":"cold_audit","error_line":595}
        path = self.cold.parent / "status.json"
        for line,cause,site in ((595,"FAILED_BACKLOG_GUARD","OTHER"),
                                (628,"FAILED_BACKLOG_GUARD","OTHER"),
                                (665,"FAILED_BACKLOG_GUARD","OTHER"),
                                (265,"CANCEL_SITE_265_360_386","FRAME_265"),
                                (360,"CANCEL_SITE_265_360_386","STAGE_360"),
                                (386,"CANCEL_SITE_265_360_386",
                                 "SEAL_386_NOT_WHITELISTED"),
                                (123,"OTHER_COLD_AUDIT_LINE","OTHER")):
            with self.subTest(error_line=line):
                status["error_line"] = line
                canonical = json.dumps(status,sort_keys=True,
                                       separators=(",", ":")).encode()
                path.write_text(json.dumps({"payload":status,"digest":
                    hashlib.sha256(canonical).hexdigest()}))
                path.chmod(0o600)
                result = self.read()
                self.assertEqual(result["live_error_origin_class"],cause)
                self.assertEqual(result["live_cancel_site_class"],site)
                self.assertFalse(result["quiescence_verified"])

    def _synthetic_archive(self, *, historical):
        ident = probe.HISTORICAL_COLD2_ID if historical else probe.ORIGINAL_ID
        archive = self.data / "deploy" / ("rc6-failed-cold-" + ident)
        archive.mkdir(mode=0o700)
        pack = archive / (ident + ".pack")
        pack.write_bytes(b"synthetic sealed pack only")
        pack.chmod(0o600)
        info = pack.stat()
        manifest = {"snapshot_id": ident, "pack": pack.name,
                    "pack_bytes": info.st_size,
                    "pack_sha256": hashlib.sha256(pack.read_bytes()).hexdigest(),
                    "pack_identity": [info.st_dev,info.st_ino,info.st_size,
                                      info.st_mtime_ns,info.st_ctime_ns]}
        manifest_sha = hashlib.sha256(json.dumps(manifest,
            sort_keys=True,separators=(",", ":")).encode()).hexdigest()
        for name, payload in (
            ("manifest-"+ident+".json", manifest),
            ("receipt-"+ident+".json", {"snapshot_id":ident,"state":"FAILED",
                                            "manifest_sha256":manifest_sha}),
            ("queue.json", {"snapshot_id":ident,"state":"FAILED",
                            "manifest_sha256":manifest_sha})):
            path = archive / name
            path.write_text(envelope(payload))
            path.chmod(0o600)
        if historical:
            quarantine = {"state":"QUARANTINED_FAILED","snapshot_id":ident,
                          "manifest_sha256":manifest_sha,"old_cut_revoked":True,
                          "revoked_cut":probe.HISTORICAL_COLD2_CUT,
                          "pack_sha256":manifest.get("pack_sha256"),
                          "real_orders_sent":0}
            canonical = json.dumps(quarantine,sort_keys=True,
                                   separators=(",", ":")).encode()
            path = archive / "quarantine.json"
            path.write_text(json.dumps({"payload":quarantine,
                "digest":hashlib.sha256(canonical).hexdigest()}))
            path.chmod(0o600)
        return archive,manifest_sha

    def test_v2_original_archive_small_receipts_metadata_without_pack_hash(self):
        archive,manifest_sha = self._synthetic_archive(historical=False)
        with patch.object(probe,"ORIGINAL_MANIFEST",manifest_sha):
            record = self.read()
        self.assertEqual(record["original_archive_class"],
                         "METADATA_BOUND_PACK_NOT_HASHED")
        self.assertFalse(record["pack_content_hash_verified"])
        self.assertNotIn(str(archive),json.dumps(record))
        pack = archive / (probe.ORIGINAL_ID + ".pack")
        stat_before = pack.stat()
        pack.write_bytes(b"X" * stat_before.st_size)
        os.utime(pack,ns=(stat_before.st_mtime_ns + 2_000_000_000,
                          stat_before.st_mtime_ns + 2_000_000_000))
        with patch.object(probe,"ORIGINAL_MANIFEST",manifest_sha):
            self.assertEqual(self.read()["original_archive_class"],
                             "PACK_METADATA_UNVERIFIED")

    def test_v2_historical_archive_distinct_and_missing_quarantine_fails_closed(self):
        archive,manifest_sha = self._synthetic_archive(historical=True)
        with patch.object(probe,"HISTORICAL_COLD2_MANIFEST",manifest_sha):
            record = self.read()
            self.assertEqual(record["historical_cold2_archive_class"],
                             "METADATA_BOUND_PACK_NOT_HASHED")
            (archive / "quarantine.json").unlink()
            record = self.read()
        self.assertEqual(record["historical_cold2_archive_class"],
                         "INCOMPLETE_OR_EXTRA")

    def test_v3_historical_cold2_pack_reason_is_categorical_and_bounded(self):
        archive,manifest_sha = self._synthetic_archive(historical=True)
        pack = archive / (probe.HISTORICAL_COLD2_ID + ".pack")
        with patch.object(probe,"HISTORICAL_COLD2_MANIFEST",manifest_sha):
            record = self.read()
            self.assertEqual(record["historical_cold2_archive_class"],
                             "METADATA_BOUND_PACK_NOT_HASHED")
            self.assertEqual(record["historical_cold2_pack_metadata_cause"],"NONE")
            pack.chmod(0o640)
            record = self.read()
            self.assertEqual(record["historical_cold2_archive_class"],
                             "PACK_METADATA_UNVERIFIED")
            self.assertEqual(record["historical_cold2_pack_metadata_cause"],
                             "PACK_TYPE_LINK_MODE")
            pack.chmod(0o600)
            old_mtime = pack.stat().st_mtime_ns
            os.utime(pack,ns=(old_mtime+1_000_000_000,
                              old_mtime+1_000_000_000))
            record = self.read()
            self.assertEqual(record["historical_cold2_pack_metadata_cause"],
                             "PACK_MTIME")
            self.assertFalse(record["pack_content_hash_verified"])
            self.assertNotIn(str(archive),json.dumps(record))

    def test_v2_archive_proof_never_reads_pack_contents(self):
        _,manifest_sha = self._synthetic_archive(historical=False)
        actual_open,actual_read = os.open,os.read
        pack_fds = set()
        def tracked_open(path,flags,*args,**kwargs):
            fd = actual_open(path,flags,*args,**kwargs)
            if str(path).endswith(".pack"):
                pack_fds.add(fd)
            return fd
        def tracked_read(fd,count):
            self.assertNotIn(fd,pack_fds)
            return actual_read(fd,count)
        with patch.object(probe,"ORIGINAL_MANIFEST",manifest_sha), \
             patch.object(probe.os,"open",side_effect=tracked_open), \
             patch.object(probe.os,"read",side_effect=tracked_read):
            record = self.read()
        self.assertEqual(record["original_archive_class"],
                         "METADATA_BOUND_PACK_NOT_HASHED")

    def test_v2_workflow_schema_sanitizes_only_fixed_categories(self):
        content = WORKFLOW.read_text()
        self.assertIn("rc6.a667.cold-failure-cause-readonly.v3",content)
        self.assertIn("CURRENT_CATEGORY_WITHOUT_FAILED_BINDING",content)
        self.assertIn("SUCCESSOR_METADATA_WITHOUT_IDENTITY",content)
        self.assertIn("UNKNOWN_FAILED_ID_NOT_BOUND",content)
        self.assertIn("HISTORICAL_PACK_CAUSE_CONTRADICTION",content)
        self.assertIn("LIVE_SITE_CAUSE_CONTRADICTION",content)
        self.assertIn("LIVE_STATUS_UNBOUND",content)
        self.assertIn("raw.stat().st_size>4096",content)
        self.assertNotIn("pack_content_hash_verified':True",content)

    def test_v2_all_inline_workflow_python_heredocs_compile_offhost(self):
        content = WORKFLOW.read_text()
        scripts = re.findall(r"(?ms)^          [^\n]*python3 - <<'PY'\n(.*?)^          PY$", content)
        self.assertGreaterEqual(len(scripts), 2)
        for number, source in enumerate(scripts, 1):
            with self.subTest(inline_python_number=number):
                ast.parse(textwrap.dedent(source), filename=f"workflow-inline-{number}")


if __name__ == "__main__":
    unittest.main()
