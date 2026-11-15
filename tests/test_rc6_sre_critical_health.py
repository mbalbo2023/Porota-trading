"""Guard the critical gateway against dashboard health and PID 1 zombie leaks."""

import re
import shlex
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "ops" / "rc6_deploy_critical_control_plane.sh"
REPAIR = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "rc6_sre_critical_repair.py"


class CriticalHealthContract(unittest.TestCase):
    def test_staged_gateway_delegates_critical_start_to_root_fenced_helper(self):
        source = SOURCE.read_text(encoding="utf-8")
        line = next(
            line.partition("ExecStart=")[2]
            for line in source.splitlines()
            if line.startswith("ExecStart=/usr/local/sbin/porota-rc6-oci-custody ")
        )
        args = shlex.split(line)
        self.assertEqual(args[:3], [
            "/usr/local/sbin/porota-rc6-oci-custody", "critical", "start-newborn",
        ])
        self.assertIn("--paper-only", args)
        self.assertIn("--source-sha", args)
        self.assertIn("--generation", args)
        self.assertIn("Type=oneshot", source)
        self.assertIn("RemainAfterExit=yes", source)
        self.assertIn("CRITICAL_UNIT=STAGED_HOLD79", source)
        self.assertNotIn("CRITICAL_HEALTH_AND_INIT=GREEN", source)

    def test_host_patch_is_exact_and_rejects_reapply(self):
        spec = importlib.util.spec_from_file_location("sre_critical_repair", REPAIR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        raw = (b"ExecStart=/usr/bin/docker run --pull never --read-only "
               b"--user botuser --cap-drop ALL --pids-limit 64 --memory 192m "
               b"-e POROTA_RUNTIME_MODE=PRODUCTION_PAPER "
               b"--health-cmd=\"python -c 'import socket; socket.socket(); # github.sock'\" image cmd\n")
        modified = module.patch_unit(raw)
        self.assertEqual(modified.count(b"--init"), 1)
        self.assertEqual(modified.count(b"--health-cmd"), 1)
        argv = shlex.split(modified.decode().split("=", 1)[1])
        command = next(a.split("=", 1)[1] for a in argv if a.startswith("--health-cmd="))
        self.assertIn("github.sock", command)
        self.assertIn("socket", command)
        with self.assertRaisesRegex(RuntimeError, "UNIT_SOURCE_MISMATCH"):
            module.patch_unit(modified)
        with self.assertRaisesRegex(RuntimeError, "UNIT_EXECSTART_COUNT"):
            module.patch_unit(raw + raw)


class CriticalDockerCustodyContract(unittest.TestCase):
    """Off-host regressions for the legacy critical unit's destructive paths."""

    def _render_gateway_unit(self):
        # Execute the actual shell heredoc in a temporary directory.  This
        # checks the emitted systemd directives after shell variable expansion,
        # rather than searching for a particular line in the source template.
        source = SOURCE.read_text(encoding="utf-8")
        match = re.search(
            r"(?ms)^cat > \"\$STAGED_GATEWAY_UNIT\" <<UNIT\n"
            r"(?P<body>.*?)\nUNIT$",
            source,
        )
        self.assertIsNotNone(match, "critical systemd unit heredoc not found")
        with tempfile.TemporaryDirectory(prefix="rc6-critical-unit-") as root:
            target = Path(root) / "gateway.service"
            env = os.environ.copy()
            env.update({
                "RC6_TEST_UNIT_PATH": str(target),
                "GATEWAY_CONTAINER": "porota_critical_approval_rc6",
                "LIVE_IMAGE": "test-paper-image:immutable",
                "CIDFILE": str(Path(root) / "new-generation" / "critical.cid"),
                "SOURCE_SHA": "a" * 40,
                "cid_generation": "c" * 32,
            })
            command = 'cat > "$RC6_TEST_UNIT_PATH" <<UNIT\n' + match["body"] + "\nUNIT\n"
            result = subprocess.run(
                ["bash", "-c", command],
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return target.read_text(encoding="utf-8")

    @staticmethod
    def _directives(unit, key):
        return [
            line.partition("=")[2]
            for line in unit.splitlines()
            if line.startswith(f"{key}=")
        ]

    def test_missing_trusted_custody_holds_before_sudo_or_exit_cleanup(self):
        # No role receipts, trusted authority or birth CID are supplied.  The
        # script must stop at its first gate (79), before installing an EXIT
        # trap that could remove stage data or invoking a privileged command.
        with tempfile.TemporaryDirectory(prefix="rc6-critical-hold-") as root:
            root_path = Path(root)
            bin_dir = root_path / "bin"
            bin_dir.mkdir()
            calls = root_path / "host_calls.log"
            for name in ("sudo", "docker", "systemctl"):
                stub = bin_dir / name
                stub.write_text(
                    '#!/bin/sh\nprintf "%s\\n" "' + name +
                    ' $*" >> "$RC6_TEST_HOST_CALLS"\nexit 97\n',
                    encoding="utf-8",
                )
                stub.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
                "RC6_TEST_HOST_CALLS": str(calls),
                "SOURCE_SHA": "a" * 40,
                "REMOTE_STAGE": str(root_path / "unused-stage"),
            })
            result = subprocess.run(
                ["bash", str(SOURCE)],
                env=env,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            attempted_host_calls = calls.read_text(encoding="utf-8") if calls.exists() else ""
            self.assertEqual(
                (result.returncode, attempted_host_calls),
                (79, ""),
                result.stdout + result.stderr,
            )
            self.assertIn("HOLD", result.stdout + result.stderr)

    def test_rendered_unit_never_removes_a_container_by_name_on_start(self):
        unit = self._render_gateway_unit()
        for directive in self._directives(unit, "ExecStartPre"):
            self.assertNotRegex(
                directive,
                r"\bdocker\s+rm\b[^\n]*\bporota_critical_approval_rc6\b",
                "a name is not proof of critical-container birth or custody",
            )

    def test_rendered_unit_never_stops_a_container_by_name_on_stop(self):
        unit = self._render_gateway_unit()
        for directive in self._directives(unit, "ExecStop"):
            self.assertNotRegex(
                directive,
                r"\bdocker\s+stop\b[^\n]*\bporota_critical_approval_rc6\b",
                "the legacy unit must not stop an unproven name collision",
            )

    def test_staged_critical_start_never_executes_docker_without_fencer(self):
        unit = self._render_gateway_unit()
        starts = self._directives(unit, "ExecStart")
        self.assertEqual(len(starts), 1)
        argv = shlex.split(starts[0])
        self.assertEqual(argv[:3], [
            "/usr/local/sbin/porota-rc6-oci-custody", "critical", "start-newborn",
        ])
        self.assertIn("--paper-only", argv)
        self.assertIn("--source-sha", argv)
        self.assertIn("--generation", argv)
        self.assertNotIn("docker run", unit)
        self.assertNotIn("/usr/bin/docker", unit)

    def test_staged_unit_cannot_create_containers_directly(self):
        unit = self._render_gateway_unit()
        starts = self._directives(unit, "ExecStart")
        self.assertEqual(len(starts), 1)
        argv = shlex.split(starts[0])
        self.assertEqual(argv[:3], [
            "/usr/local/sbin/porota-rc6-oci-custody", "critical", "start-newborn",
        ])
        self.assertNotIn("--cidfile", argv)
        self.assertNotIn("--name", argv)
        # CIDFILE birth and labels belong exclusively to root-owned `critical
        # birth`, not to the systemd stage protector. No hook can fake a birth.
        for key in ("ExecStartPre", "ExecStop", "ExecStopPost"):
            for directive in self._directives(unit, key):
                self.assertNotIn("docker", directive)
                self.assertNotRegex(directive, r"\b(?:rm|unlink|truncate)\b")

    def test_legacy_transition_must_only_stage_and_not_activate_gateway(self):
        # Replacing a unit file plus daemon-reload is staging.  In particular,
        # 'enable --now' can start an old unproven generation after it exits.
        # Until independent 3/3 custody exists, neither it nor stop/restart is
        # authorized for the preexisting critical service.
        source = SOURCE.read_text(encoding="utf-8")
        transition = re.sub(r"(?ms)^cat > .*? <<UNIT\n.*?\nUNIT$", "", source)
        forbidden = re.findall(
            r"(?m)^\s*(?:sudo -n )?systemctl\s+"
            r"(?:stop|restart|try-restart|start|enable\s+--now|disable\s+--now)\b"
            r"[^\n]*porota-critical-approval-rc6\.service",
            transition,
        )
        self.assertEqual(forbidden, [], "the staged unit must not be activated")
        self.assertIn("critical stage-protector", transition)
        self.assertNotRegex(transition, r"(?m)^\s*systemctl daemon-reload\s*$")
        self.assertIn("systemctl cat --no-pager", transition)
        self.assertIn("--property=ExecCondition --value", transition)

    def test_staged_unit_unconditionally_holds_before_docker_run(self):
        unit = self._render_gateway_unit()
        conditions = self._directives(unit, "ExecCondition")
        self.assertEqual(len(conditions), 1)
        condition = shlex.split(conditions[0])
        self.assertEqual(condition[:2], ["/bin/sh", "-c"])
        result = subprocess.run(
            condition, capture_output=True, text=True, timeout=5, check=False,
        )
        self.assertEqual(result.returncode, 79)
        self.assertIn("RC6_OCI_CUSTODY_HOLD79", result.stderr)
        self.assertEqual(self._directives(unit, "Restart"), ["no"])

    def test_future_cidfile_reservation_is_not_generated_locally(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn('cid_generation="$(python3 ', source)
        self.assertIn("critical describe-stage-intent", source)


class CriticalLegacyBootstrapRedContract(unittest.TestCase):
    """Desired V3 staging behavior before any legacy CID can be adopted.

    The genuine deployment script runs in a confined temporary directory.
    Only its absolute custody/lock/unit paths are redirected; sudo, stat,
    Docker, and systemctl are harmless, recording test executables.
    """

    GENESIS = "c" * 32

    def _probe(self, *, verify_three_exit=79, intent_override="", stage_write=True,
               stage_helper_exit=0, changed_mainpid=False):
        with tempfile.TemporaryDirectory(prefix="rc6-critical-bootstrap-") as root:
            place = Path(root)
            commands = place / "commands.log"
            captured = place / "captured-unit.service"
            cli = place / "trusted-custody"
            cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            cli.chmod(0o755)
            bin_dir = place / "bin"
            bin_dir.mkdir()
            (bin_dir / "stat").write_text(
                '#!/bin/sh\nprintf "0:1:755\\n"\n', encoding="utf-8")
            (bin_dir / "sudo").write_text(
                '#!/bin/sh\n'
                'printf "sudo %s\\n" "$*" >> "$RC6_TEST_CALLS"\n'
                'case " $* " in\n'
                '  *" critical describe-stage-intent "*)\n'
                '    if [ -n "$RC6_TEST_INTENT_OVERRIDE" ]; then '
                'printf "%s\\n" "$RC6_TEST_INTENT_OVERRIDE"; exit 0; fi\n'
                '    printf "RC6_STAGE_INTENT_V1 %s 424242 1 sha256:%s porota_rc6_critical_g_%s\\n" '
                '"$RC6_TEST_TRUSTED_GENERATION" '
                '"$(printf f%.0s $(seq 1 64))" '
                '"$RC6_TEST_TRUSTED_GENERATION"; exit 0;;\n'
                '  *" critical stage-protector "*)\n'
                '    if [ "$RC6_TEST_STAGE_EXIT" != 0 ]; then '
                'exit "$RC6_TEST_STAGE_EXIT"; fi\n'
                '    previous=""; staged=""\n'
                '    for arg do\n'
                '      if [ "$previous" = "--unit-template" ]; then staged="$arg"; break; fi\n'
                '      previous="$arg"\n'
                '    done\n'
                '    test -f "$staged" || exit 98\n'
                '    if [ "$RC6_TEST_STAGE_WRITE" = 1 ]; then '
                'cp "$staged" "$RC6_TEST_CAPTURE"; fi\n'
                '    touch "$RC6_TEST_STAGE_MARKER"\n'
                '    exit 0;;\n'
                'esac\n'
                'exit 0\n', encoding="utf-8")
            (bin_dir / "systemctl").write_text(
                '#!/bin/sh\n'
                'printf "systemctl %s\\n" "$*" >> "$RC6_TEST_CALLS"\n'
                'if [ "$1" = "cat" ]; then cat "$RC6_TEST_CAPTURE"; exit; fi\n'
                'case " $* " in\n'
                '  *" --property=ActiveState,MainPID "*)\n'
                '    pid=4242\n'
                '    if [ "$RC6_TEST_CHANGE_MAINPID" = 1 ] && '
                '[ -f "$RC6_TEST_STAGE_MARKER" ]; then pid=9999; fi\n'
                '    printf "ActiveState=active\\nMainPID=%s\\n" "$pid";;\n'
                '  *" --property=FragmentPath "*) printf "%s\\n" "$RC6_TEST_GATEWAY_UNIT";;\n'
                '  *" --property=ExecCondition "*) printf "RC6_OCI_CUSTODY_HOLD79\\n";;\n'
                'esac\n', encoding="utf-8")
            (bin_dir / "docker").write_text(
                '#!/bin/sh\nprintf "docker %s\\n" "$*" >> "$RC6_TEST_CALLS"\nexit 97\n',
                encoding="utf-8")
            for executable in bin_dir.iterdir():
                executable.chmod(0o755)

            source = SOURCE.read_text(encoding="utf-8")
            # Redirect only trusted absolute paths, never weaken the script's
            # birth, sudo, systemd, or Docker control flow under test.
            source = source.replace(
                'CUSTODY_CLI=/usr/local/sbin/porota-rc6-oci-custody',
                'CUSTODY_CLI="$RC6_TEST_CUSTODY_CLI"')
            source = source.replace(
                'CUSTODY_LOCK=/var/lib/porota-rc6-oci-custody/lock',
                'CUSTODY_LOCK="$RC6_TEST_CUSTODY_LOCK"')
            source = source.replace(
                'GATEWAY_UNIT=/etc/systemd/system/porota-critical-approval-rc6.service',
                'GATEWAY_UNIT="$RC6_TEST_GATEWAY_UNIT"')
            script = place / "script.sh"
            script.write_text(source, encoding="utf-8")
            env = os.environ.copy()
            env.update({
                "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
                "SOURCE_SHA": "a" * 40,
                "REMOTE_STAGE": str(place / "unused-stage"),
                "RC6_TEST_CUSTODY_CLI": str(cli),
                "RC6_TEST_CUSTODY_LOCK": str(place / "lock"),
                "RC6_TEST_GATEWAY_UNIT": str(place / "unit.service"),
                "RC6_TEST_CALLS": str(commands),
                "RC6_TEST_CAPTURE": str(captured),
                "RC6_TEST_VERIFY_EXIT": str(verify_three_exit),
                "RC6_TEST_TRUSTED_GENERATION": self.GENESIS,
                "RC6_TEST_INTENT_OVERRIDE": intent_override,
                "RC6_TEST_STAGE_WRITE": "1" if stage_write else "0",
                "RC6_TEST_STAGE_EXIT": str(stage_helper_exit),
                "RC6_TEST_STAGE_MARKER": str(place / "stage-completed"),
                "RC6_TEST_CHANGE_MAINPID": "1" if changed_mainpid else "0",
            })
            result = subprocess.run(
                ["bash", str(script)], cwd=place, env=env,
                capture_output=True, text=True, timeout=15, check=False)
            return (result, commands.read_text(encoding="utf-8")
                    if commands.exists() else "",
                    captured.read_text(encoding="utf-8")
                    if captured.exists() else "")

    def test_protector_stages_before_three_births_exist(self):
        # The existing critical process needs a safe unit before any genesis
        # can possibly supply three fresh CIDs. A failed 3-role proof is not
        # permission to operate Docker, but must not block passive staging.
        result, calls, _ = self._probe(verify_three_exit=79)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CRITICAL_UNIT=STAGED_HOLD79", result.stdout)
        self.assertNotIn("verify-three-roles", calls)
        self.assertNotIn("docker ", calls)
        self.assertIn("critical stage-protector", calls)

    def test_staging_does_not_execute_checkout_shell_as_root(self):
        result, calls, _ = self._probe(verify_three_exit=0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotRegex(calls, r"sudo [^\n]*\bflock\b[^\n]*/bin/bash\s+-s\b")
        self.assertNotRegex(calls, r"sudo [^\n]*\b(?:install|tee|cp)\b")

    def test_critical_stage_exposes_exact_intent_not_untrusted_labels(self):
        unit = CriticalDockerCustodyContract()._render_gateway_unit()
        argv = shlex.split(CriticalDockerCustodyContract._directives(unit, "ExecStart")[0])
        self.assertEqual(argv[:3], [
            "/usr/local/sbin/porota-rc6-oci-custody", "critical", "start-newborn",
        ])
        self.assertNotIn("--label", argv)
        self.assertEqual(argv[argv.index("--source-sha") + 1], "a" * 40)

    def test_critical_start_uses_authoritative_generation_not_local_random(self):
        result, _, unit = self._probe(verify_three_exit=0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(unit, "staged unit was not captured")
        argv = shlex.split(CriticalDockerCustodyContract._directives(unit, "ExecStart")[0])
        generation = argv[argv.index("--generation") + 1]
        self.assertEqual(generation, self.GENESIS)
        self.assertNotIn("--cidfile", argv)

    def test_mainpid_guard_precedes_any_three_birth_gate_or_service_lifecycle(self):
        result, calls, _ = self._probe(verify_three_exit=79)
        self.assertIn("systemctl show --property=ActiveState,MainPID", calls,
                      "legacy MainPID must be observed before staging authority")
        self.assertNotRegex(calls, r"systemctl (?:start|stop|restart|try-restart|enable --now)\b")
        self.assertNotIn("docker ", calls)
        self.assertIn("CRITICAL_UNIT=STAGED_HOLD79", result.stdout)

    def test_staging_receipt_exposes_critical_availability_risk(self):
        # ExecCondition=79 + Restart=no means an organically exited legacy
        # gateway stays down until a separately authorized cutover. Emit a
        # machine-readable warning, never a false no-downtime success claim.
        result, _, _ = self._probe(verify_three_exit=0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CRITICAL_AVAILABILITY=GAP_IF_LEGACY_EXITS", result.stdout)

    def test_staging_helper_never_accepts_untrusted_digest_as_authority(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("INDEPENDENT_TEMPLATE_ALLOWLIST_REQUIRED", source)
        self.assertNotRegex(source, r"sudo -n flock[^\n]*/bin/bash -s")

    def test_invalid_root_intent_never_stages_or_mutates_docker(self):
        result, calls, _ = self._probe(intent_override=(
            "RC6_STAGE_INTENT_V1 ../../legacy 424242 1 "
            "sha256:" + "f" * 64 + " porota_critical_approval_rc6"))
        self.assertEqual(result.returncode, 79)
        self.assertIn("TRUSTED_INTENT_IDENTITY", result.stderr)
        self.assertNotIn("critical stage-protector", calls)
        self.assertNotIn("docker ", calls)

    def test_no_partial_success_if_root_helper_rejects_template(self):
        result, calls, _ = self._probe(stage_helper_exit=79)
        self.assertEqual(result.returncode, 79)
        self.assertIn("UNIT_STAGE_OR_EFFECTIVE_STATE_UNVERIFIED", result.stderr)
        self.assertIn("critical stage-protector", calls)
        self.assertNotIn("docker ", calls)
        self.assertNotIn("CRITICAL_UNIT=STAGED_HOLD79", result.stdout)

    def test_helper_success_without_effective_safe_unit_is_rejected(self):
        result, calls, _ = self._probe(stage_write=False)
        self.assertEqual(result.returncode, 79)
        self.assertIn("EFFECTIVE_UNIT_UNREADABLE", result.stderr)
        self.assertIn("critical stage-protector", calls)
        self.assertNotIn("docker ", calls)

    def test_original_legacy_pid_changing_during_stage_is_rejected(self):
        result, calls, _ = self._probe(changed_mainpid=True)
        self.assertEqual(result.returncode, 79)
        self.assertIn("LEGACY_MAINPID_CHANGED", result.stderr)
        self.assertIn("--expected-mainpid 4242", calls)
        self.assertNotIn("docker ", calls)


if __name__ == "__main__":
    unittest.main()
