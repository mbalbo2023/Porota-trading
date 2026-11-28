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
from datetime import datetime
from unittest.mock import patch

import rc6_preopen as preopen
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

    def test_postclose_preserves_three_commands_and_publishes_campaign_on_dashboard(self):
        self.current.update(deployment_sha="d" * 40, source_tree="e" * 40,
                            health={"orders_guard": "BLOCKED"})
        def call(*args, **kwargs):
            self.calls.append(args)
            if args[5:6] == ("/app/rc6_validation_publish.py",):
                return json.dumps({"state": "PUBLISHED", "session_date": "2026-11-20", "records": 12})

        self.manager.docker.call = call
        with patch.object(jobs, "verified_host", return_value=(self.manager, self.current, {})), \
             patch.object(jobs, "database_snapshot", return_value={"real_orders_sent": 0}), \
             patch.object(jobs, "disk_usage", return_value=types.SimpleNamespace(free=2**34)):
            result = jobs.run_job(jobs.POSTCLOSE, root=self.root, identity=self.identity)
        self.assertEqual([command[4] for command in self.calls[:3]], ["/app/rc6_snapshot.py",
            "/app/rc6_postclose_review.py", "/app/rc6_decision_evidence_report.py"])
        self.assertTrue(all(command[:3] == ("container", "exec", "2" * 64) for command in self.calls))
        self.assertEqual(self.calls[-1][5:7], ("/app/rc6_validation_publish.py", "--runtime-proof"))
        proof = json.loads(self.calls[-1][-1])
        self.assertEqual(proof["deployment_sha"], "d" * 40)
        self.assertEqual(proof["orders_guard"], "BLOCKED")
        self.assertEqual(result["commands"][-1]["result"], "PUBLISHED")
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

    def test_post_deploy_preopen_uses_exact_release_and_preserves_typed_go_receipt(self):
        source = self.identity.state / "releases" / self.current["source_sha"] / "source"
        host.private_dir(source)
        self.current["database"] = {"real_orders_sent": 0}

        def run(command, **kwargs):
            self.assertEqual(command, ("/usr/bin/python3", "-B", str(source / "rc6_preopen.py"),
                                       "--revalidate-release", self.current["source_sha"]))
            kwargs["stdout"].write(json.dumps({
                "schema": "POROTA_RC6_PREOPEN_V3", "status": "GREEN",
                "paper_admission_receipt": {"status": "GO_PAPER_SCALPING",
                                            "reason": "PAPER_CURRENT_RELEASE_REVALIDATED"},
                "raw": "PRIVATE_PAYLOAD",
            }).encode())
            return subprocess.CompletedProcess(command, 0)

        with patch.object(jobs, "verified_host", return_value=(self.manager, self.current, {})), \
             patch.object(jobs, "database_snapshot", return_value=self.current["database"]):
            result = jobs.run_job(jobs.PREOPEN, root=self.root, identity=self.identity, run=run,
                                  revalidate_paper_gate=True)
        self.assertEqual(result["source_sha"], self.current["source_sha"])
        self.assertEqual(result["commands"][0]["result"], "GO_PAPER_SCALPING")
        self.assertNotIn("PRIVATE_PAYLOAD", json.dumps(result))

    def test_iol_jobs_use_the_verified_observer_namespace_and_preserve_typed_receipt(self):
        self.current["database"] = {"real_orders_sent": 0}
        source = self.identity.state / "releases" / self.current["source_sha"] / "source"
        host.private_dir(source)

        def exec_observer(*args, timeout):
            self.calls.append(args)
            self.assertEqual(args[-6:], ("1" * 64, "python", "-B", "-m",
                                        "scripts.rc6_iol_shadow_collect", "--oauth-health"))
            self.assertIn("POROTA_IOL_OPERATIONAL_DB=/app/data/paper_v17/observer_v17.db", args)
            self.assertNotIn("2" * 64, args)
            return subprocess.CompletedProcess(args, 0, stdout='{"result":"AUTHENTICATED"}')

        def exec_collector(*args, timeout):
            self.assertEqual(args[-5:], ("1" * 64, "python", "-B", "-m", "scripts.rc6_iol_shadow_collect"))
            self.assertIn("POROTA_IOL_SHADOW_SCOPE=AUDIT_ALL", args)
            return subprocess.CompletedProcess(args, 0, stdout="IOL_SHADOW_COLLECTION=COMPLETE READY=1")

        def source_cycle(**kwargs):
            self.assertEqual(kwargs["release_database"], self.current["database"])
            self.assertTrue(kwargs["iol_runner"](timeout=110).stdout.startswith("IOL_SHADOW_COLLECTION=COMPLETE"))
            return {"status": "COMPLETE", "stages": {"iol": "OK"}}

        self.manager.docker.command_result = exec_observer
        with patch.object(jobs, "database_snapshot", return_value=self.current["database"]):
            health = jobs.run_iol_job(jobs.IOL_HEALTH, self.root, self.identity, self.current,
                                      manager=self.manager)
            self.assertEqual(health, {"source": "IOL_REST", "result": "AUTHENTICATED", "exit_code": 0})
            self.manager.docker.command_result = exec_collector
            with patch("scripts.rc6_source_cycle_dispatch.run_cycle", side_effect=source_cycle):
                collector = jobs.run_iol_job(jobs.IOL_COLLECTOR, self.root, self.identity, self.current,
                                             manager=self.manager)
            self.assertEqual(collector["exit_code"], 0)

    def test_existing_iol_health_revalidates_only_when_technical_retry_is_due(self):
        self.current.update(market_provider="IOL_REST", database={"real_orders_sent": 0})
        source = self.identity.state / "releases" / self.current["source_sha"] / "source"
        host.private_dir(source)

        def live_preopen(command, **kwargs):
            self.assertEqual(command[-2:], ("--revalidate-release", self.current["source_sha"]))
            self.assertEqual(kwargs["env"]["POROTA_RC6_MARKET_PROVIDER"], "IOL_REST")
            self.assertLess(kwargs["timeout"], 60)
            with self.assertRaisesRegex(host.Hold, "OWNER_BUSY"):
                with host.user_lock(self.identity.state / "paper-preopen-revalidation.lock"):
                    pass
            kwargs["stdout"].write(json.dumps({
                "schema": "POROTA_RC6_PREOPEN_V3", "status": "GREEN",
                "paper_admission_receipt": {"status": "GO_PAPER_SCALPING",
                                            "reason": "PAPER_INTRADAY_PREOPEN_RECOVERED"},
            }).encode())
            return subprocess.CompletedProcess(command, 0)

        health = {"source": "IOL_REST", "result": "AUTHENTICATED", "exit_code": 0}
        with patch.object(jobs, "verified_host", return_value=(self.manager, self.current, {})), \
             patch.object(jobs, "database_snapshot", return_value=self.current["database"]), \
             patch.object(jobs, "run_iol_job", return_value=health), \
             patch.object(jobs, "iol_recovery_due", side_effect=[True, False]) as retry:
            live = jobs.run_job(jobs.IOL_HEALTH, root=self.root, identity=self.identity, run=live_preopen)
            self.assertEqual(live["paper_recovery"]["result"], "GO_PAPER_SCALPING")
            self.assertEqual(live["commands"], [health])
            repeated = jobs.run_job(jobs.IOL_HEALTH, root=self.root, identity=self.identity, run=live_preopen)
            self.assertNotIn("paper_recovery", repeated)
            reference = jobs.run_job(jobs.IOL_HEALTH, root=self.root, identity=self.identity, run=live_preopen,
                                     verify_rest=True)
            self.assertNotIn("paper_recovery", reference)
            self.assertEqual(retry.call_count, 2)

    def test_runtime_evidence_runs_the_verified_publisher_under_the_shared_lock(self):
        self.current["source_tree"] = "b" * 40
        source = self.identity.state / "releases" / self.current["source_sha"] / "source"
        host.private_dir(source)

        def run(command, **kwargs):
            self.assertEqual(command, ("/bin/sh", str(source / "scripts/porota_publish_runtime_evidence_github_rc6.sh"),
                                       "--verified-release"))
            self.assertEqual(kwargs["env"]["POROTA_BUILD_SHA"], self.current["source_sha"])
            self.assertEqual(kwargs["env"]["POROTA_CANDIDATE_TREE_SHA"], self.current["source_tree"])
            with self.assertRaisesRegex(host.Hold, "OWNER_BUSY"):
                with host.user_lock(self.identity.state / "deploy.lock"):
                    pass
            return subprocess.CompletedProcess(command, 0)

        with patch.object(jobs, "read_release_state", return_value=(self.current, {})), \
             patch.object(jobs, "byma_paper_spot_phase", return_value="OPEN"):
            jobs.publish_runtime_evidence(self.identity, root=self.root, run=run)

    def test_periodic_reports_skip_outside_the_wheel_without_docker_or_publishing(self):
        with patch.object(jobs, "read_release_state", return_value=(self.current, {})), \
             patch.object(jobs, "byma_paper_spot_phase", return_value="CLOSED"), \
             patch.object(jobs, "verified_host", side_effect=AssertionError("unexpected Docker read")):
            def run(*args, **kwargs):
                self.fail("scheduled report started outside the wheel")

            for unit in (jobs.INTROSPECTION, jobs.PUBLISH):
                receipt = jobs.run_job(unit, root=self.root, identity=self.identity, run=run)
                self.assertEqual(receipt["state"], "SKIPPED")
                self.assertEqual(receipt["exit_code"], 0)
                self.assertEqual(receipt["reason"], "REPORT_OUTSIDE_PAPER_WHEEL")
            receipt = jobs.publish_runtime_evidence(self.identity, root=self.root, run=run)
            self.assertEqual(receipt["unit"], "porota-runtime-evidence-rc6.service")
            self.assertEqual(receipt["state"], "SKIPPED")
            self.assertEqual(receipt["source_sha"], self.current["source_sha"])
            self.assertEqual(receipt["exit_code"], 0)

    def test_preopen_red_child_reports_only_allowlisted_checks_and_preserves_exit_two(self):
        source = self.identity.state / "releases" / self.current["source_sha"] / "source"
        host.private_dir(source)
        private = "PRIVATE_PREOPEN_TOKEN_NOT_FOR_JOURNAL"
        def run(command, **kwargs):
            self.assertNotEqual(kwargs["stdout"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            body = {"schema": "POROTA_RC6_PREOPEN_V3", "status": "RED",
                    "red_checks": ["observer_db", "byma_morning_watch"],
                    "checks": {
                        "observer_db": {"state": "RED", "reason": private},
                        "byma_morning_watch": {"state": "RED",
                                               "reason": "BYMA_WATCH_DEGRADED",
                                               "errors": [private]},
                    }}
            kwargs["stdout"].write(json.dumps(body).encode("utf-8"))
            return subprocess.CompletedProcess(command, 2)
        with patch.object(jobs, "verified_host", return_value=(self.manager, self.current, {})), \
             patch.object(jobs, "database_snapshot", return_value={"real_orders_sent": 0}):
            result = jobs.run_job(jobs.PREOPEN, root=self.root, identity=self.identity, run=run)
        self.assertEqual(result["state"], "BLOQUEADO")
        self.assertEqual(result["failure_signature"], "PREOPEN_RED_CHECKS")
        self.assertEqual(result["preopen_red_checks"], ["observer_db", "byma_morning_watch"])
        self.assertEqual(result["preopen_red_reasons"], {"byma_morning_watch": "BYMA_WATCH_DEGRADED"})
        self.assertEqual(result["child_exit_code"], 2)
        self.assertEqual(result["exit_code"], 2)
        self.assertNotIn(private, json.dumps(result))

    def test_preopen_ignores_unverified_byma_subreason_but_records_red_check(self):
        source = self.identity.state / "releases" / self.current["source_sha"] / "source"
        host.private_dir(source)
        private = "BYMA_WATCH_PRIVATE_ERROR_NOT_FOR_JOURNAL"
        for details in ({"state": "RED", "reason": private},
                        {"state": "RED", "reason": [private]},
                        {"state": "GREEN", "reason": "BYMA_WATCH_DEGRADED"}):
            with self.subTest(details=details):
                def run(command, **kwargs):
                    body = {"schema": "POROTA_RC6_PREOPEN_V3", "status": "RED",
                            "red_checks": ["byma_morning_watch"],
                            "checks": {"byma_morning_watch": details}}
                    kwargs["stdout"].write(json.dumps(body).encode("utf-8"))
                    return subprocess.CompletedProcess(command, 2)
                with patch.object(jobs, "verified_host", return_value=(self.manager, self.current, {})), \
                     patch.object(jobs, "database_snapshot", return_value={}):
                    result = jobs.run_job(jobs.PREOPEN, root=self.root, identity=self.identity, run=run)
                self.assertEqual(result["failure_signature"], "PREOPEN_RED_CHECKS")
                self.assertEqual(result["preopen_red_checks"], ["byma_morning_watch"])
                self.assertNotIn("preopen_red_reasons", result)
                self.assertNotIn(private, json.dumps(result))

    def test_preopen_rejects_a_future_byma_receipt_dated_today(self):
        at = datetime(2026, 11, 24, 10, 20, tzinfo=preopen.TZ)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz):
                return at.astimezone(tz)

        latest = self.root / "byma_morning_watch_latest.json"
        latest.write_text(json.dumps({"state": "NO_CHANGE",
                                      "observed_at": "2026-11-24T14:00:00Z"}), encoding="utf-8")
        with patch.object(preopen, "datetime", FixedDateTime), \
             patch.object(preopen, "BYMA_MORNING_WATCH", latest):
            result = preopen.byma_morning_watch(at.date())
        self.assertEqual(result["state"], "RED")
        self.assertEqual(result["reason"], "BYMA_WATCH_TIMESTAMP_FUTURE")

    def test_preopen_untrusted_child_receipt_never_publishes_raw_values(self):
        source = self.identity.state / "releases" / self.current["source_sha"] / "source"
        host.private_dir(source)
        private = "PRIVATE_PREOPEN_TRACE_NOT_FOR_JOURNAL"
        def run(command, **kwargs):
            body = {"schema": "POROTA_RC6_PREOPEN_V3", "status": "RED",
                    "red_checks": [private], "checks": {"private": private}}
            kwargs["stdout"].write(json.dumps(body).encode("utf-8"))
            return subprocess.CompletedProcess(command, 2)
        with patch.object(jobs, "verified_host", return_value=(self.manager, self.current, {})), \
             patch.object(jobs, "database_snapshot", return_value={"real_orders_sent": 0}):
            result = jobs.run_job(jobs.PREOPEN, root=self.root, identity=self.identity, run=run)
        self.assertEqual(result["failure_signature"], "PREOPEN_CHILD_RECEIPT_UNVERIFIED")
        self.assertEqual(result["preopen_red_checks"], [])
        self.assertEqual(result["exit_code"], 2)
        self.assertNotIn(private, json.dumps(result))

    def test_preopen_child_exception_exit_is_not_generic_79(self):
        source = self.identity.state / "releases" / self.current["source_sha"] / "source"
        host.private_dir(source)
        def run(command, **kwargs):
            kwargs["stdout"].write(b"private traceback")
            return subprocess.CompletedProcess(command, 1)
        with patch.object(jobs, "verified_host", return_value=(self.manager, self.current, {})), \
             patch.object(jobs, "database_snapshot", return_value={"real_orders_sent": 0}):
            result = jobs.run_job(jobs.PREOPEN, root=self.root, identity=self.identity, run=run)
        self.assertEqual(result["failure_signature"], "PREOPEN_CHILD_EXIT_1")
        self.assertEqual(result["exit_code"], 1)
        self.assertNotIn("private traceback", json.dumps(result))

    def test_preopen_dispatcher_hold_keeps_cause_but_not_generic_79(self):
        stream = io.StringIO()
        with patch.object(jobs.Identity, "current", return_value=self.identity), \
             patch.object(jobs, "run_job", side_effect=host.Hold("PAPER_ZERO_REQUIRED")), \
             redirect_stdout(stream):
            code = jobs.main(["--unit", jobs.PREOPEN])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stream.getvalue())["failure_signature"], "PAPER_ZERO_REQUIRED")

    def test_nonpreopen_dispatcher_hold_keeps_cause_without_generic_79(self):
        stream = io.StringIO()
        with patch.object(jobs.Identity, "current", return_value=self.identity), \
             patch.object(jobs, "run_job", side_effect=host.Hold("SCHEDULER_SOURCE_CUSTODY")), \
             redirect_stdout(stream):
            code = jobs.main(["--unit", "porota-candle-integrity-rc6.service"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stream.getvalue())["failure_signature"], "SCHEDULER_SOURCE_CUSTODY")

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
             patch.object(jobs, "database_snapshot", return_value={}), \
             patch.object(jobs, "byma_paper_spot_phase", return_value="OPEN"):
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
        self.assertEqual(stopped.exception.code, 1)
        failure = json.loads(output.getvalue())
        self.assertEqual(failure["failure_signature"], "SCHEDULER_LAUNCHER_CUSTODY")
        self.assertEqual(failure["failure_stage"], "CURRENT_RELEASE")
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
