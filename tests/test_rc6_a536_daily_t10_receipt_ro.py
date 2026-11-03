"""A536 current-day T-10 observation is strictly passive and release-bound."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import rc6_a536_daily_t10_receipt_ro as probe


ART = ZoneInfo("America/Argentina/Buenos_Aires")
PRODUCT_SHA = "a" * 40
SOURCE_SHA = "b" * 40
TREE_SHA = "c" * 40
SCRIPT_DIGEST = "d" * 64
IMAGE_ID = "sha256:" + "e" * 64


def at(day=4, hour=10, minute=32, second=0):
    return datetime(2026, 11, day, hour, minute, second, tzinfo=ART)


def request(now=None):
    now = now or at()
    return {"trade_day_ar": now.date().isoformat(),
            "expected_product_sha": PRODUCT_SHA,
            "source_commit": SOURCE_SHA,
            "source_sha256": SCRIPT_DIGEST,
            "requested_at_ar": now.isoformat(timespec="seconds")}


def existing_gate(day=4, hour=10, minute=24):
    return {"schema": "RC6_PAPER_PREOPEN_GATE_V2",
            "trade_day_ar": f"2026-11-{day:02d}",
            "candidate_sha": PRODUCT_SHA,
            "status": "GO_PAPER_SCALPING",
            "phase": "T_MINUS_10",
            "issued_at_ar": at(day, hour, minute).isoformat()}


def marker():
    return {"candidate_sha": PRODUCT_SHA, "image_id": IMAGE_ID,
            "validation_status": "VALIDATED_RUNTIME", "mode": "PRODUCTION_PAPER",
            "real_orders_sent": 0, "real_order_routes": "NOT_CALLED",
            "ppi_watch_untouched": True}


def frozen():
    return {"candidate_sha": PRODUCT_SHA, "candidate_tree_sha": TREE_SHA,
            "real_order_capability_required": "BLOCKED",
            "paper_mode_required": "PRODUCTION_PAPER", "build_once": True}


def containers(name):
    common = {"running": True, "image": IMAGE_ID, "readonly_rootfs": True}
    if name == "porota_production_observer":
        return {**common, "build_sha": PRODUCT_SHA, "tree_sha": TREE_SHA}
    return common


class WindowAndRequestTests(unittest.TestCase):
    def test_current_day_window_and_weekday(self):
        self.assertEqual(probe._window(at()).date().isoformat(), "2026-11-04")
        for invalid in (at(4, 10, 29), at(4, 10, 45), at(4, 9, 30),
                        at(7, 10, 32), at(8, 10, 32)):
            with self.subTest(invalid=invalid), self.assertRaises(probe.ProbeBlocked):
                probe._window(invalid)

    def test_uses_current_art_not_host_utc_date(self):
        utc = datetime(2026, 11, 4, 13, 32, tzinfo=timezone.utc)
        self.assertEqual(probe.validate_request(request(), utc).date(), at().date())
        with self.assertRaises(probe.ProbeBlocked):
            probe.validate_request({**request(), "trade_day_ar": "2026-11-02"}, utc)

    def test_request_source_provenance_and_clock_fail_closed(self):
        good = request()
        self.assertEqual(probe.validate_request(good, at()), at())
        variants = (
            {**good, "expected_product_sha": ""},
            {**good, "source_commit": "g" * 40},
            {**good, "source_sha256": "h" * 64},
            {**good, "source_sha256": ""},
            {**good, "unexpected": True},
            {**good, "requested_at_ar": "2026-11-04T10:32:00"},
            {**good, "requested_at_ar": "2026-11-04T13:32:00+00:00"},
            {**good, "requested_at_ar": at(4, 10, 40).isoformat()},
            {**good, "requested_at_ar": at(4, 10, 29).isoformat()},
            {**good, "trade_day_ar": "2026-11-03"},
        )
        for invalid in variants:
            with self.subTest(invalid=invalid), self.assertRaises(probe.ProbeBlocked):
                probe.validate_request(invalid, at())

    def test_environment_has_no_date_or_image_user_input(self):
        env = {"GITHUB_SHA": SOURCE_SHA, "RC6_EXPECTED_PRODUCT_SHA": PRODUCT_SHA,
               "RC6_SOURCE_SHA256": SCRIPT_DIGEST}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(probe.request_from_environment(now=at()), request())
        self.assertEqual(len(probe.REQUEST_KEYS), 5)


class ExistingReceiptTests(unittest.TestCase):
    def test_matching_same_day_go_is_only_observation(self):
        self.assertIsNone(probe.verify_existing_gate(existing_gate(), now=at(),
                                                     expected_sha=PRODUCT_SHA))
        with patch.object(probe, "verify_release", return_value=(TREE_SHA, IMAGE_ID)), \
                patch.object(probe, "verify_installed_helper") as helper, \
                patch.object(probe, "_read_json", return_value=existing_gate()):
            helper.return_value.return_value = (
                True, "OPERATOR_REARM_AUTHORIZED",
                {"candidate_sha": PRODUCT_SHA, "candidate_tree_sha": TREE_SHA,
                 "image_id": IMAGE_ID})
            observed = probe.probe(request(), at())
        self.assertEqual(observed["status"], "OBSERVED_ONLY")
        self.assertEqual(observed["reason"], "EXISTING_GO_RECEIPT_MATCHED")
        self.assertFalse(observed["entry_authority"])
        self.assertEqual(observed["real_orders_sent_by_action"], 0)
        self.assertEqual(observed["host_mutations"], 0)
        self.assertFalse(observed["database_opened"])
        self.assertEqual(observed["provider_calls"], 0)
        self.assertFalse(observed["ppi_watch_touched"])
        self.assertEqual(observed["installed_sha"], PRODUCT_SHA)

    def test_gate_rejects_old_day_wrong_sha_forged_or_future(self):
        gate = existing_gate()
        changed = (
            {**gate, "trade_day_ar": "2026-11-03"},
            {**gate, "candidate_sha": "f" * 40},
            {**gate, "status": "NO_GO_PAPER_SCALPING"},
            {**gate, "status": "GO_PAPER_SCALPING", "schema": "RC6_PAPER_PREOPEN_GATE_V1"},
            {**gate, "phase": "T45"},
            {**gate, "issued_at_ar": at(4, 10, 19).isoformat()},
            {**gate, "issued_at_ar": at(4, 10, 30).isoformat()},
            {**gate, "issued_at_ar": at(4, 10, 40).isoformat()},
            {**gate, "issued_at_ar": at(3, 10, 24).isoformat()},
            {**gate, "issued_at_ar": at(4, 10, 24).replace(
                tzinfo=timezone.utc).isoformat()},
            {**gate, "issued_at_ar": "2026-11-04T10:24:00"},
            {**gate, "issued_at_ar": None},
        )
        for altered in changed:
            with self.subTest(altered=altered), self.assertRaises(probe.ProbeBlocked):
                probe.verify_existing_gate(altered, now=at(), expected_sha=PRODUCT_SHA)

    def test_operator_explicit_pause_or_wrong_release_holds(self):
        with patch.object(probe, "verify_release", return_value=(TREE_SHA, IMAGE_ID)), \
                patch.object(probe, "verify_installed_helper") as helper, \
                patch.object(probe, "_read_json", return_value=existing_gate()):
            helper.return_value.return_value = (False, "OPERATOR_NO_GO_PENDING_EXPLICIT_REARM", None)
            with self.assertRaisesRegex(probe.ProbeBlocked, "OPERATOR_INTENT_NOT_VERIFIED"):
                probe.probe(request(), at())
            helper.return_value.return_value = (
                True, "OPERATOR_REARM_AUTHORIZED",
                {"candidate_sha": "f" * 40, "candidate_tree_sha": TREE_SHA,
                 "image_id": IMAGE_ID})
            with self.assertRaisesRegex(probe.ProbeBlocked, "OPERATOR_INTENT_NOT_VERIFIED"):
                probe.probe(request(), at())

    def test_duplicate_json_keys_and_nonfinite_rejected(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'[]'):
            with self.subTest(raw=raw), self.assertRaises(probe.ProbeBlocked):
                probe._json(raw)


class HostReadSafetyTests(unittest.TestCase):
    def test_runner_sanitizer_blocks_false_go_and_sensitive_extra_fields(self):
        good = probe.result_template(request(), at())
        good.update(status="OBSERVED_ONLY", reason="EXISTING_GO_RECEIPT_MATCHED",
                    installed_sha=PRODUCT_SHA, installed_tree_sha=TREE_SHA,
                    image_id=IMAGE_ID)
        options = {"return_code": 0, "product_sha": PRODUCT_SHA,
                   "source_commit": SOURCE_SHA, "source_sha256": SCRIPT_DIGEST}
        normalized = probe.sanitize_runner_receipt(json.dumps(good), **options)
        self.assertEqual(normalized["status"], "OBSERVED_ONLY")
        for changed, overrides in (
            ({**good, "read_only": False}, {}),
            ({**good, "entry_authority": True}, {}),
            ({**good, "database_opened": True}, {}),
            ({**good, "provider_calls": 1}, {}),
            ({**good, "host_mutations": 1}, {}),
            ({**good, "ppi_watch_touched": True}, {}),
            ({**good, "real_orders_sent_by_action": 1}, {}),
            ({**good, "source_commit": PRODUCT_SHA}, {}),
            ({**good, "source_sha256": "f" * 64}, {}),
            ({**good, "source_sha256": "PASSWORD=secret"}, {}),
            ({**good, "installed_sha": "f" * 40}, {}),
            ({**good, "image_id": "sha256:broken"}, {}),
            ({**good, "secret": "must-not-leak"}, {}),
            ({**good, "reason": "INVALID;SECRET"}, {}),
            ({**good, "reason": "GO_PAPER_SCALPING"}, {}),
            ({**good, "observed_at_ar": "2026-11-04T13:32:00+00:00"}, {}),
            (good, {"return_code": 2}),
        ):
            with self.subTest(changed=changed, overrides=overrides), \
                    self.assertRaises(probe.ProbeBlocked):
                probe.sanitize_runner_receipt(json.dumps(changed),
                                              **{**options, **overrides})
        blocked = {**good, "status": "NO_VERIFICADO",
                   "reason": "EXISTING_T10_GO_RECEIPT_NOT_VERIFIED",
                   "installed_sha": "", "installed_tree_sha": "", "image_id": ""}
        self.assertEqual(probe.sanitize_runner_receipt(
            json.dumps(blocked), **{**options, "return_code": 2})["status"],
            "NO_VERIFICADO")
        with self.assertRaises(probe.ProbeBlocked):
            probe.sanitize_runner_receipt(json.dumps(blocked), **options)

    def test_frozen_release_identity_requires_full_paper_zero(self):
        with patch.object(probe, "_read_json", side_effect=[marker(), frozen()]), \
                patch.object(probe, "_container", side_effect=containers):
            self.assertEqual(probe.verify_release(PRODUCT_SHA), (TREE_SHA, IMAGE_ID))
        variants = (
            ({**marker(), "candidate_sha": "f" * 40}, frozen()),
            ({**marker(), "real_orders_sent": True}, frozen()),
            ({**marker(), "real_order_routes": "CALLED"}, frozen()),
            ({**marker(), "ppi_watch_untouched": False}, frozen()),
            ({**marker(), "mode": "LIVE"}, frozen()),
            (marker(), {**frozen(), "build_once": False}),
            (marker(), {**frozen(), "candidate_tree_sha": ""}),
        )
        for mutated_marker, mutated_frozen in variants:
            with self.subTest(marker=mutated_marker), \
                    patch.object(probe, "_read_json", side_effect=[
                        mutated_marker, mutated_frozen]), \
                    patch.object(probe, "_container", side_effect=containers), \
                    self.assertRaises(probe.ProbeBlocked):
                probe.verify_release(PRODUCT_SHA)

    def test_root_owned_regular_nofollow_and_duplicate_key(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "operator"
            directory.mkdir()
            path = directory / "paper-preopen-v2.json"
            payload = existing_gate()
            path.write_text(json.dumps(payload), encoding="utf-8")
            original_stat, original_lstat = os.fstat, os.lstat

            def root_stat(fd):
                st = original_stat(fd)
                return SimpleNamespace(st_mode=st.st_mode, st_nlink=st.st_nlink,
                                       st_size=st.st_size, st_uid=0)

            def root_lstat(name):
                st = original_lstat(name)
                return SimpleNamespace(st_mode=st.st_mode, st_uid=0)

            with patch.object(probe.os, "fstat", side_effect=root_stat), \
                    patch.object(probe.os, "lstat", side_effect=root_lstat):
                self.assertEqual(probe._read_json(path, 4096, root_owned=True,
                                                  root_directory=True), payload)
            with patch.object(probe.os, "fstat", side_effect=lambda fd: SimpleNamespace(
                    st_mode=stat.S_IFREG | 0o644, st_nlink=1, st_size=path.stat().st_size,
                    st_uid=1000)):
                with self.assertRaises(probe.ProbeBlocked):
                    probe._read_json(path, 4096, root_owned=True)
            path.unlink()
            path.symlink_to(directory / "missing")
            with self.assertRaises(OSError):
                probe._read_json(path, 4096, root_owned=True)

    def test_installed_helper_only_if_manifest_verified(self):
        source = ("def evaluate_rearm(**kwargs):\n"
                  "    return False, 'OPERATOR_NO_GO_PENDING_EXPLICIT_REARM', None\n").encode()
        with tempfile.TemporaryDirectory() as td:
            helper = Path(td) / "rc6_operator_paper_rearm.py"
            helper.write_bytes(source)
            record = {"path": "scripts/rc6_operator_paper_rearm.py",
                      "bytes": len(source), "sha256": hashlib.sha256(source).hexdigest()}
            manifest = {"schema_version": 1, "status": "GREEN", "files": [record],
                        "file_count": 1, "bundle_sha256": "b" * 64}
            with patch.object(probe, "HELPER", helper), \
                    patch.object(probe, "_read_json", return_value=manifest):
                self.assertEqual(probe.verify_installed_helper()(), (
                    False, "OPERATOR_NO_GO_PENDING_EXPLICIT_REARM", None))
                record["sha256"] = "c" * 64
                with self.assertRaisesRegex(probe.ProbeBlocked, "HASH_MISMATCH"):
                    probe.verify_installed_helper()

    def test_workflow_is_parameter_free_manual_only_and_host_gated(self):
        yaml = (Path(__file__).resolve().parents[1] / ".github/workflows/"
                "rc6-a536-daily-t10-receipt-readonly.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", yaml)
        self.assertNotIn("    inputs:", yaml)
        self.assertNotIn("\n  schedule:", yaml)
        self.assertIn("github.actor == 'mbalbo2023'", yaml)
        self.assertIn("github.ref == 'refs/heads/main'", yaml)
        self.assertIn("--check-window", yaml)
        self.assertLess(yaml.index("--check-window"), yaml.index("Strict SSH"))
        self.assertIn("RC6_EXPECTED_PRODUCT_SHA", yaml)
        self.assertIn("--sanitize-runner", yaml)
        self.assertIn("if: always()", yaml)
        self.assertIn("entry_authority", yaml)
        self.assertNotIn("python3 -m pytest", yaml)

    def test_result_template_cannot_authorize_paper(self):
        outcome = probe.result_template(request(), at())
        self.assertEqual(set(outcome), probe.RESULT_KEYS)
        self.assertEqual(outcome["status"], "NO_VERIFICADO")
        self.assertFalse(outcome["entry_authority"])
        self.assertEqual(outcome["host_mutations"], 0)
        self.assertEqual(outcome["real_orders_sent_by_action"], 0)
        self.assertFalse(outcome["database_opened"])
        self.assertEqual(outcome["provider_calls"], 0)


if __name__ == "__main__":
    unittest.main()
