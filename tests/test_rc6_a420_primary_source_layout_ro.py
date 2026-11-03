"""A420 offline guards: only public role labels and coarse metadata escape."""

from __future__ import annotations

from datetime import datetime, timezone
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/rc6_a420_primary_source_layout_ro.py"
WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/rc6-a420-primary-source-layout-readonly.yml"
spec = importlib.util.spec_from_file_location("rc6_a420_primary_source_layout_ro", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
NOW = datetime(2026, 11, 3, 1, 0, tzinfo=timezone.utc)  # 02-Nov 22:00 ART


class A420ReadOnlyLayoutTests(unittest.TestCase):
    def test_fixed_paths_and_units_never_include_db_or_secret_files(self):
        self.assertEqual(len(module.FILES), 7)
        self.assertEqual(len({item[0] for item in module.FILES}), 7)
        self.assertEqual(len(module.UNIT_NAMES), 2)
        self.assertEqual(module.FILES[-1][0], "PPI_SCANNER_PRIVATE_RECEIPT")
        self.assertEqual(module.FILES[-1][1],
                         Path("/opt/porota-trading/data/market/rc6_ppi_scanner_primary_latest.json"))
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

    def test_service_identity_and_dropins_are_sanitized_read_only(self):
        private = "/run/secret/private-dropin.conf"

        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0,
                    "LoadState=loaded\nActiveState=active\nResult=success\n"
                    f"ExecMainStatus=0\nUser=1000\nDynamicUser=no\nDropInPaths={private}\n", "")

        row = module.inspect_unit(module.UNIT_NAMES[1], runner=fake_run)
        self.assertEqual(row["effective_user"], "UID1000")
        self.assertEqual(row["dynamic_user"], "NO")
        self.assertEqual(row["dropins"], "PRESENT")
        self.assertNotIn(private, json.dumps(row))

    def test_service_identity_root_dynamic_override_and_unknown_fail_closed(self):
        for user, dynamic, expected in (
            ("", "no", "ROOT"), ("root", "no", "ROOT"),
            ("1000", "no", "UID1000"), ("5000", "no", "OTHER"),
            ("1000", "yes", "DYNAMIC"), ("1000", "unknown", "UNKNOWN"),
        ):
            with self.subTest(user=user, dynamic=dynamic):
                self.assertEqual(module._effective_user_class(user, dynamic), expected)
        self.assertEqual(module._effective_user_class(None, "no"), "UNKNOWN")

        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0,
                    "LoadState=loaded\nActiveState=inactive\nResult=success\n"
                    "ExecMainStatus=0\nUser=root\nDynamicUser=no\nDropInPaths=\n", "")

        row = module.inspect_unit(module.UNIT_NAMES[0], runner=fake_run)
        self.assertEqual(row["effective_user"], "NOT_APPLICABLE")
        self.assertEqual(row["dynamic_user"], "NOT_APPLICABLE")
        self.assertEqual(row["dropins"], "NOT_APPLICABLE")

        def missing_run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0,
                    "LoadState=not-found\nActiveState=inactive\nResult=success\n"
                    "ExecMainStatus=0\nUser=root\nDynamicUser=no\nDropInPaths=\n", "")

        missing = module.inspect_unit(module.UNIT_NAMES[1], runner=missing_run)
        self.assertEqual(missing["effective_user"], "UNKNOWN")

    def test_private_sidecar_0600_expected_owner_and_dac_scope_only(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "rc6_ppi_scanner_primary_latest.json"
            secret = "PRIVATE_BROKER_ACCOUNT_PPI_MARKET_PRICE_123"
            path.write_text(secret, encoding="utf-8")
            path.chmod(0o600)
            with patch.object(module, "_verify_sidecar_access", return_value="ACCESS_OK") as probe:
                row = module.inspect_private_receipt(
                    path, 1024, effective_user="UID1000", now=NOW,
                    expected_owner_uid=os.getuid())
            self.assertEqual(row["status"], "PRESENT_REGULAR")
            self.assertEqual(row["private_mode"], "PRIVATE_0600")
            self.assertEqual(row["owner_class"], "UID1000")
            self.assertEqual(row["consumer_access"], "ACCESS_OK")
            self.assertEqual(row["access_scope"], "HOST_DAC_ONLY")
            self.assertEqual(probe.call_count, 1)
            self.assertNotIn(secret, json.dumps(row))
            self.assertNotIn(str(path), json.dumps(row))
            self.assertEqual(set(row), {'role', 'status', 'mtime_age_bucket',
                                        'private_mode', 'owner_class',
                                        'consumer_access', 'access_scope'})

    def test_private_sidecar_root_fd_not_inherited_by_dac_probe_fork(self):
        # A473 P1 regression: a forked UID1000 child inherits *all* open FDs.
        # If the parent keeps the privileged root-open sidecar descriptor,
        # setuid is meaningless: the child can read through that same FD.
        if not hasattr(os, "fork") or not Path("/proc/self/fd").is_dir():
            self.skipTest("requires Linux fork and /proc/self/fd")
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "rc6_ppi_scanner_primary_latest.json"
            path.write_bytes(b"PRIVATE_DO_NOT_INHERIT")
            path.chmod(0o600)

            def fork_snapshot(candidate, maximum, consumer, expected):
                self.assertEqual(candidate, path)
                pid = os.fork()
                if pid == 0:
                    try:
                        for descriptor in os.listdir("/proc/self/fd"):
                            if not descriptor.isdecimal() or int(descriptor) < 3:
                                continue
                            try:
                                actual = os.fstat(int(descriptor))
                            except OSError:
                                continue
                            if (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino):
                                os._exit(1)  # privileged FD leaked to child
                        os._exit(0)
                    except BaseException:
                        os._exit(2)
                _, status = os.waitpid(pid, 0)
                self.assertTrue(os.WIFEXITED(status))
                self.assertEqual(os.WEXITSTATUS(status), 0,
                                 "root-open private sidecar FD inherited across fork")
                return "NO_VERIFICADO"

            with patch.object(module, "_verify_sidecar_access", side_effect=fork_snapshot):
                row = module.inspect_private_receipt(
                    path, 1024, effective_user="UID1000", now=NOW,
                    expected_owner_uid=os.getuid())
            self.assertEqual(row["status"], "PRESENT_REGULAR")
            self.assertEqual(row["consumer_access"], "NO_VERIFICADO")

    def test_private_sidecar_missing_bad_mode_and_owner_never_claim_access(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "receipt.json"
            absent = module.inspect_private_receipt(
                path, 1024, effective_user="ROOT", now=NOW)
            self.assertEqual(absent["status"], "MISSING")
            self.assertEqual(absent["consumer_access"], "NO_VERIFICADO")
            path.write_text("private", encoding="utf-8")
            path.chmod(0o644)
            with patch.object(module, "_verify_sidecar_access", side_effect=AssertionError("called")):
                wrong_mode = module.inspect_private_receipt(
                    path, 1024, effective_user="ROOT", now=NOW,
                    expected_owner_uid=os.getuid())
            self.assertEqual(wrong_mode["status"], "UNSAFE_MODE")
            self.assertEqual(wrong_mode["private_mode"], "OTHER")
            self.assertEqual(wrong_mode["consumer_access"], "NO_VERIFICADO")
            path.chmod(0o600)
            with patch.object(module, "_verify_sidecar_access", side_effect=AssertionError("called")):
                wrong_owner = module.inspect_private_receipt(
                    path, 1024, effective_user="ROOT", now=NOW,
                    expected_owner_uid=os.getuid() + 5)
            self.assertEqual(wrong_owner["status"], "OWNER_MISMATCH")
            self.assertEqual(wrong_owner["owner_class"], "OTHER")
            self.assertEqual(wrong_owner["consumer_access"], "NO_VERIFICADO")

    def test_private_sidecar_alias_size_hardlink_and_unknown_reader_fail_closed(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            path = root / "receipt.json"
            path.write_bytes(b"secret")
            path.chmod(0o600)
            alias = root / "alias.json"
            alias.symlink_to(path)
            symlink = module.inspect_private_receipt(alias, 128, effective_user="ROOT", now=NOW)
            self.assertEqual(symlink["status"], "UNAVAILABLE_OR_ALIAS")
            oversize = module.inspect_private_receipt(path, 3, effective_user="ROOT", now=NOW)
            self.assertEqual(oversize["status"], "SIZE_LIMIT")
            self.assertEqual(module._verify_sidecar_access(path, 128, "UNKNOWN", path.stat()),
                             "NO_VERIFICADO")
            hardlink = root / "duplicate.json"
            os.link(path, hardlink)
            unsafe = module.inspect_private_receipt(path, 128, effective_user="ROOT", now=NOW)
            self.assertEqual(unsafe["status"], "UNSAFE_FILE")

    def test_runner_sanitizer_rejects_access_ok_without_verified_reader_identity(self):
        # A473-EXT: exercise the actual workflow inline Python sanitizer rather
        # than merely duplicating its boolean predicate in this test.
        workflow = WORKFLOW.read_text(encoding="utf-8")
        marker = 'REMOTE_RC="$remote_rc" python3 - <<\'PY\''
        self.assertIn(marker, workflow)
        inline = textwrap.dedent(workflow.split(marker, 1)[1].split("\n          PY", 1)[0]).lstrip("\n")
        compile(inline, "a420-runner-sanitizer", "exec")

        files = [
            {"role": role, "status": "MISSING", "mtime_age_bucket": "UNKNOWN"}
            for role, _, _, _ in module.FILES[:-1]
        ]
        files.append({"role": "PPI_SCANNER_PRIVATE_RECEIPT", "status": "PRESENT_REGULAR",
                      "mtime_age_bucket": "WITHIN_5M", "private_mode": "PRIVATE_0600",
                      "owner_class": "UID1000", "consumer_access": "ACCESS_OK",
                      "access_scope": "HOST_DAC_ONLY"})
        units = [
            {"unit": "CYCLE_TIMER", "query": "READ_ONLY", "load": "loaded",
             "active": "active", "result": "success", "main_exit": "ZERO",
             "effective_user": "NOT_APPLICABLE", "dynamic_user": "NOT_APPLICABLE",
             "dropins": "NOT_APPLICABLE"},
            {"unit": "CYCLE_SERVICE", "query": "READ_ONLY", "load": "loaded",
             "active": "active", "result": "success", "main_exit": "ZERO",
             "effective_user": "ROOT", "dynamic_user": "NO", "dropins": "NONE"},
        ]
        baseline = {"schema": module.SCHEMA, "read_only": True,
                    "host_metadata_only": True, "database_opened": False,
                    "provider_calls": 0, "order_routes_called": False,
                    "ppi_watch_touched": False, "paper_ready": False,
                    "diagnostic_only": True, "files": files, "units": units}

        def actual_runner_probe(payload):
            with tempfile.TemporaryDirectory() as work:
                evidence = Path(work) / "evidence"
                evidence.mkdir()
                (evidence / ".raw.json").write_text(json.dumps(payload), encoding="utf-8")
                process = subprocess.run([sys.executable, "-c", inline], cwd=work,
                                         env={"REMOTE_RC": "0"}, capture_output=True,
                                         text=True, check=False, timeout=4)
                self.assertEqual(process.returncode, 0, process.stderr)
                return json.loads((evidence / "source_cycle_layout.json").read_text())

        self.assertEqual(actual_runner_probe(baseline)["probe_status"], "METADATA_ONLY")
        # Negative fixtures must fail closed even though sidecar mode and
        # ownership fields remain correctly formatted.
        invalid = [
            ("effective_user", "OTHER"),
            ("effective_user", "DYNAMIC"),
            ("effective_user", "UNKNOWN"),
            ("dynamic_user", "YES"),
            ("dynamic_user", "UNKNOWN"),
            ("query", "UNAVAILABLE"),
            ("load", "not-found"),
        ]
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                payload = copy.deepcopy(baseline)
                payload["units"][1][key] = value
                self.assertEqual(actual_runner_probe(payload)["probe_status"], "NO_VERIFICADO")

        # A failed/unknown DAC receipt is allowed as a negative *diagnostic*
        # even when a unit identity is unknown: never promote PAPER readiness.
        negative = copy.deepcopy(baseline)
        negative["files"][-1]["consumer_access"] = "READ_FAILED"
        negative["units"][1]["effective_user"] = "UNKNOWN"
        artifact = actual_runner_probe(negative)
        self.assertEqual(artifact["probe_status"], "METADATA_ONLY")
        self.assertFalse(artifact["paper_ready"])

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
