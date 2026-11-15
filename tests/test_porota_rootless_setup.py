import json
import hashlib
import io
import os
import subprocess
import tempfile
import unittest
import sys
import types
from contextlib import redirect_stdout, contextmanager
from pathlib import Path
from unittest.mock import patch
from scripts import porota_rootless_host as host

try:
    from scripts import porota_rootless_setup as setup
except ImportError:
    setup = None


class InspectTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(setup, "rootless inspection must exist before migration")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.commands = []

    def fake_run(self, command, **kwargs):
        self.commands.append(tuple(command))
        if command[0] == "/usr/bin/sudo" and command[-1] == "/usr/bin/true":
            return subprocess.CompletedProcess(command, 0, "", "")
        if "inspect" in command:
            name = command[-1]
            script = {setup.LEGACY_NAMES[0]: "bv_paper_runtime.py",
                      setup.LEGACY_NAMES[1]: "o_dashboard.py"}.get(name)
            raw = [{"Id": "a" * 64, "Name": "/" + name,
                    "Config": {"Env": ["SECRET=must-never-leak"], "Entrypoint": ["python"],
                               "Cmd": [script] if script else ["private-unknown-argument"]},
                    "State": {"Status": "running", "Running": True},
                    "Image": "sha256:" + "b" * 64,
                    "Mounts": [{"Destination": "/app/data", "Source": str(self.root / "data")}]}]
            return subprocess.CompletedProcess(command, 0, json.dumps(raw), "")
        return subprocess.CompletedProcess(command, 1, "", "denied secret detail")

    def test_inspection_never_mutates_or_serializes_environment_values(self):
        result = setup.inspect_host(self.root, run=self.fake_run)
        self.assertEqual(result["host_mutations"], 0)
        self.assertNotIn("must-never-leak", json.dumps(result))
        self.assertNotIn("denied secret detail", json.dumps(result))
        self.assertTrue(result["sudo_noninteractive_true"])
        self.assertEqual(len(result["legacy_rc6"]), 3)
        self.assertTrue(all(row["data_mount_matches"] for row in result["legacy_rc6"]))
        self.assertFalse(any(set(c) & {"stop", "start", "restart", "rm", "prune", "enable", "disable"}
                             for c in self.commands))

    def test_inspection_exposes_only_recognized_command_literals(self):
        result = setup.inspect_host(self.root, run=self.fake_run)
        observer, dashboard, monolith = result["legacy_rc6"]
        self.assertEqual(observer["command_identity"]["profile"], "BV_PAPER_RUNTIME")
        self.assertEqual(observer["command_identity"]["entrypoint"], ["python"])
        self.assertEqual(observer["command_identity"]["cmd"], ["bv_paper_runtime.py"])
        self.assertEqual(dashboard["command_identity"]["profile"], "DASHBOARD")
        self.assertEqual(monolith["command_identity"]["profile"], "UNRECOGNIZED")
        self.assertNotIn("cmd", monolith["command_identity"])
        self.assertNotIn("entrypoint", monolith["command_identity"])
        self.assertNotIn("private-unknown-argument", json.dumps(result))
        self.assertTrue(all(len(row["command_identity"]["sha256"]) == 64
                            for row in result["legacy_rc6"]))

    def test_only_fixed_rc6_names_are_inspected_on_explicit_rootful_endpoint(self):
        setup.inspect_host(self.root, run=self.fake_run)
        commands = [c for c in self.commands if "inspect" in c]
        self.assertEqual({c[-1] for c in commands}, {
            "porota_production_observer", "porota_production_dashboard", "porota_trading_bot"})
        self.assertTrue(all("unix:///var/run/docker.sock" in c for c in commands))

    def test_missing_sudo_is_reported_without_claiming_legacy_absent(self):
        def unavailable(command, **kwargs):
            raise FileNotFoundError("private diagnostic")
        result = setup.inspect_host(self.root, run=unavailable)
        self.assertFalse(result["sudo_noninteractive_true"])
        self.assertTrue(all(x["status"] == "NO_VERIFICADO" for x in result["legacy_rc6"]))
        self.assertEqual(result["state"], "NO_VERIFICADO")

    def test_streamed_inspection_can_report_real_units_without_local_checkout(self):
        def commands(command, **kwargs):
            if "list-units" in command or "list-unit-files" in command:
                return subprocess.CompletedProcess(command, 0, "porota-preopen-rc6.timer enabled\n", "")
            if "show" in command:
                return subprocess.CompletedProcess(command, 0, "Id=porota-preopen-rc6.timer\nLoadState=loaded\nActiveState=active\nUnitFileState=enabled\n", "")
            raise AssertionError(command)
        result = setup.unit_inventory(self.root, run=commands)
        self.assertEqual(result["status"], "OBSERVED_POLICY_UNAVAILABLE")
        self.assertEqual(result["units"][0]["name"], "porota-preopen-rc6.timer")
        self.assertEqual(result["units"][0]["policy_role"], "UNKNOWN")
        self.assertEqual(result["unmapped_units"], ["porota-preopen-rc6.timer"])

    def test_unit_details_keep_paths_and_digests_without_environment_or_shell_secrets(self):
        unit = "porota-snapshot-preopen-rc6.service"
        virtual = "/etc/systemd/system/" + unit
        actual = self.root / "unit"
        actual.write_text("[Service]\nUser=porotaadmin\nEnvironment=TOKEN=private-env-value MODE=PAPER\n"
            "EnvironmentFile=/etc/porota/secrets.env\n"
            "ExecStart=/bin/bash -c '/usr/bin/python3 /opt/porota-trading/job.py --token private-argument https://secret.invalid/token'\n")
        raw = actual.read_bytes()
        original_open, original_stat = os.open, os.lstat
        def opened(path, flags, *args, **kwargs):
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))
            return original_open(actual if path == virtual else path, flags, *args, **kwargs)
        def metadata(path, *args, **kwargs):
            value = original_stat(actual if path == virtual else path, *args, **kwargs)
            if path == virtual:
                fields = list(value)
                fields[4] = 0
                return os.stat_result(fields)
            return value
        out = io.StringIO()
        with patch.object(sys, "argv", ["inspect", json.dumps([unit]), json.dumps([virtual])]), \
             patch.object(os, "open", side_effect=opened), patch.object(os, "lstat", side_effect=metadata), \
             redirect_stdout(out):
            exec(compile(setup.UNIT_DETAILS, "unit-details-readonly", "exec"), {})
        result = json.loads(out.getvalue())
        serialized = json.dumps(result)
        for secret in ("private-env-value", "private-argument", "secret.invalid"):
            self.assertNotIn(secret, serialized)
        self.assertIn("/opt/porota-trading/job.py", serialized)
        self.assertIn("/etc/porota/secrets.env", serialized)
        self.assertEqual(result[0]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(set(result[0]["environment_names"]), {"TOKEN", "MODE"})

    def test_tool_versions_are_sanitized_and_launcher_is_never_executed(self):
        calls = []
        def version(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "version 29.1.0 PRIVATE_DIAGNOSTIC", "")
        with patch.object(setup.shutil, "which", side_effect=lambda name, **kwargs: "/usr/bin/" + name):
            result = setup.toolchain_metadata(run=version)
        self.assertTrue(result["vpnkit"]["available"])
        self.assertEqual(result["docker"]["version"], "29.1.0")
        self.assertNotIn("PRIVATE_DIAGNOSTIC", json.dumps(result))
        self.assertFalse(any(command[0].endswith("dockerd-rootless.sh") for command in calls))

    def test_filesystem_inspection_does_not_create_missing_store(self):
        missing = self.root / "nested" / "docker"
        result = setup.filesystem_metadata(missing)
        self.assertEqual(result["status"], "OBSERVED")
        self.assertFalse(result["target_exists"])
        self.assertFalse(missing.parent.exists())


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.identity = host.Identity(os.getuid(), os.getgid(), "porotaadmin", self.home)
        self.commands = []
        self.info = {"ID": "dedicated-daemon", "SecurityOptions": ["name=rootless"], "CgroupVersion": "2",
                     "CgroupDriver": "systemd", "DockerRootDir": str(self.identity.store),
                     "Driver": "overlay2", "DriverStatus": [["Backing Filesystem", "extfs"]]}
        self.images, self.containers = {}, []
        self.config_path = self.home / ".config/porota-rc6/daemon.json"
        self.unit_path = self.home / ".config/systemd/user" / setup.SERVICE
        self.restart_failure = False
        self.daemon_active = True

    def commands_ok(self, command, **kwargs):
        self.commands.append(command)
        stdout = ""
        if "loginctl" in command[0] and "show-user" in command:
            stdout = "yes"
        if "compose" in command and "version" in command:
            stdout = "2.35.1"
        if command[0] == setup.DOCKER and command[3:4] == ("info",):
            stdout = json.dumps(self.info)
        if command[0] == setup.DOCKER and command[3:5] == ("container", "ls"):
            stdout = "\n".join(self.containers)
        if command[0] == setup.DOCKER and command[3:5] == ("image", "ls"):
            stdout = "\n".join(self.images)
        if command[0] == setup.DOCKER and command[3:5] == ("image", "inspect"):
            stdout = json.dumps([self.images[command[-1]]])
        if command[0] == setup.DOCKER and command[3:5] == ("image", "rm"):
            del self.images[command[-1]]
        if command[:3] == (setup.SYSTEMCTL, "--user", "show"):
            stdout = f"FragmentPath={self.unit_path}\nDropInPaths=\n"
        if command[:3] == (setup.SYSTEMCTL, "--user", "restart"):
            if self.restart_failure:
                self.daemon_active = False
                return subprocess.CompletedProcess(command, 1, "", "private failure")
            self.daemon_active = True
            self.info.update(Driver="overlay2", DriverStatus=[["Backing Filesystem", "extfs"]])
        if command[:4] == (setup.SYSTEMCTL, "--user", "enable", "--now") and not self.daemon_active:
            self.daemon_active = True
            self.info.update(Driver="overlay2", DriverStatus=[["Backing Filesystem", "extfs"]])
        return subprocess.CompletedProcess(command, 0, stdout, "")

    @contextmanager
    def preparation(self):
        original_read = host.read_regular
        def read(path, *args):
            if str(path).endswith("cgroup.controllers"):
                return b"cpu memory pids"
            return original_read(path, *args)
        def boundary(_manager, **kwargs):
            if not self.daemon_active:
                raise host.Hold("ROOTLESS_SOCKET_REQUIRED")
            return host.validate_daemon(self.info, self.identity, **kwargs)
        with patch.object(setup.shutil, "which", side_effect=lambda name, **kwargs: "/usr/bin/" + name), \
             patch.object(setup, "subids_available", return_value=True), \
             patch.object(host, "read_regular", side_effect=read), \
             patch.object(host.Host, "boundary", new=boundary), \
             patch.object(setup.shutil, "disk_usage", side_effect=lambda path:
                          types.SimpleNamespace(free=1000 if self.images else 5000)):
            yield

    def prior_setup(self, *, new_config=False):
        runtime = f"/run/user/{self.identity.uid}/porota-rc6"
        config = {"hosts": [f"unix://{self.identity.socket}"], "data-root": str(self.identity.store),
                  "exec-root": runtime + "/exec", "pidfile": runtime + "/dockerd.pid",
                  "exec-opts": ["native.cgroupdriver=systemd"], "live-restore": False}
        if new_config:
            config.update(features={"containerd-snapshotter": False}, **{"storage-driver": "overlay2"})
        self.config_path.parent.mkdir(parents=True, mode=0o700)
        self.config_path.write_text(json.dumps(config, sort_keys=True) + "\n")
        self.config_path.chmod(0o600)
        self.unit_path.parent.mkdir(parents=True, mode=0o700)
        self.unit_path.write_bytes(setup.daemon_configuration(self.identity, "/usr/bin/dockerd-rootless.sh")[2])
        self.info.update(Driver="overlayfs", DriverStatus=[["driver-type", "io.containerd.snapshotter.v1"]])

    def own_image(self):
        source, tree, config_id, target = "a" * 40, "b" * 40, "sha256:" + "c" * 64, "sha256:" + "d" * 64
        self.images[target] = {"Id": target, "Config": {
            "Env": [f"POROTA_BUILD_SHA={source}", f"POROTA_CANDIDATE_TREE_SHA={tree}", "PRIVATE_VALUE=never-publish"],
            "Labels": {"porota.commit": source, "porota.predeploy": "v2"}}}
        release = {"schema": "porota.release.v1", "source_sha": source, "source_tree": tree, "image_id": config_id,
                   "operating_mode": "PRODUCTION_PAPER", "execution_mode": "SIMULATION",
                   "real_order_capability": "BLOCKED", "real_orders_sent_required": 0}
        host.private_dir(self.identity.state)
        host.private_dir(self.identity.state / "releases")
        host.private_dir(self.identity.state / "attempts")
        # Host.deploy's mkdir(parents=True) keeps this intermediate directory
        # owned and non-writable by others; only the attempt leaf is private.
        (self.identity.state / "attempts").chmod(0o755)
        directory = self.identity.state / "releases" / source
        directory.mkdir(parents=True, mode=0o700)
        raw = (json.dumps(release, sort_keys=True) + "\n").encode()
        host.atomic_write(directory / "release.json", raw)
        journal = self.identity.state / "attempts" / "123-1" / "receipt.json"
        journal.parent.mkdir(parents=True, mode=0o700)
        host.write_json(journal, {"schema": "porota.rootless.deploy.v1", "state": "NO_VERIFICADO",
            "phase": "LOAD", "source_sha": source, "source_tree": tree, "image_id": config_id,
            "roles": {}, "transition_started": False, "release_manifest_sha256": hashlib.sha256(raw).hexdigest()})
        return target, directory / "release.json"

    def test_upgrade_removes_only_verified_image_before_restart_and_is_idempotent(self):
        self.prior_setup()
        target, _ = self.own_image()
        with self.preparation():
            result = setup.prepare(self.identity, run=self.commands_ok)
            setup.prepare(self.identity, run=self.commands_ok)
        config = json.loads(self.config_path.read_bytes())
        self.assertEqual(config["features"], {"containerd-snapshotter": False})
        self.assertEqual(config["storage-driver"], "overlay2")
        restarts = [c for c in self.commands if "restart" in c]
        self.assertEqual(restarts, [(setup.SYSTEMCTL, "--user", "restart", setup.SERVICE)])
        removal = next(c for c in self.commands if c[3:5] == ("image", "rm"))
        self.assertEqual(removal[-1], target)
        self.assertLess(self.commands.index(removal), self.commands.index(restarts[0]))
        self.assertEqual(result["storage_transition"]["reclaimed_bytes"], 4000)
        self.assertEqual(result["storage_transition"]["state"], "COMPLETE")
        self.assertEqual(result["image_store"], {"driver": "overlay2", "containerd_snapshotter": False})
        self.assertNotIn("never-publish", json.dumps(result))
        self.assertFalse(self.images)
        self.assertFalse(any("prune" in c or "--force" in c or "docker.service" in c for c in self.commands))

    def test_failed_restart_leaving_daemon_stopped_resumes_with_idempotent_start(self):
        self.prior_setup()
        self.own_image()
        self.restart_failure = True
        with self.preparation():
            with self.assertRaises(host.Hold):
                setup.prepare(self.identity, run=self.commands_ok)
            self.assertEqual(json.loads(self.config_path.read_bytes())["storage-driver"], "overlay2")
            self.restart_failure = False
            result = setup.prepare(self.identity, run=self.commands_ok)
        self.assertEqual(result["state"], "PREPARADO")
        self.assertEqual(len([c for c in self.commands if c[3:5] == ("image", "rm")]), 1)
        self.assertEqual(len([c for c in self.commands if "restart" in c]), 1)

    def test_new_config_with_old_daemon_converges_after_interrupted_config_write(self):
        self.prior_setup(new_config=True)
        with self.preparation():
            result = setup.prepare(self.identity, run=self.commands_ok)
        self.assertEqual(result["state"], "PREPARADO")
        self.assertEqual(len([c for c in self.commands if "restart" in c]), 1)

    def test_unknown_config_or_unit_is_not_overwritten_or_cleaned(self):
        self.prior_setup()
        self.own_image()
        self.config_path.write_text(self.config_path.read_text() + " ")
        before = self.config_path.read_bytes()
        with self.preparation(), self.assertRaisesRegex(host.Hold, "ROOTLESS_SETUP_DRIFT"):
            setup.prepare(self.identity, run=self.commands_ok)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertTrue(self.images)
        self.assertFalse(any("restart" in c or "rm" in c for c in self.commands))

    def test_unknown_unit_or_missing_existing_config_is_not_repaired(self):
        self.prior_setup()
        self.own_image()
        original = self.unit_path.read_bytes()
        for damage in ("unit", "missing_config"):
            with self.subTest(damage=damage):
                if damage == "unit":
                    self.unit_path.write_bytes(original + b"# unknown change\n")
                else:
                    self.unit_path.write_bytes(original)
                    self.config_path.unlink()
                with self.preparation(), self.assertRaisesRegex(host.Hold, "ROOTLESS_SETUP_DRIFT"):
                    setup.prepare(self.identity, run=self.commands_ok)
                self.assertTrue(self.images)
                self.assertFalse(any("restart" in c or "rm" in c for c in self.commands))

    def test_existing_current_or_any_container_forbids_backend_transition(self):
        self.prior_setup()
        self.own_image()
        for existing_current in (False, True):
            with self.subTest(existing_current=existing_current):
                self.commands.clear()
                self.containers = [] if existing_current else ["f" * 64]
                if existing_current:
                    host.write_json(self.identity.state / "current.json", {"state": "VALIDADO_RUNTIME"})
                with self.preparation(), self.assertRaisesRegex(host.Hold, "ROOTLESS_STORE_TRANSITION_NOT_EMPTY"):
                    setup.prepare(self.identity, run=self.commands_ok)
                self.assertTrue(self.images)
                self.assertFalse(any("restart" in c or "rm" in c for c in self.commands))

    def test_image_with_wrong_tree_or_release_custody_is_never_removed(self):
        self.prior_setup()
        target, release_path = self.own_image()
        for damage in ("labels", "tree", "public", "changed"):
            with self.subTest(damage=damage):
                self.commands.clear()
                self.images[target]["Config"]["Labels"]["porota.predeploy"] = "other" if damage == "labels" else "v2"
                if damage == "tree":
                    self.images[target]["Config"]["Env"][1] = "POROTA_CANDIDATE_TREE_SHA=" + "e" * 40
                else:
                    self.images[target]["Config"]["Env"][1] = "POROTA_CANDIDATE_TREE_SHA=" + "b" * 40
                    release_path.chmod(0o644 if damage == "public" else 0o600)
                    if damage == "changed":
                        release_path.write_bytes(release_path.read_bytes() + b" ")
                with self.preparation(), self.assertRaisesRegex(host.Hold, "ROOTLESS_STORE_IMAGE_PROVENANCE"):
                    setup.prepare(self.identity, run=self.commands_ok)
                self.assertTrue(self.images)
                self.assertFalse(any("restart" in c or "rm" in c for c in self.commands))

    def test_journal_must_explicitly_prove_transition_did_not_start(self):
        self.prior_setup()
        self.own_image()
        journal = self.identity.state / "attempts/123-1/receipt.json"
        original = json.loads(journal.read_bytes())
        for state in ("missing", True):
            with self.subTest(state=state):
                value = dict(original)
                if state == "missing":
                    value.pop("transition_started")
                else:
                    value["transition_started"] = state
                host.write_json(journal, value)
                with self.preparation(), self.assertRaisesRegex(host.Hold, "ROOTLESS_STORE_IMAGE_PROVENANCE"):
                    setup.prepare(self.identity, run=self.commands_ok)
                self.assertTrue(self.images)
                self.assertFalse(any("restart" in c or "rm" in c for c in self.commands))

    def test_prepare_never_installs_packages_when_toolchain_is_missing(self):
        with patch.object(setup.shutil, "which", return_value=None):
            with self.assertRaisesRegex(host.Hold, "ROOTLESS_PACKAGES_REQUIRED"):
                setup.prepare(self.identity, run=self.commands_ok)
        self.assertEqual(self.commands, [])
        receipt = json.loads((self.identity.state / "prepare.json").read_text())
        self.assertIn("newuidmap", receipt["missing_prerequisites"])
        self.assertEqual(receipt["state"], "NO_VERIFICADO")

    def test_prepare_creates_only_dedicated_user_unit_and_pins_socket_store(self):
        original_read = host.read_regular
        def read(path, *args):
            if str(path).endswith("cgroup.controllers"):
                return b"cpu memory pids"
            return original_read(path, *args)
        with patch.object(setup.shutil, "which", side_effect=lambda name, **kwargs: "/usr/bin/" + name), \
             patch.object(setup, "subids_available", return_value=True), \
             patch.object(host, "read_regular", side_effect=read), \
             patch.object(host.Host, "boundary", return_value="dedicated-daemon"):
            result = setup.prepare(self.identity, run=self.commands_ok)
            # Identical re-entry checks the same files; no global Docker lifecycle exists.
            setup.prepare(self.identity, run=self.commands_ok)
        config = json.loads((self.home / ".config/porota-rc6/daemon.json").read_text())
        self.assertEqual(config["hosts"], [f"unix://{self.identity.socket}"])
        self.assertEqual(config["data-root"], str(self.identity.store))
        self.assertEqual(result["runtime_validation"], "NO_VERIFICADO")
        system = [x for x in self.commands if x[0] == setup.SYSTEMCTL]
        self.assertTrue(system)
        self.assertTrue(all("--user" in x for x in system))
        self.assertFalse(any(x[0] == setup.SUDO for x in self.commands))
        compose = [x for x in self.commands if "compose" in x]
        self.assertTrue(compose and all(f"unix://{self.identity.socket}" in x for x in compose))
        self.assertFalse((self.home / ".config/systemd/user/docker.service").exists())

    def test_bootstrap_rejects_unsupported_systemd_path(self):
        unsafe = host.Identity(1000, 1000, "porotaadmin", Path("/home/user%h"))
        with self.assertRaisesRegex(host.Hold, "BOOTSTRAP_PATH_UNSUPPORTED"):
            setup.daemon_configuration(unsafe, "/usr/bin/dockerd-rootless.sh")

    def test_subids_do_not_overlap_another_daemon_or_real_host_account(self):
        path = self.home / "subuid"
        for contents, expected in (("porotaadmin:231072:65536\n", True),
                ("porotaadmin:10000:65536\n", False),
                ("porotaadmin:231072:65536\nboca:240000:65536\n", False),
                ("porotaadmin:231072:65536\nporotaadmin:250000:65536\n", False)):
            path.write_text(contents)
            self.assertEqual(setup.subids_available(path, self.identity), expected)

    def test_access_repairs_are_bounded_and_do_not_read_secret_contents(self):
        compile(setup.PREPARE_ACCESS, "bootstrap-access", "exec")
        captures = []
        def command(args, **kwargs):
            captures.append(args)
            return subprocess.CompletedProcess(args, 0, '{"scope":"EXPLICIT_REQUIRED_RC6_PATHS","changed_paths":[],"recursive":false}', "")
        receipt = setup.prepare_access(self.identity, run=command)
        self.assertFalse(receipt["recursive"])
        self.assertEqual(captures[0][:5], (setup.SUDO, "-n", "--", "/usr/bin/python3", "-c"))
        self.assertNotIn("os.walk", setup.PREPARE_ACCESS)
        self.assertNotIn(".read(", setup.PREPARE_ACCESS)

    def test_network_install_accepts_only_new_network_packages_and_exact_versions(self):
        plan = ("0 upgraded, 2 newly installed, 0 to remove and 7 not upgraded.\n"
                "Inst libslirp0 (4.7.0-1ubuntu3 Ubuntu:noble [amd64])\n"
                "Inst slirp4netns (1.2.1-1build2 Ubuntu:noble [amd64])\n"
                "Conf libslirp0 (4.7.0-1ubuntu3 Ubuntu:noble [amd64])\n"
                "Conf slirp4netns (1.2.1-1build2 Ubuntu:noble [amd64])\n")
        available = [False]
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if "--simulate" in command:
                return subprocess.CompletedProcess(command, 0, plan, "")
            if command[0] == setup.SUDO:
                available[0] = True
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[0].endswith("dpkg-query"):
                return subprocess.CompletedProcess(command, 0,
                    "libslirp0\t4.7.0-1ubuntu3\tinstalled\nslirp4netns\t1.2.1-1build2\tinstalled\n", "")
            raise AssertionError(command)
        receipt = {}
        with patch.object(setup.shutil, "which", side_effect=lambda name, **kwargs:
                "/usr/bin/slirp4netns" if name == "slirp4netns" and available[0] else None):
            self.assertEqual(setup.ensure_network(receipt, run=run), "slirp4netns")
        install = [command for command in calls if command[0] == setup.SUDO]
        self.assertEqual(len(install), 1)
        self.assertIn("NEEDRESTART_MODE=l", install[0])
        self.assertEqual(install[0][-2:], ("libslirp0=4.7.0-1ubuntu3", "slirp4netns=1.2.1-1build2"))
        self.assertFalse(any(set(command) & {"upgrade", "update", "restart", "autoremove"} for command in calls))
        self.assertEqual(receipt["daemon_restarts_requested"], 0)
        for adverse in (plan.replace("Inst libslirp0 (", "Inst libslirp0 [4.0.0] ("),
                        plan.replace("Inst libslirp0", "Inst docker-ce"),
                        plan.replace("0 upgraded", "1 upgraded"), plan + "Remv unrelated [1.0]\n"):
            with self.subTest(plan=adverse), self.assertRaisesRegex(host.Hold, "ROOTLESS_NETWORK_INSTALL_SCOPE"):
                setup.network_install_plan(adverse)

    def test_unavailable_network_plan_never_installs_and_receipt_survives(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 100, "", "private mirror diagnostic")
        receipt = {}
        with patch.object(setup.shutil, "which", return_value=None), self.assertRaisesRegex(
                host.Hold, "ROOTLESS_NETWORK_PACKAGE_PLAN_UNAVAILABLE"):
            setup.ensure_network(receipt, run=run)
        self.assertEqual(len(calls), 1)
        self.assertIn("--simulate", calls[0])
        self.assertNotIn("private mirror", json.dumps(receipt))

    def test_access_repair_preserves_data_mode_inode_and_unrelated_private_file(self):
        base = self.home / "root"
        for name in (".secrets", "data/diagnosticos", "data/paper_v17"):
            (base / name).mkdir(parents=True)
        (base / "data").chmod(0o750)
        for name in (".env", ".secrets/ppi_production.json", ".secrets/unrelated",
                     "data/diagnosticos/dashboard_preview_v1633.env", "data/paper_v17/observer_v17.db"):
            (base / name).write_bytes(b"private-sentinel")
            (base / name).chmod(0o400)
        database = base / "data/paper_v17/observer_v17.db"
        inode = database.stat().st_ino
        original_open = os.open
        def opened(path, *args, **kwargs):
            return original_open(base if path == "/opt/porota-trading" else path, *args, **kwargs)
        output = io.StringIO()
        with patch.object(sys, "argv", ["access", str(os.getuid()), str(os.getgid())]), \
             patch.dict(os.environ, {"SUDO_UID": str(os.getuid())}), \
             patch.object(os, "open", side_effect=opened), patch.object(os.path, "lexists", return_value=False), \
             patch.object(os, "read", side_effect=AssertionError("access repair must not read contents")), \
             redirect_stdout(output):
            exec(compile(setup.PREPARE_ACCESS, "bounded-access-repair", "exec"), {})
        self.assertEqual((base / "data").stat().st_mode & 0o777, 0o750)
        self.assertEqual(database.stat().st_ino, inode)
        self.assertEqual(database.read_bytes(), b"private-sentinel")
        self.assertEqual((base / ".secrets/unrelated").stat().st_mode & 0o777, 0o400)
        self.assertEqual((base / ".secrets/ppi_production.json").stat().st_mode & 0o777, 0o600)
        self.assertNotIn("private-sentinel", output.getvalue())


class LegacyTransitionTests(unittest.TestCase):
    """Fake rootful commands model only reviewed RC6 objects; no host access."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        self.root = Path("/opt/porota-trading")
        self.identity = host.Identity(os.getuid(), os.getgid(), "porotaadmin", self.source / "home")
        self.name = "porota-functional-health-rc6.timer"
        unit = self.source / "systemd" / self.name
        unit.parent.mkdir()
        unit.write_bytes(b"[Timer]\nOnBootSec=1min\n")
        policy = self.source / "ops/policy/host-control-plane-reconciliation-v2.json"
        policy.parent.mkdir(parents=True)
        policy.write_text(json.dumps({"units": {"systemd/" + self.name: {"role": "RETIRED_TIMER"}}}))
        self.digest = hashlib.sha256(unit.read_bytes()).hexdigest()
        self.units = [{"name": self.name, "policy_role": "RETIRED_TIMER", "LoadState": "loaded",
                       "ActiveState": "active", "UnitFileState": "enabled", "DropInPaths": "",
                       "FragmentPath": "/etc/systemd/system/" + self.name}]
        self.containers = {}
        for name, cid, command in ((setup.LEGACY_NAMES[0], "1" * 64, "bv_paper_runtime.py"),
                                   (setup.LEGACY_NAMES[1], "2" * 64, "o_dashboard.py")):
            self.containers[cid] = {"Id": cid, "Name": "/" + name, "Image": "sha256:" + "a" * 64,
                "Config": {"Cmd": [command], "Entrypoint": ["python"]},
                "Mounts": [{"Type": "bind", "Source": str(self.root / "data"), "Destination": "/app/data"}],
                "State": {"Running": True}, "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}}}
        self.commands = []

    def fake(self, command, **kwargs):
        self.commands.append(command)
        out = ""
        if setup.DOCKER in command:
            start = command.index("unix:///var/run/docker.sock") + 1
            args = command[start:]
            if args[0] == "info":
                out = json.dumps({"ID": "legacy-daemon"})
            elif args[:2] == ("container", "ls"):
                out = "\n".join(self.containers)
            elif args[:2] == ("container", "inspect"):
                out = json.dumps([self.containers[args[-1]]])
            elif args[:2] == ("container", "stop"):
                self.containers[args[-1]]["State"]["Running"] = False
            elif args[:2] == ("container", "update"):
                self.containers[args[-1]]["HostConfig"]["RestartPolicy"]["Name"] = "no"
            else:
                raise AssertionError(command)
        elif setup.SYSTEMCTL in command and "disable" in command:
            self.units[0].update(ActiveState="inactive", UnitFileState="disabled")
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, out, "")

    def patches(self):
        return (patch.object(setup, "unit_inventory", side_effect=lambda *args, **kwargs:
                    {"status": "OBSERVED", "units": self.units, "unmapped_units": []}),
                patch.object(setup.LegacyTransition, "unit_digest", return_value=self.digest))

    def test_functional_enabled_service_blocks_before_any_quiescence(self):
        self.units[0]["policy_role"] = "ACTIVE_TIMER"
        inventory, digest = self.patches()
        with inventory, digest, self.assertRaisesRegex(host.Hold, "LEGACY_FUNCTIONAL_UNIT_NEEDS_ROOTLESS_SUCCESSOR") as caught:
            setup.LegacyTransition(self.root, self.source, identity=self.identity, run=self.fake)
        self.assertEqual(caught.exception.public_details["functional_units_needing_successor"], [self.name])
        self.assertFalse(any("stop" in c or "disable" in c or "update" in c for c in self.commands))

    def test_quiescence_receipt_is_produced_by_exact_stops_and_zero_database_check(self):
        inventory, digest = self.patches()
        with inventory, digest, patch.object(host, "database_snapshot", return_value={"inode": 4, "real_orders_sent": 0}):
            transition = setup.LegacyTransition(self.root, self.source, identity=self.identity, run=self.fake)
            result = transition.quiesce()
        self.assertEqual(result["state"], "QUIESCENT")
        self.assertEqual({x["cid"] for x in result["containers"]}, set(self.containers))
        self.assertEqual({x["command_identity"]["profile"] for x in result["containers"]},
                         {"BV_PAPER_RUNTIME", "DASHBOARD"})
        self.assertEqual({x["cid"] for x in result["observed_containers"]}, set(self.containers))
        self.assertTrue(all(not x["State"]["Running"] for x in self.containers.values()))
        stopped = [c[-1] for c in self.commands if "stop" in c]
        self.assertEqual(set(stopped), set(self.containers))
        self.assertFalse(result["rootful_daemon_stopped"])
        self.assertFalse(any("rm" in c or "prune" in c or "docker.service" in c for c in self.commands))

    def test_wrong_role_scanner_shell_or_extra_arguments_block_before_any_mutation(self):
        invalid_commands = (
            (["python"], ["bf_production_paper_observer.py"]),
            (["python"], ["o_dashboard.py"]),
            (["python"], ["bv_paper_runtime.py", "--exit-reader"]),
            (["python", "-c"], ["private-unknown-argument"]),
            (["sh", "-c"], ["python bv_paper_runtime.py; private-unknown-argument"]),
            (None, ["python", "bv_paper_runtime.py"]),
            ([], ["python", "bf_production_paper_observer.py"]),
        )
        for entrypoint, command in invalid_commands:
            with self.subTest(entrypoint=entrypoint, command=command):
                self.commands.clear()
                self.containers["1" * 64]["Config"].update(Entrypoint=entrypoint, Cmd=command)
                with self.assertRaisesRegex(host.Hold, "LEGACY_COMMAND_IDENTITY") as caught:
                    setup.LegacyTransition(self.root, self.source, identity=self.identity, run=self.fake)
                evidence = caught.exception.public_details["legacy_command_identity"]
                self.assertEqual(evidence["cid"], "1" * 64)
                self.assertEqual(evidence["name"], setup.LEGACY_NAMES[0])
                self.assertEqual(evidence["command_identity"]["profile"], "UNRECOGNIZED")
                self.assertNotIn("cmd", evidence["command_identity"])
                self.assertNotIn("private-unknown-argument", json.dumps(evidence))
                self.assertFalse(any("stop" in c or "disable" in c or "update" in c for c in self.commands))

    def test_dashboard_cannot_use_observer_command(self):
        self.containers["2" * 64]["Config"]["Cmd"] = ["bv_paper_runtime.py"]
        with self.assertRaisesRegex(host.Hold, "LEGACY_COMMAND_IDENTITY"):
            setup.LegacyTransition(self.root, self.source, identity=self.identity, run=self.fake)
        self.assertFalse(any("stop" in c or "disable" in c or "update" in c for c in self.commands))

    def test_command_change_after_inspection_blocks_before_quiescence(self):
        inventory, digest = self.patches()
        with inventory, digest, patch.object(host, "database_snapshot", return_value={"inode": 4, "real_orders_sent": 0}):
            transition = setup.LegacyTransition(self.root, self.source, identity=self.identity, run=self.fake)
            self.containers["1" * 64]["Config"]["Cmd"] = ["bv_paper_runtime.py", "--exit-reader"]
            with self.assertRaisesRegex(host.Hold, "LEGACY_COMMAND_IDENTITY"):
                transition.quiesce()
        self.assertFalse(any("stop" in c or "disable" in c or "update" in c for c in self.commands))

    def test_named_container_with_foreign_storage_is_refused(self):
        self.containers["1" * 64]["Mounts"][0]["Source"] = "/opt/boca/data"
        with self.assertRaisesRegex(host.Hold, "LEGACY_DATA_IDENTITY"):
            setup.LegacyTransition(self.root, self.source, identity=self.identity, run=self.fake)
        self.assertFalse(any("stop" in c or "disable" in c or "update" in c for c in self.commands))

    def test_missing_rootful_permission_is_not_treated_as_empty_legacy(self):
        def denied(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, "", "private denied details")
        with self.assertRaisesRegex(host.Hold, "BOOTSTRAP_COMMAND_UNAVAILABLE"):
            setup.LegacyTransition(self.root, self.source, identity=self.identity, run=denied)


if __name__ == "__main__":
    unittest.main()
