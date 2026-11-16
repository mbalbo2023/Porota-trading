"""Adversarial offline contracts for the remote READ_ONLY RC6 market monitor."""
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
import sys
import types
import unittest
import zipfile
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1] / "scripts/rc6_market_watch_readonly.py"
spec = importlib.util.spec_from_file_location("rc6_market_watch_readonly", SOURCE)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
SHA = "a" * 40
IMAGE = "sha256:" + "b" * 64
NOW = datetime(2026, 10, 14, 14, 0, tzinfo=timezone.utc)


class MonitorContracts(unittest.TestCase):
    def test_transport_diagnostics_classify_without_exporting_hosts_or_keys(self):
        for text,code in [('ssh: connect to host private.example port 22: Connection timed out','CONNECT_TIMEOUT'),
                          ('user@private: Permission denied (publickey)','AUTH_DENIED'),
                          ('Host key verification failed. private fingerprint','HOST_KEY_REJECTED')]:
            result=monitor.transport_diagnostic(text,255)
            self.assertEqual(result['class'],code)
            self.assertNotIn('private',json.dumps(result))
        self.assertEqual(monitor.transport_diagnostic('',0)['class'],'OK')
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        db = self.root / monitor.DB_REL
        db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db) as connection:
            connection.execute("""CREATE TABLE observer_state
                (id INTEGER PRIMARY KEY, mode TEXT, process_state TEXT,
                session_state TEXT, ppi_auth TEXT, real_orders_sent INTEGER,
                heartbeat_at TEXT, last_market_data_at TEXT)""")
            connection.execute("INSERT INTO observer_state VALUES(1,?,?,?,?,?,?,?)", (
                "PRODUCTION_PAPER", "RUNNING", "MARKET_OPEN", "OK", 0,
                NOW.isoformat(), NOW.isoformat()))
            connection.execute("""CREATE TABLE candidate_identity_v2
                (can_simulate INTEGER, status TEXT)""")
            connection.executemany("INSERT INTO candidate_identity_v2 VALUES(?,?)",
                                   [(1, "AVAILABLE"), (0, "PAUSED_EXPLICIT")])
            for name in monitor.TRACKED:
                connection.execute(f'CREATE TABLE "{name}" (id INTEGER PRIMARY KEY)')
                connection.execute(f'INSERT INTO "{name}" VALUES(1)')
        self.db = db
        self.provenance_path = self.root / "data/deploy/CURRENT_STATE_V2.json"
        self.provenance_path.parent.mkdir(parents=True, exist_ok=True)
        self.provenance_path.write_text(json.dumps({
            "deploy_sha": SHA, "candidate_sha": "c" * 40,
            "image_id": IMAGE, "mode": "PRODUCTION_PAPER",
            "real_orders_sent": 0, "real_order_routes": "NOT_CALLED",
            "validation_status": "VALIDATED_RUNTIME"}))
        self.shadow_path = self.root / (
            "data/paper_v17/artifacts/observer_v17.db/dynamic-shadow/status.json")
        self.shadow_path.parent.mkdir(parents=True, exist_ok=True)
        self.write_shadow()
        self.performance_path = self.root / (
            "data/paper_v17/observer_v17.db.performance.sqlite.latest.json")
        self.performance_path.write_text(json.dumps({
            "generated_at": NOW.isoformat(), "economic_edge_validated": False,
            "forward_signal_evaluation": {"status": "NO_VERIFICADO"}}))
        self.inspect = {name: {"state": "running", "restarts": 0, "oom": False,
                               "image": IMAGE} for name in monitor.CONTAINERS}
        self.resources = {"disk_free_bytes": 10 * 1024**3,
                          "inode_free_pct": 70, "mem_available_bytes": 700 * 1024**2}

    def write_shadow(self, status="SHADOW_OBSERVING", as_of=None, real=0):
        payload = {"as_of": as_of or NOW.isoformat(), "status": status,
                   "real_orders_sent": real, "real_routes": "NOT_CALLED",
                   "entry_signal_lab_status": "NO_VERIFICADO",
                   "operational_funnel_status": "NO_VERIFICADO"}
        self.shadow_path.write_text(json.dumps({
            "payload": payload, "digest": monitor.canonical_digest(payload)}))

    def capture(self, at=NOW, host_state=None, expected_tree_sha=None):
        with patch.object(monitor, "independent_session", return_value={"state":"OPEN" if monitor.active_hours(at) else "CLOSED"}), patch.object(monitor, "_resources", return_value=self.resources), patch.object(
                monitor, "_host_control_state", return_value=host_state if host_state is not None else {
                    "legacy_health_timer": "inactive", "fast_health_timer": "active",
                    "observer_container": {"Status": "running"}}):
            return monitor.probe(root=self.root, at=at, expected_sha=SHA,
                                 expected_tree_sha=expected_tree_sha,
                                 inspect=self.inspect)

    def rootless_context(self, command_result=None):
        identity = SimpleNamespace(
            socket="/run/user/1000/porota-rc6-docker.sock",
            environment=lambda: {"PATH": "/usr/bin:/bin"})
        return SimpleNamespace(
            roles={"observer": "1" * 64, "dashboard": "2" * 64},
            host=SimpleNamespace(identity=identity, docker=SimpleNamespace(
                command_result=command_result)))

    def codes(self, record, severity=None):
        return {f["code"] for f in record["findings"]
                if severity is None or f["severity"] == severity}

    def test_healthy_with_no_trades_or_economic_edge(self):
        observation = self.capture()
        self.assertEqual(observation["severity"], "GREEN")
        self.assertEqual(observation["readiness"], {"total": 2, "ready": 1})
        self.assertEqual(observation["cursors"]["paper_fills"], 1)
        self.assertFalse(observation["performance"]["edge_validated"])
        self.assertFalse(observation["ppi_watch_accessed"])
        self.assertEqual(observation["broker_requests"], 0)
        self.assertEqual(observation["real_order_routes_called"], 0)
        self.assertEqual(observation["host_control_state"]["legacy_health_timer"], "inactive")

    def test_ppi_auth_last_attempt_reports_http_metadata_without_raw_detail(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("""CREATE TABLE api_health(
                component TEXT PRIMARY KEY, state TEXT, detail TEXT,
                checked_at TEXT, last_success_at TEXT, source TEXT)""")
            connection.execute("INSERT INTO api_health VALUES(?,?,?,?,?,?)", (
                "PPI_PRODUCTION_AUTH", "ROJO",
                "ProductionAuthResponseError: PPI_PRODUCTION_AUTH_NON_JSON_RESPONSE: "
                "HTTP=403; Content-Type=TEXT_HTML; Content-Length=228 "
                "https://private.example/?token=DO_NOT_LEAK",
                "2026-11-16T19:05:00+00:00", "2026-11-05T19:00:00+00:00", "PPI Producción"))
        result = monitor._db_snapshot(self.db)["ppi_auth_last_attempt"]
        self.assertEqual(result["status"], "LAST_FAILED_AUTH_RECORDED")
        self.assertEqual(result["error_class"], "ProductionAuthResponseError")
        self.assertEqual(result["http_status"], 403)
        self.assertEqual(result["response_media_type"], "TEXT_HTML")
        self.assertEqual(result["response_bytes"], 228)
        self.assertEqual(result["checked_at"], "2026-11-16T19:05:00+00:00")
        self.assertEqual(result["last_success_at"], "2026-11-05T19:00:00+00:00")
        self.assertEqual(result["provider_requests_by_probe"], 0)
        self.assertNotIn("DO_NOT_LEAK", json.dumps(result))
        self.assertNotIn("private.example", json.dumps(result))
        self.assertNotIn("url", json.dumps(result).lower())

    def test_ppi_auth_unclassified_detail_never_becomes_guessed_http_status(self):
        absent = monitor._db_snapshot(self.db)["ppi_auth_last_attempt"]
        self.assertEqual(absent["status"], "NO_VERIFICADO")
        self.assertIsNone(absent["http_status"])
        with sqlite3.connect(self.db) as connection:
            connection.execute("""CREATE TABLE api_health(
                component TEXT PRIMARY KEY, state TEXT, detail TEXT,
                checked_at TEXT, last_success_at TEXT, source TEXT)""")
            connection.execute("INSERT INTO api_health VALUES(?,?,?,?,?,?)", (
                "PPI_PRODUCTION_AUTH", "ROJO",
                "RuntimeError: secret=DO_NOT_LEAK http://private.example/401?token=123",
                "2026-11-16T19:05:00Z", None, "PPI Producción"))
        unknown = monitor._db_snapshot(self.db)["ppi_auth_last_attempt"]
        self.assertEqual(unknown["status"], "LAST_FAILED_AUTH_RECORDED")
        self.assertEqual(unknown["error_class"], "RuntimeError")
        self.assertIsNone(unknown["http_status"])
        self.assertNotIn("DO_NOT_LEAK", json.dumps(unknown))
        self.assertNotIn("private.example", json.dumps(unknown))
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE api_health SET detail=? WHERE component=?", (
                "HTTPError: 401 Client Error: Unauthorized for url: "
                "https://private.example/api?token=DO_NOT_LEAK", "PPI_PRODUCTION_AUTH"))
        http = monitor._db_snapshot(self.db)["ppi_auth_last_attempt"]
        self.assertEqual(http["http_status"], 401)
        self.assertEqual(http["reason"], "HTTP_ERROR_STATUS_ONLY")
        self.assertNotIn("DO_NOT_LEAK", json.dumps(http))
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE api_health SET state='VERDE', detail='Login correcto' "
                               "WHERE component='PPI_PRODUCTION_AUTH'")
        recovered = monitor._db_snapshot(self.db)["ppi_auth_last_attempt"]
        self.assertEqual(recovered["status"], "LAST_AUTH_SUCCESS_RECORDED")
        self.assertIsNone(recovered["http_status"])

    def test_rootless_current_marker_uses_candidate_sha_not_removed_deploy_sha(self):
        current = json.loads(self.provenance_path.read_text())
        current.pop("deploy_sha")
        current["candidate_sha"] = "c" * 40
        current["candidate_tree_sha"] = "e" * 40
        current["deployment_backend"] = "ROOTLESS_SPLIT"
        self.provenance_path.write_text(json.dumps(current))
        for role in monitor.CONTAINERS:
            self.inspect[role].update(source_sha="c" * 40, source_tree="e" * 40)
        observation = self.capture(expected_tree_sha="e" * 40)
        self.assertNotIn("DEPLOY_PROVENANCE_INVALID", self.codes(observation))
        self.assertNotIn("CONTAINER_OR_IMAGE_UNVERIFIED", self.codes(observation))
        self.assertIn("c" * 40, json.dumps(observation["provenance"]))
        self.assertNotEqual(current["candidate_sha"], SHA)
        self.assertEqual(observation["expected_deploy_sha"], SHA)
        self.assertEqual(observation["expected_product_tree"], "e" * 40)
        self.assertEqual(observation["observed_candidate_sha"], "c" * 40)
        self.assertEqual(observation["provenance_identity"], "ROOTLESS_PRODUCT_TREE_MATCHED")

        absent = self.capture()
        self.assertIn("DEPLOY_PROVENANCE_INVALID", self.codes(absent))
        self.assertEqual(absent["provenance_identity"], "PRODUCT_TREE_NOT_PROVIDED")
        mismatch = self.capture(expected_tree_sha="f" * 40)
        self.assertIn("DEPLOY_PROVENANCE_INVALID", self.codes(mismatch))
        self.assertEqual(mismatch["provenance_identity"], "PRODUCT_TREE_MISMATCH")
        self.inspect["dashboard"]["source_sha"] = "d" * 40
        drift = self.capture(expected_tree_sha="e" * 40)
        self.assertIn("DEPLOY_PROVENANCE_INVALID", self.codes(drift))
        self.assertEqual(drift["provenance_identity"], "PRIVATE_RELEASE_SOURCE_MISMATCH")

        # Missing private Docker evidence is not proof of a source mismatch.
        # Keep a preventive P0 while attributing its uncertainty honestly.
        self.inspect = {}
        unavailable = self.capture(expected_tree_sha="e" * 40)
        self.assertEqual(unavailable["provenance_identity"], "PRIVATE_RUNTIME_INSPECT_UNAVAILABLE")
        self.assertIn("CONTAINER_OR_IMAGE_UNVERIFIED", self.codes(unavailable, "P0"))
        self.assertIn("DEPLOY_PROVENANCE_UNVERIFIED", self.codes(unavailable, "P0"))
        self.assertNotIn("DEPLOY_PROVENANCE_INVALID", self.codes(unavailable))

    def test_private_rootless_inspection_carries_exact_source_provenance(self):
        cid = "1" * 64
        info = {"Id": cid, "Image": IMAGE, "State": {"Status": "running"}}
        context = self.rootless_context()
        context.current = {"source_sha": "c" * 40, "source_tree": "e" * 40}
        context.items = {"observer": info,
                         "dashboard": dict(info, Id="2" * 64)}
        with patch.object(monitor, "_rootless_context", return_value=context):
            rows = monitor._docker_inspect()
        self.assertEqual(rows["observer"]["source_sha"], "c" * 40)
        self.assertEqual(rows["dashboard"]["source_tree"], "e" * 40)

    def test_docker_inspection_uses_exact_private_release_pair_and_no_legacy_names(self):
        observer_cid = "1" * 64
        dashboard_cid = "2" * 64
        def item(role, cid):
            return {"Id": cid, "Image": IMAGE,
                    "State": {"Status": "running", "RestartCount": 0,
                              "OOMKilled": False},
                    "Name": "/porota-rc6-" + role + "-attempt-a"}
        context = SimpleNamespace(
            current={"image_id": IMAGE, "source_sha": SHA},
            roles={"observer": observer_cid, "dashboard": dashboard_cid},
            items={"observer": item("observer", observer_cid),
                   "dashboard": item("dashboard", dashboard_cid)})
        with patch.object(monitor, "_rootless_context", return_value=context, create=True), patch.object(
                monitor.subprocess, "run", side_effect=AssertionError("must not use default Docker")):
            containers = monitor._docker_inspect()
        self.assertEqual(set(containers), {"observer", "dashboard"})
        self.assertEqual(containers["observer"]["cid"], observer_cid)
        self.assertEqual(containers["dashboard"]["cid"], dashboard_cid)

    def rootless_production_fixture(self):
        """Actual deploy receipt/daemon/container shapes, no hypothetical APIs."""
        home = self.root / "porotaadmin"
        state = home / ".local/state/porota-rc6"
        state.mkdir(parents=True)
        for directory in (home, home / ".local", home / ".local/state", state):
            directory.chmod(0o700)
        cids = {"observer": "1" * 64, "dashboard": "2" * 64}
        current = {"schema": "porota.rootless.deploy.v1", "state": "VALIDADO_RUNTIME",
                   "source_sha": SHA, "source_tree": "e" * 40,
                   "image_id": IMAGE, "attempt": "attempt-a", "roles": cids,
                   "daemon_id": "dedicated-private-daemon", "real_orders_sent": 0,
                   "operating_mode": "PRODUCTION_PAPER", "execution_mode": "SIMULATION"}
        marker = state / "current.json"
        marker.write_text(json.dumps(current))
        marker.chmod(0o600)
        daemon = {"ID": current["daemon_id"], "SecurityOptions": ["name=rootless"],
                  "CgroupVersion": "2", "CgroupDriver": "systemd",
                  "DockerRootDir": str(home / ".local/share/porota-rc6/docker")}
        items = {}
        for role, cid in cids.items():
            mounts = [
                {"Type": "bind", "Destination": "/app/data", "Source": str(monitor.ROOT / "data"), "RW": True},
                {"Type": "bind", "Destination": "/app/operator", "Source": str(home / ".config/porota-rc6/operator"), "RW": False}]
            if role == "observer":
                mounts.append({"Type": "bind", "Destination": "/run/secrets/ppi_production.json",
                               "Source": str(monitor.ROOT / ".secrets/ppi_production.json"), "RW": False})
            mounts.extend({"Type": "tmpfs", "Destination": destination, "RW": True}
                          for destination in ("/tmp", "/home/botuser/.cache", "/home/botuser/.config"))
            host_config = {"ReadonlyRootfs": True, "Privileged": False, "CapDrop": ["ALL"],
                           "CapAdd": None, "SecurityOpt": ["no-new-privileges:true"],
                           "NetworkMode": "porota-rc6-private", "PidMode": "", "Memory": 1024**3,
                           "NanoCpus": 1000000000, "PidsLimit": 256,
                           "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=128m",
                                     "/home/botuser/.cache": "rw,noexec,nosuid,size=64m",
                                     "/home/botuser/.config": "rw,noexec,nosuid,size=16m"},
                           "PortBindings": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}]}
                           if role == "dashboard" else {}}
            items[cid] = {"Id": cid, "Name": f"/porota-rc6-{role}-attempt-a", "Image": IMAGE,
                "RestartCount": 0, "State": {"Status": "running", "Running": True, "OOMKilled": False},
                "Config": {"Labels": {"io.porota.project": "rc6", "io.porota.role": role,
                                      "io.porota.attempt": "attempt-a"},
                           "Image": IMAGE, "User": "0:0", "Entrypoint": None,
                           "WorkingDir": "/app", "Cmd": ["python", "o_dashboard.py" if role == "dashboard"
                                                         else "bv_paper_runtime.py"],
                           "Env": [f"POROTA_BUILD_SHA={SHA}", f"POROTA_CANDIDATE_TREE_SHA={'e'*40}",
                                   "PAPER_V17_DB_PATH=/app/data/paper_v17/observer_v17.db",
                                   "DASHBOARD_OPERATION_MODE=PRODUCTION_PAPER",
                                   "EXECUTION_MODE=SIMULATION", "REAL_ORDER_CAPABILITY=BLOCKED"]},
                "HostConfig": host_config,
                "Mounts": mounts}
        calls = []
        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            self.assertEqual(argv[:3], ["/usr/bin/docker", "--host",
                                        "unix:///run/user/1000/porota-rc6-docker.sock"])
            self.assertEqual(kwargs.get("env", {}).get("DOCKER_CONFIG"),
                             str(home / ".config/porota-rc6/client"))
            self.assertNotIn("sudo", argv)
            command = argv[3:]
            if command == ["info", "--format", "{{json .}}"]:
                value = daemon
            elif command[:2] == ["container", "inspect"] and command[2] in items:
                value = [items[command[2]]]
            elif command[:2] == ["container", "exec"] and command[2] in items:
                value = {"schema": "rc6.shadow-readonly-diagnostic.v1", "read_only": True,
                         "stages": [{"stage": "RUNTIME_INPUT", "status": "OK"}]}
            else:
                self.fail(f"Unexpected Docker operation: {command[:3]}")
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(value), stderr="secret=must_not_export")
        account = SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_name="porotaadmin", pw_dir=str(home))
        return home, marker, current, daemon, items, runner, calls, account

    def test_rootless_product_command_and_tmpfs_contract(self):
        home, marker, current, daemon, items, runner, calls, account = self.rootless_production_fixture()
        identity = SimpleNamespace(operator=home / ".config/porota-rc6/operator")
        for role, cid in current["roles"].items():
            with self.subTest(role=role):
                item = items[cid]
                self.assertEqual(monitor._verify_rootless_item(
                    item, cid=cid, role=role, current=current, identity=identity), item)
                without_optional_mount_rows = dict(item, Mounts=[row for row in item["Mounts"]
                    if row["Type"] == "bind"])
                self.assertEqual(monitor._verify_rootless_item(
                    without_optional_mount_rows, cid=cid, role=role,
                    current=current, identity=identity), without_optional_mount_rows)
        observer = items[current["roles"]["observer"]]
        for wrong in ("bf_production_paper_observer.py", "unexpected.py"):
            with self.subTest(command=wrong):
                altered = dict(observer, Config=dict(observer["Config"], Cmd=["python", wrong]))
                with self.assertRaisesRegex(ValueError, "ROOTLESS_CONTAINER_COMMAND_CHANGED"):
                    monitor._verify_rootless_item(altered, cid=observer["Id"], role="observer",
                                                  current=current, identity=identity)
        for change in ("additional_bind", "unknown_tmpfs", "missing_tmpfs", "unsafe_flags", "wrong_size"):
            with self.subTest(change=change):
                altered = dict(observer, Mounts=list(observer["Mounts"]),
                               HostConfig=dict(observer["HostConfig"]))
                if change == "additional_bind":
                    altered["Mounts"].append({"Type": "bind", "Destination": "/other",
                                               "Source": "/other", "RW": False})
                elif change == "unknown_tmpfs":
                    altered["Mounts"].append({"Type": "tmpfs", "Destination": "/unexpected", "RW": True})
                else:
                    altered["HostConfig"]["Tmpfs"] = dict(altered["HostConfig"]["Tmpfs"])
                    if change == "missing_tmpfs":
                        del altered["HostConfig"]["Tmpfs"]["/tmp"]
                    if change == "unsafe_flags":
                        altered["HostConfig"]["Tmpfs"]["/tmp"] = "rw,nosuid,size=128m"
                    if change == "wrong_size":
                        altered["HostConfig"]["Tmpfs"]["/tmp"] = "rw,noexec,nosuid,size=256m"
                with self.assertRaises(ValueError):
                    monitor._verify_rootless_item(altered, cid=observer["Id"], role="observer",
                                                  current=current, identity=identity)

    def test_rootless_resolver_requires_private_current_and_two_pinned_cids(self):
        home, marker, current, daemon, items, runner, calls, account = self.rootless_production_fixture()
        # This source commit contains NO private copied Python helper; the
        # production Docker API has only call/json, no command_result.
        self.assertFalse((home / ".local/state/porota-rc6/releases" / SHA /
                          "source/scripts/porota_rootless_host.py").exists())
        # Runner UID/owner may differ from productive 1000; simulate metadata
        # only, while validating real file modes, symlinks and Docker shapes.
        real_lstat = Path.lstat
        real_fstat = monitor.os.fstat
        def owned_by_1000(meta):
            values = list(meta)
            values[4] = 1000
            return monitor.os.stat_result(values)
        def virtual_lstat(path):
            return owned_by_1000(real_lstat(path))
        with patch.object(monitor, "pwd", SimpleNamespace(getpwuid=lambda _: account)), \
                patch.object(monitor.os, "getuid", return_value=1000), \
                patch.object(monitor.os, "geteuid", return_value=1000), \
                patch.object(monitor.os, "getgid", return_value=1000), \
                patch.object(monitor.os, "getegid", return_value=1000), \
                patch.object(Path, "lstat", virtual_lstat), \
                patch.object(monitor.os, "fstat", side_effect=lambda fd: owned_by_1000(real_fstat(fd))), \
                patch.object(monitor, "_verify_rootless_socket", return_value=None, create=True):
            context = monitor._rootless_context(runner=runner)
            self.assertEqual(context.roles, current["roles"])
            self.assertEqual(context.current["source_sha"], SHA)
            self.assertEqual(context.items["observer"]["Id"], "1" * 64)
            self.assertEqual(context.items["dashboard"]["Id"], "2" * 64)
            completed = context.host.docker.command_result(
                "container", "exec", current["roles"]["observer"],
                "python", "-B", "-c", "print(1)", timeout=7)
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(json.loads(completed.stdout)["read_only"], True)
            self.assertEqual(len(calls), 4)  # info, two exact inspect, one exec
            self.provenance_path.write_text(json.dumps({
                "candidate_sha": SHA, "candidate_tree_sha": current["source_tree"],
                "deployment_backend": "ROOTLESS_SPLIT", "image_id": IMAGE,
                "mode": "PRODUCTION_PAPER", "validation_status": "VALIDATED_RUNTIME",
                "real_orders_sent": 0, "real_order_routes": "NOT_CALLED"}))
            real_context = monitor._rootless_context
            with patch.object(monitor, "_rootless_context",
                    side_effect=lambda: real_context(runner=runner)), patch.object(
                    monitor, "independent_session", return_value={"state": "OPEN"}), patch.object(
                    monitor, "_resources", return_value=self.resources), patch.object(
                    monitor, "_host_control_state", return_value={
                        "legacy_health_timer": "inactive", "fast_health_timer": "active"}):
                observation = monitor.probe(root=self.root, at=NOW,
                    expected_sha="f" * 40, expected_tree_sha=current["source_tree"])
            self.assertNotIn("CONTAINER_OR_IMAGE_UNVERIFIED", self.codes(observation))
            self.assertNotIn("DEPLOY_PROVENANCE_INVALID", self.codes(observation))
            self.assertEqual(observation["provenance_identity"], "ROOTLESS_PRODUCT_TREE_MATCHED")
            self.assertEqual(set(observation["containers"]), {"observer", "dashboard"})
            daemon["ID"] = "forged-daemon"
            with self.assertRaises(ValueError):
                monitor._rootless_context(runner=runner)
            daemon["ID"] = current["daemon_id"]
            items[current["roles"]["dashboard"]]["Config"]["Labels"]["io.porota.role"] = "observer"
            with self.assertRaises(ValueError):
                monitor._rootless_context(runner=runner)
            marker.unlink()
            marker.symlink_to(self.provenance_path)
            with self.assertRaises(ValueError):
                monitor._rootless_context(runner=runner)

    def test_root_owned_nonwritable_parent_directories_follow_product_contract(self):
        home, marker, current, daemon, items, runner, calls, account = self.rootless_production_fixture()
        real_lstat, real_fstat = Path.lstat, monitor.os.fstat
        intermediates = {home / ".local", home / ".local/state"}
        def owner(meta, uid):
            values = list(meta)
            values[4] = uid
            return monitor.os.stat_result(values)
        for intermediate_uid, expected in ((0, True), (1002, False)):
            with self.subTest(intermediate_uid=intermediate_uid):
                def lstat(path):
                    return owner(real_lstat(path),
                                 intermediate_uid if Path(path) in intermediates else 1000)
                with patch.object(monitor.pwd, "getpwuid", return_value=account), \
                        patch.object(monitor.os, "getuid", return_value=1000), \
                        patch.object(monitor.os, "geteuid", return_value=1000), \
                        patch.object(monitor.os, "getgid", return_value=1000), \
                        patch.object(monitor.os, "getegid", return_value=1000), \
                        patch.object(Path, "lstat", lstat), \
                        patch.object(monitor.os, "fstat", side_effect=lambda fd: owner(real_fstat(fd), 1000)), \
                        patch.object(monitor, "_verify_rootless_socket", return_value=None):
                    if expected:
                        self.assertEqual(monitor._rootless_context(runner=runner).roles, current["roles"])
                    else:
                        with self.assertRaisesRegex(ValueError, "ROOTLESS_RELEASE_CUSTODY"):
                            monitor._rootless_context(runner=runner)

    def test_group_writable_local_state_requires_private_ancestor(self):
        home, marker, current, daemon, items, runner, calls, account = self.rootless_production_fixture()
        real_lstat, real_fstat = Path.lstat, monitor.os.fstat
        # A writable intermediate cannot be used for path substitution only
        # when HOME or LOCAL blocks all traversal by other UIDs, while HOME
        # itself remains non-writable to group/other users.
        cases = (
            ("private-home", 0o700, 0o700, 0o770, 0o700, True),
            ("group-traversable-home-private-local", 0o750, 0o700, 0o770, 0o700, True),
            ("public-home-private-local", 0o755, 0o700, 0o770, 0o700, True),
            ("public-home", 0o755, 0o755, 0o770, 0o700, False),
            ("group-traversable-home-and-local", 0o750, 0o750, 0o770, 0o700, False),
            ("writable-home", 0o770, 0o700, 0o770, 0o700, False),
            ("world-writable-home", 0o702, 0o700, 0o770, 0o700, False),
            ("writable-local", 0o700, 0o770, 0o770, 0o700, False),
            ("world-writable-state-parent", 0o700, 0o700, 0o777, 0o700, False),
            ("exposed-state", 0o700, 0o700, 0o770, 0o750, False),
        )
        targets = (home, home / ".local", home / ".local/state",
                   home / ".local/state/porota-rc6")
        for name, *fields in cases:
            *modes, allowed = fields
            with self.subTest(name=name):
                def lstat(path):
                    meta = list(real_lstat(path))
                    meta[4] = 1000
                    if Path(path) in targets:
                        meta[0] = (meta[0] & ~0o777) | modes[targets.index(Path(path))]
                    return monitor.os.stat_result(meta)
                def fstat(fd):
                    meta = list(real_fstat(fd))
                    meta[4] = 1000
                    return monitor.os.stat_result(meta)
                with patch.object(monitor.pwd, "getpwuid", return_value=account), \
                        patch.object(monitor.os, "getuid", return_value=1000), \
                        patch.object(monitor.os, "geteuid", return_value=1000), \
                        patch.object(monitor.os, "getgid", return_value=1000), \
                        patch.object(monitor.os, "getegid", return_value=1000), \
                        patch.object(Path, "lstat", lstat), \
                        patch.object(monitor.os, "fstat", side_effect=fstat), \
                        patch.object(monitor, "_verify_rootless_socket", return_value=None):
                    if allowed:
                        self.assertEqual(monitor._rootless_context(runner=runner).roles,
                                         current["roles"])
                    else:
                        with self.assertRaisesRegex(ValueError, "ROOTLESS_RELEASE_CUSTODY"):
                            monitor._rootless_context(runner=runner)

    def test_rootless_failure_receipt_is_bounded_and_never_exports_secret(self):
        for error, expected in ((ValueError("ROOTLESS_RELEASE_CUSTODY"),
                                 "ROOTLESS_RELEASE_CUSTODY"),
                                (ValueError("ROOTLESS_CONTAINER_TMPFS_CHANGED"),
                                 "ROOTLESS_CONTAINER_TMPFS_CHANGED"),
                                (ValueError("private-token=do-not-export"),
                                 "ROOTLESS_UNCLASSIFIED"),
                                (OSError("private-token=do-not-export"),
                                 "ROOTLESS_UNCLASSIFIED")):
            with self.subTest(expected=expected), patch.object(monitor, "_rootless_context", side_effect=error):
                diagnostic = []
                self.assertIsNone(monitor._docker_inspect(diagnostics=diagnostic))
                self.assertEqual(diagnostic, [expected])
                self.assertNotIn("private-token", repr(diagnostic))

    def test_rootless_custody_metadata_is_fixed_scope_and_never_exports_paths(self):
        home, marker, current, daemon, items, runner, calls, account = self.rootless_production_fixture()
        real_lstat = Path.lstat
        def virtual_lstat(path):
            meta = list(real_lstat(path))
            meta[4] = 1000
            if Path(path) == home / ".local":
                meta[4] = 1002
                meta[0] = (meta[0] & ~0o777) | 0o770
            return monitor.os.stat_result(meta)
        with patch.object(monitor.pwd, "getpwuid", return_value=account), \
                patch.object(monitor.os, "getuid", return_value=1000), \
                patch.object(monitor.os, "geteuid", return_value=1000), \
                patch.object(Path, "lstat", virtual_lstat):
            metadata = monitor._rootless_custody_metadata()
        self.assertEqual(metadata["state"], "OBSERVED")
        self.assertEqual(set(metadata["segments"]), {"HOME", "LOCAL", "LOCAL_STATE", "STATE"})
        self.assertEqual(metadata["segments"]["LOCAL"], {
            "owner_class": "OTHER", "kind": "DIRECTORY",
            "group_write": True, "other_write": False,
            "group_or_other_any_access": True})
        self.assertEqual(metadata["segments"]["STATE"]["owner_class"], "UID1000")
        self.assertIs(metadata["segments"]["STATE"]["group_or_other_any_access"], False)
        self.assertIs(metadata["segments"]["HOME"]["group_or_other_any_access"], False)
        self.assertNotIn(str(home), json.dumps(metadata))
        with patch.object(monitor.os, "getuid", return_value=0):
            self.assertEqual(monitor._rootless_custody_metadata()["state"], "IDENTITY_UNVERIFIED")

    def test_private_socket_0660_allowed_but_world_access_never_allowed(self):
        socket = Path("/run/user/1000/porota-rc6-docker.sock")
        identity = SimpleNamespace(uid=1000, socket=socket)
        mode = {"socket": 0o660, "owner": 1000}
        def virtual_lstat(path):
            if Path(path) == socket.parent:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=1000)
            if Path(path) == socket:
                return SimpleNamespace(st_mode=stat.S_IFSOCK | mode["socket"],
                                       st_uid=mode["owner"])
            self.fail("Unexpected filesystem lookup")
        with patch.object(Path, "lstat", virtual_lstat):
            monitor._verify_rootless_socket(identity)  # Real socket was 0660.
            for socket_mode, owner in ((0o666, 1000), (0o664, 1000), (0o660, 1001)):
                mode.update(socket=socket_mode, owner=owner)
                with self.subTest(socket_mode=socket_mode, owner=owner):
                    with self.assertRaises(ValueError):
                        monitor._verify_rootless_socket(identity)

    def test_runtime_env_parser_matches_product_key_and_control_guards(self):
        home, _marker, current, _daemon, items, _runner, _calls, _account = (
            self.rootless_production_fixture())
        cid = current["roles"]["observer"]
        identity = SimpleNamespace(operator=home / ".config/porota-rc6/operator")
        def verify(lines):
            item = json.loads(json.dumps(items[cid]))
            item["Config"]["Env"] = lines
            return monitor._verify_rootless_item(
                item, cid=cid, role="observer", current=current, identity=identity)
        original = list(items[cid]["Config"]["Env"])
        self.assertEqual(verify(original)["Id"], cid)
        quoted = [line.replace("REAL_ORDER_CAPABILITY=BLOCKED",
                               "REAL_ORDER_CAPABILITY='BLOCKED'") for line in original]
        self.assertEqual(verify(quoted)["Id"], cid)
        for bad in ("lowercase_key=value", "BAD-NAME=value", "BAD\x00NAME=value",
                    "EXTRA=value\x01", "REAL_ORDER_CAPABILITY=ENABLED"):
            with self.subTest(invalid=repr(bad)):
                with self.assertRaises(ValueError):
                    verify(original + [bad])
        with self.assertRaises(ValueError):
            verify(original + ["POROTA_BUILD_SHA=" + SHA])  # Duplicate is ambiguous.

    def test_shadow_probe_executes_only_validated_private_observer_cid(self):
        calls = []
        payload = {"schema": "rc6.shadow-readonly-diagnostic.v1", "read_only": True,
                   "stages": [{"stage": "RUNTIME_INPUT", "status": "NOT_RUN_STAGE_ONLY"}]}
        def command(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload))
        context = SimpleNamespace(roles={"observer": "1" * 64, "dashboard": "2" * 64},
            host=SimpleNamespace(docker=SimpleNamespace(command_result=command)))
        with patch.object(monitor, "_rootless_context", return_value=context), patch.object(
                monitor.subprocess, "run", side_effect=AssertionError("rootful Docker forbidden")):
            result = monitor.shadow_diagnostic_host()
        self.assertEqual(result["stages"], payload["stages"])
        self.assertEqual(calls[0][0][:3], ("container", "exec", "1" * 64))
        self.assertEqual(calls[0][1]["timeout"], 9)

    def test_all_optional_readers_use_private_cids_and_never_default_docker(self):
        observer_cid, dashboard_cid = "1" * 64, "2" * 64
        calls = []
        def command(*args, **kwargs):
            calls.append(args)
            if args[:2] == ("container", "exec"):
                return SimpleNamespace(returncode=0, stdout=json.dumps({
                    "status": "API_OK", "total_count": 0, "latest_event": None}))
            return SimpleNamespace(returncode=0, stdout="")
        context = SimpleNamespace(roles={"observer": observer_cid, "dashboard": dashboard_cid},
            items={"observer": {"State": {"Status": "running", "OOMKilled": False}}},
            host=SimpleNamespace(identity=SimpleNamespace(
                    socket="/run/user/1000/porota-rc6-docker.sock",
                    environment=lambda: {"PATH": "/usr/bin:/bin"}),
                docker=SimpleNamespace(command_result=command)))
        log_calls = []
        def host_run(argv, **kwargs):
            if argv[:4] == ["/usr/bin/docker", "--host",
                            "unix:///run/user/1000/porota-rc6-docker.sock", "logs"]:
                log_calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
            if argv[0] != "systemctl":
                raise AssertionError("default Docker was accessed")
            return SimpleNamespace(returncode=0, stdout="active\n")
        with patch.object(monitor, "_rootless_context", return_value=context), patch.object(
                monitor.subprocess, "run", side_effect=host_run):
            state = monitor._host_control_state()
            schedule = monitor.schedule_api_host()
            lifecycle = monitor.observer_lifecycle_host()
            recent = monitor.recent_critical_host()
        self.assertEqual(state["observer_container"]["Status"], "running")
        self.assertEqual(schedule["status"], "API_OK")
        self.assertEqual(lifecycle["status"], "OK")
        self.assertEqual(set(recent["containers"]), {"observer", "dashboard"})
        self.assertTrue(any(args[:2] == ("events", "--since") and
                            "container=" + observer_cid in args for args in calls))
        self.assertEqual({args[-1] for args in log_calls if "--timestamps" in args},
                         {observer_cid, dashboard_cid})
        self.assertTrue(all("porota_production_" not in str(args) for args in calls + log_calls))

    def test_normal_alert_delivery_uses_private_observer_cid_with_ack_only(self):
        calls = []
        def command(*args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0,
                                   stdout=json.dumps({"status": "SENT", "message_id": 317}))
        context = SimpleNamespace(roles={"observer": "1" * 64},
            host=SimpleNamespace(docker=SimpleNamespace(command_result=command)))
        request = {"transition_id": "a" * 64,
                   "body": "POROTA RC6 | ALERT\nRUNTIME_INVALID\nPAPER/SHADOW ONLY"}
        encoded = monitor.base64.urlsafe_b64encode(json.dumps(request).encode()).decode()
        with patch.object(monitor, "_rootless_context", return_value=context), patch.object(
                monitor.subprocess, "run", side_effect=AssertionError("rootful Docker forbidden")), patch.object(
                monitor, "deliver_transition_once", side_effect=lambda _p, _id, body, send: send(body)):
            receipt = monitor.alert_host(encoded)
        self.assertEqual(receipt["status"], "SENT")
        self.assertEqual(receipt["message_id"], "317")
        self.assertEqual(calls[0][:3], ("container", "exec", "1" * 64))

    def test_actions_monitor_drops_sudo_and_push_is_non_delivering(self):
        workflow = (SOURCE.parents[1] / ".github/workflows/rc6-market-watch-readonly.yml").read_text()
        self.assertNotIn("sudo -n timeout", workflow)
        self.assertEqual(workflow.count('"timeout '), 7)
        self.assertIn("github.event_name == 'schedule' && 'normal'", workflow)
        self.assertIn("inputs.notification_mode || 'read_only'", workflow)
        self.assertIn("for name in ('observer','dashboard')", workflow)
        self.assertIn("if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'", workflow)
        self.assertIn("tree_sha=", workflow)
        self.assertIn("--expected-tree-sha '$EXPECTED_TREE_SHA'", workflow)
        for header in ("Product branch HEAD", "Candidate source SHA",
                       "Expected product tree", "Provenance identity"):
            self.assertIn(header, workflow)

    def test_only_rootless_pair_matters_without_legacy_broker_dependency(self):
        current = self.capture()
        self.assertEqual(set(current["containers"]), {"observer", "dashboard"})
        self.inspect["dashboard"]["image"] = "sha256:" + "d" * 64
        self.assertIn("CONTAINER_IMAGE_MISMATCH:dashboard",
                      self.codes(self.capture(), "P0"))
        self.inspect["dashboard"]["image"] = IMAGE
        self.assertNotIn("CONTAINER_IMAGE_MISMATCH:dashboard", self.codes(self.capture()))

    def test_live_probe_uses_completed_db_read_clock_without_future_or_stale_bypass(self):
        future=(NOW+timedelta(milliseconds=200)).isoformat()
        with sqlite3.connect(self.root/monitor.DB_REL) as db:
            db.execute('CREATE TABLE paper_notification_worker(id INTEGER PRIMARY KEY,state TEXT,heartbeat_at TEXT)')
            db.execute("INSERT INTO paper_notification_worker VALUES(1,'RUNNING',?)",(future,))
        def capture_live():
            with patch.object(monitor,'now_utc',side_effect=[NOW,NOW+timedelta(seconds=1)]), patch.object(
                    monitor,'independent_session',return_value={'state':'OPEN'}), patch.object(
                    monitor,'_resources',return_value=self.resources), patch.object(
                    monitor,'_host_control_state',return_value={}):
                return monitor.probe(root=self.root,expected_sha=SHA,inspect=self.inspect)
        value=capture_live()
        self.assertNotIn('WORKER_HEARTBEAT_INVALID:paper_notification_worker',self.codes(value))
        self.assertEqual(value['collection_started_at'],NOW.isoformat())
        self.assertEqual(value['generated_at'],(NOW+timedelta(seconds=1)).isoformat())
        for bad in (NOW+timedelta(seconds=2),NOW-timedelta(seconds=301)):
            with sqlite3.connect(self.root/monitor.DB_REL) as db:
                db.execute('UPDATE paper_notification_worker SET heartbeat_at=?',(bad.isoformat(),))
            self.assertIn('WORKER_HEARTBEAT_INVALID:paper_notification_worker',self.codes(capture_live()))

    def test_open_contract_counts_do_not_invent_missing_ledgers_or_inflight_authority(self):
        value=self.capture()['paper_open_contracts']
        self.assertIsNone(value['paper_positions'])
        self.assertEqual(value['inflight_evaluation_or_fill'],'NO_VERIFICADO')
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE paper_positions(id INTEGER PRIMARY KEY,status TEXT)')
            db.execute("INSERT INTO paper_positions VALUES(1,'OPEN')")
        self.assertEqual(self.capture()['paper_open_contracts']['paper_positions'],1)
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE paper_positions SET status='CLOSED'")
        self.assertEqual(self.capture()['paper_open_contracts']['paper_positions'],0)
        self.assertIsNone(self.capture()['paper_open_contracts']['paper_future_positions'])

    def test_independent_watchdog_ack_is_read_only_and_not_schedule_cadence(self):
        path=self.root/'data/paper_v17/market_watch_silence.json'
        path.write_text(json.dumps({'schema':'rc6.market-watch-silence.v1','status':'SILENT',
            'acknowledged_at':NOW.isoformat(),'message_id':'967','secret':'hidden'}))
        value=self.capture()['schedule_watchdog_receipt']
        self.assertEqual(value['message_id'],'967')
        self.assertEqual(value['status'],'SILENT')
        self.assertEqual(value['ack_age_seconds'],0)
        self.assertNotIn('hidden',json.dumps(value))
        path.write_text(json.dumps({'schema':'rc6.market-watch-silence.v1','status':'OK',
            'acknowledged_at':NOW.isoformat(),'message_id':'0'}))
        self.assertEqual(self.capture()['schedule_watchdog_receipt']['status'],'NO_VERIFICADO')
        path.write_text(json.dumps({'schema':'rc6.market-watch-silence.v1','status':'OK',
            'acknowledged_at':(NOW+timedelta(seconds=1)).isoformat(),'message_id':'2'}))
        self.assertEqual(self.capture()['schedule_watchdog_receipt']['status'],'NO_VERIFICADO')

    def test_shadow_sqlite_context_is_allowlisted(self):
        payload = {"as_of": NOW.isoformat(), "status": "FAIL_CLOSED",
                   "real_orders_sent": 0, "real_routes": "NOT_CALLED",
                   "error_class": "OperationalError", "sqlite_errorcode": 9,
                   "sqlite_errorname": "SQLITE_INTERRUPT",
                   "error_module": "runtime", "error_line": 76}
        self.shadow_path.write_text(json.dumps({
            "payload": payload, "digest": monitor.canonical_digest(payload)}))
        record = self.capture()
        self.assertEqual(record["shadow"]["sqlite_errorname"], "SQLITE_INTERRUPT")
        self.assertEqual(record["shadow"]["error_module"], "runtime")
        self.assertEqual(record["shadow"]["error_line"], 76)
        self.assertIn("DYNAMIC_SHADOW_FAIL_CLOSED", self.codes(record, "P1"))

        payload.update(sqlite_errorname="SQLITE_INTERRUPT secret",
                       error_module={"path": "/app/private"},
                       error_line="76;secret")
        self.shadow_path.write_text(json.dumps({
            "payload": payload, "digest": monitor.canonical_digest(payload)}))
        rejected = self.capture()["shadow"]
        self.assertNotIn("sqlite_errorname", rejected)
        self.assertNotIn("error_module", rejected)
        self.assertNotIn("error_line", rejected)

    def test_shadow_value_error_keeps_only_reviewed_site(self):
        payload = {"as_of": NOW.isoformat(), "status": "FAIL_CLOSED",
                   "real_orders_sent": 0, "real_routes": "NOT_CALLED",
                   "error_class": "ValueError", "error_module": "stages",
                   "error_line": 42}
        self.shadow_path.write_text(json.dumps({
            "payload": payload, "digest": monitor.canonical_digest(payload)}))
        record = self.capture()
        self.assertEqual(record["shadow"]["error_module"], "stages")
        self.assertEqual(record["shadow"]["error_line"], 42)
        self.assertIn("DYNAMIC_SHADOW_FAIL_CLOSED", self.codes(record, "P1"))

        payload["error_module"] = "stages/private-token"
        self.shadow_path.write_text(json.dumps({
            "payload": payload, "digest": monitor.canonical_digest(payload)}))
        self.assertNotIn("error_module", self.capture()["shadow"])

        payload.update(error_module="cold_audit", error_line=595)
        self.shadow_path.write_text(json.dumps({
            "payload": payload, "digest": monitor.canonical_digest(payload)}))
        observed = self.capture()["shadow"]
        self.assertEqual(observed["error_module"], "cold_audit")
        self.assertEqual(observed["error_line"], 595)
        payload["error_module"] = "cold_audit/private-token"
        self.shadow_path.write_text(json.dumps({
            "payload": payload, "digest": monitor.canonical_digest(payload)}))
        self.assertNotIn("error_module", self.capture()["shadow"])

    def test_shadow_payload_context_is_strictly_allowlisted(self):
        payload = {"as_of": NOW.isoformat(), "status": "FAIL_CLOSED",
                   "real_orders_sent": 0, "real_routes": "NOT_CALLED",
                   "error_class": "EvidencePayloadLimit",
                   "evidence_file": "latest.json.gz",
                   "evidence_measured_bytes": 70 * 1024**2,
                   "evidence_limit_bytes": 64 * 1024**2}
        self.shadow_path.write_text(json.dumps({
            "payload": payload, "digest": monitor.canonical_digest(payload)}))
        record = self.capture()
        self.assertEqual(record["shadow"]["evidence_file"], "latest.json.gz")
        self.assertEqual(record["shadow"]["evidence_measured_bytes"], 70 * 1024**2)
        self.assertIn("DYNAMIC_SHADOW_FAIL_CLOSED", self.codes(record, "P1"))
        payload.update(evidence_file="preopen-private-token",
                       evidence_measured_bytes="private-token",
                       evidence_limit_bytes=0)
        self.shadow_path.write_text(json.dumps({
            "payload": payload, "digest": monitor.canonical_digest(payload)}))
        rejected = self.capture()["shadow"]
        self.assertNotIn("evidence_file", rejected)
        self.assertNotIn("evidence_measured_bytes", rejected)
        self.assertNotIn("evidence_limit_bytes", rejected)
        self.assertNotIn("private-token", str(rejected))

    def test_manual_shadow_diagnostic_redacts_sqlite_failure_and_never_ticks(self):
        runtime = types.ModuleType("rc6_dynamic_universe.runtime")
        shadow = types.ModuleType("rc6_shadow_runtime.worker")
        def failing_read(*args, **kwargs):
            raise sqlite3.OperationalError("secret SQL text and account data")
        class Reader:
            @staticmethod
            def _metadata(obj, at, since):
                return "readonly", []
            def tick(self, *args):
                self.fail("diagnostic must never invoke a writer")
        runtime.read_runtime = failing_read
        shadow.ShadowRuntime = Reader
        with patch.dict(sys.modules, {
                "rc6_dynamic_universe.runtime": runtime,
                "rc6_shadow_runtime.worker": shadow}), patch.dict(
                "os.environ", {"PAPER_V17_DB_PATH": str(self.db)}):
            record = monitor.shadow_diagnostic()
        self.assertEqual([x["stage"] for x in record["stages"]],
                         ["RUNTIME_INPUT", "METADATA"])
        self.assertEqual([x["status"] for x in record["stages"]],
                         ["FAIL_CLOSED", "OK"])
        self.assertEqual(record["stages"][0]["error_class"], "OperationalError")
        self.assertNotIn("secret", json.dumps(record))
        self.assertTrue(record["read_only"])

    def test_provenance_divergence_is_critical(self):
        payload = json.loads(self.provenance_path.read_text())
        payload["deploy_sha"] = "d" * 40
        self.provenance_path.write_text(json.dumps(payload))
        self.assertIn("DEPLOY_PROVENANCE_INVALID", self.codes(self.capture(), "P0"))

    def test_sql_counter_is_fail_closed(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE observer_state SET real_orders_sent=NULL")
        self.assertIn("REAL_ORDER_COUNTER_INVALID", self.codes(self.capture(), "P0"))

    def test_boolean_counter_is_not_exact_zero(self):
        original = monitor._db_snapshot
        def malformed(path):
            record = original(path)
            record["observer"]["real_orders_sent"] = False
            return record
        with patch.object(monitor, "_db_snapshot", side_effect=malformed):
            self.assertIn("REAL_ORDER_COUNTER_INVALID", self.codes(self.capture(), "P0"))

    def test_heartbeat_future_is_not_healthy(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE observer_state SET heartbeat_at=?",
                               ((NOW + timedelta(minutes=3)).isoformat(),))
        self.assertIn("OBSERVER_HEARTBEAT_INVALID", self.codes(self.capture(), "P0"))

    def test_explicit_fail_closed_is_p1_even_in_preopen(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE observer_state SET session_state='PREOPEN'")
        envelope = json.loads(self.shadow_path.read_text())
        envelope["payload"]["status"] = "FAIL_CLOSED"
        envelope["payload"]["error_class"] = "ValueError"
        envelope["digest"] = monitor.canonical_digest(envelope["payload"])
        self.shadow_path.write_text(json.dumps(envelope))
        observation = self.capture()
        self.assertIn("DYNAMIC_SHADOW_FAIL_CLOSED", self.codes(observation, "P1"))
        self.assertEqual(observation["shadow"]["error_class"], "ValueError")

    def test_shadow_hash_corruption_is_detected(self):
        envelope = json.loads(self.shadow_path.read_text())
        envelope["payload"]["status"] = "SHADOW_OBSERVING_MODIFIED"
        self.shadow_path.write_text(json.dumps(envelope))
        self.assertIn("DYNAMIC_SHADOW_STATUS_MISSING_OR_INVALID",
                      self.codes(self.capture(), "P1"))

    def test_shadow_stale_when_market_open(self):
        self.write_shadow(as_of=(NOW - timedelta(minutes=10)).isoformat())
        self.assertIn("DYNAMIC_SHADOW_NOT_PROGRESSING", self.codes(self.capture(), "P1"))

    def test_source_stale_is_not_replaced_by_synthetic_data(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE observer_state SET last_market_data_at=?",
                               ((NOW - timedelta(minutes=30)).isoformat(),))
        current = self.capture()
        self.assertIn("PPI_MARKETDATA_NOT_FRESH", self.codes(current, "P1"))
        self.assertEqual(current["broker_requests"], 0)

    def test_closed_market_does_not_require_fills_or_live_quotes(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE observer_state SET session_state='MARKET_CLOSED',"
                               "last_market_data_at=?", ((NOW - timedelta(days=1)).isoformat(),))
        with patch.object(monitor, 'independent_session', return_value={'state':'CLOSED'}):
            with patch.object(monitor, '_resources', return_value=self.resources), patch.object(monitor, '_host_control_state', return_value={}):
                result=monitor.probe(root=self.root,at=NOW,expected_sha=SHA,inspect=self.inspect)
        self.assertEqual(result['severity'], 'GREEN')

    def test_observer_false_closed_does_not_blind_market_monitor(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE observer_state SET session_state='MARKET_CLOSED',last_market_data_at=?", ((NOW-timedelta(days=1)).isoformat(),))
        result=self.capture()
        self.assertIn('OBSERVER_SESSION_DISAGREEMENT',self.codes(result,'P1'))
        self.assertIn('PPI_MARKETDATA_NOT_FRESH',self.codes(result,'P1'))

    def test_schedule_manual_push_and_retry_never_prove_cadence(self):
        fake=[{'event':'push','created_at':NOW.isoformat()},
              {'event':'workflow_dispatch','created_at':NOW.isoformat()},
              {'event':'schedule','run_attempt':2,'created_at':NOW.isoformat()}]
        self.assertEqual(monitor.schedule_health(fake,NOW,expected_open=True)['state'],'SCHEDULE_SILENT')
        real=[{'event':'schedule','status':'completed','run_attempt':1,'run_started_at':(NOW-timedelta(minutes=m)).isoformat()} for m in (1,11,21)]
        self.assertEqual(monitor.schedule_health(real,NOW,expected_open=True)['state'],'TWO_INTERVALS_OBSERVED')
        self.assertEqual(monitor.schedule_health([real[0]]*3,NOW,expected_open=True)['state'],'NO_VERIFICADO')
        self.assertEqual(monitor.schedule_health([{k:v for k,v in r.items() if k!='run_attempt'} for r in real],NOW,expected_open=True)['state'],'SCHEDULE_SILENT')
        self.assertFalse(monitor.schedule_health([],NOW,expected_open=False)['silence'])
        snapshot=self.capture()
        snapshot['schedule_watchdog_receipt']={'status':'SILENT','message_id':'932','ack_age_seconds':80000}
        monitor.add_schedule_health(snapshot,'mbalbo2023/Porota-trading',get=lambda _: {'workflow_runs':[]})
        self.assertEqual(snapshot['schedule_health']['independent_watchdog_ack'],'HISTORICAL_VERIFIED')
        self.assertEqual(snapshot['schedule_health']['state'],'SCHEDULE_SILENT')

    def test_memory_shortage_is_not_hidden(self):
        self.resources["mem_available_bytes"] = 20 * 1024**2
        self.assertIn("MEMORY_PRESSURE", self.codes(self.capture(), "P1"))

    def test_docker_oom_is_critical(self):
        self.inspect["observer"]["oom"] = True
        self.assertIn("CONTAINER_NOT_HEALTHY:observer",
                      self.codes(self.capture(), "P0"))

    def test_prior_oom_keeps_history_without_false_current_p0(self):
        self.inspect['observer']['oom']=True
        detail={'observer_container':{'Status':'running','ExitCode':0,
            'StartedAt':(NOW-timedelta(hours=2)).isoformat(),
            'FinishedAt':(NOW-timedelta(hours=3)).isoformat()}}
        value=self.capture(host_state=detail)
        self.assertIn('OOM_HISTORICAL:observer',self.codes(value,'P2'))
        self.assertNotIn('CONTAINER_NOT_HEALTHY:observer',self.codes(value,'P0'))
        detail['observer_container']['FinishedAt']=NOW.isoformat()
        self.assertIn('CONTAINER_NOT_HEALTHY:observer',self.codes(self.capture(host_state=detail),'P0'))
        detail['observer_container']['FinishedAt']=(NOW-timedelta(hours=3)).isoformat()
        with sqlite3.connect(self.db) as connection:
            connection.execute('UPDATE observer_state SET heartbeat_at=?',((NOW-timedelta(minutes=4)).isoformat(),))
        self.assertIn('CONTAINER_NOT_HEALTHY:observer',self.codes(self.capture(host_state=detail),'P0'))

    def test_p0_immediate_but_p1_needs_two_samples(self):
        first = self.capture()
        first["findings"] = [monitor.finding("ENGINE_STALE", "P1")]
        first["severity"] = "P1"
        one = monitor.classify(first)
        self.assertEqual(one["pending_confirmation"], ["ENGINE_STALE"])
        self.assertEqual(one["new_alerts"], [])
        second = dict(first, generated_at=(NOW + timedelta(minutes=10)).isoformat())
        two = monitor.classify(second, first, one)
        self.assertEqual(two["new_alerts"], ["ENGINE_STALE"])
        two = monitor.finalize_delivery(two, {"status": "SENT", "message_id": "123"})
        third = dict(second, generated_at=(NOW + timedelta(minutes=20)).isoformat())
        three = monitor.classify(third, second, two)
        self.assertEqual(three["new_alerts"], [])
        three = monitor.finalize_delivery(three, {"status": "NO_TRANSITION"})
        recovered = dict(third, findings=[], severity="GREEN",
                         generated_at=(NOW + timedelta(minutes=30)).isoformat())
        four = monitor.classify(recovered, third, three)
        self.assertEqual(four["recovered"], ["ENGINE_STALE"])
        p0 = dict(first, findings=[monitor.finding("PAPER_INVALID", "P0")], severity="P0")
        self.assertEqual(monitor.classify(p0)["new_alerts"], ["PAPER_INVALID"])

    def test_unacknowledged_transition_retries_and_recovery_requires_ack(self):
        first = self.capture()
        first["findings"] = [monitor.finding("ENGINE_STALE", "P1")]
        first["severity"] = "P1"
        one = monitor.classify(first)
        second = dict(first, generated_at=(NOW + timedelta(minutes=10)).isoformat())
        two = monitor.finalize_delivery(monitor.classify(second, first, one),
                                        {"status": "NOT_CONFIGURED"})
        self.assertEqual(two["acknowledged_codes"], [])
        third = dict(second, generated_at=(NOW + timedelta(minutes=20)).isoformat())
        retry = monitor.classify(third, second, two)
        self.assertEqual(retry["new_alerts"], ["ENGINE_STALE"])
        acknowledged = monitor.finalize_delivery(retry, {"status": "SENT", "message_id": "7"})
        self.assertEqual(acknowledged["acknowledged_codes"], ["ENGINE_STALE"])
        recovered = dict(third, findings=[], severity="GREEN",
                         generated_at=(NOW + timedelta(minutes=30)).isoformat())
        recovery = monitor.classify(recovered, third, acknowledged)
        self.assertEqual(recovery["recovered"], ["ENGINE_STALE"])
        failed = monitor.finalize_delivery(recovery, {"status": "FAILED"})
        self.assertEqual(failed["acknowledged_codes"], ["ENGINE_STALE"])
        assert monitor.prepare_alert(recovery)

    def test_same_run_prior_attempt_confirms_p1_and_requires_telegram_ack(self):
        prior = self.capture()
        prior["generated_at"] = (NOW - timedelta(minutes=10)).isoformat()
        prior["findings"] = [monitor.finding("DYNAMIC_SHADOW_FAIL_CLOSED", "P1")]
        prior["severity"] = "P1"
        earlier = monitor.classify(prior)
        with io.BytesIO() as stream:
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("snapshot.json", json.dumps(prior))
                archive.writestr("decision.json", json.dumps(earlier))
            raw = stream.getvalue()
        artifact = {"id": 123, "name": "rc6-market-watch-88", "expired": False,
                    "size_in_bytes": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                    "created_at": prior["generated_at"], "workflow_run": {"id": 88}}
        current = dict(prior, generated_at=NOW.isoformat())
        calls = []
        def get(url, _limit):
            calls.append(url)
            if url.endswith("/runs/88/artifacts?per_page=100"):
                return json.dumps({"artifacts": [artifact]}).encode()
            if "workflows/384354765/runs" in url:
                return json.dumps({"workflow_runs": []}).encode()
            if url.endswith("/artifacts/123/zip"):
                return raw
            self.fail("unexpected API request")
        receipt = monitor.read_previous_artifact(current, "mbalbo2023/Porota-trading",
            384354765, 88, self.root / "prior", get=get)
        self.assertEqual(receipt["run_id"], 88)
        self.assertEqual(receipt["artifact_sha256"], artifact["digest"])
        self.assertTrue(any("runs/88/artifacts" in url for url in calls))
        previous = json.loads((self.root / "prior/snapshot.json").read_text())
        decision = monitor.classify(current, previous, earlier)
        self.assertTrue(decision["previous_comparable"])
        self.assertEqual(decision["new_alerts"], ["DYNAMIC_SHADOW_FAIL_CLOSED"])
        self.assertEqual(monitor.finalize_delivery(decision, {"status": "UNVERIFIED"})[
            "acknowledged_codes"], [])
        self.assertEqual(monitor.finalize_delivery(decision, {"status": "SENT",
            "message_id": "909"})["acknowledged_codes"], ["DYNAMIC_SHADOW_FAIL_CLOSED"])
        corrupted = dict(artifact, digest="sha256:" + "0" * 64)
        self.assertIsNone(monitor.verified_previous_zip(raw, corrupted, current))
        wrong_sha = dict(current, expected_deploy_sha="d" * 40)
        self.assertIsNone(monitor.verified_previous_zip(raw, artifact, wrong_sha))
        stale = dict(current, generated_at=(NOW + timedelta(minutes=31)).isoformat())
        self.assertIsNone(monitor.verified_previous_zip(raw, artifact, stale))

    def test_other_run_lookup_stops_at_first_verified_artifact(self):
        current = self.capture()
        earlier = dict(current, generated_at=(NOW - timedelta(minutes=3)).isoformat())
        prior_decision = monitor.classify(earlier)
        with io.BytesIO() as stream:
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("snapshot.json", json.dumps(earlier))
                archive.writestr("decision.json", json.dumps(prior_decision))
            raw = stream.getvalue()
        artifact = {"id": 321, "name": "rc6-market-watch-87", "expired": False,
            "size_in_bytes": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "created_at": earlier["generated_at"], "workflow_run": {"id": 87}}
        calls = []
        def get(url, _limit):
            calls.append(url)
            if "/runs/88/artifacts" in url:
                return b'{"artifacts":[]}'
            if "/workflows/384354765/runs" in url:
                return json.dumps({"workflow_runs": [
                    {"id": 88, "status": "in_progress"},
                    {"id": 87, "status": "completed"},
                    {"id": 86, "status": "completed"}]}).encode()
            if "/runs/87/artifacts" in url:
                return json.dumps({"artifacts": [artifact]}).encode()
            if "/artifacts/321/zip" in url:
                return raw
            self.fail("older or unexpected run fetched")
        receipt = monitor.read_previous_artifact(current, "mbalbo2023/Porota-trading",
            384354765, 88, self.root / "lazy", get=get)
        self.assertEqual(receipt["run_id"], 87)
        self.assertEqual(len(calls), 4)
        self.assertFalse(any("/runs/86/" in url for url in calls))

    def test_default_lookup_uses_runner_gh_without_token_on_argv(self):
        current = self.capture()
        calls = []
        def invoke(command, **options):
            calls.append(command)
            options["stdout"].write(b'{"artifacts":[]}' if "/runs/88/" in command[-1]
                                    else b'{"workflow_runs":[]}')
            return subprocess.CompletedProcess(command, 0)
        with patch.object(monitor.subprocess, "run", side_effect=invoke), patch.dict(
                monitor.os.environ, {"GH_TOKEN": "private-test-token"}):
            self.assertIsNone(monitor.read_previous_artifact(current,
                "mbalbo2023/Porota-trading", 384354765, 88, self.root / "none"))
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(command[:2] == ["gh", "api"] for command in calls))
        self.assertNotIn("private-test-token", str(calls))

    def test_telegram_host_transport_acks_only_message_id_and_never_exposes_token(self):
        body = "POROTA RC6 | ALERT\nOBSERVER_HEARTBEAT_INVALID"
        request={'body':body,'transition_id':'c'*64}
        encoded = monitor.base64.urlsafe_b64encode(json.dumps(request).encode()).decode()
        send = Mock(return_value=subprocess.CompletedProcess(
            [], 0, stdout='{"status":"SENT","message_id":"34"}', stderr="token=secret"))
        context = self.rootless_context(send)
        with patch.object(monitor, "_rootless_context", return_value=context), patch.object(
                monitor,'ROOT',self.root):
            receipt=monitor.alert_host(encoded)
            self.assertEqual(receipt['status'],'SENT')
            self.assertEqual(receipt['message_id'],'34')
            self.assertFalse(receipt['deduplicated'])
            self.assertTrue(monitor.alert_host(encoded)['deduplicated'])
            self.assertEqual(send.call_count,1)
        args, options = send.call_args
        self.assertEqual(args[:3], ("container", "exec", "1" * 64))
        self.assertEqual(options["timeout"], 19)
        compile(args[-2], "<telegram-child>", "exec")
        unsent = Mock(return_value=subprocess.CompletedProcess(
            [], 0, stdout='{"status":"SENT"}', stderr="token=secret"))
        with patch.object(monitor, "_rootless_context", return_value=self.rootless_context(unsent)),patch.object(monitor,'ROOT',self.root):
            request['transition_id']='d'*64
            encoded=monitor.base64.urlsafe_b64encode(json.dumps(request).encode()).decode()
            self.assertEqual(monitor.alert_host(encoded)["status"], "UNVERIFIED")

    def test_same_recovery_from_two_branch_probes_delivers_once_and_new_incident_delivers(self):
        previous=self.capture(at=NOW-timedelta(minutes=10))
        previous['findings']=[monitor.finding('NO_QUOTES','P0')]
        old=monitor.classify(previous)
        monitor.finalize_delivery(old,{'status':'SENT','message_id':'980'})
        one=monitor.classify(self.capture(),previous,old)
        two=monitor.classify(self.capture(at=NOW+timedelta(minutes=1)),previous,old)
        self.assertEqual(one['notification'],'RECOVERY')
        self.assertEqual(one['transition_id'],two['transition_id'])
        sends=[]
        def send(body):
            sends.append(body)
            return {'status':'SENT','message_id':str(980+len(sends))}
        path=self.root/'receipts.sqlite'
        for decision in (one,two):
            request=json.loads(monitor.base64.urlsafe_b64decode(monitor.prepare_alert(decision)))
            receipt=monitor.deliver_transition_once(path,request['transition_id'],request['body'],send)
            monitor.finalize_delivery(decision,receipt)
            self.assertEqual(decision['acknowledged_codes'],[])
        self.assertEqual(len(sends),1)
        newer=self.capture(at=NOW+timedelta(minutes=5));newer['findings']=previous['findings']
        alarm=monitor.classify(newer)
        monitor.finalize_delivery(alarm,{'status':'SENT','message_id':'982'})
        recovery=monitor.classify(self.capture(at=NOW+timedelta(minutes=6)),newer,alarm)
        self.assertNotEqual(recovery['transition_id'],one['transition_id'])
        self.assertEqual(monitor.deliver_transition_once(path,recovery['transition_id'],'POROTA RC6 | RECOVERY',send)['status'],'SENT')
        self.assertEqual(len(sends),2)

    def test_uncertain_ack_restart_never_manufactures_ack_or_resends(self):
        path=self.root/'receipts.sqlite';calls=[]
        def crash(body):
            calls.append(body)
            raise TimeoutError('unknown remote outcome')
        first=monitor.deliver_transition_once(path,'f'*64,'POROTA RC6 | ALERT',crash)
        second=monitor.deliver_transition_once(path,'f'*64,'POROTA RC6 | ALERT',crash)
        self.assertEqual(first['status'],second['status'],'UNVERIFIED')
        self.assertEqual(len(calls),1)

    def test_delivery_concurrent_reservation_and_invalid_receipts_fail_closed(self):
        path=self.root/'receipts.sqlite';nested=[]
        def send(body):
            nested.append(monitor.deliver_transition_once(path,'e'*64,body,lambda b:self.fail('double send')))
            return {'status':'SENT','message_id':'12'}
        self.assertEqual(monitor.deliver_transition_once(path,'e'*64,'POROTA RC6 | ALERT',send)['status'],'SENT')
        self.assertEqual(nested[0]['status'],'UNVERIFIED')
        for value in (None,0,True,'0','bad'):
            result=monitor.deliver_transition_once(path,hashlib.sha256(str(value).encode()).hexdigest(),
                'POROTA RC6 | ALERT',lambda b:{'status':'SENT','message_id':value})
            self.assertEqual(result['status'],'UNVERIFIED')

    def test_incompatible_old_sha_cannot_confirm_p1(self):
        current = self.capture()
        current["findings"] = [monitor.finding("NO_QUOTES", "P1")]
        previous = dict(current, expected_deploy_sha="b" * 40,
                        generated_at=(NOW - timedelta(minutes=10)).isoformat())
        self.assertEqual(monitor.classify(current, previous)["new_alerts"], [])

    def test_expired_previous_sample_cannot_confirm_p1(self):
        current = self.capture()
        current["findings"] = [monitor.finding("NO_QUOTES", "P1")]
        previous = dict(current, generated_at=(NOW - timedelta(minutes=35)).isoformat())
        self.assertEqual(monitor.classify(current, previous)["new_alerts"], [])

    def test_transport_failure_does_not_claim_zero_orders_verified(self):
        result = monitor.transport_error()
        decision = monitor.classify(result)
        self.assertEqual(decision["new_alerts"], ["REMOTE_PROBE_UNAVAILABLE"])
        self.assertFalse(decision["real_orders_sent_verified"])

    def test_schedule_clock_is_local_and_conservative(self):
        self.assertTrue(monitor.active_hours(NOW))
        self.assertFalse(monitor.active_hours(datetime(2026, 10, 14, 12, tzinfo=timezone.utc)))
        self.assertFalse(monitor.active_hours(datetime(2026, 10, 18, 14, tzinfo=timezone.utc)))

    def test_com19031_late_reference_window_is_not_universal_market_open(self):
        art = monitor.ART
        old = datetime(2026, 10, 30, 17, 30, tzinfo=art)
        late = datetime(2026, 11, 2, 17, 30, tzinfo=art)
        after = datetime(2026, 11, 2, 18, 15, tzinfo=art)
        self.assertFalse(monitor.late_source_observation_hours(old))
        self.assertTrue(monitor.late_source_observation_hours(late))
        self.assertTrue(monitor.active_hours(late))
        self.assertFalse(monitor.active_hours(after))
        current = {'expected_session':{'state':'CLOSED','operational_day':True},
                   'generated_at':late.isoformat(), 'findings':[]}
        monitor.add_schedule_health(current, 'mbalbo2023/Porota-trading',
                                    get=lambda url:{'workflow_runs':[]})
        self.assertEqual(current['schedule_health']['state'], 'SCHEDULE_SILENT')
        current['expected_session']['operational_day'] = False
        monitor.add_schedule_health(current, 'mbalbo2023/Porota-trading',
                                    get=lambda url:{'workflow_runs':[]})
        self.assertFalse(current['schedule_health']['silence'])

    def test_com19031_main_workflow_has_last_1807_tick_and_date_aware_gate(self):
        workflow = (SOURCE.parents[1] / '.github/workflows/rc6-market-watch-readonly.yml').read_text()
        self.assertIn("cron: '7 21 * * 1-5'", workflow)
        self.assertIn('close = 18*60+15 if late else 17*60+15', workflow)
        self.assertIn('date(2026,11,2)', workflow)

    def test_late_mixed_observer_state_cannot_assert_spot_scope_failure(self):
        late = datetime(2026, 11, 2, 17, 30, tzinfo=monitor.ART)
        with patch.object(monitor, 'independent_session', return_value={
                'state':'CLOSED','scope':'BYMA_PAPER_SPOT','operational_day':True}), \
             patch.object(monitor, '_resources', return_value=self.resources), \
             patch.object(monitor, '_host_control_state', return_value={}):
            snapshot = monitor.probe(root=self.root, at=late, expected_sha=SHA,
                                     inspect=self.inspect)
        self.assertTrue(snapshot['late_source_observation_window'])
        self.assertEqual(snapshot['late_source_contract_scope'], 'EXACT_BYMA_T1_REFERENCE_ONLY')
        self.assertNotIn('OBSERVER_SESSION_DISAGREEMENT', self.codes(snapshot))
        self.assertIn('OBSERVER_LATE_SOURCE_SCOPE_UNVERIFIED', self.codes(snapshot, 'P2'))

    def test_host_diagnostic_uses_bounded_container_read_and_redacts_stderr(self):
        payload = {"schema": "rc6.shadow-readonly-diagnostic.v1", "read_only": True,
                   "stages": [{"stage": "RUNTIME_INPUT", "status": "OK"}],
                   "shadow_worker_process_count": 1,
                   "shadow_worker_processes": [{"state": "S", "wchan": "futex_wait_queue"}]}
        run = Mock(return_value=subprocess.CompletedProcess(
            [], 0, stdout=json.dumps(payload), stderr="secret"))
        with patch.object(monitor, "_rootless_context", return_value=self.rootless_context(run)):
            result = monitor.shadow_diagnostic_host()
            self.assertEqual(result["stages"], payload["stages"])
            self.assertEqual(result["shadow_worker_processes"], payload["shadow_worker_processes"])
        args, options = run.call_args
        self.assertEqual(args[:4], ("container", "exec", "1" * 64, "python"))
        self.assertEqual(options["timeout"], 9)
        compile(args[-1], "<container-diagnostic>", "exec")
        for snippet in ("EXPLAIN QUERY PLAN", "NOT INDEXED", 'result["query_stages"]',
                        'observation_limit=3001', 'result["observation_source_limit_with_sentinel"]',
                        'b"--dynamic-shadow-worker" not in argv',
                        'result["shadow_worker_process_count"]',
                        'c.set_progress_handler(expired,1000)', 'NOT_RUN_STAGE_ONLY'):
            self.assertIn(snippet, args[-1])
        self.assertNotIn('read_runtime(db,as_of=at', args[-1])
        with patch.object(monitor, "_rootless_context", return_value=self.rootless_context(
                Mock(side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=9, stderr="secret")))):
            failure = monitor.shadow_diagnostic_host()
        self.assertEqual(failure["status"], "CONTAINER_PROBE_TIMEOUT")
        self.assertNotIn("secret", json.dumps(failure))

    def test_host_state_survives_optional_container_diagnostic_timeout(self):
        def read(command, **kwargs):
            if command[:2] == ["systemctl", "is-active"]:
                status = "inactive" if command[-1] == "porota-functional-health-rc6.timer" else "active"
                return subprocess.CompletedProcess(command, 0, stdout=status + "\n", stderr="secret")
            if command[:2] == ['systemctl','show']:
                return subprocess.CompletedProcess(command,0,stdout='Result=success\nExecMainStatus=0\nEnv=secret\n',stderr='secret')
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({
                "Status": "running", "StartedAt": "2026-10-14T15:30:00Z",
                "ExitCode": 0, "OOMKilled": False, "Env": "secret"}), stderr="secret")
        context = self.rootless_context()
        context.items = {"observer": {"State": {
            "Status": "running", "StartedAt": "2026-10-14T15:30:00Z",
            "ExitCode": 0, "OOMKilled": False, "Env": "secret"}}}
        with patch.object(monitor.subprocess, "run", side_effect=read), patch.object(
                monitor, "_rootless_context", return_value=context):
            result = monitor._host_control_state()
        self.assertEqual(result["legacy_health_timer"], "inactive")
        self.assertEqual(result["fast_health_timer"], "active")
        self.assertEqual(result['schedule_silence_timer'],'active')
        self.assertEqual(result['schedule_silence_service'],{'Result':'success','ExecMainStatus':'0'})
        self.assertEqual(result["observer_container"]["Status"], "running")
        self.assertNotIn("secret", json.dumps(result))

    def test_manual_public_schedule_api_probe_is_bounded_and_redacted(self):
        payload = {"status": "API_OK", "total_count": 0,
                   "latest_event": None, "latest_path": None}
        call = Mock(return_value=subprocess.CompletedProcess(
            [], 0, stdout=json.dumps(payload), stderr="secret"))
        with patch.object(monitor, "_rootless_context", return_value=self.rootless_context(call)):
            result = monitor.schedule_api_host()
        self.assertEqual(result["total_count"], 0)
        self.assertTrue(result["read_only"])
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(call.call_args.kwargs["timeout"], 7)
        self.assertIn("event=schedule", call.call_args.args[-1])

    def test_manual_lifecycle_events_report_only_action_clock_and_exit(self):
        rows = [
            {"Action": "kill", "time": 1791990000,
             "Actor": {"Attributes": {"signal": "15", "env": "secret"}}},
            {"Action": "die", "time": 1791990001,
             "Actor": {"Attributes": {"exitCode": "1", "token": "secret"}}},
            {"Action": "start", "time": 1791990002},
        ]
        output = "\n".join(map(json.dumps, rows))
        events = Mock(return_value=subprocess.CompletedProcess([], 0, stdout=output, stderr="secret"))
        with patch.object(monitor, "_rootless_context", return_value=self.rootless_context(events)), patch.object(
                monitor.subprocess, "run", return_value=subprocess.CompletedProcess([], 0,
                    stdout="Traceback (most recent call last):\n", stderr="ValueError: secret")) as call:
            result = monitor.observer_lifecycle_host()
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["events"], [
            {"action": "kill", "epoch_seconds": 1791990000, "signal": 15},
            {"action": "die", "epoch_seconds": 1791990001, "exit_code": 1},
            {"action": "start", "epoch_seconds": 1791990002}])
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(result["proximate_log"]["exception_classes"], ["ValueError"])
        self.assertEqual(result["proximate_log"]["traceback_count"], 1)
        self.assertEqual(events.call_args.kwargs["timeout"], 6)
        self.assertEqual(events.call_args.args[:2], ("events", "--since"))
        self.assertIn("container=" + "1" * 64, events.call_args.args)
        self.assertEqual(call.call_args.kwargs["timeout"], 5)
        self.assertEqual(call.call_args.args[0][:4], ["/usr/bin/docker", "--host",
            "unix:///run/user/1000/porota-rc6-docker.sock", "logs"])
        self.assertEqual(call.call_args.args[0][-1], "1" * 64)

    def test_sqlite_exit_reason_is_categorical_and_redacted(self):
        event = json.dumps({"Action": "die", "time": 1791998273,
                            "Actor": {"Attributes": {"exitCode": "1"}}})
        secret = ('Traceback (most recent call last):\n'
                  '  File "/app/bf_production_paper_observer.py", line 1859, in run\n'
                  'sqlite3.OperationalError: database is locked; credential=secret')
        events = Mock(return_value=subprocess.CompletedProcess([], 0, stdout=event, stderr=""))
        with patch.object(monitor, "_rootless_context", return_value=self.rootless_context(events)), patch.object(
                monitor.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=secret)):
            result = monitor.observer_lifecycle_host()
        self.assertEqual(result["proximate_log"]["sqlite_hints"],
                         ["SQLITE_BUSY_LOCKED"])
        self.assertEqual(result["proximate_log"]["tracebacks"], [{
            "exception_class": "sqlite3.OperationalError",
            "last_app_frame": {"module": "bf_production_paper_observer.py",
                               "line": 1859, "function": "run"}}])
        self.assertNotIn("secret", json.dumps(result))

    def test_recent_critical_signatures_are_bounded_and_redacted(self):
        log = ('Traceback (most recent call last):\n'
               '  File "/app/rc6_shadow_runtime/worker.py", line 91, in run\n'
               'sqlite3.OperationalError: database is locked; token=secret\n'
               'REAL_ORDER_CAPABILITY=ENABLED token=secret\n')
        responses = [subprocess.CompletedProcess([], 0, stdout="", stderr=log),
                     subprocess.CompletedProcess([], 0, stdout="healthy", stderr="")]
        with patch.object(monitor, "_rootless_context", return_value=self.rootless_context()), patch.object(
                monitor.subprocess, "run", side_effect=responses) as call:
            result = monitor.recent_critical_host()
        observer = result["containers"]["observer"]
        self.assertEqual(observer["traceback_count"], 1)
        self.assertEqual(observer["tracebacks"], [{
            "exception_class": "sqlite3.OperationalError",
            "last_app_frame": {"module": "worker.py", "line": 91, "function": "run"},
            "sqlite_hint": "SQLITE_BUSY_LOCKED", "observed_utc": None}])
        self.assertEqual(observer["capability_signature_count"], 1)
        self.assertEqual(result["containers"]["dashboard"]["traceback_count"], 0)
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(call.call_count, 2)
        for entry in call.call_args_list:
            self.assertEqual(entry.kwargs["timeout"], 6)
            self.assertEqual(entry.args[0][:7],
                             ["/usr/bin/docker", "--host", "unix:///run/user/1000/porota-rc6-docker.sock",
                              "logs", "--timestamps", "--since", "30m"])

    def test_recent_critical_timing_identifies_current_errors(self):
        log = ('2026-10-14T18:20:02.123456789Z Traceback (most recent call last):\n'
               '2026-10-14T18:20:02.123456789Z sqlite3.OperationalError: database is locked\n'
               '2026-10-14T18:10:02.123456789Z ValueError: secret\n')
        with patch.object(monitor, "now_utc", return_value=datetime(
                2026, 10, 14, 18, 22, tzinfo=timezone.utc)), patch.object(
                monitor, "_rootless_context", return_value=self.rootless_context()), patch.object(
                monitor.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0, stdout="", stderr=log),
                    subprocess.CompletedProcess([], 0, stdout="", stderr="")]):
            result = monitor.recent_critical_host()
        current = result["containers"]["observer"]
        self.assertEqual(current["exception_lines_last_5m"], 1)
        self.assertEqual(current["tracebacks"][0]["observed_utc"],
                         "2026-10-14T18:20:02Z")
        self.assertNotIn("secret", json.dumps(result))

    def test_recent_critical_unavailable_does_not_export_stderr(self):
        with patch.object(monitor, "_rootless_context", return_value=self.rootless_context()), patch.object(
                monitor.subprocess, "run", side_effect=[
                subprocess.TimeoutExpired(["docker"], 6, stderr="secret"),
                subprocess.CompletedProcess([], 1, stdout="", stderr="secret")]):
            result = monitor.recent_critical_host()
        self.assertEqual([item["status"] for item in result["containers"].values()],
                         ["UNAVAILABLE", "UNAVAILABLE"])
        self.assertNotIn("secret", json.dumps(result))



class A363ScalpingSanitizedErrors(unittest.TestCase):
    """No trading DB, network, secrets or real market input in these tests."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE paper_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_at TEXT, event_type TEXT,
            detail TEXT)""")

    def tearDown(self):
        self.db.close()

    def add_event(self, kind, detail, at="2026-11-02T15:30:00+00:00"):
        self.db.execute(
            "INSERT INTO paper_events(event_at,event_type,detail) VALUES (?,?,?)",
            (at, kind, detail))

    def test_extract_code_and_utc_without_exporting_identity(self):
        self.add_event("INTRADAY_SCALPING_ERROR",
                       'GGAL: PPI_HTTP_403;shadow_identity=["GGAL","ACCIONES","BYMA","ARS","A-24HS"]')
        result = monitor._scalping_recent_error_codes(self.db, {"paper_events"})
        self.assertEqual("PPI_HTTP_403", result["events"][0]["code"])
        self.assertEqual("2026-11-02T15:30:00+00:00", result["events"][0]["event_at_utc"])
        self.assertEqual("EVIDENCE_ONLY_NO_GO", result["status"])
        self.assertNotIn("GGAL", json.dumps(result))
        self.assertNotIn("ARS", json.dumps(result))

    def test_malformed_or_confidential_detail_never_leaves(self):
        self.add_event("INTRADAY_SCALPING_ERROR",
                       'TICKER: token=secret-provider-response;shadow_identity=["SECRET"]')
        r = monitor._scalping_recent_error_codes(self.db, {"paper_events"})
        self.assertEqual("NO_VERIFICADO", r["events"][0]["code"])
        self.assertNotIn("secret", json.dumps(r).lower())
        self.assertNotIn("TICKER", json.dumps(r))

    def test_a366_phase_forward_compatible_with_a365(self):
        self.add_event("INTRADAY_SCALPING_ERROR",
            'GGAL: PPI_VALUEERROR;phase=NORMALIZE;shadow_identity=["GGAL","ACCIONES","BYMA","ARS","A-24HS"]')
        r = monitor._scalping_recent_error_codes(self.db, {"paper_events"})
        self.assertEqual("PPI_VALUEERROR", r["events"][0]["code"])
        self.assertEqual("NORMALIZE", r["events"][0]["phase"])
        self.assertNotIn("GGAL", json.dumps(r))
        self.assertNotIn("ARS", json.dumps(r))

    def test_a366_legacy_error_retains_code_with_unknown_phase(self):
        self.add_event("INTRADAY_SCALPING_ERROR",
            'OLD_SYM: PPI_VALUEERROR;shadow_identity=["OLD_SYM","ACCIONES","BYMA","ARS","A-24HS"]')
        r = monitor._scalping_recent_error_codes(self.db, {"paper_events"})
        self.assertEqual("PPI_VALUEERROR", r["events"][0]["code"])
        self.assertEqual("NO_VERIFICADO", r["events"][0]["phase"])
        self.assertNotIn("OLD_SYM", json.dumps(r))

    def test_a366_malformed_phase_never_exports_secret(self):
        self.add_event("INTRADAY_SCALPING_ERROR",
            'GGAL: PPI_VALUEERROR;phase=AUTHORIZATION_SECRET;shadow_identity=["GGAL"]')
        r = monitor._scalping_recent_error_codes(self.db, {"paper_events"})
        self.assertEqual("NO_VERIFICADO", r["events"][0]["phase"])
        self.assertNotIn("AUTHORIZATION_SECRET", json.dumps(r))

    def test_clock_rollback_code_not_ticker(self):
        self.add_event("INTRADAY_SCALPING_CLOCK_ROLLBACK", "CUSTOMER_ACCOUNT_NUMBER")
        r = monitor._scalping_recent_error_codes(self.db, {"paper_events"})
        self.assertEqual("INTRADAY_SCALPING_CLOCK_ROLLBACK", r["events"][0]["code"])
        self.assertNotIn("CUSTOMER", json.dumps(r))

    def test_nonmatching_event_not_counted_as_no_failures(self):
        self.add_event("SOME_OTHER_EVENT", "PPI_HTTP_403")
        r = monitor._scalping_recent_error_codes(self.db, {"paper_events"})
        self.assertEqual("NO_VERIFICADO", r["status"])
        self.assertEqual([], r["events"])

    def test_tail_and_output_budget(self):
        for i in range(5005):
            self.add_event("OTHER", "token=SECRET")
        self.add_event("INTRADAY_SCALPING_ERROR",
                       'GGAL: PPI_TIMEOUTERROR;shadow_identity=["GGAL"]')
        r = monitor._scalping_recent_error_codes(self.db, {"paper_events"})
        self.assertEqual(1, len(r["events"]))
        self.assertEqual("PPI_TIMEOUTERROR", r["events"][0]["code"])

    def test_missing_table_or_query_failure_fail_closed(self):
        self.assertEqual("PAPER_EVENTS_TABLE_ABSENT",
                         monitor._scalping_recent_error_codes(self.db, set())["reason"])
        self.db.execute("DROP TABLE paper_events")
        self.assertEqual("ERROR_EVENT_READ_UNAVAILABLE",
                         monitor._scalping_recent_error_codes(self.db, {"paper_events"})["reason"])



class A367CalendarSubprocessRegression(unittest.TestCase):
    """Execute the real embedded Python -c probe against synthetic modules.

    Before A367 its top-level unexpected indentation caused IndentationError
    and all real host market sessions were classified UNKNOWN.
    """

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="a367-calendar-")
        self.addCleanup(self.directory.cleanup)
        self.fixture_root = Path(self.directory.name)
        (self.fixture_root / "ak_byma_calendar.py").write_text(
            'from datetime import date\n'
            'CALENDARIO_ACTUALIZADO_EL = date(2026,10,14)\n'
            'ANIOS_AUDITADOS = frozenset({2026})\n'
            'FUENTE_OFICIAL = "SYNTHETIC_CALENDAR_ONLY"\n'
            'def es_dia_habil_operativo(day):\n'
            '    return day.weekday() < 5 and day != date(2026,11,6)\n',
            encoding="utf-8")
        (self.fixture_root / "co_market_sessions_hf6.py").write_text(
            'from datetime import time\n'
            'from zoneinfo import ZoneInfo\n'
            'TZ = ZoneInfo("America/Argentina/Buenos_Aires")\n'
            'BYMA_PAPER_SPOT_OPEN = time(10,30)\n'
            'BYMA_PAPER_SPOT_CLOSE = time(17,0)\n',
            encoding="utf-8")

    def session_at(self, instant):
        return monitor.independent_session(
            datetime.fromisoformat(instant), root=self.fixture_root)

    def test_monday_20261102_open_is_not_indentation_unknown(self):
        result = self.session_at("2026-11-02T14:05:00-03:00")
        self.assertEqual("OPEN", result["state"])
        self.assertIs(result["operational_day"], True)
        self.assertEqual("BYMA_PAPER_SPOT", result["scope"])
        self.assertEqual("2026-10-14", result["audited_at"])

    def test_operational_day_before_and_after_spot_gate_is_closed(self):
        for instant in ("2026-11-02T10:20:00-03:00",
                        "2026-11-02T17:30:00-03:00"):
            self.assertEqual("CLOSED", self.session_at(instant)["state"])

    def test_weekend_and_holiday_fixture_are_closed(self):
        self.assertEqual("CLOSED", self.session_at(
            "2026-11-01T14:05:00-03:00")["state"])
        self.assertEqual("CLOSED", self.session_at(
            "2026-11-06T14:05:00-03:00")["state"])

    def test_unaudited_year_remains_unknown_and_fail_closed(self):
        result = self.session_at("2027-01-04T14:05:00-03:00")
        self.assertEqual("UNKNOWN", result["state"])
        self.assertIsNone(result["operational_day"])



class PerformanceEvidencePeriodContracts(unittest.TestCase):
    """Only indexed, bounded reads from the dedicated performance evidence DB."""

    CURRENT_SOURCE_SHA = "21b6d6b3882a35f1f5ad0469df739c5b0d6d2f76"
    CANDIDATE_SOURCE_SHA = "494116c1200b1c2914369d1e0ffd186c1906d781"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / monitor.DB_REL
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.performance = Path(str(self.path) + ".performance.sqlite")

    def events(self, *rows):
        with sqlite3.connect(self.performance) as db:
            db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, recorded_at TEXT NOT NULL, payload_json TEXT NOT NULL)")
            db.executemany("INSERT INTO events VALUES(?,?,?)",
                           [(i, at, "PRIVATE_ACCOUNT_VALUE") for i, at in rows])

    def test_empty_files_are_explicit_not_evidence_of_fresh_reporting(self):
        stat_result = monitor.performance_evidence_capacity_stat(
            self.root, runtime_source_sha=self.CURRENT_SOURCE_SHA)
        self.assertEqual(stat_result["state"], "NO_EVIDENCE_FILES")
        self.assertEqual(stat_result["default_capacity_bytes"], 128 * 1024**2)
        self.assertEqual(stat_result["capacity_limit_source"], "EXACT_RUNTIME_SOURCE_SHA")
        self.assertEqual(stat_result["recorded_period"]["status"], "NO_EVIDENCE_FILES")
        self.assertFalse(self.performance.exists())

    def test_first_and_last_recorded_at_by_index_without_leaking_payload(self):
        self.events((7, "2026-10-01T12:00:00Z"), (19, "2026-11-16T17:00:00+00:00"),
                    (11, "2026-10-20T14:00:00Z"))
        before = self.performance.stat().st_size
        result = monitor.performance_evidence_capacity_stat(
            self.root, runtime_source_sha=self.CURRENT_SOURCE_SHA)
        period = result["recorded_period"]
        self.assertEqual(period["status"], "OBSERVED")
        self.assertEqual(period["first_recorded_at"], "2026-10-01T12:00:00+00:00")
        self.assertEqual(period["last_recorded_at"], "2026-11-16T17:00:00+00:00")
        self.assertEqual(period["ordering"], "EVENT_ID_PRIMARY_KEY")
        self.assertEqual(result["default_capacity_bytes"], 128 * 1024**2)
        self.assertEqual(self.performance.stat().st_size, before)
        self.assertFalse(Path(str(self.performance)+"-wal").exists())
        self.assertFalse(Path(str(self.performance)+"-shm").exists())
        self.assertNotIn("PRIVATE_ACCOUNT_VALUE", json.dumps(result))

    def test_planned_256_mib_is_only_source_bound_and_never_applied_to_current(self):
        self.events((1, "2026-10-01T12:00:00Z"))
        current = monitor.performance_evidence_capacity_stat(
            self.root, runtime_source_sha=self.CURRENT_SOURCE_SHA)
        future = monitor.performance_evidence_capacity_stat(
            self.root, runtime_source_sha=self.CANDIDATE_SOURCE_SHA)
        unknown = monitor.performance_evidence_capacity_stat(
            self.root, runtime_source_sha="e"*40)
        self.assertEqual(current["default_capacity_bytes"], 128 * 1024**2)
        self.assertEqual(future["default_capacity_bytes"], 256 * 1024**2)
        self.assertEqual(future["capacity_limit_source"], "EXACT_RUNTIME_SOURCE_SHA")
        self.assertIsNone(unknown["default_capacity_bytes"])
        self.assertEqual(unknown["state"], "NO_VERIFICADO_CAPACITY_LIMIT")
        self.assertEqual(unknown["recorded_period"]["status"], "OBSERVED")

    def test_invalid_timestamp_or_unavailable_schema_is_no_verificado(self):
        self.events((1, "PRIVATE_INVALID_TIMESTAMP"), (2, "2026-11-16T17:00:00Z"))
        invalid = monitor.performance_evidence_capacity_stat(
            self.root, runtime_source_sha=self.CURRENT_SOURCE_SHA)
        self.assertEqual(invalid["recorded_period"]["status"], "NO_VERIFICADO")
        self.assertNotIn("PRIVATE_INVALID_TIMESTAMP", json.dumps(invalid))
        with sqlite3.connect(self.performance) as db:
            db.execute("DROP TABLE events")
        missing = monitor.performance_evidence_capacity_stat(
            self.root, runtime_source_sha=self.CURRENT_SOURCE_SHA)
        self.assertEqual(missing["recorded_period"]["status"], "NO_VERIFICADO")
        self.assertEqual(missing["recorded_period"]["reason"], "SQLITE_READ_OR_SCHEMA_UNAVAILABLE")

    def test_live_wal_sidecars_are_never_opened_or_rewritten(self):
        self.events((1, "2026-11-15T17:00:00Z"))
        writer = sqlite3.connect(self.performance)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("INSERT INTO events VALUES(?,?,?)",
                           (2, "2026-11-16T17:00:00Z", "PRIVATE_WAL_PAYLOAD"))
            writer.commit()
            sidecars = [Path(str(self.performance)+"-wal"), Path(str(self.performance)+"-shm")]
            self.assertTrue(all(p.is_file() and p.stat().st_size > 0 for p in sidecars))
            before = [(p.stat().st_mtime_ns, p.read_bytes()) for p in (self.performance, *sidecars)]
            outcome = monitor.performance_evidence_capacity_stat(
                self.root, runtime_source_sha=self.CURRENT_SOURCE_SHA)
            after = [(p.stat().st_mtime_ns, p.read_bytes()) for p in (self.performance, *sidecars)]
            self.assertEqual(before, after)
            self.assertEqual(outcome["recorded_period"]["status"], "NO_VERIFICADO")
            self.assertEqual(outcome["recorded_period"]["reason"], "WAL_ACTIVE_OR_PRESENT")
            self.assertNotIn("PRIVATE_WAL_PAYLOAD", json.dumps(outcome))
        finally:
            writer.close()

    def test_unavailable_database_read_does_not_emit_exception_body(self):
        self.events((1, "2026-11-16T17:00:00Z"))
        with patch.object(monitor.sqlite3, "connect",
                          side_effect=sqlite3.OperationalError("PRIVATE_CONNECTION_ERROR")):
            outcome = monitor.performance_evidence_capacity_stat(
                self.root, runtime_source_sha=self.CURRENT_SOURCE_SHA)
        self.assertEqual(outcome["recorded_period"]["status"], "NO_VERIFICADO")
        self.assertNotIn("PRIVATE_CONNECTION_ERROR", json.dumps(outcome))


class A372PerformanceWorkerLogClassification(unittest.TestCase):
    """Only synthetic logs; non-sensitive labels and stable temporal budget."""

    def test_status_code_capacity_reached_is_preserved(self):
        line = "2026-10-14T13:59:00Z WARNING PERFORMANCE_CAPTURE:EVIDENCE_CAPACITY_REACHED user=secret"
        result = monitor._performance_log_signatures(line, at=NOW)
        self.assertEqual("DIAGNOSTIC_OBSERVED_NO_GO", result["status"])
        self.assertEqual("CAPTURE_EVIDENCE_CAPACITY_REACHED", result["observations"][0]["code"])
        self.assertNotIn("secret", json.dumps(result))

    def test_generic_exception_redacted_without_inventing_cause(self):
        line = "2026-10-14T13:59:00Z WARNING PERFORMANCE_CAPTURE_UNVERIFIED:OperationalError ticker=GGAL token=secret"
        result = monitor._performance_log_signatures(line, at=NOW)
        self.assertEqual("ERROR_OperationalError", result["observations"][0]["code"])
        self.assertNotIn("GGAL", json.dumps(result))
        self.assertNotIn("secret", json.dumps(result))

    def test_unknown_error_class_is_not_echoed(self):
        line = "2026-10-14T13:59:00Z WARNING PERFORMANCE_CAPTURE_UNVERIFIED:CustomerTokenXYZ"
        result = monitor._performance_log_signatures(line, at=NOW)
        self.assertEqual("ERROR_UNCLASSIFIED_CLASS", result["observations"][0]["code"])
        self.assertNotIn("CustomerTokenXYZ", json.dumps(result))

    def test_empty_or_unrelated_logs_are_not_green(self):
        result = monitor._performance_log_signatures("2026-10-14T13:59:00Z unrelated", at=NOW)
        self.assertEqual("NO_VERIFICADO", result["status"])
        self.assertEqual([], result["observations"])

    def test_outside_window_does_not_count(self):
        lines = ("2026-10-14T13:00:00Z WARNING PERFORMANCE_CAPTURE:EVIDENCE_CAPACITY_REACHED\n"
                 "2026-10-14T14:01:00Z WARNING PERFORMANCE_CAPTURE_UNVERIFIED:ValueError")
        result = monitor._performance_log_signatures(lines, at=NOW)
        self.assertEqual("NO_VERIFICADO", result["status"])

    def test_response_budget_and_max_events(self):
        line = "2026-10-14T13:59:00Z WARNING PERFORMANCE_CAPTURE:EVIDENCE_CAPACITY_REACHED"
        many = "\n".join([line] * 24)
        result = monitor._performance_log_signatures(many, at=NOW)
        self.assertTrue(result["possibly_truncated"])
        self.assertEqual(12, len(result["observations"]))
        blocked = monitor._performance_log_signatures("x" * 262145, at=NOW)
        self.assertEqual("LOG_BUDGET_EXCEEDED", blocked["reason"])



if __name__ == "__main__":
    unittest.main()

