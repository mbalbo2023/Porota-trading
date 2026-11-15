"""A1075: mode manager may call only a fixed, root-owned OCI custody helper.

All tests use a fake process boundary. No Docker daemon, sudo, Telegram or PPI
call is allowed during the suite. The helper itself has separate A1092 tests.
"""

from contextlib import redirect_stdout
from io import StringIO
import fcntl
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import porota_mode_manager as manager


SHA = "a" * 40
TAG = "porota-trading-bot:17.0.0-rc6-candidate-" + SHA
IMAGE_ID = "sha256:" + "b" * 64
ATTEMPT = "e" * 32
ROOT_HELPER = "/usr/local/sbin/porota-rc6-oci-custody"


def fake_session():
    """The public, unprivileged manager-facing contract (not fake Docker)."""
    return SimpleNamespace(
        verify_candidate=Mock(),
        retire=Mock(),
        birth=Mock(),
        verify_observer_sources=Mock(),
        stop_observer=Mock(),
    )


def helper_process(stdout="RC6_CUSTODY=OK\n", returncode=0):
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr="")


class FixedRootHelperCLITests(unittest.TestCase):
    """A typed client cannot smuggle argv, daemon context or a Docker CID."""

    def test_uses_exact_sudo_n_helper_path_and_scrubbed_environment(self):
        with patch.dict(os.environ, {
                 "DOCKER_HOST": "unix:///run/user/1000/docker.sock",
                 "DOCKER_CONTEXT": "boca-rootless",
                 "SUDO_ASKPASS": "/tmp/bad-helper",
                 "DOCKER_CONFIG": "/tmp/rootless-config",
             }), patch("subprocess.run",
                       return_value=helper_process("RC6_CUSTODY=GENESIS\n"
                                                   "RC6_CUSTODY_ATTEMPT=" + ATTEMPT + "\n")) as process:
            self.assertEqual(manager._helper_call("manager", "begin"),
                             "RC6_CUSTODY=GENESIS\nRC6_CUSTODY_ATTEMPT=" + ATTEMPT)
        command = process.call_args.args[0]
        kwargs = process.call_args.kwargs
        self.assertEqual(command, ("/usr/bin/sudo", "-n", ROOT_HELPER,
                                   "manager", "begin"))
        self.assertNotIn("DOCKER_HOST", kwargs["env"])
        self.assertNotIn("DOCKER_CONTEXT", kwargs["env"])
        self.assertNotIn("DOCKER_CONFIG", kwargs["env"])
        self.assertNotIn("SUDO_ASKPASS", kwargs["env"])
        self.assertEqual(kwargs["cwd"], "/")
        self.assertFalse(kwargs["check"])

    def test_verbs_whitelist_rejects_raw_docker_flags(self):
        bad = (
            ("manager", "birth", "dashboard", "--privileged"),
            ("manager", "birth", "boca"),
            ("manager", "retire", "critical"),
            ("manager", "stop", "dashboard"),
            ("manager", "docker", "rm", "-f", "porota_production_observer"),
            ("manager", "verify-image", "../image", IMAGE_ID),
            ("manager", "verify-image", TAG, "sha256:wrong"),
            ("manager", "begin", "--context", "boca-rootless"),
        )
        with patch("subprocess.run") as process:
            for arguments in bad:
                with self.subTest(arguments=arguments):
                    with self.assertRaises(SystemExit) as raised:
                        manager._helper_call(*arguments, attempt=ATTEMPT)
                    self.assertEqual(raised.exception.code, 79)
        process.assert_not_called()

    def test_accepted_birth_invocation_has_exactly_one_fixed_role(self):
        with patch("subprocess.run", return_value=helper_process()) as process:
            manager._helper_call("manager", "birth", "observer", attempt=ATTEMPT)
        self.assertEqual(process.call_args.args[0][-5:],
                         ("manager", "birth", "observer", "--attempt", ATTEMPT))
        self.assertEqual(len(process.call_args.args[0]), 8)

    def test_missing_or_invalid_attempt_refuses_mutation_before_helper(self):
        with patch("subprocess.run") as process:
            for invalid in (None, "short", "../../run", "x" * 32):
                with self.subTest(attempt=invalid):
                    with self.assertRaises(SystemExit) as raised:
                        manager._helper_call("manager", "birth", "observer", attempt=invalid)
                    self.assertEqual(raised.exception.code, 79)
        process.assert_not_called()

    def test_missing_helper_holds_without_alternate_docker_fallback(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("missing")) as process:
            with self.assertRaises(SystemExit) as raised:
                manager._helper_call("manager", "begin")
        self.assertEqual(raised.exception.code, 79)
        self.assertEqual(process.call_count, 1)

    def test_timeout_holds_instead_of_retrying_or_releasing_lock(self):
        from subprocess import TimeoutExpired
        with patch("subprocess.run",
                   side_effect=TimeoutExpired("helper", 120)) as process:
            with self.assertRaises(SystemExit) as raised:
                manager._helper_call("manager", "birth", "observer", attempt=ATTEMPT)
        self.assertEqual(raised.exception.code, 79)
        self.assertEqual(process.call_count, 1)

    def test_nonzero_or_noncanonical_stdout_holds_and_never_exposes_cid(self):
        samples = (
            helper_process("", 1),
            helper_process("legacy-container-ID\n"),
            helper_process("RC6_CUSTODY=OK\n" + "c" * 64 + "\n"),
            helper_process("RC6_CUSTODY=GENESIS\n" + "d" * 64 + "\n"),
        )
        for response in samples:
            with self.subTest(stdout=response.stdout), \
                 patch("subprocess.run", return_value=response), \
                 redirect_stdout(StringIO()) as printed:
                with self.assertRaises(SystemExit) as raised:
                    manager._helper_call("manager", "begin")
                self.assertEqual(raised.exception.code, 79)
                self.assertEqual(printed.getvalue(), "")

    def test_begin_status_cannot_be_faked_by_generic_ok(self):
        with patch("subprocess.run", return_value=helper_process()):
            with self.assertRaises(SystemExit) as raised:
                manager._begin_custody()
        self.assertEqual(raised.exception.code, 79)

    def test_legacy_docker_runner_remains_hold_even_if_called_as_root(self):
        with patch("os.geteuid", return_value=0), \
             patch("subprocess.run") as process:
            with self.assertRaises(SystemExit) as raised:
                manager.run("docker", "rm", "-f", "porota_production_dashboard")
        self.assertEqual(raised.exception.code, 79)
        process.assert_not_called()


class CustodyLifecycleTests(unittest.TestCase):
    def test_genesis_never_sends_retire_of_legacy_named_objects(self):
        verbs = []

        def host(*tokens, attempt=None):
            verbs.append((tokens, attempt))
            return ("RC6_CUSTODY=GENESIS\nRC6_CUSTODY_ATTEMPT=" + ATTEMPT
                    if tokens == ("manager", "begin") else "RC6_CUSTODY=OK")

        with patch.object(manager, "_helper_call", side_effect=host):
            session = manager._begin_custody()
            manager.stop_engines(_session=session)
            session.birth("dashboard")
            session.birth("observer")
            session.verify_observer_sources()
        self.assertEqual(verbs, [
            (("manager", "begin"), None),
            (("manager", "birth", "dashboard"), ATTEMPT),
            (("manager", "verify-newborn", "dashboard"), ATTEMPT),
            (("manager", "birth", "observer"), ATTEMPT),
            (("manager", "verify-newborn", "observer"), ATTEMPT),
            (("manager", "verify-observer-sources"), ATTEMPT),
        ])

    def test_continuity_retires_only_two_roles_through_proven_helper(self):
        verbs = []

        def host(*tokens, attempt=None):
            verbs.append((tokens, attempt))
            return ("RC6_CUSTODY=CONTINUITY\nRC6_CUSTODY_ATTEMPT=" + ATTEMPT
                    if tokens == ("manager", "begin") else "RC6_CUSTODY=OK")

        with patch.object(manager, "_helper_call", side_effect=host):
            session = manager._begin_custody()
            manager.stop_engines(_session=session)
        self.assertEqual(verbs, [
            (("manager", "begin"), None),
            (("manager", "retire", "observer"), ATTEMPT),
            (("manager", "retire", "dashboard"), ATTEMPT),
        ])

    def test_changed_root_intent_aborts_birth_before_newborn_verification(self):
        commands = []

        def host(*tokens, attempt=None):
            commands.append((tokens, attempt))
            if tokens == ("manager", "begin"):
                return "RC6_CUSTODY=GENESIS\nRC6_CUSTODY_ATTEMPT=" + ATTEMPT
            if tokens == ("manager", "birth", "dashboard"):
                # The independently owned helper detects root-intent changed.
                if attempt != ATTEMPT:
                    raise AssertionError("attempt binding missing")
                raise SystemExit(79)
            raise AssertionError("birth must HOLD, not proceed to another verb")

        with patch.object(manager, "_helper_call", side_effect=host):
            session = manager._begin_custody()
            with self.assertRaises(SystemExit) as raised:
                session.birth("dashboard")
        self.assertEqual(raised.exception.code, 79)
        self.assertEqual(commands, [(("manager", "begin"), None),
                                    (("manager", "birth", "dashboard"), ATTEMPT)])

    def test_interleaved_root_attempt_holds_retire_before_any_birth(self):
        invocations = []
        alternate = "1" * 32

        def host(*tokens, attempt=None):
            invocations.append((tokens, attempt))
            if tokens == ("manager", "begin"):
                # Old client binds generation A; the next job advances root
                # intent to B before this client attempts a destructive step.
                return "RC6_CUSTODY=CONTINUITY\nRC6_CUSTODY_ATTEMPT=" + ATTEMPT
            if tokens == ("manager", "retire", "observer"):
                self.assertNotEqual(attempt, alternate)
                raise SystemExit(79)  # host holds changed intent
            raise AssertionError("old generation must not perform a birth")

        with patch.object(manager, "_helper_call", side_effect=host):
            session = manager._begin_custody()
            with self.assertRaises(SystemExit) as raised:
                manager.stop_engines(_session=session)
        self.assertEqual(raised.exception.code, 79)
        self.assertEqual(invocations,
                         [(("manager", "begin"), None),
                          (("manager", "retire", "observer"), ATTEMPT)])

    def test_emergency_stop_delegates_only_sealed_observer_by_helper(self):
        session = fake_session()
        with patch.object(manager, "notify", return_value="NO_CONFIGURADO"), \
             patch.object(manager, "write_mode") as mode_write, \
             patch("subprocess.run") as process:
            manager.stop(_session=session)
        session.stop_observer.assert_called_once_with()
        session.retire.assert_not_called()
        session.birth.assert_not_called()
        self.assertEqual(mode_write.call_args.args[:3],
                         ("DETENIDO", "NONE", "NONE"))
        process.assert_not_called()

    def test_dashboard_birth_accepts_no_candidate_supplied_docker_configuration(self):
        session = fake_session()
        with patch.object(manager, "dashboard_env",
                          return_value=Path("/data/dashboard_mode_v17.env")) as env, \
             patch("subprocess.run") as process:
            manager.start_dashboard("PRODUCTION_PAPER", image=TAG,
                                    _session=session, _prior_retired=True)
        env.assert_called_once_with("PRODUCTION_PAPER")
        session.birth.assert_called_once_with("dashboard")
        session.retire.assert_not_called()
        process.assert_not_called()

    def test_direct_dashboard_birth_requires_retirement_in_continuity(self):
        session = fake_session()
        with patch.object(manager, "dashboard_env",
                          return_value=Path("/data/dashboard_mode_v17.env")):
            manager.start_dashboard("PRODUCTION_PAPER", _session=session)
        session.retire.assert_called_once_with("dashboard")
        session.birth.assert_called_once_with("dashboard")

    def test_legacy_sandbox_and_detained_dashboard_birth_are_denied(self):
        session = fake_session()
        with patch.object(manager, "dashboard_env") as env:
            for mode in ("SANDBOX", "DETENIDO", "PRODUCTION_REAL"):
                with self.subTest(mode=mode):
                    with self.assertRaises(SystemExit) as raised:
                        manager.start_dashboard(mode, _session=session)
                    self.assertEqual(raised.exception.code, 79)
        env.assert_not_called()
        session.birth.assert_not_called()


class ImmutableCandidateTests(unittest.TestCase):
    def test_default_image_utility_does_not_query_docker(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("subprocess.run") as process:
            self.assertEqual(manager.simulation_image(), manager.IMAGE)
        process.assert_not_called()

    def test_simulation_requires_exact_frozen_image_when_bound_to_helper(self):
        session = fake_session()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as raised:
                manager.simulation_image(_session=session)
        self.assertEqual(raised.exception.code, 79)
        session.verify_candidate.assert_not_called()

    def test_malformed_candidate_rejected_without_host_or_Docker(self):
        cases = (
            {"POROTA_DEPLOY_CANDIDATE_IMAGE": "ppi-watch:current",
             "POROTA_DEPLOY_CANDIDATE_IMAGE_ID": IMAGE_ID},
            {"POROTA_DEPLOY_CANDIDATE_IMAGE": TAG,
             "POROTA_DEPLOY_CANDIDATE_IMAGE_ID": "short"},
        )
        for values in cases:
            with self.subTest(values=values), patch.dict(os.environ, values, clear=True), \
                 patch("subprocess.run") as process:
                with self.assertRaisesRegex(RuntimeError, "CANDIDATE_IMAGE_INVALID"):
                    manager.simulation_image(_session=fake_session())
            process.assert_not_called()

    def test_candidate_verification_only_uses_root_manifest_expectations(self):
        session = fake_session()
        with patch.dict(os.environ, {
                 "POROTA_DEPLOY_CANDIDATE_IMAGE": TAG,
                 "POROTA_DEPLOY_CANDIDATE_IMAGE_ID": IMAGE_ID,
             }, clear=True), patch("subprocess.run") as process:
            self.assertEqual(manager.simulation_image(_session=session), TAG)
        session.verify_candidate.assert_called_once_with(TAG, IMAGE_ID)
        process.assert_not_called()

    def test_helper_image_mismatch_holds_before_birth(self):
        session = fake_session()
        session.verify_candidate.side_effect = SystemExit(79)
        with patch.dict(os.environ, {
                 "POROTA_DEPLOY_CANDIDATE_IMAGE": TAG,
                 "POROTA_DEPLOY_CANDIDATE_IMAGE_ID": IMAGE_ID,
             }, clear=True):
            with self.assertRaises(SystemExit) as raised:
                manager.simulation_image(_session=session)
        self.assertEqual(raised.exception.code, 79)
        session.retire.assert_not_called()
        session.birth.assert_not_called()


class ManagerPaperModeTests(unittest.TestCase):
    def test_two_births_remain_pending_until_separate_critical_seal(self):
        session = fake_session()
        with tempfile.TemporaryDirectory(prefix="rc6-manager-tdd-") as folder:
            root = Path(folder)
            (root / ".secrets").mkdir()
            (root / ".secrets/ppi_production.json").write_text("{}")
            out = StringIO()
            with patch.dict(os.environ, {
                     "POROTA_DEPLOY_CANDIDATE_IMAGE": TAG,
                     "POROTA_DEPLOY_CANDIDATE_IMAGE_ID": IMAGE_ID,
                 }, clear=True), patch.object(manager, "ROOT", root), \
                 patch.object(manager, "env_file", return_value={}), \
                 patch.object(manager, "observer_runtime_env",
                              return_value=root / "observer.env"), \
                 patch.object(manager, "dashboard_env",
                              return_value=root / "dashboard.env"), \
                 patch.object(manager, "write_mode") as write_mode, \
                 patch.object(manager, "notify") as notification, \
                 patch("subprocess.run") as process, redirect_stdout(out):
                manager.simulation(_session=session)
        session.verify_candidate.assert_called_once_with(TAG, IMAGE_ID)
        self.assertEqual(session.retire.call_args_list,
                         [call("observer"), call("dashboard")])
        self.assertEqual(session.birth.call_args_list,
                         [call("dashboard"), call("observer")])
        session.verify_observer_sources.assert_called_once_with()
        self.assertEqual(write_mode.call_args.args[:3],
                         ("PENDING_CUSTODY_SEAL", "NONE", "NONE"))
        self.assertEqual(write_mode.call_args.kwargs["detail"], "Iniciando")
        notification.assert_not_called()
        process.assert_not_called()
        self.assertNotIn("POROTA_MODE=OK", out.getvalue())

    def test_missing_secret_holds_before_retire_or_birth(self):
        session = fake_session()
        with tempfile.TemporaryDirectory(prefix="rc6-manager-nosecret-") as folder:
            with patch.dict(os.environ, {
                     "POROTA_DEPLOY_CANDIDATE_IMAGE": TAG,
                     "POROTA_DEPLOY_CANDIDATE_IMAGE_ID": IMAGE_ID,
                 }, clear=True), patch.object(manager, "ROOT", Path(folder)), \
                 patch.object(manager, "env_file", return_value={}):
                with self.assertRaisesRegex(RuntimeError, "secreto productivo"):
                    manager.simulation(_session=session)
        session.retire.assert_not_called()
        session.birth.assert_not_called()

    def test_invalid_budget_selector_fails_before_host_birth(self):
        session = fake_session()
        with patch.object(manager, "env_file",
                          return_value={"POROTA_DYNAMIC_CAPACITY_MODE": "LIVE"}):
            with self.assertRaisesRegex(RuntimeError, "RC6_PPI_BUDGET_MODE_INVALID"):
                manager.simulation(_session=session)
        session.verify_candidate.assert_not_called()
        session.birth.assert_not_called()

    def test_source_hash_failure_does_not_publish_ready_or_trigger_cleanup(self):
        session = fake_session()
        session.verify_observer_sources.side_effect = SystemExit(79)
        with tempfile.TemporaryDirectory(prefix="rc6-manager-hash-") as folder:
            root = Path(folder)
            (root / ".secrets").mkdir()
            (root / ".secrets/ppi_production.json").write_text("{}")
            with patch.dict(os.environ, {
                     "POROTA_DEPLOY_CANDIDATE_IMAGE": TAG,
                     "POROTA_DEPLOY_CANDIDATE_IMAGE_ID": IMAGE_ID,
                 }, clear=True), patch.object(manager, "ROOT", root), \
                 patch.object(manager, "env_file", return_value={}), \
                 patch.object(manager, "observer_runtime_env",
                              return_value=root / "observer.env"), \
                 patch.object(manager, "dashboard_env",
                              return_value=root / "dashboard.env"), \
                 patch.object(manager, "write_mode"), \
                 patch.object(manager, "notify") as notification:
                with self.assertRaises(SystemExit) as raised:
                    manager.simulation(_session=session)
        self.assertEqual(raised.exception.code, 79)
        self.assertEqual(session.birth.call_count, 2)
        session.retire.assert_any_call("observer")
        notification.assert_not_called()

    def test_error_after_both_births_persists_only_pending_not_paper_ready(self):
        session = fake_session()
        session.verify_observer_sources.side_effect = SystemExit(79)
        with tempfile.TemporaryDirectory(prefix="rc6-pending-after-error-") as folder:
            root = Path(folder)
            data = root / "data"
            data.mkdir()
            (root / ".secrets").mkdir()
            (root / ".secrets/ppi_production.json").write_text("{}")
            mode_file = data / "operation_mode.json"
            with patch.dict(os.environ, {
                     "POROTA_DEPLOY_CANDIDATE_IMAGE": TAG,
                     "POROTA_DEPLOY_CANDIDATE_IMAGE_ID": IMAGE_ID,
                 }, clear=True), patch.object(manager, "ROOT", root), \
                 patch.object(manager, "DATA", data), \
                 patch.object(manager, "MODE_FILE", mode_file), \
                 patch.object(manager, "env_file", return_value={}), \
                 patch.object(manager, "observer_runtime_env",
                              return_value=root / "observer.env"), \
                 patch.object(manager, "dashboard_env",
                              return_value=root / "dashboard.env"), \
                 patch.object(manager, "notify") as notification:
                with self.assertRaises(SystemExit) as raised:
                    manager.simulation(_session=session)
                persisted = json.loads(mode_file.read_text())
        self.assertEqual(raised.exception.code, 79)
        self.assertEqual(persisted["mode"], "PENDING_CUSTODY_SEAL")
        self.assertEqual(persisted["execution"], "NONE")
        self.assertEqual(persisted["apis"]["PPI_ORDERS"], "BLOCKED")
        self.assertEqual(persisted["detail"], "Iniciando")
        notification.assert_not_called()

    def test_sandbox_holds_without_spawning_compose_or_creating_lock(self):
        with tempfile.TemporaryDirectory(prefix="rc6-sandbox-") as folder:
            data = Path(folder)
            with patch.object(manager, "DATA", data), \
                 patch("subprocess.run") as process:
                with self.assertRaises(SystemExit) as raised:
                    manager.main(["sandbox"])
            self.assertEqual(raised.exception.code, 79)
            self.assertFalse((data / "diagnosticos" / "porota_mode.lock").exists())
        process.assert_not_called()

    def test_real_production_disabled_before_any_root_helper_call(self):
        with patch("subprocess.run") as process:
            with self.assertRaisesRegex(RuntimeError, "PRODUCCION REAL DESHABILITADA"):
                manager.main(["production"])
        process.assert_not_called()

    def test_helper_missing_holds_before_local_mutation_lock(self):
        with tempfile.TemporaryDirectory(prefix="rc6-helper-hold-") as folder:
            data = Path(folder)
            with patch.object(manager, "DATA", data), \
                 patch.object(manager, "_begin_custody", side_effect=SystemExit(79)):
                with self.assertRaises(SystemExit) as raised:
                    manager.main(["simulation"])
            self.assertEqual(raised.exception.code, 79)
            # A harmless local lock file may remain, but no operational state
            # may be written when the independent host helper is missing.
            self.assertFalse((data / "operation_mode.json").exists())

    def test_begin_never_runs_before_local_mode_flock(self):
        with tempfile.TemporaryDirectory(prefix="rc6-flock-race-") as folder:
            data = Path(folder)
            lock_path = data / "diagnosticos" / "porota_mode.lock"
            lock_path.parent.mkdir(parents=True)
            with open(lock_path, "w") as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(manager, "DATA", data), \
                     patch.object(manager, "_begin_custody") as begin:
                    with self.assertRaises(BlockingIOError):
                        manager.main(["simulation"])
                begin.assert_not_called()

    def test_main_reports_pending_not_ready_after_two_births(self):
        session = fake_session()
        with tempfile.TemporaryDirectory(prefix="rc6-pending-mode-") as folder:
            out = StringIO()
            with patch.object(manager, "DATA", Path(folder)), \
                 patch.object(manager, "_begin_custody", return_value=session), \
                 patch.object(manager, "simulation") as simulation, \
                 patch.object(manager, "copy_output"), redirect_stdout(out):
                result = manager.main(["simulation"])
        self.assertEqual(result, 0)
        simulation.assert_called_once_with(_session=session)
        self.assertIn("POROTA_MODE=PENDING_CUSTODY_SEAL", out.getvalue())
        self.assertNotIn("POROTA_MODE=OK", out.getvalue())
        self.assertIn("DASHBOARD_24X7=NO_VERIFICADO", out.getvalue())
        self.assertNotIn("DASHBOARD_24X7=SI", out.getvalue())

    def test_main_status_does_not_assert_docker_runtime_from_local_manifest(self):
        with tempfile.TemporaryDirectory(prefix="rc6-readonly-status-") as folder:
            data = Path(folder)
            (data / "operation_mode.json").write_text(
                '{"mode":"PENDING_CUSTODY_SEAL","execution":"NONE"}')
            output = StringIO()
            with patch.object(manager, "DATA", data), \
                 patch.object(manager, "MODE_FILE", data / "operation_mode.json"), \
                 patch.object(manager, "copy_output"), \
                 patch("subprocess.run") as process, redirect_stdout(output):
                self.assertEqual(manager.main(["status"]), 0)
            self.assertIn("POROTA_MODE=NO_VERIFICADO", output.getvalue())
            self.assertIn("DASHBOARD_24X7=NO_VERIFICADO", output.getvalue())
            process.assert_not_called()

    def test_status_does_not_enumerate_other_users_docker_containers(self):
        with tempfile.TemporaryDirectory(prefix="rc6-status-") as folder:
            with patch.object(manager, "MODE_FILE", Path(folder) / "absent.json"), \
                 patch("subprocess.run") as process, redirect_stdout(StringIO()):
                manager.status()
        process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
