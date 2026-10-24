"""Adversarial, off-host tests for Deploy V2 serialization and staging."""
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import yaml

from scripts import rc6_deploy_host_mutex as mutex
from scripts import rc6_deploy_stage_transfer as stage

SHA = 'a' * 40
RUN = '1234'
ATTEMPT = '2'


class HostMutexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rc6-mutex-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.lock = self.root / mutex.LOCK_PATH.name

    def acquire(self):
        return mutex.open_lock(self.root, os.geteuid())

    def test_orphan_holds_inode_after_parent_loses_fd_and_second_attempt_cannot_mutate(self):
        first = self.acquire()
        reader, writer = os.pipe()
        child = subprocess.Popen(['python3', '-c', f'import os;os.read({reader},1)'],
                                 pass_fds=(first, reader))
        os.close(reader)
        try:
            os.close(first)  # synthetic SSH/wrapper termination
            with self.assertRaisesRegex(RuntimeError, 'MUTEX_BUSY'):
                self.acquire()
            os.write(writer, b'1')
            self.assertEqual(child.wait(timeout=3), 0)
            successor = self.acquire()
            os.close(successor)
        finally:
            os.close(writer)
            if child.poll() is None:
                child.kill(); child.wait(timeout=3)

    def test_symlink_hardlink_and_replaced_inode_fail_closed(self):
        self.lock.symlink_to(self.root / 'target')
        with self.assertRaises(OSError):
            self.acquire()
        self.lock.unlink()
        self.lock.write_bytes(b'')
        os.chmod(self.lock, 0o600)
        alias = self.root / 'alias'
        os.link(self.lock, alias)
        with self.assertRaisesRegex(RuntimeError, 'MUTEX_ALIAS_UNSAFE'):
            self.acquire()
        alias.unlink()
        original_lstat = mutex.os.lstat
        count = [0]
        def swap_after_open(path):
            count[0] += 1
            if path == self.lock and count[0] == 3:
                self.lock.unlink()
                self.lock.write_bytes(b'')
                os.chmod(self.lock, 0o600)
            return original_lstat(path)
        # The first lstat reads the parent, second verifies the open inode,
        # third checks again after flock before any command is allowed.
        with patch.object(mutex.os, 'lstat', side_effect=swap_after_open):
            with self.assertRaisesRegex(RuntimeError, 'MUTEX_REPLACED'):
                self.acquire()

    def test_bad_parent_and_mode_rejected(self):
        os.chmod(self.root, 0o777)
        with self.assertRaisesRegex(RuntimeError, 'MUTEX_PARENT_UNSAFE'):
            self.acquire()
        os.chmod(self.root, 0o700)
        self.lock.write_bytes(b'')
        os.chmod(self.lock, 0o666)
        with self.assertRaisesRegex(RuntimeError, 'MUTEX_ALIAS_UNSAFE'):
            self.acquire()

    def test_busy_mutex_blocks_each_host_operation_before_side_effect(self):
        owner = self.acquire()
        fake_user = SimpleNamespace(pw_uid=1, pw_gid=1, pw_name='nobody', pw_dir='/tmp')
        real_open = mutex.open_lock
        try:
            with patch.object(mutex.os, 'geteuid', return_value=0), \
                 patch.object(mutex.pwd, 'getpwnam', return_value=fake_user), \
                 patch.object(mutex, 'open_lock', side_effect=lambda: real_open(self.root, os.getuid())):
                for operation in ('preclean', 'suspend', 'drain', 'transfer',
                                  'promote', 'failure_cleanup'):
                    marker = self.root / operation
                    with patch.object(mutex.sys, 'argv', ['mutex', 'nobody', 'root',
                                       'python3', '-c',
                                       f'from pathlib import Path;Path({str(marker)!r}).touch()']):
                        result = mutex.main()
                    self.assertEqual(result, 75, operation)
                    self.assertFalse(marker.exists(), operation)
        finally:
            os.close(owner)

    def test_orphan_docker_writer_blocks_new_mutation_after_flock_releases(self):
        fake_user = SimpleNamespace(pw_uid=1, pw_gid=1, pw_name='nobody', pw_dir='/tmp')
        real_open = mutex.open_lock
        marker = self.root / 'mutated'
        with patch.object(mutex.os, 'geteuid', return_value=0), \
             patch.object(mutex.pwd, 'getpwnam', return_value=fake_user), \
             patch.object(mutex, 'open_lock', side_effect=lambda: real_open(self.root, os.getuid())), \
             patch.object(mutex.subprocess, 'run', return_value=SimpleNamespace(
                 returncode=0, stdout='porota-deploy-contract-' + SHA + '\n')) as docker:
            with self.assertRaisesRegex(RuntimeError, 'CONTRACT_WRITER_ORPHAN_RUNNING'):
                mutex.run(['nobody', 'root', 'python3', '-c',
                           f'from pathlib import Path;Path({str(marker)!r}).touch()'])
        self.assertFalse(marker.exists())
        self.assertEqual(docker.call_count, 1)


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rc6-transfer-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.root_patch = patch.object(stage, 'ROOT', self.root)
        self.root_patch.start(); self.addCleanup(self.root_patch.stop)

    def archive(self, names=None, symlink=False):
        sink = io.BytesIO()
        with tarfile.open(fileobj=sink, mode='w') as out:
            for name in names if names is not None else sorted(stage.NAMES):
                data = b'synthetic receipt'
                item = tarfile.TarInfo(name)
                item.size = len(data)
                if symlink:
                    item.type = tarfile.SYMTYPE
                    item.linkname = '/etc/passwd'
                out.addfile(item, io.BytesIO(data) if not symlink else None)
        sink.seek(0)
        return sink

    def test_exact_attempt_namespace_and_contents(self):
        target = stage.attempt_dir(SHA, RUN, ATTEMPT)
        stage.receive(target, self.archive())
        self.assertEqual({p.name for p in target.iterdir()}, stage.NAMES)
        with self.assertRaisesRegex(ValueError, 'ALREADY_PRESENT'):
            stage.receive(target, self.archive())
        self.assertNotEqual(target, stage.attempt_dir(SHA, RUN, '3'))

    def test_duplicate_missing_symlink_or_invalid_identity_rejected(self):
        with self.assertRaisesRegex(ValueError, 'STAGING_IDENTITY_INVALID'):
            stage.attempt_dir(SHA, '../1234', ATTEMPT)
        for names, symlink in ((['porota-frozen-candidate.json'], False),
                               (['porota-frozen-candidate.json'] * 2, False),
                               (['porota-frozen-candidate.json'], True)):
            target = stage.attempt_dir(SHA, RUN, str(ATTEMPT) + str(len(list(self.root.iterdir())) + 1))
            with self.assertRaises(ValueError):
                stage.receive(target, self.archive(names, symlink))

    def test_attempt_stage_symlink_does_not_follow(self):
        outside = self.root / 'outside'
        outside.mkdir()
        target = stage.attempt_dir(SHA, RUN, ATTEMPT)
        target.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'STAGING_PATH_UNSAFE'):
            stage.receive(target, self.archive())
        self.assertEqual(list(outside.iterdir()), [])


class WorkflowIntegrationTests(unittest.TestCase):
    def test_all_mutation_boundaries_use_same_mutex_and_attempt_stage(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    '.github/workflows/porota-deploy-v2-promote.yml').read_text()
        steps = yaml.safe_load(workflow)['jobs']['promote']['steps']
        by_id = {step.get('id'): step.get('run', '') for step in steps}
        for key in ('diskguard', 'suspend', 'drain', 'transfer', 'promotion',
                    'timers_restore', 'backup_retire', 'finalize_runtime'):
            self.assertIn('RC6_MUTEX_B64', by_id[key], key)
        failure = next(step['run'] for step in steps
                       if step['name'] == 'Failure-safe host cleanup')
        self.assertEqual(failure.count('RC6_MUTEX_B64'), 4)
        self.assertEqual(failure.count('--allow-contract-lifecycle'), 1)
        self.assertIn('RC6_FAILURE_CLEANUP_MUTEX_OR_WRITER=RED', failure)
        self.assertIn('RC6_FAILURE_RECOVERY=RED', failure)
        self.assertIn("--run-id '$GITHUB_RUN_ID' --attempt '$GITHUB_RUN_ATTEMPT'", failure)
        self.assertIn('tar -C /tmp -cf -', by_id['transfer'])
        self.assertNotIn('scp ', by_id['transfer'])
        self.assertNotIn("rm -rf '$REMOTE_DIR'", by_id['transfer'])
        self.assertIn('$CANDIDATE_SHA-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT', by_id['promotion'])
        self.assertNotIn("! -path \"$REMOTE_DIR\" -exec rm -rf", by_id['promotion'])


if __name__ == '__main__':
    unittest.main()
