"""Exercise GENESIS publication on real temporary files with the R280 verifier."""

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
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
        return genesis._publish_genesis(r280, self.manifest, self.sources)

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
                        genesis._publish_genesis(r280, manifest, sources)
                self.assertFalse(any(path.exists() for path in self.targets.values()))

    def test_cli_is_offhost_hold_with_no_trust_input_or_checkout_write(self):
        entry = Path(genesis.__file__)
        result = subprocess.run([sys.executable, "-I", str(entry), "install-approved"],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 79)
        self.assertIn("HOLD79", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertFalse(any(path.exists() for path in self.targets.values()))
