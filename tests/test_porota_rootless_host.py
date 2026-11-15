import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import types
import tarfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

try:
    from scripts import porota_rootless_host as host
except ImportError:
    host = None

SHA = "a" * 40
TREE = "b" * 40
IMAGE = "sha256:" + "c" * 64


def container_fixture(root, operator, role, *, attempt="next", image=IMAGE, cid=None):
    mounts = [{"Type": "bind", "Destination": "/app/data", "Source": str(root / "data"), "RW": True},
              {"Type": "bind", "Destination": "/app/operator", "Source": str(operator), "RW": False}]
    if role == "observer":
        mounts.append({"Type": "bind", "Destination": "/run/secrets/ppi_production.json",
                       "Source": str(root / ".secrets/ppi_production.json"), "RW": False})
    return {"Id": cid or ("1" if role == "observer" else "2") * 64,
            "Name": f"/porota-rc6-{role}-{attempt}", "Image": image,
            "Config": {"Image": image, "User": "0:0", "WorkingDir": "/app", "Entrypoint": None,
                "Cmd": ["python", "o_dashboard.py" if role == "dashboard" else "bf_production_paper_observer.py"],
                "Labels": {host.PROJECT: "rc6", "io.porota.role": role, "io.porota.attempt": attempt},
                "Env": [f"POROTA_BUILD_SHA={SHA}", f"POROTA_CANDIDATE_TREE_SHA={TREE}",
                    "DASHBOARD_OPERATION_MODE=PRODUCTION_PAPER", "EXECUTION_MODE=SIMULATION",
                    "REAL_ORDER_CAPABILITY=BLOCKED", "PAPER_V17_DB_PATH=/app/data/paper_v17/observer_v17.db"]},
            "Mounts": mounts, "State": {"Running": True, "OOMKilled": False, "Health": {"Status": "healthy"}},
            "HostConfig": {"ReadonlyRootfs": True, "Privileged": False, "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"], "NetworkMode": "private", "PidMode": "",
                "Memory": 512 * 1024**2, "NanoCpus": 500_000_000, "PidsLimit": 128,
                "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=128m", "/home/botuser/.cache": "rw,noexec,nosuid,size=64m",
                          "/home/botuser/.config": "rw,noexec,nosuid,size=16m"},
                "PortBindings": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}]} if role == "dashboard" else {}}}


class RootlessContracts(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(host, "rootless runtime must enforce its host boundary")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_rootful_daemon_is_refused_even_at_expected_socket(self):
        identity = host.Identity(1000, 1000, "porotaadmin", self.base)
        info = {"SecurityOptions": ["name=seccomp"], "CgroupVersion": "2",
                "CgroupDriver": "systemd", "DockerRootDir": str(identity.store), "ID": "daemon"}
        with self.assertRaisesRegex(host.Hold, "ROOTLESS_DAEMON_REQUIRED"):
            host.validate_daemon(info, identity)

    def test_wrong_store_or_cgroup_is_rejected(self):
        identity = host.Identity(1000, 1000, "porotaadmin", self.base)
        info = {"SecurityOptions": ["name=rootless"], "CgroupVersion": "2",
                "CgroupDriver": "systemd", "DockerRootDir": str(identity.store), "ID": "daemon"}
        self.assertEqual(host.validate_daemon(info, identity), "daemon")
        for key, value in (("DockerRootDir", "/var/lib/docker"), ("CgroupVersion", "1"),
                           ("CgroupDriver", "cgroupfs")):
            wrong = dict(info, **{key: value})
            with self.subTest(key=key), self.assertRaises(host.Hold):
                host.validate_daemon(wrong, identity)

    def test_effective_mapping_must_map_only_namespace_root_to_ssh_user(self):
        host.validate_zero_mapping("0 1000 1\n1 231072 65536\n", 1000)
        for value in ("0 0 4294967295\n", "0 1001 1\n1 231072 65536\n",
                      "0 1000 2\n1 231072 65536\n", "0 1000 1\n0 231072 65536\n",
                      "0 1000 1\n1 1000 65536\n", "0 1000 1\n1 10000 65536\n", "garbage"):
            with self.subTest(value=value), self.assertRaises(host.Hold):
                host.validate_zero_mapping(value, 1000)
        with self.assertRaisesRegex(host.Hold, "UID_MAPPING_HOST_IDENTITY_COLLISION"):
            host.validate_zero_mapping("0 1000 1\n1 231072 65536\n", 1000, forbidden_ids=[231090])

    def test_limits_must_be_effectively_enforced_inside_namespace(self):
        valid = {"cpu": "25000 100000", "memory": str(128 * 1024**2), "pids": "16"}
        host.validate_effective_limits(valid, memory=128 * 1024**2, pids=16, nanocpus=250_000_000)
        for key, value in (("cpu", "max 100000"), ("cpu", "100000 100000"), ("memory", "max"), ("pids", "512")):
            with self.subTest(key=key), self.assertRaisesRegex(host.Hold, "EFFECTIVE_CGROUP_LIMITS_REQUIRED"):
                host.validate_effective_limits(dict(valid, **{key: value}), memory=128 * 1024**2,
                                               pids=16, nanocpus=250_000_000)

    def test_runtime_config_reuses_frozen_paper_and_strips_dashboard_provider_secrets(self):
        observer, dashboard = host.runtime_environments(
            {"TELEGRAM_BOT_TOKEN": "private", "PAPER_MAX_OPEN_POSITIONS": "999"},
            {"DASHBOARD_ACCESS_TOKEN": "auth", "PPI_TOKEN": "must-not-copy", "PYTHONPATH": "/evil"},
            SHA, TREE)
        self.assertEqual(observer["PAPER_MAX_OPEN_POSITIONS"], "5")
        self.assertEqual(observer["PAPER_V17_DB_PATH"], "/app/data/paper_v17/observer_v17.db")
        self.assertEqual(observer["REAL_ORDER_CAPABILITY"], "BLOCKED")
        self.assertEqual(dashboard["DASHBOARD_OPERATION_MODE"], "PRODUCTION_PAPER")
        self.assertEqual(dashboard["DASHBOARD_ACCESS_TOKEN"], "auth")
        self.assertNotIn("PPI_TOKEN", dashboard)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", dashboard)
        self.assertNotIn("PYTHONPATH", dashboard)

    def test_real_configuration_is_refused_before_any_command(self):
        for values in ({"ALLOW_REAL_ORDERS": "true"}, {"REAL_ORDER_CAPABILITY": "ENABLED"},
                       {"EXECUTION_MODE": "REAL"}):
            with self.subTest(values=values), self.assertRaisesRegex(host.Hold, "REAL_ORDERS_CONFIG"):
                host.runtime_environments(values, {"DASHBOARD_ACCESS_TOKEN": "x"}, SHA, TREE)

    def test_environment_duplicates_and_control_characters_are_not_silently_overridden(self):
        for value in ("A=x\nA=y", "A=x\x00", "export A=foo", "bad line"):
            with self.subTest(value=value), self.assertRaises(host.Hold):
                host.parse_env(value)
        self.assertEqual(host.parse_env("# hi\nA='literal$()'\n"), {"A": "literal$()"})

    def test_missing_database_is_never_initialized(self):
        path = self.base / "absent.db"
        with self.assertRaisesRegex(host.Hold, "DATABASE_REQUIRED"):
            host.database_snapshot(path)
        self.assertFalse(path.exists())

    def test_database_snapshot_is_read_only_and_rejects_nonzero_real_orders(self):
        path = self.base / "observer_v17.db"
        with sqlite3.connect(path) as connection:
            connection.executescript("CREATE TABLE observer_state(id,mode,real_orders_sent);"
                                     "INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);"
                                     "CREATE TABLE paper_workspace(id,dataset_id);"
                                     "INSERT INTO paper_workspace VALUES(1,'dataset-a');")
        before = path.read_bytes()
        snapshot = host.database_snapshot(path)
        self.assertEqual(snapshot["real_orders_sent"], 0)
        self.assertEqual(snapshot["inode"], path.stat().st_ino)
        self.assertEqual(path.read_bytes(), before)
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE observer_state SET real_orders_sent=1")
        with self.assertRaisesRegex(host.Hold, "PAPER_DATABASE_SAFETY"):
            host.database_snapshot(path)

    def test_database_symlinks_and_hardlinks_are_refused(self):
        path = self.base / "data.db"
        path.write_bytes(b"data")
        alias = self.base / "alias.db"
        alias.symlink_to(path)
        with self.assertRaises(host.Hold):
            host.database_snapshot(alias)
        alias.unlink()
        os.link(path, alias)
        with self.assertRaises(host.Hold):
            host.database_snapshot(path)

    def test_user_lock_excludes_parallel_writer_and_rejects_alias(self):
        path = self.base / "deploy.lock"
        with host.user_lock(path):
            with self.assertRaisesRegex(host.Hold, "OWNER_BUSY"):
                with host.user_lock(path):
                    self.fail("second writer entered")
        alias = self.base / "alias.lock"
        alias.symlink_to(path)
        with self.assertRaises(host.Hold):
            with host.user_lock(alias):
                self.fail("symlink lock entered")

    def test_docker_calls_always_pin_private_socket_without_ambient_context(self):
        calls = []
        def command(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, "{}", "")
        identity = host.Identity(1000, 1000, "porotaadmin", self.base)
        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://foreign", "DOCKER_CONTEXT": "evil"}):
            host.Docker(identity, run=command).call("info", "--format", "{{json .}}")
        args, kwargs = calls[0]
        self.assertEqual(args[:3], ("/usr/bin/docker", "--host", "unix:///run/user/1000/porota-rc6-docker.sock"))
        self.assertNotIn("DOCKER_CONTEXT", kwargs["env"])
        self.assertNotIn("tcp://foreign", str(kwargs))
        self.assertNotIn("sudo", args)

    def test_compose_uses_source_sibling_of_strict_release_directory(self):
        stage = self.base / "stage"
        release_dir = stage / "release"
        source = stage / "source"
        release_dir.mkdir(parents=True)
        source.mkdir()
        compose = source / "compose.rootless.yml"
        compose.write_text("services: {}\n")
        identity = host.Identity(os.getuid(), os.getgid(), "test", self.base)
        manager = host.Host(self.base, identity)
        manager.receipt["attempt"] = "23-1"
        with patch.object(manager.docker, "call", return_value="") as command:
            manager.compose(release_dir, self.base, "create", "--no-build", "--pull", "never")
        self.assertIn(compose, command.call_args.args)
        self.assertNotIn(release_dir / "source/compose.rootless.yml", command.call_args.args)
        self.assertIsNone(host.ATTEMPT.fullmatch("ABC"))

    def test_disk_requirement_preserves_executable_policy_formula(self):
        release = {"image_size_bytes": 200,
                   "members": {"porota-predeploy-image.tar.gz": {"bytes": 100},
                               "porota-deploy-bundle-v2.tgz": {"bytes": 30}}}
        self.assertEqual(host.required_free_bytes(release), 2147483648 + 100 + 400 + 60)

    def test_cleanup_refuses_candidate_referenced_by_any_container(self):
        class Commands:
            def __init__(self):
                self.calls = []
            def call(self, *args, **kwargs):
                self.calls.append(args)
                if args[:2] == ("container", "ls"):
                    return "f" * 64 + "\n"
                if args[:2] == ("container", "inspect"):
                    return json.dumps([{"Image": IMAGE}])
                raise AssertionError(args)
        docker = Commands()
        self.assertEqual(host.remove_unreferenced_image(docker, IMAGE), "RETAINED_REFERENCED")
        self.assertFalse(any(c[:2] == ("image", "rm") for c in docker.calls))

    def test_ambiguous_inspect_blocks_image_cleanup(self):
        class Commands:
            def call(self, *args, **kwargs):
                if args[:2] == ("container", "ls"):
                    return "f" * 64
                return "not-json"
        with self.assertRaises(host.Hold):
            host.remove_unreferenced_image(Commands(), IMAGE)

    def test_runtime_readiness_requires_new_fresh_heartbeat_and_known_paper_state(self):
        self.assertTrue(hasattr(host, "classify_runtime"), "runtime postflight must exist")
        now = datetime.now(timezone.utc)
        row = {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0, "ppi_auth": "OK",
               "process_state": "WAITING_MARKET", "session_state": "CLOSED",
               "heartbeat_at": now.isoformat(), "last_market_data_at": None, "detail": ""}
        self.assertEqual(host.classify_runtime(row, now=now, started=now - timedelta(seconds=1))["status"], "READY")
        self.assertEqual(host.classify_runtime(row, now=now, started=now + timedelta(seconds=1))["status"], "PENDING")
        row["process_state"] = "STARTING"
        self.assertEqual(host.classify_runtime(row, now=now, started=now)["status"], "PENDING")
        row["heartbeat_at"] = (now - timedelta(seconds=121)).isoformat()
        self.assertEqual(host.classify_runtime(row, now=now, started=now)["status"], "PENDING")
        for counter in (None, 1, "0"):
            row["real_orders_sent"] = counter
            with self.subTest(counter=counter), self.assertRaisesRegex(host.Hold, "PAPER_RUNTIME_SAFETY"):
                host.classify_runtime(row, now=now, started=now)

    def test_known_provider_outage_is_amber_but_unknown_degradation_is_rejected(self):
        self.assertTrue(hasattr(host, "classify_runtime"), "runtime postflight must exist")
        now = datetime.now(timezone.utc)
        row = {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0, "ppi_auth": "ERROR",
               "process_state": "DEGRADED", "session_state": "BOOT_PPI_LOGIN",
               "heartbeat_at": now.isoformat(), "detail": "Login local bloqueado por interlock persistente."}
        result = host.classify_runtime(row, now=now, started=now)
        self.assertEqual(result, {"status": "READY", "source_alert": "AMBER_PPI_LOGIN_UNAVAILABLE"})
        row["detail"] = "CAUCION_LEDGER_INVALID"
        with self.assertRaisesRegex(host.Hold, "RUNTIME_UNCLASSIFIED_DEGRADED"):
            host.classify_runtime(row, now=now, started=now)

    def test_foreign_container_cannot_be_adopted_by_name_alone(self):
        self.assertTrue(hasattr(host, "validate_container"), "birth guard must exist")
        with self.assertRaisesRegex(host.Hold, "CONTAINER_SCOPE"):
            host.validate_container({"Id": "d" * 64, "Name": "/porota-rc6-observer-x",
                                     "Image": IMAGE, "Config": {"Labels": {}}},
                                    role="observer", image_id=IMAGE, source_sha=SHA,
                                    source_tree=TREE, attempt="x", root=self.base)

    def test_container_boundary_checks_command_mounts_image_and_hardening(self):
        valid = container_fixture(self.base, self.base / "operator", "dashboard")
        def check(value):
            return host.validate_container(value, role="dashboard", image_id=IMAGE, source_sha=SHA,
                source_tree=TREE, attempt="next", root=self.base, operator=self.base / "operator")
        self.assertEqual(check(valid), "2" * 64)
        edits = (("Image", "sha256:" + "d" * 64), ("Config.User", "1000:1000"),
                 ("Config.Cmd", ["python", "other.py"]), ("HostConfig.Privileged", True),
                 ("HostConfig.ReadonlyRootfs", False), ("HostConfig.NetworkMode", "host"),
                 ("HostConfig.PidMode", "host"), ("HostConfig.CapDrop", []),
                 ("HostConfig.SecurityOpt", []), ("HostConfig.Memory", 0),
                 ("HostConfig.PortBindings", {"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8000"}]}),
                 ("State.OOMKilled", True), ("Mounts", []))
        for dotted, value in edits:
            bad = copy.deepcopy(valid)
            target = bad
            parts = dotted.split(".")
            for part in parts[:-1]:
                target = target[part]
            target[parts[-1]] = value
            with self.subTest(field=dotted), self.assertRaises(host.Hold):
                check(bad)

    def test_postflight_starting_is_bounded_and_timeout_never_becomes_validated(self):
        identity = host.Identity(os.getuid(), os.getgid(), "test", self.base)
        manager = host.Host(self.base, identity)
        started = datetime.now(timezone.utc)
        row = {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0, "ppi_auth": "OK",
               "process_state": "STARTING", "heartbeat_at": started.isoformat()}
        times = iter((0, 0, 5, 10))
        document = {"roles": {"observer": "1" * 64}}
        with patch.object(manager, "check_role", return_value={"State": {"Running": True, "Health": {"Status": "healthy"}}}), \
             patch.object(manager.docker, "json", return_value=row):
            with self.assertRaisesRegex(host.Hold, "RUNTIME_STARTUP_TIMEOUT"):
                manager.postflight(document, started, budget=10, sleep=lambda seconds: None,
                                   clock=lambda: next(times))
        self.assertEqual(manager.receipt["state"], "NO_VERIFICADO")

    def test_release_marker_is_accepted_only_after_real_postflight_signal(self):
        from scripts.rc6_operator_paper_rearm import _validated_paper_release
        (self.base / "data").mkdir()
        release = {"source_sha": SHA, "source_tree": TREE, "image_id": IMAGE,
                   "members": {"porota-predeploy-image.tar.gz": {"sha256": "e" * 64}}}
        kwargs = {"marker_path": self.base / "data/deploy/CURRENT_STATE_V2.json",
                  "frozen_path": self.base / "data/deploy/porota-frozen-candidate.json", "context": "runtime",
                  "candidate_sha": SHA, "candidate_tree_sha": TREE}
        host.publish_release_marker(self.base, release, validated=False)
        with self.assertRaisesRegex(ValueError, "OPERATOR_REARM_RUNTIME_NOT_VALIDATED_PAPER0"):
            _validated_paper_release(**kwargs)
        host.publish_release_marker(self.base, release, validated=True)
        self.assertEqual(_validated_paper_release(**kwargs), (SHA, TREE, IMAGE))
        frozen = json.loads(kwargs["frozen_path"].read_text())
        self.assertEqual(frozen["image_tar_sha256"], "e" * 64)
        self.assertEqual(frozen["real_orders_sent_required"], 0)
        mode = json.loads((self.base / "data/operation_mode.json").read_text())
        self.assertEqual((mode["mode"], mode["execution"], mode["apis"]["PPI_ORDERS"]),
                         ("PRODUCTION_PAPER", "SIMULATED", "BLOCKED"))

    def test_private_release_state_rechecks_hash_identity_policy_and_directory_custody(self):
        identity = host.Identity(os.getuid(), os.getgid(), "test", self.base)
        host.private_dir(identity.state)
        directory = host.private_dir(identity.state / "releases" / SHA)
        release = {"schema": "porota.release.v1", "source_sha": SHA, "source_tree": TREE, "image_id": IMAGE,
                   "operating_mode": "PRODUCTION_PAPER", "execution_mode": "SIMULATION",
                   "real_orders_sent_required": 0, "real_order_capability": "BLOCKED"}
        host.write_json(directory / "release.json", release)
        raw = (directory / "release.json").read_bytes()
        current = {"schema": "porota.rootless.deploy.v1", "state": "VALIDADO_RUNTIME",
                   **{key: release[key] for key in ("source_sha", "source_tree", "image_id", "operating_mode", "execution_mode")},
                   "real_orders_sent": 0, "release_manifest_sha256": hashlib.sha256(raw).hexdigest()}
        host.write_json(identity.state / "current.json", current)
        self.assertEqual(host.read_release_state(identity), (current, release))
        directory.chmod(0o777)
        with self.assertRaisesRegex(host.Hold, "PRIVATE_RELEASE_DIRECTORY_UNSAFE"):
            host.read_release_state(identity)
        directory.chmod(0o700)
        (directory / "release.json").write_bytes(raw + b" ")
        with self.assertRaisesRegex(host.Hold, "PRIVATE_RELEASE_MANIFEST_CHANGED"):
            host.read_release_state(identity)
        release["real_order_capability"] = "ENABLED"
        host.write_json(directory / "release.json", release)
        current["release_manifest_sha256"] = hashlib.sha256((directory / "release.json").read_bytes()).hexdigest()
        host.write_json(identity.state / "current.json", current)
        with self.assertRaisesRegex(host.Hold, "PRIVATE_RELEASE_IDENTITY_MISMATCH"):
            host.read_release_state(identity)

    def test_tmpfs_mount_reporting_is_portable_but_extra_bind_mounts_are_rejected(self):
        value = container_fixture(self.base, self.base / "operator", "dashboard")
        kwargs = dict(role="dashboard", image_id=IMAGE, source_sha=SHA, source_tree=TREE,
                      attempt="next", root=self.base, operator=self.base / "operator")
        value["Mounts"].append({"Type": "tmpfs", "Destination": "/tmp"})
        host.validate_container(value, **kwargs)
        value["HostConfig"]["Tmpfs"]["/tmp"] = "rw,noexec,nosuid,size=134217728"
        host.validate_container(value, **kwargs)
        value["Mounts"].append({"Type": "bind", "Source": "/var/run/docker.sock", "Destination": "/docker.sock", "RW": True})
        with self.assertRaisesRegex(host.Hold, "RUNTIME_STORAGE_MISMATCH"):
            host.validate_container(value, **kwargs)

    def test_persisted_source_survives_staging_without_copying_or_creating_data(self):
        identity = host.Identity(os.getuid(), os.getgid(), "test", self.base)
        stage = self.base / "stage"
        stage.mkdir()
        content = b"print('source only')\n"
        manifest = json.dumps({"files": [{"path": "scripts/job.py", "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest()}]}).encode()
        release = {"source_sha": SHA}
        (stage / "release.json").write_text(json.dumps(release))
        with tarfile.open(stage / "porota-deploy-bundle-v2.tgz", "w:gz") as archive:
            member = tarfile.TarInfo("scripts/job.py")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        digest = host.persist_release(identity, stage, release, manifest)
        self.assertEqual(digest, hashlib.sha256((stage / "release.json").read_bytes()).hexdigest())
        path = identity.state / "releases" / SHA / "source/scripts/job.py"
        self.assertEqual(path.read_bytes(), content)
        self.assertFalse((self.base / "data").exists())
        host.persist_release(identity, stage, release, manifest)
        path.write_bytes(b"drift")
        with self.assertRaisesRegex(host.Hold, "PERSISTED_SOURCE_DRIFT"):
            host.persist_release(identity, stage, release, manifest)


class DeploymentFailureTests(unittest.TestCase):
    """Explicit fake Docker commands exercise host journaling, not real runtime."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "root"
        self.root.mkdir()
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir()
        self.identity = host.Identity(os.getuid(), os.getgid(), "test", self.home)
        host.private_dir(self.identity.store)
        host.private_dir(self.identity.state)
        (self.root / "data/paper_v17").mkdir(parents=True)
        with sqlite3.connect(self.root / host.DB_RELATIVE) as connection:
            connection.executescript("CREATE TABLE observer_state(id,mode,real_orders_sent);"
                "INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);"
                "CREATE TABLE paper_workspace(id,dataset_id);INSERT INTO paper_workspace VALUES(1,'existing');")
        manifest = b'{"schema_version":1,"status":"GREEN","files":[]}'
        (self.root / "porota-deploy-bundle-v2-manifest.json").write_bytes(manifest)
        self.release = {"source_sha": SHA, "source_tree": TREE, "image_id": IMAGE, "image_size_bytes": 1,
            "members": {"porota-predeploy-image.tar.gz": {"bytes": 1, "sha256": "e" * 64},
                        "porota-deploy-bundle-v2.tgz": {"bytes": 1},
                        "porota-deploy-bundle-v2-manifest.json": {"sha256": hashlib.sha256(manifest).hexdigest()}}}
        self.verifier = patch.dict(sys.modules, {"scripts.porota_release": types.SimpleNamespace(
            verify_release=lambda *args, **kwargs: copy.deepcopy(self.release))})
        self.verifier.start()
        self.addCleanup(self.verifier.stop)
        self.disk = patch.object(host.shutil, "disk_usage", return_value=types.SimpleNamespace(free=20 * 1024**3))
        self.disk.start()
        self.addCleanup(self.disk.stop)
        self.persistence = patch.object(host, "persist_release", return_value="e" * 64)
        self.persistence.start()
        self.addCleanup(self.persistence.stop)

    def manager(self, *, failure=None, previous=False):
        test = self
        class FakeDocker:
            def __init__(self):
                self.containers, self.calls, self.images = {}, [], set()
            def json(self, *args):
                return json.loads(self.call(*args))
            def call(self, *args, **kwargs):
                self.calls.append(args)
                if args[:2] == ("image", "ls"):
                    return "\n".join(sorted(self.images))
                if args[:2] == ("image", "load"):
                    self.images.add(IMAGE)
                    return ""
                if args[:2] == ("image", "inspect"):
                    return json.dumps([{"Id": IMAGE, "Config": {"Env": [f"POROTA_BUILD_SHA={SHA}", f"POROTA_CANDIDATE_TREE_SHA={TREE}"]}}])
                if args[:2] == ("image", "rm"):
                    self.images.remove(args[-1])
                    return ""
                if args[:2] == ("container", "inspect"):
                    return json.dumps([self.containers[args[-1]]])
                if args[:2] == ("container", "ls"):
                    selected = self.containers.values()
                    if "--filter" in args:
                        selected = [x for x in selected if x["Config"]["Labels"]["io.porota.attempt"] == "next"]
                    return "\n".join(x["Id"] for x in selected)
                if args[:2] == ("container", "start"):
                    self.containers[args[-1]]["State"]["Running"] = True
                    return ""
                if args[:2] == ("container", "update"):
                    return ""
                if args[:2] == ("container", "stop"):
                    self.containers[args[-1]]["State"]["Running"] = False
                    return ""
                if args[:2] == ("container", "rm"):
                    if failure == "cleanup" and self.containers[args[-1]]["Config"]["Labels"]["io.porota.attempt"] == "old":
                        raise host.Hold("FAKE_OLD_CLEANUP_FAILURE")
                    del self.containers[args[-1]]
                    return ""
                if args[:2] == ("network", "ls"):
                    return ""
                raise AssertionError(args)
        docker = FakeDocker()
        class FakeHost(host.Host):
            def boundary(self):
                return "daemon"
            def prepare_environment(self, directory, release):
                return None
            def effective_mapping_probe(self, image_id, attempt):
                return {"fake_test_mapping": True}
            def compose(self, release_dir, directory, *args):
                if args[0] == "create":
                    for role in host.ROLES:
                        item = container_fixture(self.root, self.identity.operator, role, attempt=directory.name)
                        item["State"]["Running"] = False
                        docker.containers[item["Id"]] = item
                        if failure == "partial_create":
                            raise host.Hold("FAKE_PARTIAL_CREATE")
                    return ""
                return ("1" if args[-1] == "observer" else "2") * 64
            def postflight(self, document, started, **kwargs):
                if failure == "postflight":
                    raise host.Hold("FAKE_RUNTIME_TIMEOUT")
                return {"dashboard": "healthy", "observer": "running", "orders_guard": "BLOCKED"}
        manager = FakeHost(self.root, self.identity, docker)
        if previous:
            old = {"source_sha": SHA, "source_tree": TREE, "image_id": "sha256:" + "d" * 64,
                   "attempt": "old", "roles": {}, "state": "VALIDADO_RUNTIME", "daemon_id": "daemon"}
            for role, token in (("observer", "3"), ("dashboard", "4")):
                item = container_fixture(self.root, self.identity.operator, role, attempt="old", image=old["image_id"], cid=token * 64)
                docker.containers[item["Id"]] = item
                old["roles"][role] = item["Id"]
            docker.images.add(old["image_id"])
            host.write_json(self.identity.state / "current.json", old)
        return manager

    def test_partial_creation_is_cleaned_without_touching_legacy_runtime(self):
        manager = self.manager(failure="partial_create")
        touched = []
        with self.assertRaisesRegex(host.Hold, "FAKE_PARTIAL_CREATE"):
            manager.deploy(self.root, SHA, TREE, "next", transition=lambda: touched.append(True))
        self.assertEqual(touched, [])
        self.assertEqual(manager.docker.containers, {})
        self.assertEqual(manager.receipt["cleanup"]["state"], "COMPLETE")
        self.assertFalse((self.identity.state / "current.json").exists())

    def test_data_disk_is_independently_checked_before_load_or_quiescence(self):
        manager = self.manager(previous=True)
        with patch.object(host.shutil, "disk_usage", side_effect=lambda path:
                types.SimpleNamespace(free=1 if Path(path) == self.root / "data" else 20 * 1024**3)):
            with self.assertRaisesRegex(host.Hold, "DATA_DISK_CAPACITY"):
                manager.deploy(self.root, SHA, TREE, "next")
        self.assertFalse(any(command[:2] in (("image", "load"), ("container", "stop"))
                             for command in manager.docker.calls))
        self.assertEqual(manager.receipt["storage"]["data_free_before"], 1)
        current = json.loads((self.identity.state / "current.json").read_text())
        self.assertEqual(current["state"], "VALIDADO_RUNTIME")

    def test_failed_quiescence_is_not_recorded_as_no_transition(self):
        manager = self.manager()
        def transition():
            raise host.Hold("FAKE_PARTIAL_QUIESCE")
        with self.assertRaisesRegex(host.Hold, "FAKE_PARTIAL_QUIESCE"):
            manager.deploy(self.root, SHA, TREE, "next", transition=transition)
        self.assertTrue(manager.receipt["transition_started"])
        current = json.loads((self.identity.state / "current.json").read_text())
        self.assertEqual(current["state"], "NO_VERIFICADO")
        self.assertFalse(current["fix_forward_ready"])

    def test_lost_legacy_lock_after_quiescence_prevents_candidate_start(self):
        manager = self.manager()
        alive = [True]
        def transition():
            alive[0] = False
            return {"state": "QUIESCENT"}
        def guard():
            host.require(alive[0], "LEGACY_LOCK_LOST")
        with self.assertRaisesRegex(host.Hold, "LEGACY_LOCK_LOST"):
            manager.deploy(self.root, SHA, TREE, "next", transition=transition, guard=guard)
        self.assertFalse(any(command[:2] == ("container", "start") for command in manager.docker.calls))
        self.assertEqual(manager.receipt["state"], "NO_VERIFICADO")

    def test_routine_deploy_refuses_unrestored_initial_migration_before_loading_image(self):
        manager = self.manager(previous=True)
        path = self.identity.state / "current.json"
        current = json.loads(path.read_text())
        current["migration_pending"] = True
        host.write_json(path, current)
        with self.assertRaisesRegex(host.Hold, "INITIAL_MIGRATION_INCOMPLETE_USE_MIGRATE"):
            manager.deploy(self.root, SHA, TREE, "next")
        self.assertFalse(any(command[:2] in (("image", "load"), ("container", "stop"))
                             for command in manager.docker.calls))

    def test_resumed_migration_retires_its_existing_private_pair_before_new_start(self):
        manager = self.manager(previous=True)
        path = self.identity.state / "current.json"
        current = json.loads(path.read_text())
        current["migration_pending"] = True
        host.write_json(path, current)
        result = manager.deploy(self.root, SHA, TREE, "next", transition=lambda: {"state": "QUIESCENT"})
        self.assertEqual(result["state"], "VALIDADO_RUNTIME")
        self.assertTrue(result["migration_pending"], "only setup may record timer restoration")
        starts = [i for i, command in enumerate(manager.docker.calls) if command[:2] == ("container", "start")]
        stops = [i for i, command in enumerate(manager.docker.calls) if command[:2] == ("container", "stop")]
        self.assertTrue(starts and stops and max(stops) < min(starts))
        self.assertEqual(set(manager.docker.containers), set(result["roles"].values()))

    def test_partial_initial_quiescence_can_retry_only_through_real_migration_producer(self):
        manager = self.manager()
        def failed():
            raise host.Hold("FAKE_PARTIAL_QUIESCE")
        with self.assertRaisesRegex(host.Hold, "FAKE_PARTIAL_QUIESCE"):
            manager.deploy(self.root, SHA, TREE, "next", transition=failed)
        current = json.loads((self.identity.state / "current.json").read_text())
        self.assertTrue(current["migration_pending"])
        self.assertEqual(current["roles"], {})
        self.assertFalse(current["fix_forward_ready"])
        manager = self.manager()
        produced = []
        def quiesce():
            produced.append(True)
            return {"state": "QUIESCENT"}
        result = manager.deploy(self.root, SHA, TREE, "retry", transition=quiesce)
        self.assertEqual(result["state"], "VALIDADO_RUNTIME")
        self.assertEqual(produced, [True])

    def test_failed_postflight_invalidates_previous_marker_and_does_not_rollback(self):
        manager = self.manager(failure="postflight", previous=True)
        with self.assertRaisesRegex(host.Hold, "FAKE_RUNTIME_TIMEOUT"):
            manager.deploy(self.root, SHA, TREE, "next")
        current = json.loads((self.identity.state / "current.json").read_text())
        self.assertEqual(current["state"], "NO_VERIFICADO")
        self.assertTrue(current["fix_forward_ready"])
        self.assertEqual(set(manager.docker.containers), {"3" * 64, "4" * 64})
        self.assertTrue(all(not x["State"]["Running"] for x in manager.docker.containers.values()))
        marker = json.loads((self.root / "data/deploy/CURRENT_STATE_V2.json").read_text())
        self.assertEqual(marker["validation_status"], "NO_VERIFICADO")
        started = [x[-1] for x in manager.docker.calls if x[:2] == ("container", "start")]
        self.assertNotIn("3" * 64, started)

    def test_cleanup_failure_preserves_validated_replacement_and_evidence(self):
        manager = self.manager(failure="cleanup", previous=True)
        with self.assertRaisesRegex(host.Hold, "FAKE_OLD_CLEANUP_FAILURE"):
            manager.deploy(self.root, SHA, TREE, "next")
        current = json.loads((self.identity.state / "current.json").read_text())
        self.assertEqual(current["state"], "VALIDADO_RUNTIME")
        self.assertEqual(current["cleanup"]["state"], "INCOMPLETE")
        self.assertTrue(all(manager.docker.containers[cid]["State"]["Running"] for cid in current["roles"].values()))
        self.assertEqual(current["cleanup_pending"]["attempt"], "old")


if __name__ == "__main__":
    unittest.main()
