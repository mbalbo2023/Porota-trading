"""A1038: protected-main, operator-only logind inventory without mutation."""

import contextlib
import errno
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


def entry(kind, mode, uid, *, inode=1, device=1):
    return SimpleNamespace(st_mode=kind | mode, st_uid=uid,
                           st_ino=inode, st_dev=device)


class LingerInventoryReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.account = SimpleNamespace(pw_name='porotaadmin', pw_uid=1000,
                                       pw_gid=1000, pw_dir='/home/porotaadmin')
        self.paths = {
            '/run': entry(stat.S_IFDIR, 0o755, 0),
            '/run/user': entry(stat.S_IFDIR, 0o755, 0),
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
        self.after_logind = None

    def inspect(self, *, uid=1000, name='porotaadmin', through_main=False):
        probe = module()
        account = SimpleNamespace(pw_name=name, pw_uid=1000, pw_gid=1000,
                                  pw_dir='/home/' + name)

        def fake_lstat(path):
            item = self.paths[str(path)]
            if isinstance(item, OSError):
                raise item
            return item

        def fake_run(argv, *, env, timeout, max_bytes):
            self.calls.append(tuple(argv))
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 5)
            self.assertLessEqual(max_bytes, 16384)
            if argv[0] == '/usr/bin/loginctl':
                self.assertNotIn('XDG_RUNTIME_DIR', env)
                self.assertNotIn('DBUS_SESSION_BUS_ADDRESS', env)
            else:
                self.assertEqual(env['XDG_RUNTIME_DIR'], '/run/user/1000')
                self.assertEqual(env['DBUS_SESSION_BUS_ADDRESS'],
                                 'unix:path=/run/user/1000/bus')
            self.assertNotIn('DOCKER_HOST', env)
            self.assertNotIn('DOCKER_CONTEXT', env)
            self.assertNotIn('SSH_KEY', env)
            for name, payload in self.responses.items():
                if name in argv:
                    if isinstance(payload, Exception):
                        raise payload
                    if callable(payload):
                        return payload(argv, env=env, timeout=timeout,
                                       max_bytes=max_bytes)
                    if name == 'show-user' and self.after_logind is not None:
                        self.after_logind(self.paths)
                    return payload
            raise AssertionError('unreviewed command')

        with (mock.patch.object(probe.os, 'geteuid', return_value=uid),
              mock.patch.object(probe.pwd, 'getpwuid', return_value=account),
              mock.patch.object(probe.pwd, 'getpwall', return_value=[account]),
              mock.patch.object(probe.os, 'lstat', side_effect=fake_lstat),
              mock.patch.object(probe, '_run_bounded_command', side_effect=fake_run)):
            if through_main:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    exit_code = probe.main(['--linger-inventory'])
                return exit_code, output.getvalue()
            return probe.collect()

    def test_private_uid_and_bounded_queries_never_export_unit_names(self):
        result = self.inspect()
        self.assertEqual(result['schema'], 'rc6.r1.linger-runtime-inventory-readonly.v2')
        self.assertEqual(result['probe_extent'], 'FULL')
        self.assertEqual(result['user_runtime_status'], 'METADATA_VALID')
        self.assertEqual(result['user_bus_status'], 'METADATA_VALID')
        self.assertEqual(result['bus_mode_other_bits'], 'ABSENT')
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
        self.assertNotIn('ABSENT', [result[field] for field in (
            'enabled_user_units', 'active_user_services', 'user_timers',
            'user_sockets', 'default_target_dependencies', 'autostart_risk')])

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
        with self.assertRaises(probe.InventoryHold) as caught:
            self.inspect()
        self.assertEqual(caught.exception.user_bus_status, 'SYMLINK')
        self.assertEqual(caught.exception.linger, 'NO')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], '/usr/bin/loginctl')

    def test_bus_mode_other_bits_allowed_only_under_private_verified_runtime(self):
        # Root-owned parents and a true 0700 UID1000 runtime directory make
        # 0666 AF_UNIX metadata acceptable for read-only systemd user reads.
        # It does not authenticate the endpoint or certify rootless Docker.
        self.paths['/run/user/1000/bus'] = entry(stat.S_IFSOCK, 0o666, 1000)
        result = self.inspect()
        self.assertEqual(result['user_bus_status'], 'METADATA_VALID')
        self.assertEqual(result['bus_mode_other_bits'], 'PRESENT')
        self.assertEqual(len(self.calls), 4)

        self.calls.clear()
        self.paths['/run/user/1000'] = entry(stat.S_IFDIR, 0o755, 1000)
        with self.assertRaises(module().InventoryHold) as caught:
            self.inspect()
        self.assertEqual(caught.exception.user_runtime_status, 'UNSAFE_MODE')
        self.assertEqual(caught.exception.user_bus_status, 'NOT_CHECKED')
        self.assertEqual([call[0] for call in self.calls], ['/usr/bin/loginctl'])

    def test_runtime_bus_independent_fixed_categories_and_linger_partial(self):
        probe = module()
        runtime = '/run/user/1000'
        bus = '/run/user/1000/bus'
        cases = (
            (runtime, OSError(errno.ENOENT, 'secret'), 'MISSING', 'NOT_CHECKED'),
            (runtime, OSError(errno.EACCES, 'secret'), 'ACCESS_DENIED', 'NOT_CHECKED'),
            (runtime, OSError(errno.EIO, 'secret'), 'UNVERIFIED', 'NOT_CHECKED'),
            (runtime, entry(stat.S_IFLNK, 0o777, 1000), 'SYMLINK', 'NOT_CHECKED'),
            (runtime, entry(stat.S_IFREG, 0o700, 1000), 'WRONG_TYPE', 'NOT_CHECKED'),
            (runtime, entry(stat.S_IFDIR, 0o700, 1001), 'WRONG_OWNER', 'NOT_CHECKED'),
            (runtime, entry(stat.S_IFDIR, 0o755, 1000), 'UNSAFE_MODE', 'NOT_CHECKED'),
            (bus, OSError(errno.ENOENT, 'secret'), 'METADATA_VALID', 'MISSING'),
            (bus, OSError(errno.EACCES, 'secret'), 'METADATA_VALID', 'ACCESS_DENIED'),
            (bus, OSError(errno.EIO, 'secret'), 'METADATA_VALID', 'UNVERIFIED'),
            (bus, entry(stat.S_IFLNK, 0o777, 1000), 'METADATA_VALID', 'SYMLINK'),
            (bus, entry(stat.S_IFREG, 0o600, 1000), 'METADATA_VALID', 'WRONG_TYPE'),
            (bus, entry(stat.S_IFSOCK, 0o660, 1001), 'METADATA_VALID', 'WRONG_OWNER'),
            (bus, entry(stat.S_IFSOCK, 0o000, 1000), 'METADATA_VALID', 'UNSAFE_MODE'),
        )
        for path, item, runtime_status, bus_status in cases:
            with self.subTest(path='runtime' if path == runtime else 'bus',
                              status=runtime_status + '/' + bus_status):
                previous = self.paths[path]
                self.paths[path] = item
                self.calls.clear()
                self.responses['show-user'] = b'Linger=yes\n'
                try:
                    with self.assertRaises(probe.InventoryHold) as caught:
                        self.inspect()
                finally:
                    self.paths[path] = previous
                hold = caught.exception
                self.assertEqual(hold.user_runtime_status, runtime_status)
                self.assertEqual(hold.user_bus_status, bus_status)
                self.assertEqual(hold.linger, 'YES')
                self.assertEqual(hold.phase, 'PREFLIGHT')
                self.assertEqual([call[0] for call in self.calls], ['/usr/bin/loginctl'])
        self.responses['show-user'] = b'Linger=no\n'

    def test_untrusted_ancestor_cannot_be_ambiguous_missing_receipt(self):
        self.paths['/run/user'] = entry(stat.S_IFLNK, 0o777, 0)
        with self.assertRaises(module().InventoryHold) as caught:
            self.inspect()
        self.assertNotEqual(caught.exception.user_runtime_status, 'MISSING')
        self.assertEqual(caught.exception.linger, 'UNVERIFIED')
        self.assertEqual(self.calls, [])

    def test_world_writable_ancestor_and_untrusted_loginctl_never_query(self):
        probe = module()
        for path, untrusted in (
                ('/run/user', entry(stat.S_IFDIR, 0o777, 0)),
                ('/usr/bin/loginctl', entry(stat.S_IFREG, 0o777, 0)),
                ('/usr/bin/loginctl', entry(stat.S_IFLNK, 0o777, 0))):
            with self.subTest(path=path, kind=untrusted.st_mode):
                previous = self.paths[path]
                self.paths[path] = untrusted
                self.calls.clear()
                try:
                    with self.assertRaises(probe.InventoryHold):
                        self.inspect()
                finally:
                    self.paths[path] = previous
                self.assertEqual(self.calls, [])

    def test_unit_query_failure_preserves_known_linger_in_partial_hold(self):
        probe = module()
        for cmd in ('list-unit-files', 'list-units', 'list-dependencies'):
            with self.subTest(cmd=cmd):
                previous = self.responses[cmd]
                self.responses[cmd] = probe.InventoryHold('QUERY_FAILED')
                try:
                    with self.assertRaises(probe.InventoryHold) as caught:
                        self.inspect()
                finally:
                    self.responses[cmd] = previous
                self.assertEqual(caught.exception.linger, 'NO')
                self.assertEqual(caught.exception.user_bus_status, 'METADATA_VALID')

    def test_invalid_user_bus_never_invokes_systemctl_even_if_logind_fails(self):
        probe = module()
        self.paths['/run/user/1000/bus'] = OSError(errno.ENOENT, 'PRIVATE_PATH')
        self.responses['show-user'] = probe.InventoryHold('QUERY_TIMEOUT')
        with self.assertRaises(probe.InventoryHold) as caught:
            self.inspect()
        self.assertEqual(caught.exception.user_bus_status, 'MISSING')
        self.assertEqual(caught.exception.linger, 'UNVERIFIED')
        self.assertEqual(caught.exception.logind_status, 'QUERY_UNVERIFIED')
        self.assertEqual([call[0] for call in self.calls], ['/usr/bin/loginctl'])
        receipt = probe._partial_receipt(caught.exception)
        self.assertEqual(receipt['probe_extent'], 'PARTIAL')
        self.assertEqual(receipt['enabled_user_units'], 'NOT_QUERIED')
        self.assertEqual(receipt['systemd_dbus_state'], 'NOT_QUERIED')
        self.assertEqual(receipt['autostart_risk'], 'UNVERIFIED')
        self.assertEqual(receipt['rootless_certified'], False)
        self.assertNotIn('PRIVATE_PATH', json.dumps(receipt))

    def test_query_failure_partial_retains_logind_but_no_unit_certification(self):
        probe = module()
        self.responses['list-units'] = probe.InventoryHold('QUERY_FAILED')
        with self.assertRaises(probe.InventoryHold) as caught:
            self.inspect()
        receipt = probe._partial_receipt(caught.exception)
        self.assertEqual(receipt['linger_effective'], 'NO')
        self.assertEqual(receipt['logind_status'], 'PARSED_READONLY')
        self.assertEqual(receipt['user_bus_status'], 'METADATA_VALID')
        self.assertEqual(receipt['systemd_dbus_state'], 'INCOMPLETE_READONLY')
        self.assertEqual({receipt[key] for key in probe.UNIT_FIELDS}, {'UNVERIFIED'})
        self.assertEqual(receipt['autostart_risk'], 'UNVERIFIED')

    def test_malformed_logind_on_invalid_bus_preserves_independent_cause(self):
        probe = module()
        self.paths['/run/user/1000/bus'] = entry(stat.S_IFLNK, 0o777, 1000)
        self.responses['show-user'] = b'Linger=NO PRIVATE_NAME\n'
        with self.assertRaises(probe.InventoryHold) as caught:
            self.inspect()
        receipt = probe._partial_receipt(caught.exception)
        self.assertEqual(receipt['reason'], 'USER_BUS_UNVERIFIED')
        self.assertEqual(receipt['user_bus_status'], 'SYMLINK')
        self.assertEqual(receipt['logind_status'], 'PARSE_UNVERIFIED')
        self.assertEqual(receipt['linger_effective'], 'UNVERIFIED')
        self.assertNotIn('PRIVATE_NAME', json.dumps(receipt))

    def test_user_bus_is_rechecked_after_logind_and_before_each_user_query(self):
        probe = module()
        runtime = '/run/user/1000'
        bus = runtime + '/bus'
        cases = (
            (bus, entry(stat.S_IFLNK, 0o777, 1000),
             'METADATA_VALID', 'SYMLINK'),
            (bus, entry(stat.S_IFSOCK, 0o660, 1000, inode=23),
             'METADATA_VALID', 'UNVERIFIED'),
            (runtime, entry(stat.S_IFDIR, 0o755, 1000),
             'UNSAFE_MODE', 'NOT_CHECKED'),
            ('/run/user', entry(stat.S_IFLNK, 0o777, 0),
             'UNVERIFIED', 'NOT_CHECKED'),
        )
        for path, modified, runtime_status, bus_status in cases:
            with self.subTest(path=path, status=runtime_status+'/'+bus_status):
                old = self.paths[path]
                self.calls.clear()
                self.after_logind = lambda paths, path=path, modified=modified: paths.__setitem__(path, modified)
                self.responses['show-user'] = b'Linger=yes\n'
                try:
                    with self.assertRaises(probe.InventoryHold) as caught:
                        self.inspect()
                finally:
                    self.paths[path] = old
                    self.after_logind = None
                self.assertEqual(caught.exception.user_runtime_status, runtime_status)
                self.assertEqual(caught.exception.user_bus_status, bus_status)
                self.assertEqual(caught.exception.linger, 'YES')
                self.assertEqual([call[0] for call in self.calls], ['/usr/bin/loginctl'])

    def test_bus_changed_between_user_queries_blocks_remaining_calls(self):
        probe = module()
        original = self.paths['/run/user/1000/bus']
        after_first = self.responses['list-unit-files']
        # Simulate the endpoint being replaced just as the first bounded
        # `systemctl --user` call finishes. No second user query is allowed.
        def swap_after_first(*args, **kwargs):
            self.paths['/run/user/1000/bus'] = entry(stat.S_IFLNK, 0o777, 1000)
            return after_first
        self.responses['list-unit-files'] = swap_after_first
        try:
            with self.assertRaises(probe.InventoryHold) as caught:
                self.inspect()
        finally:
            self.responses['list-unit-files'] = after_first
            self.paths['/run/user/1000/bus'] = original
        self.assertEqual([call[0] for call in self.calls],
                         ['/usr/bin/loginctl', '/usr/bin/systemctl'])
        self.assertEqual(caught.exception.phase, 'ACTIVE_UNITS_QUERY')
        self.assertEqual(str(caught.exception), 'USER_BUS_UNVERIFIED')
        self.assertEqual(caught.exception.systemd_dbus_state, 'INCOMPLETE_READONLY')
        self.assertEqual(caught.exception.linger, 'NO')

        # Exercise the real remote serializer and the actual runner receiver,
        # not just a constructed JSON fixture or a source-text assertion.
        out = io.StringIO()
        with (mock.patch.object(probe, 'collect', side_effect=caught.exception),
              contextlib.redirect_stdout(out)):
            self.assertEqual(probe.main(['--linger-inventory']), 0)
        receipt = json.loads(out.getvalue())
        self.assertEqual(receipt['probe_extent'], 'PARTIAL')
        self.assertEqual(receipt['systemd_dbus_state'], 'INCOMPLETE_READONLY')
        self.assertEqual({receipt[key] for key in probe.UNIT_FIELDS}, {'UNVERIFIED'})
        self.assertEqual(receipt['linger_effective'], 'NO')
        self.assertNotIn('/run/user/1000', out.getvalue())
        result = run_receiver('Read bounded systemd user inventory',
                              'linger.json', out.getvalue())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PHASE=ACTIVE_UNITS_QUERY|REASON=USER_BUS_UNVERIFIED',
                      result.stdout + result.stderr)
        self.assertIn('UNIT_INVENTORY=UNVERIFIED', result.stdout + result.stderr)
        self.assertIn('LINGER_EFFECTIVE=NO', result.stdout + result.stderr)
        self.assertIn('CERT2=NO_VERIFICADO', result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stdout + result.stderr)
        for forged in (
                dict(receipt, systemd_dbus_state='NOT_QUERIED',
                     **{key: 'NOT_QUERIED' for key in probe.UNIT_FIELDS}),
                dict(receipt, phase='PREFLIGHT'),
                dict(receipt, phase='UNIT_FILES_QUERY'),
                dict(receipt, user_bus_status='METADATA_VALID',
                     bus_mode_other_bits='ABSENT')):
            with self.subTest(forged=forged['phase']):
                rejected = run_receiver('Read bounded systemd user inventory',
                                        'linger.json', json.dumps(forged))
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn('RECEIPT_INVALID', rejected.stdout + rejected.stderr)
                self.assertNotIn('Traceback', rejected.stdout + rejected.stderr)

    def test_bus_drift_after_second_user_query_is_not_reported_as_not_queried(self):
        probe = module()
        old_bus = self.paths['/run/user/1000/bus']
        second = self.responses['list-units']
        def drift_after_second(*args, **kwargs):
            self.paths['/run/user/1000/bus'] = entry(stat.S_IFSOCK, 0o660, 1000, inode=44)
            return second
        self.responses['list-units'] = drift_after_second
        try:
            with self.assertRaises(probe.InventoryHold) as caught:
                self.inspect()
        finally:
            self.responses['list-units'] = second
            self.paths['/run/user/1000/bus'] = old_bus
        self.assertEqual(caught.exception.phase, 'TARGET_DEPS_QUERY')
        self.assertEqual(caught.exception.user_bus_status, 'UNVERIFIED')
        self.assertEqual(caught.exception.systemd_dbus_state, 'INCOMPLETE_READONLY')
        self.assertEqual(len(self.calls), 3)  # loginctl + two systemctl reads
        out = io.StringIO()
        with (mock.patch.object(probe, 'collect', side_effect=caught.exception),
              contextlib.redirect_stdout(out)):
            self.assertEqual(probe.main(['--linger-inventory']), 0)
        receipt = json.loads(out.getvalue())
        self.assertEqual({receipt[key] for key in probe.UNIT_FIELDS}, {'UNVERIFIED'})
        self.assertEqual(receipt['systemd_dbus_state'], 'INCOMPLETE_READONLY')
        remote = run_receiver('Read bounded systemd user inventory',
                              'linger.json', out.getvalue())
        self.assertNotEqual(remote.returncode, 0)
        self.assertIn('PHASE=TARGET_DEPS_QUERY|REASON=USER_BUS_UNVERIFIED',
                      remote.stdout + remote.stderr)
        self.assertIn('UNIT_INVENTORY=UNVERIFIED', remote.stdout + remote.stderr)
        self.assertIn('CERT2=NO_VERIFICADO', remote.stdout + remote.stderr)

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
                    with self.assertRaises(probe.InventoryHold) as caught:
                        self.inspect()
                    expected_phase = {
                        'show-user': 'LOGIND_PARSE',
                        'list-unit-files': 'UNIT_FILES_PARSE',
                        'list-units': 'ACTIVE_UNITS_PARSE',
                        'list-dependencies': 'TARGET_DEPS_PARSE'}[name]
                    self.assertEqual(caught.exception.phase, expected_phase)
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
        self.assertIn('"phase":"PREFLIGHT"', out.getvalue())

        out = io.StringIO()
        with (mock.patch.object(probe, 'collect',
                                side_effect=RuntimeError('PRIVATE_SECRET')),
              contextlib.redirect_stdout(out)):
            self.assertEqual(probe.main(['--linger-inventory']), 0)
        self.assertIn('"reason":"INVENTORY_UNVERIFIED"', out.getvalue())
        self.assertIn('"phase":"INVENTORY_UNKNOWN"', out.getvalue())
        self.assertNotIn('PRIVATE_SECRET', out.getvalue())

    def test_unit_files_parse_failure_has_exact_stage_category_and_safe_receipt(self):
        probe = module()
        # Four mutually exclusive classes. The third (preset) column stays
        # unvalidated by this change; no acceptance expansion is authorized.
        invalid = (
            ('ENCODING', b'private-name.service enabled\xff\n', 'UNIT_FILES_ENCODING'),
            ('COLUMNS', b'private-name.service enabled extra extra\n', 'UNIT_FILES_COLUMNS'),
            ('NAME', b'private/name.service enabled enabled\n', 'UNIT_FILES_NAME'),
            ('STATE', b'private-name.service unexpected enabled\n', 'UNIT_FILES_STATE'),
        )
        for category, raw, reason in invalid:
            with self.subTest(category=category):
                self.calls.clear()
                saved = self.responses['list-unit-files']
                self.responses['list-unit-files'] = raw
                try:
                    with self.assertRaises(probe.InventoryHold) as caught:
                        self.inspect()
                finally:
                    self.responses['list-unit-files'] = saved
                self.assertEqual(caught.exception.phase, 'UNIT_FILES_PARSE')
                self.assertEqual(str(caught.exception), reason)
                self.assertEqual(caught.exception.linger, 'NO')
                self.assertEqual(caught.exception.systemd_dbus_state,
                                 'INCOMPLETE_READONLY')
                self.assertEqual(len(self.calls), 4)

                # Serialized by the real remote main() then interpreted by
                # the real inline Actions receiver, never raw host output.
                output = io.StringIO()
                with (mock.patch.object(probe, 'collect', side_effect=caught.exception),
                      contextlib.redirect_stdout(output)):
                    self.assertEqual(probe.main(['--linger-inventory']), 0)
                receipt = json.loads(output.getvalue())
                self.assertEqual(receipt['reason'], reason)
                self.assertEqual(receipt['linger_effective'], 'NO')
                self.assertEqual(receipt['autostart_risk'], 'UNVERIFIED')
                self.assertEqual(receipt['enabled_user_units'], 'UNVERIFIED')
                self.assertEqual(receipt['systemd_dbus_state'], 'INCOMPLETE_READONLY')
                self.assertNotIn('private-name', output.getvalue())
                runner = run_receiver('Read bounded systemd user inventory',
                                      'linger.json', output.getvalue())
                self.assertNotEqual(runner.returncode, 0)
                self.assertIn('PHASE=UNIT_FILES_PARSE|REASON=' + reason,
                              runner.stdout + runner.stderr)
                self.assertIn('LINGER_EFFECTIVE=NO', runner.stdout + runner.stderr)
                self.assertIn('UNIT_INVENTORY=UNVERIFIED', runner.stdout + runner.stderr)
                self.assertIn('CERT2=NO_VERIFICADO', runner.stdout + runner.stderr)
                self.assertNotIn('private', runner.stdout + runner.stderr)
                self.assertNotIn('Traceback', runner.stdout + runner.stderr)

    def test_unit_files_valid_states_retained_without_preset_certification(self):
        probe = module()
        self.assertEqual(probe._unit_files(b'private.service enabled enabled\n'),
                         'PRESENT')
        self.assertEqual(probe._unit_files(b'private.service enabled-runtime -\n'),
                         'PRESENT')
        self.assertEqual(probe._unit_files(b'private.service disabled disabled\n'),
                         'NONE_OBSERVED')
        # State is interpreted; preset's meaning is explicitly NOT certified.
        self.assertEqual(probe._unit_files(b'private.service enabled UNVALIDATED_PRESET\n'),
                         'PRESENT')

    def test_systemd_unit_name_validator_accepts_bounded_escapes_and_all_unit_types(self):
        probe = module()
        types = ('service', 'socket', 'device', 'mount', 'automount', 'swap',
                 'target', 'path', 'timer', 'slice', 'scope')
        for suffix in types:
            with self.subTest(suffix=suffix):
                self.assertTrue(probe._valid_unit_name('private.' + suffix, probe.ALL_UNIT_TYPES))
                self.assertTrue(probe._valid_unit_name(r'private\x2fhidden.' + suffix,
                                                       probe.ALL_UNIT_TYPES))
                self.assertTrue(probe._valid_unit_name(r'private\x2Fhidden.' + suffix,
                                                       probe.ALL_UNIT_TYPES))
        for allowed in ('systemd-networkd.service', '-.mount',
                        'getty@.service', 'getty@tty1.service',
                        'getty@tty@remote.service', 'foo@bar@baz.service',
                        'worker@@instance.timer',
                        r'private\x20space.service', r'private\xC3\xB1.service',
                        r'private\x01.service', r'private\x09.service',
                        r'private\x1f.service', r'private\x7F.service',
                        r'private\xff.service',
                        r'private\x2f\x2F.socket', 'a' * (255 - len('.service')) + '.service'):
            with self.subTest(allowed=allowed[:48]):
                self.assertTrue(probe._valid_unit_name(allowed, probe.ALL_UNIT_TYPES))
        escaped_at_limit = ('a' * (255 - len('.service') - len(r'\x2f')) +
                            r'\x2f.service')
        self.assertEqual(len(escaped_at_limit), 255)
        self.assertTrue(probe._valid_unit_name(escaped_at_limit, probe.ALL_UNIT_TYPES))
        for bad in ('', '.service', '@.service', 'private', 'private.unknown',
                    'private/device.service', 'private/thing.mount',
                    r'private\x.service', r'private\X2F.service',
                    r'private\x2.service', r'private\x2G.service',
                    r'private\xGG.service', r'private\x00.service',
                    'private\x00.service', 'private\t.service',
                    'private\r.service', 'private\n.service',
                    'private\x1b.service', 'private\x7f.service',
                    'privateé.service',
                    'a' * (256 - len('.service')) + '.service',
                    'a' + escaped_at_limit):
            with self.subTest(bad=repr(bad)[:48]):
                self.assertFalse(probe._valid_unit_name(bad, probe.ALL_UNIT_TYPES))
        # USER inventory lists only service/socket/timer despite the shared
        # validator accepting 11 official types in dependency summaries.
        for suffix in ('device', 'mount', 'target', 'scope'):
            self.assertFalse(probe._valid_unit_name('private.' + suffix,
                                                    probe.USER_UNIT_TYPES))

    def test_escaped_unit_names_parse_consistently_across_three_systemd_queries(self):
        probe = module()
        self.assertEqual(probe._unit_files(
            rb'private\x2fservice@tenant\x2Fone.service enabled enabled' + b'\n'),
            'PRESENT')
        self.assertEqual(probe._unit_files(
            rb'private\x09service.service enabled -' + b'\n'), 'PRESENT')
        self.assertEqual(probe._loaded_units(
            rb'private\x2fservice@tenant@two.service loaded active running SAFE' + b'\n'),
            ('PRESENT', 'NONE_OBSERVED', 'NONE_OBSERVED'))
        self.assertEqual(probe._loaded_units(
            rb'private\x7Fservice.service loaded active running SAFE' + b'\n'),
            ('PRESENT', 'NONE_OBSERVED', 'NONE_OBSERVED'))
        deps = b'default.target\n' + b'\n'.join(
            (r'private\x2funit.' + suffix).encode('ascii') for suffix in (
                'service', 'socket', 'device', 'mount', 'automount', 'swap',
                'target', 'path', 'timer', 'slice', 'scope')) + b'\n'
        self.assertEqual(probe._dependencies(deps), 'PRESENT')
        self.assertEqual(probe._dependencies(b'default.target\n' +
                                               rb'private\xFF.device' + b'\n'), 'PRESENT')

    def test_documented_systemd_load_and_active_states_are_conservative(self):
        probe = module()
        self.assertEqual(probe._loaded_units(
            b'private.service bad-setting maintenance waiting SAFE\n'),
            ('PRESENT', 'NONE_OBSERVED', 'NONE_OBSERVED'))
        self.assertEqual(probe._loaded_units(
            b'private.service loaded refreshing running SAFE\n'),
            ('PRESENT', 'NONE_OBSERVED', 'NONE_OBSERVED'))
        with self.assertRaises(probe.InventoryHold):
            probe._loaded_units(b'private.service UNKNOWN_LOAD active running SAFE\n')
        with self.assertRaises(probe.InventoryHold):
            probe._loaded_units(b'private.service loaded UNKNOWN_ACTIVE running SAFE\n')
        with self.assertRaises(probe.InventoryHold) as caught:
            probe._loaded_units(b'private.service bad active running SAFE\n')
        self.assertEqual(str(caught.exception), 'ACTIVE_UNITS_LOAD')
        # `bad` is a valid unit-file state, not an official LOAD state.
        self.assertEqual(probe._unit_files(b'private.service bad -\n'),
                         'NONE_OBSERVED')

    def test_dependency_empty_root_and_exact_bound_are_distinct(self):
        probe = module()
        self.assertEqual(probe._dependencies(b'default.target\n' +
                                              b'private.service\n' * 127), 'PRESENT')
        for raw, reason in (
                (b'', 'TARGET_DEPS_EMPTY'),
                (b'   \n\t\n', 'TARGET_DEPS_EMPTY'),
                (b'PRIVATE_ROOT.target\n', 'TARGET_DEPS_ROOT'),
                (b'default.target\n' + b'private.service\n' * 128,
                 'TARGET_DEPS_BOUND')):
            with self.subTest(reason=reason):
                with self.assertRaises(probe.InventoryHold) as caught:
                    probe._dependencies(raw)
                self.assertEqual(str(caught.exception), reason)

    def test_escaped_inventory_through_real_collect_main_and_workflow_receiver(self):
        self.responses['list-unit-files'] = (
            rb'private\x2fescaped@tenant@one.service enabled enabled' + b'\n')
        self.responses['list-units'] = (
            rb'private\x2Fescaped@tenant@one.service bad-setting refreshing running SAFE' + b'\n')
        self.responses['list-dependencies'] = (
            b'default.target\n' + rb'private\x2fescaped.device' + b'\n')
        code, raw_receipt = self.inspect(through_main=True)
        self.assertEqual(code, 0)
        receipt = json.loads(raw_receipt)
        self.assertEqual(receipt['probe_extent'], 'FULL')
        self.assertEqual(receipt['linger_effective'], 'NO')
        self.assertEqual(receipt['systemd_dbus_state'], 'QUERIED_READONLY')
        self.assertEqual(receipt['autostart_risk'], 'PRESENT')
        self.assertEqual(receipt['enabled_user_units'], 'PRESENT')
        self.assertEqual(receipt['active_user_services'], 'PRESENT')
        self.assertEqual(receipt['default_target_dependencies'], 'PRESENT')
        self.assertNotIn('private', raw_receipt)
        self.assertNotIn(r'\x2f', raw_receipt)
        self.assertEqual(len(self.calls), 4)
        runner = run_receiver('Read bounded systemd user inventory',
                              'linger.json', raw_receipt)
        self.assertEqual(runner.returncode, 0, runner.stdout + runner.stderr)
        self.assertIn('READ_ONLY_OBSERVED', runner.stdout + runner.stderr)
        self.assertIn('LINGER_EFFECTIVE=NO', runner.stdout + runner.stderr)
        self.assertIn('CERT2=NO_VERIFICADO', runner.stdout + runner.stderr)
        self.assertNotIn('private', runner.stdout + runner.stderr)

    def test_active_and_dependency_parse_failures_have_fixed_sanitized_categories(self):
        probe = module()
        invalid = (
            ('list-units', b'private.service loaded active\xff running PRIVATE\n',
             'ACTIVE_UNITS_PARSE', 'ACTIVE_UNITS_ENCODING'),
            ('list-units', b'private.service loaded active\n',
             'ACTIVE_UNITS_PARSE', 'ACTIVE_UNITS_COLUMNS'),
            ('list-units', b'private/name.service loaded active running PRIVATE\n',
             'ACTIVE_UNITS_PARSE', 'ACTIVE_UNITS_NAME'),
            ('list-units', b'private.service UNKNOWN_LOAD active running PRIVATE\n',
             'ACTIVE_UNITS_PARSE', 'ACTIVE_UNITS_LOAD'),
            ('list-units', b'private.service loaded UNKNOWN_ACTIVE running PRIVATE\n',
             'ACTIVE_UNITS_PARSE', 'ACTIVE_UNITS_ACTIVE'),
            ('list-dependencies', b'default.target\nprivate\xff.service\n',
             'TARGET_DEPS_PARSE', 'TARGET_DEPS_ENCODING'),
            ('list-dependencies', b'PRIVATE_ROOT.target\nprivate.service\n',
             'TARGET_DEPS_PARSE', 'TARGET_DEPS_ROOT'),
            ('list-dependencies', b'',
             'TARGET_DEPS_PARSE', 'TARGET_DEPS_EMPTY'),
            ('list-dependencies', b'default.target\n' + b'private.service\n' * 128,
             'TARGET_DEPS_PARSE', 'TARGET_DEPS_BOUND'),
            ('list-dependencies', b'default.target\nprivate/name.device\n',
             'TARGET_DEPS_PARSE', 'TARGET_DEPS_NAME'),
        )
        for command, raw, phase, reason in invalid:
            with self.subTest(reason=reason):
                previous = self.responses[command]
                self.responses[command] = raw
                try:
                    with self.assertRaises(probe.InventoryHold) as caught:
                        self.inspect()
                finally:
                    self.responses[command] = previous
                self.assertEqual(caught.exception.phase, phase)
                self.assertEqual(str(caught.exception), reason)
                self.assertEqual(caught.exception.linger, 'NO')
                self.assertEqual(caught.exception.systemd_dbus_state,
                                 'INCOMPLETE_READONLY')
                output = io.StringIO()
                with (mock.patch.object(probe, 'collect', side_effect=caught.exception),
                      contextlib.redirect_stdout(output)):
                    self.assertEqual(probe.main(['--linger-inventory']), 0)
                receipt = json.loads(output.getvalue())
                self.assertEqual(receipt['autostart_risk'], 'UNVERIFIED')
                self.assertEqual({receipt[key] for key in probe.UNIT_FIELDS}, {'UNVERIFIED'})
                self.assertNotIn('PRIVATE', output.getvalue())
                result = run_receiver('Read bounded systemd user inventory',
                                      'linger.json', output.getvalue())
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('PHASE=' + phase + '|REASON=' + reason,
                              result.stdout + result.stderr)
                self.assertIn('LINGER_EFFECTIVE=NO', result.stdout + result.stderr)
                self.assertIn('CERT2=NO_VERIFICADO', result.stdout + result.stderr)
                self.assertNotIn('PRIVATE', result.stdout + result.stderr)
                self.assertNotIn('Traceback', result.stdout + result.stderr)

    def test_unit_files_rejects_malformed_late_rows_without_partial_success(self):
        probe = module()
        for invalid, reason in (
                (b'private.service enabled enabled\nsecret/service enabled -\n',
                 'UNIT_FILES_NAME'),
                (b'private.service enabled enabled\nsecret.service UNKNOWN_STATE -\n',
                 'UNIT_FILES_STATE'),
                (b'private.service enabled enabled\nsecret.service enabled a b\n',
                 'UNIT_FILES_COLUMNS'),
                (b'private.service enabled enabled\nsecret.service enabled\xff\n',
                 'UNIT_FILES_ENCODING')):
            with self.subTest(reason=reason):
                with self.assertRaises(probe.InventoryHold) as caught:
                    probe._unit_files(invalid)
                self.assertEqual(str(caught.exception), reason)

    def test_inventory_hold_identifies_only_fixed_query_phase(self):
        probe = module()
        saved = self.paths['/run/user/1000/bus']
        self.paths['/run/user/1000/bus'] = entry(stat.S_IFLNK, 0o777, 1000)
        try:
            with self.assertRaises(probe.InventoryHold) as caught:
                self.inspect()
            self.assertEqual(str(caught.exception), 'USER_BUS_UNVERIFIED')
            self.assertEqual(caught.exception.phase, 'PREFLIGHT')
        finally:
            self.paths['/run/user/1000/bus'] = saved

        for command, phase in (
                ('show-user', 'LOGIND_QUERY'),
                ('list-unit-files', 'UNIT_FILES_QUERY'),
                ('list-units', 'ACTIVE_UNITS_QUERY'),
                ('list-dependencies', 'TARGET_DEPS_QUERY')):
            with self.subTest(command=command):
                original = self.responses[command]
                self.responses[command] = probe.InventoryHold('QUERY_FAILED')
                try:
                    with self.assertRaises(probe.InventoryHold) as caught:
                        self.inspect()
                finally:
                    self.responses[command] = original
                self.assertEqual(str(caught.exception), 'QUERY_FAILED')
                self.assertEqual(caught.exception.phase, phase)

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
    def test_a1043_parser_reason_enums_reject_forged_cross_stage_receipts(self):
        probe = module()
        base = probe._partial_receipt(probe.InventoryHold(
            'ACTIVE_UNITS_NAME', 'ACTIVE_UNITS_PARSE', linger='NO',
            user_runtime_status='METADATA_VALID', user_bus_status='METADATA_VALID',
            bus_mode_other_bits='PRESENT', logind_status='PARSED_READONLY',
            systemd_dbus_state='INCOMPLETE_READONLY'))
        forged = (
            dict(base, phase='TARGET_DEPS_PARSE'),
            dict(base, phase='UNIT_FILES_PARSE'),
            dict(base, reason='SYSTEMD_OUTPUT_UNVERIFIED'),
            dict(base, reason='TARGET_DEPS_NAME'),
            dict(base, reason='ACTIVE_UNITS_PRIVATE_HOST_VALUE'),
            dict(base, reason=['ACTIVE_UNITS_NAME']),
            dict(base, reason={'PRIVATE_HOST': 'ACTIVE_UNITS_NAME'}),
            dict(base, user_bus_status=['METADATA_VALID']),
            dict(base, user_bus_status={'PRIVATE_HOST': 'METADATA_VALID'}),
            dict(base, rootless_certified=True),
            dict(base, user_sockets='PRESENT'),
            dict(base, logind_status='NOT_QUERIED'),
            dict(base, PRIVATE_FIELD='PRIVATE_VALUE'),
            {key: value for key, value in base.items() if key != 'phase'},
        )
        for bad in forged:
            with self.subTest(bad=str(bad)[:48]):
                receipt = run_receiver('Read bounded systemd user inventory',
                                       'linger.json', json.dumps(bad))
                self.assertNotEqual(receipt.returncode, 0)
                self.assertIn('RECEIPT_INVALID', receipt.stdout + receipt.stderr)
                self.assertNotIn('PRIVATE_', receipt.stdout + receipt.stderr)
                self.assertNotIn('Traceback', receipt.stdout + receipt.stderr)

        raw = json.dumps(base)
        for modified in (
                raw.replace('"reason": "ACTIVE_UNITS_NAME"',
                            '"reason": "TARGET_DEPS_NAME", "reason": "ACTIVE_UNITS_NAME"'),
                raw.replace('"mutations": 0', '"mutations": NaN'),
                raw.replace('"mutations": 0', '"mutations": Infinity')):
            receipt = run_receiver('Read bounded systemd user inventory',
                                   'linger.json', modified)
            self.assertNotEqual(receipt.returncode, 0)
            self.assertIn('RECEIPT_INVALID', receipt.stdout + receipt.stderr)
            self.assertNotIn('Traceback', receipt.stdout + receipt.stderr)

    def test_unit_files_parse_categories_are_strictly_phase_bound_and_sanitized(self):
        probe = module()
        baseline = probe._partial_receipt(probe.InventoryHold(
            'UNIT_FILES_STATE', 'UNIT_FILES_PARSE', linger='NO',
            user_runtime_status='METADATA_VALID', user_bus_status='METADATA_VALID',
            bus_mode_other_bits='PRESENT', logind_status='PARSED_READONLY',
            systemd_dbus_state='INCOMPLETE_READONLY'))
        for reason in ('UNIT_FILES_ENCODING', 'UNIT_FILES_COLUMNS',
                       'UNIT_FILES_NAME', 'UNIT_FILES_STATE'):
            with self.subTest(valid=reason):
                valid = dict(baseline, reason=reason)
                proc = run_receiver('Read bounded systemd user inventory',
                                    'linger.json', json.dumps(valid))
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn('PHASE=UNIT_FILES_PARSE|REASON=' + reason,
                              proc.stdout + proc.stderr)
                self.assertNotIn('RECEIPT_INVALID', proc.stdout + proc.stderr)

        forged = (
            dict(baseline, phase='ACTIVE_UNITS_PARSE'),
            dict(baseline, phase='UNIT_FILES_QUERY'),
            dict(baseline, reason='SYSTEMD_OUTPUT_UNVERIFIED'),
            dict(baseline, reason='UNIT_FILES_PRIVATE_SECRET'),
            dict(baseline, reason=['UNIT_FILES_STATE']),
            dict(baseline, reason={'PRIVATE_KEY': 'UNIT_FILES_STATE'}),
            dict(baseline, user_bus_status=['METADATA_VALID']),
            dict(baseline, autostart_risk='PRESENT'),
            dict(baseline, enabled_user_units='PRESENT'),
            dict(baseline, rootless_certified=True),
            dict(baseline, PRIVATE_FIELD='PRIVATE_VALUE'),
            {k: v for k, v in baseline.items() if k != 'systemd_dbus_state'},
        )
        for bad in forged:
            with self.subTest(bad=str(bad)[:72]):
                proc = run_receiver('Read bounded systemd user inventory',
                                    'linger.json', json.dumps(bad))
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn('RECEIPT_INVALID', proc.stdout + proc.stderr)
                self.assertNotIn('PRIVATE_', proc.stdout + proc.stderr)
                self.assertNotIn('Traceback', proc.stdout + proc.stderr)

        valid = json.dumps(baseline)
        for corrupt in (
                valid.replace('"reason": "UNIT_FILES_STATE"',
                              '"reason": "UNIT_FILES_NAME", "reason": "UNIT_FILES_STATE"'),
                valid.replace('"mutations": 0', '"mutations": NaN'),
                valid.replace('"mutations": 0', '"mutations": -Infinity')):
            proc = run_receiver('Read bounded systemd user inventory',
                                'linger.json', corrupt)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn('RECEIPT_INVALID', proc.stdout + proc.stderr)
            self.assertNotIn('Traceback', proc.stdout + proc.stderr)

    def test_partial_bus_receipt_is_strictly_validated_and_never_certified(self):
        probe = module()
        receipt = probe._partial_receipt(probe.InventoryHold(
            'USER_BUS_UNVERIFIED', 'PREFLIGHT', linger='YES',
            user_runtime_status='METADATA_VALID', user_bus_status='MISSING',
            bus_mode_other_bits='NOT_CHECKED', logind_status='PARSED_READONLY'))
        good = run_receiver('Read bounded systemd user inventory', 'linger.json',
                            json.dumps(receipt))
        self.assertNotEqual(good.returncode, 0, good.stderr + good.stdout)
        self.assertIn('REASON=USER_BUS_UNVERIFIED', good.stdout + good.stderr)
        self.assertIn('RUNTIME_STATUS=METADATA_VALID', good.stdout + good.stderr)
        self.assertIn('BUS_STATUS=MISSING', good.stdout + good.stderr)
        self.assertIn('LINGER_EFFECTIVE=YES', good.stdout + good.stderr)
        self.assertIn('BUS_MODE_OTHER_BITS=NOT_CHECKED', good.stdout + good.stderr)
        self.assertIn('UNIT_INVENTORY=NOT_QUERIED', good.stdout + good.stderr)
        self.assertIn('CERT2=NO_VERIFICADO', good.stdout + good.stderr)
        self.assertNotIn('/run/user', good.stdout + good.stderr)

        invalid = (
            dict(receipt, user_runtime_status='MISSING', user_bus_status='METADATA_VALID'),
            dict(receipt, user_bus_status='NOT_CHECKED'),
            dict(receipt, bus_mode_other_bits='PRESENT'),
            dict(receipt, systemd_dbus_state='QUERIED_READONLY'),
            dict(receipt, user_timers='PRESENT'),
            dict(receipt, logind_status='QUERY_UNVERIFIED'),
            dict(receipt, linger_effective='UNVERIFIED'),
            dict(receipt, logind_status='NOT_QUERIED', linger_effective='UNVERIFIED'),
            dict(receipt, probe_extent='FULL'),
            dict(receipt, read_only=1),
            dict(receipt, rootless_certified=True),
            dict(receipt, reason='PRIVATE_REASON'),
            dict(receipt, phase='LOGIND_PARSE'),
            dict(receipt, user_runtime_status=['METADATA_VALID']),
            dict(receipt, user_runtime_status={'PRIVATE_PATH': 'METADATA_VALID'}),
            dict(receipt, user_bus_status=['MISSING']),
            dict(receipt, user_bus_status={'PRIVATE_PATH': 'MISSING'}),
            dict(receipt, logind_status=['PARSED_READONLY']),
            dict(receipt, bus_mode_other_bits=['PRESENT']),
            dict(receipt, probe_extent=['PARTIAL']),
            dict(receipt, extra='PRIVATE_FIELD'),
            {key: value for key, value in receipt.items() if key != 'user_bus_status'},
        )
        for malicious in invalid:
            with self.subTest(mutation=str(malicious)[:100]):
                result = run_receiver('Read bounded systemd user inventory',
                                      'linger.json', json.dumps(malicious))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('RECEIPT_INVALID', result.stdout + result.stderr)
                self.assertNotIn('PRIVATE_', result.stdout + result.stderr)
                self.assertNotIn('Traceback', result.stdout + result.stderr)
        valid = json.dumps(receipt)
        for raw in (
                valid.replace('"phase": "PREFLIGHT"',
                              '"phase": "LOGIND_QUERY", "phase": "PREFLIGHT"'),
                valid.replace('"mutations": 0', '"mutations": NaN'),
                valid.replace('"mutations": 0', '"mutations": Infinity')):
            result = run_receiver('Read bounded systemd user inventory',
                                  'linger.json', raw)
            self.assertIn('RECEIPT_INVALID', result.stdout + result.stderr)

    def test_late_systemctl_error_retains_linger_in_sanitized_receiver(self):
        probe = module()
        receipt = probe._partial_receipt(probe.InventoryHold(
            'QUERY_FAILED', 'ACTIVE_UNITS_QUERY', linger='YES',
            user_runtime_status='METADATA_VALID', user_bus_status='METADATA_VALID',
            bus_mode_other_bits='PRESENT', logind_status='PARSED_READONLY',
            systemd_dbus_state='INCOMPLETE_READONLY'))
        good = run_receiver('Read bounded systemd user inventory', 'linger.json',
                            json.dumps(receipt))
        self.assertNotEqual(good.returncode, 0, good.stdout + good.stderr)
        self.assertIn('LINGER_EFFECTIVE=YES', good.stdout + good.stderr)
        self.assertIn('BUS_MODE_OTHER_BITS=PRESENT', good.stdout + good.stderr)
        self.assertIn('UNIT_INVENTORY=UNVERIFIED', good.stdout + good.stderr)
        self.assertIn('CERT2=NO_VERIFICADO', good.stdout + good.stderr)

    def test_real_receiver_reports_allowlisted_stage_and_reason_only(self):
        fields = {'schema': 'rc6.r1.linger-runtime-inventory-readonly.v2',
                  'state': 'NO_VERIFICADO', 'read_only': True,
                  'mutations': 0, 'docker_queries': 0,
                  'docker_socket_connections': 0, 'sudo_executed': False,
                  'rootless_certified': False,
                  'probe_extent': 'PARTIAL',
                  'user_runtime_status': 'METADATA_VALID',
                  'user_bus_status': 'SYMLINK',
                  'bus_mode_other_bits': 'NOT_CHECKED',
                  'logind_status': 'PARSED_READONLY',
                  'linger_effective': 'NO',
                  'enabled_user_units': 'NOT_QUERIED',
                  'active_user_services': 'NOT_QUERIED',
                  'user_timers': 'NOT_QUERIED',
                  'user_sockets': 'NOT_QUERIED',
                  'default_target_dependencies': 'NOT_QUERIED',
                  'autostart_risk': 'UNVERIFIED',
                  'systemd_dbus_state': 'NOT_QUERIED',
                  'reason': 'USER_BUS_UNVERIFIED', 'phase': 'PREFLIGHT'}
        proc = run_receiver('Read bounded systemd user inventory',
                            'linger.json', json.dumps(fields))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('HOST_PREREQUISITES_UNVERIFIED|PHASE=PREFLIGHT'
                      '|REASON=USER_BUS_UNVERIFIED', proc.stdout + proc.stderr)
        self.assertIn('|CERT2=NO_VERIFICADO', proc.stdout + proc.stderr)

        for field, bad in (('reason', 'UNREVIEWED_PRIVATE_CODE'),
                           ('phase', 'untrusted; echo PRIVATE_SECRET')):
            with self.subTest(field=field):
                malicious = dict(fields, **{field: bad})
                proc = run_receiver('Read bounded systemd user inventory',
                                    'linger.json', json.dumps(malicious))
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn('RECEIPT_INVALID', proc.stdout + proc.stderr)
                self.assertNotIn(bad, proc.stdout + proc.stderr)

        # The receiver must reject valid-looking but impossible pairs, and
        # never interpret duplicate keys or non-JSON numbers as trusted enums.
        for malicious in (
                dict(fields, reason='QUERY_FAILED', phase='PREFLIGHT'),
                dict(fields, reason='USER_BUS_UNVERIFIED', phase='LOGIND_QUERY'),
                dict(fields, reason='INVENTORY_UNVERIFIED', phase='LOGIND_PARSE'),
                dict(fields, reason=['USER_BUS_UNVERIFIED']),
                dict(fields, phase={'name': 'PREFLIGHT'}),
                dict(fields, docker_queries=1),
                dict(fields, rootless_certified=True),
                dict(fields, private_name='DO_NOT_PRINT')):
            with self.subTest(malicious=str(malicious)[:80]):
                proc = run_receiver('Read bounded systemd user inventory',
                                    'linger.json', json.dumps(malicious))
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn('RECEIPT_INVALID', proc.stdout + proc.stderr)

        valid = json.dumps(fields)
        for modified in (valid.replace('"phase": "PREFLIGHT"',
                         '"phase": "LOGIND_QUERY", "phase": "PREFLIGHT"'),
                         valid.replace('"mutations": 0', '"mutations": NaN')):
            proc = run_receiver('Read bounded systemd user inventory',
                                'linger.json', modified)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn('RECEIPT_INVALID', proc.stdout + proc.stderr)
            self.assertNotIn('Traceback', proc.stdout + proc.stderr)

    def test_query_and_parse_error_enums_remain_fail_closed(self):
        base = {'schema': 'rc6.r1.linger-runtime-inventory-readonly.v2',
                'state': 'NO_VERIFICADO', 'read_only': True,
                'mutations': 0, 'docker_queries': 0,
                'docker_socket_connections': 0, 'sudo_executed': False,
                'rootless_certified': False,
                'probe_extent': 'PARTIAL',
                'user_runtime_status': 'METADATA_VALID',
                'user_bus_status': 'METADATA_VALID',
                'bus_mode_other_bits': 'ABSENT',
                'logind_status': 'PARSED_READONLY',
                'linger_effective': 'NO',
                'enabled_user_units': 'UNVERIFIED',
                'active_user_services': 'UNVERIFIED',
                'user_timers': 'UNVERIFIED',
                'user_sockets': 'UNVERIFIED',
                'default_target_dependencies': 'UNVERIFIED',
                'autostart_risk': 'UNVERIFIED',
                'systemd_dbus_state': 'INCOMPLETE_READONLY'}
        examples = (
            ('PREFLIGHT', 'USER_BUS_UNVERIFIED'),
            ('LOGIND_QUERY', 'QUERY_TIMEOUT'),
            ('UNIT_FILES_QUERY', 'QUERY_FAILED'),
            ('ACTIVE_UNITS_QUERY', 'QUERY_OUTPUT_BOUND'),
            ('TARGET_DEPS_QUERY', 'QUERY_UNVERIFIED'),
            ('LOGIND_PARSE', 'LOGIND_UNVERIFIED'),
            ('UNIT_FILES_PARSE', 'UNIT_FILES_STATE'),
            ('ACTIVE_UNITS_PARSE', 'ACTIVE_UNITS_NAME'),
            ('TARGET_DEPS_PARSE', 'TARGET_DEPS_NAME'),
            ('INVENTORY_UNKNOWN', 'INVENTORY_UNVERIFIED'))
        for phase, reason in examples:
            with self.subTest(phase=phase):
                receipt = dict(base, phase=phase, reason=reason)
                if phase == 'PREFLIGHT':
                    receipt.update(user_bus_status='SYMLINK',
                                   bus_mode_other_bits='NOT_CHECKED',
                                   systemd_dbus_state='NOT_QUERIED',
                                   **{key: 'NOT_QUERIED' for key in (
                                       'enabled_user_units', 'active_user_services',
                                       'user_timers', 'user_sockets',
                                       'default_target_dependencies')})
                if phase in ('LOGIND_QUERY', 'LOGIND_PARSE', 'INVENTORY_UNKNOWN'):
                    receipt.update(logind_status=(
                        'QUERY_UNVERIFIED' if phase == 'LOGIND_QUERY' else
                        'PARSE_UNVERIFIED' if phase == 'LOGIND_PARSE' else
                        'NOT_QUERIED'),
                        linger_effective='UNVERIFIED',
                        systemd_dbus_state='NOT_QUERIED',
                        **{key: 'NOT_QUERIED' for key in (
                            'enabled_user_units', 'active_user_services',
                            'user_timers', 'user_sockets',
                            'default_target_dependencies')})
                if phase == 'INVENTORY_UNKNOWN':
                    receipt.update(user_runtime_status='UNVERIFIED',
                                   user_bus_status='NOT_CHECKED',
                                   bus_mode_other_bits='NOT_CHECKED')
                proc = run_receiver('Read bounded systemd user inventory',
                                    'linger.json', json.dumps(receipt))
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn('PHASE=' + phase + '|REASON=' + reason,
                              proc.stdout + proc.stderr)
                self.assertNotIn('Traceback', proc.stdout + proc.stderr)

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
        fields = {'schema': 'rc6.r1.linger-runtime-inventory-readonly.v2',
                  'state': 'NO_VERIFICADO', 'read_only': True,
                  'probe_extent': 'FULL',
                  'user_runtime_status': 'METADATA_VALID',
                  'user_bus_status': 'METADATA_VALID',
                  'bus_mode_other_bits': 'ABSENT',
                  'logind_status': 'PARSED_READONLY',
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
        inventory = {'schema': 'rc6.r1.linger-runtime-inventory-readonly.v2',
                     'state': 'NO_VERIFICADO', 'read_only': True,
                     'probe_extent': 'FULL',
                     'user_runtime_status': 'METADATA_VALID',
                     'user_bus_status': 'METADATA_VALID',
                     'bus_mode_other_bits': 'ABSENT',
                     'logind_status': 'PARSED_READONLY',
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
