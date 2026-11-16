import json
import hashlib
import io
import os
import subprocess
import tempfile
import unittest
import sys
from datetime import datetime, timezone
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts import porota_rootless_host as host

try:
    from scripts import porota_rootless_setup as setup
except ImportError:
    setup = None


class InspectTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(setup, "rootless inspection must exist before migration")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.commands = []

    def fake_run(self, command, **kwargs):
        self.commands.append(tuple(command))
        if command[0] == "/usr/bin/sudo" and command[-1] == "/usr/bin/true":
            return subprocess.CompletedProcess(command, 0, "", "")
        if "inspect" in command:
            name = command[-1]
            raw = [{"Id": "a" * 64, "Name": "/" + name,
                    "Config": {"Env": ["SECRET=must-never-leak"]},
                    "State": {"Status": "running", "Running": True},
                    "Image": "sha256:" + "b" * 64,
                    "Mounts": [{"Destination": "/app/data", "Source": str(self.root / "data")}]}]
            return subprocess.CompletedProcess(command, 0, json.dumps(raw), "")
        return subprocess.CompletedProcess(command, 1, "", "denied secret detail")

    def test_inspection_never_mutates_or_serializes_environment_values(self):
        result = setup.inspect_host(self.root, run=self.fake_run)
        self.assertEqual(result["host_mutations"], 0)
        self.assertNotIn("must-never-leak", json.dumps(result))
        self.assertNotIn("denied secret detail", json.dumps(result))
        self.assertTrue(result["sudo_noninteractive_true"])
        self.assertEqual(len(result["legacy_rc6"]), 3)
        self.assertTrue(all(row["data_mount_matches"] for row in result["legacy_rc6"]))
        self.assertFalse(any(set(c) & {"stop", "start", "restart", "rm", "prune", "enable", "disable"}
                             for c in self.commands))

    def test_only_fixed_rc6_names_are_inspected_on_explicit_rootful_endpoint(self):
        setup.inspect_host(self.root, run=self.fake_run)
        commands = [c for c in self.commands if "inspect" in c]
        self.assertEqual({c[-1] for c in commands}, {
            "porota_production_observer", "porota_production_dashboard", "porota_trading_bot"})
        self.assertTrue(all("unix:///var/run/docker.sock" in c for c in commands))

    def test_missing_sudo_is_reported_without_claiming_legacy_absent(self):
        def unavailable(command, **kwargs):
            raise FileNotFoundError("private diagnostic")
        result = setup.inspect_host(self.root, run=unavailable)
        self.assertFalse(result["sudo_noninteractive_true"])
        self.assertTrue(all(x["status"] == "NO_VERIFICADO" for x in result["legacy_rc6"]))
        self.assertEqual(result["state"], "NO_VERIFICADO")

    def test_streamed_inspection_can_report_real_units_without_local_checkout(self):
        def commands(command, **kwargs):
            if "list-units" in command or "list-unit-files" in command:
                return subprocess.CompletedProcess(command, 0, "porota-preopen-rc6.timer enabled\n", "")
            if "show" in command:
                return subprocess.CompletedProcess(command, 0, "Id=porota-preopen-rc6.timer\nLoadState=loaded\nActiveState=active\nUnitFileState=enabled\n", "")
            raise AssertionError(command)
        result = setup.unit_inventory(self.root, run=commands)
        self.assertEqual(result["status"], "OBSERVED_POLICY_UNAVAILABLE")
        self.assertEqual(result["units"][0]["name"], "porota-preopen-rc6.timer")
        self.assertEqual(result["units"][0]["policy_role"], "UNKNOWN")
        self.assertEqual(result["unmapped_units"], ["porota-preopen-rc6.timer"])

    def test_systemd_inventory_reports_bounded_timer_and_last_service_failure_causality(self):
        service = "porota-preopen-rc6.service"
        timer = "porota-preopen-rc6.timer"
        def commands(command, **kwargs):
            if "list-units" in command or "list-unit-files" in command:
                return subprocess.CompletedProcess(command, 0,
                    f"{service} enabled\n{timer} enabled\n", "")
            if "show" in command:
                unit = command[2]
                if unit == service:
                    payload = (f"Id={service}\nLoadState=loaded\nActiveState=failed\n"
                        "Result=exit-code\nExecMainCode=1\nExecMainStatus=79\n"
                        "ExecMainStartTimestamp=Fri 2026-11-13 17:20:00 UTC\n"
                        "ExecMainExitTimestamp=Fri 2026-11-13 17:20:01 UTC\n"
                        "Environment=SECRET=must-never-leak\n")
                else:
                    payload = (f"Id={timer}\nLoadState=loaded\nActiveState=active\n"
                        "LastTriggerUSec=Fri 2026-11-13 17:20:00 UTC\n"
                        "NextElapseUSecRealtime=Mon 2026-11-16 13:20:00 UTC\n")
                return subprocess.CompletedProcess(command, 0, payload, "")
            raise AssertionError(command)
        result = setup.unit_inventory(self.root, run=commands)
        by_name = {row["name"]: row for row in result["units"]}
        self.assertEqual({key: by_name[service].get(key) for key in (
            "Result", "ExecMainCode", "ExecMainStatus", "ExecMainStartTimestamp", "ExecMainExitTimestamp")},
            {"Result": "exit-code", "ExecMainCode": 1, "ExecMainStatus": 79,
             "ExecMainStartTimestamp": "Fri 2026-11-13 17:20:00 UTC",
             "ExecMainExitTimestamp": "Fri 2026-11-13 17:20:01 UTC"})
        self.assertEqual(by_name[timer].get("LastTriggerUSec"), "Fri 2026-11-13 17:20:00 UTC")
        self.assertEqual(by_name[timer].get("NextElapseUSecRealtime"), "Mon 2026-11-16 13:20:00 UTC")
        self.assertNotIn("must-never-leak", json.dumps(result))

    def test_scheduler_evidence_rejects_untrusted_result_and_timestamp_values(self):
        service, timer = "porota-preopen-rc6.service", "porota-preopen-rc6.timer"
        def commands(command, **kwargs):
            if "list-units" in command or "list-unit-files" in command:
                return subprocess.CompletedProcess(command, 0,
                    f"{service} enabled\n{timer} enabled\n", "")
            if "show" in command:
                payload = ("Result=exit-code private-value\nExecMainCode=-1\n"
                           "ExecMainStatus=79 private-value\n"
                           "ExecMainExitTimestamp=private-value\n") if command[2] == service else (
                           "LastTriggerUSec=private-value\nNextElapseUSecRealtime=Mon 2026-11-16 13:20:00 UTC\n")
                return subprocess.CompletedProcess(command, 0, payload, "")
            raise AssertionError(command)
        result = setup.unit_inventory(self.root, run=commands)
        by_name = {row["name"]: row for row in result["units"]}
        self.assertEqual([by_name[service][key] for key in (
            "Result", "ExecMainCode", "ExecMainStatus", "ExecMainExitTimestamp")], [None] * 4)
        self.assertIsNone(by_name[timer]["LastTriggerUSec"])
        self.assertEqual(by_name[timer]["NextElapseUSecRealtime"], "Mon 2026-11-16 13:20:00 UTC")
        self.assertNotIn("private-value", json.dumps(result))

    def test_unit_details_keep_paths_and_digests_without_environment_or_shell_secrets(self):
        unit = "porota-snapshot-preopen-rc6.service"
        virtual = "/etc/systemd/system/" + unit
        actual = self.root / "unit"
        actual.write_text("[Service]\nUser=porotaadmin\nEnvironment=TOKEN=private-env-value MODE=PAPER\n"
            "EnvironmentFile=/etc/porota/secrets.env\n"
            "ExecStart=/bin/bash -c '/usr/bin/python3 /opt/porota-trading/job.py --token private-argument https://secret.invalid/token'\n")
        raw = actual.read_bytes()
        original_open, original_stat = os.open, os.lstat
        def opened(path, flags, *args, **kwargs):
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))
            return original_open(actual if path == virtual else path, flags, *args, **kwargs)
        def metadata(path, *args, **kwargs):
            value = original_stat(actual if path == virtual else path, *args, **kwargs)
            if path == virtual:
                fields = list(value)
                fields[4] = 0
                return os.stat_result(fields)
            return value
        out = io.StringIO()
        with patch.object(sys, "argv", ["inspect", json.dumps([unit]), json.dumps([virtual])]), \
             patch.object(os, "open", side_effect=opened), patch.object(os, "lstat", side_effect=metadata), \
             redirect_stdout(out):
            exec(compile(setup.UNIT_DETAILS, "unit-details-readonly", "exec"), {})
        result = json.loads(out.getvalue())
        serialized = json.dumps(result)
        for secret in ("private-env-value", "private-argument", "secret.invalid"):
            self.assertNotIn(secret, serialized)
        self.assertIn("/opt/porota-trading/job.py", serialized)
        self.assertIn("/etc/porota/secrets.env", serialized)
        self.assertEqual(result[0]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(set(result[0]["environment_names"]), {"TOKEN", "MODE"})

    def test_tool_versions_are_sanitized_and_launcher_is_never_executed(self):
        calls = []
        def version(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "version 29.1.0 PRIVATE_DIAGNOSTIC", "")
        with patch.object(setup.shutil, "which", side_effect=lambda name, **kwargs: "/usr/bin/" + name):
            result = setup.toolchain_metadata(run=version)
        self.assertTrue(result["vpnkit"]["available"])
        self.assertEqual(result["docker"]["version"], "29.1.0")
        self.assertNotIn("PRIVATE_DIAGNOSTIC", json.dumps(result))
        self.assertFalse(any(command[0].endswith("dockerd-rootless.sh") for command in calls))

    def test_filesystem_inspection_does_not_create_missing_store(self):
        missing = self.root / "nested" / "docker"
        result = setup.filesystem_metadata(missing)
        self.assertEqual(result["status"], "OBSERVED")
        self.assertFalse(result["target_exists"])
        self.assertFalse(missing.parent.exists())

    def test_private_directory_metadata_is_bounded_and_never_repairs_permissions(self):
        home = self.root / "home"
        directories = {"state": (home / ".local/state/porota-rc6", 0o700),
                       "daemon_config": (home / ".config/porota-rc6", 0o700),
                       "daemon_store": (home / ".local/share/porota-rc6/docker", 0o710)}
        for directory, mode in directories.values():
            directory.mkdir(parents=True)
            directory.chmod(mode)
            (directory / "private-value").write_text("never-publish-directory-contents")
        client = home / ".config/porota-rc6/client"
        account = SimpleNamespace(pw_dir=str(home), pw_name="porota-test",
                                  pw_uid=os.getuid(), pw_gid=os.getgid())
        with patch.object(setup.pwd, "getpwuid", return_value=account), \
             patch.object(Path, "mkdir", side_effect=AssertionError("inspection cannot create")), \
             patch.object(Path, "chmod", side_effect=AssertionError("inspection cannot repair")), \
             patch.object(Path, "iterdir", side_effect=AssertionError("inspection cannot enumerate")):
            result = setup.inspect_host(self.root, run=self.fake_run)
        metadata = result["directory_metadata"]
        self.assertEqual(set(metadata), {*directories, "client_config"})
        for label, (directory, mode) in directories.items():
            self.assertTrue(metadata[label]["present"])
            self.assertEqual(metadata[label]["mode"], oct(mode))
            self.assertEqual(metadata[label]["uid"], os.getuid())
            self.assertFalse(metadata[label]["symlink"])
            self.assertEqual(directory.stat().st_mode & 0o777, mode)
        self.assertEqual(metadata["client_config"], {"present": False})
        self.assertFalse(client.exists())
        self.assertEqual(result["host_mutations"], 0)
        self.assertNotIn("never-publish-directory-contents", json.dumps(result))
        self.assertFalse(any(set(c) & {"stop", "start", "restart", "rm", "prune", "enable", "disable"}
                             for c in self.commands))


class PrivateImageInspectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.socket = Path("/run/user/1000/porota-rc6-docker.sock")
        self.config_id, self.manifest_id = "sha256:" + "c" * 64, "sha256:" + "d" * 64
        self.info = {"ID": "private-daemon", "SecurityOptions": ["name=rootless"],
            "DockerRootDir": str(self.home / ".local/share/porota-rc6/docker"),
            "ServerVersion": "29.1.0", "KernelVersion": "6.8.0-90-generic", "Driver": "overlayfs",
            "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"],
                             ["Backing Filesystem", "extfs"], ["secret", "must-not-leak"]]}
        self.calls = []
        self.listed = self.manifest_id
        self.real_fstat = os.fstat
        self.image = {"Id": self.config_id, "Os": "linux", "Architecture": "amd64",
            "Descriptor": {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": self.manifest_id, "size": 3141,
                "platform": {"os": "linux", "architecture": "amd64", "private": "must-not-leak"},
                "annotations": {"secret": "must-not-leak"}},
            "Config": {"Env": ["POROTA_BUILD_SHA=" + "a" * 40,
                "POROTA_CANDIDATE_TREE_SHA=" + "b" * 40, "SECRET=must-not-leak"]}}
        for obj, name, value in ((setup.os, "getuid", lambda: 1000),
                (setup.pwd, "getpwuid", lambda _: SimpleNamespace(pw_dir=str(self.home), pw_name="fixture", pw_uid=1000)),
                (setup, "_metadata", lambda _: {"present": True, "uid": 1000, "socket": True, "symlink": False})):
            mocked = patch.object(obj, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def run_command(self, command, **kwargs):
        self.calls.append(command)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        if command[0] == "/usr/bin/stat":
            self.assertEqual(command, ("/usr/bin/stat", "--file-system", "--format=%T",
                                      str(self.home / ".local/share/porota-rc6/docker")))
            return subprocess.CompletedProcess(command, 0, "ext2/ext3\n", "must-not-leak")
        self.assertEqual(command[:3], (setup.DOCKER, "--host", "unix://" + str(self.socket)))
        args = command[3:]
        if args == ("image", "ls", "--all", "--quiet", "--no-trunc"):
            return subprocess.CompletedProcess(command, 0, self.listed, "must-not-leak")
        if args == ("container", "ls", "--all", "--quiet", "--no-trunc"):
            return subprocess.CompletedProcess(command, 0, "1" * 64, "must-not-leak")
        if len(args) == 3 and args[:2] == ("image", "inspect"):
            self.assertRegex(args[-1], r"^sha256:[0-9a-f]{64}$")
            if args[-1] == self.config_id:
                return subprocess.CompletedProcess(command, 1, "", "must-not-leak")
            return subprocess.CompletedProcess(command, 0, json.dumps([self.image]), "must-not-leak")
        self.fail("Unexpected or mutating Docker operation: " + repr(args))

    def inspect(self, info=None):
        inspector = getattr(setup, "private_image_inventory", None)
        self.assertTrue(callable(inspector), "Private image diagnostics are missing")
        return inspector(self.home, self.socket, self.info if info is None else info, run=self.run_command)

    def test_image_ids_descriptor_and_build_identity_remain_distinct_without_secrets(self):
        result = self.inspect()
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["ServerVersion"], "29.1.0")
        self.assertEqual(result["KernelVersion"], "6.8.0-90-generic")
        self.assertEqual(result["Driver"], "overlayfs")
        self.assertEqual(result.get("backing_filesystem"), "ext2/ext3")
        self.assertEqual(result["DriverStatus"], self.info["DriverStatus"][:2])
        image = result["images"][0]
        self.assertEqual(image["requested_id"], self.manifest_id)
        self.assertEqual(image["Id"], self.config_id)
        self.assertEqual(image["Descriptor"]["digest"], self.manifest_id)
        self.assertEqual(image["platform"], {"os": "linux", "architecture": "amd64"})
        self.assertEqual(image["source_sha"], "a" * 40)
        self.assertEqual(image["source_tree"], "b" * 40)
        self.assertEqual(result["containers"], {"status": "OBSERVED", "count": 1, "ids": ["1" * 64], "truncated": False})
        self.assertNotIn("must-not-leak", json.dumps(result))
        self.assertNotIn("Env", json.dumps(result))

    def test_foreign_or_rootful_daemon_cannot_trigger_image_or_container_queries(self):
        for change in ({"SecurityOptions": []}, {"DockerRootDir": "/var/lib/docker"}):
            with self.subTest(change=change):
                self.assertEqual(self.inspect(dict(self.info, **change))["status"], "NO_VERIFICADO")
        self.assertEqual(self.calls, [])

    def test_existing_inspect_operation_includes_the_private_inventory(self):
        exists = Path.exists
        def transport(command, **kwargs):
            if command[:3] == (setup.DOCKER, "--host", "unix://" + str(self.socket)):
                if command[3:] == ("info", "--format", "{{json .}}"):
                    return subprocess.CompletedProcess(command, 0, json.dumps(self.info), "")
                return self.run_command(command, **kwargs)
            if command[0] == "/usr/bin/stat":
                return self.run_command(command, **kwargs)
            return subprocess.CompletedProcess(command, 1, "", "must-not-leak")
        with patch.object(Path, "exists", lambda path: path == self.socket or exists(path)):
            result = setup.inspect_host(self.home, run=transport)
        self.assertEqual(result["own_daemon"]["images"][0]["requested_id"], self.manifest_id)
        self.assertEqual(result["host_mutations"], 0)
        self.assertIn("current_metadata", result)
        self.assertNotIn("must-not-leak", json.dumps(result))

    def test_inventory_is_bounded_and_invalid_ids_are_never_used_as_arguments(self):
        self.listed = "\n".join("sha256:" + format(index, "064x") for index in range(12))
        result = self.inspect()
        self.assertEqual(result["image_count"], 12)
        self.assertTrue(result["images_truncated"])
        self.assertEqual(len(result["images"]), 8)
        self.assertEqual(sum(command[3:5] == ("image", "inspect") for command in self.calls), 8)
        self.calls.clear()
        self.listed = "--arbitrary-command"
        self.assertEqual(self.inspect()["status"], "NO_VERIFICADO")
        self.assertFalse(any(command[3:5] == ("image", "inspect") for command in self.calls))

    def test_invalid_or_duplicate_build_fields_are_not_reported_as_identity(self):
        self.image["Config"]["Env"] = ["POROTA_BUILD_SHA=" + "a" * 40,
            "POROTA_BUILD_SHA=" + "b" * 40, "POROTA_CANDIDATE_TREE_SHA=must-not-leak"]
        image = self.inspect()["images"][0]
        self.assertIsNone(image["source_sha"])
        self.assertIsNone(image["source_tree"])
        self.assertNotIn("must-not-leak", json.dumps(image))

    def test_only_validated_release_labels_are_reported(self):
        self.image["Config"]["Labels"] = {"porota.commit": "a" * 40, "porota.predeploy": "v2",
                                          "private": "must-not-leak"}
        image = self.inspect()["images"][0]
        self.assertEqual(image["labels"], {"porota.commit": "a" * 40, "porota.predeploy": "v2"})
        self.assertIn("release_custody", image)
        self.image["Config"]["Labels"] = {"porota.commit": "must-not-leak", "porota.predeploy": "must-not-leak"}
        result = self.inspect()
        self.assertEqual(result["images"][0]["labels"], {})
        self.assertNotIn("must-not-leak", json.dumps(result))

    def test_journals_have_one_bounded_inventory_across_multiple_images(self):
        self.listed = "\n".join("sha256:" + format(index, "064x") for index in range(3))
        original = setup.private_release_custody
        with patch.object(setup, "private_release_custody", wraps=original) as custody:
            result = self.inspect()
        self.assertEqual(len(result["images"]), 3)
        self.assertEqual(sum(call.kwargs["include_attempts"] for call in custody.call_args_list), 1)
        self.assertIn("journal_custody", result)
        self.assertTrue(all("attempts" not in row["release_custody"] for row in result["images"]))

    def release_file(self):
        path = self.home / ".local/state/porota-rc6/releases" / ("a" * 40) / "release.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"schema": "porota.release.v1", "source_sha": "a" * 40,
            "source_tree": "b" * 40, "image_id": self.config_id, "private": "must-not-leak"}))
        path.chmod(0o600)
        return path

    def owned_stat(self, fd):
        value = self.real_fstat(fd)
        return SimpleNamespace(st_mode=value.st_mode, st_uid=1000, st_nlink=value.st_nlink, st_size=value.st_size)

    def test_persisted_release_supplies_exact_config_lookup_without_hardcoded_ids(self):
        self.release_file()
        with patch.object(setup.os, "fstat", side_effect=self.owned_stat):
            result = self.inspect()
        image = result["images"][0]
        self.assertEqual(image.get("release", {}).get("image_id"), self.config_id)
        self.assertEqual(image["release"]["source_tree"], "b" * 40)
        self.assertEqual(image["release_image_lookup"], {
            "requested_id": self.config_id, "status": "NO_VERIFICADO", "returncode": 1})
        self.assertNotIn("must-not-leak", json.dumps(result))

    def test_release_alias_or_source_mismatch_cannot_supply_an_image_query(self):
        path = self.release_file()
        original = path.read_bytes()
        path.write_text(json.dumps({"schema": "porota.release.v1", "source_sha": "f" * 40,
            "source_tree": "b" * 40, "image_id": self.config_id}))
        with patch.object(setup.os, "fstat", side_effect=self.owned_stat):
            self.inspect()
        self.assertFalse(any(command[-1] == self.config_id for command in self.calls))
        path.unlink()
        target = self.home / "private.json"
        target.write_bytes(original)
        path.symlink_to(target)
        self.calls.clear()
        with patch.object(setup.os, "fstat", side_effect=self.owned_stat):
            self.inspect()
        self.assertFalse(any(command[-1] == self.config_id for command in self.calls))


class PrivateRuntimeObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.socket = Path(f"/run/user/{os.getuid()}/porota-rc6-docker.sock")
        self.cids = {"observer": "1" * 64, "dashboard": "2" * 64}
        self.source_sha, self.source_tree = "a" * 40, "b" * 40
        self.image_id = "sha256:" + "c" * 64
        self.attempt = "43758003307-1"
        self.info = {"ID": "dedicated-daemon", "SecurityOptions": ["name=rootless"],
            "DockerRootDir": str(self.home / ".local/share/porota-rc6/docker")}
        self.current = self.home / ".local/state/porota-rc6/current.json"
        self.current.parent.mkdir(parents=True)
        self.current.parent.chmod(0o700)
        self.document = {"schema": "porota.rootless.deploy.v1", "state": "VALIDADO_RUNTIME",
            "phase": "COMPLETE", "source_sha": self.source_sha,
            "source_tree": self.source_tree, "image_id": self.image_id,
            "daemon_id": self.info["ID"], "attempt": self.attempt,
            "roles": self.cids, "secret": "never-print-current-secret"}
        self.write_current()
        self.calls = []
        metadata = setup._metadata
        def socket_metadata(path):
            if Path(path) == self.socket:
                return {"present": True, "uid": os.getuid(), "socket": True, "symlink": False}
            return metadata(path)
        patcher = patch.object(setup, "_metadata", side_effect=socket_metadata)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_current(self):
        self.current.write_text(json.dumps(self.document))
        self.current.chmod(0o600)

    def run_command(self, command, **kwargs):
        self.calls.append(tuple(command))
        self.assertEqual(command[:5], (setup.DOCKER, "--host", f"unix://{self.socket}",
                                       "container", "inspect"))
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(kwargs["timeout"], 8)
        cid = command[5]
        role = next(role for role, value in self.cids.items() if cid == value)
        row = {"Id": cid, "Name": f"/porota-rc6-{role}-{self.attempt}",
            "Image": self.image_id, "RestartCount": 2 if role == "observer" else 0,
            "Config": {"Labels": {"io.porota.project": "rc6", "io.porota.role": role,
                       "io.porota.attempt": self.attempt, "private": "must-never-leak"},
                       "Env": ["POROTA_BUILD_SHA=" + self.source_sha,
                               "POROTA_CANDIDATE_TREE_SHA=" + self.source_tree,
                               "PRIVATE=must-never-leak"]},
            "State": {"Status": "running", "Running": True, "OOMKilled": False,
                      "Pid": 4321 if role == "observer" else 4567, "ExitCode": 0,
                      "StartedAt": "2026-11-15T20:32:00Z",
                      "Health": {"Status": "healthy"} if role == "dashboard" else None}}
        return subprocess.CompletedProcess(command, 0, json.dumps([row]), "must-never-leak")

    def observe(self):
        inspector = getattr(setup, "private_runtime_inventory", None)
        self.assertTrue(callable(inspector), "Bounded rootless runtime observation is missing")
        return inspector(self.home, self.socket, self.info, run=self.run_command)

    def test_only_pinned_current_rootless_pair_is_inspected_and_health_is_sanitized(self):
        result = self.observe()
        self.assertEqual(result.get("status"), "OBSERVED")
        self.assertEqual(result.get("source_sha"), self.source_sha)
        self.assertEqual(result.get("source_tree"), self.source_tree)
        self.assertEqual(result.get("image_id"), self.image_id)
        self.assertEqual(result["roles"]["observer"]["Pid"], 4321)
        self.assertEqual(result["roles"]["observer"]["RestartCount"], 2)
        self.assertEqual(result["roles"]["dashboard"]["Health"], "healthy")
        self.assertEqual(set(command[-1] for command in self.calls), set(self.cids.values()))
        self.assertNotIn("must-never-leak", json.dumps(result))
        self.assertNotIn("never-print-current-secret", json.dumps(result))
        self.assertTrue(all(command[3:5] == ("container", "inspect") for command in self.calls))

    def test_duplicate_or_foreign_current_cids_fail_closed_before_docker(self):
        for change in ({"roles": {"observer": self.cids["observer"],
                         "dashboard": self.cids["observer"]}},
                       {"daemon_id": "not-the-private-daemon"}):
            with self.subTest(change=change):
                self.document["roles"] = self.cids
                self.document.update(change)
                self.write_current()
                result = self.observe()
                self.assertEqual(result.get("status"), "NO_VERIFICADO")
                self.assertEqual(self.calls, [])
                self.assertNotIn("never-print-current-secret", json.dumps(result))

    def test_existing_host_inspect_exposes_runtime_without_new_workflow(self):
        exists = Path.exists
        def transport(command, **kwargs):
            if command[:3] == (setup.DOCKER, "--host", f"unix://{self.socket}"):
                if command[3:] == ("info", "--format", "{{json .}}"):
                    return subprocess.CompletedProcess(command, 0, json.dumps(self.info), "")
                return self.run_command(command, **kwargs)
            return subprocess.CompletedProcess(command, 1, "", "private-no-leak")
        with patch.object(Path, "exists", lambda path: path == self.socket or exists(path)), \
             patch.object(setup, "private_image_inventory", return_value={"status": "OBSERVED", "images": []}), \
             patch.object(setup.pwd, "getpwuid", return_value=SimpleNamespace(
                 pw_dir=str(self.home), pw_name="porotaadmin", pw_uid=os.getuid())):
            result = setup.inspect_host(self.home, run=transport)
        self.assertEqual(result["own_daemon"].get("runtime", {}).get("status"), "OBSERVED")
        self.assertEqual(result["host_mutations"], 0)
        self.assertNotIn("must-never-leak", json.dumps(result))


class PrivateControllerHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "data/paper_v17/artifacts/observer_v17.db/runtime-health.json"
        self.path.parent.mkdir(parents=True)
        self.source_sha, self.source_tree = "a" * 40, "b" * 40
        self.roles = ("scanner", "performance", "dynamic_shadow", "exit_reader",
                      "notifications", "candles", "intraday_scalping", "caucion_cash_sweep")
        self.health = {"schema": "rc6.runtime-child-health.v1", "source_sha": self.source_sha,
            "candidate_tree_sha": self.source_tree, "recorded_at": "2026-11-15T20:31:00+00:00",
            "children": {role: {"state": "RUNNING", "pid": index + 101,
                                "started_at": "2026-11-15T20:30:00+00:00"}
                         for index, role in enumerate(self.roles)},
            "ppi_budget": {"secret": "never-print-budget"}}
        self.write_health()

    def write_health(self):
        self.path.write_text(json.dumps(self.health))
        self.path.chmod(0o600)

    def observe(self):
        reader = getattr(setup, "private_controller_health", None)
        self.assertTrue(callable(reader), "Bounded controller health reader is missing")
        return reader(self.root, self.source_sha, self.source_tree,
                      now=datetime(2026, 11, 15, 20, 32, 0, tzinfo=timezone.utc))

    def test_fresh_eight_role_file_is_attributed_without_claiming_pid_liveness(self):
        before = self.path.stat()
        result = self.observe()
        after = self.path.stat()
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(set(result["children"]), set(self.roles))
        self.assertEqual(len({row["pid"] for row in result["children"].values()}), 8)
        self.assertEqual(result["process_liveness"], "NO_VERIFICADO")
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertNotIn("never-print-budget", json.dumps(result))
        self.assertNotIn("ppi_budget", json.dumps(result))

    def test_stale_wrong_source_or_duplicate_pids_fail_closed(self):
        original = json.loads(json.dumps(self.health))
        for mutation in ({"recorded_at": "2026-11-15T20:29:00+00:00"},
                         {"source_sha": "f" * 40},
                         {"children": {**self.health["children"],
                                       "scanner": {"state": "RUNNING", "pid": 102,
                                                   "started_at": "2026-11-15T20:30:00+00:00"}}}):
            with self.subTest(mutation=list(mutation)):
                self.health = json.loads(json.dumps(original))
                self.health.update(mutation)
                self.write_health()
                result = self.observe()
                self.assertEqual(result["status"], "NO_VERIFICADO")
                self.assertNotIn("children", result)


class PrivateReleaseCustodyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.state = self.home / ".local/state/porota-rc6"
        self.release = self.state / "releases" / ("a" * 40) / "release.json"
        self.attempts = self.state / "attempts"
        self.release.parent.mkdir(parents=True)
        self.attempts.mkdir()
        self.release.write_text(json.dumps({"schema": "porota.release.v1", "source_sha": "a" * 40,
            "source_tree": "b" * 40, "image_id": "sha256:" + "c" * 64, "secret": "must-not-leak"}))
        self.release.chmod(0o600)

    def journal(self, name="43746889398-1", **changes):
        path = self.attempts / name / "receipt.json"
        path.parent.mkdir(mode=0o700)
        value = {"schema": "porota.rootless.deploy.v1", "state": "NO_VERIFICADO",
            "source_sha": "a" * 40, "source_tree": "b" * 40, "image_id": "sha256:" + "c" * 64,
            "release_manifest_sha256": hashlib.sha256(self.release.read_bytes()).hexdigest(),
            "transition_started": False, "roles": {}, "secret": "must-not-leak"}
        value.update(changes)
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        return path

    def inspect(self):
        return setup.private_release_custody(self.home, "a" * 40)

    def test_owned_journal_identity_and_metadata_are_read_without_mutation_or_secrets(self):
        journal = self.journal()
        opened, scanned = [], []
        real_open, real_scandir = os.open, os.scandir
        def open_readonly(path, flags, *args, **kwargs):
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            self.assertTrue(flags & os.O_NOFOLLOW)
            opened.append(Path(path))
            return real_open(path, flags, *args, **kwargs)
        def bounded_scan(path):
            scanned.append(Path(path))
            return real_scandir(path)
        with patch.object(setup.os, "open", side_effect=open_readonly), \
             patch.object(setup.os, "scandir", side_effect=bounded_scan), \
             patch.object(Path, "mkdir", side_effect=AssertionError("no mkdir")), \
             patch.object(Path, "chmod", side_effect=AssertionError("no chmod")):
            result = self.inspect()
        self.assertEqual(opened, [self.release, journal])
        self.assertEqual(scanned, [self.attempts])
        self.assertEqual(result["attempts_status"], "OBSERVED")
        self.assertEqual(result["directories"]["attempts"]["mode"], "0o755")
        self.assertEqual(result["release"]["sha256"], hashlib.sha256(self.release.read_bytes()).hexdigest())
        row = result["attempts"][0]
        self.assertEqual(row["attempt"], "43746889398-1")
        self.assertEqual(row["directory"]["mode"], "0o700")
        self.assertEqual(row["receipt"]["metadata"]["mode"], "0o600")
        self.assertEqual(row["receipt"]["fields"], {
            "schema": "porota.rootless.deploy.v1", "state": "NO_VERIFICADO", "source_sha": "a" * 40,
            "source_tree": "b" * 40, "image_id": "sha256:" + "c" * 64,
            "release_manifest_sha256": result["release"]["sha256"], "transition_started": False,
            "roles_empty": True})
        self.assertNotIn("must-not-leak", json.dumps(result))

    def test_journal_fields_reject_arbitrary_values_without_serializing_roles(self):
        self.journal(schema="must-not-leak", state="must-not-leak", source_sha="must-not-leak",
            source_tree="must-not-leak", image_id="must-not-leak", release_manifest_sha256="must-not-leak",
            transition_started="must-not-leak", roles={"private": "must-not-leak"})
        result = self.inspect()
        fields = result["attempts"][0]["receipt"]["fields"]
        self.assertFalse(fields.pop("roles_empty"))
        self.assertTrue(all(value is None for value in fields.values()))
        self.assertNotIn("must-not-leak", json.dumps(result))

    def test_absent_transition_is_distinct_from_false(self):
        journal = self.journal()
        value = json.loads(journal.read_text())
        value.pop("transition_started")
        journal.write_text(json.dumps(value))
        self.assertNotIn("transition_started", self.inspect()["attempts"][0]["receipt"]["fields"])

    def test_nonprivate_or_oversize_or_hardlinked_receipts_are_not_read(self):
        journal = self.journal()
        for change in ("nonprivate", "oversize", "hardlink"):
            with self.subTest(change=change):
                journal.chmod(0o640 if change == "nonprivate" else 0o600)
                journal.write_bytes(b"x" * (2 * 1024**2 + 1) if change == "oversize" else b"{}")
                if change == "hardlink":
                    os.link(journal, self.home / "duplicate")
                result = self.inspect()["attempts"][0]["receipt"]
                self.assertEqual(result["status"], "NO_VERIFICADO")
                self.assertNotIn("fields", result)
                self.assertNotIn("sha256", result)

    def test_receipt_symlink_and_parent_alias_are_not_opened(self):
        journal = self.journal()
        journal.unlink()
        journal.symlink_to(self.release)
        result = self.inspect()["attempts"][0]["receipt"]
        self.assertTrue(result["metadata"]["symlink"])
        self.assertNotIn("fields", result)
        journal.unlink()
        journal.parent.rmdir()
        journal.parent.symlink_to(self.release.parent, target_is_directory=True)
        result = self.inspect()["attempts"][0]
        self.assertTrue(result["directory"]["symlink"])
        self.assertEqual(result["receipt"]["metadata"]["status"], "PARENT_ALIAS")
        self.assertNotIn("fields", result["receipt"])

    def test_at_most_32_attempts_are_inspected_and_unknown_names_are_not_reported(self):
        for index in range(1, 34):
            (self.attempts / f"{index}-1").mkdir(mode=0o700)
        result = self.inspect()
        self.assertEqual(result["attempts_status"], "LIMIT_EXCEEDED")
        self.assertEqual(result["attempts"], [])
        (self.attempts / "33-1").rmdir()
        self.assertEqual(len(self.inspect()["attempts"]), 32)
        (self.attempts / "32-1").rmdir()
        (self.attempts / "must-not-leak").mkdir()
        result = self.inspect()
        self.assertEqual(result["attempts_status"], "UNKNOWN_ENTRY")
        self.assertEqual(len(result["attempts"]), 31)
        self.assertNotIn("must-not-leak", json.dumps(result))

    def test_unsafe_parent_and_invalid_source_do_not_open_private_files(self):
        self.journal()
        self.state.chmod(0o777)
        with patch.object(setup.os, "open", side_effect=AssertionError("unsafe parent")):
            result = self.inspect()
            self.assertIsNone(setup.private_release_custody(self.home, "../../private"))
        self.assertEqual(result["release"]["status"], "NO_VERIFICADO")
        self.assertEqual(result["attempts"], [])


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.identity = host.Identity(os.getuid(), os.getgid(), "porotaadmin", self.home)
        self.commands = []

    def commands_ok(self, command, **kwargs):
        self.commands.append(command)
        stdout = ""
        if "loginctl" in command[0] and "show-user" in command:
            stdout = "yes"
        if "compose" in command and "version" in command:
            stdout = "2.35.1"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def test_prepare_never_installs_packages_when_toolchain_is_missing(self):
        with patch.object(setup.shutil, "which", return_value=None):
            with self.assertRaisesRegex(host.Hold, "ROOTLESS_PACKAGES_REQUIRED"):
                setup.prepare(self.identity, run=self.commands_ok)
        self.assertEqual(self.commands, [])
        receipt = json.loads((self.identity.state / "prepare.json").read_text())
        self.assertIn("newuidmap", receipt["missing_prerequisites"])
        self.assertEqual(receipt["state"], "NO_VERIFICADO")

    def test_prepare_creates_only_dedicated_user_unit_and_pins_socket_store(self):
        original_read = host.read_regular
        def read(path, *args):
            if str(path).endswith("cgroup.controllers"):
                return b"cpu memory pids"
            return original_read(path, *args)
        with patch.object(setup.shutil, "which", side_effect=lambda name, **kwargs: "/usr/bin/" + name), \
             patch.object(setup, "subids_available", return_value=True), \
             patch.object(host, "read_regular", side_effect=read), \
             patch.object(host.Host, "boundary", return_value="dedicated-daemon"):
            result = setup.prepare(self.identity, run=self.commands_ok)
            # Identical re-entry checks the same files; no global Docker lifecycle exists.
            setup.prepare(self.identity, run=self.commands_ok)
        config = json.loads((self.home / ".config/porota-rc6/daemon.json").read_text())
        self.assertEqual(config["hosts"], [f"unix://{self.identity.socket}"])
        self.assertEqual(config["data-root"], str(self.identity.store))
        self.assertEqual(result["runtime_validation"], "NO_VERIFICADO")
        system = [x for x in self.commands if x[0] == setup.SYSTEMCTL]
        self.assertTrue(system)
        self.assertTrue(all("--user" in x for x in system))
        self.assertFalse(any(x[0] == setup.SUDO for x in self.commands))
        compose = [x for x in self.commands if "compose" in x]
        self.assertTrue(compose and all(f"unix://{self.identity.socket}" in x for x in compose))
        self.assertFalse((self.home / ".config/systemd/user/docker.service").exists())

    def test_bootstrap_rejects_unsupported_systemd_path(self):
        unsafe = host.Identity(1000, 1000, "porotaadmin", Path("/home/user%h"))
        with self.assertRaisesRegex(host.Hold, "BOOTSTRAP_PATH_UNSUPPORTED"):
            setup.daemon_configuration(unsafe, "/usr/bin/dockerd-rootless.sh")

    def test_subids_do_not_overlap_another_daemon_or_real_host_account(self):
        path = self.home / "subuid"
        for contents, expected in (("porotaadmin:231072:65536\n", True),
                ("porotaadmin:10000:65536\n", False),
                ("porotaadmin:231072:65536\nboca:240000:65536\n", False),
                ("porotaadmin:231072:65536\nporotaadmin:250000:65536\n", False)):
            path.write_text(contents)
            self.assertEqual(setup.subids_available(path, self.identity), expected)

    def test_access_repairs_are_bounded_and_do_not_read_secret_contents(self):
        compile(setup.PREPARE_ACCESS, "bootstrap-access", "exec")
        captures = []
        def command(args, **kwargs):
            captures.append(args)
            return subprocess.CompletedProcess(args, 0, '{"scope":"EXPLICIT_REQUIRED_RC6_PATHS","changed_paths":[],"recursive":false}', "")
        receipt = setup.prepare_access(self.identity, run=command)
        self.assertFalse(receipt["recursive"])
        self.assertEqual(captures[0][:5], (setup.SUDO, "-n", "--", "/usr/bin/python3", "-c"))
        self.assertNotIn("os.walk", setup.PREPARE_ACCESS)
        self.assertNotIn(".read(", setup.PREPARE_ACCESS)


class LegacyTransitionTests(unittest.TestCase):
    """Fake rootful commands model only reviewed RC6 objects; no host access."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        self.root = Path("/opt/porota-trading")
        self.name = "porota-functional-health-rc6.timer"
        unit = self.source / "systemd" / self.name
        unit.parent.mkdir()
        unit.write_bytes(b"[Timer]\nOnBootSec=1min\n")
        policy = self.source / "ops/policy/host-control-plane-reconciliation-v2.json"
        policy.parent.mkdir(parents=True)
        policy.write_text(json.dumps({"units": {"systemd/" + self.name: {"role": "RETIRED_TIMER"}}}))
        self.digest = hashlib.sha256(unit.read_bytes()).hexdigest()
        self.units = [{"name": self.name, "policy_role": "RETIRED_TIMER", "LoadState": "loaded",
                       "ActiveState": "active", "UnitFileState": "enabled", "DropInPaths": "",
                       "FragmentPath": "/etc/systemd/system/" + self.name}]
        self.containers = {}
        for name, cid, command in ((setup.LEGACY_NAMES[0], "1" * 64, "bf_production_paper_observer.py"),
                                   (setup.LEGACY_NAMES[1], "2" * 64, "o_dashboard.py")):
            self.containers[cid] = {"Id": cid, "Name": "/" + name, "Image": "sha256:" + "a" * 64,
                "Config": {"Cmd": ["python", command], "Entrypoint": None},
                "Mounts": [{"Type": "bind", "Source": str(self.root / "data"), "Destination": "/app/data"}],
                "State": {"Running": True}, "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}}}
        self.commands = []

    def fake(self, command, **kwargs):
        self.commands.append(command)
        out = ""
        if setup.DOCKER in command:
            start = command.index("unix:///var/run/docker.sock") + 1
            args = command[start:]
            if args[0] == "info":
                out = json.dumps({"ID": "legacy-daemon"})
            elif args[:2] == ("container", "ls"):
                out = "\n".join(self.containers)
            elif args[:2] == ("container", "inspect"):
                out = json.dumps([self.containers[args[-1]]])
            elif args[:2] == ("container", "stop"):
                self.containers[args[-1]]["State"]["Running"] = False
            elif args[:2] == ("container", "update"):
                self.containers[args[-1]]["HostConfig"]["RestartPolicy"]["Name"] = "no"
            else:
                raise AssertionError(command)
        elif setup.SYSTEMCTL in command and "disable" in command:
            self.units[0].update(ActiveState="inactive", UnitFileState="disabled")
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, out, "")

    def patches(self):
        return (patch.object(setup, "unit_inventory", side_effect=lambda *args, **kwargs:
                    {"status": "OBSERVED", "units": self.units, "unmapped_units": []}),
                patch.object(setup.LegacyTransition, "unit_digest", return_value=self.digest))

    def test_functional_enabled_service_blocks_before_any_quiescence(self):
        self.units[0]["policy_role"] = "ACTIVE_TIMER"
        inventory, digest = self.patches()
        with inventory, digest, self.assertRaisesRegex(host.Hold, "LEGACY_FUNCTIONAL_UNIT_NEEDS_ROOTLESS_SUCCESSOR") as caught:
            setup.LegacyTransition(self.root, self.source, run=self.fake)
        self.assertEqual(caught.exception.public_details["functional_units_needing_successor"], [self.name])
        self.assertFalse(any("stop" in c or "disable" in c or "update" in c for c in self.commands))

    def test_quiescence_receipt_is_produced_by_exact_stops_and_zero_database_check(self):
        inventory, digest = self.patches()
        with inventory, digest, patch.object(host, "database_snapshot", return_value={"inode": 4, "real_orders_sent": 0}):
            transition = setup.LegacyTransition(self.root, self.source, run=self.fake)
            result = transition.quiesce()
        self.assertEqual(result["state"], "QUIESCENT")
        self.assertEqual({x["cid"] for x in result["containers"]}, set(self.containers))
        self.assertTrue(all(not x["State"]["Running"] for x in self.containers.values()))
        stopped = [c[-1] for c in self.commands if "stop" in c]
        self.assertEqual(set(stopped), set(self.containers))
        self.assertFalse(result["rootful_daemon_stopped"])
        self.assertFalse(any("rm" in c or "prune" in c or "docker.service" in c for c in self.commands))

    def test_named_container_with_foreign_storage_is_refused(self):
        self.containers["1" * 64]["Mounts"][0]["Source"] = "/opt/boca/data"
        with self.assertRaisesRegex(host.Hold, "LEGACY_DATA_IDENTITY"):
            setup.LegacyTransition(self.root, self.source, run=self.fake)
        self.assertFalse(any("stop" in c or "disable" in c or "update" in c for c in self.commands))

    def test_missing_rootful_permission_is_not_treated_as_empty_legacy(self):
        def denied(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, "", "private denied details")
        with self.assertRaisesRegex(host.Hold, "BOOTSTRAP_COMMAND_UNAVAILABLE"):
            setup.LegacyTransition(self.root, self.source, run=denied)


class Preopen79DiagnosticTests(unittest.TestCase):
    """The inspect receipt must expose a cause, never raw journal content."""

    UNIT = "porota-preopen-rc6.service"
    START = datetime(2026, 11, 16, 13, 20, 24, tzinfo=timezone.utc)
    INVOCATION = "a" * 32

    def journal_row(self, signature="SCHEDULER_HOST_JOB_FAILED", **override):
        event = {
            "_SYSTEMD_UNIT": self.UNIT,
            "_SYSTEMD_INVOCATION_ID": self.INVOCATION,
            "__REALTIME_TIMESTAMP": str(int(self.START.timestamp() * 1_000_000) + 500000),
            "MESSAGE": json.dumps({"schema": "porota.rootless.job.v1", "state": "BLOQUEADO",
                                   "failure_signature": signature, "private": "TOKEN=never-export"}),
        }
        event.update(override)
        return json.dumps(event)

    def run_probe(self, *, journal=None, status=79, invocation=None, changed=False,
                  journal_rc=0, journal_timeout=False):
        calls = []
        show_count = 0
        if journal is None:
            journal = self.journal_row()
        def runner(command, **kwargs):
            nonlocal show_count
            calls.append(tuple(command))
            if command[0] == setup.SYSTEMCTL and command[1] == "show":
                show_count += 1
                code = (0 if changed and show_count > 1 else status)
                fields = (
                    f"Id={self.UNIT}\nResult=exit-code\nExecMainStatus={code}\n"
                    f"FragmentPath=/etc/systemd/system/{self.UNIT}\n"
                    "ExecMainStartTimestamp=Mon 2026-11-16 13:20:24 UTC\n"
                    "ExecMainExitTimestamp=Mon 2026-11-16 13:20:25 UTC\n"
                    f"InvocationID={self.INVOCATION if invocation is None else invocation}\n")
                return subprocess.CompletedProcess(command, 0, fields, "SECRET=not-exported")
            if len(command) > 3 and command[3] == "/usr/bin/journalctl":
                if journal_timeout:
                    raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                return subprocess.CompletedProcess(command, journal_rc, journal, "TOKEN=not-exported")
            raise AssertionError(command)
        return runner, calls

    def test_exposes_single_typed_79_signature_without_journal_or_environment(self):
        runner, calls = self.run_probe()
        receipt = setup.preopen_79_diagnostic(run=runner)
        self.assertEqual(receipt["status"], "OBSERVED")
        self.assertEqual(receipt["failure_signature"], "SCHEDULER_HOST_JOB_FAILED")
        self.assertEqual(receipt["subcheck"], "NO_VERIFICADO")
        self.assertEqual(receipt["event_start_utc"], "2026-11-16T13:20:24Z")
        self.assertNotIn("never-export", json.dumps(receipt))
        self.assertEqual(len([c for c in calls if c[0] == setup.SYSTEMCTL]), 2)
        journal = next(c for c in calls if len(c) > 3 and c[3] == "/usr/bin/journalctl")
        self.assertIn("_SYSTEMD_UNIT=" + self.UNIT, journal)
        self.assertNotIn("--unit", journal)
        self.assertIn("--lines=16", journal)
        self.assertIn("--since=2026-11-16 13:20:22 UTC", journal)
        self.assertFalse(any(set(c) & {"start", "stop", "restart", "enable", "disable"} for c in calls))

    def test_skips_systemd_manager_messages_but_not_other_unit_job_events(self):
        manager = json.dumps({"__REALTIME_TIMESTAMP": str(int(self.START.timestamp() * 1e6)),
                              "MESSAGE": "Started porota-preopen-rc6.service private=do-not-export"})
        runner, _ = self.run_probe(journal=manager + "\n" + self.journal_row() + "\n" + manager)
        receipt = setup.preopen_79_diagnostic(run=runner)
        self.assertEqual(receipt["status"], "OBSERVED")
        self.assertEqual(receipt["failure_signature"], "SCHEDULER_HOST_JOB_FAILED")
        self.assertNotIn("do-not-export", json.dumps(receipt))

    def test_custody_hold_codes_from_deployed_dispatcher_are_preserved(self):
        for code in ("DATABASE_REQUIRED", "PAPER_DATABASE_UNREADABLE",
                     "DATABASE_REPLACED", "REQUIRED_FILE_UNREADABLE",
                     "DEDICATED_DOCKER_STORE_REQUIRED", "PATH_ALIAS"):
            with self.subTest(code=code):
                runner, _ = self.run_probe(journal=self.journal_row(code))
                receipt = setup.preopen_79_diagnostic(run=runner)
                self.assertEqual(receipt["failure_signature"], code)

    def test_rejects_other_units_and_invocations(self):
        for event in (
            self.journal_row(**{"_SYSTEMD_UNIT": "boca-worker.service"}),
            self.journal_row(**{"_SYSTEMD_INVOCATION_ID": "b" * 32}),
        ):
            with self.subTest(event=event[:45]):
                runner, _ = self.run_probe(journal=event)
                self.assertEqual(setup.preopen_79_diagnostic(run=runner)["status"], "NO_VERIFICADO")

    def test_rejects_conflicting_multiple_invalid_or_large_records(self):
        for journal in (
            self.journal_row() + "\n" + self.journal_row("SCHEDULER_LAUNCHER_CUSTODY"),
            self.journal_row() + "\n" + self.journal_row(),
            self.journal_row("UNRECOGNIZED_PRIVATE_REASON"),
            self.journal_row() + "x" * 65000,
            self.journal_row(**{"__REALTIME_TIMESTAMP": str(int(self.START.timestamp() * 1e6) + 200_000_000)}),
            self.journal_row()[:-1] + ',"MESSAGE":"forged"}',
        ):
            with self.subTest(size=len(journal)):
                runner, _ = self.run_probe(journal=journal)
                self.assertEqual(setup.preopen_79_diagnostic(run=runner)["status"], "NO_VERIFICADO")

    def test_failed_permission_non79_or_unit_race_stay_unverified(self):
        for options in ({"journal_rc": 1}, {"journal_timeout": True},
                        {"status": 0}, {"changed": True},
                        {"invocation": "not-an-invocation"}, {"journal": ""}):
            with self.subTest(options=options):
                runner, calls = self.run_probe(**options)
                receipt = setup.preopen_79_diagnostic(run=runner)
                self.assertEqual(receipt["status"], "NO_VERIFICADO")
                self.assertNotIn("never-export", json.dumps(receipt))
                if options.get("status") == 0:
                    self.assertFalse(any(len(c) > 3 and c[3] == "/usr/bin/journalctl" for c in calls))

    def test_unverified_diagnosis_explains_which_evidence_stage_failed(self):
        for options, expected in (
            ({"invocation": ""}, "UNIT_RESULT_OR_INVOCATION_UNVERIFIED"),
            ({"journal_rc": 1}, "JOURNAL_QUERY_UNAVAILABLE"),
            ({"journal": ""}, "NO_TYPED_JOB_RECEIPT"),
            ({"changed": True}, "UNIT_CHANGED_DURING_INSPECT"),
        ):
            with self.subTest(options=options):
                runner, _ = self.run_probe(**options)
                receipt = setup.preopen_79_diagnostic(run=runner)
                self.assertEqual(receipt["reason"], expected)

    def test_preopen_is_included_in_existing_hashed_unit_allowlist(self):
        self.assertIn(self.UNIT, setup.INSPECT_UNITS)

    def test_preopen_installed_unit_exports_only_fragment_and_command_digests(self):
        path = "/etc/systemd/system/" + self.UNIT
        raw = [{"path": path, "status": "OBSERVED", "sha256": "a" * 64,
                "environment_names": ["PRIVATE_KEY"],
                "directives": [{"name": "ExecStart", "sha256": "b" * 64,
                                "tokens": [{"path": "/private/never-export"}]}]}]
        inventory = {"units": [{"name": self.UNIT, "FragmentPath": path, "DropInPaths": ""}]}
        def fake(command, **kwargs):
            self.assertEqual(command[0:4], ("/usr/bin/sudo", "-n", "--", "/usr/bin/python3"))
            return subprocess.CompletedProcess(command, 0, json.dumps(raw), "")
        result = setup.detailed_units(inventory, run=fake)
        self.assertEqual(result["files"], [{"path": path, "status": "OBSERVED",
                                            "sha256": "a" * 64, "execstart_sha256": ["b" * 64]}])
        self.assertNotIn("never-export", json.dumps(result))

    def test_existing_inspect_receipt_carries_only_sanitized_diagnosis(self):
        with tempfile.TemporaryDirectory() as directory:
            sentinel = {"status": "OBSERVED", "failure_signature": "SCHEDULER_LAUNCHER_CUSTODY",
                        "subcheck": "NO_VERIFICADO", "read_only": True}
            with patch.object(setup, "preopen_79_diagnostic", return_value=sentinel):
                def unavailable(command, **kwargs):
                    return subprocess.CompletedProcess(command, 1, "", "SECRET=never-include")
                result = setup.inspect_host(Path(directory), run=unavailable)
        self.assertEqual(result["preopen_79"], sentinel)
        self.assertEqual(result["host_mutations"], 0)
        self.assertNotIn("never-include", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
