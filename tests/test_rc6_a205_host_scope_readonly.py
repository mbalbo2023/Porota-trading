"""A205 physical-scope probe must be bounded, honest and non-mutating.

This is an OFFHOST source test; a GREEN probe is NOT host/deploy approval.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import yaml


WORKFLOW = Path(__file__).parents[1] / '.github' / 'workflows' / 'rc6-a205-paper-book-readonly.yml'
EXPECTED_FIELDS = {
    'contract', 'read_only', 'host_mutations', 'ssh_uid_nonroot',
    'tmp_root_sticky', 'do_user_docker_read', 'docker_daemon_owner_proven',
    'exact_stage', 'same_uid_stage', 'root_attested_stage',
    'docker_mutation_authorized',
}


class A205ReadOnlyHostScopeTests(unittest.TestCase):
    @staticmethod
    def steps():
        return yaml.safe_load(WORKFLOW.read_text())['jobs']['paper-preflight']['steps']

    @classmethod
    def probe(cls):
        matches = [(idx, x) for idx, x in enumerate(cls.steps())
                   if x.get('id') == 'a205_host_scope_ro']
        if len(matches) != 1:
            raise AssertionError('Exactly one existing-workflow A205 host-scope probe required')
        return matches[0]

    @classmethod
    def remote_source(cls):
        _, step = cls.probe()
        run = step['run']
        return run.split("<<'A205_HOST_SCOPE_PY'\n", 1)[1].split(
            '\nA205_HOST_SCOPE_PY', 1)[0]

    def test_probes_after_pin_and_before_forbidden_sudo_c(self):
        steps = self.steps()
        probe_idx, _ = self.probe()
        pin_idx = next(i for i, s in enumerate(steps)
                       if 'pin-checked read-only connection' in s.get('name', ''))
        sudo_idx = next(i for i, s in enumerate(steps)
                        if 'sudo inherited FD capability' in s.get('name', ''))
        self.assertLess(pin_idx, probe_idx)
        self.assertLess(probe_idx, sudo_idx)
        self.assertIn('RC6_A205_HOST_SCOPE_RO=', steps[probe_idx]['run'])

    def test_probe_script_has_no_write_or_unsafe_docker_commands(self):
        _, step = self.probe()
        script = self.remote_source()
        self.assertIn('os.lstat(', script)
        self.assertIn('subprocess.run(', script)
        self.assertNotIn('sudo', script, 'Probe must assume DO_USER has no root privileges')
        self.assertNotRegex(script, r'\b(unlink|rmtree|mkdir|chmod|chown|rename|replace|open)\s*\(')
        self.assertNotRegex(script, r'\b(systemctl|docker\s+(rm|rmi|prune|build|run|tag|pull|start|stop)|sudo\s+-C)\b')
        self.assertNotIn('print(os.environ', script)
        self.assertNotIn('print(result.stdout', script)
        self.assertNotIn('print(result.stderr', script)

    def test_local_mock_denial_is_unknown_and_never_prints_private_identifiers(self):
        source = self.remote_source()
        with tempfile.TemporaryDirectory(prefix='rc6-a205-probe-') as temp:
            fake_bin = Path(temp) / 'bin'
            fake_bin.mkdir()
            for name in ('docker',):
                executable = fake_bin / name
                executable.write_text('#!/bin/sh\nexit 1\n')
                executable.chmod(0o755)
            env = os.environ.copy()
            env.update({
                'PATH': f'{fake_bin}:{os.defpath}',
                'RC6_A205_PROBE_SHA': 'f' * 40,
                'RC6_A205_PROBE_RUN': '9988776655',
                'RC6_A205_PROBE_ATTEMPT': '2',
            })
            result = subprocess.run([sys.executable, '-B', '-c', source], env=env,
                                    capture_output=True, text=True, check=False, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipts = result.stdout.strip().splitlines()
        self.assertEqual(len(receipts), 1, receipts)
        data = json.loads(receipts[0])
        self.assertEqual(set(data), EXPECTED_FIELDS)
        self.assertEqual(data['contract'], 'RC6_A205_HOST_SCOPE_READONLY_V1')
        self.assertIs(data['read_only'], True)
        self.assertEqual(data['host_mutations'], 0)
        self.assertIs(data['docker_mutation_authorized'], False)
        self.assertEqual(data['do_user_docker_read'], 'UNKNOWN')
        self.assertEqual(data['docker_daemon_owner_proven'], 'UNKNOWN')
        self.assertEqual(data['root_attested_stage'], 'UNKNOWN')
        self.assertNotIn('9988776655', result.stdout)
        self.assertNotIn('ffffffff', result.stdout)
        self.assertIsNone(re.search(r'\b[0-9a-f]{40}\b', result.stdout))

    def test_remote_proof_is_scoped_to_exact_sha_run_attempt_not_wildcard(self):
        script = self.remote_source()
        self.assertIn('RC6_A205_PROBE_SHA', script)
        self.assertIn('RC6_A205_PROBE_RUN', script)
        self.assertIn('RC6_A205_PROBE_ATTEMPT', script)
        self.assertIn('porota-deploy-v2-', script)
        self.assertNotIn('glob(', script)
        self.assertNotIn('os.listdir(', script)


if __name__ == '__main__':
    unittest.main()
