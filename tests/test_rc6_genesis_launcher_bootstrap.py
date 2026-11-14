"""Exercise the launcher's real filesystem operations without SSH or root."""

from contextlib import nullcontext, redirect_stdout
import hashlib
import io
import os
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_r295_host_bootstrap_guard as legacy
from scripts import rc6_genesis_launcher_bootstrap as launcher

ROOT = Path(__file__).resolve().parents[1]
TARGET = "/usr/local/sbin/porota-rc6-host-installer-genesis"
RAW = (ROOT / "scripts/rc6_root_install_new_files_only.py").read_bytes()
HASH = "76eac14930a913ff7ed5c0720ccb5ff5bfc050b7bebe5b4725a76685ee67aaff"


class LocalHost:
    """Map fixed remote paths to a private tree, run GNU tools there as the tester."""

    def __init__(self, directory):
        self.root = Path(directory)
        self.path("/usr/local/sbin").mkdir(parents=True)
        self.calls = []
        self.fail_after = None
        self.uid = b"1000\n"
        self.real_group = None
        self.before = lambda command: None

    def path(self, value):
        return self.root / value.lstrip("/")

    def call(self, command, stdin=None):
        self.calls.append(command)
        self.before(command)
        if command == legacy.HOST_ID_COMMAND:
            return 0, b"1234567\n"
        if command == legacy.HOST_REGION_COMMAND:
            return 0, b"nyc1\n"
        if command == "/usr/bin/id -u":
            return 0, self.uid
        if command == "/usr/bin/id -un":
            return 0, b"porotaadmin\n"
        argv = shlex.split(command)
        assert argv[:2] == ["/usr/bin/sudo", "-n"], argv
        exe, *args = argv[2:]
        assert exe in {"/usr/bin/" + tool for tool in
                       ("stat", "test", "dd", "sha256sum", "chmod", "mkdir",
                        "ln", "rm", "rmdir", "sync")}, exe
        argv = [exe]
        for arg in args:
            if arg.startswith("of=/"):
                arg = "of=" + str(self.path(arg[3:]))
            elif arg.startswith("/"):
                arg = str(self.path(arg))
            argv.append(arg)
        result = subprocess.run(argv, input=stdin, capture_output=True, check=False,
                                env={**os.environ, "LC_ALL": "C"}, timeout=5)
        output = result.stdout.replace(str(self.root).encode(), b"")
        if exe.endswith("/stat") and not result.returncode:
            # Scratch is owned by the unprivileged test user. Only UID/GID are
            # virtual; inode, device, file type, mode and link count are real.
            fields = output.rstrip(b"\n").split(b"|")
            fields[1:3] = [b"0", self.real_group or b"0"]
            output = b"|".join(fields) + b"\n"
        if self.fail_after and self.fail_after in command and not result.returncode:
            self.fail_after = None
            raise legacy.BootstrapHold("SIMULATED_LOST_REPLY")
        return result.returncode, output


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.app = launcher
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.host = LocalHost(tmp.name)
        self.context = legacy.BootstrapContext("a" * 40, 222, "1234567",
                                               "nyc1", "example.invalid", "porotaadmin")
        self.target = self.host.path(TARGET)

    def install(self, *, context=None, check=lambda: None, raw=RAW):
        return self.app.install_launcher(self.host, context or self.context, raw, check)

    def test_create_exact_file_then_observe_without_writing(self):
        result = self.install()
        self.assertEqual(result, "PUBLISHED")
        self.assertEqual(hashlib.sha256(self.target.read_bytes()).hexdigest(), HASH)
        first = self.target.stat()
        self.assertEqual((stat.S_IMODE(first.st_mode), first.st_nlink), (0o555, 1))
        self.assertEqual([self.target], list(self.target.parent.iterdir()))
        self.host.calls.clear()
        self.assertEqual(self.install(), "ALREADY_PRESENT_VERIFIED")
        self.assertEqual(first.st_ino, self.target.stat().st_ino)
        self.assertFalse(any(c in self.app.LAUNCHER.mutations for c in self.host.calls))

    def test_other_file_or_symlink_survives_a_collision(self):
        self.target.write_bytes(b"some other executable")
        self.target.chmod(0o555)
        before = self.target.stat()
        with self.assertRaises(legacy.BootstrapHold):
            self.install()
        self.assertEqual(self.target.read_bytes(), b"some other executable")
        self.assertEqual(self.target.stat().st_ino, before.st_ino)
        self.target.unlink()
        self.target.symlink_to("missing")
        with self.assertRaises(legacy.BootstrapHold):
            self.install()
        self.assertTrue(self.target.is_symlink())

    def test_root_group_parent_uid_and_reviewed_bytes_gate_every_write(self):
        for failure in ("group", "mode", "uid", "digest"):
            with self.subTest(failure=failure):
                self.host.real_group = b"3" if failure == "group" else None
                self.host.uid = b"1\n" if failure == "uid" else b"1000\n"
                self.target.parent.chmod(0o777 if failure == "mode" else 0o755)
                with self.assertRaises(legacy.BootstrapHold):
                    self.install(raw=RAW+b"x" if failure == "digest" else RAW)
                self.assertFalse(any(c in self.app.LAUNCHER.mutations for c in self.host.calls))
                self.assertEqual([], list(self.target.parent.iterdir()))
                self.host.calls.clear()

    def test_lost_reply_after_link_recovers_only_same_inode_and_authority(self):
        self.host.fail_after = "/usr/bin/ln -T"
        with self.assertRaises(legacy.BootstrapHold):
            self.install()
        before = self.target.stat()
        self.assertEqual(before.st_nlink, 2)
        with self.assertRaises(legacy.BootstrapHold):
            self.install(context=legacy.replace(self.context, owner_comment_id=223))
        self.assertEqual(before.st_nlink, self.target.stat().st_nlink)
        self.assertEqual(self.install(), "ALREADY_PRESENT_VERIFIED")
        after = self.target.stat()
        self.assertEqual((before.st_ino, 1), (after.st_ino, after.st_nlink))

    def test_partial_with_extra_alias_is_held_without_unlinking_it(self):
        self.host.fail_after = "/usr/bin/ln -T"
        with self.assertRaises(legacy.BootstrapHold):
            self.install()
        alias = self.host.root / "unrelated"
        os.link(self.target, alias)
        with self.assertRaises(legacy.BootstrapHold):
            self.install()
        self.assertEqual(self.target.stat().st_nlink, 3)
        self.assertEqual(self.target.stat().st_ino, alias.stat().st_ino)

    def test_lost_marker_cleanup_reply_reports_target_and_retains_unattributed_residue(self):
        self.host.fail_after = "/usr/bin/rm -- " + self.app.LAUNCHER.marker
        with self.assertRaises(legacy.BootstrapHold):
            self.install()
        before = self.target.stat()
        reserved = self.host.path(self.app.LAUNCHER.private)
        extra = reserved / "foreign"
        extra.write_bytes(b"do not remove")
        self.assertEqual(self.install(), "ALREADY_PRESENT_RESERVATION_RETAINED")
        self.assertEqual(extra.read_bytes(), b"do not remove")
        self.assertTrue(reserved.exists())
        self.assertEqual(before.st_ino, self.target.stat().st_ino)

    def test_exact_stage_can_resume_but_partial_bytes_remain_untouched(self):
        self.host.fail_after = "of=" + self.app.LAUNCHER.stage
        with self.assertRaises(legacy.BootstrapHold):
            self.install()
        stage = self.host.path(self.app.LAUNCHER.stage)
        self.assertEqual(stage.read_bytes(), RAW)
        stage.write_bytes(RAW[:17])
        with self.assertRaises(legacy.BootstrapHold):
            self.install()
        self.assertFalse(self.target.exists())
        self.assertEqual(stage.read_bytes(), RAW[:17])
        stage.write_bytes(RAW)
        self.assertEqual(self.install(), "PUBLISHED")

    def test_root_owned_destination_with_an_external_hardlink_is_rejected(self):
        self.target.write_bytes(RAW)
        self.target.chmod(0o555)
        alias = self.host.root / "external"
        os.link(self.target, alias)
        with self.assertRaises(legacy.BootstrapHold):
            self.install()
        self.assertTrue(alias.exists())
        self.assertFalse(any(c in self.app.LAUNCHER.mutations for c in self.host.calls))

    def test_empty_reservation_cannot_create_a_missing_launcher(self):
        self.host.path(self.app.LAUNCHER.private).mkdir(mode=0o700)
        with self.assertRaises(legacy.BootstrapHold):
            self.install()
        self.assertFalse(self.target.exists())
        self.assertFalse(any(c in self.app.LAUNCHER.mutations for c in self.host.calls))

    def test_revocation_during_partial_or_final_check_prevents_next_write_or_receipt(self):
        def revoke():
            if any("/usr/bin/chmod 0555" in c for c in self.host.calls):
                raise legacy.BootstrapHold("LEASE_REVOKED")

        with self.assertRaisesRegex(legacy.BootstrapHold, "LEASE_REVOKED"):
            self.install(check=revoke)

    def test_revocation_during_final_stage_digest_prevents_link(self):
        revoked = False
        def expire(command):
            nonlocal revoked
            if command == self.app._sha_cmd(self.app.LAUNCHER.stage) and self.app.LAUNCHER.chmod in self.host.calls:
                revoked = True
        def authority():
            if revoked:
                raise legacy.BootstrapHold("LEASE_REVOKED")
        self.host.before = expire
        with self.assertRaisesRegex(legacy.BootstrapHold, "LEASE_REVOKED"):
            self.install(check=authority)
        self.assertFalse(self.target.exists())
        self.assertNotIn(self.app.LAUNCHER.link, self.host.calls)

    def test_revocation_during_retained_final_stat_suppresses_receipt(self):
        self.target.write_bytes(RAW)
        self.target.chmod(0o555)
        self.host.path(self.app.LAUNCHER.private).mkdir(mode=0o700)
        count = 0
        def expire(command):
            nonlocal count
            if command == self.app._sha_cmd(TARGET):
                count += 1
        def authority():
            if count >= 2:
                raise legacy.BootstrapHold("LEASE_REVOKED")
        self.host.before = expire
        with self.assertRaisesRegex(legacy.BootstrapHold, "LEASE_REVOKED"):
            self.install(check=authority)
        self.assertFalse(any(c in self.app.LAUNCHER.mutations for c in self.host.calls))

    def test_conflicting_target_created_immediately_before_link_is_preserved(self):
        def collide(command):
            if "/usr/bin/ln -T" in command:
                self.target.write_bytes(b"late collision")
        self.host.before = collide
        with self.assertRaises(legacy.BootstrapHold):
            self.install()
        self.assertEqual(self.target.read_bytes(), b"late collision")

    def test_equal_bytes_on_a_different_inode_at_link_do_not_earn_a_receipt(self):
        stage = self.host.path(self.app.LAUNCHER.stage)
        def exchange(command):
            if command == self.app.LAUNCHER.link:
                replacement = stage.with_name("replacement")
                replacement.write_bytes(RAW)
                replacement.chmod(0o555)
                os.replace(replacement, stage)
        self.host.before = exchange
        with self.assertRaises(legacy.BootstrapHold):
            self.install()
        self.assertEqual(self.target.read_bytes(), RAW)
        self.assertEqual(self.target.stat().st_nlink, 2)

    def test_launchers_are_not_callable_and_legacy_paths_are_disjoint(self):
        self.assertTrue(self.app.LAUNCHER.commands.isdisjoint(
            {"/usr/bin/sudo -n /usr/bin/python3 -I " + TARGET + " install-approved",
             "/usr/bin/sudo -n /usr/bin/rm -- " + TARGET}))
        self.assertTrue(self.app.LAUNCHER.mutations.isdisjoint(legacy.reviewed_remote_root_commands()))
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.app.main(["--execute-approved-bootstrap"], env={}), 79)
        self.assertIn("HOLD79", output.getvalue())

    def test_cli_publishes_from_reviewed_bytes_and_emits_only_the_file_receipt(self):
        env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
               "GITHUB_ACTOR": "mbalbo2023", "GITHUB_SHA": self.context.main_sha,
               "BOOTSTRAP_ACTION": "install_genesis_launcher", "RC6_R295_OWNER_COMMENT_ID": "222",
               "DO_HOST": self.context.host, "DO_USER": "porotaadmin", "DO_SSH_KEY": "test-key",
               "DO_KNOWN_HOSTS": "test-known-hosts", "GITHUB_TOKEN": "test-fake-token",
               "RC6_EXPECTED_DO_ID": self.context.droplet_id, "RC6_EXPECTED_DO_REGION": "nyc1"}
        with patch.object(self.app.authority, "verify_authority"), \
                patch.object(self.app, "LauncherSSHHost", return_value=nullcontext(self.host)), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.app.main(["--install-approved-launcher"], env=env), 0)
        self.assertEqual(self.target.read_bytes(), RAW)
        self.assertEqual(output.getvalue(), "RC6_R362_LAUNCHER=PUBLISHED|sha256=" + HASH +
                         "|host_control_verified=false|deploy_authorized=false\n")

    def test_custodian_cli_uses_its_own_fixed_package_destination_and_receipt(self):
        from types import ModuleType
        import sys
        package = ModuleType("scripts.rc6_genesis_custodian_package")
        raw = b"PK\x03\x04a reviewed test custodian"
        package.build_custodian = lambda root: raw if root == ROOT else b"wrong checkout"
        digest = hashlib.sha256(raw).hexdigest()
        path = "/usr/local/libexec/porota-rc6-custody/rc6-genesis-custodian"
        target = self.host.path(path)
        target.parent.mkdir(parents=True)
        self.target.write_bytes(b"preserve existing launcher")
        env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
               "GITHUB_ACTOR": "mbalbo2023", "GITHUB_SHA": self.context.main_sha,
               "BOOTSTRAP_ACTION": "install_genesis_custodian", "RC6_R295_OWNER_COMMENT_ID": "222",
               "DO_HOST": self.context.host, "DO_USER": "porotaadmin", "DO_SSH_KEY": "test-key",
               "DO_KNOWN_HOSTS": "test-known-hosts", "GITHUB_TOKEN": "test-fake-token",
               "RC6_EXPECTED_DO_ID": self.context.droplet_id, "RC6_EXPECTED_DO_REGION": "nyc1"}
        with (patch.dict(sys.modules, {package.__name__: package}),
              patch.object(self.app.authority, "verify_authority") as authority,
              patch.object(self.app, "CustodianSSHHost", return_value=nullcontext(self.host), create=True) as ssh,
              patch.object(self.app.authority, "CUSTODIAN_SHA256", digest, create=True),
              redirect_stdout(io.StringIO()) as output):
            self.assertEqual(self.app.main(["--install-approved-custodian"], env=env), 0)
            self.assertIn("RC6_R367_CUSTODIAN=PUBLISHED|sha256=" + digest, output.getvalue())
            before = target.stat()
            self.assertEqual(target.read_bytes(), raw)
            self.assertEqual((stat.S_IMODE(before.st_mode), before.st_nlink), (0o555, 1))
            self.assertEqual(self.app.main(["--install-approved-custodian"], env=env), 0)
            self.assertIn("RC6_R367_CUSTODIAN=ALREADY_PRESENT_VERIFIED", output.getvalue())
            self.assertEqual(before.st_ino, target.stat().st_ino)
            self.assertEqual(self.target.read_bytes(), b"preserve existing launcher")
            self.assertFalse(any(TARGET in cmd for cmd in self.host.calls))
            self.assertGreater(authority.call_count, 3)
            already_opened = ssh.call_count
            for verb, action in (("--install-approved-custodian", "install_genesis_launcher"),
                                 ("--install-approved-launcher", "install_genesis_custodian")):
                with patch.dict(env, BOOTSTRAP_ACTION=action):
                    self.assertEqual(self.app.main([verb], env=env), 79)
            package.build_custodian = lambda root: raw + b"tampered"
            self.assertEqual(self.app.main(["--install-approved-custodian"], env=env), 79)
            self.assertEqual(ssh.call_count, already_opened)

    def test_custodian_prepare_binds_built_bytes_and_never_opens_a_connection(self):
        from types import ModuleType
        import sys
        package = ModuleType("scripts.rc6_genesis_custodian_package")
        data = b"a synthetic package"
        package.build_custodian = lambda root: data if root == ROOT else b""
        pin = hashlib.sha256(data).hexdigest()
        env = {"GITHUB_SHA": "a" * 40, "BOOTSTRAP_ACTION": "install_genesis_custodian",
               "RC6_R295_OWNER_COMMENT_ID": "PREPARE", "DO_HOST": "host.example",
               "RC6_EXPECTED_DO_ID": "12345678", "RC6_EXPECTED_DO_REGION": "nyc1",
               "DO_KNOWN_HOSTS": "host.example ssh-ed25519 "
               "AAAAC3NzaC1lZDI1NTE5AAAAIAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g"}
        with (patch.dict(sys.modules, {package.__name__: package}),
              patch.object(self.app.authority, "CUSTODIAN_SHA256", pin),
              patch.object(self.app, "CustodianSSHHost", side_effect=AssertionError) as host,
              patch.object(self.app.authority, "verify_authority", side_effect=AssertionError) as github):
            for change, status in (({}, 0), ({"RC6_R295_OWNER_COMMENT_ID": "123"}, 79),
                                   ({"DO_KNOWN_HOSTS": "MALFORMED_PRIVATE_VALUE"}, 79),
                                   ({"BOOTSTRAP_ACTION": "install_genesis_launcher"}, 79)):
                with self.subTest(change=change), patch.dict(env, change), redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(self.app.main(["--prepare-custodian"], env=env), status)
                    if not status:
                        self.assertEqual(out.getvalue().splitlines(), [
                            "RC6_R367_HOST_IDENTITY_SHA256=3b13435c2e35275b6642a717bce9b7b865512d0d9448949a324623b53e20cde8",
                            "RC6_R367_CUSTODIAN_SHA256=" + pin, "GITHUB_SHA=" + env["GITHUB_SHA"]])
                    else:
                        self.assertNotIn("MALFORMED_PRIVATE_VALUE", out.getvalue())
                        self.assertNotIn("RC6_R367_CUSTODIAN_SHA256=", out.getvalue())
            host.assert_not_called()
            github.assert_not_called()

    def test_actual_custodian_build_is_selected_by_trusted_publication_policy(self):
        from scripts.rc6_genesis_custodian_package import build_custodian
        package = self.app._custodian_bytes()
        self.assertEqual(package, build_custodian(ROOT))
        self.assertEqual(hashlib.sha256(package).hexdigest(), self.app.authority.CUSTODIAN_SHA256)

    def test_two_ssh_vocabularies_do_not_authorize_sibling_writes_or_custodian_execution(self):
        for publication, transport in ((self.app.LAUNCHER, self.app.LauncherSSHHost),
                                        (self.app.CUSTODIAN, self.app.CustodianSSHHost)):
            other = self.app.CUSTODIAN if publication is self.app.LAUNCHER else self.app.LAUNCHER
            host = object.__new__(transport)
            self.assertTrue(all(host._allows(command) for command in publication.mutations))
            for denied in (*other.mutations, self.app.SUDO + "rm -- " + publication.target,
                           self.app.SUDO + "python3 -I " + self.app.CUSTODIAN.target + " prepare-env"):
                with self.subTest(denied=denied):
                    self.assertFalse(host._allows(denied))


if __name__ == "__main__":
    unittest.main()
