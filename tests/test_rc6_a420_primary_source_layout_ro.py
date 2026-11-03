"""A420 offline guards: only public role labels and coarse metadata escape."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/rc6_a420_primary_source_layout_ro.py"
spec = importlib.util.spec_from_file_location("rc6_a420_primary_source_layout_ro", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
NOW = datetime(2026, 11, 3, 1, 0, tzinfo=timezone.utc)  # 02-Nov 22:00 ART


class A420ReadOnlyLayoutTests(unittest.TestCase):
    def test_fixed_paths_and_units_never_include_db_or_secret_files(self):
        self.assertEqual(len(module.FILES), 6)
        self.assertEqual(len({item[0] for item in module.FILES}), 6)
        self.assertEqual(len(module.UNIT_NAMES), 2)
        all_paths = " ".join(str(item[1]) for item in module.FILES).lower()
        self.assertNotIn(".env", all_paths)
        self.assertNotIn(".db", all_paths)
        self.assertNotIn("-wal", all_paths)
        self.assertNotIn("-shm", all_paths)
        self.assertNotIn("ppi-watch", all_paths)

    def test_regular_file_report_never_reads_private_content(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "primary_last.json"
            secret = "PERSONAL_QUOTE_ACCOUNT_TOKEN_999"
            path.write_text(secret, encoding="utf-8")
            record = module.inspect_file("PRIMARY_TEST", path, 4096, False, now=NOW)
            encoded = json.dumps(record)
            self.assertEqual(record["status"], "PRESENT_REGULAR")
            self.assertNotIn(secret, encoded)
            self.assertNotIn(str(path), encoded)
            self.assertNotIn("mtime", encoded.replace("mtime_age_bucket", ""))

    def test_file_and_parent_symlinks_blocked(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            target = root / "private"
            target.write_text("SECRET", encoding="utf-8")
            link = root / "alias"
            link.symlink_to(target)
            result = module.inspect_file("TEST", link, 1024, False, now=NOW)
            self.assertEqual(result["status"], "UNAVAILABLE_OR_ALIAS")
            directory = root / "some-dir"
            directory.mkdir()
            (directory / "valid").write_text("not private", encoding="utf-8")
            dirlink = root / "dirlink"
            dirlink.symlink_to(directory, target_is_directory=True)
            other = module.inspect_file("TEST", dirlink / "valid", 1024, False, now=NOW)
            self.assertIn(other["status"], {"UNAVAILABLE_OR_ALIAS", "UNSAFE_PATH"})

    def test_size_guard_blocks_oversized_or_changed_capture(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "oversize"
            path.write_bytes(b"x" * 129)
            receipt = module.inspect_file("TEST", path, 128, True, now=NOW)
            self.assertEqual(receipt["status"], "SIZE_LIMIT")
            self.assertEqual(receipt["mtime_age_bucket"], "UNKNOWN")

    def test_state_schema_clock_and_art_day_separated(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "state.json"
            data = {"schema": module.STATE_SCHEMA, "session_date": "2026-11-02",
                    "last_public_success_at": "2026-11-03T00:58:00+00:00",
                    "last_evidence_success_at": "2026-11-02T23:45:00+00:00"}
            path.write_text(json.dumps(data), encoding="utf-8")
            receipt = module.inspect_file("SOURCE_CYCLE_STATE", path, 4096, True, now=NOW)
            self.assertEqual(receipt["state_schema"], "MATCHED")
            self.assertEqual(receipt["session"], "TODAY")
            self.assertEqual(receipt["public_last_success"], "WITHIN_5M")
            self.assertEqual(receipt["evidence_last_success"], "WITHIN_2H")
            self.assertNotIn("2026-11-03T00:58", json.dumps(receipt))

    def test_unknown_state_does_not_export_unrecognized_keys(self):
        private = "PRIVATE_CLOCK_SECRET_JOURNAL"
        payload = {"schema": module.STATE_SCHEMA, "session_date": private,
                   "last_public_success_at": private,
                   "last_evidence_success_at": {"token": private},
                   "database_password": private}
        result = module._state_receipt(json.dumps(payload).encode(), NOW)
        self.assertNotIn(private, json.dumps(result))
        self.assertEqual(result["session"], "OTHER_DAY_OR_MISSING")
        self.assertEqual(result["public_last_success"], "UNKNOWN")

    def test_systemctl_only_query_and_validated_labels(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0,
                       "LoadState=loaded\nActiveState=active\nResult=success\nExecMainStatus=0\n", "")

        row = module.inspect_unit(module.UNIT_NAMES[0], runner=fake_run)
        self.assertEqual(row["query"], "READ_ONLY")
        self.assertEqual(row["active"], "active")
        self.assertEqual(row["main_exit"], "ZERO")
        self.assertEqual(calls[0][:2], ["systemctl", "show"])
        self.assertNotIn("restart", " ".join(calls[0]))
        self.assertNotIn("show-environment", " ".join(calls[0]))

    def test_systemctl_untrusted_output_fails_closed(self):
        private = "SECRET_MALICIOUS_PAYLOAD"

        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0,
                       f"LoadState={private}\nActiveState={private}\nResult={private}\nExecMainStatus={private}\n", "")

        row = module.inspect_unit(module.UNIT_NAMES[1], runner=fake_run)
        self.assertNotIn(private, json.dumps(row))
        self.assertEqual(row["active"], "UNKNOWN")
        self.assertEqual(row["result"], "UNKNOWN")

    def test_unknown_unit_does_not_invoke_command(self):
        with patch.object(module.subprocess, "run", side_effect=AssertionError("called")):
            row = module.inspect_unit("ppi-watch.service", runner=module.subprocess.run)
        self.assertEqual(row["query"], "UNAVAILABLE")

    def test_aggregate_diagnostic_never_elevates_readiness(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "exists"
            path.write_bytes(b"x")

            def unavailable(*args, **kwargs):
                raise subprocess.TimeoutExpired(cmd="systemctl show", timeout=4)

            data = module.inspect_all(now=NOW, files=(("FIXED_TEST", path, 2, False),),
                                      runner=unavailable)
            self.assertEqual(data["schema"], module.SCHEMA)
            self.assertTrue(data["diagnostic_only"])
            self.assertFalse(data["database_opened"])
            self.assertFalse(data["paper_ready"])
            self.assertFalse(data["ppi_watch_touched"])
            self.assertEqual(data["provider_calls"], 0)
            self.assertEqual(data["order_routes_called"], False)
            self.assertEqual(data["units"][0]["query"], "UNAVAILABLE")

    def test_cli_requires_exact_no_path_arguments(self):
        self.assertEqual(module.main([]), 64)
        self.assertEqual(module.main(["host-probe", "/etc/shadow"]), 64)


if __name__ == "__main__":
    unittest.main()
