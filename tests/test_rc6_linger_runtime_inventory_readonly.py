"""A1038: protected-main, operator-only logind inventory without mutation."""

import contextlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/rc6-r1-docker-capacity-readonly.yml'


def workflow_step_script(name):
    source = WORKFLOW.read_text()
    step = source.split('\n      - name: ' + name + '\n', 1)[1]
    step = step.split('\n      - name: ', 1)[0]
    raw = step.split('        run: |\n', 1)[1]
    return '\n'.join(line[10:] if line.startswith('          ') else line
                     for line in raw.splitlines()) + '\n'


def python_receiver(name):
    step = workflow_step_script(name)
    return step.split("python3 - <<'PY'\n", 1)[1].split('\nPY\n', 1)[0]


def run_receiver(name, filename, receipt):
    with tempfile.TemporaryDirectory(prefix='rc6-a1038-') as directory:
        subdirectory = Path(directory) / 'rc6_current_login_ro'
        subdirectory.mkdir(mode=0o700)
        (subdirectory / filename).write_text(receipt)
        env = {'RUNNER_TEMP': directory,
               'GITHUB_STEP_SUMMARY': str(Path(directory) / 'summary')}
        return subprocess.run([sys.executable, '-c', python_receiver(name)],
                              env=env, capture_output=True, text=True, timeout=3)


def module():
    name = 'scripts.rc6_linger_runtime_inventory_readonly'
    assert importlib.util.find_spec(name) is not None, 'A1038 bounded inventory missing'
    return importlib.import_module(name)


def entry(kind, mode, uid):
    return SimpleNamespace(st_mode=kind | mode, st_uid=uid)


class LingerInventoryReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.account = SimpleNamespace(pw_name='porotaadmin', pw_uid=1000,
                                       pw_gid=1000, pw_dir='/home/porotaadmin')
        self.paths = {
            '/run/user/1000': entry(stat.S_IFDIR, 0o700, 1000),
            '/run/user/1000/bus': entry(stat.S_IFSOCK, 0o660, 1000),
            '/usr': entry(stat.S_IFDIR, 0o755, 0),
            '/usr/bin': entry(stat.S_IFDIR, 0o755, 0),
            '/usr/bin/loginctl': entry(stat.S_IFREG, 0o755, 0),
            '/usr/bin/systemctl': entry(stat.S_IFREG, 0o755, 0),
        }
        self.responses = {
            'show-user': b'Linger=no\n',
            'list-unit-files': b'boca-private.service enabled enabled\n'
                               b'foreign-private.timer enabled-runtime -\n',
            'list-units': b'boca-private.service loaded active running PRIVATE DESCRIPTION\n'
                          b'foreign-private.timer loaded active waiting PRIVATE DESCRIPTION\n'
                          b'foreign-private.socket loaded inactive dead PRIVATE DESCRIPTION\n',
            'list-dependencies': b'default.target\nboca-private.service\n',
        }
        self.calls = []

    def inspect(self, *, uid=1000, name='porotaadmin'):
        probe = module()
        account = SimpleNamespace(pw_name=name, pw_uid=1000, pw_gid=1000,
                                  pw_dir='/home/' + name)

        def fake_lstat(path):
            return self.paths[str(path)]

        def fake_run(argv, *, env, timeout, max_bytes):
            self.calls.append(tuple(argv))
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 5)
            self.assertLessEqual(max_bytes, 16384)
            self.assertEqual(env['XDG_RUNTIME_DIR'], '/run/user/1000')
            self.assertEqual(env['DBUS_SESSION_BUS_ADDRESS'],
                             'unix:path=/run/user/1000/bus')
            self.assertNotIn('DOCKER_HOST', env)
            self.assertNotIn('DOCKER_CONTEXT', env)
            self.assertNotIn('SSH_KEY', env)
            for name, payload in self.responses.items():
                if name in argv:
                    return payload
            raise AssertionError('unreviewed command')

        with (mock.patch.object(probe.os, 'geteuid', return_value=uid),
              mock.patch.object(probe.pwd, 'getpwuid', return_value=account),
              mock.patch.object(probe.pwd, 'getpwall', return_value=[account]),
              mock.patch.object(probe.os, 'lstat', side_effect=fake_lstat),
              mock.patch.object(probe, '_run_bounded_command', side_effect=fake_run)):
            return probe.collect()

    def test_private_uid_and_bounded_queries_never_export_unit_names(self):
        result = self.inspect()
        self.assertEqual(result['schema'], 'rc6.r1.linger-runtime-inventory-readonly.v1')
        self.assertEqual(result['state'], 'NO_VERIFICADO')
        self.assertEqual(result['linger_effective'], 'NO')
        self.assertEqual(result['enabled_user_units'], 'PRESENT')
        self.assertEqual(result['active_user_services'], 'PRESENT')
        self.assertEqual(result['user_timers'], 'PRESENT')
        self.assertEqual(result['user_sockets'], 'PRESENT')
        self.assertEqual(result['default_target_dependencies'], 'PRESENT')
        self.assertEqual(result['autostart_risk'], 'PRESENT')
        self.assertEqual(result['systemd_dbus_state'], 'QUERIED_READONLY')
        self.assertEqual(result['mutations'], 0)
        self.assertEqual(result['docker_queries'], 0)
        self.assertEqual(result['docker_socket_connections'], 0)
        self.assertFalse(result['sudo_executed'])
        self.assertFalse(result['rootless_certified'])
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.calls[0][0], '/usr/bin/loginctl')
        self.assertEqual([call[0] for call in self.calls[1:]], ['/usr/bin/systemctl'] * 3)
        self.assertTrue(all('enable' not in call and 'start' not in call and
                            'restart' not in call for call in self.calls))
        serialized = json.dumps(result)
        for secret in ('boca-private', 'foreign-private', 'PRIVATE DESCRIPTION',
                       '/run/user/', '/home/', 'porotaadmin'):
            self.assertNotIn(secret, serialized)

    def test_empty_unit_lists_are_unknown_not_a_false_absence_certification(self):
        self.responses.update({'list-unit-files': b'', 'list-units': b'',
                               'list-dependencies': b'default.target\n'})
        result = self.inspect()
        self.assertEqual(result['enabled_user_units'], 'NONE_OBSERVED')
        self.assertEqual(result['active_user_services'], 'NONE_OBSERVED')
        self.assertEqual(result['user_timers'], 'NONE_OBSERVED')
        self.assertEqual(result['user_sockets'], 'NONE_OBSERVED')
        self.assertEqual(result['default_target_dependencies'], 'NONE_OBSERVED')
        self.assertEqual(result['autostart_risk'], 'UNVERIFIED')
        self.assertNotIn('ABSENT', json.dumps(result))

    def test_identity_or_untrusted_bus_denies_before_external_commands(self):
        probe = module()
        for uid, name in ((0, 'porotaadmin'), (1001, 'porotaadmin'),
                          (1000, 'other-app')):
            with self.subTest(uid=uid, name=name):
                self.calls.clear()
                with self.assertRaises(probe.InventoryHold):
                    self.inspect(uid=uid, name=name)
                self.assertEqual(self.calls, [])
        self.paths['/run/user/1000/bus'] = entry(stat.S_IFLNK, 0o777, 1000)
        with self.assertRaises(probe.InventoryHold):
            self.inspect()
        self.assertEqual(self.calls, [])

    def test_malformed_logind_or_units_fail_closed_without_raw_leak(self):
        probe = module()
        for name, raw in (('show-user', b'Linger=YES private-user\n'),
                          ('list-unit-files', b'PRIVATE_SECRET.service escaped-state\n'),
                          ('list-units', b'PRIVATE_SECRET unit format wrong\n'),
                          ('list-dependencies', b'PRIVATE_SECRET\n')):
            with self.subTest(command=name):
                saved = self.responses[name]
                self.responses[name] = raw
                try:
                    with self.assertRaises(probe.InventoryHold):
                        self.inspect()
                finally:
                    self.responses[name] = saved

        out = io.StringIO()
        with (mock.patch.object(probe, 'collect',
                                side_effect=probe.InventoryHold('SYSTEMD_OUTPUT_UNVERIFIED')),
              contextlib.redirect_stdout(out)):
            self.assertEqual(probe.main(['--linger-inventory']), 0)
        self.assertNotIn('PRIVATE_SECRET', out.getvalue())
        self.assertNotIn('porotaadmin', out.getvalue())
        self.assertIn('"state":"NO_VERIFICADO"', out.getvalue())

    def test_output_limit_and_deadline_prevent_systemd_unbounded_read(self):
        probe = module()
        start = time.monotonic()
        with self.assertRaises(probe.InventoryHold):
            probe._run_bounded_command(
                [sys.executable, '-c',
                 "import sys,time;sys.stdout.write('X'*400000);sys.stdout.flush();time.sleep(2)"],
                env={'PATH': '/usr/bin:/bin'}, timeout=0.5, max_bytes=100)
        self.assertLess(time.monotonic() - start, 1.5)


class LingerInventoryWorkflowTests(unittest.TestCase):
    def test_inventory_flag_is_exclusive_and_never_enters_rootful_job(self):
        source = WORKFLOW.read_text()
        self.assertIn('      linger_inventory_only:\n', source)
        host = source.split('\n  host_readonly:\n', 1)[1].split('\n  current_login_readonly:\n', 1)[0]
        self.assertIn('!inputs.current_login_capability_only', host)
        self.assertIn('!inputs.linger_inventory_only', host)
        login = source.split('\n  current_login_readonly:\n', 1)[1]
        self.assertIn('inputs.linger_inventory_only', login.split('    runs-on:', 1)[0])
        self.assertIn('Validate current-login trusted context', login)
        self.assertIn('actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683', login)
        inventory = login.split('      - name: Read bounded systemd user inventory\n', 1)[1]
        inventory = inventory.split('\n      - name: Runner-only current-login credential cleanup\n', 1)[0]
        self.assertIn('if: ${{ inputs.linger_inventory_only }}', inventory)
        self.assertIn('--linger-inventory', inventory)
        self.assertIn('StrictHostKeyChecking=yes', inventory)
        self.assertIn('2>/dev/null', inventory)
        for forbidden in ('sudo -n', 'docker info', 'docker ps', 'systemctl enable',
                          'systemctl start', 'systemctl restart'):
            self.assertNotIn(forbidden, inventory)

    def test_actual_inventory_receiver_rejects_duplicate_json_and_preserves_safe_output(self):
        fields = {'schema': 'rc6.r1.linger-runtime-inventory-readonly.v1',
                  'state': 'NO_VERIFICADO', 'read_only': True,
                  'linger_effective': 'NO', 'enabled_user_units': 'NONE_OBSERVED',
                  'active_user_services': 'NONE_OBSERVED',
                  'user_timers': 'NONE_OBSERVED', 'user_sockets': 'NONE_OBSERVED',
                  'default_target_dependencies': 'NONE_OBSERVED',
                  'autostart_risk': 'UNVERIFIED',
                  'systemd_dbus_state': 'QUERIED_READONLY',
                  'mutations': 0, 'docker_queries': 0,
                  'docker_socket_connections': 0, 'sudo_executed': False,
                  'rootless_certified': False}
        good = run_receiver('Read bounded systemd user inventory', 'linger.json',
                            json.dumps(fields))
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertIn('AUTOSTART_RISK=UNVERIFIED', good.stdout)
        raw = json.dumps(fields).replace('"state": "NO_VERIFICADO"',
                                         '"state": "PRESENT", "state": "NO_VERIFICADO"')
        evil = run_receiver('Read bounded systemd user inventory', 'linger.json', raw)
        self.assertNotEqual(evil.returncode, 0)
        self.assertIn('RECEIPT_INVALID', evil.stderr + evil.stdout)
        self.assertNotIn('PRESENT", "state"', evil.stderr + evil.stdout)

    def test_existing_current_login_receiver_also_rejects_duplicate_keys(self):
        fields = {'schema': 'rc6.r1.current-login-capability-readonly.v1',
                  'state': 'NO_VERIFICADO', 'read_only': True, 'mutations': 0,
                  'provider_gets': 0, 'docker_queries': 0,
                  'socket_connections': 0, 'sudo_executed': False,
                  'rootless_certified': False, 'reason': 'EXPECTED_UNVERIFIED'}
        good = run_receiver('Read current login OS metadata without Docker or sudo',
                            'receipt.json', json.dumps(fields))
        self.assertNotEqual(good.returncode, 0)
        self.assertIn('PREREQUISITES_UNVERIFIED', good.stderr + good.stdout)
        raw = json.dumps(fields).replace('"reason": "EXPECTED_UNVERIFIED"',
                      '"reason": "PRIVATE-LEAK", "reason": "EXPECTED_UNVERIFIED"')
        evil = run_receiver('Read current login OS metadata without Docker or sudo',
                            'receipt.json', raw)
        self.assertNotEqual(evil.returncode, 0)
        self.assertIn('RECEIPT_INVALID', evil.stderr + evil.stdout)
        self.assertNotIn('PRIVATE-LEAK', evil.stderr + evil.stdout)

    def test_both_runner_receivers_reject_non_json_nan_constants(self):
        for step in ('Read bounded systemd user inventory',
                     'Read current login OS metadata without Docker or sudo'):
            with self.subTest(step=step):
                self.assertIn('parse_constant=reject_non_json_constant',
                              python_receiver(step))

    def test_actual_receivers_fail_closed_on_nan_and_both_infinities(self):
        # Python's plain json.loads accepts these non-JSON tokens as floats.
        # A source-string assertion alone cannot prove the real runner rejects
        # the receipt without exposing host-controlled text to public logs.
        for raw_constant in ('NaN', 'Infinity', '-Infinity'):
            self.assertIsInstance(json.loads(raw_constant), float)
        inventory = {'schema': 'rc6.r1.linger-runtime-inventory-readonly.v1',
                     'state': 'NO_VERIFICADO', 'read_only': True,
                     'linger_effective': 'NO', 'enabled_user_units': 'NONE_OBSERVED',
                     'active_user_services': 'NONE_OBSERVED',
                     'user_timers': 'NONE_OBSERVED', 'user_sockets': 'NONE_OBSERVED',
                     'default_target_dependencies': 'NONE_OBSERVED',
                     'autostart_risk': 'UNVERIFIED',
                     'systemd_dbus_state': 'QUERIED_READONLY',
                     'mutations': 0, 'docker_queries': 0,
                     'docker_socket_connections': 0, 'sudo_executed': False,
                     'rootless_certified': False}
        login = {'schema': 'rc6.r1.current-login-capability-readonly.v1',
                 'state': 'NO_VERIFICADO', 'reason': 'EXPECTED_UNVERIFIED',
                 'read_only': True, 'mutations': 0, 'provider_gets': 0,
                 'docker_queries': 0, 'socket_connections': 0,
                 'sudo_executed': False, 'rootless_certified': False}
        for step, filename, receipt in (
                ('Read bounded systemd user inventory', 'linger.json', inventory),
                ('Read current login OS metadata without Docker or sudo',
                 'receipt.json', login)):
            valid = json.dumps(receipt)
            self.assertIn('"state": "NO_VERIFICADO"', valid)
            for raw_constant in ('NaN', 'Infinity', '-Infinity', '+Infinity'):
                with self.subTest(step=step, malicious_constant=raw_constant):
                    malicious = valid.replace('"state": "NO_VERIFICADO"',
                                               '"state": ' + raw_constant, 1)
                    process = run_receiver(step, filename, malicious)
                    self.assertNotEqual(process.returncode, 0)
                    output = process.stdout + process.stderr
                    self.assertIn('RECEIPT_INVALID|CERT2=NO_VERIFICADO', output)
                    self.assertNotIn('Traceback', output)
                    self.assertNotIn(malicious, output)

    def test_actual_trust_step_excludes_all_conflicting_linger_modes_pre_secrets(self):
        source = WORKFLOW.read_text()
        job = source.split('\n  current_login_readonly:\n', 1)[1]
        self.assertLess(job.index('      - name: Validate current-login trusted context'),
                        job.index('      - name: Checkout exact trusted workflow commit'))
        shell = workflow_step_script('Validate current-login trusted context')
        env_base = {'GITHUB_REPOSITORY': 'mbalbo2023/Porota-trading',
                    'GITHUB_EVENT_NAME': 'workflow_dispatch',
                    'GITHUB_REF': 'refs/heads/main',
                    'GITHUB_REF_PROTECTED': 'true',
                    'GITHUB_WORKFLOW_REF': 'mbalbo2023/Porota-trading/.github/workflows/rc6-r1-docker-capacity-readonly.yml@refs/heads/main',
                    'GITHUB_WORKFLOW_SHA': 'a' * 40,
                    'GITHUB_SHA': 'a' * 40,
                    'RUNNER_ENVIRONMENT': 'github-hosted'}
        from itertools import product
        for login, rootless, timers, deploy_sha in product((False, True), repeat=4):
            with self.subTest(login=login, rootless=rootless, timers=timers,
                              deploy_sha=deploy_sha):
                env = dict(env_base, RC6_LINGER_ONLY='true',
                           RC6_CURRENT_LOGIN_ONLY=str(login).lower(),
                           RC6_ROOTLESS_ONLY=str(rootless).lower(),
                           RC6_TIMER_MODE=str(timers).lower(),
                           RC6_DEPLOY_SHA='a'*40 if deploy_sha else '')
                process = subprocess.run(['bash', '-c', shell], env=env,
                                         capture_output=True, text=True, timeout=3)
                if any((login, rootless, timers, deploy_sha)):
                    self.assertEqual(process.returncode, 79)
                    self.assertIn('INPUT_MODE_CONFLICT', process.stdout)
                else:
                    self.assertEqual(process.returncode, 0, process.stderr)
        host = source.split('\n  host_readonly:\n', 1)[1].split(
            '\n  current_login_readonly:\n', 1)[0]
        self.assertIn('!inputs.current_login_capability_only && !inputs.linger_inventory_only',
                      host.split('    if:', 1)[1].split('\n', 1)[0])
        self.assertIn('if: ${{ inputs.current_login_capability_only }}',
                      job.split('      - name: Read current login OS metadata without Docker or sudo', 1)[1].split('      - name:', 1)[0])

    def test_inventory_ssh_and_credential_preflight_have_sanitized_holds(self):
        shell = workflow_step_script('Read bounded systemd user inventory')
        self.assertIn('CREDENTIAL_PREREQUISITES_UNVERIFIED', shell)
        self.assertIn('SSH_OR_REMOTE_UNVERIFIED', shell)
        self.assertIn('if ! ssh ', shell)
        self.assertIn('2>/dev/null', shell)
        with tempfile.TemporaryDirectory(prefix='rc6-a1038-shell-') as directory:
            base = {'DO_HOST': 'bad@invalid', 'DO_USER': 'porotaadmin',
                    'SSH_KEY': 'PRIVATE_TEST_KEY', 'KNOWN_HOSTS': 'PRIVATE_TEST_HOST',
                    'RUNNER_TEMP': directory, 'PATH': '/usr/bin:/bin'}
            denied = subprocess.run(['bash', '-c', shell], env=base,
                                    capture_output=True, text=True, timeout=3)
            self.assertEqual(denied.returncode, 79)
            self.assertIn('CREDENTIAL_PREREQUISITES_UNVERIFIED', denied.stdout)
            self.assertNotIn('PRIVATE_TEST', denied.stdout + denied.stderr)
            program = Path(directory) / 'bin'
            program.mkdir()
            ssh = program / 'ssh'
            ssh.write_text('#!/bin/sh\nexit 255\n')
            ssh.chmod(0o700)
            trusted = dict(base, DO_HOST='trusted.example',
                           PATH=str(program) + ':/usr/bin:/bin')
            failed = subprocess.run(['bash', '-c', shell], env=trusted,
                                    capture_output=True, text=True, timeout=3)
            self.assertEqual(failed.returncode, 79)
            self.assertIn('SSH_OR_REMOTE_UNVERIFIED', failed.stdout)
            self.assertNotIn('PRIVATE_TEST', failed.stdout + failed.stderr)


if __name__ == '__main__':
    unittest.main()
