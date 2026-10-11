"""Guard the critical gateway against dashboard health and PID 1 zombie leaks."""

import re
import shlex
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "ops" / "rc6_deploy_critical_control_plane.sh"


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


if __name__ == "__main__":
    unittest.main()
