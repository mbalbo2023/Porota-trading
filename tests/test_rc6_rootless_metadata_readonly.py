"""A998: opt-in Porota-only rootless metadata, without shared Docker access."""

import contextlib
import errno
import grp
import io
import json
import os
from pathlib import Path
import stat
import socket
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
        gate = step("Require independent physical owner binding before any host access")
        timer = step("Inspect RC6 timer contract READ_ONLY")
        new = step("Read private Porota rootless metadata only")
        self.assertIn('if [[ "$ROOTLESS_METADATA_ONLY" == "true" ]]', gate)
        self.assertLess(source.index('      - name: Require independent physical owner binding before any host access\n'),
                        source.index('      - name: Bound SHA and runner-only SSH credentials\n'))
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

    def inspect(self, *, uid=1000, subids=True, tooling=True, userns=True,
                lstat_errors=None):
        def fake_lstat(path):
            path = str(path)
            if lstat_errors and path in lstat_errors:
                raise lstat_errors[path]
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
            "user_linger_marker_status": "TRUSTED",
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
            (marker, None, 'MARKER_MISSING'),
            (marker, _entry(stat.S_IFLNK, mode=0o777, uid=0), 'MARKER_UNTRUSTED'),
            (marker, _entry(stat.S_IFDIR, mode=0o755, uid=0), 'MARKER_UNTRUSTED'),
            (marker, _entry(stat.S_IFREG, mode=0o644, uid=1000), 'MARKER_UNTRUSTED'),
            (marker, _entry(stat.S_IFREG, mode=0o666, uid=0), 'MARKER_UNTRUSTED'),
            (directory, None, 'PARENT_MISSING'),
            (directory, _entry(stat.S_IFLNK, mode=0o755, uid=0), 'PARENT_UNTRUSTED'),
            (directory, _entry(stat.S_IFREG, mode=0o644, uid=0), 'PARENT_UNTRUSTED'),
            (directory, _entry(stat.S_IFDIR, mode=0o777, uid=0), 'PARENT_UNTRUSTED'),
            (directory, _entry(stat.S_IFDIR, mode=0o755, uid=1000), 'PARENT_UNTRUSTED'),
        )
        for path, entry, expected in cases:
            with self.subTest(path=path, entry=entry, expected=expected):
                original = self.paths[path]
                if entry is None:
                    self.paths.pop(path)
                else:
                    self.paths[path] = entry
                try:
                    observed = self.inspect()
                    self.assertFalse(observed["user_linger_record_present"])
                    self.assertEqual(observed['user_linger_marker_status'], expected)
                    self.assertFalse(observed["ready_for_private_rootless_review"])
                    self.assertFalse(observed["rootless_certified"])
                finally:
                    self.paths[path] = original

        for path, expected in ((marker, 'MARKER_UNVERIFIED'),
                               (directory, 'PARENT_UNVERIFIED')):
            with self.subTest(path=path, expected=expected):
                observed = self.inspect(lstat_errors={path: PermissionError('PRIVATE_PATH')})
                self.assertEqual(observed['user_linger_marker_status'], expected)
                self.assertFalse(observed['user_linger_record_present'])
                self.assertFalse(observed['ready_for_private_rootless_review'])
                self.assertNotIn('PRIVATE_PATH', json.dumps(observed))

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
        self.assertFalse(check("private-operator-name:100000:65536\n"
                               "private-operator-name:120000:65536\n",
                               "private-operator-name:100000:65536\n"))


class DedicatedAccountPreflightTests(unittest.TestCase):
    """A1047: probe dedicated-account prerequisites as UID1000, no mutation."""

    def setUp(self):
        self.login = SimpleNamespace(pw_name='porotaadmin', pw_uid=1000,
                                     pw_gid=1000, pw_dir='/home/porotaadmin')
        self.target = SimpleNamespace(pw_name='porota-rc6', pw_uid=1808,
                                      pw_gid=1808, pw_dir='/var/lib/porota-rc6')
        self.absent = True
        self.passwd = 'porotaadmin:x:1000:1000::/home/porotaadmin:/bin/bash\n'
        self.groups = 'porotaadmin:x:1000:\n'
        self.paths = {'/var': _entry(stat.S_IFDIR, 0o755, 0),
                      '/var/lib': _entry(stat.S_IFDIR, 0o755, 0),
                      '/home': _entry(stat.S_IFDIR, 0o755, 0),
                      '/home/porotaadmin': _entry(stat.S_IFDIR, 0o750, 1000)}
        self.port = ('NONE_OBSERVED', 'NONE')
        self.subids = True
        self.tools = True
        self.launcher = True
        self.userns = True

    def inspect(self, uid=1000, through_main=False):
        def getpwnam(name):
            if name == 'porotaadmin':
                return self.login
            if name == 'porota-rc6' and not self.absent:
                return self.target
            raise KeyError('PRIVATE_NSS_SECRET')

        def getpwuid(number):
            if number == 1000:
                return self.login
            if number == self.target.pw_uid and not self.absent:
                return self.target
            raise KeyError('PRIVATE_UID_SECRET')

        def getgrnam(name):
            if name == 'porotaadmin':
                return SimpleNamespace(gr_name=name, gr_gid=1000)
            if name == 'porota-rc6' and not self.absent:
                return SimpleNamespace(gr_name=name, gr_gid=self.target.pw_gid)
            raise KeyError('PRIVATE_GROUP_SECRET')

        def getgrgid(number):
            if number == 1000:
                return SimpleNamespace(gr_name='porotaadmin', gr_gid=1000)
            if number == self.target.pw_gid and not self.absent:
                return SimpleNamespace(gr_name='porota-rc6', gr_gid=number)
            raise KeyError('PRIVATE_GID_SECRET')

        def lstat(path):
            if str(path) not in self.paths:
                raise FileNotFoundError(str(path))
            return self.paths[str(path)]

        def forbid(*args, **kwargs):
            raise AssertionError('dedicated preflight cannot execute commands or sockets')

        with (mock.patch.object(probe.os, 'geteuid', return_value=uid),
              mock.patch.object(probe.pwd, 'getpwnam', side_effect=getpwnam),
              mock.patch.object(probe.pwd, 'getpwuid', side_effect=getpwuid),
              mock.patch.object(grp, 'getgrnam', side_effect=getgrnam),
              mock.patch.object(grp, 'getgrgid', side_effect=getgrgid),
              mock.patch.object(probe, '_login_subid_text',
                                side_effect=lambda p: self.groups if p == '/etc/group'
                                else self.passwd),
              mock.patch.object(probe.os, 'lstat', side_effect=lstat),
              mock.patch.object(probe, '_login_subids_ready', return_value=self.subids),
              mock.patch.object(probe, '_login_rootless_toolchain', return_value=self.tools),
              mock.patch.object(probe, '_login_rootless_launcher_present',
                                return_value=self.launcher),
              mock.patch.object(probe, '_login_userns_ready', return_value=self.userns),
              mock.patch.object(probe, '_port_8000_listener', return_value=self.port),
              mock.patch.object(probe.subprocess, 'run', side_effect=forbid),
              mock.patch.object(probe.subprocess, 'Popen', side_effect=forbid),
              mock.patch.object(socket, 'socket', side_effect=forbid)):
            if through_main:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = probe.main_dedicated_preflight()
                return code, output.getvalue()
            return probe.collect_dedicated_preflight()

    def test_missing_account_does_not_infer_orphan_subids_are_absent(self):
        receipt = self.inspect()
        self.assertEqual(receipt['schema'],
                         'rc6.r1.dedicated-account-preflight-readonly.v1')
        self.assertEqual(receipt['state'], 'NO_VERIFICADO')
        self.assertEqual(receipt['dedicated_account'], 'ABSENT')
        self.assertEqual(receipt['dedicated_uid'], 'NOT_ASSIGNED')
        self.assertEqual(receipt['dedicated_home'], 'NOT_ASSIGNED')
        self.assertEqual(receipt['dedicated_subids'], 'UNVERIFIED')
        self.assertEqual(receipt['dedicated_socket'], 'NOT_ASSIGNED')
        self.assertEqual(receipt['rootless_toolchain'], 'AVAILABLE')
        self.assertEqual(receipt['rootless_launcher'], 'AVAILABLE')
        self.assertEqual(receipt['user_namespace'], 'AVAILABLE')
        self.assertEqual(receipt['port_8000_listen'], 'NONE_OBSERVED')
        self.assertEqual(receipt['port_8000_reason'], 'NONE')
        for key, value in (('mutations', 0), ('docker_queries', 0),
                           ('direct_socket_connections', 0),
                           ('nss_socket_activity', 'UNVERIFIED'),
                           ('sudo_executed', False),
                           ('systemctl_queries', 0), ('rootless_certified', False)):
            self.assertEqual(receipt[key], value)
        for token in ('porotaadmin', 'porota-rc6', '/var/lib', 'PRIVATE_NSS_SECRET'):
            self.assertNotIn(token, json.dumps(receipt))

    def test_present_private_account_is_observed_not_certified(self):
        self.absent = False
        self.passwd += 'porota-rc6:x:1808:1808::/var/lib/porota-rc6:/usr/sbin/nologin\n'
        self.groups += 'porota-rc6:x:1808:\n'
        self.paths['/var/lib/porota-rc6'] = _entry(stat.S_IFDIR, 0o700, 1808)
        receipt = self.inspect()
        self.assertEqual(receipt['dedicated_account'], 'PRESENT')
        self.assertEqual(receipt['dedicated_uid'], 'DISTINCT')
        self.assertEqual(receipt['dedicated_home'], 'PRIVATE')
        self.assertEqual(receipt['dedicated_subids'], 'AVAILABLE')
        self.assertEqual(receipt['dedicated_socket'], 'NOT_INSPECTED')
        self.assertFalse(receipt['rootless_certified'])

    def test_unsafe_identity_or_home_is_never_positive(self):
        with self.assertRaises(probe.ProbeError) as caught:
            self.inspect(uid=0)
        self.assertEqual(caught.exception.reason, 'DEDICATED_LOGIN_UNVERIFIED')
        self.absent = False
        self.passwd += 'porota-rc6:x:1808:1808::/var/lib/porota-rc6:/usr/sbin/nologin\n'
        self.groups += 'porota-rc6:x:1808:\n'
        self.paths['/var/lib/porota-rc6'] = _entry(stat.S_IFDIR, 0o700, 1808)
        for change in ('duplicate', 'symlink', 'mode', 'bad_home', 'subids'):
            with self.subTest(change=change):
                old = (self.passwd, self.paths['/var/lib/porota-rc6'],
                       self.target.pw_dir, self.subids)
                if change == 'duplicate':
                    self.passwd += 'foreign:x:1808:1808::/home/foreign:/bin/false\n'
                elif change == 'symlink':
                    self.paths['/var/lib/porota-rc6'] = _entry(stat.S_IFLNK, 0o700, 1808)
                elif change == 'mode':
                    self.paths['/var/lib/porota-rc6'] = _entry(stat.S_IFDIR, 0o777, 1808)
                elif change == 'bad_home':
                    self.target.pw_dir = '/var/lib/foreign-private-path'
                elif change == 'subids':
                    self.subids = False
                try:
                    receipt = self.inspect()
                    self.assertNotEqual((receipt['dedicated_uid'], receipt['dedicated_home'],
                                         receipt['dedicated_subids']),
                                        ('DISTINCT', 'PRIVATE', 'AVAILABLE'))
                finally:
                    (self.passwd, self.paths['/var/lib/porota-rc6'],
                     self.target.pw_dir, self.subids) = old

    def test_actual_json_never_exposes_nss_or_paths(self):
        code, text = self.inspect(through_main=True)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)['dedicated_account'], 'ABSENT')
        self.assertNotIn('porotaadmin', text)
        self.assertNotIn('porota-rc6', text)
        self.assertNotIn('/var/lib', text)

    def test_actual_isolated_stdin_python_mode_emits_only_categorical_receipt(self):
        source = Path(probe.__file__).read_text(encoding='utf-8')
        observed = subprocess.run([sys.executable, '-I', '-S', '-B', '-',
                                   '--dedicated-preflight'], input=source, text=True,
                                  capture_output=True, timeout=5)
        self.assertEqual(observed.returncode, 0, observed.stderr)
        self.assertFalse(observed.stderr)
        receipt = json.loads(observed.stdout)
        self.assertEqual(receipt['schema'], probe.DEDICATED_PREFLIGHT_SCHEMA)
        self.assertEqual(receipt['state'], 'NO_VERIFICADO')
        self.assertIs(receipt['read_only'], True)
        self.assertEqual(receipt['mutations'], 0)
        self.assertEqual(receipt['docker_queries'], 0)
        self.assertIs(receipt['rootless_certified'], False)
        self.assertNotIn('porotaadmin', observed.stdout)
        self.assertNotIn('porota-rc6', observed.stdout)
        self.assertNotIn('/var/lib', observed.stdout)

    def test_absent_account_with_preexisting_candidate_home_is_not_a_free_namespace(self):
        self.paths['/var/lib/porota-rc6'] = _entry(stat.S_IFDIR, 0o700, 999)
        receipt = self.inspect()
        self.assertEqual(receipt['dedicated_home'], 'CANDIDATE_EXISTS')
        self.paths.pop('/var/lib/porota-rc6')
        receipt = self.inspect()
        self.assertEqual(receipt['dedicated_home'], 'NOT_ASSIGNED')
        self.paths['/home/porota-rc6'] = _entry(stat.S_IFLNK, 0o777, 999)
        receipt = self.inspect()
        self.assertEqual(receipt['dedicated_home'], 'CANDIDATE_EXISTS')

    def test_untrusted_current_login_home_is_rejected_before_target_inspection(self):
        self.paths['/home/porotaadmin'] = _entry(stat.S_IFLNK, 0o777, 1000)
        with self.assertRaises(probe.ProbeError) as caught:
            self.inspect()
        self.assertEqual(caught.exception.reason, 'DEDICATED_LOGIN_UNVERIFIED')

    def test_shared_or_duplicate_primary_gid_must_not_be_trusted(self):
        self.absent = False
        self.passwd += 'porota-rc6:x:1808:1808::/var/lib/porota-rc6:/usr/sbin/nologin\n'
        self.groups += 'porota-rc6:x:1808:\n'
        self.paths['/var/lib/porota-rc6'] = _entry(stat.S_IFDIR, 0o700, 1808)
        self.assertEqual(self.inspect()['dedicated_account'], 'PRESENT')
        self.groups += 'foreign:x:1808:\n'
        self.assertEqual(self.inspect()['dedicated_account'], 'UNVERIFIED')
        self.groups = 'porotaadmin:x:1000:\nporota-rc6:x:1000:\n'
        self.target.pw_gid = 1000
        self.assertEqual(self.inspect()['dedicated_account'], 'UNVERIFIED')

    def test_absent_account_with_orphan_local_primary_group_is_not_clean_absence(self):
        self.groups += 'porota-rc6:x:1808:\n'
        receipt = self.inspect()
        self.assertEqual(receipt['dedicated_account'], 'UNVERIFIED')
        self.assertEqual(receipt['dedicated_uid'], 'UNVERIFIED')
        self.assertEqual(receipt['dedicated_subids'], 'UNVERIFIED')

    def test_proc_net_reader_reads_until_eof_or_rejects_bound(self):
        original = (b'  sl local_address rem_address st\n',
                    b'  0: 00000000:1F40 00000000:0000 0A\n', b'')
        pieces = iter(original)
        with (mock.patch.object(probe.os, 'open', return_value=123),
              mock.patch.object(probe.os, 'fstat', return_value=_entry(stat.S_IFREG, 0o444, 0)),
              mock.patch.object(probe.os, 'read', side_effect=lambda *args: next(pieces)),
              mock.patch.object(probe.os, 'close')):
            self.assertEqual(probe._read_proc_net('/proc/self/net/tcp'),
                             original[0] + original[1])

    def test_root_owned_os_map_reads_until_eof_before_claiming_absence(self):
        pieces = iter((b'porotaadmin:100000:', b'65536\n', b''))
        metadata = SimpleNamespace(st_mode=stat.S_IFREG | 0o644,
                                   st_uid=0, st_size=27)
        with (mock.patch.object(probe.os, 'open', return_value=123),
              mock.patch.object(probe.os, 'fstat', return_value=metadata),
              mock.patch.object(probe.os, 'read', side_effect=lambda *_: next(pieces)),
              mock.patch.object(probe.os, 'close')):
            self.assertEqual(probe._login_subid_text('/etc/subuid'),
                             'porotaadmin:100000:65536\n')

    def test_rootless_toolchain_cannot_accept_symlink_leaf_or_parent(self):
        paths = ('/usr/bin/docker', '/usr/bin/rootlesskit',
                 '/usr/bin/dockerd-rootless-setuptool.sh', '/usr/bin/newuidmap',
                 '/usr/bin/newgidmap', '/usr/bin/loginctl', '/usr/bin/systemctl')
        entries = {'/usr': _entry(stat.S_IFDIR, 0o755, 0),
                   '/usr/bin': _entry(stat.S_IFDIR, 0o755, 0)}
        for path in paths:
            mode = 0o4755 if path.endswith(('newuidmap','newgidmap')) else 0o755
            entries[path] = _entry(stat.S_IFREG, mode, 0)

        def check():
            with (mock.patch.object(probe.os,'lstat',side_effect=lambda p: entries[p]),
                  mock.patch.object(probe.os,'stat',side_effect=AssertionError('FOLLOWED'))):
                return probe._login_rootless_toolchain()

        self.assertTrue(check())
        original = entries['/usr/bin/docker']
        entries['/usr/bin/docker'] = _entry(stat.S_IFLNK, 0o755, 0)
        self.assertFalse(check())
        entries['/usr/bin/docker'] = original
        entries['/usr/bin'] = _entry(stat.S_IFLNK, 0o755, 0)
        self.assertFalse(check())

    def test_port_status_requires_same_network_namespace_as_pid1(self):
        row = b'  sl local_address rem_address st\n'
        def different(path):
            return SimpleNamespace(st_dev=1, st_ino=2 if path == '/proc/1/ns/net' else 3)
        with (mock.patch.object(probe.os, 'stat', side_effect=different),
              mock.patch.object(probe, '_read_proc_net', return_value=row)):
            self.assertEqual(probe._port_8000_listener(),
                             ('UNVERIFIED', 'NETNS_MISMATCH'))
        def second_stat_unreadable(path):
            if path == '/proc/self/ns/net':
                raise PermissionError('PRIVATE_HOST_DATA')
            return SimpleNamespace(st_dev=1, st_ino=2)
        with (mock.patch.object(probe.os, 'stat', side_effect=second_stat_unreadable),
              mock.patch.object(probe, '_read_proc_net') as read_mock):
            self.assertEqual(probe._port_8000_listener(),
                             ('UNVERIFIED', 'NETNS_SELF_OTHER_OSERROR'))
            read_mock.assert_not_called()

    def test_proc_net_port_is_categorical_with_strict_bounds(self):
        ipv4 = ('  sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n'
                '  0: 00000000:1F40 00000000:0000 0A 00000000:00000000 '
                '00:00000000 00000000 1000 0 1234 1 0000000000000000 100 0 0 10 0\n').encode()
        ipv6 = ('  sl local_address remote_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n'
                '  0: 00000000000000000000000000000000:01BB '
                '00000000000000000000000000000000:01BB 01 00000000:00000000 '
                '00:00000000 00000000 1000 0 1234 1 0000000000000000 100 0 0 10 0\n').encode()
        def run(raw4, raw6):
            # Production must reject a foreign namespace, tested separately;
            # these parser fixtures must not depend on the hosted runner's
            # actual PID1/self namespace relationship.
            def same_netns(_path):
                return SimpleNamespace(st_dev=1, st_ino=2)
            with (mock.patch.object(probe, '_read_proc_net',
                                    side_effect=lambda path: raw4 if path.endswith('tcp') else raw6),
                  mock.patch.object(probe.os,'stat',side_effect=same_netns)):
                return probe._port_8000_listener()
        self.assertEqual(run(ipv4, ipv6), ('LISTEN_PRESENT', 'NONE'))
        self.assertEqual(run(ipv4.replace(b'0A', b'01'), ipv6),
                         ('NONE_OBSERVED', 'NONE'))
        self.assertEqual(run(ipv4.replace(b'1F40', b'1F41'), ipv6),
                         ('NONE_OBSERVED', 'NONE'))
        self.assertEqual(run(ipv4.replace(b' 0A ', b' FF '), ipv6),
                         ('UNVERIFIED', 'TCP4_PARSE_UNVERIFIED'))
        self.assertEqual(run(ipv4.split(b'\n')[0] + b'\n'
                             b'  0: 00000000:1F40 00000000:0000 01\n', ipv6),
                         ('UNVERIFIED', 'TCP4_PARSE_UNVERIFIED'))
        self.assertEqual(run(ipv4.replace(b'1F40', b'1F41'),
                             ipv6.replace(b':01BB ', b':1F40 ', 1)
                                 .replace(b' 01 00000000', b' 0A 00000000')),
                         ('LISTEN_PRESENT', 'NONE'))
        self.assertEqual(run(b'PRIVATE_UNPARSEABLE', ipv6),
                         ('UNVERIFIED', 'TCP4_PARSE_UNVERIFIED'))
        self.assertEqual(run(ipv4 * 8000, ipv6),
                         ('UNVERIFIED', 'TCP4_PARSE_UNVERIFIED'))
        self.assertEqual(run(ipv4, b'PRIVATE_UNPARSEABLE'),
                         ('UNVERIFIED', 'TCP6_PARSE_UNVERIFIED'))

    def test_port_reason_fails_closed_at_exact_bounded_stage(self):
        same = SimpleNamespace(st_dev=12, st_ino=34)
        other = SimpleNamespace(st_dev=12, st_ino=35)
        cases = (
            ('NETNS_MISMATCH', 2, 0, 0),
            ('TCP4_READ_UNVERIFIED', 2, 1, 0),
            ('TCP4_PARSE_UNVERIFIED', 2, 1, 1),
            ('TCP6_READ_UNVERIFIED', 2, 2, 1),
            ('TCP6_PARSE_UNVERIFIED', 2, 2, 2),
        )
        for reason, stat_calls, read_calls, parse_calls in cases:
            with self.subTest(reason=reason):
                def stat_func(path):
                    return other if reason == 'NETNS_MISMATCH' and path.endswith(
                        '/self/ns/net') else same

                def read_func(path):
                    if (reason == 'TCP4_READ_UNVERIFIED' and path.endswith('/tcp') or
                            reason == 'TCP6_READ_UNVERIFIED' and path.endswith('/tcp6')):
                        raise OSError('PRIVATE_HOST_DATA')
                    return b'bounded kernel table'

                def parse_func(_raw, length):
                    if (reason == 'TCP4_PARSE_UNVERIFIED' and length == 8 or
                            reason == 'TCP6_PARSE_UNVERIFIED' and length == 32):
                        raise ValueError('PRIVATE_HOST_DATA')
                    # Even when IPv4 has a listener, IPv6 must be inspected.
                    return True

                with (mock.patch.object(probe.os, 'stat', side_effect=stat_func) as stat_mock,
                      mock.patch.object(probe, '_read_proc_net',
                                        side_effect=read_func) as read_mock,
                      mock.patch.object(probe, '_parse_proc_net_port',
                                        side_effect=parse_func) as parse_mock):
                    self.assertEqual(probe._port_8000_listener(),
                                     ('UNVERIFIED', reason))
                    self.assertEqual(stat_mock.call_count, stat_calls)
                    self.assertEqual(read_mock.call_count, read_calls)
                    self.assertEqual(parse_mock.call_count, parse_calls)

    def test_netns_stat_stage_and_errno_classes_remain_private_and_bounded(self):
        """A physical stat failure must identify its attempt, never a raw exception."""
        cases = (
            (errno.EACCES, 'ACCESS_DENIED'),
            (errno.EPERM, 'ACCESS_DENIED'),
            (errno.ENOENT, 'NOT_FOUND'),
            (errno.ENOTDIR, 'NOT_FOUND'),
            (errno.EIO, 'OTHER_OSERROR'),
            (None, 'OTHER_OSERROR'),
            ('unicode', 'OTHER_EXCEPTION'),
            ('value', 'OTHER_EXCEPTION'),
        )
        for stage, attempted in (('PID1', 1), ('SELF', 2)):
            for code, category in cases:
                with self.subTest(stage=stage, code=code):
                    if code == 'unicode':
                        failure = UnicodeError('PRIVATE_HOST_SECRET')
                    elif code == 'value':
                        failure = ValueError('PRIVATE_HOST_SECRET')
                    elif code is None:
                        failure = OSError('PRIVATE_HOST_SECRET')
                    else:
                        failure = OSError(code, 'PRIVATE_HOST_SECRET')

                    def fake_stat(path):
                        if (stage == 'PID1' and path == '/proc/1/ns/net' or
                                stage == 'SELF' and path == '/proc/self/ns/net'):
                            raise failure
                        return SimpleNamespace(st_dev=1, st_ino=2)

                    with (mock.patch.object(probe.os, 'stat', side_effect=fake_stat) as stat_mock,
                          mock.patch.object(probe, '_read_proc_net') as read_mock,
                          mock.patch.object(probe, '_parse_proc_net_port') as parse_mock):
                        observed = probe._port_8000_listener()
                        self.assertEqual(observed,
                                         ('UNVERIFIED', f'NETNS_{stage}_{category}'))
                        self.assertNotIn('PRIVATE_HOST_SECRET', str(observed))
                        self.assertEqual(stat_mock.call_count, attempted)
                        read_mock.assert_not_called()
                        parse_mock.assert_not_called()

    def test_tcp_ssthresh_signed_minus_one_only_in_documented_slot(self):
        # docs.kernel.org/networking/proc_net_tcp.html: ssthresh is field 17;
        # documented -1 means threshold >= 0xFFFF, and is not malformed.
        header = (b'  sl local_address rem_address st tx_queue rx_queue tr '
                  b'tm->when retrnsmt uid timeout inode\n')
        official_row = (b'46: 010310AC:9C4C 030310AC:1770 01 '
                        b'00000150:00000000 01:00000019 00000000 '
                        b'1000 0 54165785 4 cd1e6040 25 4 27 3 -1\n')
        self.assertFalse(probe._parse_proc_net_port(header + official_row, 8))
        listening_row = (b'0: 00000000:1F40 00000000:0000 0A '
                         b'00000000:00000000 00:00000000 00000000 '
                         b'1000 0 1234 1 0000000000000000 100 0 0 10 -1')
        self.assertTrue(probe._parse_proc_net_port(header + listening_row + b'\n', 8))
        fields = listening_row.split()
        self.assertEqual(len(fields), 17)
        for index, value in ((15, b'-1'), (16, b'-2'),
                             (16, b'--1'), (17, b'-1')):
            with self.subTest(index=index,value=value):
                damaged = list(fields)
                if index == 17:
                    damaged.append(value)
                else:
                    damaged[index] = value
                with self.assertRaises(ValueError):
                    probe._parse_proc_net_port(header + b' '.join(damaged) + b'\n', 8)
        # TIME_WAIT's shortened 12-column record has no ssthresh field.
        timewait = (b'0: 00000000:1F40 00000000:0000 06 '
                    b'00000000:00000000 03:00000000 00000000 '
                    b'1000 0 1234 1 -1\n')
        with self.assertRaises(ValueError):
            probe._parse_proc_net_port(header + timewait, 8)


class DedicatedPreflightWorkflowTests(unittest.TestCase):
    def test_actual_runner_shell_rejects_foreign_user_and_classifies_ssh_error(self):
        source = WORKFLOW.read_text()
        name = 'Read dedicated account prerequisites without privileged commands'
        step = source.split('\n      - name: '+name+'\n', 1)[1].split(
            '\n      - name: ', 1)[0]
        body = textwrap.dedent(step.split('        run: |\n', 1)[1])
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, RUNNER_TEMP=tmp, DO_HOST='127.0.0.1',
                       DO_USER='foreign', SSH_KEY='FAKE_TEST_KEY',
                       KNOWN_HOSTS='FAKE_TEST_HOSTKEY')
            command = 'ssh() { echo SHOULD_NOT_SSH; return 255; }\n' + body
            rejected = subprocess.run(['bash'], input=command,cwd=ROOT,env=env,
                                      text=True,capture_output=True,timeout=5)
            self.assertEqual(rejected.returncode,79)
            self.assertIn('CREDENTIAL_PREREQUISITES_UNVERIFIED',rejected.stdout)
            self.assertNotIn('SHOULD_NOT_SSH',rejected.stdout+rejected.stderr)
            env['DO_USER']='porotaadmin'
            transport = subprocess.run(['bash'],input=command,cwd=ROOT,env=env,
                                       text=True,capture_output=True,timeout=5)
            self.assertEqual(transport.returncode,79)
            self.assertIn('SSH_OR_REMOTE_UNVERIFIED',transport.stdout)
            for private in ('FAKE_TEST_KEY','FAKE_TEST_HOSTKEY','foreign'):
                self.assertNotIn(private,transport.stdout+transport.stderr)

    def test_exact_exclusive_mode_matrix_all_32_boolean_combinations(self):
        import itertools
        source = WORKFLOW.read_text()
        self.assertIn('dedicated_account_preflight_only:', source)
        self.assertIn('!inputs.dedicated_account_preflight_only',
                      source.split('\n  host_readonly:\n', 1)[1].split(
                          '\n    concurrency:', 1)[0])
        new = source.split('\n  current_login_readonly:\n', 1)[1]
        self.assertIn('inputs.dedicated_account_preflight_only',
                      new.split('\n    runs-on:', 1)[0])
        trust = new.split('      - name: Validate current-login trusted context\n', 1)[1]
        body = textwrap.dedent(trust.split('        run: |\n', 1)[1].split(
            '\n      - name: ', 1)[0])
        base = dict(os.environ, GITHUB_REPOSITORY='mbalbo2023/Porota-trading',
                    GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main',
                    GITHUB_REF_PROTECTED='true', GITHUB_WORKFLOW_SHA='a'*40,
                    GITHUB_SHA='a'*40, RUNNER_ENVIRONMENT='github-hosted',
                    GITHUB_WORKFLOW_REF=('mbalbo2023/Porota-trading/.github/workflows/'
                                         'rc6-r1-docker-capacity-readonly.yml@refs/heads/main'))
        names = ('RC6_DEDICATED_ONLY','RC6_LINGER_ONLY','RC6_CURRENT_LOGIN_ONLY',
                 'RC6_ROOTLESS_ONLY','RC6_TIMER_MODE')
        for combination in itertools.product((False, True), repeat=5):
            with self.subTest(flags=combination):
                env = dict(base, **{key:'true' if enabled else 'false'
                                    for key,enabled in zip(names,combination)},
                           RC6_DEPLOY_SHA='')
                result = subprocess.run(['bash','-e','-o','pipefail'], input=body,
                                        text=True, capture_output=True, env=env, timeout=3)
                # New dedicated mode forbids *any* other mode. Existing
                # linger-only guard remains unchanged when dedicated is off.
                expected = (79 if (combination[0] and any(combination[1:])) or
                            (combination[1] and any(combination[2:])) else 0)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertNotIn('porotaadmin', result.stderr + result.stdout)
        # Even one unrelated pinned deployed SHA conflicts with this mode.
        env = dict(base, **{key:('true' if index == 0 else 'false')
                            for index,key in enumerate(names)},
                   RC6_DEPLOY_SHA='a'*40)
        result = subprocess.run(['bash','-e','-o','pipefail'], input=body,
                                text=True,capture_output=True,env=env,timeout=3)
        self.assertEqual(result.returncode, 79)
        self.assertIn('INPUT_MODE_CONFLICT',result.stdout)

    def test_dedicated_step_is_read_only_and_has_strict_real_receiver(self):
        source = WORKFLOW.read_text()
        name = 'Read dedicated account prerequisites without privileged commands'
        step = source.split('\n      - name: '+name+'\n', 1)[1].split(
            '\n      - name: ', 1)[0]
        for expected in ('--dedicated-preflight', 'StrictHostKeyChecking=yes',
                         'scripts/rc6_rootless_metadata_readonly.py',
                         'CERT2=NO_VERIFICADO',
                         'SSH_OR_REMOTE_UNVERIFIED',
                         'CREDENTIAL_PREREQUISITES_UNVERIFIED',
                         '"$DO_USER" != "porotaadmin"'):
            self.assertIn(expected, step)
        self.assertIn('path.stat().st_size',step)
        for forbidden in ('sudo -n','docker info','docker ps','/usr/bin/systemctl',
                          'systemctl show','socket.connect'):
            self.assertNotIn(forbidden, step)
        self.assertIn('if: ${{ inputs.dedicated_account_preflight_only }}', step)
        receiver = textwrap.dedent(step.split("          python3 - <<'PY'\n", 1)[1]
                                   .split('\n          PY', 1)[0])
        safe = {
            'schema':'rc6.r1.dedicated-account-preflight-readonly.v1',
            'state':'NO_VERIFICADO','read_only':True,'mutations':0,
            'docker_queries':0,'direct_socket_connections':0,
            'nss_socket_activity':'UNVERIFIED','systemctl_queries':0,
            'sudo_executed':False,'rootless_certified':False,
            'dedicated_account':'ABSENT','dedicated_uid':'NOT_ASSIGNED',
            'dedicated_home':'NOT_ASSIGNED','dedicated_subids':'UNVERIFIED',
            'dedicated_socket':'NOT_ASSIGNED',
            'rootless_toolchain':'AVAILABLE','rootless_launcher':'AVAILABLE',
            'user_namespace':'AVAILABLE','port_8000_listen':'NONE_OBSERVED',
            'port_8000_reason':'NONE',
        }
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/'rc6_current_login_ro'
            dest.mkdir(mode=0o700)
            receipt=dest/'dedicated.json'
            env=dict(os.environ,RUNNER_TEMP=tmp,
                     GITHUB_STEP_SUMMARY=str(Path(tmp)/'summary'))
            def invoke(raw):
                receipt.write_text(raw,encoding='utf-8')
                return subprocess.run([sys.executable,'-c',receiver],
                                      capture_output=True,text=True,env=env,timeout=3)
            good=invoke(json.dumps(safe))
            self.assertEqual(good.returncode, 0)
            self.assertIn('ACCOUNT=ABSENT',good.stdout+good.stderr)
            self.assertIn('PORT8000=NONE_OBSERVED',good.stdout+good.stderr)
            self.assertIn('PORT8000_REASON=NONE',good.stdout+good.stderr)
            self.assertIn('BOOTSTRAP=HOLD',good.stdout+good.stderr)
            self.assertIn('CERT2=NO_VERIFICADO',good.stdout+good.stderr)
            occupied = dict(safe, port_8000_listen='LISTEN_PRESENT')
            observed = invoke(json.dumps(occupied))
            self.assertEqual(observed.returncode, 0)
            self.assertIn('PORT8000=LISTEN_PRESENT', observed.stdout)
            self.assertIn('BOOTSTRAP=HOLD', observed.stdout)
            for reason in (
                    *(f'NETNS_{stage}_{category}' for stage in ('PID1', 'SELF')
                      for category in ('ACCESS_DENIED', 'NOT_FOUND',
                                       'OTHER_OSERROR', 'OTHER_EXCEPTION')),
                    'NETNS_MISMATCH', 'TCP4_READ_UNVERIFIED',
                    'TCP4_PARSE_UNVERIFIED', 'TCP6_READ_UNVERIFIED',
                    'TCP6_PARSE_UNVERIFIED'):
                with self.subTest(reason=reason):
                    incomplete = dict(safe,port_8000_listen='UNVERIFIED',
                                      port_8000_reason=reason)
                    result = invoke(json.dumps(incomplete))
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertIn('PORT8000=UNVERIFIED|PORT8000_REASON='+reason,
                                  result.stdout)
                    self.assertIn('BOOTSTRAP=HOLD|CERT2=NO_VERIFICADO',
                                  result.stdout)
            present = dict(occupied, dedicated_account='PRESENT',
                           dedicated_uid='DISTINCT', dedicated_home='PRIVATE',
                           dedicated_subids='AVAILABLE',
                           dedicated_socket='NOT_INSPECTED')
            observed = invoke(json.dumps(present))
            self.assertEqual(observed.returncode, 0)
            self.assertIn('ACCOUNT=PRESENT', observed.stdout)
            self.assertIn('PORT8000=LISTEN_PRESENT', observed.stdout)
            self.assertIn('BOOTSTRAP=HOLD', observed.stdout)
            forged=(dict(safe,dedicated_account='PRIVATE_USER'),
                    dict(safe,dedicated_uid='DISTINCT'),
                    dict(safe,dedicated_subids='AVAILABLE'),
                    dict(safe,dedicated_subids='NOT_ASSIGNED'),
                    dict(safe,port_8000_listen='PRIVATE_PORT'),
                    dict(safe,port_8000_reason='PRIVATE_PATH_OR_USERNAME'),
                    dict(safe,port_8000_listen='UNVERIFIED',
                         port_8000_reason='NETNS_READ_UNVERIFIED'),
                    dict(safe,port_8000_reason=['NONE']),
                    dict(safe,port_8000_reason={'host':'PRIVATE'}),
                    dict(safe,port_8000_reason=None),
                    dict(safe,port_8000_listen='UNVERIFIED',port_8000_reason='NONE'),
                    dict(safe,port_8000_listen='LISTEN_PRESENT',
                         port_8000_reason='TCP6_PARSE_UNVERIFIED'),
                    dict(safe,port_8000_listen='NONE_OBSERVED',
                         port_8000_reason='NETNS_MISMATCH'),
                    dict(safe,mutations=True),
                    dict(safe,dedicated_account=['ABSENT']),
                    dict(safe,dedicated_account={'host':'private'}),
                    dict(safe,dedicated_socket='NOT_INSPECTED'),
                    dict(safe,dedicated_home='PRIVATE'),
                    dict(safe,dedicated_home='CANDIDATE_EXISTS',dedicated_account='PRESENT'),
                    dict(safe,rootless_certified=True))
            missing_reason=dict(safe)
            del missing_reason['port_8000_reason']
            forged += (missing_reason,)
            for data in forged:
                with self.subTest(data=data):
                    result=invoke(json.dumps(data))
                    self.assertNotEqual(result.returncode,0)
                    self.assertIn('RECEIPT_INVALID',result.stdout+result.stderr)
                    self.assertNotIn('PRIVATE_',result.stdout+result.stderr)
                    self.assertNotIn('Traceback',result.stdout+result.stderr)
            for raw in (json.dumps(safe).replace('"mutations": 0',
                      '"mutations": 0, "mutations": 0'),
                        json.dumps(safe).replace('"mutations": 0',
                      '"mutations": NaN'),
                        json.dumps(safe).replace('"mutations": 0',
                      '"mutations": Infinity'),
                        json.dumps([safe]),json.dumps({'host':'PRIVATE_SECRET'}),
                        json.dumps(safe)[:-17]):
                with self.subTest(raw=raw[:40]):
                    result=invoke(raw)
                    self.assertNotEqual(result.returncode,0)
                    self.assertIn('RECEIPT_INVALID',result.stdout+result.stderr)
            bounded=invoke('X'*8192)
            self.assertNotEqual(bounded.returncode,0)
            self.assertIn('RECEIPT_BOUND',bounded.stderr+bounded.stdout)
            self.assertNotIn('X'*80,bounded.stderr+bounded.stdout)


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
            'user_linger_marker_status': 'TRUSTED',
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

            def missing_variant(*fields):
                variant = dict(positive, ready_for_private_rootless_review=False)
                for field in fields:
                    variant[field] = False
                if 'user_linger_record_present' in fields:
                    variant['user_linger_marker_status'] = 'MARKER_MISSING'
                return variant

            success = run(positive)
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertIn('PREREQUISITES_READY_NOT_CERTIFIED|CERT2=NO_VERIFICADO',
                          success.stdout)
            for missing in ('rootless_launcher_present', 'user_linger_record_present'):
                with self.subTest(missing=missing):
                    incomplete = missing_variant(missing)
                    result = run(incomplete)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('PREREQUISITES_INCOMPLETE|MISSING_CATEGORIES=',
                                  result.stderr + result.stdout)
                    self.assertIn('|CERT2=NO_VERIFICADO', result.stderr + result.stdout)
                    absent = dict(positive)
                    del absent[missing]
                    result = run(absent)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('RECEIPT_INVALID|CERT2=NO_VERIFICADO',
                                  result.stderr + result.stdout)

            categories = (
                ('principal_nonroot_unique', 'LOGIN_IDENTITY'),
                ('runtime_directory_private', 'RUNTIME_DIRECTORY'),
                ('user_manager_socket_present', 'USER_MANAGER_BUS'),
                ('rootless_tools_present', 'ROOTLESS_TOOLCHAIN'),
                ('rootless_launcher_present', 'ROOTLESS_LAUNCHER'),
                ('user_linger_record_present', 'SYSTEMD_LINGER'),
                ('subuids_assigned', 'SUBID_MAPPINGS'),
                ('userns_enabled', 'USER_NAMESPACE'),
                ('default_socket_absent', 'DEFAULT_SOCKET_CONFLICT'),
                ('project_socket_namespace_absent', 'PROJECT_NAMESPACE_CONFLICT'),
            )
            for missing, category in categories:
                with self.subTest(category=category):
                    incomplete = missing_variant(missing)
                    result = run(incomplete)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('PREREQUISITES_INCOMPLETE|MISSING_CATEGORIES=' + category,
                                  result.stderr + result.stdout)
                    self.assertIn('|CERT2=NO_VERIFICADO', result.stderr + result.stdout)
                    self.assertNotIn('private-operator-name', result.stdout + result.stderr)
                    self.assertNotIn('/run/user/', result.stdout + result.stderr)

            multiple = missing_variant('rootless_launcher_present',
                                       'user_linger_record_present')
            result = run(multiple)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('PREREQUISITES_INCOMPLETE|MISSING_CATEGORIES='
                          'ROOTLESS_LAUNCHER,SYSTEMD_LINGER|LINGER_MARKER_STATUS=MARKER_MISSING'
                          '|CERT2=NO_VERIFICADO',
                          result.stdout + result.stderr)
            self.assertLess(len(result.stdout + result.stderr), 300)

            for status in ('MARKER_MISSING', 'MARKER_UNTRUSTED', 'MARKER_UNVERIFIED',
                           'PARENT_MISSING', 'PARENT_UNTRUSTED', 'PARENT_UNVERIFIED'):
                with self.subTest(marker_status=status):
                    incomplete = missing_variant('user_linger_record_present')
                    incomplete['user_linger_marker_status'] = status
                    result = run(incomplete)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('|LINGER_MARKER_STATUS=' + status + '|CERT2=NO_VERIFICADO',
                                  result.stdout + result.stderr)
                    self.assertNotIn('/var/lib/systemd', result.stdout + result.stderr)
                    self.assertNotIn('private-operator-name', result.stdout + result.stderr)

            for malformed_status, present in (('TRUSTED', False),
                                              ('MARKER_MISSING', True),
                                              ('SECRET_PATH_OR_ACCOUNT', False)):
                with self.subTest(malformed=malformed_status):
                    inconsistent = dict(positive, user_linger_record_present=present,
                                        user_linger_marker_status=malformed_status,
                                        ready_for_private_rootless_review=False)
                    result = run(inconsistent)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('RECEIPT_INVALID|CERT2=NO_VERIFICADO',
                                  result.stdout + result.stderr)
                    self.assertNotIn('SECRET_PATH_OR_ACCOUNT', result.stdout + result.stderr)

            absent_marker_status = dict(positive)
            del absent_marker_status['user_linger_marker_status']
            result = run(absent_marker_status)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('RECEIPT_INVALID|CERT2=NO_VERIFICADO',
                          result.stdout + result.stderr)

            malformed = dict(positive, rootless_launcher_present='SECRET_USER_OR_PATH',
                             ready_for_private_rootless_review=False)
            result = run(malformed)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('RECEIPT_INVALID|CERT2=NO_VERIFICADO',
                          result.stdout + result.stderr)
            self.assertNotIn('SECRET_USER_OR_PATH', result.stdout + result.stderr)

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
