"""A491 guard tests: metadata-only host probe and independent public sanitizer."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from scripts import rc6_a491_retention_host_metadata_ro as probe


SCRIPT = Path(probe.__file__)
ROOT = SCRIPT.parents[1]
WORKFLOW = ROOT / ".github/workflows/rc6-a491-retention-metadata-readonly.yml"


def sample():
    units = [{"role": role, "load": "loaded", "active": "inactive",
              "unit_file": "masked" if role.startswith("A3_") else "static",
              "result": "success"} for role, _ in probe.UNITS]
    files = [{"role": role, "status": "PRESENT_REGULAR", "size_bytes": 4096,
              "sidecars": {side: "MISSING" for side in probe.SIDECARS}}
             for role, _, _ in probe.DATABASES]
    return {"schema": probe.SCHEMA, "state": "METADATA_ONLY",
            **probe.INVARIANTS, "files": files,
            "resources": {"mem_available_bytes": probe.MIN_MEMORY,
                          "disk_free_root_bytes": probe.MIN_DISK,
                          "disk_total_root_bytes": probe.MIN_DISK * 2,
                          "disk_free_data_bytes": probe.MIN_DISK * 2,
                          "disk_total_data_bytes": probe.MIN_DISK * 4,
                          "memory_gate": "PASS", "disk_gate": "PASS"},
            "units": units, "a3_timer_gate": "PASS", "eod_writer_gate": "PASS"}


def raw(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")


def fd_sample():
    return {"schema": probe.FD_SCHEMA, "state": "PROCESS_METADATA_ONLY",
            **probe.FD_INVARIANTS,
            "files": {kind: "PRESENT" for kind in probe.FD_FILES},
            "activity": {kind: "NO_METADATA_CHANGE_AT_ENDPOINTS"
                         for kind in probe.FD_FILES},
            "containers": [
                {"role": role, "runtime": "RUNNING",
                 "matches": {kind: "NO_MATCH_IN_TARGET" for kind in probe.FD_FILES}}
                for role, _ in probe.FD_CONTAINERS]}


class LocalMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        (self.data / "paper_v17").mkdir(parents=True)
        self.history = self.data / "market_history.db"
        self.observer = self.data / "paper_v17/observer_v17.db"
        self.history.write_bytes(b"A" * 4096)
        self.observer.write_bytes(b"B" * 4096)
        self.database_patch = mock.patch.object(probe, "DATABASES", (
            ("HISTORY_DB", self.history, 1024**3),
            ("PAPER_OBSERVER_DB", self.observer, 4 * 1024**3)))
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)

    def test_stat_metadata_never_opens_a_database_or_sidecar(self):
        original = os.open
        calls = []

        def guarded_open(name, flags, **kwargs):
            calls.append((str(name), flags))
            if str(name) in {self.history.name, self.observer.name, self.history.name + "-wal"}:
                self.fail("SQLite file opened")
            return original(name, flags, **kwargs)

        with mock.patch.object(probe.os, "open", side_effect=guarded_open):
            result = probe._scan()
        self.assertEqual([item["status"] for item in result], ["PRESENT_REGULAR"] * 2)
        self.assertEqual([item["size_bytes"] for item in result], [4096, 4096])
        self.assertTrue(calls)
        self.assertTrue(all(flags & os.O_PATH for _, flags in calls))

    def test_wal_and_shm_metadata_present_without_content_read(self):
        Path(str(self.history) + "-wal").write_bytes(b"WAL")
        Path(str(self.history) + "-shm").write_bytes(b"SHM")
        result = probe._stability(probe._scan(), probe._scan())
        self.assertEqual(result[0]["sidecars"], {
            "WAL": "PRESENT", "SHM": "PRESENT", "JOURNAL": "MISSING"})

    def test_wal_appearance_between_stats_fails_closed(self):
        before = probe._scan()
        Path(str(self.history) + "-wal").write_bytes(b"WAL")
        after = probe._scan()
        result = probe._stability(before, after)
        self.assertEqual(result[0]["sidecars"]["WAL"], "CHANGED_DURING_PROBE")

    def test_database_change_between_stats_fails_closed(self):
        before = probe._scan()
        replacement = self.data / "replacement.db"
        replacement.write_bytes(b"C" * 4096)
        replacement.replace(self.history)
        result = probe._stability(before, probe._scan())
        self.assertEqual(result[0]["status"], "CHANGED_DURING_PROBE")
        self.assertIsNone(result[0]["size_bytes"])

    def test_final_symlink_never_followed(self):
        self.history.unlink()
        self.history.symlink_to(self.observer)
        self.assertEqual(probe._scan()[0]["status"], "UNSAFE_FILE")

    def test_parent_symlink_never_followed(self):
        alias = self.root / "link"
        alias.symlink_to(self.data, target_is_directory=True)
        status, size, _ = probe._stat_regular(alias / "market_history.db", maximum=2**31)
        self.assertEqual(status, "UNSAFE_PATH")
        self.assertIsNone(size)

    def test_hardlink_never_trusted(self):
        os.link(self.history, self.data / "same.db")
        self.assertEqual(probe._scan()[0]["status"], "UNSAFE_FILE")

    def test_db_size_over_budget_fails_closed(self):
        status, size, _ = probe._stat_regular(self.history, maximum=1000)
        self.assertEqual((status, size), ("SIZE_LIMIT", None))

    def test_history_oversize_mocked_stat_fails_closed_no_large_file(self):
        # Simulate one byte over A330's actual max, without creating a >1GiB file.
        cap = 1024**3
        self.assertEqual(probe.DATABASES[0][2], cap)
        observed = []

        def simulated_stat(path, *, maximum, minimum=0):
            observed.append((path, maximum, minimum))
            if path == self.history:
                self.assertEqual((maximum, minimum), (cap, 4096))
                simulated_size = cap + 1
                if minimum <= simulated_size <= maximum:
                    return ("PRESENT_REGULAR", simulated_size, (1, 2, simulated_size))
                return ("SIZE_LIMIT", None, None)
            if path == self.observer:
                return ("PRESENT_REGULAR", 4096, (1, 3, 4096))
            return ("MISSING", None, None)

        with mock.patch.object(probe, "_stat_regular", side_effect=simulated_stat):
            rows = probe._stability(probe._scan(), probe._scan())
        self.assertTrue(any(path == self.history for path, _, _ in observed))
        self.assertEqual((rows[0]["status"], rows[0]["size_bytes"]),
                         ("SIZE_LIMIT", None))
        self.assertEqual(rows[1]["status"], "PRESENT_REGULAR")
        payload = sample()
        payload["files"][0].update(status="SIZE_LIMIT", size_bytes=None)
        receipt = probe.sanitize(raw(payload), 0)
        self.assertEqual(receipt["state"], "METADATA_ONLY")
        self.assertEqual(receipt["files"][0]["status"], "SIZE_LIMIT")
        self.assertEqual(receipt["files"][0]["size_band"], "UNKNOWN")
        # Even a hostile host payload claiming PRESENT_REGULAR over the cap
        # cannot promote the public receipt to a false passing condition.
        payload["files"][0].update(status="PRESENT_REGULAR", size_bytes=cap + 1)
        self.assertEqual(probe.sanitize(raw(payload), 0)["state"], "NO_VERIFICADO")

    def test_unit_parser_is_fixed_and_no_shell(self):
        expected = "LoadState=loaded\nActiveState=inactive\nUnitFileState=masked\nResult=success\n"

        def runner(argv, **kwargs):
            self.assertEqual(argv[:2], ["systemctl", "show"])
            self.assertFalse(kwargs.get("shell", False))
            return subprocess.CompletedProcess(argv, 0, expected, "")

        with mock.patch.object(probe.subprocess, "run", side_effect=runner):
            row = probe._unit("A3_DAILY_TIMER", probe.UNITS[0][1], probe.time.monotonic() + 5)
        self.assertEqual(row["unit_file"], "masked")

    def test_bad_systemctl_field_or_timeout_becomes_unknown(self):
        with mock.patch.object(probe.subprocess, "run", side_effect=subprocess.TimeoutExpired("systemctl", 2)):
            row = probe._unit("A3_DAILY_TIMER", probe.UNITS[0][1], probe.time.monotonic() + 5)
        self.assertEqual(row["active"], "UNKNOWN")
        self.assertEqual(probe._timer_gate([row] * 3), "NO_VERIFICADO")

    def test_duplicate_systemctl_key_cannot_override_active_or_masked(self):
        for duplicate in ("ActiveState=active", "UnitFileState=enabled", "Result=exit-code"):
            output = ("LoadState=loaded\nActiveState=inactive\nUnitFileState=masked\n"
                      "Result=success\n" + duplicate + "\n")
            with mock.patch.object(probe.subprocess, "run", return_value=subprocess.CompletedProcess(
                    ["systemctl"], 0, output, "")):
                row = probe._unit("A3_DAILY_TIMER", probe.UNITS[0][1], probe.time.monotonic() + 5)
            self.assertEqual(row["active"], "UNKNOWN")
        malformed = "LoadState=loaded\nActiveState=inactive\nUnitFileState=masked\nResult=success\nraw-private-line"
        with mock.patch.object(probe.subprocess, "run", return_value=subprocess.CompletedProcess(
                ["systemctl"], 0, malformed, "")):
            row = probe._unit("A3_DAILY_TIMER", probe.UNITS[0][1], probe.time.monotonic() + 5)
        self.assertEqual(row["active"], "UNKNOWN")

    def test_named_service_active_cannot_claim_writer_idle(self):
        units = sample()["units"]
        units[3]["active"] = "active"
        self.assertEqual(probe._eod_gate(units), "FAIL")
        units[3]["active"] = "inactive"
        units[4]["active"] = "active"
        self.assertEqual(probe._eod_gate(units), "FAIL")
        units[4]["active"] = "failed"
        self.assertEqual(probe._eod_gate(units), "NO_VERIFICADO")
        units[0]["unit_file"] = "enabled"
        self.assertEqual(probe._timer_gate(units), "FAIL")

    def test_wrong_load_does_not_promote_timer_or_eod_gate(self):
        units = sample()["units"]
        units[0]["load"] = "UNKNOWN"
        self.assertEqual(probe._timer_gate(units), "NO_VERIFICADO")
        units[0]["load"] = "masked"
        self.assertEqual(probe._timer_gate(units), "PASS")
        units[0]["unit_file"] = "enabled"
        self.assertEqual(probe._timer_gate(units), "NO_VERIFICADO")
        units[0]["unit_file"] = "masked"
        units[0]["active"] = "active"
        self.assertEqual(probe._timer_gate(units), "FAIL")
        units[0]["load"] = "loaded"
        units[0]["active"] = "inactive"
        units[3]["load"] = "UNKNOWN"
        self.assertEqual(probe._eod_gate(units), "NO_VERIFICADO")
        units[3]["load"] = "loaded"
        units[4]["load"] = "UNKNOWN"
        self.assertEqual(probe._eod_gate(units), "NO_VERIFICADO")

    def test_host_metadata_synthetic_roundtrip_without_sqlite(self):
        rows = sample()["units"]
        with (mock.patch.object(probe, "_available_memory", return_value=probe.MIN_MEMORY),
              mock.patch.object(probe, "_disk_space", side_effect=[
                  (probe.MIN_DISK, probe.MIN_DISK * 2),
                  (probe.MIN_DISK * 2, probe.MIN_DISK * 4)]),
              mock.patch.object(probe, "_unit", side_effect=rows)):
            receipt = probe._receipt()
        self.assertEqual(probe.sanitize(raw(receipt), 0)["state"], "METADATA_ONLY")
        self.assertEqual(receipt["a3_timer_gate"], "PASS")
        self.assertEqual(receipt["eod_writer_gate"], "PASS")
        self.assertFalse(receipt["paper_zero_checked"])

    def test_missing_memory_and_disk_cannot_pass(self):
        self.assertEqual(probe._gate(None, probe.MIN_MEMORY), "NO_VERIFICADO")
        self.assertEqual(probe._gate(probe.MIN_MEMORY - 1, probe.MIN_MEMORY), "FAIL")
        self.assertEqual(probe._gate(probe.MIN_DISK - 1, probe.MIN_DISK), "FAIL")

    def test_data_mount_space_cannot_substitute_root_free_space(self):
        value = sample()
        value["resources"]["disk_free_root_bytes"] = probe.MIN_DISK - 1
        self.assertEqual(probe.sanitize(raw(value), 0)["state"], "NO_VERIFICADO")
        value["resources"]["disk_gate"] = "FAIL"
        self.assertEqual(probe.sanitize(raw(value), 0)["state"], "METADATA_ONLY")


class SanitizerTests(unittest.TestCase):
    def test_strict_positive_preserves_metadata_and_never_grants_capture(self):
        result = probe.sanitize(raw(sample()), 0)
        self.assertEqual(result["state"], "METADATA_ONLY")
        self.assertEqual(result["files"][0]["size_band"], "BELOW_256M")
        self.assertEqual(result["resources"]["memory_band"], "512M_TO_1G")
        self.assertEqual(result["resources"]["root_disk_band"], "4G_TO_8G")
        payload = json.dumps(result)
        for private_key in ("size_bytes", "disk_free_root_bytes", "disk_total_root_bytes",
                            "disk_free_data_bytes", "disk_total_data_bytes",
                            "mem_available_bytes"):
            self.assertNotIn(private_key, payload)
        self.assertFalse(result["database_opened"])
        self.assertFalse(result["a330_capture_certified"])
        self.assertFalse(result["host_purge_allowed"])
        self.assertFalse(result["paper_zero_checked"])

    def test_failure_exit_classes_are_private_and_ambiguous(self):
        for rc, expected in [(124, "EXIT_124_TIMEOUT_OR_REMOTE"), (255, "SSH_255_TRANSPORT_OR_REMOTE"),
                             (2, "REMOTE_EXIT_NONZERO"), (0, "RECEIPT_INVALID")]:
            with self.subTest(rc=rc):
                result = probe.sanitize(b"HOST_PATH=/private/token", rc)
                self.assertEqual(result["state"], "NO_VERIFICADO")
                self.assertEqual(result["failure_class"], expected)
                self.assertNotIn("/private", json.dumps(result))

    def test_extra_free_text_secret_and_ticker_rejected(self):
        value = sample()
        value["ticker"] = "GGAL"
        self.assertEqual(probe.sanitize(raw(value), 0)["state"], "NO_VERIFICADO")
        value.pop("ticker")
        value["files"][0]["path"] = "/private/history.db"
        self.assertEqual(probe.sanitize(raw(value), 0)["state"], "NO_VERIFICADO")

    def test_wrong_unit_role_or_unknown_unit_state_rejected(self):
        for name, bad in [("role", "MALICIOUS"), ("load", "SECRET_UNIT_PATH")]:
            value = sample()
            value["units"][0][name] = bad
            self.assertEqual(probe.sanitize(raw(value), 0)["state"], "NO_VERIFICADO")

    def test_false_positive_resource_gate_rejected(self):
        value = sample()
        value["resources"]["mem_available_bytes"] = probe.MIN_MEMORY - 1
        self.assertEqual(probe.sanitize(raw(value), 0)["state"], "NO_VERIFICADO")
        value = sample()
        value["resources"]["disk_free_root_bytes"] = probe.MIN_DISK - 1
        self.assertEqual(probe.sanitize(raw(value), 0)["state"], "NO_VERIFICADO")

    def test_false_capture_or_purge_authority_rejected(self):
        for key, bad in [("a330_capture_certified", True), ("host_purge_allowed", True),
                         ("database_opened", True), ("host_db_writes", True),
                         ("ppi_watch_touched", True), ("physical_host_delete_count", 1)]:
            value = sample()
            value[key] = bad
            self.assertEqual(probe.sanitize(raw(value), 0)["state"], "NO_VERIFICADO")

    def test_bool_not_int_and_negative_size_rejected(self):
        for invalid in [True, -1, probe.MAX_NUMBER + 1, "4096"]:
            value = sample()
            value["files"][0]["size_bytes"] = invalid
            self.assertEqual(probe.sanitize(raw(value), 0)["state"], "NO_VERIFICADO")

    def test_duplicate_keys_nonfinite_binary_and_oversize_rejected(self):
        good = raw(sample())
        variants = [good[:-1] + b',"schema":"duplicate"}',
                    good.replace(b'"mem_available_bytes":536870912', b'"mem_available_bytes":NaN'),
                    b"\xff", b"X" * (probe.MAX_RAW + 1)]
        for variant in variants:
            with self.subTest(size=len(variant)):
                self.assertEqual(probe.sanitize(variant, 0)["state"], "NO_VERIFICADO")

    def test_wrong_counts_or_sidecar_schema_rejected(self):
        value = sample()
        value["files"].pop()
        self.assertEqual(probe.sanitize(raw(value), 0)["state"], "NO_VERIFICADO")
        value = sample()
        value["files"][0]["sidecars"]["WAL"] = "SQLITE_WAL_CONTENTS"
        self.assertEqual(probe.sanitize(raw(value), 0)["state"], "NO_VERIFICADO")

    def test_runner_cli_writes_only_sanitized_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_path = root / ".a491-raw.json"
            output = root / "a491-sanitized.json"
            raw_path.write_bytes(raw(sample()))
            result = subprocess.run([sys.executable, str(SCRIPT), "sanitize",
                                     "--raw", str(raw_path), "--out", str(output),
                                     "--remote-rc", "0"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(output.read_text())
            self.assertEqual(receipt["state"], "METADATA_ONLY")
            self.assertIn("size_band", receipt["files"][0])
            self.assertNotIn("size_bytes", output.read_text())
            self.assertNotIn(str(root), output.read_text())

    def test_band_threshold_edges_disclose_no_exact_bytes(self):
        mib = 1024**2
        for size, expect in [(256*mib-1, "BELOW_256M"),
                             (256*mib, "256M_TO_512M"),
                             (512*mib, "512M_TO_1G"),
                             (1024*mib, "1G_TO_2G"),
                             (2*1024*mib, "2G_TO_4G")]:
            value = sample()
            # The observer role has the 4GiB bound required for upper bands;
            # history itself is capped at 1GiB to match A330.
            file_index = 0 if size <= 1024**3 else 1
            value["files"][file_index]["size_bytes"] = size
            result = probe.sanitize(raw(value), 0)
            self.assertEqual(result["state"], "METADATA_ONLY")
            self.assertEqual(result["files"][file_index]["size_band"], expect)
        for count in (probe.MIN_MEMORY-1, probe.MIN_MEMORY):
            value = sample()
            value["resources"]["mem_available_bytes"] = count
            value["resources"]["memory_gate"] = probe._gate(count, probe.MIN_MEMORY)
            output = probe.sanitize(raw(value), 0)
            self.assertEqual(output["state"], "METADATA_ONLY")
            self.assertEqual(output["resources"]["memory_band"],
                             "256M_TO_512M" if count < probe.MIN_MEMORY else "512M_TO_1G")


class ObserverFdTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.observer = root / "observer_v17.db"
        self.observer.write_bytes(b"synthetic db" * 512)
        self.wal = Path(str(self.observer) + "-wal")
        self.shm = Path(str(self.observer) + "-shm")
        self.wal.write_bytes(b"synthetic wal")
        self.shm.write_bytes(b"synthetic shm")
        self.patch_databases = mock.patch.object(probe, "DATABASES", (
            ("HISTORY_DB", root / "unused.db", 1024**3),
            ("PAPER_OBSERVER_DB", self.observer, 4 * 1024**3)))
        self.patch_databases.start()
        self.addCleanup(self.patch_databases.stop)

    def test_real_proc_fd_stat_matches_open_synthetic_fixture_only(self):
        info = self.observer.stat()
        with self.observer.open("rb") as handle:
            # A managed sandbox can expose a /proc belonging to a different
            # PID namespace even when /proc/<our pid> happens to exist.
            try:
                entry = os.stat(f"/proc/{os.getpid()}/fd/{handle.fileno()}")
            except OSError:
                self.skipTest("procfs PID namespace differs from process")
            if (entry.st_dev, entry.st_ino) != (info.st_dev, info.st_ino):
                self.skipTest("procfs PID maps to another process")
            matches, _ = probe._pid_fd_matches(os.getpid(),
                {"DB": (info.st_dev, info.st_ino), "WAL": (0, 0),
                 "SHM": (0, 0)}, time.monotonic() + 5)
        self.assertIn("DB", matches)
        self.assertNotIn("WAL", matches)
        self.assertNotIn("SHM", matches)

    def test_docker_top_exact_pid_only_and_rejects_untrusted_text(self):
        valid = subprocess.CompletedProcess([], 0, "PID\n321\n322\n", "")
        with mock.patch.object(probe.subprocess, "run", return_value=valid) as runner:
            self.assertEqual(probe._docker_pids("porota_production_observer",
                                                time.monotonic() + 8), [321, 322])
            self.assertEqual(runner.call_args.args[0],
                             ["docker", "top", "porota_production_observer", "-eo", "pid"])
        for bad in ("PID\n321\n321\n", "PID\n321  --privileged\n",
                    "PID\n9999999999\n", "USER PID\n123\n", "PID\n"):
            with mock.patch.object(probe.subprocess, "run", return_value=
                                   subprocess.CompletedProcess([], 0, bad, "")):
                self.assertIsNone(probe._docker_pids("porota_production_dashboard",
                                                       time.monotonic() + 8))
        self.assertIsNone(probe._docker_pids("unknown_app", time.monotonic() + 8))

    def test_docker_inspect_fixed_runtime_state_never_exposes_config(self):
        for stdout, expected in (("true\n", "RUNNING"),
                                 ("false\n", "STOPPED"),
                                 ("/secrets/credentials", "NO_VERIFICADO")):
            with mock.patch.object(probe.subprocess, "run", return_value=
                                   subprocess.CompletedProcess([], 0, stdout, "")) as runner:
                self.assertEqual(probe._docker_runtime("porota_production_observer",
                                                       time.monotonic() + 5), expected)
                self.assertEqual(runner.call_args.args[0],
                                 ["docker", "inspect", "--type", "container",
                                  "--format", "{{.State.Running}}",
                                  "porota_production_observer"])
        with mock.patch.object(probe.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 1, "",
                                   "Error: No such object: porota_production_dashboard\n")):
            self.assertEqual(probe._docker_runtime("porota_production_dashboard",
                                                   time.monotonic() + 5), "ABSENT")
        self.assertEqual(probe._docker_runtime("other_service", time.monotonic() + 5),
                         "NO_VERIFICADO")

    def test_two_probes_identify_live_holder_only_in_exact_named_container(self):
        with (mock.patch.object(probe, "_docker_runtime", return_value="RUNNING"),
              mock.patch.object(probe, "_proc_start_tick", return_value=1234),
              mock.patch.object(probe, "_docker_pids",
                                side_effect=lambda name, deadline: [21] if
                                name == "porota_production_observer" else [22]),
              mock.patch.object(probe, "_pid_fd_matches",
                                side_effect=lambda pid, ids, deadline:
                                ({"DB", "WAL", "SHM"}, True) if pid == 21 else (set(), True))):
            result = probe._fd_receipt()
        self.assertEqual(result["state"], "PROCESS_METADATA_ONLY")
        self.assertEqual(result["containers"][0]["matches"],
                         {kind: "MATCH_BOTH_SAMPLES" for kind in probe.FD_FILES})
        self.assertEqual(result["containers"][1]["matches"],
                         {kind: "NO_MATCH_IN_TARGET" for kind in probe.FD_FILES})
        self.assertFalse(result["all_processes_covered"])
        self.assertFalse(result["database_opened"])
        self.assertFalse(result["host_purge_allowed"])
        self.assertEqual(probe.sanitize_fd(raw(result), 0), result)

    def test_partial_process_visibility_or_missing_sidecar_is_never_negative(self):
        self.wal.unlink()
        with mock.patch.object(probe, "_docker_pids", return_value=None):
            result = probe._fd_receipt()
        self.assertEqual(result["files"]["WAL"], "MISSING")
        for row in result["containers"]:
            self.assertEqual(row["matches"]["WAL"], "NO_VERIFICADO")
            self.assertEqual(row["matches"]["DB"], "NO_VERIFICADO")
        self.assertEqual(probe.sanitize_fd(raw(result), 0)["state"],
                         "PROCESS_METADATA_ONLY")

    def test_match_in_one_sample_only_does_not_claim_continuity(self):
        def observations(is_positive):
            return [{"role": role, "matches": {kind: ("MATCH" if
                                                 is_positive else "NO_MATCH_IN_TARGET")
                                               for kind in probe.FD_FILES}}
                    for role, _ in probe.FD_CONTAINERS]

        with (mock.patch.object(probe, "_docker_runtime", return_value="RUNNING"),
              mock.patch.object(probe, "_container_fd_sample",
                                side_effect=[observations(True), observations(False)])):
            result = probe._fd_receipt()
        self.assertEqual(result["containers"][0]["matches"]["WAL"],
                         "MATCH_ONE_SAMPLE")
        self.assertFalse(result["continuous_ownership_proven"])

    def test_stopped_container_never_uses_a_fd_hit_as_running_attribution(self):
        observation = [{"role": role, "matches": {kind: "MATCH"
                                                    for kind in probe.FD_FILES}}
                       for role, _ in probe.FD_CONTAINERS]
        with (mock.patch.object(probe, "_docker_runtime", return_value="STOPPED"),
              mock.patch.object(probe, "_container_fd_sample", return_value=observation)):
            result = probe._fd_receipt()
        self.assertEqual(result["containers"][0]["runtime"], "STOPPED")
        self.assertEqual(result["containers"][0]["matches"]["DB"],
                         "NO_VERIFICADO")

    def test_pid_reused_during_fd_scan_cannot_produce_match(self):
        calls = 0

        def moved_start(pid):
            nonlocal calls
            calls += 1
            return calls

        with (mock.patch.object(probe, "_docker_pids", return_value=[17]),
              mock.patch.object(probe, "_proc_start_tick", side_effect=moved_start),
              mock.patch.object(probe, "_pid_fd_matches",
                                return_value=({"DB", "WAL", "SHM"}, True))):
            sample = probe._container_fd_sample(
                {kind: (1, 1) for kind in probe.FD_FILES}, time.monotonic() + 5)
        self.assertTrue(all(value == "NO_VERIFICADO" for row in sample
                            for value in row["matches"].values()))

    def test_replaced_wal_inode_fails_closed_even_with_earlier_fd_match(self):
        calls = 0

        def sample_fd(ids, deadline):
            nonlocal calls
            calls += 1
            if calls == 2:
                replacement = self.observer.parent / "replacement"
                replacement.write_bytes(b"replacement WAL")
                replacement.replace(self.wal)
            return [{"role": role, "matches": {kind: "MATCH"
                                                for kind in probe.FD_FILES}}
                    for role, _ in probe.FD_CONTAINERS]

        with mock.patch.object(probe, "_container_fd_sample", side_effect=sample_fd):
            result = probe._fd_receipt()
        self.assertEqual(result["files"]["WAL"], "NO_VERIFICADO")
        self.assertEqual(result["containers"][0]["matches"]["WAL"],
                         "NO_VERIFICADO")

    def test_unscanned_fd_caps_and_permission_do_not_claim_absence(self):
        with mock.patch.object(probe.os, "scandir", side_effect=PermissionError()):
            matches, complete = probe._pid_fd_matches(
                os.getpid(), {"DB": (1, 1)}, time.monotonic() + 5)
        self.assertEqual(matches, set())
        self.assertFalse(complete)
        with (mock.patch.object(probe, "_docker_pids", return_value=[17]),
              mock.patch.object(probe, "_pid_fd_matches", return_value=(set(), False))):
            results = probe._container_fd_sample({kind: (1, 1)
                                                  for kind in probe.FD_FILES},
                                                 time.monotonic() + 5)
        self.assertTrue(all(value == "NO_VERIFICADO" for row in results
                            for value in row["matches"].values()))

    def test_fd_limit_and_expired_deadline_fail_closed(self):
        entry = mock.Mock()
        entry.stat.return_value = mock.Mock(st_dev=10, st_ino=20,
                                            st_mode=stat.S_IFREG | 0o600)
        listing = mock.MagicMock()
        listing.__enter__.return_value = iter([entry] * (probe.FD_MAX_PER_PID + 1))
        with mock.patch.object(probe.os, "scandir", return_value=listing):
            hits, complete = probe._pid_fd_matches(
                123, {"DB": (10, 20)}, time.monotonic() + 10)
        self.assertIn("DB", hits)
        self.assertFalse(complete)
        listing.__enter__.return_value = iter([entry])
        with mock.patch.object(probe.os, "scandir", return_value=listing):
            hits, complete = probe._pid_fd_matches(
                123, {"DB": (10, 20)}, time.monotonic() - 1)
        self.assertEqual(hits, set())
        self.assertFalse(complete)

    def test_docker_pid_membership_race_discards_even_positive_inode_hit(self):
        # A reused host PID could otherwise make an unrelated service look
        # like the holder of an observer DB handle.
        calls = 0

        def unstable_pids(name, deadline):
            nonlocal calls
            calls += 1
            return [17] if calls % 2 else [18]

        with (mock.patch.object(probe, "_proc_start_tick", return_value=1234),
              mock.patch.object(probe, "_docker_pids", side_effect=unstable_pids),
              mock.patch.object(probe, "_pid_fd_matches",
                                return_value=({"DB", "WAL", "SHM"}, True))):
            result = probe._container_fd_sample(
                {kind: (1, 1) for kind in probe.FD_FILES}, time.monotonic() + 5)
        self.assertTrue(all(value == "NO_VERIFICADO" for row in result
                            for value in row["matches"].values()))

    def test_strict_fd_public_contract_rejects_fake_purge_pid_path_and_conflicts(self):
        value = fd_sample()
        self.assertEqual(probe.sanitize_fd(raw(value), 0), value)
        for key, invalid in (("all_processes_covered", True),
                             ("host_purge_allowed", True),
                             ("database_opened", True)):
            changed = fd_sample()
            changed[key] = invalid
            self.assertEqual(probe.sanitize_fd(raw(changed), 0)["state"],
                             "NO_VERIFICADO")
        for key, secret in (("pid", 31337), ("path", "/secrets/observer"),
                            ("command", "docker rm")):
            changed = fd_sample()
            changed["containers"][0][key] = secret
            output = probe.sanitize_fd(raw(changed), 0)
            self.assertEqual(output["state"], "NO_VERIFICADO")
            self.assertNotIn(str(secret), json.dumps(output))
        changed = fd_sample()
        changed["files"]["WAL"] = "MISSING"
        self.assertEqual(probe.sanitize_fd(raw(changed), 0)["state"],
                         "NO_VERIFICADO")
        changed = fd_sample()
        changed["containers"][0]["matches"]["SHM"] = "root:password"
        self.assertEqual(probe.sanitize_fd(raw(changed), 0)["state"],
                         "NO_VERIFICADO")

    def test_fd_runner_sanitizes_only_fixed_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_path = root / ".a498-raw.json"
            output = root / "a498-sanitized.json"
            raw_path.write_bytes(raw(fd_sample()))
            command = [sys.executable, str(SCRIPT), "sanitize-fd",
                       "--raw", str(raw_path), "--out", str(output),
                       "--remote-rc", "0"]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text())["state"],
                             "PROCESS_METADATA_ONLY")
            self.assertNotIn(str(root), output.read_text())
            command[command.index(str(output))] = str(root / "wrong.json")
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)


class WorkflowSafetyTests(unittest.TestCase):
    def test_exact_three_owned_paths_and_no_schedule(self):
        text = WORKFLOW.read_text()
        for name in (".github/workflows/rc6-a491-retention-metadata-readonly.yml",
                     "scripts/rc6_a491_retention_host_metadata_ro.py",
                     "tests/test_rc6_a491_retention_host_metadata_ro.py"):
            self.assertEqual(text.count("- '" + name + "'"), 2)
        self.assertNotIn("  schedule:", text)
        self.assertIn("github.event_name == 'push'", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("inputs.mode == 'METADATA_ONLY'", text)
        self.assertIn("rc6-retention-offhost-census", text)
        self.assertIn("cancel-in-progress: false", text)

    def test_host_secrets_only_in_non_pr_job_and_runner_artifact_fixed(self):
        text = WORKFLOW.read_text()
        before, after = text.split("  host_metadata:", 1)
        self.assertNotIn("secrets.DO_", before)
        self.assertIn("github.event_name == 'push'", after)
        self.assertIn("sudo -n timeout 25 python3 -B - host-probe", after)
        self.assertIn("--remote-rc \"$remote_rc\"", after)
        self.assertIn("/a491-sanitized.json", after)
        self.assertNotIn("path: ${{ env.RC6_A491_DIR }}/.a491-raw.json", after)
        self.assertIn("ulimit -f 16", after)

    def test_a498_is_separate_postmerge_receipt_and_does_not_enable_mutation(self):
        text = WORKFLOW.read_text()
        self.assertIn("host-observer-fd", text)
        self.assertIn("sanitize-fd", text)
        self.assertIn(".a498-raw.json", text)
        self.assertIn("a498-sanitized.json", text)
        self.assertIn("rc6-a498-observer-fd-metadata-", text)
        self.assertNotIn("path: ${{ env.RC6_A491_DIR }}/.a498-raw.json", text)
        self.assertIn("if: always()", text)
        for forbidden in ("docker rm", "docker stop", "sqlite3", "VACUUM ",
                          "predeploy", "deploy-v2"):
            self.assertNotIn(forbidden, text)

    def test_no_sqlite_open_copy_data_route_or_mutation_api(self):
        source = SCRIPT.read_text()
        for forbidden in ("import sqlite3", "sqlite3.connect", "os.O_RDWR",
                          "os.O_WRONLY", "DELETE FROM", "VACUUM ", "pandas",
                          "tarfile", "shutil.copy", "requests.", "broker."):
            self.assertNotIn(forbidden, source)
        self.assertIn("os.O_PATH", source)
        self.assertIn("follow_symlinks=False", source)
        self.assertIn("a330_capture_certified", source)


if __name__ == "__main__":
    unittest.main()
