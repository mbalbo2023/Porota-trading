"""OFFHOST: absent independent physical custody must never reach the host."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/rc6-r1-docker-capacity-readonly.yml'


def host_steps():
    return WORKFLOW.read_text().split('\n  host_readonly:', 1)[1].split('\n  current_login_readonly:', 1)[0]


def first_shell():
    steps = host_steps().split('\n    steps:', 1)[1]
    return textwrap.dedent(steps.split('        run: |\n', 1)[1].split('\n      - ', 1)[0])


class PorotaOnlyHoldContract(unittest.TestCase):
    def test_untrusted_receipts_never_reach_privileged_or_remote_commands(self):
        for attack in ('missing', 'forged', 'stale', 'symlink', 'private-error'):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                receipt = root / 'untrusted.json'
                if attack == 'symlink':
                    receipt.symlink_to(root / 'absent')
                elif attack != 'missing':
                    receipt.write_text('{"reason":"PRIVATE_SENTINEL"}')
                env = os.environ | {'RUNNER_TEMP': tmp, 'GITHUB_STEP_SUMMARY': str(root / 'summary'),
                    'ROOTLESS_METADATA_ONLY': 'false', 'EXPECTED_DEPLOY_SHA': 'a' * 40,
                    'RC6_OWNER_RECEIPT': str(receipt), 'DO_HOST': 'PRIVATE_SENTINEL',
                    'DO_USER': 'fake', 'SSH_KEY': 'PRIVATE_SENTINEL', 'KNOWN_HOSTS': 'PRIVATE_SENTINEL'}
                spies = 'ssh() { echo FORBIDDEN_CALL >&2; return 99; }; sudo() { echo FORBIDDEN_CALL >&2; return 99; }; docker() { echo FORBIDDEN_CALL >&2; return 99; };\n'
                result = subprocess.run(['bash', '-c', spies + first_shell()], env=env,
                    text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                output = result.stdout + result.stderr + (root / 'summary').read_text()
                self.assertIn('POROTA_ONLY_PARTIAL', output)
                self.assertIn('OWNER_BINDING_UNVERIFIED', output)
                self.assertNotIn('FORBIDDEN_CALL', output)
                self.assertNotIn('PRIVATE_SENTINEL', output)
                self.assertNotIn('GREEN', output)
                self.assertFalse((root / 'rc6_r1_capacity' / 'key').exists())
                self.assertEqual(json.loads(result.stdout), {
                    'schema': 'rc6.r1.docker-capacity-readonly.v2',
                    'scope': 'POROTA_ONLY_PARTIAL', 'state': 'NO_VERIFICADO',
                    'reason': 'OWNER_BINDING_UNVERIFIED', 'mutations': 0,
                    'provider_gets': 0, 'docker_queries': 0,
                    'global_capacity': 'NO_VERIFICADO',
                    'cleanup_eligibility': 'NO_VERIFICADO'})

    def test_rootless_mode_passes_gate_without_staging_credentials(self):
        result = subprocess.run(['bash', '-c', first_shell()], env=os.environ |
            {'ROOTLESS_METADATA_ONLY': 'true'}, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout + result.stderr, '')

    def test_other_modes_and_global_mutex_preserved(self):
        source = WORKFLOW.read_text()
        current = '  current_login_readonly:' + source.split('\n  current_login_readonly:', 1)[1]
        self.assertEqual(hashlib.sha256(current.encode()).hexdigest(),
            '2c559268756b9476d8d5642fb87ff5f18d50ad98b3b4402ba0afe782452d6c99')
        self.assertIn('group: rc6-unified-paper-deploy', host_steps())
        self.assertIn('cancel-in-progress: false', host_steps())
        self.assertIn('timeout-minutes: 4', host_steps())
        self.assertIn("github.ref == 'refs/heads/main'", host_steps())
        for forbidden in ("docker('ps'", "docker('image'", "docker('system'",
                          'docker-capacity-readonly.v1'):
            self.assertNotIn(forbidden, host_steps())
        timer = source.split('      - name: Inspect RC6 timer contract READ_ONLY', 1)[1].split(
            '      - name: Runner-only SSH cleanup', 1)[0]
        self.assertEqual(hashlib.sha256(timer.encode()).hexdigest(),
            'f360976816a5458653933fc05d8ed5b0f4ee8d30fd919b3c64be4083b3e43e4c')


if __name__ == '__main__':
    unittest.main()
