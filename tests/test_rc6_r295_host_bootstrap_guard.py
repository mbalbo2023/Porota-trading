"""R295 fail-closed trusted-main host bootstrap tests (offline only)."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts import rc6_r295_host_bootstrap_guard as guard


MAIN = "1a8b27063c36e52936c5d67708473ff72775fd39"
ROOT = Path(__file__).resolve().parents[1]


def context(**overrides):
    data = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": MAIN,
        "GITHUB_ACTOR": "mbalbo2023",
        "RC6_R295_OWNER_COMMENT_ID": "6837530000",
        "RC6_EXPECTED_DO_ID": "123456789",
        "RC6_EXPECTED_DO_REGION": "nyc1",
        "DO_HOST": "example.invalid",
        "DO_USER": "porotaadmin",
        "DO_KNOWN_HOSTS": "example.invalid ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFRkfake\n",
        "DO_SSH_KEY": "-----BEGIN OPENSSH PRIVATE KEY-----\nplaceholder\n-----END OPENSSH PRIVATE KEY-----",
        "GITHUB_TOKEN": "runner-masked-token",
    }
    data.update(overrides)
    return data


def owner_comment(**overrides):
    payload = {
        "operation": "TRUSTED_MAIN_ROOT_BOOTSTRAP_FILES_ONLY",
        "issue": 1093,
        "trusted_main_sha": MAIN,
        "installer_cli_sha256": guard.TRUSTED_INSTALLER_CLI_SHA256,
        "installer_contract_sha256": guard.TRUSTED_INSTALLER_CONTRACT_SHA256,
        "paper_mode": "PRODUCTION_PAPER",
        "real_orders_allowed": False,
        "host_control_verified": False,
    }
    data = {
        "id": 6837530000,
        "user": {"login": "mbalbo2023"},
        "author_association": "OWNER",
        "issue_url": "https://api.github.com/repos/mbalbo2023/Porota-trading/issues/1093",
        "created_at": "2026-11-12T22:10:00Z",
        "updated_at": "2026-11-12T22:10:00Z",
        "body": guard.OWNER_GO_MARKER + json.dumps(payload, sort_keys=True, separators=(",", ":")),
    }
    data.update(overrides)
    return data


class R295HostBootstrapGuardTest(unittest.TestCase):
    def test_valid_context_is_not_a_host_go(self):
        parsed = guard.validate_context(context())
        self.assertEqual(parsed.main_sha, MAIN)
        self.assertEqual(parsed.droplet_id, "123456789")
        self.assertFalse(parsed.host_control_verified)

    def test_unsafe_dispatch_context_fails_before_host(self):
        cases = (
            {"GITHUB_EVENT_NAME": "pull_request"},
            {"GITHUB_REF": "refs/heads/deploy/rc6-pr69-isolated-20260915"},
            {"GITHUB_SHA": "F" * 40},
            {"GITHUB_ACTOR": "untrusted-user"},
            {"RC6_R295_OWNER_COMMENT_ID": "123;id"},
            {"RC6_EXPECTED_DO_ID": ""},
            {"RC6_EXPECTED_DO_ID": "1.2.3"},
            {"RC6_EXPECTED_DO_REGION": ""},
            {"RC6_EXPECTED_DO_REGION": "'; id"},
            {"DO_HOST": "site;rm -rf /"},
            {"DO_USER": "root;id"},
            {"DO_KNOWN_HOSTS": ""},
            {"DO_SSH_KEY": ""},
            {"GITHUB_TOKEN": ""},
        )
        for case in cases:
            with self.subTest(case=case), self.assertRaises(guard.BootstrapHold):
                guard.validate_context(context(**case))

    def test_owner_receipt_edited_spoofed_or_replayed_on_new_main_fails(self):
        valid = owner_comment()
        self.assertFalse(guard.verify_owner_go(valid, MAIN, 6837530000)["host_control_verified"])
        forged = (
            {"updated_at": "2026-11-12T22:11:00Z"},
            {"user": {"login": "bot"}},
            {"author_association": "COLLABORATOR"},
            {"issue_url": "https://api.github.com/repos/elsewhere/repo/issues/1093"},
            {"id": 6837530001},
            {"body": "invalid"},
        )
        for case in forged:
            with self.subTest(case=case), self.assertRaises(guard.BootstrapHold):
                guard.verify_owner_go(owner_comment(**case), MAIN, 6837530000)
        with self.assertRaises(guard.BootstrapHold):
            guard.verify_owner_go(valid, "a" * 40, 6837530000)

    def test_go_doc_cannot_authorize_host_or_change_files(self):
        bad = owner_comment()
        raw = bad["body"][len(guard.OWNER_GO_MARKER):]
        payload = json.loads(raw)
        for key, value in (("host_control_verified", True),
                           ("real_orders_allowed", True),
                           ("installer_cli_sha256", "b" * 64),
                           ("unapproved_extra", True)):
            item = dict(payload)
            item[key] = value
            bad["body"] = guard.OWNER_GO_MARKER + json.dumps(item)
            with self.subTest(key=key), self.assertRaises(guard.BootstrapHold):
                guard.verify_owner_go(bad, MAIN, 6837530000)

    def test_source_closure_is_exact_two_sha_pinned_python_files(self):
        sources = guard.verify_reviewed_sources(ROOT)
        self.assertEqual(set(sources), {"cli", "contract"})
        self.assertEqual(hashlib.sha256(sources["cli"]).hexdigest(),
                         guard.TRUSTED_INSTALLER_CLI_SHA256)
        self.assertEqual(hashlib.sha256(sources["contract"]).hexdigest(),
                         guard.TRUSTED_INSTALLER_CONTRACT_SHA256)
        self.assertTrue(sources["cli"].startswith(b"#!/usr/bin/python3 -I\n"))

    def test_source_symlink_and_changed_bytes_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "scripts").mkdir()
            for role, src in (("cli", ROOT / "scripts/rc6_host_rootowned_installer_cli.py"),
                              ("contract", ROOT / "scripts/rc6_host_rootowned_installer_contract.py")):
                target = root / "scripts" / guard.SOURCE_FILES[role]
                target.write_bytes(src.read_bytes())
            self.assertEqual(set(guard.verify_reviewed_sources(root)), {"cli", "contract"})
            target = root / "scripts" / guard.SOURCE_FILES["cli"]
            target.unlink()
            target.symlink_to(ROOT / "scripts/rc6_host_rootowned_installer_cli.py")
            with self.assertRaises(guard.BootstrapHold):
                guard.verify_reviewed_sources(root)
            target.unlink()
            target.write_bytes(b"#!/usr/bin/python3 -I\nprint('changed')\n")
            with self.assertRaises(guard.BootstrapHold):
                guard.verify_reviewed_sources(root)

    def test_host_identity_exact_before_any_root_action(self):
        parsed = guard.validate_context(context())
        self.assertIsNone(guard.verify_host_identity(b"123456789\n", b"nyc1\n", parsed))
        for observed in ((b"111111111\n", b"nyc1\n"),
                         (b"123456789\n", b"sfo3\n"),
                         (b"123456789\nextra", b"nyc1\n"),
                         (b"", b"nyc1")):
            with self.subTest(observed=observed), self.assertRaises(guard.BootstrapHold):
                guard.verify_host_identity(*observed, parsed)

    def test_unreviewed_root_commands_are_not_present(self):
        self.assertFalse(hasattr(guard, "ROOT_COMMANDS"))
        self.assertFalse(hasattr(guard, "execute_bootstrap"))
        self.assertFalse(hasattr(guard, "ssh_host"))

    def test_workflow_is_dispatch_only_and_physically_hold79(self):
        source = (ROOT / ".github/workflows/rc6-r295-host-bootstrap-trusted.yml").read_text()
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("group: rc6-unified-paper-deploy", source)
        self.assertIn("cancel-in-progress: false", source)
        self.assertIn("refs/heads/main", source)
        self.assertIn("--check-offhost", source)
        self.assertNotIn("ssh -", source)
        self.assertNotIn("scp ", source)
        self.assertNotIn("sudo -", source)
        self.assertNotIn("docker ", source)
        self.assertNotIn("systemctl", source)
        self.assertNotIn("--execute-bootstrap", source)

    def test_offhost_check_stays_hold79_even_with_a_context(self):
        from contextlib import redirect_stdout
        import io
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = guard.main(["--check-offhost"], env=context())
        self.assertEqual(code, 79)
        self.assertIn("host_control_verified=NO_VERIFICADO", stream.getvalue())
        self.assertIn("deploy_authorized=NO", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
