"""Guard the critical gateway against dashboard health and PID 1 zombie leaks."""

import re
import shlex
import importlib.util
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "ops" / "rc6_deploy_critical_control_plane.sh"
REPAIR = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "rc6_sre_critical_repair.py"


class CriticalHealthContract(unittest.TestCase):
    def test_gateway_uses_init_and_issues_only_health(self):
        source = SOURCE.read_text(encoding="utf-8")
        line = next(
            line.partition("ExecStart=")[2]
            for line in source.splitlines()
            if line.startswith("ExecStart=/usr/bin/docker run ")
        )
        args = shlex.split(line)
        self.assertEqual(args.count("--init"), 1)
        self.assertIn("--pids-limit", args)
        self.assertEqual(args[args.index("--pids-limit") + 1], "64")
        health = [arg.partition("=")[2] for arg in args if arg.startswith("--health-cmd=")]
        self.assertEqual(len(health), 1)
        self.assertNotIn("DASHBOARD_PORT", health[0])
        self.assertNotIn("http://", health[0])
        command = shlex.split(health[0])
        self.assertEqual(command[:2], ["python", "-c"])
        compile(command[2], "<critical-health>", "exec")
        self.assertIn("UnixGithubIssuesClient().health()", command[2])
        self.assertIn('h.get("capability")=="issues_only"', command[2])
        self.assertIn("--health-interval=30s", args)
        self.assertIn("--health-timeout=20s", args)
        self.assertIn("CRITICAL_HEALTH_AND_INIT=GREEN", source)

    def test_host_patch_is_exact_and_rejects_reapply(self):
        spec = importlib.util.spec_from_file_location("sre_critical_repair", REPAIR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        raw = (b"ExecStart=/usr/bin/docker run --pull never --read-only "
               b"--user botuser --cap-drop ALL --pids-limit 64 --memory 192m "
               b"-e POROTA_RUNTIME_MODE=PRODUCTION_PAPER image cmd\n")
        modified = module.patch_unit(raw)
        self.assertEqual(modified.count(b"--init"), 1)
        self.assertEqual(modified.count(b"--health-cmd"), 1)
        argv = shlex.split(modified.decode().split("=", 1)[1])
        command = next(a.split("=", 1)[1] for a in argv if a.startswith("--health-cmd="))
        compile(shlex.split(command)[2], "<critical-health>", "exec")
        with self.assertRaisesRegex(RuntimeError, "UNIT_SOURCE_MISMATCH"):
            module.patch_unit(modified)
        with self.assertRaisesRegex(RuntimeError, "UNIT_EXECSTART_COUNT"):
            module.patch_unit(raw + raw)


if __name__ == "__main__":
    unittest.main()
