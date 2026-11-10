"""OFFHOST contract and adversarial tests for the single privileged OCI helper."""

import json
import hashlib
import os
import shlex
import sqlite3
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from scripts import rc6_oci_host_helper as helper


class HostHelperCommandContractTest(unittest.TestCase):
    def test_manager_birth_accepts_only_owned_roles_without_docker_args(self):
        attempt = "f" * 32
        command = helper.parse_command(("manager", "birth", "dashboard",
                                        "--attempt", attempt))
        self.assertEqual((command.namespace, command.verb, command.role),
                         ("manager", "birth", "dashboard"))
        for candidate in (
            ("manager", "birth", "boca", "--attempt", attempt),
            ("manager", "birth", "critical", "--attempt", attempt),
            ("manager", "birth", "dashboard", "--mount", "/tmp:/data", "--attempt", attempt),
            ("manager", "birth", "dashboard", "--env-file", "/tmp/boca", "--attempt", attempt),
            ("manager", "birth", "dashboard", "--name", "porota_production_dashboard", "--attempt", attempt),
            ("manager", "birth", "dashboard", "--host", "unix:///run/user/1000/docker.sock", "--attempt", attempt),
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(helper.CustodyHold):
                    helper.parse_command(candidate)

    def test_invalid_namespaces_and_legacy_mutators_are_rejected(self):
        for candidate in (
            ("docker", "rm", "boca"),
            ("manager", "remove", "porota_production_dashboard", "--attempt", "f" * 32),
            ("manager", "retire", "critical", "--attempt", "f" * 32),
            ("manager", "stop", "dashboard", "--attempt", "f" * 32),
            ("critical", "restart"),
            ("critical", "stop"),
            ("critical", "remove"),
            ("critical", "start", "porota_critical_approval_rc6"),
            ("exec", "--", "/bin/sh"),
            ("status", "--all"),
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(helper.CustodyHold):
                    helper.parse_command(candidate)

    def test_manager_begin_and_seal_have_no_free_arguments(self):
        self.assertEqual(helper.parse_command(("manager", "begin")).verb, "begin")
        self.assertEqual(helper.parse_command(("seal",)).verb, "seal")
        for candidate in (("manager", "begin", "--force"),
                          ("seal", "--cid", "deadbeef"),
                          ("manager", "verify-newborn", "critical", "--attempt", "f" * 32)):
            with self.subTest(candidate=candidate):
                with self.assertRaises(helper.CustodyHold):
                    helper.parse_command(candidate)

    def test_manager_post_begin_commands_bind_exact_root_attempt(self):
        nonce = "f" * 32
        parsed = helper.parse_command(("manager", "birth", "dashboard",
                                       "--attempt", nonce))
        self.assertEqual(parsed.attempt_token, nonce)
        for unbound in (("manager", "birth", "dashboard"),
                        ("manager", "retire", "observer"),
                        ("manager", "verify-observer-sources"),
                        ("manager", "verify-image", "tag", "a" * 64)):
            with self.subTest(unbound=unbound):
                with self.assertRaises(helper.CustodyHold):
                    helper.parse_command(unbound)

    def test_verify_image_requires_same_prefixed_immutable_id_as_manager(self):
        tag = "porota-trading-bot:17.0.0-rc6-candidate-" + "a" * 40
        digest = "sha256:" + "8" * 64
        cmd = helper.parse_command(("manager", "verify-image", tag, digest,
                                    "--attempt", "f" * 32))
        self.assertEqual(cmd.expectations, (tag, digest))
        for rejected in ("8" * 64, "sha256:" + "0" * 63, "boca:latest"):
            with self.subTest(rejected=rejected):
                with self.assertRaises(helper.CustodyHold):
                    helper.parse_command(("manager", "verify-image", tag, rejected,
                                          "--attempt", "f" * 32))

    def test_critical_stage_command_matches_manager_contract(self):
        sha = "a" * 40
        command = helper.parse_command(("critical", "describe-stage-intent",
                                        "--paper-only", "--source-sha", sha))
        self.assertEqual((command.verb, command.expectations),
                         ("describe-stage-intent", ("true", sha)))
        stage = helper.parse_command((
            "critical", "stage-protector", "--unit-template",
            "/tmp/rc6-critical-unit.abcdef/porota-critical-approval-rc6.service",
            "--candidate-sha256", "b" * 64,
            "--expected-mainpid", "1234", "--source-sha", sha))
        self.assertEqual(stage.verb, "stage-protector")
        with self.assertRaises(helper.CustodyHold):
            helper.parse_command(("critical", "stage-protector", "--unit-template",
                                  "/tmp/boca.service", "--candidate-sha256", "b" * 64,
                                  "--expected-mainpid", "1234", "--source-sha", sha))

    def test_critical_start_has_root_manifest_expectations_not_docker_args(self):
        sha = "a" * 40
        generation = "e" * 32
        command = helper.parse_command(("critical", "start-newborn",
                                        "--paper-only", "--source-sha", sha,
                                        "--generation", generation))
        self.assertEqual(command.expectations, ("true", sha, generation))
        for candidate in (
            ("critical", "start-newborn"),
            ("critical", "start-newborn", "--paper-only", "--source-sha", sha,
             "--generation", "boca"),
            ("critical", "start-newborn", "--paper-only", "--source-sha", sha,
             "--generation", generation, "--force"),
            ("critical", "start-newborn", "--cid", "b" * 64),
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(helper.CustodyHold):
                    helper.parse_command(candidate)

    def test_quiescence_only_two_roles_and_fixed_lifecycle(self):
        self.assertEqual(helper.parse_command(("quiescence", "stop", "observer")).role,
                         "observer")
        self.assertEqual(helper.parse_command(("quiescence", "start", "dashboard")).role,
                         "dashboard")
        for candidate in (("quiescence", "stop", "critical"),
                          ("quiescence", "rm", "observer"),
                          ("quiescence", "start", "boca"),
                          ("quiescence", "stop", "observer", "--timeout", "0")):
            with self.subTest(candidate=candidate):
                with self.assertRaises(helper.CustodyHold):
                    helper.parse_command(candidate)

    def test_unimplemented_external_attestors_are_typed_deny_only(self):
        for argv in (("quiescence", "verify-prebirth"),
                     ("quiescence", "verify-receipt"),
                     ("image", "preflight-cleanup")):
            with self.subTest(argv=argv):
                parsed = helper.parse_command(argv)
                self.assertEqual(parsed.verb, argv[1])
        for argv in (("image", "cleanup-owned", "--all"),
                     ("quiescence", "verify-prebirth", "--receipt", "/tmp/malicious"),
                     ("quiescence", "verify-receipt", "--cid", "a" * 64)):
            with self.subTest(argv=argv):
                with self.assertRaises(helper.CustodyHold):
                    helper.parse_command(argv)

    def test_entrypoint_fails_before_loading_checkout_as_root(self):
        with mock.patch.object(helper, "_load_installed_authority",
                               side_effect=AssertionError("should never import"),
                               create=True):
            with self.assertRaises(helper.CustodyHold) as caught:
                helper.run_entrypoint(("status",),
                                      executable_path="/tmp/candidate/scripts/rc6_oci_host_helper.py")
        self.assertEqual(caught.exception.exit_code, 79)

    def test_entrypoint_from_installed_path_requires_effective_root(self):
        with mock.patch.object(helper.os, "geteuid", return_value=1000), \
                mock.patch.object(helper, "_load_installed_authority",
                                  side_effect=AssertionError("should never import"),
                                  create=True):
            with self.assertRaises(helper.CustodyHold) as caught:
                helper.run_entrypoint(("status",),
                                      executable_path=helper.INSTALLED_EXECUTABLE)
        self.assertEqual(caught.exception.reason, "ROOT_REQUIRED")

    def test_untrusted_inodes_fail_protected_file_validation(self):
        class Inode:
            st_mode = stat.S_IFREG | 0o755
            st_uid = 0
            st_nlink = 1
            st_size = 128

        helper._validate_protected_inode(Inode(), expected_kind="file")
        for uid, mode, nlink in ((1000, stat.S_IFREG | 0o755, 1),
                                 (0, stat.S_IFREG | 0o777, 1),
                                 (0, stat.S_IFLNK | 0o755, 1),
                                 (0, stat.S_IFREG | 0o755, 2)):
            with self.subTest(uid=uid, mode=mode, nlink=nlink):
                inode = Inode()
                inode.st_uid, inode.st_mode, inode.st_nlink = uid, mode, nlink
                with self.assertRaises(helper.CustodyHold):
                    helper._validate_protected_inode(inode, expected_kind="file")


class _Session:
    def __init__(self):
        self.events = []
        self.state = "running"

    def prepare_birth(self, role):
        self.events.append(("prepare", role))
        return "/var/lib/porota-rc6-oci-custody/generations/example/" + role + ".cid"

    def birth_name(self, role):
        return "porota_rc6_" + role + "_g_" + "a" * 32

    def birth_labels(self, role):
        return ("--label", "porota.rc6.role=" + role)

    def record_birth(self, role, cid):
        self.events.append(("record", role, cid))

    def verify_pending_birth(self, role):
        self.events.append(("verify", role))
        return {"Id": "b" * 64, "Image": "sha256:" + "8" * 64,
                "State": {"Status": "running"}}

    def seal_generation(self):
        self.events.append(("seal",))
        return {"sealed": True, "role_count": 3}

    def authorize_newborn_start(self, role):
        self.events.append(("start", role))

    def verify_role(self, role):
        self.events.append(("verify-sealed", role))
        return {"Id": "b" * 64, "State": {"Status": self.state}}

    def authorize_operation(self, role, operation):
        self.events.append(("operation", role, operation))
        self.state = {"stop": "exited", "start": "running",
                      "remove": "removed"}[operation]

    def authorize_newborn_operation(self, role, operation, *argv, capture=False):
        self.events.append(("newborn-operation", role, operation, argv, capture))
        path = argv[1]
        digest = self.source_digest.get(path, "0" * 64)
        return type("Result", (), {"stdout": digest + "  " + path + "\n"})()


class _Custody:
    def __init__(self, session):
        self.session = session
        self.calls = []

    def begin_genesis(self):
        self.calls.append("genesis")
        return self.session

    def begin_attempt(self):
        self.calls.append("continuity")
        return self.session

    def _daemon_identity(self):
        self.calls.append("daemon-identity")
        return "daemon-rootful-rc6"


class _ApprovedAuthority:
    def __init__(self, phase="GENESIS"):
        self.phase = phase
        self.calls = []
        self.candidate_sha = "a" * 40
        self.generation = "e" * 32
        self.attempt_token = "f" * 32
        self.unit_template_sha256 = "c" * 64
        self.run_id = "42644665843"
        self.run_attempt = "1"
        self.artifact_digest = "sha256:" + "d" * 64
        self.image_id = "sha256:" + "8" * 64
        self.image_tag = "porota-trading-bot:17.0.0-rc6-candidate-" + "a" * 40
        self.daemon_id = "daemon-rootful-rc6"
        self.observer_source_digests = {
            "/app/cf_intraday_scalping.py": "1" * 64,
            "/app/co_market_sessions_hf6.py": "2" * 64,
        }
        self.preflight_error = None
        self.staged = []

    def preflight_launch_profiles(self):
        self.calls.append(("preflight-launch-profiles",))
        if self.preflight_error is not None:
            raise helper.CustodyHold(self.preflight_error)
        return True

    def stage_protector(self, *, source, expected_mainpid):
        self.staged.append((source, expected_mainpid))
        return {"stage": "STAGED", "mainpid_unchanged": True}

    def launch_argv(self, role, *, name, cidfile, labels):
        self.calls.append((role, name, cidfile, labels))
        program = ("o_dashboard.py" if role == "dashboard" else
                   "bv_paper_runtime.py" if role == "observer" else
                   "/code/fm_critical_approval_unix_runtime_rc6.py")
        return ("docker", "create", "--cidfile", cidfile, "--name", name,
                *labels, "--pull", "never", self.image_id, program)


class HostHelperBirthContractTest(unittest.TestCase):
    def setUp(self):
        self.cid = "b" * 64
        self.session = _Session()
        self.custody = _Custody(self.session)
        self.authority = _ApprovedAuthority()
        self.commands = []

    def docker(self, command, *, capture=False):
        self.commands.append((tuple(command), capture))
        output = self.authority.image_id if command[:2] == ("docker", "image") else self.cid
        return type("Result", (), {"stdout": output + "\n"})()

    def controller(self, authority=None):
        return helper.HostHelperController(authority or self.authority,
                                           self.custody, self.docker)

    def test_genesis_begin_does_not_mutate_docker_or_adopt_old_names(self):
        status = self.controller().dispatch(helper.parse_command(("manager", "begin")))
        self.assertEqual(status, "RC6_CUSTODY=GENESIS\nRC6_CUSTODY_ATTEMPT=" + "f" * 32)
        self.assertEqual(self.custody.calls, ["genesis"])
        self.assertEqual(self.commands, [])

    def test_genesis_cannot_retire_legacy_container(self):
        with self.assertRaises(helper.CustodyHold) as caught:
            self.controller().dispatch(helper.parse_command(("manager", "retire", "observer",
                                                            "--attempt", "f" * 32)))
        self.assertIn("LEGACY", str(caught.exception))
        self.assertEqual(self.commands, [])

    def test_verify_image_needs_root_tag_image_and_daemon_not_mutators(self):
        tag = self.authority.image_tag
        image = self.authority.image_id
        cmd = lambda t, d: helper.parse_command((
            "manager", "verify-image", t, d, "--attempt", "f" * 32))
        self.assertEqual(self.controller().dispatch(cmd(tag, image)), "RC6_CUSTODY=OK")
        self.assertEqual(self.commands, [(('docker', 'image', 'inspect', '--format',
                                          '{{.Id}}', tag), True)])
        for tag2, digest2 in ((tag + "x", image), (tag, "sha256:" + "7" * 64)):
            self.commands.clear()
            with self.subTest(tag=tag2, digest=digest2):
                with self.assertRaises(helper.CustodyHold):
                    self.controller().dispatch(cmd(tag2, digest2))
                self.assertEqual(self.commands, [])
        self.custody._daemon_identity = lambda: "unexpected-daemon"
        with self.assertRaises(helper.CustodyHold):
            self.controller().dispatch(cmd(tag, image))

    def test_verify_image_mismatched_actual_tag_does_not_pass(self):
        cmd = helper.parse_command(("manager", "verify-image",
                                    self.authority.image_tag, self.authority.image_id,
                                    "--attempt", "f" * 32))
        self.docker = lambda _args, capture=False: type("Result", (), {
            "stdout": "sha256:" + "7" * 64 + "\n"})()
        with self.assertRaises(helper.CustodyHold):
            self.controller().dispatch(cmd)

    def test_verify_image_full_prelaunch_blocks_before_any_docker_mutation(self):
        cmd = helper.parse_command(("manager", "verify-image",
                                    self.authority.image_tag, self.authority.image_id,
                                    "--attempt", "f" * 32))
        for reason in ("OBSERVER_SOURCE_CLOSURE_NOT_APPROVED",
                       "ENV_CONTROL_KEY_FORBIDDEN", "ENV_SEAL_DIGEST_MISMATCH"):
            self.authority.preflight_error = reason
            self.commands.clear()
            with self.subTest(reason=reason):
                with self.assertRaises(helper.CustodyHold) as caught:
                    self.controller().dispatch(cmd)
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual(self.commands, [],
                                 "Preflight MUST HOLD before Docker image inspection")

    def test_verify_newborn_requires_proven_running_exact_image(self):
        cmd = helper.parse_command(("manager", "verify-newborn", "observer",
                                    "--attempt", "f" * 32))
        self.assertEqual(self.controller().dispatch(cmd), "RC6_CUSTODY=OK")
        self.assertEqual(self.session.events, [("verify", "observer")])
        for mismatch in ({"Id": "x", "Image": self.authority.image_id,
                          "State": {"Status": "running"}},
                         {"Id": "b" * 64, "Image": "sha256:" + "0" * 64,
                          "State": {"Status": "running"}},
                         {"Id": "b" * 64, "Image": self.authority.image_id,
                          "State": {"Status": "created"}}):
            self.session.events.clear()
            self.session.verify_pending_birth = lambda _role: mismatch
            with self.subTest(mismatch=mismatch):
                with self.assertRaises(helper.CustodyHold):
                    self.controller().dispatch(cmd)
                self.assertEqual(self.commands, [])

    def test_retire_only_continuity_sealed_exact_cid_stop_then_remove(self):
        approved = _ApprovedAuthority(phase="CONTINUITY")
        cmd = helper.parse_command(("manager", "retire", "observer",
                                    "--attempt", "f" * 32))
        self.assertEqual(self.controller(approved).dispatch(cmd), "RC6_CUSTODY=OK")
        self.assertEqual(self.session.events, [
            ("verify-sealed", "observer"), ("operation", "observer", "stop"),
            ("verify-sealed", "observer"), ("operation", "observer", "remove")])
        self.assertEqual([call[0][1] for call in self.commands], ["image"])

    def test_retire_exited_only_remove_and_bad_state_hold(self):
        approved = _ApprovedAuthority(phase="CONTINUITY")
        cmd = helper.parse_command(("manager", "retire", "dashboard",
                                    "--attempt", "f" * 32))
        self.session.state = "exited"
        self.assertEqual(self.controller(approved).dispatch(cmd), "RC6_CUSTODY=OK")
        self.assertEqual(self.session.events, [("verify-sealed", "dashboard"),
                                              ("operation", "dashboard", "remove")])
        self.session.events.clear()
        self.session.state = "created"
        with self.assertRaises(helper.CustodyHold):
            self.controller(approved).dispatch(cmd)
        self.assertEqual(self.session.events, [("verify-sealed", "dashboard")])

    def test_stop_observer_only_continuity_sealed_and_idempotent(self):
        cmd = helper.parse_command(("manager", "stop", "observer",
                                    "--attempt", "f" * 32))
        with self.assertRaises(helper.CustodyHold):
            self.controller().dispatch(cmd)
        self.assertEqual(self.session.events, [])
        approved = _ApprovedAuthority(phase="CONTINUITY")
        self.assertEqual(self.controller(approved).dispatch(cmd), "RC6_CUSTODY=OK")
        self.assertEqual(self.session.events, [
            ("verify-sealed", "observer"), ("operation", "observer", "stop"),
            ("verify-sealed", "observer")])
        self.session.events.clear()
        self.assertEqual(self.controller(approved).dispatch(cmd), "RC6_CUSTODY=OK")
        self.assertEqual(self.session.events, [("verify-sealed", "observer")])

    def test_verify_observer_sources_needs_root_approved_two_hashes(self):
        cmd = helper.parse_command(("manager", "verify-observer-sources",
                                    "--attempt", "f" * 32))
        self.authority.observer_source_digests = None
        with self.assertRaises(helper.CustodyHold) as caught:
            self.controller().dispatch(cmd)
        self.assertEqual(caught.exception.reason,
                         "OBSERVER_SOURCE_CLOSURE_NOT_APPROVED")
        self.assertEqual(self.session.events, [])
        sources = {"/app/cf_intraday_scalping.py": "1" * 64,
                   "/app/co_market_sessions_hf6.py": "2" * 64}
        self.authority.observer_source_digests = sources
        self.session.source_digest = sources
        self.assertEqual(self.controller().dispatch(cmd), "RC6_CUSTODY=OK")
        self.assertEqual([e[3] for e in self.session.events if e[0] == "newborn-operation"],
                         [("/usr/bin/sha256sum", "/app/cf_intraday_scalping.py"),
                          ("/usr/bin/sha256sum", "/app/co_market_sessions_hf6.py")])
        self.session.events.clear()
        self.session.source_digest = {path: "0" * 64 for path in sources}
        with self.assertRaises(helper.CustodyHold):
            self.controller().dispatch(cmd)

    def test_complete_manager_genesis_protocol_uses_only_typed_root_oci(self):
        sources = {"/app/cf_intraday_scalping.py": "1" * 64,
                   "/app/co_market_sessions_hf6.py": "2" * 64}
        self.authority.observer_source_digests = sources
        self.session.source_digest = sources
        controller = self.controller()
        attempt = ("--attempt", "f" * 32)
        protocol = (
            ("manager", "begin"),
            ("manager", "verify-image", self.authority.image_tag,
             self.authority.image_id, *attempt),
            ("manager", "birth", "dashboard", *attempt),
            ("manager", "verify-newborn", "dashboard", *attempt),
            ("manager", "birth", "observer", *attempt),
            ("manager", "verify-newborn", "observer", *attempt),
            ("manager", "verify-observer-sources", *attempt),
        )
        results = [controller.dispatch(helper.parse_command(argv)) for argv in protocol]
        self.assertTrue(results[0].startswith("RC6_CUSTODY=GENESIS\n"))
        self.assertEqual(results[1:], ["RC6_CUSTODY=OK"] * (len(protocol) - 1))
        self.assertEqual([c[0][1] for c in self.commands],
                         ["image", "image", "create", "image", "create"])
        self.assertEqual([e for e in self.session.events if e[0] == "start"],
                         [("start", "dashboard"), ("start", "observer")])
        self.assertEqual(len([e for e in self.session.events
                              if e[0] == "newborn-operation"]), 2)
        for response in results:
            self.assertNotIn(self.cid, response)

    def test_all_manager_mutations_require_fresh_root_attempt(self):
        for argv in (("manager", "verify-image", self.authority.image_tag,
                      self.authority.image_id),
                     ("manager", "retire", "observer"),
                     ("manager", "birth", "observer"),
                     ("manager", "verify-newborn", "observer"),
                     ("manager", "verify-observer-sources"),
                     ("manager", "stop", "observer")):
            with self.subTest(argv=argv):
                with self.assertRaises(helper.CustodyHold) as caught:
                    self.controller().dispatch(helper.parse_command(
                        (*argv, "--attempt", "0" * 32)))
                self.assertEqual(caught.exception.reason,
                                 "ATTEMPT_CHANGED_OR_UNVERIFIED")
                self.assertEqual(self.session.events, [])
                self.assertEqual(self.commands, [])

    def test_retire_unresolved_journal_is_not_replayed_as_second_mutation(self):
        approved = _ApprovedAuthority(phase="CONTINUITY")
        original = self.session.authorize_operation

        def unresolved(role, operation):
            if operation == "stop":
                raise helper.CustodyHold("PRIOR_OPERATION_UNRESOLVED")
            original(role, operation)

        self.session.authorize_operation = unresolved
        cmd = helper.parse_command(("manager", "retire", "dashboard",
                                    "--attempt", "f" * 32))
        with self.assertRaises(helper.CustodyHold) as caught:
            self.controller(approved).dispatch(cmd)
        self.assertEqual(caught.exception.reason, "PRIOR_OPERATION_UNRESOLVED")
        self.assertNotIn(("operation", "dashboard", "remove"), self.session.events)

    def test_birth_dashboard_uses_root_profile_and_records_cid_from_birth(self):
        status = self.controller().dispatch(helper.parse_command(("manager", "birth", "dashboard",
                                                                 "--attempt", "f" * 32)))
        self.assertEqual(status, "RC6_CUSTODY=OK")
        self.assertEqual(self.session.events,
                         [("prepare", "dashboard"), ("record", "dashboard", self.cid),
                          ("start", "dashboard"), ("verify", "dashboard")])
        self.assertEqual([c[0][1] for c in self.commands], ["image", "create"])
        command, capture = self.commands[-1]
        self.assertTrue(capture)
        self.assertEqual(command[:2], ("docker", "create"))
        self.assertIn("--cidfile", command)
        self.assertIn("--name", command)
        self.assertIn("porota_rc6_dashboard_g_" + "a" * 32, command)
        self.assertNotIn("porota_production_dashboard", command)
        self.assertNotIn("rm", command)
        self.assertEqual(command[-2], self.authority.image_id)

    def test_birth_mismatch_is_halted_before_any_container_start(self):
        self.authority.observer_source_digests = {
            "/app/cf_intraday_scalping.py": "1" * 64,
            "/app/co_market_sessions_hf6.py": "2" * 64,
        }

        def mismatch(_role, _cid):
            raise helper.CustodyHold("BIRTH_IMAGE_ID_MISMATCH")

        self.session.record_birth = mismatch
        with self.assertRaises(helper.CustodyHold):
            self.controller().dispatch(helper.parse_command((
                "manager", "birth", "observer", "--attempt", "f" * 32)))
        self.assertEqual([c[0][1] for c in self.commands], ["image", "create"])
        self.assertEqual(self.commands[-1][0][:2], ("docker", "create"))
        self.assertNotIn(("start", "observer"), self.session.events)

    def test_observer_missing_or_malformed_root_source_closure_cannot_be_born(self):
        cmd = helper.parse_command(("manager", "birth", "observer",
                                    "--attempt", "f" * 32))
        complete = {"/app/cf_intraday_scalping.py": "1" * 64,
                    "/app/co_market_sessions_hf6.py": "2" * 64}
        for closure in (None, {}, {"/app/cf_intraday_scalping.py": "1" * 64},
                        complete | {"/app/unapproved.py": "f" * 64},
                        complete | {"/app/co_market_sessions_hf6.py": "LIVE"}):
            with self.subTest(closure=closure):
                self.authority.observer_source_digests = closure
                self.session.events.clear()
                self.commands.clear()
                with self.assertRaises(helper.CustodyHold) as caught:
                    self.controller().dispatch(cmd)
                self.assertEqual(caught.exception.reason,
                                 "OBSERVER_SOURCE_CLOSURE_NOT_APPROVED")
                self.assertEqual(self.session.events, [],
                                 "Must HOLD before prepare_birth and A1079 start")
                self.assertEqual(self.commands, [],
                                 "Must not even CREATE an unverified observer")

    def test_all_births_require_common_root_source_preflight(self):
        self.authority.observer_source_digests = None
        for args in (("manager", "birth", "dashboard", "--attempt", "f" * 32),
                     ("critical", "birth")):
            with self.subTest(args=args):
                with self.assertRaises(helper.CustodyHold):
                    self.controller().dispatch(helper.parse_command(args))
        self.assertEqual(self.commands, [])
        self.assertEqual(self.session.events, [])
        self.authority.observer_source_digests = {
            "/app/cf_intraday_scalping.py": "1" * 64,
            "/app/co_market_sessions_hf6.py": "2" * 64,
        }
        self.assertEqual(self.controller().dispatch(helper.parse_command((
            "manager", "birth", "dashboard", "--attempt", "f" * 32))),
            "RC6_CUSTODY=OK")
        self.session.events.clear()
        self.commands.clear()
        self.assertEqual(self.controller().dispatch(helper.parse_command((
            "critical", "birth"))), "RC6_CUSTODY=OK")

    def test_direct_continuity_retire_rechecks_prelaunch_before_cid_mutation(self):
        approved = _ApprovedAuthority(phase="CONTINUITY")
        cmd = helper.parse_command(("manager", "retire", "observer",
                                    "--attempt", "f" * 32))
        for error in ("ENV_CONTROL_KEY_FORBIDDEN",
                      "ENV_SEAL_DIGEST_MISMATCH",
                      "OBSERVER_SOURCE_CLOSURE_NOT_APPROVED"):
            approved.observer_source_digests = self.authority.observer_source_digests
            approved.preflight_error = error
            with self.subTest(error=error):
                self.session.events.clear()
                with self.assertRaises(helper.CustodyHold) as caught:
                    self.controller(approved).dispatch(cmd)
                self.assertEqual(caught.exception.reason, error)
                self.assertEqual(self.session.events, [],
                                 "Cannot stop a predecessor before trusted prelaunch")
        approved.preflight_error = None
        approved.observer_source_digests = None
        with self.assertRaises(helper.CustodyHold):
            self.controller(approved).dispatch(cmd)
        self.assertEqual(self.session.events, [])

    def test_direct_retire_denied_if_daemon_or_immutable_image_changed(self):
        approved = _ApprovedAuthority(phase="CONTINUITY")
        cmd = helper.parse_command(("manager", "retire", "dashboard",
                                    "--attempt", "f" * 32))
        self.custody._daemon_identity = lambda: "unrelated-rootless-daemon"
        with self.assertRaises(helper.CustodyHold) as caught:
            self.controller(approved).dispatch(cmd)
        self.assertEqual(caught.exception.reason, "IMAGE_DAEMON_CHANGED")
        self.assertEqual(self.session.events, [])
        self.custody._daemon_identity = lambda: approved.daemon_id

        def fake_image(command, capture=False):
            self.commands.append((tuple(command), capture))
            return type("Result", (), {"stdout": "sha256:" + "0" * 64 + "\n"})()

        self.docker = fake_image
        with self.assertRaises(helper.CustodyHold) as caught:
            self.controller(approved).dispatch(cmd)
        self.assertEqual(caught.exception.reason, "IMAGE_TAG_BINDING_CHANGED")
        self.assertEqual(self.session.events, [])
        self.assertEqual([args[0][1] for args in self.commands], ["image"])

    def test_env_mutation_after_verify_image_holds_on_birth_before_docker(self):
        verify = helper.parse_command(("manager", "verify-image",
                                       self.authority.image_tag, self.authority.image_id,
                                       "--attempt", "f" * 32))
        self.assertEqual(self.controller().dispatch(verify), "RC6_CUSTODY=OK")
        self.commands.clear()
        self.authority.preflight_error = "ENV_SEAL_DIGEST_MISMATCH"
        with self.assertRaises(helper.CustodyHold) as caught:
            self.controller().dispatch(helper.parse_command((
                "manager", "birth", "observer", "--attempt", "f" * 32)))
        self.assertEqual(caught.exception.reason, "ENV_SEAL_DIGEST_MISMATCH")
        self.assertEqual(self.commands, [])
        self.assertEqual(self.session.events, [])

    def test_critical_birth_is_created_not_started(self):
        status = self.controller().dispatch(helper.parse_command(("critical", "birth")))
        self.assertEqual(status, "RC6_CUSTODY=OK")
        self.assertEqual([c[0][1] for c in self.commands], ["image", "create"])
        self.assertEqual(self.session.events[:2],
                         [("prepare", "critical"), ("record", "critical", self.cid)])

    def test_seal_calls_shared_three_role_custody_not_a_second_seal(self):
        status = self.controller().dispatch(helper.parse_command(("seal",)))
        self.assertEqual(status, "RC6_CUSTODY=OK")
        self.assertEqual(self.session.events, [("seal",)])
        self.assertEqual(self.commands, [])

    def test_verify_role_holds_before_prior_or_postseal_session_lookup(self):
        # A1079 begin_attempt/verify_role checks the PREVIOUS sealed current.
        # A post-seal call to begin_attempt fails GENERATION_REPLAY. Neither
        # can prove the NEW generation in the approved root authority.
        for phase in ("GENESIS", "CONTINUITY"):
            self.authority.phase = phase
            for role in ("dashboard", "observer", "critical"):
                with self.subTest(phase=phase, role=role):
                    with self.assertRaises(helper.CustodyHold) as caught:
                        self.controller().dispatch(helper.parse_command(("verify", role)))
                    self.assertEqual(caught.exception.reason,
                                     "SEALED_CURRENT_CUSTODY_API_UNAVAILABLE")
                    self.assertEqual(self.custody.calls, [])
                    self.assertEqual(self.session.events, [])
                    self.assertEqual(self.commands, [])

    def test_verify_role_never_claims_old_cid_as_new_after_seal(self):
        self.authority.phase = "CONTINUITY"
        self.authority.generation = "e" * 32
        self.session.verify_role = lambda _role: {
            "Id": "b" * 64, "State": {"Status": "running"}}
        with mock.patch.object(self.custody, "begin_attempt",
                               side_effect=helper.CustodyHold("GENERATION_REPLAY")):
            with self.assertRaises(helper.CustodyHold) as caught:
                self.controller().dispatch(helper.parse_command(("verify", "critical")))
        self.assertEqual(caught.exception.reason,
                         "SEALED_CURRENT_CUSTODY_API_UNAVAILABLE")
        self.assertEqual(self.custody.calls, [])
        self.assertEqual(self.commands, [])

    def test_critical_start_checks_root_intent_before_fencer_delegation(self):
        args = ("critical", "start-newborn", "--paper-only", "--source-sha",
                "a" * 40, "--generation", "e" * 32)
        self.assertEqual(self.controller().dispatch(helper.parse_command(args)),
                         "RC6_CUSTODY=OK")
        self.assertEqual(self.session.events, [("start", "critical")])
        for wrong in ("f" * 40, "b" * 40):
            self.session.events.clear()
            bad_args = ("critical", "start-newborn", "--paper-only", "--source-sha",
                        wrong, "--generation", "e" * 32)
            with self.assertRaises(helper.CustodyHold):
                self.controller().dispatch(helper.parse_command(bad_args))
            self.assertEqual(self.session.events, [])

    def test_critical_external_fencer_hold_never_becomes_docker_start(self):
        def no_independent_exclusivity(role):
            self.session.events.append(("external-fencer", role))
            raise helper.CustodyHold("CRITICAL_EXCLUSIVITY_UNPROVEN")

        self.session.authorize_newborn_start = no_independent_exclusivity
        args = ("critical", "start-newborn", "--paper-only", "--source-sha",
                "a" * 40, "--generation", "e" * 32)
        with self.assertRaises(helper.CustodyHold) as held:
            self.controller().dispatch(helper.parse_command(args))
        self.assertEqual(held.exception.reason, "CRITICAL_EXCLUSIVITY_UNPROVEN")
        self.assertEqual(self.session.events, [("external-fencer", "critical")])
        self.assertEqual(self.commands, [])

    def test_quiescence_never_touches_legacy_and_receipt_is_redacted(self):
        with self.assertRaises(helper.CustodyHold):
            self.controller().dispatch(helper.parse_command(("quiescence", "stop", "observer")))
        self.assertEqual(self.commands, [])
        approved = _ApprovedAuthority(phase="CONTINUITY")
        receipt = self.controller(approved).dispatch(
            helper.parse_command(("quiescence", "stop", "observer")))
        parsed = json.loads(receipt)
        self.assertEqual(parsed["role"], "observer")
        self.assertEqual(parsed["pre_status"], "running")
        self.assertEqual(parsed["post_status"], "exited")
        self.assertEqual(parsed["generation"], "e" * 32)
        self.assertEqual(len(parsed["cid_fingerprint"]), 12)
        self.assertNotIn("b" * 64, receipt)
        self.assertEqual(self.session.events[-3:],
                         [("verify-sealed", "observer"), ("operation", "observer", "stop"),
                          ("verify-sealed", "observer")])

    def test_critical_protector_stages_without_docker_or_restart(self):
        template = "/tmp/rc6-critical-unit.abcdef/porota-critical-approval-rc6.service"
        args = ("critical", "stage-protector", "--unit-template", template,
                "--candidate-sha256", "c" * 64,
                "--expected-mainpid", "1234", "--source-sha", "a" * 40)
        status = self.controller().dispatch(helper.parse_command(args))
        self.assertEqual(status, "RC6_CUSTODY=STAGED")
        self.assertEqual(self.authority.staged, [(template, 1234)])
        self.assertEqual(self.commands, [])
        for bad_sha in ("d" * 64, "e" * 64):
            self.authority.staged.clear()
            bad = ("critical", "stage-protector", "--unit-template", template,
                   "--candidate-sha256", bad_sha,
                   "--expected-mainpid", "1234", "--source-sha", "a" * 40)
            with self.assertRaises(helper.CustodyHold):
                self.controller().dispatch(helper.parse_command(bad))
            self.assertEqual(self.authority.staged, [])

    def test_stage_intent_is_a_bounded_read_only_root_receipt(self):
        cmd = helper.parse_command(("critical", "describe-stage-intent", "--paper-only",
                                    "--source-sha", "a" * 40))
        text = self.controller().dispatch(cmd)
        self.assertEqual(text,
                         "RC6_STAGE_INTENT_V1 " + "e" * 32 +
                         " 42644665843 1 sha256:" + "d" * 64 +
                         " porota_rc6_critical_g_" + "e" * 32)
        self.assertEqual(self.commands, [])

    def test_prebirth_postseal_and_image_cleanup_hold_without_external_proof(self):
        for argv, reason in (
            (("quiescence", "verify-prebirth"), "EXTERNAL_QUIESCENCE_ATTESTOR_UNVERIFIED"),
            (("quiescence", "verify-receipt"), "EXTERNAL_QUIESCENCE_ATTESTOR_UNVERIFIED"),
            (("image", "preflight-cleanup"), "OWNED_IMAGE_REFERENCE_PROOF_UNVERIFIED"),
        ):
            with self.subTest(argv=argv):
                self.commands.clear()
                with self.assertRaises(helper.CustodyHold) as caught:
                    self.controller().dispatch(helper.parse_command(argv))
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual(self.commands, [])


class HostHelperIndependentApprovalTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
        self.approval = {
            "schema_version": 1,
            "phase": "GENESIS",
            "candidate_sha": "a" * 40,
            "candidate_tree": "b" * 40,
            "artifact_digest": "sha256:" + "c" * 64,
            "image_id": "sha256:" + "d" * 64,
            "daemon_id": "6073f8d4-1420-4c38-9106-e071a622e0ae",
            "generation": "e" * 32,
            "run_id": "42644665843",
            "run_attempt": "1",
            "paper_mode": "PRODUCTION_PAPER",
            "real_orders_allowed": False,
            "helper_sha256": "1" * 64,
            "custody_sha256": "2" * 64,
            "profiles_sha256": "3" * 64,
            "unit_template_sha256": "4" * 64,
            "expires_at": "2026-11-09T15:30:00Z",
        }

    def validate(self, approval=None, **digests):
        return helper.validate_independent_approval(
            self.approval if approval is None else approval,
            now=self.now,
            helper_digest=digests.get("helper_digest", "1" * 64),
            custody_digest=digests.get("custody_digest", "2" * 64),
            profiles_digest=digests.get("profiles_digest", "3" * 64))

    def test_approval_binds_code_artifact_daemon_and_paper(self):
        selected = self.validate()
        self.assertEqual(selected["generation"], "e" * 32)
        self.assertFalse(selected["real_orders_allowed"])

    def test_expired_or_incomplete_or_nonpaper_approval_rejected(self):
        for patch in ({"expires_at": "2026-11-09T14:59:59Z"},
                      {"paper_mode": "REAL"},
                      {"real_orders_allowed": True},
                      {"generation": "bad"},
                      {"daemon_id": ""},
                      {"artifact_digest": "sha256:bad"},
                      {"candidate_sha": "0"},
                      {"schema_version": 2},
                      {"profiles_sha256": None}):
            with self.subTest(patch=patch):
                approval = self.approval | patch
                with self.assertRaises(helper.CustodyHold):
                    self.validate(approval)

    def test_altered_code_or_profiles_digest_rejected(self):
        for alteration in ({"helper_digest": "0" * 64},
                           {"custody_digest": "0" * 64},
                           {"profiles_digest": "0" * 64}):
            with self.subTest(alteration=alteration):
                with self.assertRaises(helper.CustodyHold):
                    self.validate(**alteration)

    def test_attempt_token_changes_on_run_attempt_even_same_generation(self):
        first = helper.derive_attempt_token(self.validate())
        same = helper.derive_attempt_token(self.validate())
        changed = helper.derive_attempt_token(
            self.validate(self.approval | {"run_attempt": "2"}))
        self.assertRegex(first, r"^[0-9a-f]{32}$")
        self.assertEqual(first, same)
        self.assertNotEqual(first, changed)

    def test_duplicate_json_keys_and_nonfinite_constants_rejected(self):
        for raw in (b'{"phase":"GENESIS","phase":"CONTINUITY"}',
                    b'{"value":NaN}', b'{"value":Infinity}'):
            with self.subTest(raw=raw):
                with self.assertRaises(helper.CustodyHold):
                    helper.strict_approval_json(raw)

    def test_loader_rejects_tampered_installed_code_before_import(self):
        fake_helper = b"# fixed trusted executable\n"
        fake_custody = b"# independently installed A1079 module\n"
        fake_profiles = b'{}\n'
        approval = self.approval | {
            "helper_sha256": hashlib.sha256(fake_helper).hexdigest(),
            "custody_sha256": hashlib.sha256(fake_custody).hexdigest(),
            "profiles_sha256": hashlib.sha256(fake_profiles).hexdigest(),
        }
        blobs = {
            helper.APPROVAL_FILE: json.dumps(approval).encode(),
            helper.INSTALLED_EXECUTABLE: fake_helper,
            helper.INSTALLED_CUSTODY_FILE: fake_custody + b"# tampered\n",
            helper.INSTALLED_PROFILES_FILE: fake_profiles,
        }

        def read_only_fixed_path(path):
            self.assertIn(path, blobs, "loader tried noninstalled path")
            return blobs[path]

        with mock.patch.object(helper, "_read_root_owned", side_effect=read_only_fixed_path,
                               create=True), \
                mock.patch.object(helper, "_import_installed_custody",
                                  side_effect=AssertionError("root must not import"),
                                  create=True):
            with self.assertRaises(helper.CustodyHold) as caught:
                helper._load_installed_authority(now=self.now)
        self.assertEqual(caught.exception.reason, "APPROVAL_INSTALLED_CODE_MISMATCH")

    def test_helper_and_custody_must_agree_on_root_owned_run_identity(self):
        class FakeInstalledCustody:
            DOCKER_BINARY = "/usr/bin/docker"
            DOCKER_ENDPOINT = "unix:///var/run/docker.sock"
            TRUSTED_ROOT = helper.Path("/var/lib/porota-rc6-oci-custody")

            def __init__(self, document):
                self.document = document

            def _read_trusted_genesis(self):
                return self.document

        fields = ("schema_version", "candidate_sha", "candidate_tree", "artifact_digest",
                  "image_id", "daemon_id", "generation", "run_id", "run_attempt",
                  "paper_mode", "real_orders_allowed")
        genesis = {field: self.approval[field] for field in fields}
        genesis.update({"state": "LEGACY_UNTRUSTED",
                        "authority": "INDEPENDENT_HOST_CONTROL_PLANE"})
        custody = FakeInstalledCustody(genesis)
        helper.verify_custody_identity(self.validate(), custody)
        genesis["generation"] = "f" * 32
        with self.assertRaises(helper.CustodyHold):
            helper.verify_custody_identity(self.validate(), custody)
        genesis["generation"] = "e" * 32
        custody.DOCKER_ENDPOINT = "unix:///run/user/1000/docker.sock"
        with self.assertRaises(helper.CustodyHold):
            helper.verify_custody_identity(self.validate(), custody)


class HostHelperApprovedProfileTest(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.digest = "sha256:" + "d" * 64
        root = "/opt/porota-trading"
        generation = "e" * 32
        self.sealed_env = ("/var/lib/porota-rc6-oci-custody/generations/" +
                           generation + "/env/")
        self.env_sha = "1" * 64
        self.profile = {
            "schema_version": 1,
            "candidate_sha": self.sha,
            "artifact_digest": "sha256:" + "c" * 64,
            "image_tag": "porota-trading-bot:17.0.0-rc6-candidate-" + self.sha,
            "root_stage": root,
            "roles": {
                "dashboard": {
                    "env_file": self.sealed_env + "dashboard.env",
                    "env_sha256": self.env_sha,
                    "environment": {"POROTA_RUNTIME_MODE": "PRODUCTION_PAPER",
                                    "PPI_ORDERS": "BLOCKED", "REAL_ORDERS_ALLOWED": "false"},
                    "mounts": [{"source": root + "/data", "target": "/app/data",
                                "readonly": False}],
                },
                "observer": {
                    "env_file": self.sealed_env + "observer.env",
                    "env_sha256": self.env_sha,
                    "environment": {"PPI_PRODUCTION_SECRET_FILE":
                                    "/run/secrets/ppi_production.json",
                                    "POROTA_RUNTIME_MODE": "PRODUCTION_PAPER",
                                    "PPI_ORDERS": "BLOCKED", "REAL_ORDERS_ALLOWED": "false",
                                    "MARKET_OPEN_HOUR": "10",
                                    "MARKET_OPEN_MINUTE": "30"},
                    "mounts": [{"source": root + "/.secrets/ppi_production.json",
                                "target": "/run/secrets/ppi_production.json",
                                "readonly": True}],
                },
                "critical": {
                    "env_file": None,
                    "env_sha256": None,
                    "environment": {"POROTA_RUNTIME_MODE": "PRODUCTION_PAPER",
                                    "PPI_ORDERS": "BLOCKED", "REAL_ORDERS_ALLOWED": "false",
                                    "PYTHONDONTWRITEBYTECODE": "1",
                                    "POROTA_CRITICAL_APPROVAL_ENABLED": "true",
                                    "POROTA_TELEGRAM_SINGLE_CONSUMER_ENFORCED": "true",
                                    "POROTA_CRITICAL_APPROVAL_POLL_SECONDS": "10",
                                    "POROTA_CRITICAL_APPROVAL_DB": "/data/critical_approval_rc6.db",
                                    "POROTA_CRITICAL_GITHUB_SOCKET": "/run/control/github.sock",
                                    "POROTA_CRITICAL_BROKER_TOKEN_FILE":
                                    "/run/secrets/broker_capability.token",
                                    "POROTA_CRITICAL_TELEGRAM_TOKEN_FILE":
                                    "/run/secrets/critical_telegram.token",
                                    "POROTA_CRITICAL_TELEGRAM_CHAT_FILE":
                                    "/run/secrets/critical_telegram.chat"},
                    "mounts": [
                        {"source": "/opt/porota-control-plane-rc6/current",
                         "target": "/code", "readonly": True},
                        {"source": "/opt/porota-control-plane-rc6/secrets/broker_capability.token",
                         "target": "/run/secrets/broker_capability.token", "readonly": True},
                        {"source": "/opt/porota-control-plane-rc6/secrets/critical_telegram.token",
                         "target": "/run/secrets/critical_telegram.token", "readonly": True},
                        {"source": "/opt/porota-control-plane-rc6/secrets/critical_telegram.chat",
                         "target": "/run/secrets/critical_telegram.chat", "readonly": True},
                        {"source": "/opt/porota-control-plane-rc6/data",
                         "target": "/data", "readonly": False},
                        {"source": "/run/porota-critical-approval-rc6",
                         "target": "/run/control", "readonly": False},
                    ],
                },
            },
        }

    def approved(self, profile=None):
        return helper.ApprovedLaunchProfiles(
            self.profile if profile is None else profile,
            candidate_sha=self.sha,
            artifact_digest="sha256:" + "c" * 64,
            image_id=self.digest,
            generation="e" * 32)

    def test_critical_required_environment_is_accepted_without_env_file(self):
        bound = self.approved()
        args = bound.launch_argv(
            "critical", name="porota_rc6_critical_g_" + "e" * 32,
            cidfile="/var/lib/porota-rc6-oci-custody/generations/e/critical.cid",
            labels=("--label", "porota.rc6.role=critical"))
        for value in (
            "POROTA_CRITICAL_APPROVAL_ENABLED=true",
            "POROTA_TELEGRAM_SINGLE_CONSUMER_ENFORCED=true",
            "POROTA_CRITICAL_APPROVAL_POLL_SECONDS=10",
            "POROTA_CRITICAL_APPROVAL_DB=/data/critical_approval_rc6.db",
            "POROTA_CRITICAL_GITHUB_SOCKET=/run/control/github.sock",
            "POROTA_CRITICAL_BROKER_TOKEN_FILE=/run/secrets/broker_capability.token",
            "POROTA_CRITICAL_TELEGRAM_TOKEN_FILE=/run/secrets/critical_telegram.token",
            "POROTA_CRITICAL_TELEGRAM_CHAT_FILE=/run/secrets/critical_telegram.chat",
        ):
            self.assertIn(value, args)
        self.assertNotIn("--env-file", args)

    def test_critical_mounts_its_own_sqlite_and_broker_socket(self):
        bound = self.approved()
        args = bound.launch_argv(
            "critical", name="porota_rc6_critical_g_" + "e" * 32,
            cidfile="/var/lib/porota-rc6-oci-custody/generations/e/critical.cid",
            labels=("--label", "porota.rc6.role=critical"))
        mounts = [args[i + 1] for i, token in enumerate(args[:-1]) if token == "--mount"]
        self.assertEqual(len(mounts), 6)
        self.assertIn("type=bind,source=/opt/porota-control-plane-rc6/data,target=/data", mounts)
        self.assertIn("type=bind,source=/run/porota-critical-approval-rc6,target=/run/control",
                      mounts)
        self.assertIn("type=bind,source=/opt/porota-control-plane-rc6/current,target=/code,readonly",
                      mounts)
        self.assertTrue(all("docker.sock" not in mount and "/opt/boca" not in mount
                            for mount in mounts))

    def test_critical_creation_reaps_children_and_checks_unix_issues_broker(self):
        args = self.approved().launch_argv(
            "critical", name="porota_rc6_critical_g_" + "e" * 32,
            cidfile="/var/lib/porota-rc6-oci-custody/generations/e/critical.cid",
            labels=("--label", "porota.rc6.role=critical"))
        self.assertIn("--init", args)
        self.assertIn("--pids-limit", args)
        self.assertEqual(args[args.index("--pids-limit") + 1], "64")
        self.assertNotIn("--no-healthcheck", args)
        health = args[args.index("--health-cmd") + 1]
        for required in ("python -I -c", "/code", "UnixGithubIssuesClient",
                         '"status"', '"ok"', '"capability"', '"issues_only"'):
            self.assertIn(required, health)
        for forbidden in ("DASHBOARD_PORT", "urllib", "PYTHONPATH"):
            self.assertNotIn(forbidden, health)
        for option, expected in (("--health-interval", "30s"),
                                 ("--health-timeout", "20s"),
                                 ("--health-retries", "3"),
                                 ("--health-start-period", "20s")):
            self.assertEqual(args[args.index(option) + 1], expected)

    def test_critical_rejects_extra_provider_secret_even_in_paper_mode(self):
        hostile = json.loads(json.dumps(self.profile))
        hostile["roles"]["critical"]["environment"]["PPI_API_KEY"] = "synthetic-not-a-secret"
        with self.assertRaises(helper.CustodyHold):
            self.approved(hostile)

    def test_critical_required_flags_paths_and_unsafe_env_fail_closed(self):
        required = (
            "POROTA_CRITICAL_APPROVAL_ENABLED",
            "POROTA_TELEGRAM_SINGLE_CONSUMER_ENFORCED",
            "POROTA_CRITICAL_APPROVAL_DB", "POROTA_CRITICAL_GITHUB_SOCKET",
            "POROTA_CRITICAL_BROKER_TOKEN_FILE",
            "POROTA_CRITICAL_TELEGRAM_TOKEN_FILE",
            "POROTA_CRITICAL_TELEGRAM_CHAT_FILE",
        )
        for key in required:
            absent = json.loads(json.dumps(self.profile))
            absent["roles"]["critical"]["environment"].pop(key)
            with self.subTest(missing=key), self.assertRaises(helper.CustodyHold):
                self.approved(absent)
        mutations = (
            ("POROTA_CRITICAL_APPROVAL_ENABLED", "false"),
            ("POROTA_TELEGRAM_SINGLE_CONSUMER_ENFORCED", "false"),
            ("POROTA_CRITICAL_APPROVAL_DB", "/app/data/critical_approval_rc6.db"),
            ("POROTA_CRITICAL_GITHUB_SOCKET", "/var/run/docker.sock"),
            ("POROTA_CRITICAL_APPROVAL_POLL_SECONDS", "0"),
            ("PYTHONDONTWRITEBYTECODE", "0"),
            ("LD_PRELOAD", "/data/hostile.so"),
            ("PYTHONPATH", "/data"),
            ("GITHUB_TOKEN", "synthetic-not-a-token"),
        )
        for key, value in mutations:
            hostile = json.loads(json.dumps(self.profile))
            hostile["roles"]["critical"]["environment"][key] = value
            with self.subTest(key=key), self.assertRaises(helper.CustodyHold):
                self.approved(hostile)
        wrong_role = json.loads(json.dumps(self.profile))
        wrong_role["roles"]["observer"]["environment"][
            "POROTA_CRITICAL_APPROVAL_ENABLED"] = "true"
        with self.assertRaises(helper.CustodyHold):
            self.approved(wrong_role)

    def test_critical_mount_adversaries_and_partial_profile_are_rejected(self):
        alterations = (
            (4, "source", "/opt/boca/data"),
            (4, "source", "/opt/porota-control-plane-rc6/data2"),
            (4, "source", "/opt/porota-control-plane-rc6/data/../boca"),
            (4, "target", "/app/data"),
            (4, "readonly", True),
            (5, "source", "/var/run/docker.sock"),
            (5, "target", "/run/control/github.sock"),
            (0, "readonly", False),
        )
        for index, field, value in alterations:
            hostile = json.loads(json.dumps(self.profile))
            hostile["roles"]["critical"]["mounts"][index][field] = value
            with self.subTest(index=index, field=field, value=value):
                with self.assertRaises(helper.CustodyHold):
                    self.approved(hostile)
        omitted = json.loads(json.dumps(self.profile))
        omitted["roles"]["critical"]["mounts"].pop(4)
        with self.assertRaises(helper.CustodyHold):
            self.approved(omitted)
        foreign_role = json.loads(json.dumps(self.profile))
        foreign_role["roles"]["dashboard"]["mounts"].append({
            "source": "/opt/porota-control-plane-rc6/data",
            "target": "/data", "readonly": False})
        with self.assertRaises(helper.CustodyHold):
            self.approved(foreign_role)

    def test_critical_health_is_syntactically_valid_isolated_python(self):
        args = self.approved().launch_argv(
            "critical", name="porota_rc6_critical_g_" + "e" * 32,
            cidfile="/var/lib/porota-rc6-oci-custody/generations/e/critical.cid",
            labels=("--label", "porota.rc6.role=critical"))
        words = shlex.split(args[args.index("--health-cmd") + 1])
        self.assertEqual(words[:3], ["python", "-I", "-c"])
        compile(words[3], "<critical-broker-health>", "exec")

    def test_critical_sqlite_state_and_telegram_offset_survive_reopen_offhost(self):
        from fg_critical_approval_gateway_rc6 import ApprovalStore

        with tempfile.TemporaryDirectory() as directory:
            db = os.path.join(directory, "critical_approval_rc6.db")
            first = ApprovalStore(path=db)
            first.ensure_issue(732)
            self.assertTrue(first.decide(732, "APPROVED", "synthetic-actor", 12345))
            first.set_offset(731)
            second = ApprovalStore(path=db)
            self.assertEqual(second.offset(), 731)
            self.assertFalse(second.decide(732, "REJECTED", "other-actor", 12346))
            with sqlite3.connect(db) as connection:
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                state = connection.execute(
                    "SELECT decision,github_comment_id FROM critical_approval_state "
                    "WHERE issue_number=732").fetchone()
                self.assertEqual(state, ("APPROVED", 12345))

    def test_dashboard_launch_is_immutable_paper_profile_local_port_only(self):
        bound = self.approved()
        with mock.patch.object(helper, "_verify_root_sealed_env", return_value=None,
                               create=True) as env_check:
            args = bound.launch_argv("dashboard", name="porota_rc6_dashboard_g_" + "e" * 32,
                                      cidfile="/var/lib/porota-rc6-oci-custody/generations/e/dashboard.cid",
                                      labels=("--label", "porota.rc6.role=dashboard"))
        env_check.assert_called_once_with(self.sealed_env + "dashboard.env", self.env_sha)
        self.assertEqual(args[:2], ("docker", "create"))
        self.assertEqual(args[-2:], (self.digest, "o_dashboard.py"))
        self.assertIn("127.0.0.1:8000:8000", args)
        self.assertIn("--cidfile", args)
        self.assertIn("--mount", args)
        self.assertIn("no-new-privileges:true", args)
        self.assertNotIn("--privileged", args)
        self.assertNotIn("--network", args)
        self.assertNotIn("--device", args)

    def test_observer_profile_preserves_byma_timing_and_rootfs(self):
        bound = self.approved()
        with mock.patch.object(helper, "_verify_root_sealed_env", return_value=None,
                               create=True):
            args = bound.launch_argv(
                "observer", name="porota_rc6_observer_g_" + "e" * 32,
                cidfile="/var/lib/porota-rc6-oci-custody/generations/e/observer.cid",
                labels=("--label", "porota.rc6.role=observer"))
        self.assertIn("MARKET_OPEN_HOUR=10", args)
        self.assertIn("MARKET_OPEN_MINUTE=30", args)
        self.assertIn("--read-only", args)
        self.assertIn("--tmpfs", args)
        self.assertIn("--cap-drop", args)
        self.assertIn("ALL", args)
        self.assertIn("no-new-privileges:true", args)
        self.assertIn("botuser", args)
        self.assertNotIn("-p", args)

    def test_observer_invalid_byma_time_fails_without_container_birth(self):
        for field, value in (("MARKET_OPEN_HOUR", "11"),
                             ("MARKET_OPEN_MINUTE", "00")):
            broken = json.loads(json.dumps(self.profile))
            broken["roles"]["observer"]["environment"][field] = value
            with self.subTest(field=field):
                with self.assertRaises(helper.CustodyHold):
                    self.approved(broken)

    def test_only_trusted_source_hash_closure_can_enable_observer_exec(self):
        root_hashes = {"/app/cf_intraday_scalping.py": "1" * 64,
                       "/app/co_market_sessions_hf6.py": "2" * 64}
        trusted = json.loads(json.dumps(self.profile))
        trusted["observer_source_sha256"] = root_hashes
        self.assertEqual(self.approved(trusted).observer_source_digests, root_hashes)
        for malformed in (
            {"/app/cf_intraday_scalping.py": "1" * 64},
            root_hashes | {"/app/custom_plugin.py": "f" * 64},
            root_hashes | {"/app/co_market_sessions_hf6.py": "x" * 64},
        ):
            with self.subTest(hashes=malformed):
                bad = json.loads(json.dumps(self.profile))
                bad["observer_source_sha256"] = malformed
                with self.assertRaises(helper.CustodyHold):
                    self.approved(bad)

    def test_prelaunch_validates_all_sealed_env_and_source_before_retire(self):
        root_hashes = {"/app/cf_intraday_scalping.py": "1" * 64,
                       "/app/co_market_sessions_hf6.py": "2" * 64}
        trusted = json.loads(json.dumps(self.profile))
        trusted["observer_source_sha256"] = root_hashes
        approved = self.approved(trusted)
        raw = (b"POROTA_RUNTIME_MODE=PRODUCTION_PAPER\nPPI_ORDERS=BLOCKED\n"
               b"REAL_ORDERS_ALLOWED=false\nMARKET_OPEN_HOUR=10\n"
               b"MARKET_OPEN_MINUTE=30\n")
        approved.roles["dashboard"]["env_sha256"] = hashlib.sha256(raw).hexdigest()
        approved.roles["observer"]["env_sha256"] = hashlib.sha256(raw).hexdigest()
        with mock.patch.object(helper, "_read_root_owned", return_value=raw) as root_read:
            self.assertTrue(approved.preflight_launch_profiles())
        self.assertEqual(root_read.call_count, 2)

        modified = raw + b"LD_PRELOAD=/app/data/evil.so\n"
        approved.roles["dashboard"]["env_sha256"] = hashlib.sha256(modified).hexdigest()
        with mock.patch.object(helper, "_read_root_owned", return_value=modified):
            with self.assertRaises(helper.CustodyHold) as caught:
                approved.preflight_launch_profiles()
        self.assertEqual(caught.exception.reason, "ENV_CONTROL_KEY_FORBIDDEN")

    def test_profiles_reject_executable_environment_injection(self):
        for key, value in (
            ("LD_PRELOAD", "/app/data/evil.so"),
            ("LD_LIBRARY_PATH", "/app/data/lib"),
            ("PATH", "/app/data/bin:/usr/bin"),
            ("PYTHONPATH", "/app/data"),
            ("PYTHONHOME", "/app/data"),
            ("BASH_ENV", "/app/data/evil.sh"),
            ("SHELLOPTS", "xtrace"),
        ):
            profile = json.loads(json.dumps(self.profile))
            profile["roles"]["observer"]["environment"][key] = value
            with self.subTest(key=key):
                with self.assertRaises(helper.CustodyHold):
                    self.approved(profile)

    def test_sealed_env_rejects_executable_control_keys_despite_paper_values(self):
        safe = (b"POROTA_RUNTIME_MODE=PRODUCTION_PAPER\nPPI_ORDERS=BLOCKED\n"
                b"REAL_ORDERS_ALLOWED=false\nMARKET_OPEN_HOUR=10\n"
                b"MARKET_OPEN_MINUTE=30\n")
        for key, value in (
            ("LD_PRELOAD", "/app/data/evil.so"),
            ("LD_LIBRARY_PATH", "/app/data/lib"),
            ("PATH", "/app/data/bin:/usr/bin"),
            ("PYTHONPATH", "/app/data"),
            ("PYTHONHOME", "/app/data"),
            ("BASH_ENV", "/app/data/startup.sh"),
            ("ENV", "/app/data/startup.sh"),
            ("SHELLOPTS", "xtrace"),
            ("BASH_FUNC_run%%", "() { /app/data/evil; }"),
        ):
            hostile = safe + (key + "=" + value + "\n").encode()
            with mock.patch.object(helper, "_read_root_owned", return_value=hostile):
                with self.subTest(key=key):
                    with self.assertRaises(helper.CustodyHold) as caught:
                        helper._verify_root_sealed_env(
                            self.sealed_env + "observer.env",
                            hashlib.sha256(hostile).hexdigest())
                    self.assertIn(caught.exception.reason,
                                  ("ENV_CONTROL_KEY_FORBIDDEN", "ENV_SEAL_KEY_UNSAFE"))

    def test_source_closure_cannot_hide_mutable_code_mounts(self):
        for role in ("dashboard", "observer"):
            for target in ("/app/cf_intraday_scalping.py", "/app",
                           "/app/co_market_sessions_hf6.py"):
                profile = json.loads(json.dumps(self.profile))
                profile["roles"][role]["mounts"].append({
                    "source": "/opt/porota-trading/custom-runtime",
                    "target": target, "readonly": False})
                with self.subTest(role=role, target=target):
                    with self.assertRaises(helper.CustodyHold):
                        self.approved(profile)

    def test_profiles_reject_foreign_mounts_and_real_order_flags(self):
        bad_mount = json.loads(json.dumps(self.profile))
        bad_mount["roles"]["dashboard"]["mounts"][0]["source"] = "/opt/boca/data"
        with self.assertRaises(helper.CustodyHold):
            self.approved(bad_mount)
        bad_env = json.loads(json.dumps(self.profile))
        bad_env["roles"]["observer"]["environment"]["REAL_ORDERS_ALLOWED"] = "true"
        with self.assertRaises(helper.CustodyHold):
            self.approved(bad_env)
        bad_scheme = json.loads(json.dumps(self.profile))
        bad_scheme["root_stage"] = "/"
        with self.assertRaises(helper.CustodyHold):
            self.approved(bad_scheme)
        old_env_source = json.loads(json.dumps(self.profile))
        old_env_source["roles"]["observer"]["env_file"] = (
            "/opt/porota-trading/data/diagnosticos/observer_runtime_v17.env")
        with self.assertRaises(helper.CustodyHold):
            self.approved(old_env_source)
        for role in ("dashboard", "observer", "critical"):
            omitted = json.loads(json.dumps(self.profile))
            omitted["roles"][role]["environment"].pop("POROTA_RUNTIME_MODE")
            with self.subTest(role=role, key="POROTA_RUNTIME_MODE"):
                with self.assertRaises(helper.CustodyHold):
                    self.approved(omitted)
            omitted = json.loads(json.dumps(self.profile))
            omitted["roles"][role]["environment"].pop("PPI_ORDERS")
            with self.subTest(role=role, key="PPI_ORDERS"):
                with self.assertRaises(helper.CustodyHold):
                    self.approved(omitted)

    def test_envfile_checksum_and_live_order_injection_hold_before_docker(self):
        safe = (b"POROTA_RUNTIME_MODE=PRODUCTION_PAPER\nPPI_ORDERS=BLOCKED\n"
                b"REAL_ORDERS_ALLOWED=false\nMARKET_OPEN_HOUR=10\n"
                b"MARKET_OPEN_MINUTE=30\n")
        expected = hashlib.sha256(safe).hexdigest()
        with mock.patch.object(helper, "_read_root_owned", return_value=safe):
            helper._verify_root_sealed_env(self.sealed_env + "observer.env", expected)
            with self.assertRaises(helper.CustodyHold):
                helper._verify_root_sealed_env(self.sealed_env + "observer.env", "0" * 64)
        injected = safe + b"PPI_LIVE_TRADING=true\n"
        with mock.patch.object(helper, "_read_root_owned", return_value=injected):
            with self.assertRaises(helper.CustodyHold):
                helper._verify_root_sealed_env(
                    self.sealed_env + "observer.env", hashlib.sha256(injected).hexdigest())
        injected = safe + b"PPI_REAL_ORDERS_SEND=true\n"
        with mock.patch.object(helper, "_read_root_owned", return_value=injected):
            with self.assertRaises(helper.CustodyHold):
                helper._verify_root_sealed_env(
                    self.sealed_env + "observer.env", hashlib.sha256(injected).hexdigest())

    def test_root_binding_reuses_a1079_session_and_approved_profile_only(self):
        approval = HostHelperIndependentApprovalTest()
        approval.setUp()
        approved = approval.validate()
        fields = ("schema_version", "candidate_sha", "candidate_tree", "artifact_digest",
                  "image_id", "daemon_id", "generation", "run_id", "run_attempt",
                  "paper_mode", "real_orders_allowed")
        genesis = {field: approved[field] for field in fields}
        genesis.update({"state": "LEGACY_UNTRUSTED",
                        "authority": "INDEPENDENT_HOST_CONTROL_PLANE"})
        session = _Session()
        custody = _Custody(session)
        custody.DOCKER_BINARY = "/usr/bin/docker"
        custody.DOCKER_ENDPOINT = "unix:///var/run/docker.sock"
        custody.TRUSTED_ROOT = helper.Path("/var/lib/porota-rc6-oci-custody")
        custody._read_trusted_genesis = lambda: genesis
        custody._docker_run = lambda command, capture=False: None
        result = helper._bind_approved_authority(
            approved, json.dumps(self.profile).encode(), custody)
        output = result.dispatch(helper.parse_command(("manager", "begin")))
        self.assertTrue(output.startswith("RC6_CUSTODY=GENESIS\nRC6_CUSTODY_ATTEMPT="))
        self.assertEqual(custody.calls, ["genesis"])


if __name__ == "__main__":
    unittest.main()
