"""A998: opt-in Porota-only rootless metadata, without shared Docker access."""

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import time
from types import SimpleNamespace
import unittest
from unittest import mock

try:
    from scripts import rc6_rootless_metadata_readonly as probe
except ImportError:
    probe = None


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/rc6-r1-docker-capacity-readonly.yml"


def _entry(kind, mode=0o700, uid=1808):
    return SimpleNamespace(st_mode=kind | mode, st_uid=uid, st_gid=uid)


class RootlessMetadataProbeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(probe, "Porota rootless probe implementation missing")
        self.account = SimpleNamespace(
            pw_name="porota-rc6", pw_uid=1808, pw_gid=1808,
            pw_dir="/var/lib/porota-rc6",
        )
        self.accounts = [self.account]
        self.effective_uid = 1808
        self.root = "/var/lib/porota-rc6/.local/share/docker"
        self.socket = "/run/user/1808/docker.sock"
        self.metadata = {
            "DockerRootDir": self.root,
            "SecurityOptions": ["name=seccomp,profile=builtin", "name=rootless"],
            "Name": "PRIVATE_HOST_SECRET_NEVER_PUBLISH",
            "Labels": ["DO_NOT_COPY_FOREIGN_METADATA"],
        }
        self.paths = {
            "/var/lib/porota-rc6": _entry(stat.S_IFDIR),
            "/var/lib/porota-rc6/.local": _entry(stat.S_IFDIR),
            "/var/lib/porota-rc6/.local/share": _entry(stat.S_IFDIR),
            self.root: _entry(stat.S_IFDIR),
            "/run/user/1808": _entry(stat.S_IFDIR),
            self.socket: _entry(stat.S_IFSOCK, mode=0o660),
        }

    def collect(self):
        def fake_stat(path):
            try:
                return self.paths[str(path)]
            except KeyError:
                raise FileNotFoundError(str(path)) from None

        def fake_run(argv, **kwargs):
            self.assertEqual(argv, [
                "docker", "--host", "unix:///run/user/1808/docker.sock",
                "info", "--format", "{{json .}}",
            ])
            self.assertEqual(kwargs["timeout"], 8)
            self.assertNotIn("DOCKER_HOST", kwargs["env"])
            self.assertNotIn("DOCKER_CONTEXT", kwargs["env"])
            self.assertNotIn("DOCKER_CONFIG", kwargs["env"])
            self.assertEqual(kwargs["env"]["HOME"], "/var/lib/porota-rc6")
            self.assertEqual(kwargs["env"]["XDG_RUNTIME_DIR"], "/run/user/1808")
            self.assertEqual(kwargs["max_bytes"], 16384)
            return json.dumps(self.metadata)

        with (mock.patch.object(probe.pwd, "getpwnam", return_value=self.account),
              mock.patch.object(probe.pwd, "getpwall", return_value=self.accounts),
              mock.patch.object(probe.os, "geteuid", return_value=self.effective_uid),
              mock.patch.object(probe.os, "lstat", side_effect=fake_stat),
              mock.patch.object(probe, "_run_bounded_command", side_effect=fake_run),
              mock.patch.dict(probe.os.environ, {
                  "DOCKER_HOST": "tcp://foreign.example:2375",
                  "DOCKER_CONTEXT": "foreign", "DOCKER_CONFIG": "/tmp/foreign",
              })):
            return probe.collect()

    def test_private_socket_is_explicit_and_receipt_does_not_leak_host_metadata(self):
        receipt = self.collect()
        self.assertEqual(receipt, {
            "schema": "rc6.r1.rootless-private-readonly.v1",
            "state": "READ_ONLY_OBSERVED_NOT_CERTIFIED",
            "read_only": True, "mutations": 0, "provider_gets": 0,
            "porota_private_account": True,
            "porota_private_socket": True,
            "porota_rootless_engine": True,
            "porota_private_data_root": True,
            "foreign_docker_inspected": False,
            "rootless_certified": False,
        })
        data = json.dumps(receipt)
        self.assertNotIn("PRIVATE_HOST_SECRET", data)
        self.assertNotIn("/run/user/", data)
        self.assertNotIn("/var/lib/", data)
        self.assertNotIn("FOREIGN", data)

    def test_missing_private_socket_blocks_before_any_docker_query(self):
        del self.paths[self.socket]
        with self.assertRaises(probe.ProbeError) as failure:
            self.collect()
        self.assertEqual(failure.exception.reason, "PRIVATE_SOCKET_UNVERIFIED")

    def test_root_or_wrong_user_cannot_be_certified(self):
        for invalid_uid in (0, 999):
            with self.subTest(uid=invalid_uid):
                self.account.pw_uid = invalid_uid
                with self.assertRaises(probe.ProbeError) as failure:
                    self.collect()
                self.assertEqual(failure.exception.reason, "DEDICATED_ACCOUNT_UNVERIFIED")

    def test_shared_numeric_uid_is_not_a_dedicated_account(self):
        self.accounts.append(SimpleNamespace(pw_name="foreign-app", pw_uid=1808))
        with self.assertRaises(probe.ProbeError) as failure:
            self.collect()
        self.assertEqual(failure.exception.reason, "DEDICATED_ACCOUNT_UNVERIFIED")

    def test_ssh_caller_must_be_dedicated_unprivileged_porota_user(self):
        self.effective_uid = 0
        with self.assertRaises(probe.ProbeError) as failure:
            self.collect()
        self.assertEqual(failure.exception.reason, "REMOTE_UID_NOT_PRIVATE")

    def test_shared_runtime_directory_or_symlink_socket_is_rejected(self):
        changes = (
            ("/run/user/1808", _entry(stat.S_IFDIR, mode=0o777)),
            (self.socket, _entry(stat.S_IFLNK, mode=0o600)),
            (self.socket, _entry(stat.S_IFSOCK, mode=0o666, uid=1701)),
        )
        for path, bad in changes:
            with self.subTest(path=path, mode=bad.st_mode, uid=bad.st_uid):
                original = self.paths[path]
                self.paths[path] = bad
                try:
                    with self.assertRaises(probe.ProbeError):
                        self.collect()
                finally:
                    self.paths[path] = original

    def test_rootful_engine_cannot_emit_success(self):
        self.metadata["SecurityOptions"] = ["name=seccomp,profile=builtin"]
        with self.assertRaises(probe.ProbeError) as failure:
            self.collect()
        self.assertEqual(failure.exception.reason, "ENGINE_NOT_ROOTLESS")

    def test_data_root_outside_private_home_or_symlink_is_rejected(self):
        self.metadata["DockerRootDir"] = "/var/lib/docker"
        with self.assertRaises(probe.ProbeError) as failure:
            self.collect()
        self.assertEqual(failure.exception.reason, "DATA_ROOT_OUTSIDE_PRIVATE_HOME")
        self.metadata["DockerRootDir"] = self.root
        self.paths[self.root] = _entry(stat.S_IFLNK, mode=0o700)
        with self.assertRaises(probe.ProbeError):
            self.collect()

    def test_failure_receipt_is_allowlisted_and_redacted(self):
        output = io.StringIO()
        with (mock.patch.object(probe, "collect",
                                side_effect=probe.ProbeError("PRIVATE_SOCKET_UNVERIFIED")),
              contextlib.redirect_stdout(output)):
            self.assertEqual(probe.main(), 0)
        self.assertEqual(json.loads(output.getvalue()), {
            "schema": "rc6.r1.rootless-private-readonly.v1",
            "state": "NO_VERIFICADO",
            "read_only": True, "mutations": 0, "provider_gets": 0,
            "rootless_certified": False,
            "reason": "PRIVATE_SOCKET_UNVERIFIED",
        })

    def test_external_process_output_is_bounded_before_allocation(self):
        self.assertTrue(hasattr(probe, "_run_bounded_command"))
        started = time.monotonic()
        with self.assertRaises(probe.ProbeError) as failure:
            probe._run_bounded_command([
                sys.executable, "-c",
                "import sys,time;sys.stdout.write('X'*131072);sys.stdout.flush();time.sleep(3)",
            ], env=os.environ.copy(), timeout=2, max_bytes=1024)
        self.assertEqual(failure.exception.reason, "PRIVATE_ENGINE_UNVERIFIED")
        self.assertLess(time.monotonic() - started, 1.5)

    def test_external_process_with_no_output_has_bounded_timeout(self):
        self.assertTrue(hasattr(probe, "_run_bounded_command"))
        started = time.monotonic()
        with self.assertRaises(probe.ProbeError) as failure:
            probe._run_bounded_command([
                sys.executable, "-c", "import time;time.sleep(3)",
            ], env=os.environ.copy(), timeout=0.2, max_bytes=1024)
        self.assertEqual(failure.exception.reason, "PRIVATE_ENGINE_UNVERIFIED")
        self.assertLess(time.monotonic() - started, 1.5)

    def test_small_external_process_output_is_preserved(self):
        self.assertTrue(hasattr(probe, "_run_bounded_command"))
        output = probe._run_bounded_command([
            sys.executable, "-c", "print('{\"status\":\"ok\"}')",
        ], env=os.environ.copy(), timeout=2, max_bytes=1024)
        self.assertEqual(output.strip(), '{"status":"ok"}')


class RootlessWorkflowModeTests(unittest.TestCase):
    def test_opt_in_isolates_rootless_job_from_old_capacity_docker_path(self):
        source = WORKFLOW.read_text()

        # Bounded same-indentation YAML step extraction, using stdlib so this
        # guard runs unmodified on stock Actions Python without pip installs.
        def step(name):
            header = "      - name: " + name + "\n"
            self.assertIn(header, source)
            body = source.split(header, 1)[1]
            return body.split("\n      - name: ", 1)[0]

        self.assertIn("      rootless_metadata_only:\n", source)
        self.assertIn("        type: boolean\n        default: false\n", source)
        sha_input = source.split("      expected_deployed_sha:\n", 1)[1].split(
            "\n      inspect_rc6_timers:", 1)[0]
        self.assertIn("required: false", sha_input)
        job = source.split("\n  host_readonly:\n", 1)[1]
        self.assertIn("group: rc6-unified-paper-deploy", job)
        self.assertIn("cancel-in-progress: false", job)
        self.assertIn("refs/heads/main", job)
        self.assertIn("ROOTLESS_METADATA_ONLY: ${{ inputs.rootless_metadata_only }}", job)
        binding = step("Bound SHA and runner-only SSH credentials")
        self.assertIn('if [[ "$ROOTLESS_METADATA_ONLY" == "true" ]]', binding)
        self.assertIn('else', binding)
        self.assertIn('[[ "$EXPECTED_DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]]', binding)
        old = step("Read filesystem and Docker metadata only (max 35 seconds)")
        timer = step("Inspect RC6 timer contract READ_ONLY")
        new = step("Read private Porota rootless metadata only")
        self.assertIn("if: ${{ !inputs.rootless_metadata_only }}", old)
        self.assertIn("if: ${{ !inputs.rootless_metadata_only && inputs.inspect_rc6_timers }}", timer)
        self.assertIn("if: ${{ inputs.rootless_metadata_only }}", new)
        self.assertIn("StrictHostKeyChecking=yes", new)
        self.assertIn('test "$(id -u)" = "$(id -u porota-rc6)"', new)
        self.assertIn("rc6_rootless_metadata_readonly.py", new)
        self.assertNotIn("sudo -n docker", new)
        self.assertNotIn("docker ps", new)


if __name__ == "__main__":
    unittest.main()
