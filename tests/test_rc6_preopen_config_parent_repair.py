"""P0-D: bounded mode-only repair of the current login's .config parent."""
import json
from io import StringIO
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from contextlib import nullcontext
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts import porota_actions_host as host
from scripts import rc6_preopen_config_parent_repair as repair


class ConfigParentRepairTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.config = self.home / '.config'
        self.config.mkdir(mode=0o700)
        self.fd = os.open(self.home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.addCleanup(os.close, self.fd)

    def test_removes_only_group_other_write_bits_and_preserves_everything_else(self):
        os.chmod(self.config, 0o777)
        observed = repair.repair_pinned_config(self.fd, expected_uid=os.getuid(), expected_gid=os.getgid())
        self.assertEqual(observed, {
            'state': 'REPAIRED', 'before_mode': '0o777',
            'after_mode': '0o755', 'mutations': 1,
        })
        self.assertEqual(stat.S_IMODE(os.stat(self.config).st_mode), 0o755)

    def test_safe_directory_is_idempotent_without_chmod(self):
        os.chmod(self.config, 0o750)
        with patch.object(repair.os, 'fchmod', side_effect=AssertionError('unnecessary mode write')):
            observed = repair.repair_pinned_config(self.fd, expected_uid=os.getuid(), expected_gid=os.getgid())
        self.assertEqual(observed['state'], 'ALREADY_SAFE')
        self.assertEqual(observed['mutations'], 0)
        self.assertEqual(observed['after_mode'], '0o750')

    def test_symlink_is_rejected_without_following_or_altering_target(self):
        outside = self.home / 'unrelated'
        outside.mkdir()
        os.chmod(outside, 0o777)
        self.config.rmdir()
        self.config.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(repair.RepairHold):
            repair.repair_pinned_config(self.fd, expected_uid=os.getuid(), expected_gid=os.getgid())
        self.assertEqual(stat.S_IMODE(os.stat(outside).st_mode), 0o777)

    def test_foreign_directory_owner_is_rejected_without_mutation(self):
        os.chmod(self.config, 0o777)
        with self.assertRaises(repair.RepairHold):
            repair.repair_pinned_config(self.fd, expected_uid=os.getuid() + 1, expected_gid=os.getgid())
        self.assertEqual(stat.S_IMODE(os.stat(self.config).st_mode), 0o777)

    def test_replaced_path_before_chmod_is_refused(self):
        os.chmod(self.config, 0o777)
        other = self.home / 'other'
        other.mkdir()
        os.chmod(other, 0o777)
        original_stat = os.stat
        swapped = False

        def replace_on_named_stat(path, *args, **kwargs):
            nonlocal swapped
            if path == '.config' and 'dir_fd' in kwargs and not swapped:
                swapped = True
                self.config.rename(self.home / 'old-config')
                other.rename(self.config)
            return original_stat(path, *args, **kwargs)

        with patch.object(repair.os, 'stat', side_effect=replace_on_named_stat):
            with self.assertRaises(repair.RepairHold):
                repair.repair_pinned_config(self.fd, expected_uid=os.getuid(), expected_gid=os.getgid())
        self.assertTrue(swapped)
        self.assertEqual(stat.S_IMODE(original_stat(self.config).st_mode), 0o777)
        self.assertEqual(stat.S_IMODE(original_stat(self.home / 'old-config').st_mode), 0o777)

    def test_replaced_path_after_chmod_reports_unknown_without_touching_new_target(self):
        os.chmod(self.config, 0o777)
        other = self.home / 'other'
        other.mkdir()
        os.chmod(other, 0o777)
        original_stat = os.stat
        visits = 0

        def replace_on_second_named_stat(path, *args, **kwargs):
            nonlocal visits
            if path == '.config' and 'dir_fd' in kwargs:
                visits += 1
                if visits == 2:
                    self.config.rename(self.home / 'old-config')
                    other.rename(self.config)
            return original_stat(path, *args, **kwargs)

        with patch.object(repair.os, 'stat', side_effect=replace_on_second_named_stat):
            with self.assertRaises(repair.RepairHold):
                repair.repair_pinned_config(self.fd, expected_uid=os.getuid(), expected_gid=os.getgid())
        self.assertEqual(visits, 2)
        self.assertEqual(stat.S_IMODE(original_stat(self.config).st_mode), 0o777)
        self.assertEqual(stat.S_IMODE(original_stat(self.home / 'old-config').st_mode), 0o755)

    def test_fchmod_oserror_is_hold_and_never_claims_zero_mutations(self):
        os.chmod(self.config, 0o777)
        with patch.object(repair.os, 'fchmod', side_effect=PermissionError('PRIVATE_TOKEN')):
            with self.assertRaises(repair.RepairHold):
                repair.repair_pinned_config(self.fd, expected_uid=os.getuid(), expected_gid=os.getgid())
        self.assertEqual(stat.S_IMODE(os.stat(self.config).st_mode), 0o777)
        output = StringIO()
        with patch.object(repair, 'repair_current_host', side_effect=repair.RepairHold('PRIVATE_TOKEN')), \
             patch.object(repair.sys, 'argv', ['-']), redirect_stdout(output):
            self.assertEqual(repair.main(), 2)
        receipt = json.loads(output.getvalue())
        self.assertEqual(receipt['mutations'], 'NO_VERIFICADO')
        self.assertNotIn('PRIVATE_TOKEN', output.getvalue())

    def test_foreign_group_is_rejected_without_chmod(self):
        os.chmod(self.config, 0o777)
        with self.assertRaises(repair.RepairHold):
            repair.repair_pinned_config(self.fd, expected_uid=os.getuid(), expected_gid=os.getgid() + 1)
        self.assertEqual(stat.S_IMODE(os.stat(self.config).st_mode), 0o777)

    def test_child_fd_closed_when_named_stat_io_fails(self):
        tracked = []
        original_open = os.open

        def tracked_open(*args, **kwargs):
            opened = original_open(*args, **kwargs)
            tracked.append(opened)
            return opened

        with patch.object(repair.os, 'open', side_effect=tracked_open), \
             patch.object(repair.os, 'stat', side_effect=PermissionError('private path')):
            with self.assertRaises(repair.RepairHold):
                repair._verified_child(self.fd, '.config', os.getuid(), os.getgid())
        self.assertEqual(len(tracked), 1)
        with self.assertRaises(OSError):
            os.fstat(tracked[0])

    def test_child_fd_closed_when_fstat_io_fails(self):
        tracked = []
        original_open = os.open

        def tracked_open(*args, **kwargs):
            opened = original_open(*args, **kwargs)
            tracked.append(opened)
            return opened

        with patch.object(repair.os, 'open', side_effect=tracked_open), \
             patch.object(repair.os, 'fstat', side_effect=PermissionError('private path')):
            with self.assertRaises(repair.RepairHold):
                repair._verified_child(self.fd, '.config', os.getuid(), os.getgid())
        self.assertEqual(len(tracked), 1)
        with self.assertRaises(OSError):
            os.fstat(tracked[0])

    def test_private_group_must_not_have_another_primary_or_explicit_member(self):
        account = SimpleNamespace(pw_uid=1000, pw_name='porotaadmin',
                                  pw_dir='/home/porotaadmin', pw_gid=1000)
        for members, accounts in [(['otheruser'], []),
                                  ([], [SimpleNamespace(pw_uid=1001, pw_gid=1000)])]:
            with self.subTest(members=members, accounts=accounts), \
                 patch.object(repair.os, 'getuid', return_value=1000), \
                 patch.object(repair.os, 'geteuid', return_value=1000), \
                 patch.object(repair.os, 'getgid', return_value=1000), \
                 patch.object(repair.os, 'getegid', return_value=1000), \
                 patch.object(repair.pwd, 'getpwuid', return_value=account), \
                 patch.object(repair.pwd, 'getpwall', return_value=accounts), \
                 patch.object(repair.grp, 'getgrgid', return_value=SimpleNamespace(
                     gr_name='porotaadmin', gr_mem=members)), \
                 patch.object(repair.os, 'open', side_effect=AssertionError('must not access host')):
                with self.assertRaisesRegex(repair.RepairHold, 'LOGIN_PRIVATE_GROUP_UNVERIFIED'):
                    repair.repair_current_host()

    def test_fixed_login_guard_rejects_foreign_uid_before_path_write(self):
        with patch.object(repair.os, 'getuid', return_value=1001):
            with self.assertRaises(repair.RepairHold):
                repair.repair_current_host()


class ConfigParentActionTests(unittest.TestCase):
    def setUp(self):
        self.receipt = {
            'schema': 'rc6.preopen.config-parent-mode.v1',
            'operation': 'config_parent_repair', 'state': 'REPAIRED',
            'uid': 1000, 'mutations': 1,
            'before_mode': '0o777', 'after_mode': '0o755',
            'ppi_watch_touched': False, 'boca_touched': False,
            'docker_touched': False, 'systemd_touched': False,
            'db_touched': False, 'order_routes': 'NOT_CALLED',
            'indirect_impact': 'NO_VERIFICADO', 'private_group_verified': True,
        }

    def test_success_receipt_proves_only_expected_repair_and_custody(self):
        host.validate_config_parent_receipt(self.receipt, 0)
        host.validate_config_parent_receipt({**self.receipt, 'state': 'ALREADY_SAFE',
                                            'mutations': 0, 'after_mode': '0o750',
                                            'before_mode': '0o750'}, 0)
        for invalid in ({'state': 'UNVERIFIED'}, {'uid': 0}, {'mutations': 2},
                        {'before_mode': '0o666'}, {'after_mode': '0o777'},
                        {'ppi_watch_touched': True}, {'order_routes': 'CALLED'},
                        {'secret': 'private token'}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                host.validate_config_parent_receipt({**self.receipt, **invalid}, 0)
        with self.assertRaises(ValueError):
            host.validate_config_parent_receipt(self.receipt, 79)

    def test_action_streams_fixed_source_over_authenticated_ssh_without_staging_sudo(self):
        commands = []
        payload = []

        def remote(_connection, command, **kwargs):
            commands.append(command)
            payload.append(kwargs.get('data'))
            return subprocess.CompletedProcess(command, 0, json.dumps(self.receipt).encode(), b'')

        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository/'scripts').mkdir()
            (repository/'scripts'/'rc6_preopen_config_parent_repair.py').write_bytes(b'# reviewed script\n')
            with patch.object(host, 'connection', return_value=nullcontext((['ssh'], ['scp'], 'user@host'))), \
                    patch.object(host, 'ssh_run', side_effect=remote):
                got = host.execute('config_parent_repair', repository)
        self.assertEqual(got, self.receipt)
        self.assertEqual(commands, [['python3', '-I', '-B', '-']])
        self.assertEqual(payload, [b'# reviewed script\n'])

    def test_action_never_republishes_untrusted_remote_receipt_fields(self):
        untrusted = {**self.receipt, 'token': 'SENSITIVE'}
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository/'scripts').mkdir()
            (repository/'scripts'/'rc6_preopen_config_parent_repair.py').write_text('# reviewed\n')
            with patch.object(host, 'connection', return_value=nullcontext((['ssh'], ['scp'], 'user@host'))), \
                    patch.object(host, 'ssh_run', return_value=subprocess.CompletedProcess([], 0,
                                json.dumps(untrusted).encode(), b'')):
                with self.assertRaises(host.HostFailure) as captured:
                    host.execute('config_parent_repair', repository)
        self.assertNotIn('SENSITIVE', json.dumps(captured.exception.receipt))

    def test_action_refuses_symlinked_script_before_ssh(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository/'scripts').mkdir()
            script = repository/'scripts'/'rc6_preopen_config_parent_repair.py'
            outside = repository/'untrusted.py'
            outside.write_text('print("nothing")\n')
            script.symlink_to(outside)
            with patch.object(host, 'connection', return_value=nullcontext((['ssh'], ['scp'], 'user@host'))), \
                 patch.object(host, 'ssh_run', side_effect=AssertionError('do not execute')):
                with self.assertRaisesRegex(ValueError, 'CONFIG_PARENT_REPAIR_SOURCE_UNVERIFIED'):
                    host.execute('config_parent_repair', repository)


if __name__ == '__main__':
    unittest.main()
