"""Offline fail-closed tests for A724 physical diagnosis. Never contacts host."""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase, mock

from scripts import rc6_a724_postfailed_physical_ro as d


R1 = "eefcf6d8c93f3eddfc23bd471d9d5b41d9a31a9f"
R2 = "7ac6e368bd42137817e3271839c35f579bdbbbbe"
CAND = "a16a31c7bc4634362870c731273e084eea9d357d"
IMG = "sha256:ce58f45790de6062da68c86aeb285ab8b45335e7affdfd0d82e635fa231c9462"
RUN = "42260034260"
CURRENT_ID = "3bfbfd6996a849f6b395f6c4a27ff15a"
GEN = "e" * 64
HASH = "f" * 64


def full_receipt():
    now = datetime.now(timezone.utc)
    return {
        "schema": d.SCHEMA, "state": "DIAGNOSTIC_ONLY",
        "read_only": True, "mount_ro_verified": True,
        "mutations": 0, "provider_gets": 0, "orders_called": False,
        "ppi_watch_touched": False,
        "generated_at_utc": now.isoformat(),
        "generated_at_art": now.astimezone(timezone(timedelta(hours=-3))).isoformat(),
        "expected_previous_deploy_sha": R1,
        "failed_deploy_sha": R2, "failed_candidate_sha": CAND,
        "failed_expected_image_id": IMG, "failed_run_id": RUN,
        "marker": {"state": "NO_VERIFICADO", "relation": "UNKNOWN",
                   "deploy_sha": None, "candidate_sha": None,
                   "image_id": None, "validation_status": None,
                   "paper_zero": None, "real_routes_not_called": None},
        "containers": [{"name": n, "state": "NO_VERIFICADO", "running": None,
                        "health": "NO_VERIFICADO", "image_id": None,
                        "image_matches_marker": None, "image_matches_r2_input": None}
                       for n in d.OWNED],
        "barrier": {"state": "ABSENT", "relation": "ABSENT",
                    "phase": None, "status": "ABSENT", "flock_idle": None},
        "barrier_snapshot_stable": True,
        "quiescence": {"state": "NO_VERIFICADO", "candidate_matches": None,
                       "status": None, "installation_started": None,
                       "inflight": None, "paper_zero": None, "watch_untouched": None,
                       "paused_postclose": None, "intraday_caucion": None,
                       "pending_caucion": None, "market_open_suspended": None,
                       "open_paper_carryover": None, "spot_carryover_absent": None,
                       "caucion_carryover_absent": None, "closed_flat_flags": None,
                       "stop_evidence_status_consistent": None, "receipt_sha256": None},
        "book": {"state": "NO_VERIFICADO", "mode_paper": None,
                 "orders_zero": None, "process_state": None, "session_state": None,
                 "spot_open": None, "caucion_open": None, "futures_active": None,
                 "fci_not_redeemed": None, "exposure_flat": None},
        "cold": {"state": "NO_VERIFICADO", "current_generation": None,
                 "current_snapshot": None, "live_status": None,
                 "current_cut_hash_bound": None, "current_cut_paper_zero": None,
                 "live_paper_zero": None, "live_routes_not_called": None,
                 "failed_queue_state": None, "failed_queue_bound": None,
                 "pack_metadata": "NO_VERIFICADO",
                 "historical_cold2_pack_metadata_cause": "NO_VERIFICADO",
                 "pack_hash_verified": False,
                 "failed_computed_report_hash_verified": False,
                 "successor_distinct_from_revoked": None,
                 "successor_predicate": "NO_VERIFICADO",
                 "original_revoked_cut_absent": None,
                 "historical_cold2_revoked_cut_absent": None},
        "scheduler": {"state": "NO_VERIFICADO", "receipt_status": None,
                      "candidate_matches": None, "attempted_count": None,
                      "suspended_timer_count": None, "preserved_iol_guard": None,
                      "iol_timer_active": None,
                      "iol_activation_after_suspend": None,
                      "unexpected_active_timers": [], "receipt_sha256": None},
        "timers": [{"name": n, "active": "NO_VERIFICADO",
                    "enabled": "NO_VERIFICADO", "entered_at": None} for n in d.TIMERS],
        "unreferenced_images": [], "unreferenced_total": 0,
        "unreferenced_redacted_count": 0,
        "runtime_running_verified": False, "recovery_authorized": False,
        "paper_admission_authorized": False,
    }


def expected():
    return {
        "expected_previous_deploy_sha": R1,
        "failed_deploy_sha": R2,
        "failed_candidate_sha": CAND,
        "failed_expected_image_id": IMG,
        "failed_run_id": RUN,
    }


class A724Tests(TestCase):
    def test_exact_art_timezone_and_allowlisted_receipt(self):
        receipt = full_receipt()
        result = d.sanitize(json.dumps(receipt), expected())
        self.assertEqual(result["state"], "DIAGNOSTIC_ONLY")
        self.assertEqual(datetime.fromisoformat(result["generated_at_art"]).utcoffset(),
                         timedelta(hours=-3))

    def test_rejects_changed_expected_deployment(self):
        receipt = full_receipt()
        receipt["failed_deploy_sha"] = R1
        self.assertEqual(d.sanitize(json.dumps(receipt), expected())["state"], "NO_VERIFICADO")

    def test_rejects_unexpected_sensitive_payload(self):
        receipt = full_receipt()
        receipt["secret_source_tokens"] = "never-upload"
        self.assertEqual(d.sanitize(json.dumps(receipt), expected())["state"], "NO_VERIFICADO")

    def test_rejects_trading_go_claim(self):
        receipt = full_receipt()
        receipt["paper_admission_authorized"] = True
        self.assertEqual(d.sanitize(json.dumps(receipt), expected())["state"], "NO_VERIFICADO")

    def test_rejects_host_mutation_claim(self):
        receipt = full_receipt()
        receipt["mutations"] = 1
        self.assertEqual(d.sanitize(json.dumps(receipt), expected())["state"], "NO_VERIFICADO")

    def test_rejects_freshness_race(self):
        receipt = full_receipt()
        receipt["generated_at_utc"] = (datetime.now(timezone.utc) -
                                      timedelta(minutes=8)).isoformat()
        self.assertEqual(d.sanitize(json.dumps(receipt), expected())["state"], "NO_VERIFICADO")

    def test_rejects_wrong_timer_scope(self):
        receipt = full_receipt()
        receipt["timers"][0]["name"] = "ppi-watch.timer"
        self.assertEqual(d.sanitize(json.dumps(receipt), expected())["state"], "NO_VERIFICADO")

    def test_rejects_other_app_alias_in_receipt(self):
        receipt = full_receipt()
        receipt["unreferenced_images"] = [{
            "image_id": IMG, "size_upper_bytes": 100,
            "tags_allowlisted": ["registry.private.example/secret:prod"],
            "class": "EXTERNAL", "referenced_by_container": False,
        }]
        # This URI would need stronger field allowlist; the public sanitizer
        # must reject unrecognized external image aliases.
        self.assertEqual(d.sanitize(json.dumps(receipt), expected())["state"], "NO_VERIFICADO")

    def test_runner_image_id_requires_exact_r2_manifest_pin(self):
        receipt = full_receipt()
        row = {"image_id": IMG, "size_upper_bytes": 1000,
               "tags_allowlisted": [f"porota-predeploy-v2:{CAND}"],
               "digests_allowlisted": ["porota-predeploy-v2@sha256:" + "a"*64],
               "noncandidate_alias_count": 0, "nonporota_digest_count": 0,
               "class": "CANDIDATE_METADATA_ONLY_NOT_CLEANUP_GO",
               "referenced_by_container": False}
        receipt["unreferenced_images"] = [row]
        receipt["unreferenced_total"] = 1
        self.assertEqual(d.sanitize(json.dumps(receipt), expected())["state"],
                         "DIAGNOSTIC_ONLY")
        row["image_id"] = "sha256:" + "b"*64
        self.assertEqual(d.sanitize(json.dumps(receipt), expected())["state"],
                         "NO_VERIFICADO")
        row["image_id"] = IMG
        row["tags_allowlisted"] = [f"porota-predeploy-v2:{R1}"]
        self.assertEqual(d.sanitize(json.dumps(receipt), expected())["state"],
                         "NO_VERIFICADO")

    def test_rejects_secret_token_inside_container_row(self):
        receipt = full_receipt()
        receipt["containers"][0]["secret_token"] = "PRIVATE"
        self.assertEqual(d.sanitize(json.dumps(receipt), expected())["state"], "NO_VERIFICADO")

    def test_rejects_secret_token_inside_timer_row(self):
        receipt = full_receipt()
        receipt["timers"][0]["secret_token"] = "PRIVATE"
        self.assertEqual(d.sanitize(json.dumps(receipt), expected())["state"], "NO_VERIFICADO")

    def test_rejects_secret_as_allowlisted_field_value(self):
        for path, value in (
            (("marker", "image_id"), "PRIVATEKEY123"),
            (("book", "process_state"), "TOKEN123"),
            (("scheduler", "unexpected_active_timers"), ["APIKEY123"]),
            (("containers", 0, "health"), "SECRET_STATUS"),
            (("timers", 0, "active"), "SECRET_TIMER")):
            with self.subTest(path=path):
                receipt = full_receipt()
                if len(path) == 2:
                    receipt[path[0]][path[1]] = value
                else:
                    receipt[path[0]][path[1]][path[2]] = value
                self.assertEqual(d.sanitize(json.dumps(receipt), expected())["state"],
                                 "NO_VERIFICADO")

    def test_rejects_long_raw_and_bad_json(self):
        self.assertEqual(d.sanitize("x" * 40000, expected())["state"], "NO_VERIFICADO")
        self.assertEqual(d.sanitize("{", expected())["state"], "NO_VERIFICADO")

    def test_valid_r1_marker_not_r2_running_proof(self):
        marker = {"schema_version": 2, "deploy_sha": R1,
                  "candidate_sha": "a" * 40, "image_id": "sha256:" + "b" * 64,
                  "validation_status": "VALIDATED_RUNTIME",
                  "mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
                  "real_order_routes": "NOT_CALLED"}
        with mock.patch.object(d, "_json", return_value=(marker, HASH)):
            result = d._marker(Path("/nohost"), R1, R2, CAND)
        self.assertEqual(result["relation"], "R1_LAST_GO")
        self.assertTrue(result["paper_zero"])
        self.assertNotEqual(result["relation"], "FAILED_R2")

    def test_failed_r2_marker_still_pending(self):
        marker = {"schema_version": 2, "deploy_sha": R2,
                  "candidate_sha": CAND, "image_id": IMG,
                  "validation_status": "DEPLOYED_VALIDATION_PENDING",
                  "mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
                  "real_order_routes": "NOT_CALLED"}
        with mock.patch.object(d, "_json", return_value=(marker, HASH)):
            result = d._marker(Path("/nohost"), R1, R2, CAND)
        self.assertEqual(result["relation"], "FAILED_R2")
        self.assertEqual(result["validation_status"], "DEPLOYED_VALIDATION_PENDING")

    def test_marker_nonzero_real_orders_fails_safety_boolean(self):
        marker = {"schema_version": 2, "deploy_sha": R2,
                  "candidate_sha": CAND, "image_id": IMG,
                  "validation_status": "DEPLOYED_VALIDATION_PENDING",
                  "mode": "PRODUCTION_PAPER", "real_orders_sent": 1,
                  "real_order_routes": "NOT_CALLED"}
        with mock.patch.object(d, "_json", return_value=(marker, HASH)):
            self.assertFalse(d._marker(Path("/nohost"), R1, R2, CAND)["paper_zero"])

    def test_clean_stops_need_live_exited(self):
        proof = {"classification": "CLEAN_EXIT", "after": {
            "Status": "exited", "ExitCode": 0, "OOMKilled": False},
            "container_id": "a" * 64}
        # The predicate covers lifecycle *status*, not Docker identity.
        self.assertTrue(d._stop_status_consistent(proof, {
            "state": "EXITED", "id": "b" * 64}))
        self.assertFalse(d._stop_status_consistent(proof, {"state": "RUNNING"}))
        self.assertIsNone(d._stop_status_consistent(None, {"state": "EXITED"}))

    def test_flat_receipt_not_substitute_for_db(self):
        receipt = {
            "candidate_sha": CAND, "status": "FIX_FORWARD_VALIDATION_REQUIRED",
            "real_orders_sent": 0, "ppi_watch_accessed": False,
            "installation_started": True,
            "paused_postclose": False, "intraday_caucion": False,
            "pending_caucion": False, "market_open_suspended": False,
            "open_paper_carryover": False, "spot_carryover": None,
            "caucion_carryover": None}
        live = [{"state": "EXITED"}, {"state": "EXITED"}, {"state": "RUNNING"}]
        with mock.patch.object(d, "_json", return_value=(receipt, HASH)):
            result = d._quiescence(Path("/nohost"), CAND, live)
        self.assertTrue(result["closed_flat_flags"])
        self.assertIsNone(result["stop_evidence_status_consistent"])
        self.assertTrue(result["paper_zero"])

    def test_missing_proof_not_cold_successor_valid(self):
        shadow = Path("/synthetic/shadow")
        pointer = {"generation_id": GEN, "file": "cut-" + GEN + ".json.gz",
                   "manifest_sha256": hashlib.sha256(d._canonical({
                       "generation_id": GEN, "sequence": 42})).hexdigest(),
                   "sequence": 42}
        cut = {"manifest": {"generation_id": GEN, "sequence": 42},
               "report": {"computed_report": {"snapshot_id": CURRENT_ID,
                   "state": "PENDING", "manifest_sha256": HASH}},
               "status": {"real_orders_sent": 0, "real_routes": "NOT_CALLED"}}
        live = {"status": "FAIL_CLOSED", "real_orders_sent": 0,
                "real_routes": "NOT_CALLED"}
        with mock.patch.object(d, "_envelope", side_effect=[pointer, cut, live,
                                                            FileNotFoundError()]):
            result = d._cold(shadow, Path("/synthetic/deploy"))
        self.assertEqual(result["successor_predicate"], "NO_CURRENT_FAILED_QUEUE")
        self.assertFalse(result["pack_hash_verified"])

    def test_pack_owner_is_causal_guard_block(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pack"
            path.write_bytes(b"immutable-payload")
            path.chmod(0o600)
            info = path.stat()
            manifest = {"pack_bytes": info.st_size, "pack_identity": [
                info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]}
            self.assertEqual(d._pack_metadata(path, manifest, info.st_uid+1), "PACK_OWNER")
            self.assertEqual(d._pack_metadata(path, manifest, info.st_uid),
                             "METADATA_BOUND_NOT_HASHED")

    def test_historical_cold2_pack_owner_independent_from_current_successor(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / ("rc6-failed-cold-" + d.HISTORICAL_COLD2_ID)
            archive.mkdir(mode=0o700)
            manifest = {"snapshot_id": d.HISTORICAL_COLD2_ID,
                        "pack": d.HISTORICAL_COLD2_ID + ".pack"}
            sha = hashlib.sha256(d._canonical(manifest)).hexdigest()
            files = (d.HISTORICAL_COLD2_ID + ".pack",
                     "manifest-" + d.HISTORICAL_COLD2_ID + ".json",
                     "receipt-" + d.HISTORICAL_COLD2_ID + ".json",
                     "queue.json", "quarantine.json")
            for name in files:
                (archive / name).write_text("fixture")
            inputs = [
                manifest,
                {"snapshot_id": d.HISTORICAL_COLD2_ID, "state": "FAILED",
                 "manifest_sha256": sha},
                {"snapshot_id": d.HISTORICAL_COLD2_ID, "state": "FAILED",
                 "manifest_sha256": sha},
                {"snapshot_id": d.HISTORICAL_COLD2_ID,
                 "state": "QUARANTINED_FAILED", "manifest_sha256": sha,
                 "old_cut_revoked": True}]
            with (mock.patch.object(d, "_envelope", side_effect=inputs),
                  mock.patch.object(d, "_pack_metadata", return_value="PACK_OWNER")):
                cause = d._historical_cold2_pack(Path(folder), expected_manifest=sha)
            self.assertEqual(cause, "PACK_OWNER")
            # This historical cause does NOT prove anything about the
            # independently CURRENT-bound successor pack/hash.
            self.assertNotEqual(cause, "METADATA_BOUND_NOT_HASHED")

    def test_historical_archive_missing_is_not_assumed_green(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(d._historical_cold2_pack(Path(folder)), "ABSENT")

    def test_symlink_pack_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            pack = Path(folder) / "pack"
            pack.symlink_to(Path(folder) / "other")
            self.assertEqual(d._pack_metadata(pack, {}, os.getuid()), "PACK_TYPE_LINK_MODE")

    def test_iol_timer_preserved_by_receipt_not_suspicious(self):
        timer = [{"name": n, "active": "ACTIVE" if n == d.IOL_OAUTH_TIMER else
                  "INACTIVE", "entered_at": "2026-11-06 17:23:35 UTC"
                  if n == d.IOL_OAUTH_TIMER else None} for n in d.TIMERS]
        row = {"candidate_sha": CAND, "status": "SUSPENDED",
               "timers": list(d.TIMERS), "attempted": list(d.TIMERS),
               "preserved_iol_timer": d.IOL_OAUTH_TIMER,
               "recorded_at": "2026-11-06T17:17:00+00:00"}
        with mock.patch.object(d, "_json", return_value=(row, HASH)):
            result = d._scheduler(Path("/nohost"), CAND, timer)
        self.assertTrue(result["preserved_iol_guard"])
        self.assertTrue(result["iol_timer_active"])
        self.assertTrue(result["iol_activation_after_suspend"])
        self.assertEqual(result["unexpected_active_timers"], [])

    def test_unpreserved_active_iol_is_reported_not_touched(self):
        timer = [{"name": n, "active": "ACTIVE" if n == d.IOL_OAUTH_TIMER
                  else "INACTIVE", "entered_at": None} for n in d.TIMERS]
        row = {"candidate_sha": CAND, "status": "SUSPENDED",
               "timers": list(d.TIMERS), "attempted": list(d.TIMERS),
               "recorded_at": "2026-11-06T17:17:00+00:00"}
        with mock.patch.object(d, "_json", return_value=(row, HASH)):
            result = d._scheduler(Path("/nohost"), CAND, timer)
        self.assertFalse(result["preserved_iol_guard"])
        self.assertEqual(result["unexpected_active_timers"], [d.IOL_OAUTH_TIMER])

    def test_unreferenced_image_strict_ownership(self):
        with (mock.patch.object(d, "_image_manifest_bound", return_value=True),
              mock.patch.object(d, "_command", side_effect=[
              "", IMG, f'"{IMG}"|["porota-predeploy-v2:{CAND}"]|null|1000'])):
            images, count, redacted = d._images(CAND, IMG)
        self.assertEqual(count, 1)
        self.assertEqual(images, [])
        self.assertEqual(redacted, 1)

    def test_boca_unreferenced_image_metadata_never_published(self):
        with (mock.patch.object(d, "_image_manifest_bound", return_value=True),
              mock.patch.object(d, "_command", side_effect=[
                  "", IMG, f'"{IMG}"|["boca-socios-assistant:private"]|'
                           '["boca-socios-assistant@sha256:' + "a" * 64 + '"]|2000'])):
            images, count, redacted = d._images(CAND, IMG)
        self.assertEqual((images, count, redacted), ([], 1, 1))

    def test_mixed_porota_boca_alias_suppresses_id_and_size(self):
        alias = json.dumps([f"porota-predeploy-v2:{CAND}", "boca-socios:prod"])
        with (mock.patch.object(d, "_image_manifest_bound", return_value=True),
              mock.patch.object(d, "_command", side_effect=[
                  "", IMG, f'"{IMG}"|{alias}|["porota-predeploy-v2@sha256:' +
                           "a" * 64 + '"]|3000'])):
            images, count, redacted = d._images(CAND, IMG)
        self.assertEqual((images, count, redacted), ([], 1, 1))

    def test_exact_r2_host_manifest_bound_candidate_metadata_is_published(self):
        tags = json.dumps([f"porota-predeploy-v2:{CAND}"])
        digests = json.dumps(["porota-predeploy-v2@sha256:" + "a"*64])
        with (mock.patch.object(d, "_image_manifest_bound", return_value=True),
              mock.patch.object(d, "_command", side_effect=[
                  "", IMG, f'"{IMG}"|{tags}|{digests}|1000'])):
            images, count, redacted = d._images(CAND, IMG)
        self.assertEqual((count, redacted), (1, 0))
        self.assertEqual(images[0]["image_id"], IMG)
        self.assertEqual(images[0]["class"], "CANDIDATE_METADATA_ONLY_NOT_CLEANUP_GO")

    def test_never_use_mutating_host_subprocesses(self):
        source = Path(d.__file__).read_text()
        for forbidden in ("docker prune", "docker image rm", "docker rm ",
                          "systemctl start", "systemctl stop", "systemctl enable",
                          "journalctl", "sqlite3.connect(path.as_uri()+'?mode=rw"):
            self.assertNotIn(forbidden, source)
        self.assertIn("?mode=ro", source)
        self.assertIn("O_NOFOLLOW", source)
        self.assertIn("recovery_authorized\": False", source)


if __name__ == "__main__":
    import unittest
    unittest.main()
