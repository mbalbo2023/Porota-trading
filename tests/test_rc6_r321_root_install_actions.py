"""R321 finite ROOT-stage planning only; no SSH, sudo or host changes."""

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
import unittest
import yaml
from unittest.mock import patch

from scripts import rc6_r321_root_install_actions as r321
from scripts import rc6_host_rootowned_installer_contract as r280
from scripts import rc6_host_rootowned_installer_cli as r280_cli


NOW = datetime(2026, 11, 13, 14, 0, tzinfo=timezone.utc)
CANDIDATE = "a" * 40
TREE = "b" * 40
MAIN = "f" * 40
MAIN_TREE = "0" * 40
WITNESS_SHA = "1" * 64
RELEASE_D_DIGEST = "sha256:" + "2" * 64
RELEASE_E_DIGEST = "sha256:" + "4" * 64
SHA256 = lambda raw: hashlib.sha256(raw).hexdigest()


def canonical(data):
    return (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()


def fixture(*, large=True):
    sources = {
        "helper": b"#!/usr/bin/python3 -I\n" + b"H" * (17000 if large else 10),
        "custody": b"C" * (18000 if large else 10),
        "profiles": b'{"paper_mode":"PRODUCTION_PAPER"}\n',
        "unit_template": b"[Service]\nExecStart=/usr/bin/false\n",
    }
    approval = {
        "schema_version": 1, "phase": "GENESIS",
        "candidate_sha": CANDIDATE, "candidate_tree": TREE,
        "artifact_digest": "sha256:" + "c" * 64,
        "image_id": "sha256:" + "d" * 64,
        "daemon_id": "engine-28.0.4", "generation": "e" * 32,
        "run_id": "43301355473", "run_attempt": "1",
        "paper_mode": "PRODUCTION_PAPER", "real_orders_allowed": False,
        "helper_sha256": SHA256(sources["helper"]),
        "custody_sha256": SHA256(sources["custody"]),
        "profiles_sha256": SHA256(sources["profiles"]),
        "unit_template_sha256": SHA256(sources["unit_template"]),
        "expires_at": "2026-11-13T16:00:00Z",
    }
    sources["approval"] = canonical(approval)
    manifest = {
        "schema": "RC6_ROOT_INSTALL_MANIFEST_V1", "candidate_sha": CANDIDATE,
        "candidate_tree": TREE, "paper_mode": "PRODUCTION_PAPER",
        "real_orders_allowed": False,
        "files": {key: SHA256(data) for key, data in sources.items()},
    }
    manifest_raw = canonical(manifest)
    return manifest_raw, sources


def install_go_fixture(*, created=NOW, expires=None):
    _manifest, sources = fixture(large=False)
    approval = json.loads(sources["approval"])
    approval["artifact_digest"] = RELEASE_D_DIGEST
    sources["approval"] = canonical(approval)
    manifest = canonical({
        "schema": "RC6_ROOT_INSTALL_MANIFEST_V1", "candidate_sha": CANDIDATE,
        "candidate_tree": TREE, "paper_mode": "PRODUCTION_PAPER",
        "real_orders_allowed": False,
        "files": {role: SHA256(raw) for role, raw in sources.items()},
    })
    context = r321.RootInstallContext(
        main_sha=MAIN, main_tree=MAIN_TREE,
        candidate_sha=CANDIDATE, candidate_tree=TREE,
        manifest_sha256=SHA256(manifest),
        witness_artifact_sha256=WITNESS_SHA,
        do_id="1029384756", do_region="nyc3",
    )
    receipt = {
        "schema_version": 1, "status": "RELEASE_BYTES_VERIFIED_POSTCOMPLETION",
        "repository": "mbalbo2023/Porota-trading", "candidate_sha": CANDIDATE,
        "candidate_tree_sha": TREE, "publisher_main_sha": MAIN,
        "publisher_workflow_path": ".github/workflows/rc6-r251-trusted-release-build.yml",
        "publisher_run_id": "43301355473", "publisher_run_attempt": "1",
        "release_artifact_id": 123456,
        "release_artifact_name": "rc6-r251-release-43301355473-1-" + CANDIDATE,
        "release_artifact_digest": RELEASE_D_DIGEST,
        "release_artifact_size_bytes": 20480, "file_count": 80,
        "total_size_bytes": 90000, "release_manifest_verified": True,
        "r144_release_go": False, "runtime_verified": False,
        "real_orders_physical_verified": False,
    }
    receipt_raw = canonical(receipt)
    witness = {"run_id": "43306221122", "run_attempt": "1"}
    if expires is None:
        expires = created + timedelta(hours=2)
    payload = {
        "operation": "RC6_ROOT_INSTALL_NEW_FILES_ONLY",
        "issue": 1093, "trusted_main_sha": MAIN,
        "trusted_main_tree": MAIN_TREE,
        "candidate_sha": CANDIDATE, "candidate_tree": TREE,
        "root_manifest_sha256": SHA256(manifest),
        "witness_artifact_sha256": WITNESS_SHA,
        "witness_run_id": witness["run_id"],
        "witness_run_attempt": witness["run_attempt"],
        "release_e_run_id": "43304509783", "release_e_run_attempt": "1",
        "release_e_artifact_id": 78910,
        "release_e_artifact_digest": RELEASE_E_DIGEST,
        "release_e_receipt_sha256": SHA256(receipt_raw),
        "release_d_run_id": receipt["publisher_run_id"],
        "release_d_run_attempt": receipt["publisher_run_attempt"],
        "release_d_artifact_id": receipt["release_artifact_id"],
        "release_d_artifact_digest": RELEASE_D_DIGEST,
        "generation": approval["generation"],
        "r305_pin_owner_go_id": 123,
        "paper_mode": "PRODUCTION_PAPER", "real_orders_allowed": False,
        "host_control_verified": False,
        "host_identity_source": "PROTECTED_DO_PINS",
        "nonce": "c" * 32,
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
    }
    comment = {
        "id": 456, "issue_url": (
            "https://api.github.com/repos/mbalbo2023/Porota-trading/issues/1093"),
        "user": {"login": "mbalbo2023"}, "author_association": "OWNER",
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "updated_at": created.isoformat().replace("+00:00", "Z"),
        "body": "RC6_R321_ROOT_INSTALL_GO_V1=" + canonical(payload).decode(),
    }
    return context, manifest, sources, receipt_raw, witness, approval, comment


class FakeR309Host:
    """In-memory finite OS-command transport: never SSH, sudo or root."""

    def __init__(self, commands, *, pin=None):
        self.allowed = set(commands)
        self.files = {}
        self.directories = {}
        self.symlinks = set()
        self.commands = []
        self.mutations = []
        self.next_inode = 20
        self.pin_sha = pin
        self.pin_transitions = 0
        self.install_count = 0
        self.identity_checks = 0
        self.staged_receipt = None
        self.on_append = None
        self.on_create = None
        self.pin_corrupt = False

    def call(self, command, *, stdin=None, max_output_bytes=256, timeout_s=15):
        if command not in self.allowed:
            raise RuntimeError("not a reviewed command")
        if stdin is not None and (type(stdin) is not bytes or len(stdin) > 8192):
            raise RuntimeError("invalid R309 stdin budget")
        if timeout_s > 15:
            raise RuntimeError("invalid R309 timeout")
        self.commands.append(command)
        status, output = self._run(command, stdin)
        if len(output) > max_output_bytes:
            raise RuntimeError("bounded SSH output would overflow")
        return status, output

    def _run(self, command, stdin):
        if command == r321.INSTALLER_COMMAND:
            self.mutations.append("installer")
            self.install_count += 1
            return 0, self.staged_receipt or b""
        if "/usr/bin/test -L " in command:
            return (0 if command.split()[-1] in self.symlinks else 1), b""
        if "/usr/bin/test -e " in command:
            path = command.split()[-1]
            return (0 if path in self.files or path in self.directories or
                    path in self.symlinks else 1), b""
        if "/usr/bin/mkdir -m 0700 -- " in command:
            path = command.split("-- ", 1)[1]
            if path in self.directories or path in self.symlinks:
                return 1, b""
            self.next_inode += 1
            self.directories[path] = self.next_inode
            self.mutations.append("mkdir:" + path)
            return 0, b""
        if "/usr/bin/stat -c " in command:
            path = command.split("-- ", 1)[1]
            if path in self.directories:
                output = f"0|0|700|2|directory|1|{self.directories[path]}|4096\n"
                return 0, output.encode()
            if path not in self.files or path in self.symlinks:
                return 1, b""
            node = self.files[path]
            links = sum(other is node for other in self.files.values())
            output = f"0|0|400|{links}|regular file|1|{node['inode']}|{len(node['data'])}\n"
            return 0, output.encode()
        if "/usr/bin/sha256sum -- " in command:
            path = command.split("-- ", 1)[1]
            if path not in self.files:
                return 1, b""
            return 0, (SHA256(bytes(self.files[path]["data"])) +
                       "  " + path + "\n").encode()
        if "/usr/bin/install -m 0400 -o 0 -g 0 /dev/null " in command:
            path = command.rsplit(" ", 1)[-1]
            if path in self.files or path in self.symlinks:
                return 1, b""
            self.next_inode += 1
            self.files[path] = {"inode": self.next_inode, "data": bytearray()}
            self.mutations.append("create:" + path)
            if callable(self.on_create):
                self.on_create(self, path)
            return 0, b""
        if "/usr/bin/dd if=/dev/stdin of=" in command:
            path = command.split(" of=", 1)[1].split(" ", 1)[0]
            if path not in self.files:
                return 1, b""
            self.files[path]["data"].extend(stdin or b"")
            self.mutations.append("append:" + path)
            if callable(self.on_append):
                self.on_append(self, path)
            return 0, b""
        if "/usr/bin/ln -T -- " in command:
            stage, final = command.split("-- ", 1)[1].split(" ")
            if stage not in self.files or final in self.files or final in self.symlinks:
                return 1, b""
            self.files[final] = self.files[stage]
            self.mutations.append("link:" + final)
            return 0, b""
        if "/usr/bin/rm -- " in command:
            stage = command.split("-- ", 1)[1]
            if stage not in self.files:
                return 1, b""
            del self.files[stage]
            self.mutations.append("rm:" + stage)
            return 0, b""
        if "/usr/bin/sync -- " in command:
            self.mutations.append("sync")
            return 0, b""
        raise RuntimeError("unsupported fake command")


def fake_r305_with_host():
    inbox = r280.INBOX_DIR
    lock = "/var/lib/.porota-rc6-r321-root-stage-reserved"
    stage = {}
    for role in r321.STAGE_ORDER:
        target = r280.SOURCE_NAMES.get(role, "manifest.json")
        source = f"{inbox}/.r321-{role}.stage"
        final = f"{inbox}/{target}"
        stage[role] = MappingProxyType({
            "stage_test_l": f"/usr/bin/sudo -n /usr/bin/test -L {source}",
            "stage_test_e": f"/usr/bin/sudo -n /usr/bin/test -e {source}",
            "final_test_l": f"/usr/bin/sudo -n /usr/bin/test -L {final}",
            "final_test_e": f"/usr/bin/sudo -n /usr/bin/test -e {final}",
            "create": f"/usr/bin/sudo -n /usr/bin/install -m 0400 -o 0 -g 0 /dev/null {source}",
            "append": f"/usr/bin/sudo -n /usr/bin/dd if=/dev/stdin of={source} bs=8192 oflag=append conv=notrunc status=none",
            "stage_stat": f"/usr/bin/sudo -n /usr/bin/stat -c '%u|%g|%a|%h|%F|%d|%i|%s' -- {source}",
            "stage_hash": f"/usr/bin/sudo -n /usr/bin/sha256sum -- {source}",
            "link": f"/usr/bin/sudo -n /usr/bin/ln -T -- {source} {final}",
            "remove_stage": f"/usr/bin/sudo -n /usr/bin/rm -- {source}",
            "final_stat": f"/usr/bin/sudo -n /usr/bin/stat -c '%u|%g|%a|%h|%F|%d|%i|%s' -- {final}",
            "final_hash": f"/usr/bin/sudo -n /usr/bin/sha256sum -- {final}",
            "sync": f"/usr/bin/sudo -n /usr/bin/sync -- {source}",
            "final_sync": f"/usr/bin/sudo -n /usr/bin/sync -- {final}",
        })
    constants = dict(
        STAGE_LOCK_CREATE=f"/usr/bin/sudo -n /usr/bin/mkdir -m 0700 -- {lock}",
        STAGE_INBOX_CREATE=f"/usr/bin/sudo -n /usr/bin/mkdir -m 0700 -- {inbox}",
        STAGE_LOCK_STAT=f"/usr/bin/sudo -n /usr/bin/stat -c '%u|%g|%a|%h|%F|%d|%i|%s' -- {lock}",
        STAGE_INBOX_STAT=f"/usr/bin/sudo -n /usr/bin/stat -c '%u|%g|%a|%h|%F|%d|%i|%s' -- {inbox}",
        STAGE_LOCK_TEST_L=f"/usr/bin/sudo -n /usr/bin/test -L {lock}",
        STAGE_LOCK_TEST_E=f"/usr/bin/sudo -n /usr/bin/test -e {lock}",
        STAGE_INBOX_TEST_L=f"/usr/bin/sudo -n /usr/bin/test -L {inbox}",
        STAGE_INBOX_TEST_E=f"/usr/bin/sudo -n /usr/bin/test -e {inbox}",
        STAGE_LOCK_SYNC=f"/usr/bin/sudo -n /usr/bin/sync -- {lock}",
        STAGE_INBOX_SYNC=f"/usr/bin/sudo -n /usr/bin/sync -- {inbox}",
        INSTALL_APPROVED_COMMAND=r321.INSTALLER_COMMAND,
    )
    whitelist = frozenset({r321.INSTALLER_COMMAND} |
                          set(constants.values()) |
                          {cmd for each in stage.values() for cmd in each.values()})

    def pin_transition(host, context, **_kwargs):
        host.pin_sha = ("0" * 64 if host.pin_corrupt else context.manifest_sha256)
        host.pin_transitions += 1
        return {"status": "PIN_TRANSITION_FILES_ONLY_NOT_HOST_GO"}

    def pin_verify(_comment, _context, **kwargs):
        if kwargs.get("owner_go_id") != 123 or kwargs.get("operation") != "NEW":
            raise RuntimeError("wrong pin GO")
        return {"operation": "RC6_ROOT_PIN_NEW"}

    return SimpleNamespace(
        **constants, PinContext=r321.RootInstallContext,
        OWNER_GO_MARKER="RC6_R305_ROOT_PIN_GO_V1=",
        reviewed_stage_commands=lambda: MappingProxyType(stage),
        reviewed_root_commands=lambda: whitelist,
        _identity=lambda host, _context: setattr(
            host, "identity_checks", host.identity_checks + 1),
        _preflight_dirs=lambda host: None,
        _bootstrap_receipt=lambda host: None,
        _snapshot_pin=lambda host, pin, **_kwargs: (
            None if host.pin_sha == pin else (_ for _ in ()).throw(
                RuntimeError("ROOT_PIN_CHANGED"))),
        validate_root_manifest=lambda raw, **_kwargs: json.loads(raw),
        validate_independent_witness=lambda _raw, **_kwargs: {
            "run_id": "43306221122", "run_attempt": "1"},
        verify_owner_go=pin_verify,
        execute_root_pin_transition=pin_transition,
    )


def simulated_install():
    (context, manifest, sources, receipt_raw, witness,
     approval, owner_comment) = install_go_fixture()
    r305 = fake_r305_with_host()
    host = FakeR309Host(r305.reviewed_root_commands())
    host.staged_receipt = canonical({
        "schema": "RC6_ROOT_INSTALL_CODE_ONLY_V1",
        "status": "INSTALLED_FILES_ONLY_NOT_HOST_GO",
        "manifest_sha256": context.manifest_sha256,
        "candidate_sha": context.candidate_sha,
        "host_control_verified": False,
    })
    pin_comment = {"id": 123, "body": "RC6_R305_ROOT_PIN_GO_V1={}\n"}
    def fresh_authority():
        return {
            "owner_comment": owner_comment,
            "main_sha": context.main_sha,
            "candidate_tree": context.candidate_tree,
            "witness_artifact_sha256": context.witness_artifact_sha256,
            "release_e_run_id": "43304509783",
            "release_e_run_attempt": "1",
            "release_e_artifact_id": 78910,
            "release_e_artifact_digest": RELEASE_E_DIGEST,
            "release_e_receipt_sha256": SHA256(receipt_raw),
            "release_d_artifact_id": 123456,
            "release_d_artifact_digest": RELEASE_D_DIGEST,
        }
    kwargs = dict(
        manifest_raw=manifest, source_bytes=sources, witness_raw=b"simulated-witness",
        owner_comment=owner_comment, owner_go_id=456,
        pin_owner_comment=pin_comment, pin_owner_go_id=123,
        release_e_receipt_raw=receipt_raw, release_e_run_id="43304509783",
        release_e_run_attempt="1", release_e_artifact_id=78910,
        release_e_artifact_digest=RELEASE_E_DIGEST,
        recheck_install_authority=fresh_authority,
        recheck_pin_authority=lambda: (pin_comment, context.main_sha,
                                       context.witness_artifact_sha256),
        clock=lambda: NOW + timedelta(minutes=1),
    )
    def imports(name):
        if name == "scripts.rc6_r305_root_pin_transition_guard":
            return r305
        if name == "scripts.rc6_r309_bounded_mux_host":
            return SimpleNamespace(R309BoundedMuxHost=FakeR309Host)
        raise ImportError(name)
    return host, r305, context, kwargs, imports


class R321RootInstallActionsTest(unittest.TestCase):
    def test_simulated_six_role_stage_pin_then_fixed_r280_install_files_only(self):
        host, _r305, context, kwargs, imports = simulated_install()
        with patch.object(r321, "import_module", side_effect=imports):
            result = r321.execute_root_install_files_only(host, context, **kwargs)
        self.assertEqual(result["status"], "ROOT_INSTALLED_FILES_ONLY_NOT_HOST_GO")
        self.assertFalse(result["host_control_verified"])
        self.assertFalse(result["real_orders_allowed"])
        self.assertEqual(host.pin_transitions, 1)
        self.assertEqual(host.install_count, 1)
        self.assertGreaterEqual(host.identity_checks, 8)
        self.assertEqual(sum(v.startswith("link:") for v in host.mutations), 6)
        self.assertEqual(sum(v.startswith("append:") for v in host.mutations), 6)
        self.assertEqual(host.commands[-1], r321.INSTALLER_COMMAND)
        for role in r321.STAGE_ORDER:
            path = r280.INBOX_DIR + "/" + r280.SOURCE_NAMES.get(role, "manifest.json")
            self.assertIn(path, host.files)
            self.assertEqual(sum(other is host.files[path]
                                 for other in host.files.values()), 1)

    def test_staging_rejects_root_lock_collisions_dangling_symlinks_and_partial_files(self):
        for conflict in ("lock", "stage-link", "final-link", "partial-stage"):
            with self.subTest(conflict=conflict):
                host, r305, context, kwargs, imports = simulated_install()
                stage_path = r280.INBOX_DIR + "/.r321-helper.stage"
                final_path = r280.INBOX_DIR + "/helper.bin"
                if conflict == "lock":
                    host.directories[r305.STAGE_LOCK_CREATE.split("-- ", 1)[1]] = 42
                elif conflict == "stage-link":
                    host.symlinks.add(stage_path)
                elif conflict == "final-link":
                    host.symlinks.add(final_path)
                elif conflict == "partial-stage":
                    host.files[stage_path] = {"inode": 44, "data": bytearray(b"partial")}
                with patch.object(r321, "import_module", side_effect=imports):
                    with self.assertRaises(r321.R321Hold):
                        r321.execute_root_install_files_only(host, context, **kwargs)
                self.assertEqual(host.pin_transitions, 0)
                self.assertEqual(host.install_count, 0)
                self.assertFalse(host.mutations)

    def test_mutated_authority_at_next_file_boundary_never_executes_installer(self):
        host, _r305, context, kwargs, imports = simulated_install()
        original = kwargs["recheck_install_authority"]
        calls = [0]
        def revoked():
            calls[0] += 1
            proof = original()
            if calls[0] >= 4:
                proof["main_sha"] = "0" * 40
            return proof
        kwargs["recheck_install_authority"] = revoked
        with patch.object(r321, "import_module", side_effect=imports):
            with self.assertRaises(r321.R321Hold):
                r321.execute_root_install_files_only(host, context, **kwargs)
        self.assertGreater(calls[0], 2)
        self.assertEqual(host.pin_transitions, 0)
        self.assertEqual(host.install_count, 0)
        self.assertTrue(any(x.startswith("create:") for x in host.mutations))

    def test_pin_go_cannot_substitute_install_go_or_install_receipt(self):
        host, _r305, context, kwargs, imports = simulated_install()
        kwargs["owner_comment"] = kwargs["pin_owner_comment"]
        with patch.object(r321, "import_module", side_effect=imports):
            with self.assertRaises(r321.R321Hold):
                r321.execute_root_install_files_only(host, context, **kwargs)
        self.assertFalse(host.mutations)

        host, _r305, context, kwargs, imports = simulated_install()
        host.staged_receipt = host.staged_receipt[:256]
        with patch.object(r321, "import_module", side_effect=imports):
            with self.assertRaises(r321.R321Hold):
                r321.execute_root_install_files_only(host, context, **kwargs)
        self.assertEqual(host.pin_transitions, 1)
        self.assertEqual(host.install_count, 1)

    def test_install_go_pin_comment_id_must_equal_actual_separate_pin_go(self):
        host, _r305, context, kwargs, imports = simulated_install()
        original = kwargs["recheck_install_authority"]
        forged = dict(kwargs["owner_comment"])
        body = json.loads(forged["body"].split("=", 1)[1])
        body["r305_pin_owner_go_id"] = 999  # Genuine PIN GO still id 123.
        forged["body"] = "RC6_R321_ROOT_INSTALL_GO_V1=" + canonical(body).decode()
        kwargs["owner_comment"] = forged
        def current():
            proof = original()
            proof["owner_comment"] = forged
            return proof
        kwargs["recheck_install_authority"] = current
        with patch.object(r321, "import_module", side_effect=imports):
            with self.assertRaises(r321.R321Hold) as caught:
                r321.execute_root_install_files_only(host, context, **kwargs)
        self.assertEqual(caught.exception.reason, "R305_PIN_GO_ID_MISMATCH")
        self.assertFalse(host.mutations)
        self.assertEqual(host.pin_transitions, 0)
        self.assertEqual(host.install_count, 0)

    def test_expiry_mid_chunks_holds_before_final_file_link(self):
        host, _r305, context, kwargs, imports = simulated_install()
        current = [NOW + timedelta(minutes=1)]
        host.on_append = lambda _host, _path: current.__setitem__(
            0, NOW + timedelta(hours=2))
        kwargs["clock"] = lambda: current[0]
        with patch.object(r321, "import_module", side_effect=imports):
            with self.assertRaises(r321.R321Hold) as caught:
                r321.execute_root_install_files_only(host, context, **kwargs)
        self.assertEqual(caught.exception.reason,
                         "ROOT_INSTALL_GO_EXPIRED_OR_TOO_SHORT")
        self.assertTrue(any(x.startswith("append:") for x in host.mutations))
        self.assertFalse(any(x.startswith("link:") for x in host.mutations))
        self.assertEqual(host.install_count, 0)

    def test_extra_stage_hardlink_and_pin_drift_fail_closed(self):
        host, _r305, context, kwargs, imports = simulated_install()
        def alias_created(fake, path):
            fake.files[path + ".foreign-alias"] = fake.files[path]
            fake.on_create = None
        host.on_create = alias_created
        with patch.object(r321, "import_module", side_effect=imports):
            with self.assertRaises(r321.R321Hold) as caught:
                r321.execute_root_install_files_only(host, context, **kwargs)
        self.assertEqual(caught.exception.reason,
                         "ROOT_STAGE_STAT_INODE_MODE_SIZE_INVALID")
        self.assertEqual(host.install_count, 0)

        host, _r305, context, kwargs, imports = simulated_install()
        host.pin_corrupt = True
        with patch.object(r321, "import_module", side_effect=imports):
            with self.assertRaises(r321.R321Hold) as caught:
                r321.execute_root_install_files_only(host, context, **kwargs)
        self.assertEqual(caught.exception.reason, "R305_ROOT_PIN_TRANSITION_HOLD")
        self.assertEqual(host.pin_transitions, 1)
        self.assertEqual(host.install_count, 0)

    def test_revocation_after_pin_and_successful_attempt_never_auto_retry(self):
        host, _r305, context, kwargs, imports = simulated_install()
        original = kwargs["recheck_install_authority"]
        def revoked_before_cli():
            proof = original()
            if host.pin_transitions:
                proof["main_sha"] = "0" * 40
            return proof
        kwargs["recheck_install_authority"] = revoked_before_cli
        with patch.object(r321, "import_module", side_effect=imports):
            with self.assertRaises(r321.R321Hold):
                r321.execute_root_install_files_only(host, context, **kwargs)
        self.assertEqual(host.pin_transitions, 1)
        self.assertEqual(host.install_count, 0)

        host, _r305, context, kwargs, imports = simulated_install()
        with patch.object(r321, "import_module", side_effect=imports):
            r321.execute_root_install_files_only(host, context, **kwargs)
            with self.assertRaises(r321.R321Hold):
                r321.execute_root_install_files_only(host, context, **kwargs)
        self.assertEqual(host.pin_transitions, 1)
        self.assertEqual(host.install_count, 1)

    def test_exact_r280_installer_receipt_is_261_bytes_and_must_be_fully_checked(self):
        receipt = canonical({
            "schema": "RC6_ROOT_INSTALL_CODE_ONLY_V1",
            "status": "INSTALLED_FILES_ONLY_NOT_HOST_GO",
            "manifest_sha256": "e" * 64,
            "candidate_sha": CANDIDATE,
            "host_control_verified": False,
        })
        self.assertEqual(len(receipt), 261)
        self.assertEqual(r321.INSTALLER_RECEIPT_CAP, 512)
        self.assertEqual(r321.validate_installer_receipt(
            receipt, expected_manifest_sha256="e" * 64,
            candidate_sha=CANDIDATE), r280_cli.validate_installer_receipt(
                json.loads(receipt), "e" * 64))
        with self.assertRaises(r321.R321Hold):
            r321.validate_installer_receipt(
                receipt[:256], expected_manifest_sha256="e" * 64,
                candidate_sha=CANDIDATE)

    def test_separate_install_go_binds_owner_witness_release_e_generation_and_paper(self):
        (context, manifest, sources, receipt_raw, witness,
         approval, owner_comment) = install_go_fixture()
        approved = r321.verify_install_owner_go(
            owner_comment, context, expected_comment_id=456,
            witness=witness, release_e_receipt_raw=receipt_raw,
            release_e_run_id="43304509783", release_e_run_attempt="1",
            release_e_artifact_id=78910, release_e_artifact_digest=RELEASE_E_DIGEST,
            approval=approval, now=NOW + timedelta(minutes=1))
        self.assertEqual(approved["operation"], "RC6_ROOT_INSTALL_NEW_FILES_ONLY")
        self.assertEqual(approved["root_manifest_sha256"], SHA256(manifest))
        self.assertEqual(approved["release_e_receipt_sha256"], SHA256(receipt_raw))
        self.assertEqual(approved["generation"], approval["generation"])
        self.assertEqual(approved["host_identity_source"], "PROTECTED_DO_PINS")
        self.assertFalse(approved["real_orders_allowed"])
        self.assertNotIn(context.do_id, json.dumps(approved))

    def test_install_go_is_not_pin_go_and_rejects_missing_stale_or_forged_owner(self):
        (context, _manifest, _sources, receipt_raw, witness,
         approval, owner_comment) = install_go_fixture()

        def verify(comment, **changes):
            return r321.verify_install_owner_go(
                comment, context, expected_comment_id=456,
                witness=witness, release_e_receipt_raw=receipt_raw,
                release_e_run_id="43304509783", release_e_run_attempt="1",
                release_e_artifact_id=78910, release_e_artifact_digest=RELEASE_E_DIGEST,
                approval=approval, now=NOW + timedelta(minutes=1), **changes)

        with self.assertRaises(r321.R321Hold):
            verify(None)
        for mutated in (
            dict(owner_comment, user={"login": "other"}),
            dict(owner_comment, updated_at="2026-11-13T14:01:00Z"),
            dict(owner_comment, body=owner_comment["body"].replace(
                "RC6_R321_ROOT_INSTALL_GO_V1", "RC6_R305_ROOT_PIN_GO_V1")),
            dict(owner_comment, author_association="CONTRIBUTOR"),
            dict(owner_comment, issue_url="https://api.github.com/repos/x/y/issues/1"),
        ):
            with self.subTest(mutated=mutated), self.assertRaises(r321.R321Hold):
                verify(mutated)

        stale = install_go_fixture(created=NOW - timedelta(hours=3),
                                   expires=NOW - timedelta(hours=1))[-1]
        with self.assertRaises(r321.R321Hold):
            verify(stale)
        short = install_go_fixture(created=NOW,
                                   expires=NOW + timedelta(minutes=3))[-1]
        with self.assertRaises(r321.R321Hold):
            verify(short)

    def test_install_go_rejects_self_asserted_release_or_altered_manifest_fields(self):
        (context, _manifest, _sources, receipt_raw, witness,
         approval, owner_comment) = install_go_fixture()
        parameters = dict(
            expected_comment_id=456, witness=witness,
            release_e_receipt_raw=receipt_raw,
            release_e_run_id="43304509783", release_e_run_attempt="1",
            release_e_artifact_id=78910, release_e_artifact_digest=RELEASE_E_DIGEST,
            approval=approval, now=NOW + timedelta(minutes=1),
        )
        for key, altered_value in (
            ("release_e_receipt_sha256", "0" * 64),
            ("release_e_artifact_id", 1),
            ("release_e_artifact_digest", "sha256:" + "0" * 64),
            ("release_d_artifact_digest", "sha256:" + "0" * 64),
            ("release_d_artifact_id", 1),
            ("witness_artifact_sha256", "0" * 64),
            ("root_manifest_sha256", "0" * 64),
            ("generation", "0" * 32),
            ("real_orders_allowed", True),
            ("nonce", "z" * 32),
        ):
            with self.subTest(key=key):
                bad = dict(owner_comment)
                body = json.loads(bad["body"].split("=", 1)[1])
                body[key] = altered_value
                bad["body"] = "RC6_R321_ROOT_INSTALL_GO_V1=" + canonical(body).decode()
                with self.assertRaises(r321.R321Hold):
                    r321.verify_install_owner_go(bad, context, **parameters)
        with self.assertRaises(r321.R321Hold):
            r321.verify_install_owner_go(
                owner_comment, context, **dict(parameters, release_e_run_id="444"))

    def test_install_go_extreme_clock_bounds_return_hold79_not_overflow(self):
        for issued, expiry, current in (
            (datetime(1, 1, 1, tzinfo=timezone.utc),
             datetime(1, 1, 1, 1, tzinfo=timezone.utc),
             datetime(1, 1, 1, tzinfo=timezone.utc)),
            (datetime(9999, 12, 31, 22, tzinfo=timezone.utc),
             datetime(9999, 12, 31, 23, tzinfo=timezone.utc),
             datetime(9999, 12, 31, 22, tzinfo=timezone.utc)),
        ):
            with self.subTest(issued=issued):
                (context, _manifest, _sources, receipt_raw, witness,
                 approval, comment) = install_go_fixture(created=issued,
                                                         expires=expiry)
                try:
                    r321.verify_install_owner_go(
                        comment, context, expected_comment_id=456,
                        witness=witness, release_e_receipt_raw=receipt_raw,
                        release_e_run_id="43304509783", release_e_run_attempt="1",
                        release_e_artifact_id=78910,
                        release_e_artifact_digest=RELEASE_E_DIGEST,
                        approval=approval, now=current)
                except r321.R321Hold:
                    pass
                except OverflowError:
                    self.fail("R321 leaked raw datetime overflow instead of HOLD79")

    def test_trusted_main_action_is_only_offhost_and_never_mutates_droplet(self):
        root = Path(__file__).resolve().parents[1]
        workflow = yaml.load(
            (root / ".github/workflows/rc6-r321-root-install-approved.yml").read_text(),
            Loader=yaml.BaseLoader)
        self.assertIn("workflow_dispatch", workflow["on"])
        self.assertEqual(workflow["concurrency"]["group"],
                         "rc6-unified-paper-deploy")
        self.assertEqual(workflow["concurrency"]["cancel-in-progress"], "false")
        self.assertEqual(workflow["permissions"],
                         {"contents": "read", "issues": "read"})
        self.assertEqual(set(workflow["jobs"]), {"root_install_offhost_preflight"})
        job = workflow["jobs"]["root_install_offhost_preflight"]
        self.assertIn("github.ref == 'refs/heads/main'", job["if"])
        self.assertIn("github.actor == 'mbalbo2023'", job["if"])
        self.assertIn("github.triggering_actor == 'mbalbo2023'", job["if"])
        checkout = job["steps"][0]
        self.assertEqual(checkout["with"]["persist-credentials"], "false")
        self.assertEqual(checkout["with"]["ref"], "${{ github.sha }}")
        commands = "\n".join(str(step.get("run", "")) for step in job["steps"])
        self.assertIn("--check-offhost", commands)
        for forbidden in ("sudo ", "ssh ", "docker ", "install-approved", "--install-approved"):
            self.assertNotIn(forbidden, commands)
        self.assertFalse(job.get("secrets"))

    def test_valid_root_bundle_plan_chunks_all_five_roles_and_manifest_below_8192(self):
        manifest, sources = fixture()
        planned = r321.build_staging_plan(
            manifest, sources, expected_manifest_sha256=SHA256(manifest), now=NOW)
        self.assertEqual(planned.state, "PROPUESTO")
        self.assertFalse(planned.host_control_verified)
        self.assertFalse(planned.executable)
        self.assertEqual(planned.candidate_sha, CANDIDATE)
        self.assertEqual(planned.candidate_tree, TREE)
        self.assertEqual(planned.manifest_sha256, SHA256(manifest))
        self.assertEqual(planned.total_bytes, len(manifest) + sum(map(len, sources.values())))
        self.assertEqual(
            tuple(dict.fromkeys(chunk.role for chunk in planned.chunks)),
            ("helper", "custody", "profiles", "approval", "unit_template", "manifest"))
        self.assertTrue(all(0 < len(chunk.data) <= 8192 for chunk in planned.chunks))
        self.assertEqual(sum(len(chunk.data) for chunk in planned.chunks), planned.total_bytes)
        self.assertEqual(
            b"".join(chunk.data for chunk in planned.chunks if chunk.role == "helper"),
            sources["helper"])
        self.assertEqual(
            b"".join(chunk.data for chunk in planned.chunks if chunk.role == "custody"),
            sources["custody"])
        self.assertEqual(planned.chunks[-1].role, "manifest")

    def test_order_source_bytes_do_not_authorize_their_own_digest(self):
        manifest, sources = fixture(large=False)
        with self.assertRaises(r321.R321Hold) as caught:
            r321.build_staging_plan(manifest, sources,
                                    expected_manifest_sha256="f" * 64, now=NOW)
        self.assertEqual(caught.exception.reason, "INDEPENDENT_ROOT_PIN_MISMATCH")

    def test_modified_role_is_rejected_before_staging_plan(self):
        manifest, sources = fixture(large=False)
        sources["custody"] += b"changed"
        with self.assertRaises(r321.R321Hold) as caught:
            r321.build_staging_plan(manifest, sources,
                                    expected_manifest_sha256=SHA256(manifest), now=NOW)
        self.assertEqual(caught.exception.reason, "ROOT_SOURCE_HASH_MISMATCH")

    def test_source_mutated_after_approval_does_not_change_frozen_staged_bytes(self):
        manifest, sources = fixture(large=False)
        original = r280._validate_approval

        def concurrent_source_change(*args, **kwargs):
            result = original(*args, **kwargs)
            sources["custody"] = b"ATTACKER_CHANGED_SOURCE_AFTER_VALIDATION"
            return result

        with patch.object(r280, "_validate_approval",
                          side_effect=concurrent_source_change):
            plan = r321.build_staging_plan(
                manifest, sources, expected_manifest_sha256=SHA256(manifest), now=NOW)
        staged = b"".join(chunk.data for chunk in plan.chunks
                          if chunk.role == "custody")
        self.assertEqual(staged, b"C" * 10)
        self.assertNotEqual(staged, sources["custody"])
        self.assertEqual(SHA256(staged), json.loads(manifest)["files"]["custody"])

    def test_roles_are_exact_and_no_unknown_paths_are_planned(self):
        manifest, sources = fixture(large=False)
        for bad in (dict(sources, new_file=b"bad"),
                    {key: value for key, value in sources.items() if key != "unit_template"}):
            with self.subTest(roles=set(bad)), self.assertRaises(r321.R321Hold):
                r321.build_staging_plan(manifest, bad,
                                        expected_manifest_sha256=SHA256(manifest), now=NOW)

    def test_release_d_schema_is_never_a_root_install_manifest(self):
        _manifest, sources = fixture(large=False)
        legacy = canonical({"schema": "RC6_RELEASE_MANIFEST_D_V1",
                            "files": {key: SHA256(data) for key, data in sources.items()}})
        with self.assertRaises(r321.R321Hold) as caught:
            r321.build_staging_plan(legacy, sources,
                                    expected_manifest_sha256=SHA256(legacy), now=NOW)
        self.assertEqual(caught.exception.reason, "ROOT_MANIFEST_SCHEMA")

    def test_paper_false_or_approval_expired_is_rejected(self):
        manifest, sources = fixture(large=False)
        bad_manifest = json.loads(manifest)
        bad_manifest["real_orders_allowed"] = True
        altered = canonical(bad_manifest)
        with self.assertRaises(r321.R321Hold):
            r321.build_staging_plan(altered, sources,
                                    expected_manifest_sha256=SHA256(altered), now=NOW)
        with self.assertRaises(r321.R321Hold) as caught:
            r321.build_staging_plan(manifest, sources,
                                    expected_manifest_sha256=SHA256(manifest),
                                    now=datetime(2026, 11, 13, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(caught.exception.reason, "ROOT_APPROVAL_R280_REJECTED")

    def test_overflow_from_r280_approval_expiry_is_normalized_to_hold79(self):
        _manifest, sources = fixture(large=False)
        approval = json.loads(sources["approval"])
        approval["expires_at"] = "9999-12-31T23:30:00Z"
        sources["approval"] = canonical(approval)
        manifest = canonical({
            "schema": "RC6_ROOT_INSTALL_MANIFEST_V1", "candidate_sha": CANDIDATE,
            "candidate_tree": TREE, "paper_mode": "PRODUCTION_PAPER",
            "real_orders_allowed": False,
            "files": {role: SHA256(raw) for role, raw in sources.items()},
        })
        with self.assertRaises(r321.R321Hold) as caught:
            r321.build_staging_plan(
                manifest, sources,
                expected_manifest_sha256=SHA256(manifest),
                now=datetime(9999, 12, 31, 23, 0, tzinfo=timezone.utc))
        self.assertEqual(caught.exception.reason, "ROOT_APPROVAL_R280_REJECTED")

    def test_stage_size_budget_blocks_large_total_without_host(self):
        manifest, sources = fixture(large=False)
        sources["custody"] = b"C" * (512 * 1024)
        with self.assertRaises(r321.R321Hold) as caught:
            r321.build_staging_plan(manifest, sources,
                                    expected_manifest_sha256=SHA256(manifest), now=NOW)
        self.assertEqual(caught.exception.reason, "ROOT_STAGE_TOTAL_BUDGET")

    def test_only_exact_trusted_main_owner_dispatch_is_eligible(self):
        context = {
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REPOSITORY": "mbalbo2023/Porota-trading",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_ACTOR": "mbalbo2023",
            "GITHUB_TRIGGERING_ACTOR": "mbalbo2023",
            "GITHUB_SHA": "a" * 40,
        }
        self.assertEqual(r321.validate_dispatch_context(context), "a" * 40)
        for name, value in (("GITHUB_EVENT_NAME", "pull_request"),
                            ("GITHUB_REF", "refs/heads/deploy/rc6-pr69-isolated-20260915"),
                            ("GITHUB_ACTOR", "fork-user"),
                            ("GITHUB_TRIGGERING_ACTOR", "fork-user"),
                            ("GITHUB_REPOSITORY", "some-other/repo"),
                            ("GITHUB_SHA", "a" * 39)):
            bad = dict(context, **{name: value})
            with self.subTest(name=name), self.assertRaises(r321.R321Hold):
                r321.validate_dispatch_context(bad)

    def test_literal_installer_is_accepted_only_from_reviewed_r305_allowlist(self):
        with self.assertRaises(r321.R321Hold):
            r321.require_reviewed_installer_command()
        reviewed = SimpleNamespace(
            reviewed_root_commands=lambda: frozenset({r321.INSTALLER_COMMAND}))
        with patch.object(r321, "import_module", return_value=reviewed):
            self.assertEqual(r321.require_reviewed_installer_command(),
                             r321.INSTALLER_COMMAND)
        unreviewed = SimpleNamespace(reviewed_root_commands=lambda: frozenset())
        with patch.object(r321, "import_module", return_value=unreviewed):
            with self.assertRaises(r321.R321Hold):
                r321.require_reviewed_installer_command()
        malformed = SimpleNamespace(reviewed_root_commands=lambda: {r321.INSTALLER_COMMAND})
        with patch.object(r321, "import_module", return_value=malformed):
            with self.assertRaises(r321.R321Hold):
                r321.require_reviewed_installer_command()
        with self.assertRaises(TypeError):
            r321.require_reviewed_installer_command(frozenset({r321.INSTALLER_COMMAND}))

    def test_physical_entry_is_fail_closed_even_with_a_valid_offhost_plan(self):
        result = io.StringIO()
        with redirect_stdout(result):
            status = r321.main(["--install-approved"])
        self.assertEqual(status, 79)
        self.assertIn("PHYSICAL_TRANSPORT_NOT_CERTIFIED", result.getvalue())
        self.assertIn("host_control_verified=NO_VERIFICADO", result.getvalue())


if __name__ == "__main__":
    unittest.main()
