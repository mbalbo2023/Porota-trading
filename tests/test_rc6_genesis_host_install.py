"""R367 fixed-argument R358 handoff on a temporary real filesystem."""
from contextlib import ExitStack
import hashlib
import importlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_genesis_custodian as private
from scripts import rc6_host_rootowned_installer_contract as r280
from scripts import rc6_r302_bootstrap_receipt_preflight as r302
from scripts import rc6_r305_root_pin_transition_guard as r305
from scripts import rc6_root_install_new_files_only as r358
from tests import test_rc6_host_rootowned_installer_contract as fixture


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class FixedInstall(unittest.TestCase):
    def setUp(self):
        self.app = importlib.import_module("scripts.rc6_genesis_host_install")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for key, value in (("_FS_ROOT", str(self.root)), ("_ROOT_UID", os.getuid()), ("_ROOT_GID", os.getgid())):
            self.stack.enter_context(patch.object(private, key, value))
        manifest, self.sources, doc, _ = fixture.bundle()
        self.manifest = private._json(doc)
        self.files = {r280.SOURCE_NAMES[role]: value for role, value in self.sources.items()}
        self.files["manifest.json"] = self.manifest
        self.auth = {"binding": {"trusted_main_sha": "c"*40, "candidate_sha": fixture.SHA,
                    "candidate_tree": fixture.TREE, "host_identity_sha256": "d"*64},
                    "root_manifest_sha256": sha(self.manifest), "witness_artifact_sha256": "e"*64,
                    "witness_run_id": "5432", "witness_run_attempt": "2", "pin_go_id": 197,
                    "install_go_id": 198, "env_go_id": 199, "lease_id": 195,
                    "proposal_id": "a"*32, "commitment": "b"*64, "nonce": "f"*32}
        ctx = r305.PinContext("c"*40, "d"*40, fixture.SHA, fixture.TREE,
                              sha(self.manifest), "e"*64, "12345678", "nyc1")
        receipt = r305._transition_receipt(ctx, original_go_id=197, witness={"run_id":"5432", "run_attempt":"2"})
        self.create(r305.ROOT_PIN, (sha(self.manifest)+"\n").encode())
        self.create(r305.ROOT_OLD_ARCHIVE, (r305.HISTORICAL_D_PIN+"\n").encode())
        self.create(r302.ROOT_RECEIPT, r302._expected_receipt_bytes())
        self.create(r305.ROOT_TRANSITION_RECEIPT, receipt)
        self.path(r305.ROOT_DIRECTORY).chmod(0o700)
        self.launcher = self.create(str(r358.LAUNCHER), Path(r358.__file__).read_bytes(), 0o555)
        for path, _, _ in r280.TARGETS.values():
            self.path(path).parent.mkdir(parents=True, exist_ok=True)
        self.inbox = self.path(r280.INBOX_DIR)
        self.boundaries = []
        self.fail_after = None
        self.runner = self.stack.enter_context(patch.object(self.app.subprocess, "run", side_effect=self.invoke))

    def path(self, path):
        return self.root / str(path).lstrip("/")

    def create(self, path, raw, mode=0o400):
        target = self.path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        target.chmod(mode)
        return target

    def invoke(self, argv, **kw):
        self.assertEqual(tuple(argv), ("/usr/bin/python3", "-I", str(r358.LAUNCHER), "install-approved"))
        self.assertNotIn("input", kw)
        self.assertIs(kw["stdin"], subprocess.DEVNULL)
        self.assertGreater(kw["timeout"], 0)
        self.assertNotIn("HOST_SECRET", kw["env"])
        self.assertEqual(set(self.files), set(p.name for p in self.inbox.iterdir()))
        for name, raw in self.files.items():
            self.assertEqual((self.inbox/name).read_bytes(), raw)
            self.assertEqual((self.inbox/name).stat().st_mode & 0o777, 0o400)
        self.assertEqual(self.boundaries[-2:], ["env", "go"])
        # Exercise the canonical publisher on these bytes; fixed absolute parents
        # are redirected into this fixture and no real host process is started.
        roles = {role:(str(self.path(path)), mode, cap) for role,(path,mode,cap) in r280.TARGETS.items()}
        with patch.object(r280, "TARGETS", roles), patch.object(r280, "ROOT_UID", os.getuid()), \
                patch.object(r280, "TRUSTED_MANIFEST_SHA256", sha(self.manifest)), \
                patch.object(r280, "_open_root_directory", side_effect=lambda name: os.open(name, os.O_RDONLY|os.O_DIRECTORY)), \
                patch.object(r358.os, "geteuid", return_value=os.getuid() or 1000):
            receipt = r358._publish_genesis(r280, self.manifest, self.sources, recheck_pin=lambda: None)
        if self.fail_after == "r358":
            raise TimeoutError("response lost")
        wire = {"schema":"RC6_ROOT_INSTALL_CODE_ONLY_V1","status":receipt["status"],
                "candidate_sha":fixture.SHA,"manifest_sha256":sha(self.manifest),"host_control_verified":False}
        kw["stdout"].write(private._json(wire))
        return subprocess.CompletedProcess(argv, 0)

    def install(self):
        return self.app.install(self.auth, self.files, check_authority=lambda:self.boundaries.append("go"),
                                verify_env=lambda:self.boundaries.append("env"))

    def test_root_receipt_binds_historical_r301_pin_and_real_r305_witness(self):
        self.app.verify_transition(self.auth, self.manifest)
        for key in ("pin_go_id", "witness_artifact_sha256", "witness_run_id", "witness_run_attempt"):
            value = 400 if key == "pin_go_id" else "9"*64 if key.endswith("sha256") else "99"
            with patch.dict(self.auth, {key:value}), self.assertRaises(private.CustodianHold):
                self.app.verify_transition(self.auth, self.manifest)
        for path in (r305.ROOT_PIN, r302.ROOT_RECEIPT, r305.ROOT_OLD_ARCHIVE, r305.ROOT_TRANSITION_RECEIPT):
            leaf = self.path(path)
            os.link(leaf, self.root/"alias")
            try:
                with self.assertRaises(private.CustodianHold):
                    self.app.verify_transition(self.auth, self.manifest)
            finally:
                (self.root/"alias").unlink()

    def test_installs_four_files_and_accepts_only_its_own_duplicate(self):
        self.assertEqual(self.install(), "INSTALLED_FILES_ONLY_NOT_HOST_GO")
        self.assertEqual(self.install(), "INSTALLED_FILES_ONLY_NOT_HOST_GO")
        self.assertEqual(self.runner.call_count, 1)
        leaf = self.path(r280.TARGETS["helper"][0])
        copy = leaf.with_name("other")
        copy.write_bytes(leaf.read_bytes())
        copy.chmod(0o555)
        os.replace(copy, leaf)
        with self.assertRaises(private.CustodianHold):
            self.install()
        self.assertEqual(self.runner.call_count, 1)

    def test_foreign_inbox_and_partial_remain_untouched(self):
        self.inbox.mkdir(mode=0o700)
        (self.inbox/"foreign").write_bytes(b"keep")
        with self.assertRaises(private.CustodianHold):
            self.install()
        self.assertEqual((self.inbox/"foreign").read_bytes(), b"keep")
        self.runner.assert_not_called()
        (self.inbox/"foreign").unlink()
        self.inbox.rmdir()
        self.fail_after = "r358"
        with self.assertRaises(TimeoutError):
            self.install()
        with self.assertRaises(private.CustodianHold):
            self.install()
        self.assertEqual(self.runner.call_count, 1)

    def test_foreign_launcher_and_denied_authority_never_create_inbox(self):
        self.launcher.unlink()
        self.create(str(r358.LAUNCHER), b"other", 0o555)
        with self.assertRaises(private.CustodianHold):
            self.install()
        self.assertFalse(self.inbox.exists())
        self.launcher.unlink()
        self.create(str(r358.LAUNCHER), Path(r358.__file__).read_bytes(), 0o555)
        with self.assertRaises(private.CustodianHold):
            self.app.install(self.auth, self.files, check_authority=lambda:False, verify_env=lambda:None)
        self.assertFalse(self.inbox.exists())
        self.runner.assert_not_called()

    def test_renamed_or_unpublished_owned_inbox_can_resume_only_with_same_lease_and_inodes(self):
        rename = self.app.seal._rename_no_replace
        def inject(parent, source, target):
            if target == self.inbox.name:
                raise OSError("before durable inbox rename")
            return rename(parent, source, target)
        with patch.object(self.app.seal, "_rename_no_replace", side_effect=inject), self.assertRaises(OSError):
            self.install()
        self.assertFalse(self.inbox.exists())
        self.runner.assert_not_called()
        with patch.dict(self.auth, nonce="8"*32), self.assertRaisesRegex(private.CustodianHold, "OTHER_OWNER"):
            self.install()
        def lost_reply(parent, source, target):
            rename(parent, source, target)
            if target == self.inbox.name:
                raise OSError("lost rename response")
        with patch.object(self.app.seal, "_rename_no_replace", side_effect=lost_reply), self.assertRaises(OSError):
            self.install()
        first = self.inbox.stat().st_ino
        self.assertEqual(self.install(), "INSTALLED_FILES_ONLY_NOT_HOST_GO")
        self.assertEqual(first, self.inbox.stat().st_ino)
        self.assertEqual(self.runner.call_count, 1)

    def test_durable_completion_survives_lost_response_after_final_fsync(self):
        write = private._write_record
        def lose(parent, name, doc):
            write(parent, name, doc)
            if name == self.app._DONE:
                raise OSError("lost final response")
        with patch.object(private, "_write_record", side_effect=lose), self.assertRaises(OSError):
            self.install()
        self.assertEqual(self.install(), "INSTALLED_FILES_ONLY_NOT_HOST_GO")
        self.assertEqual(self.runner.call_count, 1)

    def test_revocation_at_inbox_publication_never_starts_r358(self):
        def check():
            if self.inbox.exists():
                raise private.CustodianHold("REVOKED")
        with self.assertRaisesRegex(private.CustodianHold, "REVOKED"):
            self.app.install(self.auth, self.files, check_authority=check, verify_env=lambda:None)
        self.runner.assert_not_called()
        self.assertTrue(self.inbox.is_dir())
        self.assertTrue(all(not self.path(path).exists() for path, _, _ in r280.TARGETS.values()))

    def test_launcher_failure_never_exposes_untrusted_subprocess_output(self):
        def invalid(argv, **kw):
            kw["stdout"].write(b"private-opaque-fixture\n")
            return subprocess.CompletedProcess(argv, 0)
        self.runner.side_effect = invalid
        with self.assertRaisesRegex(private.CustodianHold, "^INSTALL_LAUNCHER_RECEIPT$"):
            self.install()
        self.assertFalse(self.path(r305.ROOT_DIRECTORY).joinpath(self.app._DONE).exists())
