"""R280-B offline CLI guard tests: no SSH, sudo, Docker or host install."""

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_host_rootowned_installer_cli as cli


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_SOURCE = ROOT / "scripts/rc6_host_rootowned_installer_contract.py"
MANIFEST_DIGEST = "a" * 64


class FixedRootInstallerCliTest(unittest.TestCase):
    def test_only_literal_fixed_install_approved_verb_is_accepted(self):
        for argv in ([], ["--help"], ["install"], ["install-approved", "--path", "/tmp"],
                     ["install-approved", "$(id)"], ["status"], ["install-approved\n"]):
            with self.subTest(argv=argv), self.assertRaises(cli.CliHold) as caught:
                cli.validate_invocation(argv, euid=0, isolated=1,
                                        current_path=cli.INSTALLED_CLI_PATH)
            self.assertEqual(caught.exception.reason, "FIXED_CLI_VERB_ONLY")

    def test_checkout_nonroot_and_nonisolated_invocations_hold_without_side_effects(self):
        cases = (
            (1000, 1, cli.INSTALLED_CLI_PATH, "ROOT_EUID_REQUIRED"),
            (0, 0, cli.INSTALLED_CLI_PATH, "PYTHON_ISOLATED_REQUIRED"),
            (0, 1, str(ROOT / "scripts/rc6_host_rootowned_installer_cli.py"),
             "ROOT_INSTALLED_CLI_PATH_REQUIRED"),
        )
        for uid, isolated, current, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(cli.CliHold) as caught:
                cli.validate_invocation(["install-approved"], euid=uid,
                                        isolated=isolated, current_path=current)
            self.assertEqual(caught.exception.reason, reason)

    def test_valid_fixed_invocation_is_not_itself_host_permission(self):
        self.assertIsNone(cli.validate_invocation(["install-approved"],
                                                  euid=0, isolated=1,
                                                  current_path=cli.INSTALLED_CLI_PATH))

    def test_root_owned_file_reader_rejects_symlink_hardlink_writeable_inode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "approved.sha"
            original.write_bytes(MANIFEST_DIGEST.encode() + b"\n")
            original.chmod(0o400)
            with patch.object(cli, "ROOT_UID", os.getuid()), \
                    patch.object(cli, "APPROVED_MANIFEST_PIN_FILE", str(original)), \
                    patch.object(cli, "_open_root_directory", side_effect=lambda _path: os.open(root, os.O_RDONLY|os.O_DIRECTORY)):
                self.assertEqual(cli.read_root_protected_file(str(original),
                                                              max_bytes=128, mode=0o400),
                                 MANIFEST_DIGEST.encode() + b"\n")
                alias = root / "extra-link"
                os.link(original, alias)
                with self.assertRaises(cli.CliHold):
                    cli.read_root_protected_file(str(original), max_bytes=128, mode=0o400)
                alias.unlink()
                original.chmod(0o620)
                with self.assertRaises(cli.CliHold):
                    cli.read_root_protected_file(str(original), max_bytes=128, mode=0o400)
                original.unlink()
                original.symlink_to(root / "missing")
                with self.assertRaises(cli.CliHold):
                    cli.read_root_protected_file(str(original), max_bytes=128, mode=0o400)

    def test_nofollow_missing_holds_before_file_open(self):
        with patch.object(cli, "NOFOLLOW_FLAG", None):
            with self.assertRaises(cli.CliHold) as caught:
                cli.read_root_protected_file(cli.APPROVED_MANIFEST_PIN_FILE,
                                             max_bytes=128, mode=0o400)
        self.assertEqual(caught.exception.reason, "OS_NOFOLLOW_REQUIRED")

    def test_independent_manifest_pin_is_strict_not_from_dispatch(self):
        self.assertEqual(cli.parse_approved_manifest_pin(MANIFEST_DIGEST.encode() + b"\n"),
                         MANIFEST_DIGEST)
        for raw in (b"", b"none", b"0"*63, b"b"*64+b";id", b"A"*64,
                    b"c"*64+b"\n\n", b"d"*64+b"\r", b"e"*64+b"\x00"):
            with self.subTest(raw=raw[:12]), self.assertRaises(cli.CliHold):
                cli.parse_approved_manifest_pin(raw)

    def test_contract_module_sha_is_independently_pinned_before_execution(self):
        raw = CONTRACT_SOURCE.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), cli.TRUSTED_CONTRACT_SHA256)
        installed = cli.load_verified_contract_module(raw)
        self.assertTrue(callable(installed.install_approved_bundle))
        self.assertIsNone(installed.TRUSTED_MANIFEST_SHA256)
        with self.assertRaises(cli.CliHold) as caught:
            cli.load_verified_contract_module(raw + b"\n# unapproved change\n")
        self.assertEqual(caught.exception.reason, "ROOT_CONTRACT_CODE_PIN_MISMATCH")

    def test_strict_receipt_is_not_host_go_runtime_or_paper_zero_proof(self):
        receipt = {"schema": "RC6_ROOT_INSTALL_CODE_ONLY_V1",
                   "status": "INSTALLED_FILES_ONLY_NOT_HOST_GO",
                   "manifest_sha256": MANIFEST_DIGEST,
                   "candidate_sha": "a" * 40,
                   "host_control_verified": False}
        self.assertEqual(cli.validate_installer_receipt(receipt, MANIFEST_DIGEST), receipt)
        for malformed in (
            dict(receipt, host_control_verified=True),
            dict(receipt, status="HOST_GO"),
            dict(receipt, real_orders_sent=0),
            dict(receipt, manifest_sha256="0" * 64),
            dict(receipt, candidate_sha="b" * 39),
        ):
            with self.subTest(malformed=malformed), self.assertRaises(cli.CliHold):
                cli.validate_installer_receipt(malformed, MANIFEST_DIGEST)

    def test_main_from_checkout_returns_sanitized_hold79_without_root_io(self):
        output = io.StringIO()
        with redirect_stdout(output):
            status = cli.main(["install-approved"])
        self.assertEqual(status, 79)
        self.assertIn("HOLD79", output.getvalue())
        self.assertIn("host_control_verified=NO_VERIFICADO", output.getvalue())
        self.assertNotIn(cli.APPROVED_MANIFEST_PIN_FILE, output.getvalue())


if __name__ == "__main__":
    unittest.main()
