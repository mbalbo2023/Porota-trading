"""Exercise GENESIS publication on real temporary files with the R280 verifier."""

from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import rc6_host_rootowned_installer_contract as r280
from scripts import rc6_root_install_new_files_only as genesis
from scripts import rc6_host_rootowned_installer_cli as cli
from scripts import rc6_r321_root_install_actions as r321
from tests import test_rc6_host_rootowned_installer_contract as fixtures


class GenesisPublicationTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        self.targets = {
            role: self.root / Path(path).relative_to("/")
            for role, (path, _, _) in r280.TARGETS.items()
        }
        for dest in self.targets.values():
            dest.parent.mkdir(parents=True, exist_ok=True)
        self.manifest, self.sources, self.document, self.approval = fixtures.bundle()
        self.stack.enter_context(patch.object(r280, "ROOT_UID", os.getuid()))
        self.stack.enter_context(
            patch.object(r280, "TRUSTED_MANIFEST_SHA256", fixtures.digest(self.manifest))
        )
        self.stack.enter_context(patch.object(r280, "_open_root_directory", side_effect=self.open_parent))

    def open_parent(self, physical_parent):
        allowed = {str(Path(target).parent) for target, _, _ in r280.TARGETS.values()}
        self.assertIn(physical_parent, allowed)
        return os.open(self.root / Path(physical_parent).relative_to("/"),
                       os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)

    def publish(self):
        return genesis._publish_genesis(r280, self.manifest, self.sources, recheck_pin=lambda: None)

    def test_four_durable_regular_files_and_approval_last_then_replay_holds(self):
        real_link, recorded = os.link, []

        def link(src, dst, **kwargs):
            if dst == self.targets["approval"].name:
                for role in r280.ORDER[:-1]:
                    self.assertEqual(self.targets[role].read_bytes(), self.sources[role])
            result = real_link(src, dst, **kwargs)
            recorded.append(dst)
            return result

        with patch.object(genesis.os, "link", side_effect=link):
            receipt = self.publish()
        self.assertEqual(recorded, [self.targets[role].name for role in r280.ORDER])
        self.assertEqual(receipt["manifest_sha256"], fixtures.digest(self.manifest))
        self.assertIs(receipt["host_control_verified"], False)
        self.assertIs(receipt["deploy_authorized"], False)
        before = {}
        for role, path in self.targets.items():
            self.assertEqual(path.read_bytes(), self.sources[role])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), r280.TARGETS[role][1])
            self.assertEqual(path.stat().st_nlink, 1)
            before[role] = path.stat().st_ino
        self.assertEqual({p for p in self.root.rglob("*") if p.is_file()}, set(self.targets.values()))
        with self.assertRaisesRegex(genesis.GenesisHold, "TARGET_EXISTS"):
            self.publish()
        self.assertEqual(before, {role: path.stat().st_ino for role, path in self.targets.items()})

    def test_preexisting_identical_approval_and_dangling_symlink_prevent_any_write(self):
        final = self.targets["approval"]
        for kind in ("regular", "symlink"):
            with self.subTest(kind=kind):
                if kind == "regular":
                    final.write_bytes(self.sources["approval"])
                    final.chmod(0o400)
                else:
                    final.symlink_to(self.root / "does-not-exist")
                first = final.lstat()
                with self.assertRaisesRegex(genesis.GenesisHold, "TARGET_EXISTS"):
                    self.publish()
                self.assertEqual(first.st_ino, final.lstat().st_ino)
                if kind == "regular":
                    self.assertEqual(final.read_bytes(), self.sources["approval"])
                else:
                    self.assertTrue(final.is_symlink())
                self.assertTrue(all(not self.targets[r].exists() for r in r280.ORDER[:-1]))
                final.unlink()

    def test_competing_creator_wins_without_clobber_or_approval_and_partial_cannot_resume(self):
        target = self.targets["custody"]
        real_link, competing = os.link, []

        def race(src, dst, **kwargs):
            if dst == target.name:
                with target.open("xb") as stream:
                    stream.write(b"independent creator")
                competing.append(target.stat().st_ino)
            return real_link(src, dst, **kwargs)

        with patch.object(genesis.os, "link", side_effect=race):
            with self.assertRaisesRegex(genesis.GenesisHold, "TARGET_EXISTS"):
                self.publish()
        self.assertEqual(target.stat().st_ino, competing[0])
        self.assertEqual(target.read_bytes(), b"independent creator")
        helper = self.targets["helper"]
        self.assertEqual(helper.read_bytes(), self.sources["helper"])
        first = helper.stat().st_ino
        with self.assertRaisesRegex(genesis.GenesisHold, "TARGET_EXISTS"):
            self.publish()
        self.assertEqual(helper.stat().st_ino, first)
        self.assertFalse(self.targets["profiles"].exists())
        self.assertFalse(self.targets["approval"].exists())

    def test_prior_inode_bytes_mode_or_link_drift_after_profiles_prevents_approval(self):
        real_link = os.link
        for change in ("inode", "bytes", "mode", "hardlink"):
            with self.subTest(change=change):
                helper = self.targets["helper"]
                alias = helper.with_name("foreign-alias")

                def drift(src, dst, **kwargs):
                    result = real_link(src, dst, **kwargs)
                    if dst == self.targets["profiles"].name:
                        if change == "inode":
                            replacement = helper.with_name("replacement")
                            replacement.write_bytes(self.sources["helper"])
                            replacement.chmod(0o555)
                            os.replace(replacement, helper)
                        elif change == "bytes":
                            helper.chmod(0o755)
                            helper.write_bytes(b"X" * len(self.sources["helper"]))
                            helper.chmod(0o555)
                        elif change == "mode":
                            helper.chmod(0o755)
                        else:
                            real_link(helper, alias)
                    return result

                with patch.object(genesis.os, "link", side_effect=drift):
                    with self.assertRaises((genesis.GenesisHold, r280.InstallHold)):
                        self.publish()
                self.assertFalse(self.targets["approval"].exists())
                self.assertTrue(all(self.targets[r].exists() for r in r280.ORDER[:-1]))
                if change == "hardlink":
                    self.assertTrue(helper.samefile(alias))
                    alias.unlink()
                for role in r280.ORDER[:-1]:
                    self.targets[role].unlink()

    def test_replaced_approval_parent_or_helper_at_last_link_never_gets_success_receipt(self):
        real_link = os.link
        for change in ("approval_parent", "helper"):
            with self.subTest(change=change), TemporaryDirectory() as case:
                self.root = Path(case)
                self.targets = {role: self.root / Path(path).relative_to("/")
                                for role, (path, _, _) in r280.TARGETS.items()}
                for dest in self.targets.values():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                approval = self.targets["approval"]
                moved = approval.parent.with_name("former-parent")
                helper = self.targets["helper"]

                def last_link(src, dst, **kwargs):
                    if dst == approval.name:
                        if change == "approval_parent":
                            approval.parent.rename(moved)
                            approval.parent.mkdir(mode=0o700)
                        else:
                            foreign = helper.with_name("foreign")
                            foreign.write_bytes(b"independent operation")
                            foreign.chmod(0o555)
                            os.replace(foreign, helper)
                    return real_link(src, dst, **kwargs)

                with patch.object(genesis.os, "link", side_effect=last_link):
                    with self.assertRaises(genesis.GenesisHold):
                        self.publish()
                if change == "approval_parent":
                    self.assertFalse(approval.exists())
                    self.assertEqual((moved / approval.name).read_bytes(), self.sources["approval"])
                else:
                    self.assertEqual(helper.read_bytes(), b"independent operation")
                    self.assertEqual(approval.read_bytes(), self.sources["approval"])

    def test_group_drift_on_the_same_inode_prevents_approval(self):
        real_link, real_fstat = os.link, os.fstat
        helper, affected = self.targets["helper"], set()

        def changed_group(fd):
            actual = real_fstat(fd)
            if (actual.st_dev, actual.st_ino) in affected:
                metadata = list(actual)
                metadata[stat.ST_GID] = actual.st_gid + 1
                # os.stat_result constructed from a sequence drops nanosecond fields.
                return os.stat_result(metadata, {"st_ctime_ns": actual.st_ctime_ns})
            return actual

        def mark_drift(src, dst, **kwargs):
            result = real_link(src, dst, **kwargs)
            if dst == self.targets["profiles"].name:
                info = helper.stat()
                affected.add((info.st_dev, info.st_ino))
            return result

        with patch.object(genesis.os, "fstat", side_effect=changed_group), \
                patch.object(genesis.os, "link", side_effect=mark_drift):
            with self.assertRaisesRegex(genesis.GenesisHold, "PUBLISHED_FILE_CHANGED"):
                self.publish()
        self.assertFalse(self.targets["approval"].exists())

    def test_rejects_continuity_expiry_wrong_pin_and_unverified_template(self):
        for bad in ("continuity", "expired", "pin", "template"):
            with self.subTest(bad=bad):
                manifest, sources, doc, approval = fixtures.bundle()
                if bad in ("continuity", "expired"):
                    if bad == "continuity":
                        approval["phase"] = "CONTINUITY"
                    else:
                        approval["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)
                                                  ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    sources["approval"] = fixtures.encode(approval)
                    doc["files"]["approval"] = fixtures.digest(sources["approval"])
                    manifest = fixtures.encode(doc)
                elif bad == "template":
                    sources["unit_template"] += b"changed"
                pin = "0" * 64 if bad == "pin" else fixtures.digest(manifest)
                with patch.object(r280, "TRUSTED_MANIFEST_SHA256", pin):
                    with self.assertRaises((genesis.GenesisHold, r280.InstallHold)):
                        genesis._publish_genesis(r280, manifest, sources, recheck_pin=lambda: None)
                self.assertFalse(any(path.exists() for path in self.targets.values()))

    def test_cli_is_offhost_hold_with_no_trust_input_or_checkout_write(self):
        entry = Path(genesis.__file__)
        result = subprocess.run([sys.executable, "-I", str(entry), "install-approved"],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 79)
        self.assertIn("HOLD79", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertFalse(any(path.exists() for path in self.targets.values()))


class FixedLauncherTest(unittest.TestCase):
    LAUNCHER = "/usr/local/sbin/porota-rc6-host-installer-genesis"

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        self.manifest, self.sources, _, _ = fixtures.bundle()
        self.pin = fixtures.digest(self.manifest)
        self.inbox = self.path(r280.INBOX_DIR)
        self.inbox.mkdir(parents=True, mode=0o700)
        self.files = {
            self.LAUNCHER: (Path(genesis.__file__).read_bytes(), 0o555),
            cli.INSTALLED_CLI_PATH: (Path(cli.__file__).read_bytes(), 0o555),
            cli.INSTALLED_CONTRACT_PATH: (Path(r280.__file__).read_bytes(), 0o400),
            cli.APPROVED_MANIFEST_PIN_FILE: ((self.pin + "\n").encode(), 0o400),
        }
        for name, (raw, mode) in self.files.items():
            self.put(self.path(name), raw, mode)
        self.put(self.inbox / "manifest.json", self.manifest, 0o400)
        for role, name in r280.SOURCE_NAMES.items():
            self.put(self.inbox / name, self.sources[role], 0o400)
        self.targets = {role: self.path(dest) for role, (dest, _, _) in r280.TARGETS.items()}
        # Only kernel ownership/namespace and process identity are simulated.
        # Path traversal, data, hashes, fsync and no-replace links stay real.
        real_open, real_stat, real_fstat = os.open, os.stat, os.fstat

        def open_in_namespace(name, *args, **kwargs):
            return real_open(self.root if name == "/" else name, *args, **kwargs)

        def as_root(inode):
            fields = list(inode)
            fields[stat.ST_UID] = fields[stat.ST_GID] = 0
            return os.stat_result(fields, {"st_ctime_ns": inode.st_ctime_ns,
                                           "st_mtime_ns": inode.st_mtime_ns})

        self.stack.enter_context(patch.object(os, "open", side_effect=open_in_namespace))
        self.stack.enter_context(patch.object(os, "fstat", side_effect=lambda fd: as_root(real_fstat(fd))))
        self.stack.enter_context(patch.object(os, "stat", side_effect=lambda *a, **kw: as_root(real_stat(*a, **kw))))
        self.stack.enter_context(patch.object(os, "geteuid", return_value=0))
        self.stack.enter_context(patch.object(os, "fchown", side_effect=lambda fd, u, g: self.assertEqual((u, g), (0, 0))))
        self.stack.enter_context(patch.object(genesis, "__file__", self.LAUNCHER))
        self.stack.enter_context(patch.object(sys, "argv", [self.LAUNCHER, "install-approved"]))
        self.stack.enter_context(patch.object(sys, "flags", wraps=sys.flags)).isolated = 1

    def path(self, absolute):
        return self.root / Path(absolute).relative_to("/")

    @staticmethod
    def put(path, raw, mode):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(mode)

    def invoke(self):
        output = io.StringIO()
        with redirect_stdout(output):
            status = genesis.main()
        return status, output.getvalue().encode("ascii")

    def assert_holds(self, *, prefix=()):
        status, stdout = self.invoke()
        self.assertEqual(status, 79)
        self.assertRegex(stdout, rb"^RC6_ROOT_GENESIS=HOLD79\|reason=[A-Z0-9_]+\n$")
        self.assertEqual({role for role, p in self.targets.items() if p.exists()}, set(prefix))

    def test_normal_entrypoint_roundtrips_through_real_r321(self):
        status, stdout = self.invoke()
        self.assertEqual(status, 0, stdout)
        receipt = r321.validate_installer_receipt(stdout, expected_manifest_sha256=self.pin,
                                                   candidate_sha=fixtures.SHA)
        self.assertIs(receipt["host_control_verified"], False)
        self.assertEqual(set(receipt), cli._RECEIPT_KEYS)
        for role, path in self.targets.items():
            self.assertEqual(path.read_bytes(), self.sources[role])

    def test_corrupt_either_reviewed_source_before_any_bootstrap_exec(self):
        import builtins
        from types import CodeType
        real_exec = builtins.exec
        for physical in (cli.INSTALLED_CLI_PATH, cli.INSTALLED_CONTRACT_PATH):
            with self.subTest(physical=physical):
                executed = []
                path = self.path(physical)
                original, mode = self.files[physical]
                path.chmod(0o600)
                marker = self.root / "unreviewed-code-ran"
                self.put(path, original + f"\nopen({str(marker)!r}, 'w').close()\n".encode(), mode)

                def watch(code, *args, **kwargs):
                    if isinstance(code, CodeType) and code.co_filename in self.files:
                        executed.append(code.co_filename)
                    return real_exec(code, *args, **kwargs)

                with patch.object(builtins, "exec", side_effect=watch):
                    status, stdout = self.invoke()
                self.assertEqual(status, 79)
                self.assertIn(b"BOOTSTRAP_CODE_PIN", stdout)
                self.assertEqual(executed, [])
                self.assertFalse(marker.exists())
                self.assertFalse(any(p.exists() for p in self.targets.values()))
                path.chmod(0o600)
                self.put(path, original, mode)

    def test_pin_replacement_at_link_stops_next_link_or_positive_receipt(self):
        real_link = os.link
        pin_path = self.path(cli.APPROVED_MANIFEST_PIN_FILE)
        for after in ("helper", "approval"):
            with self.subTest(after=after):
                def revoke(src, dst, **kwargs):
                    result = real_link(src, dst, **kwargs)
                    if dst == self.targets[after].name:
                        replacement = pin_path.with_name("new-pin-inode")
                        self.put(replacement, (self.pin + "\n").encode(), 0o400)
                        os.replace(replacement, pin_path)
                    return result

                with patch.object(os, "link", side_effect=revoke):
                    status, stdout = self.invoke()
                self.assertEqual(status, 79)
                self.assertIn(b"PIN_CHANGED", stdout)
                self.assertFalse(stdout.lstrip().startswith(b"{"))
                expected = {"helper"} if after == "helper" else set(self.targets)
                self.assertEqual({role for role, p in self.targets.items() if p.exists()}, expected)
                for role in expected:
                    self.targets[role].unlink()

    def test_wrong_invocation_returns_hold_before_any_filesystem_access(self):
        for override in (patch.object(sys, "argv", [self.LAUNCHER, "install-approved", "/tmp"]),
                         patch.object(os, "geteuid", return_value=1000),
                         patch.object(sys.flags, "isolated", 0),
                         patch.object(genesis, "__file__", str(cli.INSTALLED_CLI_PATH))):
            with override, patch.object(os, "open", side_effect=AssertionError) as opened:
                self.assert_holds()
                opened.assert_not_called()

    def test_inbox_scope_mode_fifo_symlink_and_unpinned_bytes_hold(self):
        role_path = self.inbox / r280.SOURCE_NAMES["custody"]
        original = role_path.read_bytes()
        real_open = os.open
        for damage in ("wrong_inbox_mode", "extra", "stage_file_mode", "hardlink", "symlink",
                       "fifo", "stage_bytes", "wrong_pin"):
            with self.subTest(damage=damage), ExitStack() as clean:
                alias = self.root / "foreign"
                if damage == "wrong_inbox_mode":
                    self.inbox.chmod(0o750)
                    clean.callback(self.inbox.chmod, 0o700)
                elif damage == "extra":
                    extra = self.inbox / "unclaimed.stage"
                    extra.touch()
                    clean.callback(extra.unlink)
                elif damage == "wrong_pin":
                    pin = self.path(cli.APPROVED_MANIFEST_PIN_FILE)
                    pin.chmod(0o600)
                    self.put(pin, b"0" * 64 + b"\n", 0o400)
                elif damage == "stage_file_mode":
                    role_path.chmod(0o600)
                    clean.callback(role_path.chmod, 0o400)
                elif damage == "hardlink":
                    os.link(role_path, alias)
                    clean.callback(alias.unlink)
                else:
                    role_path.unlink()
                    clean.callback(self.put, role_path, original, 0o400)
                    clean.callback(role_path.unlink)
                    if damage == "fifo":
                        os.mkfifo(role_path, mode=0o400)
                        seen_flags = []

                        def check_flags(name, flags, *args, **kwargs):
                            if name == role_path.name:
                                seen_flags.append(flags)
                                if not flags & os.O_NONBLOCK:
                                    raise OSError("test prevents a blocking FIFO open")
                            return real_open(name, flags, *args, **kwargs)

                        clean.enter_context(patch.object(os, "open", side_effect=check_flags))
                    elif damage == "symlink":
                        self.put(alias, original, 0o400)
                        role_path.symlink_to(alias)
                    else:
                        self.put(role_path, b"unapproved stage bytes", 0o400)
                self.assert_holds()
                if damage == "fifo":
                    self.assertTrue(seen_flags)
                    self.assertTrue(all(flags & os.O_NONBLOCK for flags in seen_flags))

    def test_expiry_during_link_stops_at_next_authority_boundary(self):
        real_link = os.link
        for after in ("helper", "approval"):
            with self.subTest(after=after):
                clock = [datetime.now(timezone.utc)]

                def expired(src, dst, **kwargs):
                    result = real_link(src, dst, **kwargs)
                    if dst == self.targets[after].name:
                        clock[0] += timedelta(hours=5)
                    return result

                with patch.object(os, "link", side_effect=expired), \
                        patch.object(genesis, "datetime", wraps=datetime) as timer:
                    timer.now.side_effect = lambda zone: clock[0]
                    prefix = ("helper",) if after == "helper" else tuple(self.targets)
                    self.assert_holds(prefix=prefix)
                for role in prefix:
                    self.targets[role].unlink()

    def test_inbox_python_payload_is_only_copied_as_bytes(self):
        marker = self.root / "inbox-code-executed"
        self.sources["custody"] += f"\nopen({str(marker)!r}, 'w').close()\n".encode()
        _, _, document, approval = fixtures.bundle()
        approval["custody_sha256"] = fixtures.digest(self.sources["custody"])
        self.sources["approval"] = fixtures.encode(approval)
        for role in ("custody", "approval"):
            document["files"][role] = fixtures.digest(self.sources[role])
            path = self.inbox / r280.SOURCE_NAMES[role]
            path.chmod(0o600)
            self.put(path, self.sources[role], 0o400)
        manifest = fixtures.encode(document)
        for path, raw in ((self.inbox / "manifest.json", manifest),
                          (self.path(cli.APPROVED_MANIFEST_PIN_FILE), (fixtures.digest(manifest) + "\n").encode())):
            path.chmod(0o600)
            self.put(path, raw, 0o400)
        status, stdout = self.invoke()
        self.assertEqual(status, 0, stdout)
        self.assertFalse(marker.exists())
        self.assertEqual(self.targets["custody"].read_bytes(), self.sources["custody"])

    def test_internal_receipt_cannot_add_authority_or_swap_identity(self):
        good = {"schema": "RC6_ROOT_GENESIS_NEW_FILES_ONLY_V1",
                "status": "INSTALLED_FILES_ONLY_NOT_HOST_GO", "candidate_sha": fixtures.SHA,
                "manifest_sha256": self.pin, "host_control_verified": False, "deploy_authorized": False}
        for changed in ({"host_control_verified": 0}, {"deploy_authorized": 0},
                        {"candidate_sha": "b" * 40}, {"extra": False}):
            with self.subTest(changed=changed), patch.object(genesis, "_publish_genesis", return_value=good | changed):
                self.assert_holds()

    def test_final_pin_read_cannot_hide_changed_published_helper(self):
        real_read = genesis._read_protected
        helper = self.targets["helper"]

        def change_during_final_pin(path, *args):
            result = real_read(path, *args)
            if path == genesis.MANIFEST_PIN and self.targets["approval"].exists():
                helper.chmod(0o600)
                helper.write_bytes(b"concurrent writer")
                helper.chmod(0o555)
            return result

        with patch.object(genesis, "_read_protected", side_effect=change_during_final_pin):
            self.assert_holds(prefix=self.targets)
        self.assertEqual(helper.read_bytes(), b"concurrent writer")

    def test_preapproval_pin_read_cannot_seal_a_changed_prefix(self):
        real_read = genesis._read_protected
        helper = self.targets["helper"]

        def change_during_preapproval_pin(path, *args):
            result = real_read(path, *args)
            if path == genesis.MANIFEST_PIN and self.targets["profiles"].exists() and not self.targets["approval"].exists():
                helper.chmod(0o600)
                helper.write_bytes(b"concurrent writer")
                helper.chmod(0o555)
            return result

        with patch.object(genesis, "_read_protected", side_effect=change_during_preapproval_pin):
            self.assert_holds(prefix=r280.ORDER[:-1])
        self.assertEqual(helper.read_bytes(), b"concurrent writer")
