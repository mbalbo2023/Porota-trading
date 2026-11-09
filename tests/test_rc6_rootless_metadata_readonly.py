"""A998: opt-in Porota-only rootless metadata, without shared Docker access."""

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import subprocess
import tempfile
import textwrap
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


class CurrentLoginReadOnlyTests(unittest.TestCase):
    """A1013: reuse this probe for the existing nonroot operator UID 1000."""

    def setUp(self):
        self.account = SimpleNamespace(
            pw_name="private-operator-name", pw_uid=1000, pw_gid=1000,
            pw_dir="/home/private-operator-name",
        )
        self.entries = [self.account]
        self.paths = {
            "/home/private-operator-name": _entry(stat.S_IFDIR, mode=0o750, uid=1000),
            "/run/user/1000": _entry(stat.S_IFDIR, mode=0o700, uid=1000),
            "/run/user/1000/bus": _entry(stat.S_IFSOCK, mode=0o660, uid=1000),
            "/usr": _entry(stat.S_IFDIR, mode=0o755, uid=0),
            "/usr/bin": _entry(stat.S_IFDIR, mode=0o755, uid=0),
            "/usr/bin/dockerd-rootless.sh": _entry(stat.S_IFREG, mode=0o755, uid=0),
            "/var": _entry(stat.S_IFDIR, mode=0o755, uid=0),
            "/var/lib": _entry(stat.S_IFDIR, mode=0o755, uid=0),
            "/var/lib/systemd": _entry(stat.S_IFDIR, mode=0o755, uid=0),
            "/var/lib/systemd/linger": _entry(stat.S_IFDIR, mode=0o755, uid=0),
            "/var/lib/systemd/linger/private-operator-name": _entry(
                stat.S_IFREG, mode=0o644, uid=0),
        }

    def inspect(self, *, uid=1000, subids=True, tooling=True, userns=True):
        def fake_lstat(path):
            path = str(path)
            if path in self.paths:
                return self.paths[path]
            raise FileNotFoundError(path)

        def forbidden(*args, **kwargs):
            raise AssertionError("current-login READ_ONLY must not run commands or connect")

        with (mock.patch.object(probe.os, "geteuid", return_value=uid),
              mock.patch.object(probe.pwd, "getpwuid", return_value=self.account),
              mock.patch.object(probe.pwd, "getpwall", return_value=self.entries),
              mock.patch.object(probe.os, "lstat", side_effect=fake_lstat),
              mock.patch.object(probe, "_login_subids_ready", return_value=subids),
              mock.patch.object(probe, "_login_rootless_toolchain", return_value=tooling),
              mock.patch.object(probe, "_login_userns_ready", return_value=userns),
              mock.patch.object(probe.subprocess, "run", side_effect=forbidden),
              mock.patch.object(probe.subprocess, "Popen", side_effect=forbidden)):
            return probe.collect_current_login_readonly()

    def test_existing_login_probe_observes_only_os_prereqs_and_never_certifies(self):
        receipt = self.inspect()
        self.assertEqual(receipt, {
            "schema": "rc6.r1.current-login-capability-readonly.v1",
            "state": "NO_VERIFICADO",
            "read_only": True,
            "mutations": 0,
            "provider_gets": 0,
            "docker_queries": 0,
            "socket_connections": 0,
            "sudo_executed": False,
            "foreign_docker_inspected": False,
            "principal_nonroot_unique": True,
            "runtime_directory_private": True,
            "user_manager_socket_present": True,
            "rootless_tools_present": True,
            "rootless_launcher_present": True,
            "user_linger_record_present": True,
            "subuids_assigned": True,
            "userns_enabled": True,
            "default_socket_absent": True,
            "project_socket_namespace_absent": True,
            "ready_for_private_rootless_review": True,
            "rootless_certified": False,
        })
        raw = json.dumps(receipt)
        self.assertNotIn(self.account.pw_name, raw)
        self.assertNotIn(self.account.pw_dir, raw)
        self.assertNotIn("/run/user/", raw)

    def test_root_other_uid_or_duplicate_uid_fails_closed(self):
        for invalid_uid in (0, 999, 1001):
            with self.subTest(uid=invalid_uid):
                with self.assertRaises(probe.ProbeError):
                    self.inspect(uid=invalid_uid)
        self.entries.append(SimpleNamespace(pw_name="some-other-user", pw_uid=1000))
        with self.assertRaises(probe.ProbeError):
            self.inspect()

    def test_unsafe_home_runtime_or_bus_fails_without_docker_query(self):
        bad = (
            ("/home/private-operator-name", _entry(stat.S_IFLNK, mode=0o777, uid=1000)),
            ("/run/user/1000", _entry(stat.S_IFDIR, mode=0o777, uid=1000)),
            ("/run/user/1000/bus", _entry(stat.S_IFSOCK, mode=0o660, uid=2022)),
        )
        for path, entry in bad:
            with self.subTest(path=path):
                before = self.paths[path]
                self.paths[path] = entry
                try:
                    with self.assertRaises(probe.ProbeError):
                        self.inspect()
                finally:
                    self.paths[path] = before

    def test_occupied_or_symlinked_socket_is_ambiguous_and_never_connected(self):
        for path in ("/run/user/1000/docker.sock", "/run/user/1000/porota-rc6"):
            for entry in (
                _entry(stat.S_IFSOCK, mode=0o660, uid=1000),
                _entry(stat.S_IFLNK, mode=0o777, uid=1000),
                _entry(stat.S_IFDIR, mode=0o700, uid=1000),
            ):
                with self.subTest(path=path, mode=entry.st_mode):
                    self.paths[path] = entry
                    try:
                        result = self.inspect()
                        self.assertFalse(result["ready_for_private_rootless_review"])
                        self.assertFalse(result["default_socket_absent"] if path.endswith("docker.sock")
                                         else result["project_socket_namespace_absent"])
                    finally:
                        del self.paths[path]

    def test_readiness_blocks_when_identity_mappings_or_tooling_missing(self):
        for kwargs in ({"subids": False}, {"tooling": False}, {"userns": False}):
            with self.subTest(kwargs=kwargs):
                observed = self.inspect(**kwargs)
                self.assertFalse(observed["ready_for_private_rootless_review"])
                self.assertFalse(observed["rootless_certified"])

    def test_missing_or_untrusted_rootless_launcher_is_not_install_readiness(self):
        path = "/usr/bin/dockerd-rootless.sh"
        original = self.paths[path]
        alternatives = (
            None,
            _entry(stat.S_IFLNK, mode=0o755, uid=0),
            _entry(stat.S_IFREG, mode=0o755, uid=1000),
            _entry(stat.S_IFREG, mode=0o775, uid=0),
            _entry(stat.S_IFREG, mode=0o644, uid=0),
            _entry(stat.S_IFREG, mode=0o700, uid=0),
            _entry(stat.S_IFREG, mode=0o711, uid=0),
        )
        for entry in alternatives:
            with self.subTest(entry=entry):
                if entry is None:
                    self.paths.pop(path, None)
                else:
                    self.paths[path] = entry
                observed = self.inspect()
                self.assertFalse(observed["rootless_launcher_present"])
                self.assertFalse(observed["ready_for_private_rootless_review"])
                self.assertFalse(observed["rootless_certified"])
        self.paths[path] = original
        self.assertTrue(self.inspect()["rootless_launcher_present"])

    def test_launcher_parent_must_be_trusted_before_reporting_available(self):
        parent = '/usr'
        original = self.paths[parent]
        for entry in (None,
                      _entry(stat.S_IFLNK, mode=0o755, uid=0),
                      _entry(stat.S_IFDIR, mode=0o777, uid=0),
                      _entry(stat.S_IFDIR, mode=0o755, uid=1000)):
            with self.subTest(entry=entry):
                if entry is None:
                    self.paths.pop(parent, None)
                else:
                    self.paths[parent] = entry
                try:
                    observed = self.inspect()
                    self.assertFalse(observed['rootless_launcher_present'])
                    self.assertFalse(observed['ready_for_private_rootless_review'])
                finally:
                    self.paths[parent] = original

    def test_linger_marker_is_only_trusted_root_owned_file_metadata(self):
        directory = "/var/lib/systemd/linger"
        marker = directory + "/private-operator-name"
        cases = (
            (marker, None),
            (marker, _entry(stat.S_IFLNK, mode=0o777, uid=0)),
            (marker, _entry(stat.S_IFREG, mode=0o644, uid=1000)),
            (marker, _entry(stat.S_IFREG, mode=0o666, uid=0)),
            (directory, _entry(stat.S_IFLNK, mode=0o755, uid=0)),
            (directory, _entry(stat.S_IFDIR, mode=0o777, uid=0)),
        )
        for path, entry in cases:
            with self.subTest(path=path, entry=entry):
                original = self.paths[path]
                if entry is None:
                    self.paths.pop(path)
                else:
                    self.paths[path] = entry
                try:
                    observed = self.inspect()
                    self.assertFalse(observed["user_linger_record_present"])
                    self.assertFalse(observed["ready_for_private_rootless_review"])
                    self.assertFalse(observed["rootless_certified"])
                finally:
                    self.paths[path] = original

    def test_current_login_subid_metadata_only_accepts_exclusive_65536_ranges(self):
        def check(uid_text, gid_text):
            with mock.patch.object(probe, "_login_subid_text",
                                   side_effect=lambda p: uid_text if p == "/etc/subuid" else gid_text):
                return probe._login_subids_ready(self.account)

        self.assertTrue(check("private-operator-name:100000:65536\nother:300000:65536\n",
                              "private-operator-name:100000:65536\n"))
        self.assertFalse(check("other:100000:65536\n", "private-operator-name:200000:65536\n"))
        self.assertFalse(check("private-operator-name:100000:65535\n",
                               "private-operator-name:100000:65536\n"))
        self.assertFalse(check("private-operator-name:100000:65536\nother:150000:65536\n",
                               "private-operator-name:100000:65536\n"))
        self.assertFalse(check("private-operator-name:0:65536\n",
                               "private-operator-name:100000:65536\n"))
        self.assertFalse(check("private-operator-name:100000:65536\n"
                               "private-operator-name:300000:65536\nother:350000:65536\n",
                               "private-operator-name:100000:65536\n"))


class CurrentLoginWorkflowTests(unittest.TestCase):
    def test_runner_receipt_rejects_missing_launcher_or_linger_without_certifying(self):
        job = WORKFLOW.read_text().split('\n  current_login_readonly:\n', 1)[1]
        consumer = job.split('\n      - name: Read current login OS metadata without Docker or sudo\n', 1)[1]
        consumer = consumer.split('\n      - name: Runner-only current-login credential cleanup\n', 1)[0]
        parser = textwrap.dedent(consumer.split("          python3 - <<'PY'\n", 1)[1]
                                 .split('\n          PY', 1)[0])
        positive = {
            'schema': 'rc6.r1.current-login-capability-readonly.v1',
            'state': 'NO_VERIFICADO', 'read_only': True,
            'mutations': 0, 'provider_gets': 0, 'docker_queries': 0,
            'socket_connections': 0, 'sudo_executed': False,
            'rootless_certified': False, 'foreign_docker_inspected': False,
            'principal_nonroot_unique': True, 'runtime_directory_private': True,
            'user_manager_socket_present': True, 'rootless_tools_present': True,
            'rootless_launcher_present': True, 'user_linger_record_present': True,
            'subuids_assigned': True, 'userns_enabled': True,
            'default_socket_absent': True,
            'project_socket_namespace_absent': True,
            'ready_for_private_rootless_review': True,
        }
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'rc6_current_login_ro'
            dest.mkdir()
            receipt_file = dest / 'receipt.json'
            summary_file = Path(temp) / 'summary.md'
            env = dict(os.environ, RUNNER_TEMP=temp, GITHUB_STEP_SUMMARY=str(summary_file))

            def run(data):
                receipt_file.write_text(json.dumps(data), encoding='utf-8')
                return subprocess.run([sys.executable, '-I', '-B', '-c', parser],
                                      text=True, capture_output=True, timeout=4, env=env)

            success = run(positive)
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertIn('PREREQUISITES_READY_NOT_CERTIFIED|CERT2=NO_VERIFICADO',
                          success.stdout)
            for missing in ('rootless_launcher_present', 'user_linger_record_present'):
                with self.subTest(missing=missing):
                    incomplete = dict(positive, **{missing: False,
                                                   'ready_for_private_rootless_review': False})
                    result = run(incomplete)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('PREREQUISITES_INCOMPLETE|CERT2=NO_VERIFICADO',
                                  result.stderr + result.stdout)
                    absent = dict(positive)
                    del absent[missing]
                    result = run(absent)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('RECEIPT_INVALID|CERT2=NO_VERIFICADO',
                                  result.stderr + result.stdout)

    def test_explicit_opt_in_skips_entire_legacy_rootful_and_dedicated_host_job(self):
        source = WORKFLOW.read_text()
        self.assertIn("current_login_capability_only:", source)
        self.assertIn("default: false", source)
        host = source.split("\n  host_readonly:\n", 1)[1].split("\n  current_login_readonly:\n", 1)[0]
        self.assertIn("!inputs.current_login_capability_only", host.split("\n    concurrency:", 1)[0])
        new = source.split("\n  current_login_readonly:\n", 1)[1]
        self.assertIn("inputs.current_login_capability_only", new)
        self.assertNotIn("group: rc6-unified-paper-deploy", new)
        self.assertIn("StrictHostKeyChecking=yes", new)
        self.assertIn("--current-login", new)
        self.assertNotIn("sudo -n", new)
        self.assertNotIn("docker info", new)
        self.assertNotIn("docker ps", new)

    def test_protected_main_trust_step_denies_untrusted_branch_before_checkout(self):
        source = WORKFLOW.read_text()
        new = source.split("\n  current_login_readonly:\n", 1)[1]
        guard = new.split("      - name: Validate current-login trusted context\n", 1)[1].split("\n      - ", 1)[0]
        script = textwrap.dedent(guard.split("        run: |\n", 1)[1])
        env = dict(os.environ, GITHUB_REPOSITORY="mbalbo2023/Porota-trading",
                   GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF="refs/heads/main",
                   GITHUB_REF_PROTECTED="true", GITHUB_WORKFLOW_SHA="a" * 40,
                   GITHUB_SHA="a" * 40, RUNNER_ENVIRONMENT="github-hosted",
                   GITHUB_WORKFLOW_REF=("mbalbo2023/Porota-trading/.github/workflows/"
                                        "rc6-r1-docker-capacity-readonly.yml@refs/heads/main"))
        good = subprocess.run(["bash", "-e", "-o", "pipefail"], input=script,
                              text=True, capture_output=True, env=env, timeout=3)
        self.assertEqual(good.returncode, 0, good.stderr)
        for field, value in (("GITHUB_REF", "refs/heads/unsafe"),
                             ("GITHUB_REF_PROTECTED", "false"),
                             ("GITHUB_WORKFLOW_SHA", "b" * 40)):
            with self.subTest(field=field):
                bad = subprocess.run(["bash", "-e", "-o", "pipefail"],
                                     input=script, text=True, capture_output=True,
                                     env=dict(env, **{field: value}), timeout=3)
                self.assertEqual(bad.returncode, 79)
                self.assertIn("UNTRUSTED_CONTEXT", bad.stdout)

    def test_current_login_secrets_only_after_trust_gate_and_pinned_checkout(self):
        source = WORKFLOW.read_text()
        new = source.split("\n  current_login_readonly:\n", 1)[1]
        self.assertLess(new.index("Validate current-login trusted context"),
                        new.index("actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"))
        self.assertLess(new.index("persist-credentials: false"), new.index("secrets.DO_SSH_KEY"))
        self.assertNotIn("print(json.dumps(data", new)


if __name__ == "__main__":
    unittest.main()
