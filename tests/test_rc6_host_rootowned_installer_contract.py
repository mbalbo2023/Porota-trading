"""Offline R280 contract tests. NEVER perform host, SSH, sudo or Docker I/O."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_host_rootowned_installer_contract as installer


NOW = datetime(2026, 11, 12, 20, 0, tzinfo=timezone.utc)
SHA = "a" * 40
TREE = "b" * 40
ARTIFACT = "sha256:" + "c" * 64
IMAGE = "sha256:" + "d" * 64
GENERATION = "e" * 32
DAEMON = "rootful-daemon-rc6"


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def bundle():
    files = {
        "helper": b"#!/usr/bin/python3 -I\n# reviewed helper bytes\n",
        "custody": b"# reviewed root-only custody\n",
        "profiles": b'{"roles":{}}\n',
    }
    expires_at = (NOW + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    approval = {
        "schema_version": 1,
        "phase": "GENESIS",
        "candidate_sha": SHA,
        "candidate_tree": TREE,
        "artifact_digest": ARTIFACT,
        "image_id": IMAGE,
        "daemon_id": DAEMON,
        "generation": GENERATION,
        "run_id": "123456",
        "run_attempt": "1",
        "paper_mode": "PRODUCTION_PAPER",
        "real_orders_allowed": False,
        "helper_sha256": digest(files["helper"]),
        "custody_sha256": digest(files["custody"]),
        "profiles_sha256": digest(files["profiles"]),
        "unit_template_sha256": "f" * 64,
        "expires_at": expires_at,
    }
    files["approval"] = encode(approval)
    manifest = {
        "schema": "RC6_ROOT_INSTALL_MANIFEST_V1",
        "candidate_sha": SHA,
        "candidate_tree": TREE,
        "paper_mode": "PRODUCTION_PAPER",
        "real_orders_allowed": False,
        "files": {role: digest(data) for role, data in files.items()},
    }
    return encode(manifest), files, manifest, approval


class RootOwnedInstallerContractTest(unittest.TestCase):
    def test_independent_manifest_pin_and_approved_paper_bundle(self):
        manifest, source, _, _ = bundle()
        with patch.object(installer, "TRUSTED_MANIFEST_SHA256", digest(manifest)):
            verified = installer.verify_approved_bundle(manifest, source, now=NOW)
        self.assertEqual(verified.candidate_sha, SHA)
        self.assertEqual(verified.manifest_sha256, digest(manifest))
        self.assertEqual(set(verified.files), {"helper", "custody", "profiles", "approval"})
        self.assertEqual(verified.files["helper"], source["helper"])
        self.assertIs(verified.host_control_verified, False)

    def test_no_independent_pin_is_hold_even_for_identical_bundle(self):
        manifest, source, _, _ = bundle()
        with patch.object(installer, "TRUSTED_MANIFEST_SHA256", None):
            with self.assertRaises(installer.InstallHold) as caught:
                installer.verify_approved_bundle(manifest, source, now=NOW)
        self.assertEqual(caught.exception.reason, "ROOT_MANIFEST_TRUST_PIN_ABSENT")

    def test_manifest_hash_is_not_self_authorized_by_supplied_content(self):
        manifest, source, _, _ = bundle()
        with patch.object(installer, "TRUSTED_MANIFEST_SHA256", "0" * 64):
            with self.assertRaises(installer.InstallHold) as caught:
                installer.verify_approved_bundle(manifest, source, now=NOW)
        self.assertEqual(caught.exception.reason, "ROOT_MANIFEST_TRUST_PIN_MISMATCH")

    def test_manifest_exact_roles_and_sources_reject_injection_and_paths(self):
        manifest, files, document, _ = bundle()
        for changed in (
            {**document, "files": {**document["files"], "/tmp/payload": "0" * 64}},
            {**document, "files": {"helper": document["files"]["helper"]}},
            {**document, "real_orders_allowed": True},
            {**document, "real_orders_allowed": 0},
            {**document, "paper_mode": "LIVE"},
            {**document, "candidate_tree": "z" * 40},
            {**document, "extra": "root"},
        ):
            raw = encode(changed)
            with self.subTest(changed=changed):
                with patch.object(installer, "TRUSTED_MANIFEST_SHA256", digest(raw)):
                    with self.assertRaises(installer.InstallHold):
                        installer.verify_approved_bundle(raw, files, now=NOW)
        with patch.object(installer, "TRUSTED_MANIFEST_SHA256", digest(manifest)):
            with self.assertRaises(installer.InstallHold):
                installer.verify_approved_bundle(manifest, {**files, "../root": b"pwn"}, now=NOW)

    def test_manifest_rejects_changed_helper_or_approval_bytes(self):
        manifest, files, _, _ = bundle()
        for role in ("helper", "custody", "profiles", "approval"):
            changed = dict(files, **{role: files[role] + b"changed"})
            with self.subTest(role=role), patch.object(
                    installer, "TRUSTED_MANIFEST_SHA256", digest(manifest)):
                with self.assertRaises(installer.InstallHold) as caught:
                    installer.verify_approved_bundle(manifest, changed, now=NOW)
                self.assertEqual(caught.exception.reason, "ROOT_BUNDLE_HASH_MISMATCH")

    def test_approval_rejects_live_expired_stale_and_forged_receipts(self):
        _, files, manifest, approval = bundle()
        cases = (
            {**approval, "real_orders_allowed": True},
            {**approval, "paper_mode": "LIVE"},
            {**approval, "candidate_sha": "f" * 40},
            {**approval, "helper_sha256": "0" * 64},
            {**approval, "expires_at": (NOW - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")},
            {**approval, "expires_at": (NOW + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")},
            {**approval, "extra": "forged"},
        )
        for altered in cases:
            with self.subTest(altered=altered):
                source = dict(files, approval=encode(altered))
                updated = {**manifest, "files": {**manifest["files"], "approval": digest(source["approval"])}}
                raw = encode(updated)
                with patch.object(installer, "TRUSTED_MANIFEST_SHA256", digest(raw)):
                    with self.assertRaises(installer.InstallHold):
                        installer.verify_approved_bundle(raw, source, now=NOW)

    def test_json_duplicate_keys_nan_and_oversized_manifest_hold(self):
        _, files, _, _ = bundle()
        for raw in (b'{"schema":"one","schema":"two"}', b'{"x":NaN}',
                    b"{" + b" " * 20000 + b"}"):
            with self.subTest(raw=raw[:30]), patch.object(
                    installer, "TRUSTED_MANIFEST_SHA256", digest(raw)):
                with self.assertRaises(installer.InstallHold):
                    installer.verify_approved_bundle(raw, files, now=NOW)

    def test_root_only_checkout_invocation_never_installs(self):
        with patch.object(installer, "TRUSTED_MANIFEST_SHA256", None):
            with self.assertRaises(installer.InstallHold):
                installer.install_approved_bundle()
        with patch.object(installer, "TRUSTED_MANIFEST_SHA256", "f" * 64):
            with self.assertRaises(installer.InstallHold) as caught:
                installer.install_approved_bundle()
        self.assertEqual(caught.exception.reason, "ROOT_INSTALL_EUID_REQUIRED")
        with patch.object(installer, "TRUSTED_MANIFEST_SHA256", "f" * 64), \
                patch.object(installer.os, "geteuid", return_value=0):
            with self.assertRaises(installer.InstallHold) as caught:
                installer.install_approved_bundle()
        self.assertEqual(caught.exception.reason, "ROOT_INSTALLED_BOOTSTRAP_UNVERIFIED")
        # This code does not create a sudo shell or alter Docker/systemd.
        self.assertFalse(hasattr(installer, "sudo_shell"))

    def test_atomic_publish_does_not_follow_existing_symlink_or_dotdot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "host-helper-approval.json"
            target = root / "unchanged"
            target.write_bytes(b"unchanged")
            destination.symlink_to(target)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(installer.InstallHold) as caught:
                    installer.atomic_publish(fd, "host-helper-approval.json", b"new", 0o400)
                self.assertEqual(caught.exception.reason, "ROOT_INODE_ALIAS")
                with self.assertRaises(installer.InstallHold):
                    installer.atomic_publish(fd, "../escape", b"new", 0o400)
            finally:
                os.close(fd)
            self.assertEqual(target.read_bytes(), b"unchanged")

    def test_atomic_publish_writes_expected_bytes_permissions_and_no_temporary_leak(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                installer.atomic_publish(fd, "host-helper-approval.json",
                                         b"expected-root-bytes", 0o400)
            finally:
                os.close(fd)
            final = directory / "host-helper-approval.json"
            self.assertEqual(final.read_bytes(), b"expected-root-bytes")
            self.assertEqual(stat.S_IMODE(final.stat().st_mode), 0o400)
            self.assertEqual([entry.name for entry in directory.iterdir()],
                             ["host-helper-approval.json"])

    def test_atomic_publish_enforces_each_fixed_target_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(installer.InstallHold) as caught:
                    installer.atomic_publish(fd, "host-helper-approval.json", b"unsafe", 0o555)
                self.assertEqual(caught.exception.reason, "ROOT_ATOMIC_INSTALL_SHAPE")
            finally:
                os.close(fd)
            self.assertEqual(list(directory.iterdir()), [])

    def test_missing_os_nofollow_capability_fails_closed_without_creating_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with patch.object(installer, "NOFOLLOW_FLAG", None):
                    with self.assertRaises(installer.InstallHold) as caught:
                        installer.atomic_publish(fd, "host-helper-approval.json", b"new", 0o400)
                self.assertEqual(caught.exception.reason, "ROOT_OS_NOFOLLOW_REQUIRED")
            finally:
                os.close(fd)
            self.assertEqual(list(root.iterdir()), [])

    def test_root_inbox_source_rejects_symlink_hardlink_and_group_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "helper.bin"
            source.write_bytes(b"trusted")
            source.chmod(0o400)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with patch.object(installer, "ROOT_UID", os.getuid()):
                    self.assertEqual(installer._read_root_inbox_file(fd, "helper.bin", 100),
                                     b"trusted")
                    alias = root / "hardlink"
                    os.link(source, alias)
                    with self.assertRaises(installer.InstallHold) as caught:
                        installer._read_root_inbox_file(fd, "helper.bin", 100)
                    self.assertEqual(caught.exception.reason, "ROOT_INODE_ALIAS")
                    alias.unlink()
                    source.chmod(0o620)
                    with self.assertRaises(installer.InstallHold) as caught:
                        installer._read_root_inbox_file(fd, "helper.bin", 100)
                    self.assertEqual(caught.exception.reason, "ROOT_INODE_OWNER_OR_MODE")
                    source.unlink()
                    source.symlink_to(root / "nonexistent")
                    with self.assertRaises(installer.InstallHold):
                        installer._read_root_inbox_file(fd, "helper.bin", 100)
            finally:
                os.close(fd)

    def test_atomic_publish_failed_fsync_preserves_existing_target_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "host-helper-approval.json"
            old.write_bytes(b"old-root-intent")
            old.chmod(0o400)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with patch.object(installer, "ROOT_UID", os.getuid()), \
                        patch.object(installer.os, "fsync", side_effect=OSError("synthetic fsync failure")):
                    with self.assertRaises(installer.InstallHold):
                        installer.atomic_publish(fd, "host-helper-approval.json", b"new", 0o400)
            finally:
                os.close(fd)
            self.assertEqual(old.read_bytes(), b"old-root-intent")
            self.assertEqual([entry.name for entry in root.iterdir()],
                             ["host-helper-approval.json"])

    def test_installation_order_publishes_approval_last_and_partial_failure_cannot_seal(self):
        manifest, sources, _, _ = bundle()
        with patch.object(installer, "TRUSTED_MANIFEST_SHA256", digest(manifest)):
            verified = installer.verify_approved_bundle(manifest, sources, now=NOW)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            recorded = []
            real_publish = installer.atomic_publish

            def publish(handle, name, raw, mode):
                recorded.append(name)
                if name == "host-launch-profiles.json":
                    raise installer.InstallHold("SYNTHETIC_PARTIAL_INSTALL")
                return real_publish(handle, name, raw, mode)

            try:
                with patch.object(installer, "_open_root_directory", side_effect=lambda _p: os.dup(fd)), \
                        patch.object(installer, "atomic_publish", side_effect=publish):
                    with self.assertRaises(installer.InstallHold) as caught:
                        installer.publish_verified_bundle(verified)
            finally:
                os.close(fd)
            self.assertEqual(caught.exception.reason, "SYNTHETIC_PARTIAL_INSTALL")
            self.assertEqual(recorded, ["porota-rc6-oci-custody",
                                        "rc6_runtime_oci_custody.py",
                                        "host-launch-profiles.json"])
            self.assertFalse((root / "host-helper-approval.json").exists())
            self.assertEqual((root / "porota-rc6-oci-custody").read_bytes(), sources["helper"])

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with patch.object(installer, "_open_root_directory", side_effect=lambda _p: os.dup(fd)):
                    installer.publish_verified_bundle(verified)
            finally:
                os.close(fd)
            self.assertEqual((root / "host-helper-approval.json").read_bytes(),
                             sources["approval"])
            self.assertEqual(stat.S_IMODE((root / "porota-rc6-oci-custody").stat().st_mode),
                             0o555)


if __name__ == "__main__":
    unittest.main()
