"""R309 trusted Actions transport: fake subprocess only, never real SSH."""

from contextlib import contextmanager
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest import TestCase, mock
import os
import sys
import traceback

from scripts.rc6_r309_bounded_mux_host import R309BoundedMuxHost, R309Hold


R305_NAME = "scripts.rc6_r305_root_pin_transition_guard"
READ_CMD = "/usr/bin/sudo -n /usr/bin/test -e /var/lib/porota-rc6-oci-custody"
HOST_ID_CMD = (
    "/usr/bin/curl -fsS --connect-timeout 3 --max-time 5 "
    "http://169.254.169.254/metadata/v1/id"
)
HOST_REGION_CMD = (
    "/usr/bin/curl -fsS --connect-timeout 3 --max-time 5 "
    "http://169.254.169.254/metadata/v1/region"
)


def _env():
    return {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "mbalbo2023/Porota-trading",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_ACTOR": "mbalbo2023",
        "GITHUB_TRIGGERING_ACTOR": "mbalbo2023",
        "GITHUB_SHA": "a" * 40,
        "DO_HOST": "192.0.2.19",
        "DO_USER": "porotaadmin",
        "DO_SSH_KEY": "-----BEGIN OPENSSH PRIVATE KEY-----\nfixture-secret\n-----END OPENSSH PRIVATE KEY-----",
        "DO_KNOWN_HOSTS": "192.0.2.19 ssh-ed25519 AAAAfixture",
    }


def _trusted_r305():
    module = ModuleType(R305_NAME)
    module.reviewed_root_commands = lambda: frozenset((READ_CMD,))
    module.HOST_ID_COMMAND = HOST_ID_CMD
    module.HOST_REGION_COMMAND = HOST_REGION_CMD
    return module


class _FakeMaster:
    def __init__(self):
        self.alive = True
        self.terminate_count = 0
        self.kill_count = 0

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminate_count += 1
        self.alive = False

    def kill(self):
        self.kill_count += 1
        self.alive = False

    def wait(self, timeout=None):
        self.alive = False
        return 0


@contextmanager
def _host_session(*, env=None, master=None, r305_module=None):
    """Only the subprocess boundary is fake; adapter state and secret files are real."""
    source = r305_module if r305_module is not None else _trusted_r305()
    fake_master = master or _FakeMaster()
    with mock.patch.dict(sys.modules, {R305_NAME: source}), \
         mock.patch("scripts.rc6_r309_bounded_mux_host.subprocess.Popen",
                    return_value=fake_master) as popen, \
         mock.patch.object(R309BoundedMuxHost, "_verified_master_socket",
                           return_value=True):
        host = R309BoundedMuxHost(_env() if env is None else env)
        with host:
            yield host, popen, fake_master


class R309BoundedMuxHostTests(TestCase):
    def test_rejects_untrusted_dispatch_before_creating_files_or_ssh(self):
        for key, wrong in (
            ("GITHUB_REF", "refs/heads/product"),
            ("GITHUB_ACTOR", "unknown"),
            ("GITHUB_TRIGGERING_ACTOR", "other-operator"),
            ("GITHUB_EVENT_NAME", "push"),
            ("GITHUB_REPOSITORY", "other/repo"),
            ("GITHUB_SHA", "not-a-sha"),
            ("DO_HOST", "host; uname -a"),
            ("DO_USER", "user name"),
            ("DO_KNOWN_HOSTS", ""),
        ):
            with self.subTest(key=key):
                candidate = _env()
                candidate[key] = wrong
                with mock.patch.dict(sys.modules, {R305_NAME: _trusted_r305()}), \
                     mock.patch("scripts.rc6_r309_bounded_mux_host.subprocess.Popen") as popen:
                    with self.assertRaises(R309Hold):
                        R309BoundedMuxHost(candidate)
                    popen.assert_not_called()

    def test_missing_r305_trusted_source_fails_closed(self):
        with mock.patch.dict(sys.modules, {R305_NAME: None}):
            with self.assertRaises(R309Hold) as caught:
                R309BoundedMuxHost(_env())
        self.assertIn("R305", str(caught.exception))

    def test_one_master_has_strict_key_pins_and_child_never_falls_back(self):
        with _host_session() as (host, popen, _master):
            self.assertEqual(popen.call_count, 1)
            master_args = popen.call_args.args[0]
            self.assertIn("ProxyCommand=none", master_args)
            self.assertIn("-M", master_args)
            self.assertIn("-N", master_args)
            self.assertNotIn("fixture-secret", str(master_args))
            self.assertIn("ProxyCommand=/usr/bin/false", host.ssh_args)
            self.assertIn("StrictHostKeyChecking=yes", host.ssh_args)
            self.assertIn("ControlMaster=auto", host.ssh_args)
            self.assertIn("ConnectionAttempts=1", host.ssh_args)
            self.assertNotIn("ProxyCommand=none", host.ssh_args)
            self.assertEqual(os.stat(host.key_path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(host.known_hosts_path).st_mode & 0o777, 0o600)

    def test_exact_reviewed_command_and_bounded_output_run_once(self):
        with _host_session() as (host, popen, _master):
            with mock.patch("scripts.rc6_r309_bounded_mux_host.run_bounded_ssh_command",
                            return_value=(1, b"")) as runner:
                self.assertEqual(host.call(READ_CMD, max_output_bytes=0), (1, b""))
                self.assertEqual(host.command_count, 1)
            self.assertEqual(popen.call_count, 1)
            args, stdin = runner.call_args.args
            self.assertEqual(stdin, b"")
            self.assertEqual(args[-1], READ_CMD)
            self.assertIn("ProxyCommand=/usr/bin/false", args)
            self.assertEqual(runner.call_args.kwargs["output_cap"], 0)
            self.assertLessEqual(runner.call_args.kwargs["timeout_s"], 15)

    def test_identity_commands_allowed_but_unreviewed_sudo_rejected(self):
        with _host_session() as (host, _, _):
            with mock.patch("scripts.rc6_r309_bounded_mux_host.run_bounded_ssh_command",
                            return_value=(0, b"123456\n")) as runner:
                self.assertEqual(host.call(HOST_ID_CMD, max_output_bytes=32)[0], 0)
                self.assertEqual(host.call(HOST_REGION_CMD, max_output_bytes=32)[0], 0)
                with self.assertRaises(R309Hold):
                    host.call("/usr/bin/sudo -n /usr/bin/systemctl restart docker")
            self.assertEqual(runner.call_count, 2)

    def test_rejects_invalid_stdin_cap_and_timeout_before_remote(self):
        with _host_session() as (host, _, _):
            with mock.patch("scripts.rc6_r309_bounded_mux_host.run_bounded_ssh_command") as runner:
                cases = [
                    dict(stdin=b"x" * 8193),
                    dict(stdin="text"),
                    dict(stdin=b""),
                    dict(max_output_bytes=4096),
                    dict(max_output_bytes=-1),
                    dict(max_output_bytes=31),
                    dict(max_output_bytes=True),
                    dict(timeout_s=16),
                    dict(timeout_s=0),
                    dict(timeout_s=float("nan")),
                ]
                for args in cases:
                    with self.subTest(args=str(args)[:80]):
                        with self.assertRaises(R309Hold):
                            host.call(READ_CMD, **args)
                runner.assert_not_called()

    def test_stdin_8192_passes_exactly_once_without_logging(self):
        with _host_session() as (host, _, _):
            raw = b"A" * 8192
            with mock.patch("scripts.rc6_r309_bounded_mux_host.run_bounded_ssh_command",
                            return_value=(0, b"")) as runner:
                self.assertEqual(host.call(READ_CMD, stdin=raw,
                                           max_output_bytes=0), (0, b""))
                self.assertEqual(runner.call_args.args[1], raw)

    def test_dead_master_and_missing_socket_do_not_retry_or_reconnect(self):
        with _host_session() as (host, popen, master):
            master.alive = False
            with mock.patch("scripts.rc6_r309_bounded_mux_host.run_bounded_ssh_command") as runner:
                with self.assertRaises(R309Hold):
                    host.call(READ_CMD)
                runner.assert_not_called()
            self.assertEqual(popen.call_count, 1)

    def test_total_deadline_rejects_command_before_runner(self):
        with _host_session() as (host, _, _):
            host.transaction_deadline = 0
            with mock.patch("scripts.rc6_r309_bounded_mux_host.run_bounded_ssh_command") as runner:
                with self.assertRaises(R309Hold):
                    host.call(READ_CMD)
                runner.assert_not_called()

    def test_runner_failure_no_retry_and_no_secret_echo(self):
        with _host_session() as (host, popen, _):
            with mock.patch("scripts.rc6_r309_bounded_mux_host.run_bounded_ssh_command",
                            side_effect=OSError("fixture-secret")) as runner:
                with self.assertRaises(R309Hold) as caught:
                    host.call(READ_CMD)
                # A nested GitHub Actions traceback must never reproduce a
                # private key/known-host/SSH stderr from the lower exception.
                self.assertNotIn(
                    "fixture-secret",
                    "".join(traceback.format_exception(caught.exception)),
                )
                # Once SSH failed, no later R305 equality probe may retry a
                # remote command even if it caught an outer PinHold.
                with self.assertRaises(R309Hold):
                    host.call(READ_CMD)
            self.assertEqual(runner.call_count, 1)
            self.assertEqual(popen.call_count, 1)
            self.assertNotIn("fixture-secret", str(caught.exception))

    def test_ssh_status_255_poisoned_without_automatic_retry(self):
        with _host_session() as (host, _, _):
            with mock.patch("scripts.rc6_r309_bounded_mux_host.run_bounded_ssh_command",
                            return_value=(255, b"")) as runner:
                with self.assertRaises(R309Hold):
                    host.call(READ_CMD)
                with self.assertRaises(R309Hold):
                    host.call(HOST_ID_CMD, max_output_bytes=32)
            self.assertEqual(runner.call_count, 1)

    def test_transport_signal_status_poisoned_before_r305_can_retry(self):
        for status in (-15, -9, 256):
            with self.subTest(status=status), _host_session() as (host, _, _):
                with mock.patch("scripts.rc6_r309_bounded_mux_host.run_bounded_ssh_command",
                                return_value=(status, b"")) as runner:
                    with self.assertRaises(R309Hold):
                        host.call(READ_CMD)
                    with self.assertRaises(R309Hold):
                        host.call(HOST_ID_CMD, max_output_bytes=32)
                self.assertEqual(runner.call_count, 1)
                self.assertTrue(host.poisoned)

    def test_actual_r305_pin_digest_cannot_recover_after_transport_failure(self):
        # On the isolated R309 branch R305 isn't merged yet. This integration
        # is active when the trusted R305 module is present in the checkout.
        try:
            r305 = import_module(R305_NAME)
        except ImportError:
            self.skipTest("R305 owner source not integrated in this branch")
        with _host_session(r305_module=r305) as (host, _, _):
            with mock.patch("scripts.rc6_r309_bounded_mux_host.run_bounded_ssh_command",
                            side_effect=OSError("remote-transport-failed")) as runner:
                with self.assertRaises(r305.PinHold):
                    r305._root_pin_digest(
                        host, SimpleNamespace(new_pin="f" * 64))
                self.assertTrue(host.poisoned)
            # _root_pin_equals catches R305 PinHold and makes a second local
            # comparison, but the adapter must not issue a second SSH call.
            self.assertEqual(runner.call_count, 1)

    def test_exit_closes_master_cleans_key_files_and_scrubs_credentials(self):
        with _host_session() as (host, _, master):
            path = Path(host.key_path)
            self.assertTrue(path.is_file())
        self.assertFalse(path.exists())
        self.assertEqual(master.terminate_count, 1)
        self.assertEqual(host.key_bytes, b"")
        self.assertEqual(host.known_bytes, b"")

    def test_call_outside_context_always_holds(self):
        with mock.patch.dict(sys.modules, {R305_NAME: _trusted_r305()}):
            host = R309BoundedMuxHost(_env())
        with self.assertRaises(R309Hold):
            host.call(READ_CMD)


if __name__ == "__main__":
    import unittest
    unittest.main()
