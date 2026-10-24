"""Adversarial, off-host tests for Deploy V2 serialization and staging."""
import io
import errno
import json
from contextlib import redirect_stderr, redirect_stdout
import os
from pathlib import Path
import signal
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
        self.barrier = self.root / mutex.BARRIER_NAME

    def args(self, phase, command=None):
        return ['nobody', 'root', '--attempt', SHA, RUN, ATTEMPT,
                '--phase', phase, *(command or ['echo', 'CLOSED'])]

    def mock_root(self):
        fake_user = SimpleNamespace(pw_uid=1, pw_gid=1, pw_name='nobody', pw_dir='/tmp')
        read_barrier = mutex._read_barrier
        open_lock = mutex.open_lock
        return (patch.object(mutex.os, 'geteuid', return_value=0),
                patch.object(mutex.pwd, 'getpwnam', return_value=fake_user),
                patch.object(mutex, 'LOCK_DIR', self.root),
                patch.object(mutex, 'open_lock', side_effect=lambda p: open_lock(p, os.getuid())),
                patch.object(mutex, '_read_barrier',
                             side_effect=lambda p: read_barrier(p, os.getuid())))

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
        try:
            from contextlib import ExitStack
            with ExitStack() as stack:
                for mocked in self.mock_root(): stack.enter_context(mocked)
                for operation in ('preclean', 'suspend', 'drain', 'transfer',
                                  'promote', 'failure_cleanup'):
                    marker = self.root / operation
                    with patch.object(mutex.sys, 'argv', ['mutex', *self.args('promotion',
                                       ['python3', '-c',
                                        f'from pathlib import Path;Path({str(marker)!r}).touch()'])]):
                        result = mutex.main()
                    self.assertEqual(result, 75, operation)
                    self.assertFalse(marker.exists(), operation)
        finally:
            os.close(owner)

    def test_orphan_docker_writer_blocks_new_mutation_after_flock_releases(self):
        from contextlib import ExitStack
        marker = self.root / 'mutated'
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            docker = stack.enter_context(patch.object(mutex.subprocess, 'run',
                return_value=SimpleNamespace(returncode=0, stdout='porota-deploy-contract-' + SHA + '\n')))
            with self.assertRaisesRegex(RuntimeError, 'CONTRACT_WRITER_ORPHAN_RUNNING'):
                mutex.run(self.args('promotion', ['python3', '-c',
                           f'from pathlib import Path;Path({str(marker)!r}).touch()']))
        self.assertFalse(marker.exists())
        self.assertFalse(self.barrier.exists())
        self.assertEqual(docker.call_count, 1)

    def test_lock_evidence_does_not_pollute_exact_child_stdout(self):
        from contextlib import ExitStack
        out, err = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            stack.enter_context(patch.object(mutex.subprocess, 'run', side_effect=[
                SimpleNamespace(returncode=0, stdout=''), SimpleNamespace(returncode=0)]))
            stack.enter_context(redirect_stdout(out)); stack.enter_context(redirect_stderr(err))
            self.assertEqual(mutex.run(self.args('disk_probe')), 0)
        self.assertEqual(out.getvalue(), '')
        self.assertIn('RC6_DEPLOY_HOST_MUTEX=HELD', err.getvalue())
        self.assertEqual(json.loads(self.barrier.read_text())['status'], 'IDLE')

    def test_same_attempt_chains_and_finalizes_but_other_attempt_cannot_enter(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            def successful(cmd, **kwargs):
                return SimpleNamespace(returncode=0, stdout='')
            stack.enter_context(patch.object(mutex.subprocess, 'run', side_effect=successful))
            self.assertEqual(mutex.run(self.args('disk_probe')), 0)
            self.assertEqual(json.loads(self.barrier.read_text())['status'], 'IDLE')
            with self.assertRaisesRegex(RuntimeError, 'BARRIER_OTHER_ATTEMPT'):
                mutex.run(['nobody', 'root', '--attempt', SHA, '9876', ATTEMPT,
                           '--phase', 'promotion', 'true'])
            self.assertEqual(mutex.run(self.args('promotion')), 0)
            self.assertEqual(mutex.run(self.args('finalize')), 0)
            self.assertFalse(self.barrier.exists())

    def test_failed_child_keeps_barrier_and_blocks_cleanup_same_attempt(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            stack.enter_context(patch.object(mutex.subprocess, 'run', side_effect=[
                SimpleNamespace(returncode=0, stdout=''), SimpleNamespace(returncode=9)]))
            self.assertEqual(mutex.run(self.args('suspend')), 9)
            self.assertEqual(json.loads(self.barrier.read_text())['status'], 'INFLIGHT')
            with self.assertRaisesRegex(RuntimeError, 'BARRIER_PRIOR_OPERATION_UNCERTAIN'):
                mutex.run(self.args('cleanup_recover'))

    def test_sudo_like_child_closes_fd_then_parent_lost_keeps_barrier(self):
        # Synthetic SSH loss: the child retains no flock fd; the marker alone
        # prevents a second writer while that exact child is alive.
        code = ('import os,subprocess,sys;from pathlib import Path;'
                'from scripts import rc6_deploy_host_mutex as m;'
                'root=Path(sys.argv[1]);fd=m.open_lock(root,os.getuid());'
                'm._enter(root,(sys.argv[2],sys.argv[3],sys.argv[4]),"promotion");'
                'p=subprocess.Popen([sys.executable,"-c",'
                '"import os,time,sys;open(sys.argv[1],\'w\').write(str(os.getpid()));time.sleep(10)",'
                'sys.argv[5]],close_fds=True);os.close(fd);os._exit(0)')
        pid_path = self.root / 'nested.pid'
        parent = subprocess.run(['python3', '-c', code, str(self.root), SHA, RUN,
                                 ATTEMPT, str(pid_path)], check=False, timeout=3)
        self.assertEqual(parent.returncode, 0)
        try:
            for _ in range(100):
                if pid_path.exists(): break
                import time; time.sleep(.01)
            child_pid = int(pid_path.read_text())
            os.kill(child_pid, 0)
            self.assertNotEqual((Path('/proc') / str(child_pid) / 'stat').read_text()
                                .rsplit(')', 1)[1].split()[0], 'Z')
            successor = self.acquire()  # fd was closed even though child lives
            try:
                with self.assertRaisesRegex(RuntimeError, 'BARRIER_PRIOR_OPERATION_UNCERTAIN'):
                    mutex._enter(self.root, (SHA, RUN, ATTEMPT), 'cleanup_recover')
                with self.assertRaisesRegex(RuntimeError, 'BARRIER_OTHER_ATTEMPT'):
                    mutex._enter(self.root, (SHA, '9876', ATTEMPT), 'promotion')
            finally:
                os.close(successor)
        finally:
            if pid_path.exists():
                os.kill(int(pid_path.read_text()), signal.SIGKILL)

    def test_barrier_symlink_hardlink_corrupt_and_replaced_fail_closed(self):
        self.barrier.symlink_to(self.root / 'outside')
        with self.assertRaises(OSError): mutex._read_barrier(self.root, os.getuid())
        self.barrier.unlink()
        mutex._write_barrier(self.root, dict(candidate=SHA, run=RUN, attempt=ATTEMPT,
                                              phase='promotion', status='INFLIGHT'))
        alias = self.root / 'alias'
        os.link(self.barrier, alias)
        with self.assertRaisesRegex(RuntimeError, 'BARRIER_ALIAS_UNSAFE'):
            mutex._read_barrier(self.root, os.getuid())
        alias.unlink()
        self.barrier.write_text('{')
        with self.assertRaisesRegex(RuntimeError, 'BARRIER_INVALID'):
            mutex._read_barrier(self.root, os.getuid())

    def test_incomplete_write_and_inode_swap_fail_before_child(self):
        state = dict(candidate=SHA, run=RUN, attempt=ATTEMPT,
                     phase='promotion', status='INFLIGHT')
        with patch.object(mutex.os, 'fsync', side_effect=OSError(errno.ENOSPC, 'full')):
            with self.assertRaises(OSError): mutex._write_barrier(self.root, state)
        self.assertFalse(self.barrier.exists())
        mutex._write_barrier(self.root, state)
        original = mutex.os.lstat
        calls = [0]
        def replace_at_second_stat(path):
            if path == self.barrier:
                calls[0] += 1
                if calls[0] == 2:
                    self.barrier.unlink()
                    mutex._write_barrier(self.root, state)
            return original(path)
        with patch.object(mutex.os, 'lstat', side_effect=replace_at_second_stat):
            with self.assertRaisesRegex(RuntimeError, 'BARRIER_REPLACED'):
                mutex._read_barrier(self.root, os.getuid())

    def test_timeout_leaves_inflight_and_classification_rejects_it(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            stack.enter_context(patch.object(mutex.subprocess, 'run', side_effect=[
                SimpleNamespace(returncode=0, stdout=''),
                subprocess.TimeoutExpired(['synthetic'], 1)]))
            with self.assertRaises(subprocess.TimeoutExpired):
                mutex.run(self.args('preclean'))
            self.assertEqual(json.loads(self.barrier.read_text())['status'], 'INFLIGHT')
            with self.assertRaisesRegex(RuntimeError, 'BARRIER_PRIOR_ATTEMPT_UNRESOLVED'):
                mutex.run(self.args('classify'))


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
        classify = next(step['run'] for step in steps
                        if step['name'] == 'Classify current flat PAPER session before host writes')
        self.assertIn('RC6_MUTEX_B64', classify)
        self.assertIn('PYTHONDONTWRITEBYTECODE=1', classify)
        for key in ('diskguard', 'suspend', 'drain', 'transfer', 'promotion',
                    'timers_restore', 'timers_restored', 'backup_retire',
                    'finalize_runtime'):
            self.assertIn('RC6_MUTEX_B64', by_id[key], key)
        self.assertEqual(by_id['flat_book'].count('RC6_MUTEX_B64'), 2)
        failure = next(step['run'] for step in steps
                       if step['name'] == 'Failure-safe host cleanup')
        self.assertEqual(failure.count('RC6_MUTEX_B64'), 4)
        boundaries = [line for line in workflow.splitlines()
                      if 'RC6_MUTEX_B64' in line and 'sudo -n python3 -c' in line]
        self.assertEqual(len(boundaries), 17)
        self.assertTrue(all("--attempt '$CANDIDATE_SHA' '$GITHUB_RUN_ID' "
                            "'$GITHUB_RUN_ATTEMPT' --phase " in line
                            for line in boundaries))
        self.assertEqual(sum('--phase classify ' in line for line in boundaries), 1)
        self.assertEqual(sum('--phase finalize ' in line for line in boundaries), 1)
        self.assertEqual(sum('--phase cleanup_final ' in line for line in boundaries), 1)
        self.assertEqual(failure.count('--allow-contract-lifecycle'), 1)
        self.assertIn('RC6_FAILURE_CLEANUP_MUTEX_OR_WRITER=RED', failure)
        self.assertIn('RC6_FAILURE_RECOVERY=RED', failure)
        self.assertIn("--run-id '$GITHUB_RUN_ID' --attempt '$GITHUB_RUN_ATTEMPT'", failure)
        self.assertIn('tar -C /tmp -cf -', by_id['transfer'])
        self.assertNotIn('scp ', by_id['transfer'])
        self.assertNotIn("find /tmp -maxdepth 1 -type d -name 'porota-deploy-v2-*'", workflow)
        self.assertNotIn("rm -rf '$REMOTE_DIR'", by_id['transfer'])
        self.assertIn('$CANDIDATE_SHA-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT', by_id['promotion'])
        self.assertNotIn("! -path \"$REMOTE_DIR\" -exec rm -rf", by_id['promotion'])


if __name__ == '__main__':
    unittest.main()
