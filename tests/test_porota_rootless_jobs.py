"""Behavioral tests use explicit fake Docker/systemd, never a host daemon."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts import porota_rootless_host as host
from scripts import porota_rootless_jobs as jobs
from scripts import porota_rootless_setup as setup


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.identity = host.Identity(os.getuid(), os.getgid(), "porotaadmin", self.root / "home")
        host.private_dir(self.identity.state)
        with host.user_lock(self.identity.state / "deploy.lock"):
            pass
        self.current = {"source_sha": "a" * 40, "image_id": "sha256:" + "c" * 64,
                        "roles": {"observer": "1" * 64, "dashboard": "2" * 64}}
        self.calls = []
        self.manager = types.SimpleNamespace(
            docker=types.SimpleNamespace(call=lambda *args, **kwargs: self.calls.append(args)),
            check_role=lambda current, role: {"State": {"Running": True}})

    def test_postclose_preserves_three_commands_and_targets_only_exact_dashboard_cid(self):
        with patch.object(jobs, "verified_host", return_value=(self.manager, self.current, {})), \
             patch.object(jobs, "database_snapshot", return_value={"real_orders_sent": 0}):
            result = jobs.run_job("porota-snapshot-postclose-rc6.service", root=self.root, identity=self.identity)
        self.assertEqual([command[4] for command in self.calls], ["/app/rc6_snapshot.py",
            "/app/rc6_postclose_review.py", "/app/rc6_decision_evidence_report.py"])
        self.assertTrue(all(command[:3] == ("container", "exec", "2" * 64) for command in self.calls))
        self.assertEqual(result["state"], "EXECUTED")

    def test_preopen_uses_current_source_explicit_rootless_environment_and_shared_lock(self):
        source = self.identity.state / "releases" / self.current["source_sha"] / "source"
        host.private_dir(source)
        def run(command, **kwargs):
            self.assertEqual(command, ("/usr/bin/python3", "-B", str(source / "rc6_preopen.py"), "--publish-paper-gate"))
            self.assertEqual(kwargs["env"]["POROTA_RUNTIME_BACKEND"], "ROOTLESS")
            self.assertEqual(kwargs["env"]["PYTHONPATH"], str(source))
            self.assertEqual(kwargs["env"]["DOCKER_HOST"], f"unix://{self.identity.socket}")
            with self.assertRaisesRegex(host.Hold, "OWNER_BUSY"):
                with host.user_lock(self.identity.state / "deploy.lock"):
                    pass
            return subprocess.CompletedProcess(command, 0)
        with patch.object(jobs, "verified_host", return_value=(self.manager, self.current, {})), \
             patch.object(jobs, "database_snapshot", return_value={}):
            jobs.run_job(jobs.PREOPEN, root=self.root, identity=self.identity, run=run)

    def test_deploy_lock_prevents_any_job_or_docker_call(self):
        with host.user_lock(self.identity.state / "deploy.lock"), \
             patch.object(jobs, "verified_host") as verified, \
             self.assertRaisesRegex(host.Hold, "DEPLOY_IN_PROGRESS"):
            jobs.run_job(jobs.PREOPEN, root=self.root, identity=self.identity)
        verified.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_private_publisher_bridge_cannot_forward_arbitrary_docker_commands(self):
        valid = ["exec", "porota_production_observer", "python", "/app/ops_publish_introspection_rc6.py",
                 "--output-dir", "/app/data/introspection_publish", "--record-status", "PUBLISHED",
                 "--day", "2026-11-15", "--retention-days", "90"]
        self.assertEqual(jobs.publisher_command(valid), tuple(valid[2:]))
        for bad in (["rm", "foreign"], valid[:1] + ["foreign"] + valid[2:],
                    valid + ["--output-dir", "/app/data/other"], valid + ["--token", "private"]):
            with self.subTest(arguments=bad), self.assertRaisesRegex(host.Hold, "SCHEDULER_DOCKER_BRIDGE_SCOPE"):
                jobs.publisher_command(bad)

    def test_only_introspection_preserves_its_classified_critical_exit_two(self):
        def run(command, **kwargs):
            self.calls.append((command, kwargs["timeout"]))
            return subprocess.CompletedProcess(command, 2, "private report content", "")
        self.manager.docker = host.Docker(self.identity, run=run)
        with patch.object(jobs, "verified_host", return_value=(self.manager, self.current, {})), \
             patch.object(jobs, "database_snapshot", return_value={}):
            result = jobs.run_job(jobs.INTROSPECTION, root=self.root, identity=self.identity)
            self.assertEqual(result["exit_code"], 2)
            self.assertNotIn("private report", json.dumps(result))
            with self.assertRaisesRegex(host.Hold, "DOCKER_COMMAND_FAILED"):
                jobs.run_job("porota-candle-integrity-rc6.service", root=self.root, identity=self.identity)

    def test_full_integrity_preserves_its_existing_fifteen_minute_limit(self):
        def call(*command, **kwargs):
            self.assertEqual(kwargs["timeout"], 900)
        self.manager.docker.call = call
        with patch.object(jobs, "verified_host", return_value=(self.manager, self.current, {})), \
             patch.object(jobs, "database_snapshot", return_value={}):
            jobs.run_job("porota-full-db-integrity-rc6.service", root=self.root, identity=self.identity)

    def test_launcher_locks_before_loading_current_and_sanitizes_missing_receipt(self):
        account = types.SimpleNamespace(pw_uid=os.getuid(), pw_dir=str(self.identity.home))
        output = io.StringIO()
        namespace = {}
        with patch.object(setup.pwd, "getpwuid", return_value=account), redirect_stdout(output), \
             self.assertRaises(SystemExit) as stopped:
            exec(compile(jobs.LAUNCHER, "private-launcher", "exec"), namespace)
        self.assertEqual(stopped.exception.code, 79)
        self.assertEqual(json.loads(output.getvalue())["failure_signature"], "SCHEDULER_LAUNCHER_CUSTODY")
        self.assertNotIn(str(self.root), output.getvalue())
        os.close(namespace["lock"])
        output = io.StringIO()
        namespace = {}
        with host.user_lock(self.identity.state / "deploy.lock"), \
             patch.object(setup.pwd, "getpwuid", return_value=account), redirect_stdout(output), \
             self.assertRaises(SystemExit) as stopped:
            exec(compile(jobs.LAUNCHER, "private-launcher", "exec"), namespace)
        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(json.loads(output.getvalue())["state"], "SKIPPED")
        os.close(namespace["lock"])


class SchedulerMigrationTests(unittest.TestCase):
    """The fake host includes functional timers, companions and quarantined units."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.identity = host.Identity(os.getuid(), os.getgid(), "porotaadmin", self.root / "home")
        self.source = Path(__file__).resolve().parents[1]
        self.name = "porota-snapshot-postclose-rc6.service"
        self.timer = self.name.replace(".service", ".timer")
        self.fragment = "/etc/systemd/system/" + self.name
        self.files = {self.fragment: (self.source / "systemd" / self.name).read_bytes()}
        self.rows = [self.row(self.name, "ACTIVE_SERVICE", active="failed"),
                     self.row(self.timer, "ACTIVE_TIMER", active="active", enabled="enabled"),
                     self.row("porota-runtime-evidence-rc6.service", "ACTIVE_SERVICE", active="active"),
                     self.row("porota-iol-oauth-health-rc6.timer", "ACTIVE_TIMER", active="active", enabled="enabled"),
                     self.row("porota-critical-approval-rc6.service", "UNKNOWN", active="active", enabled="enabled"),
                     self.row("porota-critical-github-proxy-rc6.service", "UNKNOWN", active="active", enabled="enabled"),
                     self.row("porota-contract-evidence-rc6.service", "UNKNOWN"),
                     self.row("porota-history-postclose-rc6.service", "QUARANTINED_SERVICE")]
        self.rows[-1]["DropInPaths"] = "/etc/systemd/system/porota-history-postclose-rc6.service.d/90-pii-watch-isolation.conf"
        self.calls = []

    def row(self, name, role, active="inactive", enabled="static"):
        return {"name": name, "policy_role": role, "LoadState": "loaded", "ActiveState": active,
                "UnitFileState": enabled, "DropInPaths": "", "FragmentPath": "/etc/systemd/system/" + name}

    def fake_run(self, command, **kwargs):
        self.calls.append(command)
        output = ""
        if "/usr/bin/systemd-run" in command:
            self.assertIn("ProtectHome=tmpfs", command)
            self.assertIn("NoNewPrivileges=yes", command)
            output = json.dumps({"uid": self.identity.uid, "rootless": True})
        elif setup.INSTALL_UNITS in command:
            installed = []
            for item in json.loads(command[-2]):
                path = "/etc/systemd/system/" + item["unit"]
                self.assertEqual(self.digest(Path(path)), item["fragment_sha256"])
                self.files[path] = base64.b64decode(item["content"])
                installed.append({"unit": item["unit"], "fragment_sha256": self.digest(Path(path)),
                                  "preserved_dropins": item["dropins"]})
            output = json.dumps(installed)
        elif "show" in command:
            name = command[command.index("show") + 1]
            if "--property=User,Group,ProtectHome,DynamicUser,After,Wants,Requires" in command:
                output = "User=\nGroup=\nProtectHome=no\nDynamicUser=no\nAfter=docker.service\nRequires=docker.service\n"
            elif "--property=ActiveState,UnitFileState" in command:
                row = next(row for row in self.rows if row["name"] == name)
                output = "ActiveState=" + row["ActiveState"] + "\nUnitFileState=" + row["UnitFileState"]
            else:
                row = next(row for row in self.rows if row["name"] == name)
                output = self.files["/etc/systemd/system/" + name].decode() + "\nDropInPaths=" + row["DropInPaths"]
        elif "stop" in command or "start" in command:
            action = "stop" if "stop" in command else "start"
            next(row for row in self.rows if row["name"] == command[-1])["ActiveState"] = "inactive" if action == "stop" else "active"
        elif "daemon-reload" not in command:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, output, "")

    def digest(self, path):
        return hashlib.sha256(self.files[str(path)]).hexdigest()

    def plan(self):
        return setup.SchedulerPlan(Path("/opt/porota-trading"), self.source, self.identity,
            {"units": self.rows}, self.digest, run=self.fake_run, source_sha="a" * 40)

    def test_migration_keeps_calendars_host_services_and_companions_and_restores_exact_timer(self):
        plan = self.plan()
        original = self.files[self.fragment]
        result = plan.quiesce()
        self.assertEqual(result["state"], "QUIESCENT")
        installed = self.files[self.fragment].decode()
        self.assertNotIn("docker.service", installed)
        self.assertIn("/usr/sbin/runuser -u porotaadmin -- /usr/bin/env -i", installed)
        self.assertIn("POROTA_RUNTIME_BACKEND=ROOTLESS", installed)
        for line in original.decode().splitlines():
            if not line.startswith(("After=", "Requires=", "ExecStart=")):
                self.assertIn(line, installed)
        self.assertEqual(result["paused_timers"], [self.timer])
        self.assertEqual(plan.restore()["restored_timers"], [self.timer])
        mutations = [command for command in self.calls if command[0] == setup.SUDO and setup.SYSTEMCTL in command]
        touched = {command[-1] for command in mutations if command[-1] != "daemon-reload"}
        self.assertEqual(touched, {self.name, self.timer})
        self.assertFalse(any("disable" in command or "enable" in command or "restart" in command for command in mutations))

    def test_failed_transition_reentry_keeps_the_original_timer_restore_obligation(self):
        plan = self.plan()
        plan.quiesce()
        self.assertEqual(self.rows[1]["ActiveState"], "inactive")
        reentry = self.plan()
        self.assertEqual(reentry.receipt["planned_pauses"], [self.timer])
        reentry.quiesce()
        reentry.restore()
        self.assertEqual(self.rows[1]["ActiveState"], "active")

    def test_existing_snapshot_post_hook_bytes_and_root_identity_are_preserved(self):
        path = self.fragment + ".d/90-private-site-archive.conf"
        raw = b"[Service]\nExecStartPost=-/opt/porota-private-snapshot/archive_snapshot.sh postclose\n"
        self.files[path] = raw
        self.rows[0]["DropInPaths"] = path
        original_stat, original_read = Path.stat, host.read_regular
        def metadata(value, *args, **kwargs):
            if str(value) == path:
                return os.stat_result((0o100644, 1, 1, 1, 0, 0, len(raw), 0, 0, 0))
            return original_stat(value, *args, **kwargs)
        def read(value, *args):
            return self.files[path] if str(value) == path else original_read(value, *args)
        with patch.object(Path, "stat", metadata), patch.object(host, "read_regular", side_effect=read):
            plan = self.plan()
            plan.quiesce()
            plan.restore()
            self.assertEqual(self.files[path], raw)
            self.assertNotIn("\nUser=", self.files[self.fragment].decode())
            self.files[path] = b"[Service]\nExecStart=/opt/other/needed-function\n"
            with self.assertRaisesRegex(host.Hold, "SCHEDULER_EXECSTART_OVERRIDE_NEEDS_REVIEW"):
                self.plan()

    def test_unknown_active_function_blocks_before_any_mutation(self):
        self.rows.append(self.row("porota-unreviewed-rc6.service", "UNKNOWN", active="active"))
        with self.assertRaisesRegex(host.Hold, "LEGACY_FUNCTIONAL_UNIT_NEEDS_ROOTLESS_SUCCESSOR"):
            self.plan()
        self.assertFalse(any(command[0] == setup.SUDO for command in self.calls))

    def test_introspection_masks_other_homes_and_probes_only_exact_private_binds(self):
        name = jobs.INTROSPECTION
        path = "/etc/systemd/system/" + name
        self.files[path] = (self.source / "systemd" / name).read_bytes()
        self.rows.append(self.row(name, "ACTIVE_SERVICE"))
        plan = self.plan()
        receipt = plan.quiesce()
        installed = self.files[path].decode()
        self.assertIn("ProtectHome=tmpfs\n", installed)
        self.assertNotIn("ProtectHome=read-only", installed)
        self.assertIn("ProtectSystem=strict", installed)
        self.assertIn("SuccessExitStatus=2", installed)
        self.assertEqual(set(receipt["private_namespace"]["binds"]), {
            str(self.identity.state), str(self.identity.socket),
            str(self.identity.home / ".config/porota-rc6/client")})
        self.assertEqual(receipt["private_namespace"]["state"], "PROBED")
        probe = next(index for index, command in enumerate(self.calls) if "/usr/bin/systemd-run" in command)
        stopped = next(index for index, command in enumerate(self.calls) if "stop" in command)
        self.assertLess(probe, stopped)

    def test_missing_inmarket_baseline_is_recorded_without_inventing_one_or_blocking_migration(self):
        def unavailable(command, **kwargs):
            self.calls.append(command)
            return subprocess.CompletedProcess(command, 0, '{"state":"NO_VERIFICADO"}', "")
        result = setup.prepare_inmarket_baseline(self.identity, run=unavailable)
        self.assertEqual(result["state"], "NO_VERIFICADO")
        self.assertFalse((self.identity.state / "inmarket-baseline-20260917").exists())
        self.assertTrue(all(set(command).isdisjoint({"stop", "start", "rm", "chown"}) for command in self.calls))

    def test_existing_inmarket_baseline_is_copied_exactly_without_value_in_receipt(self):
        raw = b"123456789\n"
        def existing(command, **kwargs):
            return subprocess.CompletedProcess(command, 0,
                json.dumps({"state": "OBSERVED", "raw": base64.b64encode(raw).decode()}), "")
        result = setup.prepare_inmarket_baseline(self.identity, run=existing)
        self.assertEqual((self.identity.state / "inmarket-baseline-20260917").read_bytes(), raw)
        self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertNotIn(raw.decode().strip(), json.dumps(result))

    def test_quarantined_a3_template_is_inert_only_when_all_actual_timers_are_masked(self):
        self.rows.append({"name": "porota-a3-history-rc6@.service", "status": "NO_VERIFICADO", "policy_role": "ACTIVE_SERVICE"})
        for phase in ("daily", "reconcile", "weekend"):
            row = self.row("porota-a3-history-" + phase + "-rc6.timer", "QUARANTINED_TIMER", enabled="masked")
            row["LoadState"] = "masked"
            self.rows.append(row)
        plan = self.plan()
        self.assertIn("porota-a3-history-rc6@.service", plan.receipt["preserved_inert_units"])
        self.rows[-1]["ActiveState"] = "active"
        with self.assertRaisesRegex(host.Hold, "LEGACY_UNIT_STATE_UNVERIFIED"):
            self.plan()


if __name__ == "__main__":
    unittest.main()
