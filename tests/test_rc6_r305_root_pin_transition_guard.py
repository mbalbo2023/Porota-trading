"""R320 reconstruction of R305: focused, offline, fail-closed regression tests.

No SSH, sudo, Docker or runtime operations; only an in-memory host.
The historical R305 131/131 result is not inherited by this new implementation.
"""

import hashlib
import importlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import MappingProxyType

from scripts import rc6_r302_bootstrap_receipt_preflight as r302
from scripts import rc6_host_rootowned_installer_contract as r280


MAIN_SHA = "a6eeb7228563e56e4d0b8531162e56c6168c6720"
MAIN_TREE = "98582646939c994d29c5fd6563d2dd6ee693a8f8"
CANDIDATE_SHA = "4fe14abddc4693948c8081840f4ffa60e0f6c8a0"
CANDIDATE_TREE = "9871d69ff3570dce3ff3203941f7bc615d1ce7d8"
UTC = timezone.utc
NOW = datetime(2026, 11, 13, 13, 50, tzinfo=UTC)


def _guard():
    try:
        return importlib.import_module("scripts.rc6_r305_root_pin_transition_guard")
    except ModuleNotFoundError as exc:
        raise AssertionError("R305 root pin guard source is missing") from exc


def canonical(doc):
    return (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def manifest():
    return canonical({
        "schema": "RC6_ROOT_INSTALL_MANIFEST_V1",
        "candidate_sha": CANDIDATE_SHA, "candidate_tree": CANDIDATE_TREE,
        "paper_mode": "PRODUCTION_PAPER", "real_orders_allowed": False,
        "files": {name: digest(name.encode()) for name in (
            "helper", "custody", "profiles", "unit_template", "approval")},
    })


def witness(raw):
    return canonical({
        "schema": "RC6_R305_ROOT_MANIFEST_INDEPENDENT_DIGEST_WITNESS_V1",
        "run_id": "43350000100", "run_attempt": "1",
        "trusted_main_sha": MAIN_SHA, "trusted_main_tree": MAIN_TREE,
        "manifest_schema": "RC6_ROOT_INSTALL_MANIFEST_V1",
        "manifest_sha256": digest(raw),
        "candidate_sha": CANDIDATE_SHA, "candidate_tree": CANDIDATE_TREE,
        "verification_result": "PASS",
        "paper_mode": "PRODUCTION_PAPER", "real_orders_allowed": False,
    })


def context():
    guard = _guard()
    m, w = manifest(), witness(manifest())
    return guard.PinContext(
        main_sha=MAIN_SHA, main_tree=MAIN_TREE,
        candidate_sha=CANDIDATE_SHA, candidate_tree=CANDIDATE_TREE,
        manifest_sha256=digest(m), witness_artifact_sha256=digest(w),
        do_id="123456789", do_region="nyc1",
    )


def go_comment(*, operation="NEW", now=NOW, comment_id=601, original_id=None,
               manifest_sha=None, witness_sha=None):
    m, w = manifest(), witness(manifest())
    doc = {
        "operation": "RC6_ROOT_PIN_" + operation,
        "issue": 1093, "trusted_main_sha": MAIN_SHA,
        "candidate_sha": CANDIDATE_SHA, "candidate_tree": CANDIDATE_TREE,
        "manifest_sha256": digest(m) if manifest_sha is None else manifest_sha,
        "witness_artifact_sha256": digest(w) if witness_sha is None else witness_sha,
        "witness_run_id": "43350000100", "witness_run_attempt": "1",
        "paper_mode": "PRODUCTION_PAPER", "real_orders_allowed": False,
        "host_control_verified": False,
        "host_identity_source": "PROTECTED_DO_PINS",
        "expires_at": (now + timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if original_id is not None:
        doc["original_owner_go_comment_id"] = original_id
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": comment_id, "issue_url":
        "https://api.github.com/repos/mbalbo2023/Porota-trading/issues/1093",
        "user": {"login": "mbalbo2023"}, "author_association": "OWNER",
        "created_at": stamp, "updated_at": stamp,
        "body": "RC6_R305_ROOT_PIN_GO_V1=" + json.dumps(doc, sort_keys=True),
    }


class OfflineRootPinTests(unittest.TestCase):
    def test_adapter_allowlist_is_bounded_and_no_arbitrary_root_shell(self):
        """A new generic shell/root or Docker invocation must fail this guard."""
        guard = _guard()
        commands = guard.reviewed_root_commands()
        self.assertIs(type(commands), frozenset)
        self.assertTrue(0 < len(commands) <= 512)
        self.assertTrue(all(type(c) is str and len(c) <= 512 and
                            c.startswith("/usr/bin/sudo -n /usr/bin/")
                            for c in commands))
        self.assertFalse(any("docker" in c or "systemctl" in c or
                             "sh -c" in c for c in commands))
        installer = (
            "/usr/bin/sudo -n /usr/bin/python3 -I "
            "/usr/local/sbin/porota-rc6-host-installer install-approved"
        )
        self.assertIn(installer, commands)
        self.assertEqual([c for c in commands if "/usr/bin/python3 " in c],
                         [installer])
        for name in ("HOST_ID_COMMAND", "HOST_REGION_COMMAND"):
            self.assertTrue(getattr(guard, name).startswith(
                "/usr/bin/curl -fsS --connect-timeout 3 --max-time 5 "
                "http://169.254.169.254/metadata/v1/"))

    def test_manifest_digests_are_external_and_release_D_never_root(self):
        """Using historical D digest or unpinned malformed ROOT must HOLD79."""
        guard = _guard()
        raw = manifest()
        self.assertEqual(guard.validate_root_manifest(
            raw, expected_digest=digest(raw))["candidate_sha"], CANDIDATE_SHA)
        with self.assertRaises(guard.PinHold):
            guard.validate_root_manifest(raw, expected_digest="f" * 64)
        with self.assertRaises(guard.PinHold):
            guard.validate_root_manifest(b"x" * 101203,
                                         expected_digest=digest(b"x" * 101203))

    def test_root_manifest_is_strict_schema_and_canonical(self):
        """Extra role or duplicate JSON key must not pass manifest validation."""
        guard = _guard()
        doc = json.loads(manifest())
        doc["files"]["arbitrary"] = "a" * 64
        raw = canonical(doc)
        with self.assertRaises(guard.PinHold):
            guard.validate_root_manifest(raw, expected_digest=digest(raw))
        raw = manifest().replace(b'"schema":', b'"schema":"FAKE","schema":', 1)
        with self.assertRaises(guard.PinHold):
            guard.validate_root_manifest(raw, expected_digest=digest(raw))

    def test_witness_must_match_authenticated_external_digest_and_candidate(self):
        """A guessed PASS JSON or a different candidate cannot authorize pin."""
        guard = _guard()
        raw, original = manifest(), witness(manifest())
        self.assertEqual(guard.validate_independent_witness(
            original, expected_digest=digest(original),
            manifest=guard.validate_root_manifest(
                raw, expected_digest=digest(raw)), context=context())["run_id"],
            "43350000100")
        with self.assertRaises(guard.PinHold):
            guard.validate_independent_witness(
                original, expected_digest="0" * 64,
                manifest=guard.validate_root_manifest(
                    raw, expected_digest=digest(raw)), context=context())
        forged = original.replace(CANDIDATE_SHA.encode(), b"0" * 40)
        with self.assertRaises(guard.PinHold):
            guard.validate_independent_witness(
                forged, expected_digest=digest(forged),
                manifest=guard.validate_root_manifest(
                    raw, expected_digest=digest(raw)), context=context())

    def test_owner_go_requires_fresh_immutable_comment_and_exact_authority(self):
        """Untrusted, edited, expired, or unbound owner approvals must HOLD79."""
        guard = _guard()
        comment = go_comment()
        validated = guard.verify_owner_go(comment, context(), owner_go_id=601,
                                          operation="NEW", now=NOW)
        self.assertEqual(validated["manifest_sha256"], digest(manifest()))
        broken = {**comment, "updated_at": "2026-11-13T13:50:01Z"}
        with self.assertRaises(guard.PinHold):
            guard.verify_owner_go(broken, context(), owner_go_id=601,
                                  operation="NEW", now=NOW)
        with self.assertRaises(guard.PinHold):
            guard.verify_owner_go(comment, context(), owner_go_id=601,
                                  operation="NEW", now=NOW + timedelta(hours=1))
        with self.assertRaises(guard.PinHold):
            guard.verify_owner_go(go_comment(manifest_sha="0" * 64),
                                  context(), owner_go_id=601,
                                  operation="NEW", now=NOW)

    def test_resumption_requires_distinct_fresh_go_bound_to_original(self):
        """A reused NEW approval cannot silently resume an interrupted lock."""
        guard = _guard()
        original = go_comment()
        resume = go_comment(operation="RESUME",
                            now=NOW + timedelta(minutes=8),
                            comment_id=602, original_id=601)
        actual = guard.verify_owner_go(resume, context(), owner_go_id=602,
                                       operation="RESUME",
                                       now=NOW + timedelta(minutes=8),
                                       original_go_comment=original)
        self.assertEqual(actual["original_owner_go_comment_id"], 601)
        with self.assertRaises(guard.PinHold):
            guard.verify_owner_go(original, context(), owner_go_id=601,
                                  operation="RESUME", now=NOW)

    def test_bootstrap_receipt_hash_is_frozen_not_inferred_from_host(self):
        """Unexpected R301 receipt must be rejected, not treated as host GO."""
        guard = _guard()
        self.assertEqual(guard.R301_RECEIPT_SHA256,
                         digest(r302._expected_receipt_bytes()))
        self.assertEqual(guard.HISTORICAL_D_PIN,
                         r302.APPROVED_R301_D_MANIFEST_SHA256)

    def test_protected_droplet_identity_cannot_leak_through_context_repr(self):
        """Python traceback/diagnostics must not disclose low-entropy DO pins."""
        ctx = context()
        self.assertNotIn(ctx.do_id, repr(ctx))
        self.assertNotIn(ctx.do_region, repr(ctx))

    def test_six_role_staging_grammar_is_fixed_immutable_and_root_only(self):
        """An invented role/path or incomplete append step cannot be trusted."""
        guard = _guard()
        self.assertTrue(hasattr(guard, "reviewed_stage_commands"),
                        "R321 six-role staging command grammar is missing")
        stage = guard.reviewed_stage_commands()
        self.assertIs(type(stage), MappingProxyType)
        self.assertEqual(set(stage), set(r280.SOURCE_NAMES) | {"manifest"})
        allowed = guard.reviewed_root_commands()
        for role, verbs in stage.items():
            self.assertIs(type(verbs), MappingProxyType)
            self.assertEqual(set(verbs), {
                "create", "append", "stage_stat", "stage_hash", "link",
                "remove_stage", "final_stat", "final_hash",
                "sync", "final_sync",
            })
            for command in verbs.values():
                self.assertIn(command, allowed)
                self.assertTrue(command.startswith(
                    "/usr/bin/sudo -n /usr/bin/"))
                self.assertLessEqual(len(command), 512)
            final_name = "manifest.json" if role == "manifest" else \
                         r280.SOURCE_NAMES[role]
            self.assertTrue(verbs["final_stat"].endswith(
                f"/install-inbox/{final_name}"))
            self.assertIn("bs=8192", verbs["append"])
            self.assertIn("oflag=append", verbs["append"])
            self.assertIn("conv=notrunc", verbs["append"])
            self.assertIn("status=none", verbs["append"])
            self.assertIn("-m 0400", verbs["create"])
        for name in ("STAGE_LOCK_CREATE", "STAGE_INBOX_CREATE",
                     "STAGE_LOCK_STAT", "STAGE_INBOX_STAT", "STAGE_LOCK_SYNC",
                     "STAGE_INBOX_SYNC"):
            self.assertIn(getattr(guard, name), allowed)
        with self.assertRaises(TypeError):
            stage["helper"]["append"] = "untrusted"
        with self.assertRaises(TypeError):
            stage["arbitrary"] = {}


class FakeRootHost:
    """Tiny root-owned inode simulation; no process or external endpoint."""

    def __init__(self):
        g = _guard()
        self.files = {}
        self.dirs = {}
        self.symlinks = set()
        self.calls = []
        self.next_inode = 1000
        self.fail_match = None
        self.fail_once = True
        self.identity = (b"123456789\n", b"nyc1\n")
        for directory in g._DIRECTORIES:
            if directory != g.ROOT_LOCK:
                self._directory(directory, 0o700 if directory in
                                (g.R301_LOCK, g.ROOT_DIRECTORY) else 0o755)
        self._file(g.ROOT_PIN, (g.HISTORICAL_D_PIN + "\n").encode("ascii"), 0o400)
        self._file(g.R301_RECEIPT, r302._expected_receipt_bytes(), 0o400)

    def _inode(self):
        self.next_inode += 1
        return self.next_inode

    def _directory(self, path, mode):
        self.dirs[path] = (mode, self._inode())

    def _file(self, path, payload, mode, inode=None):
        self.files[path] = (payload, mode, self._inode() if inode is None else inode)

    def stat(self, path):
        if path in self.files:
            payload, mode, inode = self.files[path]
            links = sum(v[2] == inode for v in self.files.values())
            return f"0|0|{mode:o}|{links}|regular file|2049|{inode}\n".encode()
        if path in self.dirs:
            mode, inode = self.dirs[path]
            return f"0|0|{mode:o}|2|directory|2049|{inode}\n".encode()
        return None

    def call(self, cmd, *, stdin=None, max_output_bytes=256, timeout_s=15):
        g = _guard()
        self.calls.append((cmd, stdin, max_output_bytes, timeout_s))
        if self.fail_match is not None and self.fail_match in cmd:
            if self.fail_once:
                self.fail_match = None
            return (1, b"")
        if cmd == g.HOST_ID_COMMAND:
            return (0, self.identity[0])
        if cmd == g.HOST_REGION_COMMAND:
            return (0, self.identity[1])
        if cmd not in g.reviewed_root_commands():
            raise AssertionError("command escaped reviewed R305 allowlist")
        parts = cmd.split()
        if "/usr/bin/test -L " in cmd:
            rc, output = (0 if parts[-1] in self.symlinks else 1), b""
        elif "/usr/bin/test -e " in cmd:
            rc, output = (0 if self.stat(parts[-1]) is not None else 1), b""
        elif "/usr/bin/stat " in cmd:
            output = self.stat(parts[-1])
            rc = 0 if output is not None else 1
            output = output or b""
        elif "/usr/bin/sha256sum " in cmd:
            path = parts[-1]
            if path not in self.files:
                rc, output = 1, b""
            else:
                rc, output = 0, (digest(self.files[path][0]) + "  " +
                                 path + "\n").encode("ascii")
        elif "/usr/bin/mkdir " in cmd:
            path = parts[-1]
            if self.stat(path) is not None:
                rc, output = 1, b""
            else:
                self._directory(path, 0o700)
                rc, output = 0, b""
        elif "/usr/bin/install " in cmd:
            path = parts[-1]
            if self.stat(path) is not None or type(stdin) is not bytes or not stdin:
                rc, output = 1, b""
            else:
                self._file(path, stdin, 0o400)
                rc, output = 0, b""
        elif "/usr/bin/ln -T " in cmd:
            source, dest = parts[-2:]
            if source not in self.files or self.stat(dest) is not None:
                rc, output = 1, b""
            else:
                raw, mode, inode = self.files[source]
                self._file(dest, raw, mode, inode=inode)
                rc, output = 0, b""
        elif "/usr/bin/mv -T " in cmd:
            source, dest = parts[-2:]
            if source not in self.files:
                rc, output = 1, b""
            else:
                self.files[dest] = self.files.pop(source)
                rc, output = 0, b""
        elif "/usr/bin/rm " in cmd:
            path = parts[-1]
            if path not in self.files:
                rc, output = 1, b""
            else:
                del self.files[path]
                rc, output = 0, b""
        elif "/usr/bin/sync " in cmd:
            rc, output = 0, b""
        else:
            raise AssertionError("unexpected reviewed command " + cmd)
        if len(output) > max_output_bytes:
            raise AssertionError("R309 output-cap contract violated")
        if stdin is not None and len(stdin) > 8192:
            raise AssertionError("R309 stdin-cap contract violated")
        return rc, output


def execute(host, *, operation="NEW", comment=None, original=None,
            go_id=601, now=NOW, recheck=None):
    g = _guard()
    assert hasattr(g, "execute_root_pin_transition"), (
        "R305 transactional root pin entrypoint is missing")
    comment = comment or go_comment()
    if recheck is None:
        recheck = (lambda: (comment, MAIN_SHA, digest(witness(manifest())),
                            original) if operation == "RESUME"
                   else (comment, MAIN_SHA, digest(witness(manifest()))))
    return g.execute_root_pin_transition(
        host, context(), manifest_raw=manifest(),
        witness_raw=witness(manifest()), owner_comment=comment,
        owner_go_id=go_id, operation=operation, now=now,
        original_go_comment=original, recheck_authority=recheck,
        clock=lambda: now,
    )


class RootPinTransactionTests(unittest.TestCase):
    def test_new_switch_is_root0400_atomic_and_files_only(self):
        """Replacing pin without archive or receipt-last breaks recovery."""
        g = _guard()
        host = FakeRootHost()
        result = execute(host)
        self.assertEqual(result["status"], "PIN_TRANSITION_FILES_ONLY_NOT_HOST_GO")
        self.assertFalse(result["host_control_verified"])
        self.assertEqual(host.files[g.ROOT_PIN][0],
                         (digest(manifest()) + "\n").encode("ascii"))
        self.assertEqual(host.files[g.ROOT_PIN][1], 0o400)
        self.assertEqual(host.files[g.ROOT_OLD_ARCHIVE][0],
                         (g.HISTORICAL_D_PIN + "\n").encode("ascii"))
        self.assertEqual(host.files[g.ROOT_OLD_ARCHIVE][1], 0o400)
        self.assertEqual(host.dirs[g.ROOT_LOCK][0], 0o700)
        self.assertIn(g.ROOT_TRANSITION_RECEIPT, host.files)
        self.assertFalse(any(path in host.files
                             for path in (g.ROOT_STAGE,
                                          g.ROOT_TRANSITION_RECEIPT_STAGE)))
        commands = [cmd for cmd, *_ in host.calls]
        self.assertLess(commands.index(
            f"/usr/bin/sudo -n /usr/bin/mv -T -- {g.ROOT_STAGE} {g.ROOT_PIN}"),
            commands.index(
                f"/usr/bin/sudo -n /usr/bin/ln -T -- "
                f"{g.ROOT_TRANSITION_RECEIPT_STAGE} {g.ROOT_TRANSITION_RECEIPT}"))
        self.assertTrue(all(stdin is None or len(stdin) <= 8192
                            for _, stdin, *_ in host.calls))

    def test_missing_witness_rechecks_or_wrong_DO_id_prevent_first_mutation(self):
        """A forged input or wrong physical host never reaches mkdir lock."""
        g = _guard()
        for kwargs in (
            {"recheck": lambda: (go_comment(), "0" * 40, digest(witness(manifest())))},
            {"recheck": lambda: (go_comment(), MAIN_SHA, "0" * 64)},
        ):
            host = FakeRootHost()
            with self.assertRaises(g.PinHold):
                execute(host, **kwargs)
            self.assertNotIn(g.ROOT_LOCK, host.dirs)
        host = FakeRootHost()
        host.identity = (b"987654321\n", b"nyc1\n")
        with self.assertRaises(g.PinHold):
            execute(host)
        self.assertNotIn(g.ROOT_LOCK, host.dirs)

    def test_root_pin_mismatch_symlink_or_foreign_owner_blocks_any_write(self):
        """Only exact R301 historical inode/mode/hash may be replaced."""
        g = _guard()
        for issue in ("wrong", "symlink", "mode", "receipt"):
            host = FakeRootHost()
            data, mode, ino = host.files[g.ROOT_PIN]
            if issue == "wrong":
                host.files[g.ROOT_PIN] = (b"f" * 65, mode, ino)
            elif issue == "symlink":
                host.symlinks.add(g.ROOT_PIN)
            elif issue == "mode":
                host.files[g.ROOT_PIN] = (data, 0o644, ino)
            else:
                data, mode, ino = host.files[g.R301_RECEIPT]
                host.files[g.R301_RECEIPT] = (b"wrong", mode, ino)
            with self.assertRaises(g.PinHold, msg=issue):
                execute(host)
            self.assertNotIn(g.ROOT_LOCK, host.dirs)

    def test_crash_after_lock_does_not_allow_second_new(self):
        """A second NEW may not remove or silently reuse the durable lock."""
        g = _guard()
        host = FakeRootHost()
        host.fail_match = "/usr/bin/ln -T"
        with self.assertRaises(g.PinHold):
            execute(host)
        self.assertIn(g.ROOT_LOCK, host.dirs)
        calls_before = len(host.calls)
        with self.assertRaises(g.PinHold):
            execute(host)
        self.assertNotIn(g.ROOT_OLD_ARCHIVE, host.files)
        self.assertGreater(len(host.calls), calls_before)

    def test_resume_after_stage_crash_requires_new_GO_original_identity(self):
        """Only new RESUME + authenticated original can finish fixed source."""
        g = _guard()
        host = FakeRootHost()
        host.fail_match = "/usr/bin/mv -T"
        with self.assertRaises(g.PinHold):
            execute(host)
        self.assertIn(g.ROOT_STAGE, host.files)
        resume = go_comment(operation="RESUME", now=NOW + timedelta(minutes=8),
                            comment_id=602, original_id=601)
        result = execute(host, operation="RESUME", comment=resume,
                         original=go_comment(), go_id=602,
                         now=NOW + timedelta(minutes=8))
        self.assertEqual(result["status"], "PIN_TRANSITION_FILES_ONLY_NOT_HOST_GO")
        self.assertEqual(host.files[g.ROOT_PIN][0],
                         (digest(manifest()) + "\n").encode("ascii"))

    def test_resume_after_swap_does_not_replace_pin_second_time(self):
        """A crash after atomic mv resumes receipt only, never re-swaps."""
        g = _guard()
        host = FakeRootHost()
        host.fail_match = "/usr/bin/install -m 0400 -o 0 -g 0 /dev/stdin " + \
                          g.ROOT_TRANSITION_RECEIPT_STAGE
        with self.assertRaises(g.PinHold):
            execute(host)
        new_inode = host.files[g.ROOT_PIN][2]
        resume = go_comment(operation="RESUME", now=NOW + timedelta(minutes=8),
                            comment_id=602, original_id=601)
        result = execute(host, operation="RESUME", comment=resume,
                         original=go_comment(), go_id=602,
                         now=NOW + timedelta(minutes=8))
        self.assertEqual(result["status"], "PIN_TRANSITION_FILES_ONLY_NOT_HOST_GO")
        self.assertEqual(host.files[g.ROOT_PIN][2], new_inode)
        self.assertIn(g.ROOT_TRANSITION_RECEIPT, host.files)

    def test_bare_lock_and_foreign_archive_cannot_be_recovered_automatically(self):
        """Unattributed lock has no evidence of first GO; require forensics."""
        g = _guard()
        for foreign in (False, True):
            host = FakeRootHost()
            host._directory(g.ROOT_LOCK, 0o700)
            if foreign:
                host._file(g.ROOT_OLD_ARCHIVE, b"foreign", 0o400)
            resume = go_comment(operation="RESUME",
                                now=NOW + timedelta(minutes=8),
                                comment_id=602, original_id=601)
            with self.assertRaises(g.PinHold):
                execute(host, operation="RESUME", comment=resume,
                        original=go_comment(), go_id=602,
                        now=NOW + timedelta(minutes=8))
            self.assertNotIn(g.ROOT_TRANSITION_RECEIPT, host.files)

    def test_resume_authenticates_original_go_again_at_GH_recheck(self):
        """A fabricated local original is not sufficient for RESUME authority."""
        g = _guard()
        host = FakeRootHost()
        host.fail_match = "/usr/bin/mv -T"
        with self.assertRaises(g.PinHold):
            execute(host)
        resume = go_comment(operation="RESUME",
                            now=NOW + timedelta(minutes=8),
                            comment_id=602, original_id=601)
        # This recheck does NOT return the authenticated original comment.
        insufficient = lambda: (resume, MAIN_SHA, digest(witness(manifest())))
        with self.assertRaises(g.PinHold):
            execute(host, operation="RESUME", comment=resume,
                    original=go_comment(), go_id=602,
                    now=NOW + timedelta(minutes=8),
                    recheck=insufficient)


if __name__ == "__main__":
    unittest.main()
