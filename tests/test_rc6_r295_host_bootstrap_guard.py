"""R295 fail-closed trusted-main host bootstrap tests (offline only)."""

import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile
import unittest

from scripts import rc6_r295_host_bootstrap_guard as guard


MAIN = "1a8b27063c36e52936c5d67708473ff72775fd39"
ROOT = Path(__file__).resolve().parents[1]


class FakeRootHost:
    """No shell/network: simulate only R297 fixed root commands."""
    def __init__(self):
        self.dirs = set(guard.ROOT_PARENTS_EXISTING)
        self.files = {}
        self.file_inodes = {}
        self.next_inode = 1000
        self.symlinks = set()
        self.calls = []
        self.fail_part = None
        self.host_id = b"123456789\n"
        self.host_region = b"nyc1\n"

    def call(self, cmd, stdin=None):
        self.calls.append((cmd, stdin))
        if self.fail_part is not None and self.fail_part in cmd:
            return (1, b"")
        if cmd == guard.HOST_ID_COMMAND:
            return (0, self.host_id)
        if cmd == guard.HOST_REGION_COMMAND:
            return (0, self.host_region)
        if cmd.startswith("/usr/bin/sudo -n /usr/bin/test -L "):
            path = cmd.rsplit(" ", 1)[-1]
            return (0 if path in self.symlinks else 1, b"")
        if cmd.startswith("/usr/bin/sudo -n /usr/bin/test -e "):
            path = cmd.removeprefix("/usr/bin/sudo -n /usr/bin/test -e ")
            return (0 if path in self.dirs or path in self.files else 1, b"")
        if cmd.startswith("/usr/bin/sudo -n /usr/bin/stat -c "):
            path = cmd.rsplit(" ", 1)[-1]
            if path in self.dirs:
                return (0, b"0|700|2|directory\n")
            if path in self.files:
                _data, mode = self.files[path]
                if "%d|%i" in cmd:
                    inode = self.file_inodes.get(path, 0)
                    links = sum(value == inode for value in self.file_inodes.values())
                    return (0, f"0|{mode:03o}|{links}|regular file|2049|{inode}\n".encode())
                return (0, f"0|{mode:03o}|1|regular file\n".encode())
            return (1, b"")
        if (cmd.startswith("/usr/bin/sudo -n /usr/bin/mkdir -m 0700 -- ") or
                cmd.startswith("/usr/bin/sudo -n /usr/bin/mkdir -m 0755 -- ")):
            path = cmd.rsplit(" ", 1)[-1]
            if path in self.dirs:
                return (1, b"")
            self.dirs.add(path)
            return (0, b"")
        if cmd.startswith("/usr/bin/sudo -n /usr/bin/install -m "):
            items = cmd.split()
            path = items[-1]
            mode = int(items[4], 8)
            if path in self.files or stdin is None:
                return (1, b"")
            self.files[path] = (stdin, mode)
            self.next_inode += 1
            self.file_inodes[path] = self.next_inode
            return (0, b"")
        if cmd.startswith("/usr/bin/sudo -n /usr/bin/sha256sum -- "):
            path = cmd.rsplit(" ", 1)[-1]
            if path not in self.files:
                return (1, b"")
            return (0, f"{hashlib.sha256(self.files[path][0]).hexdigest()}  {path}\n".encode())
        if cmd.startswith("/usr/bin/sudo -n /usr/bin/sync -- "):
            return (0, b"")
        if cmd.startswith("/usr/bin/sudo -n /usr/bin/ln -T -- "):
            source, target = cmd.split()[-2:]
            if source not in self.files or target in self.files:
                return (1, b"")
            self.files[target] = self.files[source]
            self.file_inodes[target] = self.file_inodes[source]
            return (0, b"")
        if cmd.startswith("/usr/bin/sudo -n /usr/bin/rm -- "):
            source = cmd.rsplit(" ", 1)[-1]
            if source not in self.files:
                return (1, b"")
            del self.files[source]
            self.file_inodes.pop(source, None)
            return (0, b"")
        raise AssertionError("Unreviewed command: " + cmd)


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
        "approved_manifest_sha256": "c" * 64,
        "expires_at": "2026-11-12T23:40:00Z",
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

    def test_offhost_owner_go_does_not_load_droplet_ssh_secrets(self):
        from contextlib import redirect_stdout
        import io
        offhost = context()
        for secret in ("DO_HOST", "DO_USER", "DO_SSH_KEY", "DO_KNOWN_HOSTS",
                       "RC6_EXPECTED_DO_ID", "RC6_EXPECTED_DO_REGION"):
            del offhost[secret]
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = guard.main(["--check-offhost"], env=offhost,
                              owner_getter=lambda _ctx: {
                                  "operation": "TRUSTED_MAIN_ROOT_BOOTSTRAP_FILES_ONLY",
                                  "host_control_verified": False,
                                  "approved_manifest_sha256": "c" * 64},
                              main_ref_getter=lambda _ctx: MAIN,
                              host_factory=lambda *_args: (_ for _ in ()).throw(
                                  AssertionError("offhost cannot open SSH")))
        self.assertEqual(code, 0)
        self.assertIn("OWNER_GO_OFFHOST_VERIFIED", stream.getvalue())
        self.assertNotIn("DROPLET", stream.getvalue())

    def test_recovery_cli_requires_distinct_go_and_reports_files_only(self):
        from contextlib import redirect_stdout
        import io
        go = guard.verify_owner_go(
            owner_comment(), MAIN, 6837530000,
            now=datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc))
        original_context = guard.validate_context(context())
        current = context(RC6_R295_OWNER_COMMENT_ID="6837530001")
        recovery_go = dict(go,
                           operation="TRUSTED_MAIN_ROOT_BOOTSTRAP_RESUME_FILES_ONLY",
                           original_owner_go_comment_id=6837530000,
                           original_trusted_main_sha=MAIN)
        host = FakeRootHost()
        host.dirs.add(guard.ROOT_LOCK_DIR)
        # Recovery has authenticated, immutable partial bytes; a bare lock
        # alone would have insufficient attribution and must stay HOLD79.
        sources = guard.verify_reviewed_sources(ROOT)
        stage = guard.ROOT_STAGE_TARGETS[0][1]
        host.files[stage] = (sources["cli"], 0o555)
        host.file_inodes[stage] = 1001
        stream = io.StringIO()

        class HostContext:
            def __enter__(self): return host
            def __exit__(self, *_exc): return None

        with redirect_stdout(stream):
            code = guard.main(
                ["--resume-approved-bootstrap"], env=current,
                owner_getter=lambda _ctx: recovery_go,
                original_go_getter=lambda _ctx, _go: (original_context, go),
                main_ref_getter=lambda _ctx: MAIN,
                host_factory=lambda *_args: HostContext())
        self.assertEqual(code, 0)
        self.assertIn("FILES_INSTALLED_ONLY_NOT_HOST_GO", stream.getvalue())
        self.assertNotIn("host_control_verified=YES", stream.getvalue())
        self.assertIn(guard.ROOT_RECEIPT, host.files)

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

    def test_github_2026_app_jwt_token_is_opaque_and_safe_as_header(self):
        """A new ghs_APPID_JWT must survive entry and fixed GitHub GET."""
        from unittest import mock

        # Deliberately synthetic, not a secret or a cryptographically valid JWT.
        token = "ghs_123456_eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig-_.~+/="
        guard.validate_context(context(GITHUB_TOKEN=token))
        response = mock.Mock(status=200)
        response.getheader.return_value = "application/json"
        response.read.return_value = b'{}'
        connection = mock.Mock()
        connection.getresponse.return_value = response
        with mock.patch.object(guard.http.client, "HTTPSConnection",
                               return_value=connection):
            self.assertEqual(guard.fetch_github_https_bytes(
                "/repos/mbalbo2023/Porota-trading/git/ref/heads/main",
                token), b'{}')
        self.assertEqual(connection.request.call_args.kwargs["headers"]["Authorization"],
                         "Bearer " + token)

    def test_opaque_token_must_never_inject_http_header_or_escape_ascii(self):
        for token in ("a" * 12 + "\rInjected:yes", "a" * 12 + "\nInjected:yes",
                      "a" * 12 + "\x00", "a" * 12 + "\t", "a" * 12 + " ",
                      "a" * 12 + "\u00e9", "short"):
            with self.subTest(token_kind=repr(token[-4:])), self.assertRaises(
                    guard.BootstrapHold):
                guard.validate_context(context(GITHUB_TOKEN=token))
            with self.subTest(token_kind=repr(token[-4:])), self.assertRaises(
                    guard.BootstrapHold):
                guard.fetch_github_https_bytes(
                    "/repos/mbalbo2023/Porota-trading/git/ref/heads/main", token)

    def test_owner_receipt_edited_spoofed_or_replayed_on_new_main_fails(self):
        valid = owner_comment()
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        self.assertFalse(guard.verify_owner_go(valid, MAIN, 6837530000, now=now)["host_control_verified"])
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
                guard.verify_owner_go(owner_comment(**case), MAIN, 6837530000, now=now)
        with self.assertRaises(guard.BootstrapHold):
            guard.verify_owner_go(valid, "a" * 40, 6837530000, now=now)

    def test_go_doc_cannot_authorize_host_or_change_files(self):
        bad = owner_comment()
        raw = bad["body"][len(guard.OWNER_GO_MARKER):]
        payload = json.loads(raw)
        for key, value in (("host_control_verified", True),
                           ("real_orders_allowed", True),
                           ("installer_cli_sha256", "b" * 64),
                           ("unapproved_extra", True),
                           ("approved_manifest_sha256", "not-a-pin")):
            item = dict(payload)
            item[key] = value
            bad["body"] = guard.OWNER_GO_MARKER + json.dumps(item)
            with self.subTest(key=key), self.assertRaises(guard.BootstrapHold):
                guard.verify_owner_go(bad, MAIN, 6837530000,
                                      now=datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc))

    def test_recovery_owner_go_is_distinct_exact_and_cannot_reuse_initial_go(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        initial = owner_comment()
        doc = json.loads(initial["body"][len(guard.OWNER_GO_MARKER):])
        doc.update(operation="TRUSTED_MAIN_ROOT_BOOTSTRAP_RESUME_FILES_ONLY",
                   original_owner_go_comment_id=6837530000,
                   original_trusted_main_sha=MAIN)
        recovery = owner_comment(
            id=6837530001,
            body=guard.OWNER_GO_MARKER + json.dumps(doc, sort_keys=True))
        verified = guard.verify_owner_go(recovery, MAIN, 6837530001, now=now)
        self.assertEqual(verified["original_owner_go_comment_id"], 6837530000)
        self.assertEqual(verified["original_trusted_main_sha"], MAIN)
        for change in ({"original_owner_go_comment_id": 6837530001},
                       {"original_owner_go_comment_id": "6837530000"},
                       {"original_trusted_main_sha": "not-a-sha"},
                       {"unexpected_recovery_key": "x"}):
            invalid = dict(doc, **change)
            with self.subTest(change=change), self.assertRaises(guard.BootstrapHold):
                guard.verify_owner_go(
                    owner_comment(id=6837530001,
                                  body=guard.OWNER_GO_MARKER + json.dumps(invalid)),
                    MAIN, 6837530001, now=now)

    def test_recovery_original_owner_go_must_be_reverified_from_github(self):
        new_context = guard.validate_context(
            context(RC6_R295_OWNER_COMMENT_ID="6837530001"))
        doc = json.loads(owner_comment()["body"][len(guard.OWNER_GO_MARKER):])
        recovery = dict(doc, operation="TRUSTED_MAIN_ROOT_BOOTSTRAP_RESUME_FILES_ONLY",
                        original_owner_go_comment_id=6837530000,
                        original_trusted_main_sha=MAIN)
        paths = []

        def get_original(path, _token):
            paths.append(path)
            return json.dumps(owner_comment()).encode()

        original_context, original_go = guard.fetch_original_recovery_go(
            new_context, recovery, token="runner-masked-token",
            fetch_bytes=get_original)
        self.assertEqual(paths, [
            "/repos/mbalbo2023/Porota-trading/issues/comments/6837530000"])
        self.assertEqual(original_context.owner_comment_id, 6837530000)
        self.assertEqual(original_go["operation"],
                         "TRUSTED_MAIN_ROOT_BOOTSTRAP_FILES_ONLY")
        with self.assertRaises(guard.BootstrapHold):
            guard.fetch_original_recovery_go(
                new_context, dict(recovery, approved_manifest_sha256="b" * 64),
                token="runner-masked-token", fetch_bytes=get_original)

    def test_go_receipt_expires_and_rejects_future_or_stale_owner_grants(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        bad = owner_comment()
        payload = json.loads(bad["body"][len(guard.OWNER_GO_MARKER):])
        for expiry in ("2026-11-12T22:34:59Z", "2026-11-14T23:40:00Z",
                       "2026-11-12 23:40:00", "not-a-time"):
            payload["expires_at"] = expiry
            bad["body"] = guard.OWNER_GO_MARKER + json.dumps(payload)
            with self.subTest(expiry=expiry), self.assertRaises(guard.BootstrapHold):
                guard.verify_owner_go(bad, MAIN, 6837530000, now=now)

    def test_owner_go_requires_external_manifest_pin_never_bundle_derived(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        doc = guard.verify_owner_go(owner_comment(), MAIN, 6837530000, now=now)
        self.assertEqual(doc["approved_manifest_sha256"], "c" * 64)
        self.assertEqual(guard.approved_manifest_pin_bytes(doc), b"c" * 64 + b"\n")
        altered = dict(doc, approved_manifest_sha256="a" * 64)
        self.assertNotEqual(guard.approved_manifest_pin_bytes(altered),
                            guard.approved_manifest_pin_bytes(doc))

    def test_get_owner_go_only_exact_fixed_github_issue_and_comment_id(self):
        parsed = guard.validate_context(context())
        received = []
        def fake_get(path, token):
            received.append((path, token))
            return json.dumps(owner_comment(), separators=(",", ":")).encode()
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        result = guard.fetch_and_verify_owner_go(parsed, token="runner-masked-token",
                                                fetch_bytes=fake_get, now=now)
        self.assertEqual(result["approved_manifest_sha256"], "c" * 64)
        self.assertEqual(received, [(
            "/repos/mbalbo2023/Porota-trading/issues/comments/6837530000",
            "runner-masked-token",
        )])
        for raw in (b"", b"x" * 16385, b"[]", b"{}", b"{\"id\":1}"):
            with self.subTest(raw=raw[:30]), self.assertRaises(guard.BootstrapHold):
                guard.fetch_and_verify_owner_go(parsed, token="runner-masked-token",
                                                fetch_bytes=lambda _p, _t: raw,
                                                now=now)

    def test_https_fetch_transport_does_not_follow_redirects_or_change_domain(self):
        from unittest import mock
        response = mock.Mock(status=302, reason="Found")
        response.getheader.return_value = "application/json"
        connection = mock.Mock()
        connection.getresponse.return_value = response
        with mock.patch.object(guard.http.client, "HTTPSConnection", return_value=connection):
            with self.assertRaises(guard.BootstrapHold):
                guard.fetch_github_https_bytes(
                    "/repos/mbalbo2023/Porota-trading/issues/comments/6837530000",
                    "runner-masked-token")
        self.assertEqual(connection.request.call_args.args[0], "GET")
        self.assertTrue(connection.request.call_args.args[1].startswith(
            "/repos/mbalbo2023/Porota-trading/issues/comments/"))
        self.assertEqual(connection.request.call_args.kwargs["headers"]["Authorization"],
                         "Bearer runner-masked-token")

    def test_live_main_head_sha_must_equal_event_even_after_approved_go(self):
        expected = guard.validate_context(context())
        path_seen = []
        def fake_get(path, token):
            path_seen.append((path, token))
            return json.dumps({"ref": "refs/heads/main", "object": {"sha": MAIN, "type": "commit"}}).encode()
        guard.verify_live_main_head(expected, token="runner-masked-token", fetch_bytes=fake_get)
        self.assertEqual(path_seen[0][0], "/repos/mbalbo2023/Porota-trading/git/ref/heads/main")
        for bad in ({"ref": "refs/heads/main", "object": {"sha": "a" * 40, "type": "commit"}},
                    {"ref": "refs/heads/product", "object": {"sha": MAIN, "type": "commit"}},
                    {"ref": "refs/heads/main", "object": {"sha": MAIN, "type": "tag"}},
                    {}):
            with self.subTest(bad=bad), self.assertRaises(guard.BootstrapHold):
                guard.verify_live_main_head(expected, token="runner-masked-token",
                                            fetch_bytes=lambda _p, _t: json.dumps(bad).encode())

    def test_fixed_terminal_status_clipboard_escapes_only_sanitized_report(self):
        report = "RC6_R297=HOLD79|id=MATCH|region=MATCH|sudo=YES|cli=ABSENT"
        value = guard.safe_osc52_status(report)
        self.assertTrue(value.startswith("\x1b]52;c;"))
        self.assertTrue(value.endswith("\x07"))
        for malicious in ("secret=abc", "DO_ID=123456", "host=example.org", "\x1b[2J",
                          "line\nsecond"):
            with self.subTest(malicious=malicious), self.assertRaises(guard.BootstrapHold):
                guard.safe_osc52_status(malicious)

    def test_os_root_inventory_denies_symlink_nonroot_writable_and_hardlink(self):
        self.assertIsNone(guard.validate_root_stat(b"0|755|2|directory\n", is_dir=True))
        self.assertIsNone(guard.validate_root_stat(b"0|400|1|regular file\n", is_dir=False,
                                                        mode=0o400))
        for raw, is_dir in ((b"1000|755|2|directory\n", True),
                            (b"0|777|2|directory\n", True),
                            (b"0|755|1|symbolic link\n", True),
                            (b"0|400|2|regular file\n", False),
                            (b"0|644|1|regular file\n", False),
                            (b"0|400|1|symbolic link\n", False)):
            with self.subTest(raw=raw), self.assertRaises(guard.BootstrapHold):
                guard.validate_root_stat(raw, is_dir=is_dir,
                                         mode=0o400 if not is_dir else None)

    def test_root_bootstrap_plan_contains_only_exact_rootowned_porota_paths(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        go = guard.verify_owner_go(owner_comment(), MAIN, 6837530000, now=now)
        source = guard.verify_reviewed_sources(ROOT)
        plan = guard.build_root_bootstrap_plan(go, source)
        self.assertEqual(tuple(item.role for item in plan), ("cli", "contract", "pin"))
        self.assertEqual(tuple(item.bytes for item in plan)[:2],
                         (source["cli"], source["contract"]))
        self.assertEqual(plan[-1].bytes, b"c" * 64 + b"\n")
        self.assertEqual(tuple(item.mode for item in plan), (0o555, 0o400, 0o400))
        self.assertEqual(tuple(item.target for item in plan), (
            "/usr/local/sbin/porota-rc6-host-installer",
            "/usr/local/libexec/porota-rc6-custody/rc6_host_rootowned_installer_contract.py",
            "/var/lib/porota-rc6-oci-custody/installer-approved-manifest.sha256",
        ))
        for item in plan:
            self.assertEqual(Path(item.stage).parent, Path(item.target).parent)
            self.assertTrue(item.stage.startswith("/usr/local/") or
                            item.stage.startswith("/var/lib/porota-rc6-oci-custody/"))
            self.assertTrue(item.stage.endswith(".r297-stage"))
            self.assertNotIn("boca", item.stage.lower())
            self.assertNotIn("watch", item.stage.lower())

    def test_root_bootstrap_plan_forbids_self_pin_or_mutated_sources(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        go = guard.verify_owner_go(owner_comment(), MAIN, 6837530000, now=now)
        source = guard.verify_reviewed_sources(ROOT)
        with self.assertRaises(guard.BootstrapHold):
            guard.build_root_bootstrap_plan(go, dict(source, cli=b"changed"))
        with self.assertRaises(guard.BootstrapHold):
            guard.build_root_bootstrap_plan(dict(go, approved_manifest_sha256="BAD"), source)

    def test_remote_root_command_allowlist_is_fixed_os_verbs_only(self):
        verbs = guard.reviewed_remote_root_commands()
        self.assertTrue(verbs)
        for command in verbs:
            self.assertIn("/usr/bin/sudo -n /usr/bin/", command)
            self.assertNotIn("/usr/bin/sudo -n /usr/bin/python", command)
            self.assertNotIn("sh -c", command)
            self.assertNotIn("/tmp/", command)
            self.assertNotIn("docker", command)
            self.assertNotIn("systemctl", command)
            self.assertNotIn("boca", command.lower())
            self.assertNotIn("ppi", command.lower())
        self.assertTrue(any("/usr/bin/mkdir -m 0700" in x for x in verbs))
        self.assertTrue(any("/usr/bin/install" in x and "/dev/stdin" in x
                            for x in verbs))

    def test_gnu_test_argv_is_valid_and_does_not_use_unsupported_double_dash(self):
        import subprocess
        for command in guard.reviewed_remote_root_commands():
            if "/usr/bin/test " in command:
                self.assertNotIn(" -- ", command)
        self.assertEqual(subprocess.run(["/usr/bin/test", "-e", "/etc/passwd"],
                                        check=False).returncode, 0)
        self.assertEqual(subprocess.run(["/usr/bin/test", "-L", "/etc/passwd"],
                                        check=False).returncode, 1)

    def test_gnu_ln_T_never_follows_directory_symlink_target(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "foreign").mkdir()
            (root / "stage").write_bytes(b"reviewed")
            (root / "target").symlink_to(root / "foreign", target_is_directory=True)
            result = subprocess.run(["/usr/bin/ln", "-T", "--",
                                     str(root / "stage"), str(root / "target")],
                                    capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list((root / "foreign").iterdir()), [])
            self.assertTrue((root / "target").is_symlink())

    def test_sync_is_file_scoped_never_filesystem_wide(self):
        import subprocess
        commands = guard.reviewed_remote_root_commands()
        self.assertTrue(any("/usr/bin/sync -- " in cmd for cmd in commands))
        self.assertFalse(any("/usr/bin/sync -f" in cmd or
                             "/usr/bin/sync --file-system" in cmd for cmd in commands))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "owned"
            path.write_bytes(b"local-fsync-negative")
            self.assertEqual(subprocess.run(["/usr/bin/sync", "--", str(path)],
                                            check=False, capture_output=True).returncode, 0)

    def test_root_transaction_one_shot_reservation_atomic_links_and_pin_last(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        go = guard.verify_owner_go(owner_comment(), MAIN, 6837530000, now=now)
        sources = guard.verify_reviewed_sources(ROOT)
        host = FakeRootHost()
        parsed = guard.validate_context(context())
        authority_calls = []
        def authority():
            authority_calls.append(1)
            return go, MAIN
        report = guard.execute_one_shot_root_bootstrap(host, parsed, go, sources,
                                                       recheck_authority=authority)
        self.assertEqual(report["status"], "INSTALLED_FILES_ONLY_NOT_HOST_GO")
        self.assertFalse(report["host_control_verified"])
        self.assertTrue(len(authority_calls) >= 3)
        self.assertIn(guard.ROOT_LOCK_DIR, host.dirs)
        self.assertIn(guard.ROOT_RECEIPT, host.files)
        self.assertEqual(host.files[guard.ROOT_STAGE_TARGETS[-1][2]][0], b"c" * 64 + b"\n")
        sequence = [command for command, _stdin in host.calls]
        allowed = guard.reviewed_remote_root_commands() | {
            guard.HOST_ID_COMMAND, guard.HOST_REGION_COMMAND,
        }
        self.assertTrue(all(cmd in allowed for cmd in sequence))
        self.assertLess(sequence.index("/usr/bin/sudo -n /usr/bin/mkdir -m 0700 -- " + guard.ROOT_LOCK_DIR),
                        next(i for i, cmd in enumerate(sequence)
                             if "/usr/bin/install -m" in cmd))
        links = [cmd for cmd in sequence if "/usr/bin/ln -T --" in cmd]
        self.assertEqual(len(links), 4)
        self.assertIn("installer-approved-manifest.sha256", links[-2])
        self.assertIn("bootstrap-r297-complete.json", links[-1])
        self.assertFalse(any("/usr/bin/python" in cmd or "docker" in cmd or
                             "boca" in cmd.lower() for cmd in sequence))

    def test_existing_root_lock_blocks_replay_without_writes(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        go = guard.verify_owner_go(owner_comment(), MAIN, 6837530000, now=now)
        host = FakeRootHost()
        host.dirs.add(guard.ROOT_LOCK_DIR)
        parsed = guard.validate_context(context())
        with self.assertRaises(guard.BootstrapHold):
            guard.execute_one_shot_root_bootstrap(host, parsed, go,
                                                  guard.verify_reviewed_sources(ROOT),
                                                  recheck_authority=lambda: (go, MAIN))
        self.assertFalse(any("/usr/bin/install -m" in cmd for cmd, _ in host.calls))

    def test_explicit_recovery_completes_pinned_stage_after_crash_without_lock_removal(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        go = guard.verify_owner_go(owner_comment(), MAIN, 6837530000, now=now)
        original = guard.validate_context(context())
        recovery = guard.validate_context(context(RC6_R295_OWNER_COMMENT_ID="6837530001"))
        resume_go = dict(go, operation="TRUSTED_MAIN_ROOT_BOOTSTRAP_RESUME_FILES_ONLY",
                         original_owner_go_comment_id=original.owner_comment_id,
                         original_trusted_main_sha=original.main_sha)
        sources = guard.verify_reviewed_sources(ROOT)
        host = FakeRootHost()
        host.fail_part = "/usr/bin/ln -T -- /usr/local/sbin/.porota-rc6-host-installer.r297-stage"
        with self.assertRaises(guard.BootstrapHold):
            guard.execute_one_shot_root_bootstrap(
                host, original, go, sources, recheck_authority=lambda: (go, MAIN))
        self.assertIn(guard.ROOT_LOCK_DIR, host.dirs)
        self.assertNotIn(guard.ROOT_RECEIPT, host.files)
        host.fail_part = None
        before_recovery = len(host.calls)
        result = guard.execute_one_shot_root_bootstrap(
            host, recovery, resume_go, sources,
            recheck_authority=lambda: (resume_go, MAIN),
            recovery=True, original_context=original, original_go=go)
        self.assertEqual(result["status"], "INSTALLED_FILES_ONLY_NOT_HOST_GO")
        self.assertIn(guard.ROOT_RECEIPT, host.files)
        self.assertIn(guard.ROOT_LOCK_DIR, host.dirs)
        self.assertFalse(any("/usr/bin/mkdir -m 0700 -- " + guard.ROOT_LOCK_DIR in cmd
                             for cmd, _ in host.calls[before_recovery:]))
        self.assertTrue(all(stage not in host.files
                            for _role, stage, _target, _mode in guard.ROOT_STAGE_TARGETS))
        self.assertEqual(host.files[guard.ROOT_RECEIPT][0],
                         guard._bootstrap_receipt_bytes(original, go))

    def test_recovery_after_hardlink_crash_requires_same_inode_and_exact_hash(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        go = guard.verify_owner_go(owner_comment(), MAIN, 6837530000, now=now)
        original = guard.validate_context(context())
        recovery = guard.validate_context(context(RC6_R295_OWNER_COMMENT_ID="6837530001"))
        resume_go = dict(go, operation="TRUSTED_MAIN_ROOT_BOOTSTRAP_RESUME_FILES_ONLY",
                         original_owner_go_comment_id=original.owner_comment_id,
                         original_trusted_main_sha=original.main_sha)
        sources = guard.verify_reviewed_sources(ROOT)
        host = FakeRootHost()
        stage = guard.ROOT_STAGE_TARGETS[0][1]
        target = guard.ROOT_STAGE_TARGETS[0][2]
        host.fail_part = "/usr/bin/rm -- " + stage
        with self.assertRaises(guard.BootstrapHold):
            guard.execute_one_shot_root_bootstrap(
                host, original, go, sources, recheck_authority=lambda: (go, MAIN))
        self.assertEqual(host.file_inodes[stage], host.file_inodes[target])
        host.fail_part = None
        result = guard.execute_one_shot_root_bootstrap(
            host, recovery, resume_go, sources,
            recheck_authority=lambda: (resume_go, MAIN),
            recovery=True, original_context=original, original_go=go)
        self.assertEqual(result["status"], "INSTALLED_FILES_ONLY_NOT_HOST_GO")
        self.assertNotIn(stage, host.files)
        self.assertIn(target, host.files)

    def test_recovery_finishes_receipt_hardlink_crash_before_reporting_success(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        go = guard.verify_owner_go(owner_comment(), MAIN, 6837530000, now=now)
        original = guard.validate_context(context())
        recovery = guard.validate_context(context(RC6_R295_OWNER_COMMENT_ID="6837530001"))
        resume_go = dict(go, operation="TRUSTED_MAIN_ROOT_BOOTSTRAP_RESUME_FILES_ONLY",
                         original_owner_go_comment_id=original.owner_comment_id,
                         original_trusted_main_sha=original.main_sha)
        sources = guard.verify_reviewed_sources(ROOT)
        host = FakeRootHost()
        host.fail_part = "/usr/bin/rm -- " + guard.ROOT_RECEIPT_STAGE
        with self.assertRaises(guard.BootstrapHold):
            guard.execute_one_shot_root_bootstrap(
                host, original, go, sources, recheck_authority=lambda: (go, MAIN))
        self.assertIn(guard.ROOT_RECEIPT_STAGE, host.files)
        self.assertIn(guard.ROOT_RECEIPT, host.files)
        self.assertEqual(host.file_inodes[guard.ROOT_RECEIPT_STAGE],
                         host.file_inodes[guard.ROOT_RECEIPT])
        host.fail_part = None
        result = guard.execute_one_shot_root_bootstrap(
            host, recovery, resume_go, sources,
            recheck_authority=lambda: (resume_go, MAIN),
            recovery=True, original_context=original, original_go=go)
        self.assertEqual(result["status"], "INSTALLED_FILES_ONLY_NOT_HOST_GO")
        self.assertNotIn(guard.ROOT_RECEIPT_STAGE, host.files)
        self.assertIn(guard.ROOT_RECEIPT, host.files)
        self.assertIn(guard.ROOT_LOCK_DIR, host.dirs)

    def test_recovery_rejects_foreign_hardlink_and_wrong_go_before_mutation(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        go = guard.verify_owner_go(owner_comment(), MAIN, 6837530000, now=now)
        original = guard.validate_context(context())
        recovery = guard.validate_context(context(RC6_R295_OWNER_COMMENT_ID="6837530001"))
        sources = guard.verify_reviewed_sources(ROOT)
        host = FakeRootHost()
        host.dirs.add(guard.ROOT_LOCK_DIR)
        stage, target = guard.ROOT_STAGE_TARGETS[0][1:3]
        host.files[stage] = (sources["cli"], 0o555)
        host.files[target] = (sources["cli"], 0o555)
        host.file_inodes[stage] = 1001
        host.file_inodes[target] = 1002  # same bytes, different inodes
        resume_go = dict(go, operation="TRUSTED_MAIN_ROOT_BOOTSTRAP_RESUME_FILES_ONLY",
                         original_owner_go_comment_id=original.owner_comment_id,
                         original_trusted_main_sha=original.main_sha)
        with self.assertRaises(guard.BootstrapHold):
            guard.execute_one_shot_root_bootstrap(
                host, recovery, resume_go, sources,
                recheck_authority=lambda: (resume_go, MAIN),
                recovery=True, original_context=original, original_go=go)
        self.assertFalse(any("/usr/bin/install -m" in cmd or "/usr/bin/rm --" in cmd
                             for cmd, _payload in host.calls))
        host.calls.clear()
        with self.assertRaises(guard.BootstrapHold):
            guard.execute_one_shot_root_bootstrap(
                host, recovery, go, sources,
                recheck_authority=lambda: (go, MAIN),
                recovery=True, original_context=original, original_go=go)
        self.assertFalse(any("/usr/bin/install -m" in cmd or "/usr/bin/rm --" in cmd
                             for cmd, _payload in host.calls))

    def test_recovery_rejects_unattributed_empty_root_lock(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        go = guard.verify_owner_go(owner_comment(), MAIN, 6837530000, now=now)
        original = guard.validate_context(context())
        recovery = guard.validate_context(context(RC6_R295_OWNER_COMMENT_ID="6837530001"))
        resume_go = dict(go, operation="TRUSTED_MAIN_ROOT_BOOTSTRAP_RESUME_FILES_ONLY",
                         original_owner_go_comment_id=original.owner_comment_id,
                         original_trusted_main_sha=original.main_sha)
        host = FakeRootHost()
        host.dirs.add(guard.ROOT_LOCK_DIR)
        with self.assertRaisesRegex(guard.BootstrapHold,
                                    "ROOT_EMPTY_LOCK_ORIGIN_UNVERIFIED"):
            guard.execute_one_shot_root_bootstrap(
                host, recovery, resume_go, guard.verify_reviewed_sources(ROOT),
                recheck_authority=lambda: (resume_go, MAIN),
                recovery=True, original_context=original, original_go=go)
        self.assertFalse(any("/usr/bin/install -m" in cmd or "/usr/bin/rm --" in cmd
                             for cmd, _payload in host.calls))

    def test_existing_foreign_target_holds_before_lock(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        go = guard.verify_owner_go(owner_comment(), MAIN, 6837530000, now=now)
        host = FakeRootHost()
        target = guard.ROOT_STAGE_TARGETS[0][2]
        host.files[target] = (b"unknown-existing-data", 0o555)
        parsed = guard.validate_context(context())
        with self.assertRaises(guard.BootstrapHold):
            guard.execute_one_shot_root_bootstrap(host, parsed, go,
                                                  guard.verify_reviewed_sources(ROOT),
                                                  recheck_authority=lambda: (go, MAIN))
        self.assertNotIn(guard.ROOT_LOCK_DIR, host.dirs)
        self.assertEqual(host.files[target][0], b"unknown-existing-data")

    def test_main_drift_after_staging_leaves_pending_lock_and_no_pin(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        go = guard.verify_owner_go(owner_comment(), MAIN, 6837530000, now=now)
        host = FakeRootHost()
        parsed = guard.validate_context(context())
        checks = []
        def drift():
            checks.append(1)
            return (go, MAIN if len(checks) < 3 else "f" * 40)
        with self.assertRaises(guard.BootstrapHold):
            guard.execute_one_shot_root_bootstrap(host, parsed, go,
                                                  guard.verify_reviewed_sources(ROOT),
                                                  recheck_authority=drift)
        self.assertIn(guard.ROOT_LOCK_DIR, host.dirs)
        self.assertNotIn(guard.ROOT_STAGE_TARGETS[-1][2], host.files)
        self.assertNotIn(guard.ROOT_RECEIPT, host.files)

    def test_owner_go_expiration_during_preflight_never_creates_root_lock(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        go = guard.verify_owner_go(owner_comment(), MAIN, 6837530000, now=now)
        host = FakeRootHost()
        checks = []

        def expires_after_preflight():
            checks.append(True)
            if len(checks) > 1:
                raise guard.BootstrapHold("OWNER_GO_EXPIRED_OR_FUTURE")
            return go, MAIN

        with self.assertRaisesRegex(guard.BootstrapHold,
                                    "OWNER_GO_EXPIRED_OR_FUTURE"):
            guard.execute_one_shot_root_bootstrap(
                host, guard.validate_context(context()), go,
                guard.verify_reviewed_sources(ROOT),
                recheck_authority=expires_after_preflight)
        self.assertGreaterEqual(len(checks), 2)
        self.assertNotIn(guard.ROOT_LOCK_DIR, host.dirs)
        self.assertFalse(any("/usr/bin/sudo -n /usr/bin/mkdir" in call
                             for call, _payload in host.calls))

    def test_mismatched_do_identity_blocks_before_all_sudo(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        go = guard.verify_owner_go(owner_comment(), MAIN, 6837530000, now=now)
        for name, value in (("host_id", b"999999999\n"),
                            ("host_region", b"sfo3\n")):
            host = FakeRootHost()
            setattr(host, name, value)
            with self.subTest(name=name), self.assertRaises(guard.BootstrapHold):
                guard.execute_one_shot_root_bootstrap(
                    host, guard.validate_context(context()), go,
                    guard.verify_reviewed_sources(ROOT),
                    recheck_authority=lambda: (go, MAIN))
            self.assertFalse(any("/usr/bin/sudo" in cmd for cmd, _ in host.calls))
            self.assertNotIn(guard.ROOT_LOCK_DIR, host.dirs)

    def test_symlink_ancestor_or_stage_denied_before_root_lock(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        go = guard.verify_owner_go(owner_comment(), MAIN, 6837530000, now=now)
        for path in ("/usr/local", guard.ROOT_STAGE_TARGETS[0][1],
                     guard.ROOT_STAGE_TARGETS[0][2]):
            host = FakeRootHost()
            host.symlinks.add(path)
            with self.subTest(path=path), self.assertRaises(guard.BootstrapHold):
                guard.execute_one_shot_root_bootstrap(
                    host, guard.validate_context(context()), go,
                    guard.verify_reviewed_sources(ROOT),
                    recheck_authority=lambda: (go, MAIN))
            self.assertNotIn(guard.ROOT_LOCK_DIR, host.dirs)
            self.assertFalse(any("/usr/bin/install -m" in cmd for cmd, _ in host.calls))

    def test_fsync_failure_after_reservation_leaves_lock_and_blocks_approval(self):
        now = datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc)
        go = guard.verify_owner_go(owner_comment(), MAIN, 6837530000, now=now)
        host = FakeRootHost()
        host.fail_part = "/usr/bin/sync -- /usr/local/sbin/.porota-rc6-host-installer.r297-stage"
        with self.assertRaises(guard.BootstrapHold):
            guard.execute_one_shot_root_bootstrap(
                host, guard.validate_context(context()), go,
                guard.verify_reviewed_sources(ROOT),
                recheck_authority=lambda: (go, MAIN))
        self.assertIn(guard.ROOT_LOCK_DIR, host.dirs)
        self.assertNotIn(guard.ROOT_STAGE_TARGETS[-1][2], host.files)
        self.assertNotIn(guard.ROOT_RECEIPT, host.files)

    def test_ssh_transport_no_config_proxy_tty_or_secret_args_and_exact_stdin(self):
        from unittest import mock
        parsed = guard.validate_context(context())
        master = mock.Mock()
        master.poll.return_value = None
        with mock.patch.object(guard.subprocess, "Popen", return_value=master), \
             mock.patch.object(guard.StrictSSHHost, "_verified_master_socket", return_value=True), \
             mock.patch.object(guard, "run_bounded_ssh_command",
                               return_value=(0, b"123456789\n")) as run:
            with guard.StrictSSHHost(parsed, context()) as host:
                rc, body = host.call(guard.HOST_ID_COMMAND, stdin=b"pinned-bytes")
        self.assertEqual((rc, body), (0, b"123456789\n"))
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "/usr/bin/ssh")
        self.assertIn("-F", argv)
        self.assertIn("/dev/null", argv)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("IdentitiesOnly=yes", argv)
        self.assertIn("ProxyCommand=/usr/bin/false", argv)
        self.assertIn("ControlMaster=auto", argv)
        self.assertIn("-S", argv)
        self.assertEqual(argv[-2:], ["porotaadmin@example.invalid", guard.HOST_ID_COMMAND])
        self.assertEqual(run.call_args.args[1], b"pinned-bytes")
        self.assertEqual(run.call_args.kwargs["timeout_s"], 22)
        self.assertEqual(run.call_args.kwargs["output_cap"], 4096)
        self.assertNotIn("runner-masked-token", " ".join(argv))
        self.assertNotIn("placeholder", " ".join(argv))

    def test_ssh_transport_reuses_one_master_socket_and_closes_it(self):
        from unittest import mock
        parsed = guard.validate_context(context())
        master_processes = []

        class Master:
            terminated = False
            def poll(self): return None if not self.terminated else 0
            def terminate(self): self.terminated = True
            def wait(self, timeout=None): return 0

        def launch(argv, **_kwargs):
            self.assertIn("-M", argv)
            self.assertIn("-N", argv)
            self.assertIn("-S", argv)
            self.assertIn("ProxyCommand=none", argv)
            self.assertNotIn("ProxyCommand=/usr/bin/false", argv)
            master = Master()
            master_processes.append(master)
            return master

        with mock.patch.object(guard.subprocess, "Popen", side_effect=launch), \
             mock.patch.object(guard.StrictSSHHost, "_verified_master_socket", return_value=True), \
             mock.patch.object(guard, "run_bounded_ssh_command",
                               return_value=(0, b"123456789\n")) as run:
            with guard.StrictSSHHost(parsed, context()) as host:
                self.assertEqual(host.call(guard.HOST_ID_COMMAND)[0], 0)
                self.assertEqual(host.call(guard.HOST_REGION_COMMAND)[0], 0)
                args1 = run.call_args_list[0].args[0]
                args2 = run.call_args_list[1].args[0]
                self.assertEqual(args1[args1.index("-S") + 1],
                                 args2[args2.index("-S") + 1])
                self.assertIn("ControlMaster=auto", args1)
                self.assertNotIn("ControlMaster=no", args1)
                self.assertIn("ProxyCommand=/usr/bin/false", args1)
                self.assertNotIn("ProxyCommand=none", args1)
            self.assertEqual(len(master_processes), 1)
            self.assertTrue(master_processes[0].terminated)

    def test_ssh_transaction_global_deadline_denies_new_commands(self):
        import time
        from unittest import mock
        parsed = guard.validate_context(context())
        master = mock.Mock()
        master.poll.return_value = None
        with mock.patch.object(guard.subprocess, "Popen", return_value=master), \
             mock.patch.object(guard.StrictSSHHost, "_verified_master_socket", return_value=True), \
             mock.patch.object(guard, "run_bounded_ssh_command") as run:
            with guard.StrictSSHHost(parsed, context()) as host:
                host.transaction_deadline = time.monotonic() - 1
                with self.assertRaisesRegex(guard.BootstrapHold,
                                            "SSH_TRANSACTION_DEADLINE_EXCEEDED"):
                    host.call(guard.HOST_ID_COMMAND)
                run.assert_not_called()

    def test_ssh_transaction_caps_per_command_by_remaining_global_budget(self):
        import time
        from unittest import mock
        parsed = guard.validate_context(context())
        master = mock.Mock()
        master.poll.return_value = None
        with mock.patch.object(guard.subprocess, "Popen", return_value=master), \
             mock.patch.object(guard.StrictSSHHost, "_verified_master_socket", return_value=True), \
             mock.patch.object(guard, "run_bounded_ssh_command",
                               return_value=(0, b"123456789\n")) as run:
            with guard.StrictSSHHost(parsed, context()) as host:
                host.transaction_deadline = time.monotonic() + 1
                host.call(guard.HOST_ID_COMMAND)
                self.assertLess(run.call_args.kwargs["timeout_s"], 2)
                self.assertGreater(run.call_args.kwargs["timeout_s"], 0)

    def test_real_subprocess_stream_output_is_bounded_before_completion(self):
        import sys
        with self.assertRaisesRegex(guard.BootstrapHold,
                                    "SSH_OUTPUT_SIZE_UNVERIFIED"):
            guard.run_bounded_ssh_command(
                [sys.executable, "-c",
                 "import sys;sys.stdout.buffer.write(b'X'*1048576)"],
                b"", timeout_s=3, output_cap=128)

    def test_real_subprocess_stderr_flood_is_bounded_before_completion(self):
        import sys
        with self.assertRaisesRegex(guard.BootstrapHold,
                                    "SSH_OUTPUT_SIZE_UNVERIFIED"):
            guard.run_bounded_ssh_command(
                [sys.executable, "-c",
                 "import sys;sys.stderr.buffer.write(b'X'*1048576)"],
                b"", timeout_s=3, output_cap=128)

    def test_real_subprocess_stdin_is_exact_and_never_merged_with_stderr(self):
        import sys
        payload = b"reviewed-immutable-bytes-\x00\xff"
        rc, output = guard.run_bounded_ssh_command(
            [sys.executable, "-c",
             "import sys,hashlib;sys.stderr.write('secret stderr');"
             "sys.stdout.write(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())"],
            payload, timeout_s=3, output_cap=128)
        self.assertEqual(rc, 0)
        self.assertEqual(output, hashlib.sha256(payload).hexdigest().encode())

    def test_real_subprocess_hang_fails_with_timeout_without_waiting_for_exit(self):
        import sys
        import time
        began = time.monotonic()
        with self.assertRaisesRegex(guard.BootstrapHold,
                                    "SSH_TRANSPORT_TIMEOUT"):
            guard.run_bounded_ssh_command(
                [sys.executable, "-c", "import time;time.sleep(4)"],
                b"", timeout_s=0.1, output_cap=32)
        self.assertLess(time.monotonic() - began, 2)

    def test_ssh_transport_timeout_terminates_its_process_group(self):
        import sys
        from unittest import mock
        with mock.patch.object(guard.os, "killpg", wraps=guard.os.killpg) as kill_group:
            with self.assertRaisesRegex(guard.BootstrapHold, "SSH_TRANSPORT_TIMEOUT"):
                guard.run_bounded_ssh_command(
                    [sys.executable, "-c", "import time; time.sleep(3)"],
                    b"", timeout_s=0.1, output_cap=32)
        kill_group.assert_called_once()
        self.assertEqual(kill_group.call_args.args[1], guard.signal.SIGKILL)

    def test_actual_main_entry_never_opens_host_without_valid_owner_go(self):
        from contextlib import redirect_stdout
        import io
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = guard.main(["--execute-approved-bootstrap"], env=context(),
                              owner_getter=lambda _context: (_ for _ in ()).throw(
                                  guard.BootstrapHold("OWNER_GO_NOT_PUBLISHED")),
                              main_ref_getter=lambda _context: MAIN,
                              host_factory=lambda *_args: (_ for _ in ()).throw(
                                  AssertionError("host must never open")))
        self.assertEqual(code, 79)
        self.assertIn("OWNER_GO_NOT_PUBLISHED", stream.getvalue())

    def test_actual_main_entry_with_fake_host_only_reports_files_installed(self):
        from contextlib import redirect_stdout
        import io
        parsed = guard.validate_context(context())
        go = guard.verify_owner_go(owner_comment(), MAIN, 6837530000,
                                   now=datetime(2026, 11, 12, 22, 35, tzinfo=timezone.utc))
        host = FakeRootHost()
        stream = io.StringIO()
        class HostContext:
            def __enter__(self): return host
            def __exit__(self, *_exc): return None
        with redirect_stdout(stream):
            code = guard.main(["--execute-approved-bootstrap"], env=context(),
                              owner_getter=lambda _context: go,
                              main_ref_getter=lambda _context: MAIN,
                              host_factory=lambda _ctx, _env: HostContext())
        self.assertEqual(code, 0)
        self.assertIn("FILES_INSTALLED_ONLY_NOT_HOST_GO", stream.getvalue())
        self.assertIn("host_control_verified=NO_VERIFICADO", stream.getvalue())
        self.assertIn(guard.ROOT_RECEIPT, host.files)

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

    def test_workflow_is_dispatch_only_and_never_claims_host_go(self):
        source = (ROOT / ".github/workflows/rc6-r295-host-bootstrap-trusted.yml").read_text()
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("group: rc6-unified-paper-deploy", source)
        self.assertIn("cancel-in-progress: false", source)
        self.assertIn("refs/heads/main", source)
        self.assertIn("issues: read", source)
        self.assertIn("contents: read", source)
        self.assertIn("never imply host_control_verified", source)
        self.assertIn("--check-offhost", source)
        self.assertNotIn("ssh -", source)
        self.assertNotIn("scp ", source)
        self.assertNotIn("sudo -", source)
        self.assertNotIn("docker ", source)
        self.assertNotIn("systemctl", source)
        self.assertNotIn("--execute-bootstrap", source)

    def test_workflow_has_explicit_default_offhost_and_separate_physical_secrets(self):
        import yaml
        source = (ROOT / ".github/workflows/rc6-r295-host-bootstrap-trusted.yml").read_text()
        document = yaml.safe_load(source)
        event = document.get("on", document.get(True))
        self.assertEqual(set(event), {"workflow_dispatch"})
        action = event["workflow_dispatch"]["inputs"]["bootstrap_action"]
        self.assertEqual(action["default"], "offhost")
        self.assertEqual(action["options"], ["offhost", "install_files", "resume_files"])
        jobs = document["jobs"]
        offhost = jobs["owner_go_offhost_evidence_only"]
        physical = jobs["approved_root_files_only"]
        self.assertIn("inputs.bootstrap_action == 'offhost'", offhost["if"])
        self.assertIn("inputs.bootstrap_action == 'install_files'", physical["if"])
        self.assertIn("inputs.bootstrap_action == 'resume_files'", physical["if"])
        self.assertNotIn("inputs.bootstrap_action != 'offhost'", physical["if"])
        for key in ("DO_SSH_KEY", "DO_KNOWN_HOSTS", "DO_HOST", "DO_USER",
                    "RC6_EXPECTED_DO_ID", "RC6_EXPECTED_DO_REGION"):
            self.assertNotIn(key, offhost["env"])
            self.assertIn(key, physical["env"])
        physical_script = "\n".join(step.get("run", "")
                                      for step in physical["steps"])
        self.assertIn("--execute-approved-bootstrap", physical_script)
        self.assertIn("--resume-approved-bootstrap", physical_script)
        self.assertIn("install_files)", physical_script)
        self.assertIn("resume_files)", physical_script)
        self.assertEqual(document["concurrency"]["cancel-in-progress"], False)

    def test_offhost_check_stays_hold79_even_with_a_context(self):
        from contextlib import redirect_stdout
        import io
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = guard.main(["--check-offhost"], env=context(),
                              owner_getter=lambda _context: (_ for _ in ()).throw(
                                  guard.BootstrapHold("OWNER_GO_NOT_PUBLISHED")),
                              main_ref_getter=lambda _context: MAIN)
        self.assertEqual(code, 79)
        self.assertIn("host_control_verified=NO_VERIFICADO", stream.getvalue())
        self.assertIn("deploy_authorized=NO", stream.getvalue())

    def test_offhost_verification_is_conditional_but_never_host_go(self):
        from contextlib import redirect_stdout
        import io
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = guard.main(["--check-offhost"], env=context(),
                              owner_getter=lambda _context: {"host_control_verified": False,
                                                             "approved_manifest_sha256": "c" * 64},
                              main_ref_getter=lambda _context: MAIN)
        self.assertEqual(code, 0)
        self.assertIn("OWNER_GO_OFFHOST_VERIFIED", stream.getvalue())
        self.assertIn("host_control_verified=NO_VERIFICADO", stream.getvalue())

    def test_dispatch_main_drift_always_hold79_even_with_valid_owner_go(self):
        from contextlib import redirect_stdout
        import io
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = guard.main(["--check-offhost"], env=context(),
                              owner_getter=lambda _context: {"host_control_verified": False,
                                                             "approved_manifest_sha256": "c" * 64},
                              main_ref_getter=lambda _context: "b" * 40)
        self.assertEqual(code, 79)
        self.assertIn("MAIN_SHA_DRIFT", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
