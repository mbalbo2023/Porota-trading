import json
import subprocess
import tempfile
import unittest
from pathlib import Path

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
            raw = [{"Id": "a" * 64, "Name": "/" + name,
                    "Config": {"Env": ["SECRET=must-never-leak"]},
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


if __name__ == "__main__":
    unittest.main()
