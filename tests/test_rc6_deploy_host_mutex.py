"""Adversarial, off-host tests for Deploy V2 serialization and staging."""
import io
import errno
import json
from contextlib import redirect_stderr, redirect_stdout
import os
from pathlib import Path
import signal
import subprocess
import sys
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
                # Legacy lifecycle algorithm coverage ONLY. Default entry
                # veto is exercised separately by A865ContainmentTests.
                patch.object(mutex, 'require_migrated_consumer_protocol'),
                patch.object(mutex.pwd, 'getpwnam', return_value=fake_user),
                patch.object(mutex, '_enable_subreaper'),
                patch.object(mutex, '_drain_children'),
                patch.object(mutex, 'LOCK_DIR', self.root),
                patch.object(mutex, 'STAGING_DIR', self.root),
                patch.object(mutex, 'open_lock', side_effect=lambda p: open_lock(p, os.getuid())),
                patch.object(mutex, '_read_barrier',
                             side_effect=lambda p: read_barrier(p, os.getuid())))

    def acquire(self):
        return mutex.open_lock(self.root, os.geteuid())

    def test_dormant_orphan_algorithm_never_delegates_owner_or_ambient_claims(self):
        from contextlib import ExitStack
        calls = []
        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return SimpleNamespace(returncode=0, stdout='')
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            stack.enter_context(patch.object(mutex.subprocess, 'run', side_effect=fake_run))
            self.assertEqual(mutex.run(self.args('orphan_gc',
                ['python3', '-B', '-'])), 0)
            self.assertFalse(self.barrier.exists())
            owner_calls = [(cmd, kwargs) for cmd, kwargs in calls
                           if cmd == ['python3', '-B', '-']]
            self.assertEqual(len(owner_calls), 1)
            self.assertNotIn('RC6_DEPLOY_MUTEX_PROOF', owner_calls[0][1]['env'])
            self.assertNotIn('RC6_DEPLOY_MUTEX_FD', owner_calls[0][1]['env'])
            self.assertEqual(owner_calls[0][1]['pass_fds'], ())

    def test_real_child_cannot_unlock_private_owner_even_if_veto_later_migrates(self):
        from contextlib import ExitStack
        owner = self.acquire()
        output = self.root / 'child-observation.json'
        code = '''import fcntl,json,os,sys
slot=int(os.environ['A865_TEST_OWNER_SLOT'])
try:
 fcntl.flock(slot,fcntl.LOCK_UN); usable=True
except OSError:
 usable=False
probe=os.open(sys.argv[1],os.O_RDONLY)
try:
 try:
  fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB); acquired=True
 except BlockingIOError:
  acquired=False
finally:
 os.close(probe)
with open(sys.argv[2],'w') as handle:
 json.dump({'owner_usable':usable,'probe_acquired':acquired,
            'legacy_fd':'RC6_DEPLOY_MUTEX_FD' in os.environ,
            'legacy_proof':'RC6_DEPLOY_MUTEX_PROOF' in os.environ},handle)
'''
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            stack.enter_context(patch.object(mutex, 'open_lock', return_value=owner))
            stack.enter_context(patch.object(mutex, '_contract_writers', return_value=set()))
            stack.enter_context(patch.dict(os.environ, {
                'A865_TEST_OWNER_SLOT': str(owner),
                'RC6_DEPLOY_MUTEX_FD': str(owner),
                'RC6_DEPLOY_MUTEX_PROOF': 'untrusted ambient claim'}))
            self.assertEqual(mutex.run(self.args('classify',
                [sys.executable, '-B', '-c', code, str(self.lock), str(output)])), 0)
        self.assertEqual(json.loads(output.read_text()), {
            'owner_usable': False, 'probe_acquired': False,
            'legacy_fd': False, 'legacy_proof': False})

    def test_a779_orphan_gc_requires_no_prior_barrier_and_exact_command(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            stack.enter_context(patch.object(mutex.subprocess, 'run'))
            with self.assertRaisesRegex(RuntimeError, 'ORPHAN_GC_COMMAND_INVALID'):
                mutex.run(self.args('orphan_gc', ['true']))
            self.barrier.write_text(json.dumps({'candidate': SHA, 'run': RUN,
                'attempt': ATTEMPT, 'phase': 'cleanup_final', 'status': 'IDLE'}))
            self.barrier.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError, 'ORPHAN_GC_BARRIER_RETAINED'):
                mutex.run(self.args('orphan_gc', ['python3', '-B', '-']))

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
                SimpleNamespace(returncode=0, stdout=''), SimpleNamespace(returncode=0),
                SimpleNamespace(returncode=0, stdout='')]))
            stack.enter_context(redirect_stdout(out)); stack.enter_context(redirect_stderr(err))
            self.assertEqual(mutex.run(self.args('classify')), 0)
        self.assertEqual(out.getvalue(), '')
        self.assertIn('RC6_DEPLOY_HOST_MUTEX=HELD', err.getvalue())
        self.assertFalse(self.barrier.exists())  # read-only classification

    def test_offline_image_cleanup_timeout_keeps_durable_barrier(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            stack.enter_context(patch.object(mutex, '_contract_writers', return_value=set()))
            stack.enter_context(patch.object(mutex.subprocess, 'run',
                                            side_effect=subprocess.TimeoutExpired(['docker'], 1)))
            with self.assertRaises(subprocess.TimeoutExpired):
                mutex.run(self.args('offline_preclean'))
            self.assertEqual(json.loads(self.barrier.read_text())['status'], 'INFLIGHT')
            with self.assertRaisesRegex(RuntimeError, 'BARRIER_PRIOR_OPERATION_UNCERTAIN'):
                mutex.run(self.args('offline_preclean'))

    def test_same_attempt_chains_and_finalizes_but_other_attempt_cannot_enter(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            def successful(cmd, **kwargs):
                return SimpleNamespace(returncode=0, stdout='')
            stack.enter_context(patch.object(mutex.subprocess, 'run', side_effect=successful))
            self.assertEqual(mutex.run(self.args('suspend')), 0)
            self.assertEqual(json.loads(self.barrier.read_text())['status'], 'IDLE')
            with self.assertRaisesRegex(RuntimeError, 'BARRIER_OTHER_ATTEMPT'):
                mutex.run(['nobody', 'root', '--attempt', SHA, '9876', ATTEMPT,
                           '--phase', 'promotion', 'true'])
            self.assertEqual(mutex.run(self.args('promotion')), 0)
            self.assertEqual(mutex.run(self.args('finalize')), 0)
            self.assertFalse(self.barrier.exists())

    def test_payload_cleanup_never_retires_barrier_or_passes_uncertain_writer(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            writer = stack.enter_context(patch.object(mutex, '_contract_writers',
                                                      return_value=set()))
            child = stack.enter_context(patch.object(mutex.subprocess, 'run',
                return_value=SimpleNamespace(returncode=0, stdout='')))
            self.assertEqual(mutex.run(self.args('promotion')), 0)
            self.assertEqual(mutex.run(self.args('cleanup_payload')), 0)
            self.assertEqual(json.loads(self.barrier.read_text())['status'], 'IDLE')
            self.assertEqual(json.loads(self.barrier.read_text())['phase'], 'cleanup_payload')
            before = child.call_count
            state = json.loads(self.barrier.read_text())
            state['status'] = 'INFLIGHT'
            self.barrier.write_text(json.dumps(state))
            with self.assertRaisesRegex(RuntimeError, 'BARRIER_PRIOR_OPERATION_UNCERTAIN'):
                mutex.run(self.args('cleanup_payload'))
            self.assertEqual(child.call_count, before)
            state['status'] = 'IDLE'
            self.barrier.write_text(json.dumps(state))
            writer.return_value = {'porota-deploy-contract-' + SHA}
            with self.assertRaisesRegex(RuntimeError, 'CONTRACT_WRITER_ORPHAN_RUNNING'):
                mutex.run(self.args('cleanup_payload'))
            self.assertEqual(child.call_count, before)

    def test_only_own_idle_preclean_barrier_can_be_retired_without_scheduler_action(self):
        from contextlib import ExitStack
        proof_command = ['python3', '-', '--policy-b64', 'fixture',
                         '--expected-fingerprint', 'f' * 64]
        calls = []
        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout='')
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            stack.enter_context(patch.object(mutex.subprocess, 'run', side_effect=fake_run))
            self.assertEqual(mutex.run(self.args('preclean')), 0)
            self.assertEqual(json.loads(self.barrier.read_text())['status'], 'IDLE')
            with self.assertRaisesRegex(RuntimeError, 'PRESUSPEND_BARRIER_NOT_EXACT_IDLE'):
                mutex.run(['nobody','root','--attempt',SHA,'9999',ATTEMPT,
                           '--phase','cleanup_presuspend',*proof_command])
            self.assertTrue(self.barrier.exists())
            self.assertEqual(mutex.run(self.args('cleanup_presuspend', proof_command)), 0)
            self.assertFalse(self.barrier.exists())
            self.assertTrue(all(cmd[0] in {'docker', 'echo', 'python3'} for cmd in calls))
            self.assertEqual(sum(cmd == proof_command for cmd in calls), 1)

    def test_presuspend_rejects_inflight_or_post_suspend_barriers(self):
        from contextlib import ExitStack
        proof_command = ['python3', '-', '--policy-b64', 'fixture',
                         '--expected-fingerprint', 'f' * 64]
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            stack.enter_context(patch.object(mutex.subprocess, 'run',
                                             return_value=SimpleNamespace(returncode=0, stdout='')))
            self.assertEqual(mutex.run(self.args('suspend')), 0)
            with self.assertRaisesRegex(RuntimeError, 'PRESUSPEND_BARRIER_NOT_EXACT_IDLE'):
                mutex.run(self.args('cleanup_presuspend', proof_command))
            self.assertEqual(json.loads(self.barrier.read_text())['phase'], 'suspend')
            state = json.loads(self.barrier.read_text()); state['phase'] = 'preclean'; state['status'] = 'INFLIGHT'
            self.barrier.write_text(json.dumps(state))
            with self.assertRaisesRegex(RuntimeError, 'PRESUSPEND_BARRIER_NOT_EXACT_IDLE'):
                mutex.run(self.args('cleanup_presuspend', proof_command))
            self.assertTrue(self.barrier.exists())

    def test_presuspend_failed_proof_keeps_barrier_for_exact_retry(self):
        from contextlib import ExitStack
        proof_command = ['python3', '-', '--policy-b64', 'fixture',
                         '--expected-fingerprint', 'f' * 64]
        proof_calls = [0]
        def fake_run(cmd, **kwargs):
            if cmd[0] == 'docker': return SimpleNamespace(returncode=0, stdout='')
            if cmd == proof_command:
                proof_calls[0] += 1
                return SimpleNamespace(returncode=2 if proof_calls[0] == 1 else 0)
            return SimpleNamespace(returncode=0)
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            stack.enter_context(patch.object(mutex.subprocess, 'run', side_effect=fake_run))
            self.assertEqual(mutex.run(self.args('offline_preclean')), 0)
            self.assertEqual(mutex.run(self.args('cleanup_presuspend', proof_command)), 2)
            self.assertEqual(json.loads(self.barrier.read_text())['status'], 'IDLE')
            self.assertEqual(json.loads(self.barrier.read_text())['phase'], 'cleanup_presuspend')
            self.assertEqual(mutex.run(self.args('cleanup_presuspend', proof_command)), 0)
            self.assertFalse(self.barrier.exists())

    def test_presuspend_no_barrier_still_verifies_host_without_creating_one(self):
        from contextlib import ExitStack
        proof_command = ['python3', '-', '--policy-b64', 'fixture',
                         '--expected-fingerprint', 'f' * 64]
        commands = []
        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return SimpleNamespace(returncode=0, stdout='')
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            stack.enter_context(patch.object(mutex.subprocess, 'run', side_effect=fake_run))
            self.assertEqual(mutex.run(self.args('cleanup_presuspend', proof_command)), 0)
            self.assertFalse(self.barrier.exists())
            self.assertEqual(sum(cmd == proof_command for cmd in commands), 1)

    def test_presuspend_cannot_retire_barrier_when_transfer_stage_exists(self):
        from contextlib import ExitStack
        proof_command = ['python3', '-', '--policy-b64', 'fixture',
                         '--expected-fingerprint', 'f' * 64]
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            stack.enter_context(patch.object(mutex.subprocess, 'run',
                                             return_value=SimpleNamespace(returncode=0, stdout='')))
            self.assertEqual(mutex.run(self.args('preclean')), 0)
            stage = self.root / f'porota-deploy-v2-{SHA}-{RUN}-{ATTEMPT}'
            stage.mkdir()
            with self.assertRaisesRegex(RuntimeError, 'PRESUSPEND_TRANSFER_STAGE_PRESENT'):
                mutex.run(self.args('cleanup_presuspend', proof_command))
            self.assertEqual(json.loads(self.barrier.read_text())['phase'], 'preclean')

    def test_failed_child_keeps_barrier_and_blocks_cleanup_same_attempt(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            stack.enter_context(patch.object(mutex.subprocess, 'run', side_effect=[
                SimpleNamespace(returncode=0, stdout=''), SimpleNamespace(returncode=9)]))
            stack.enter_context(patch.object(mutex, '_drain_children',
                                             side_effect=RuntimeError('MUTEX_DESCENDANT_STILL_RUNNING')))
            with self.assertRaisesRegex(RuntimeError, 'MUTEX_DESCENDANT_STILL_RUNNING'):
                mutex.run(self.args('suspend'))
            self.assertEqual(json.loads(self.barrier.read_text())['status'], 'INFLIGHT')
            with self.assertRaisesRegex(RuntimeError, 'BARRIER_PRIOR_OPERATION_UNCERTAIN'):
                mutex.run(self.args('cleanup_recover'))

    def test_clean_nonzero_allows_same_attempt_failure_recovery(self):
        from contextlib import ExitStack
        child_calls = [0]
        def fake_run(cmd, **kwargs):
            if cmd[0] == 'docker':
                return SimpleNamespace(returncode=0, stdout='')
            child_calls[0] += 1
            return SimpleNamespace(returncode=9 if child_calls[0] == 1 else 0)
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            stack.enter_context(patch.object(mutex.subprocess, 'run', side_effect=fake_run))
            self.assertEqual(mutex.run(self.args('suspend')), 9)
            self.assertEqual(json.loads(self.barrier.read_text())['status'], 'IDLE')
            self.assertEqual(mutex.run(['nobody', 'user', '--attempt', SHA, RUN,
                ATTEMPT, '--phase', 'cleanup_contract', '--allow-contract-lifecycle',
                'true']), 0)
            self.assertEqual(mutex.run(self.args('cleanup_recover')), 0)
            self.assertEqual(mutex.run(self.args('cleanup_restore')), 0)
            self.assertEqual(mutex.run(self.args('cleanup_final')), 0)
            self.assertFalse(self.barrier.exists())

    def test_only_exact_orphan_contract_can_be_stopped_before_recovery(self):
        from contextlib import ExitStack
        calls = [0]
        def fake_run(cmd, **kwargs):
            calls[0] += 1
            if cmd[0] == 'docker':
                # The promotion created exactly its named contract; after
                # cleanup_contract the inventory becomes empty.
                value = 'porota-deploy-contract-' + SHA + '\n' if calls[0] == 3 else ''
                return SimpleNamespace(returncode=0, stdout=value)
            return SimpleNamespace(returncode=1 if calls[0] == 2 else 0)
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            stack.enter_context(patch.object(mutex.subprocess, 'run', side_effect=fake_run))
            with self.assertRaisesRegex(RuntimeError, 'CONTRACT_WRITER_REQUIRES_EXACT_STOP'):
                mutex.run(self.args('promotion'))
            self.assertEqual(json.loads(self.barrier.read_text())['status'], 'CONTRACT_ONLY')
            with self.assertRaisesRegex(RuntimeError, 'BARRIER_PRIOR_OPERATION_UNCERTAIN'):
                mutex.run(self.args('cleanup_recover'))
            with self.assertRaisesRegex(RuntimeError, 'BARRIER_OTHER_ATTEMPT'):
                mutex.run(['nobody', 'root', '--attempt', SHA, '9876', ATTEMPT,
                           '--phase', 'promotion', 'true'])
            self.assertEqual(mutex.run(['nobody', 'user', '--attempt', SHA, RUN,
                ATTEMPT, '--phase', 'cleanup_contract', '--allow-contract-lifecycle',
                'true']), 0)
            self.assertEqual(json.loads(self.barrier.read_text())['status'], 'IDLE')
            self.assertEqual(mutex.run(self.args('cleanup_final')), 0)
            self.assertFalse(self.barrier.exists())

    def test_sudo_like_child_closes_fd_then_parent_lost_keeps_barrier(self):
        # Synthetic SSH loss: the child retains no flock fd; the marker alone
        # prevents a second writer while that exact child is alive.
        code = ('import os,subprocess,sys;from pathlib import Path;'
                'from scripts import rc6_deploy_host_mutex as m;'
                'root=Path(sys.argv[1]);fd=m.open_lock(root,os.getuid());'
                'm._enter(root,(sys.argv[2],sys.argv[3],sys.argv[4]),"promotion");'
                'p=subprocess.Popen([sys.executable,"-c",'
                '"import os,time,sys;from pathlib import Path;Path(sys.argv[1]).write_text(str(os.getpid()));[(Path(sys.argv[2]).write_text(str(i)),time.sleep(.02)) for i in range(500)]",'
                'sys.argv[5],sys.argv[6]],close_fds=True);os.close(fd);os._exit(0)')
        pid_path = self.root / 'nested.pid'
        heartbeat = self.root / 'nested.heartbeat'
        parent = subprocess.run(['python3', '-c', code, str(self.root), SHA, RUN,
                                 ATTEMPT, str(pid_path), str(heartbeat)], check=False, timeout=3)
        self.assertEqual(parent.returncode, 0)
        try:
            for _ in range(100):
                if pid_path.exists(): break
                import time; time.sleep(.01)
            child_pid = int(pid_path.read_text())
            os.kill(child_pid, 0)
            for _ in range(100):
                if heartbeat.exists(): break
                import time; time.sleep(.01)
            before = heartbeat.read_text()
            for _ in range(100):
                if heartbeat.read_text() != before: break
                import time; time.sleep(.01)
            self.assertNotEqual(heartbeat.read_text(), before)  # live child
            successor = self.acquire()  # fd was closed even though child lives
            try:
                real_read = mutex._read_barrier
                with patch.object(mutex, '_read_barrier',
                                  side_effect=lambda p: real_read(p, os.getuid())):
                    with self.assertRaisesRegex(RuntimeError, 'BARRIER_PRIOR_OPERATION_UNCERTAIN'):
                        mutex._enter(self.root, (SHA, RUN, ATTEMPT), 'cleanup_recover')
                    with self.assertRaisesRegex(RuntimeError, 'BARRIER_OTHER_ATTEMPT'):
                        mutex._enter(self.root, (SHA, '9876', ATTEMPT), 'promotion')
            finally:
                os.close(successor)
        finally:
            if pid_path.exists():
                os.kill(int(pid_path.read_text()), signal.SIGKILL)

    def test_live_adopted_grandchild_blocks_normal_failed_exit_until_it_finishes(self):
        # Exercise the real Linux subreaper in an isolated process so pytest
        # cannot adopt unrelated children from another test.
        middle = ('import subprocess,sys;subprocess.Popen([sys.executable,'
                  '"-c","import time;time.sleep(.3)"],close_fds=True)')
        code = (f'import subprocess,sys\nfrom scripts import rc6_deploy_host_mutex as m\n'
                f'm._enable_subreaper()\n'
                f'subprocess.run([sys.executable,"-c",{middle!r}],check=True)\n'
                f'try:\n m._drain_children(.05)\n'
                f'except RuntimeError as exc:\n'
                f' assert str(exc)=="MUTEX_DESCENDANT_STILL_RUNNING"\n'
                f'else:\n raise AssertionError("ADOPTED_CHILD_MISSED")\n'
                f'm._drain_children(1)\n')
        result = subprocess.run(['python3', '-c', code], capture_output=True,
                                text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_a140_exact_clean_idle_old_failure_reconciles_under_same_flock(self):
        from contextlib import ExitStack
        old = dict(candidate=mutex.FAILED_A138_CANDIDATE,
                   run=mutex.FAILED_A138_RUN,
                   attempt=mutex.FAILED_A138_ATTEMPT,
                   phase='cleanup_recover', status='IDLE')
        mutex._write_barrier(self.root, old)
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            proof = stack.enter_context(patch.object(
                mutex, '_exact_a138_stopped_host_proof', return_value='b'*64))
            writers = stack.enter_context(patch.object(
                mutex, '_contract_writers', return_value=set()))
            stack.enter_context(patch.object(mutex.subprocess, 'run',
                return_value=SimpleNamespace(returncode=0, stdout='')))
            self.assertEqual(mutex.run(self.args('reconcile',['--exact-a138-idle'])), 0)
            self.assertFalse(self.barrier.exists())
            self.assertEqual(proof.call_count, 1)
            self.assertGreaterEqual(writers.call_count, 2)
            self.assertEqual(mutex.run(self.args('classify')), 0)

    def test_a140_never_retire_uncertain_or_unrelated_barrier(self):
        from contextlib import ExitStack
        valid = dict(candidate=mutex.FAILED_A138_CANDIDATE,
                     run=mutex.FAILED_A138_RUN,attempt=mutex.FAILED_A138_ATTEMPT,
                     phase='cleanup_recover',status='IDLE')
        cases = [dict(valid,status='INFLIGHT'),dict(valid,status='CONTRACT_ONLY'),
                 dict(valid,run='999999'),dict(valid,candidate='f'*40),
                 dict(valid,phase='suspend'),dict(valid,attempt='1')]
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            proof = stack.enter_context(patch.object(mutex,
                '_exact_a138_stopped_host_proof', return_value='c'*64))
            stack.enter_context(patch.object(mutex,'_contract_writers',return_value=set()))
            for prior in cases:
                mutex._write_barrier(self.root, prior)
                with self.assertRaisesRegex(RuntimeError,'BARRIER_A138_NOT_EXACT_CLEAN_IDLE'):
                    mutex.run(self.args('reconcile',['--exact-a138-idle']))
                self.assertEqual(json.loads(self.barrier.read_text()),prior)
            proof.assert_not_called()

    def test_a140_writer_or_failed_runtime_proof_never_clears_barrier(self):
        from contextlib import ExitStack
        old = dict(candidate=mutex.FAILED_A138_CANDIDATE,
                   run=mutex.FAILED_A138_RUN,attempt=mutex.FAILED_A138_ATTEMPT,
                   phase='cleanup_recover',status='IDLE')
        mutex._write_barrier(self.root,old)
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            writers = stack.enter_context(patch.object(mutex,'_contract_writers',
                return_value={'porota-deploy-contract-'+SHA}))
            proof = stack.enter_context(patch.object(mutex,
                '_exact_a138_stopped_host_proof',return_value='d'*64))
            with self.assertRaisesRegex(RuntimeError,'BARRIER_A138_CONTRACT_WRITER_ALIVE'):
                mutex.run(self.args('reconcile',['--exact-a138-idle']))
            self.assertTrue(self.barrier.exists())
            proof.assert_not_called()
            writers.return_value=set()
            proof.side_effect=RuntimeError('BARRIER_A138_STOPPED_PAPER_CHANGED')
            with self.assertRaisesRegex(RuntimeError,'BARRIER_A138_STOPPED_PAPER_CHANGED'):
                mutex.run(self.args('reconcile',['--exact-a138-idle']))
            self.assertEqual(json.loads(self.barrier.read_text()),old)

    def test_a140_missing_barrier_is_noop_but_reconcile_argument_is_exact(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for mocked in self.mock_root(): stack.enter_context(mocked)
            proof=stack.enter_context(patch.object(mutex,'_exact_a138_stopped_host_proof'))
            self.assertEqual(mutex.run(self.args('reconcile',['--exact-a138-idle'])),0)
            proof.assert_not_called()
            with self.assertRaisesRegex(RuntimeError,'BARRIER_RECONCILE_INVOCATION_INVALID'):
                mutex.run(self.args('reconcile',['--unsafe']))
            with self.assertRaisesRegex(RuntimeError,'BARRIER_RECONCILE_INVOCATION_INVALID'):
                mutex.run(['nobody','user','--attempt',SHA,RUN,ATTEMPT,
                           '--phase','reconcile','--exact-a138-idle'])


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
        self.assertIn('backup_inventory()', classify)
        self.assertIn('RC6_BACKUP_RETIRE_PRECHECK=GREEN', classify)
        for key in ('diskguard', 'suspend', 'drain', 'transfer', 'promotion',
                    'timers_restore', 'timers_restored', 'backup_retire',
                    'finalize_runtime'):
            self.assertIn('RC6_MUTEX_B64', by_id[key], key)
        # A180 added a second *read-only* classify after finalization. It is
        # deliberately protected by the same host flock and exact attempt;
        # no new production writer was introduced.
        self.assertIn('RC6_MUTEX_B64', by_id['postfinal_unstaged'])
        self.assertIn('--phase classify bash -s', by_id['postfinal_unstaged'])
        self.assertEqual(by_id['flat_book'].count('RC6_MUTEX_B64'), 2)
        failure = next(step['run'] for step in steps
                       if step['name'] == 'Failure-safe host cleanup')
        # A207 adds an accounting-only recovery owner phase. It must run
        # under the same serialized host mutex as every other cleanup writer.
        self.assertEqual(failure.count('RC6_MUTEX_B64'), 6)
        boundaries = [line for line in workflow.splitlines()
                      if 'RC6_MUTEX_B64' in line and 'sudo -n python3 -c' in line]
        # A779 adds the governed postfailure orphan-gc reader/writer gate.
        self.assertEqual(len(boundaries), 23)
        self.assertTrue(all("--attempt '$CANDIDATE_SHA' '$GITHUB_RUN_ID' "
                            "'$GITHUB_RUN_ATTEMPT' --phase " in line
                            for line in boundaries))
        self.assertEqual(sum('--phase classify ' in line for line in boundaries), 2)
        reconcile = next(step['run'] for step in steps
                         if step['name'] == 'Reconcile only exact A138 cleanly exited host barrier')
        self.assertIn('--phase reconcile --exact-a138-idle', reconcile)
        self.assertLess(workflow.index('name: Require later exact owner authorization'),
                        workflow.index('name: Reconcile only exact A138 cleanly exited host barrier'))
        self.assertLess(workflow.index('name: Reconcile only exact A138 cleanly exited host barrier'),
                        workflow.index('name: Classify current flat PAPER session'))
        self.assertIn('--phase offline_preclean python3 -', by_id['diskguard'])
        self.assertNotIn('--phase disk_probe python3 -', by_id['diskguard'])
        self.assertIn('rc6_offline_current_image_cleanup.py', by_id['diskguard'])
        self.assertIn('--apply-plan "$PLAN" --require-empty', by_id['backup_retire'])
        self.assertEqual(sum('--phase finalize ' in line for line in boundaries), 1)
        self.assertEqual(sum('--phase cleanup_final ' in line for line in boundaries), 1)
        self.assertEqual(sum('--phase orphan_gc ' in line for line in boundaries), 1)
        self.assertEqual(sum('--phase cleanup_admin_resume ' in line for line in boundaries), 1)
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


class A865ContainmentTests(unittest.TestCase):
    """Default executable boundaries deny legacy capabilities, without host I/O."""

    def test_unmigrated_wrapper_never_opens_lock_or_launches_child(self):
        # Removing the entry veto would re-expose every arbitrary child to an
        # inherited owner OFD. No real root/lock/child is used in this test.
        with patch.object(mutex.os, 'geteuid', return_value=0), \
             patch.object(mutex, 'open_lock', side_effect=AssertionError(
                 'UNMIGRATED_WRAPPER_OPENED_PHYSICAL_LOCK')) as acquire, \
             patch.object(mutex.subprocess, 'run') as child:
            for phase in sorted(mutex.PHASES):
                with self.subTest(phase=phase):
                    with self.assertRaisesRegex(
                            RuntimeError, 'MUTEX_CONSUMER_PROTOCOL_UNMIGRATED'):
                        mutex.run(['nobody', 'root', '--attempt', SHA, RUN,
                                   ATTEMPT, '--phase', phase, 'true'])
            self.assertEqual(acquire.call_count, 0)
            self.assertEqual(child.call_count, 0)

    def test_pretransfer_default_returns_deferred_without_query_or_delete(self):
        from scripts import rc6_pretransfer_image_cleanup as clean
        with patch.object(clean, 'safety', return_value=[]), \
             patch.object(clean, 'docker', return_value='') as docker:
            record = clean.cleanup()
        self.assertEqual(record['status'], 'RED')
        self.assertEqual(record['reason'], 'DOCKER_NAMESPACE_EXCLUSIVITY_UNVERIFIED')
        self.assertEqual(record['removed_tags'], [])
        self.assertEqual(record['space_recovered'], 0)
        self.assertEqual(docker.call_count, 0)

    def test_last_window_retarget_adversary_never_reaches_remove(self):
        from scripts import rc6_pretransfer_image_cleanup as clean
        image = 'sha256:' + '1' * 64
        foreign = 'sha256:' + '2' * 64
        tag = 'porota-predeploy-v2:' + SHA
        target = {tag: image}
        removed = []
        def daemon(*args):
            if args == ('image', 'ls', '-aq', '--no-trunc'):
                return image
            if args == ('ps', '-aq', '--no-trunc'):
                return ''
            if args[:2] == ('image', 'inspect'):
                return '|'.join(map(json.dumps, [image, [tag], [], {
                    'porota.predeploy': 'v2', 'porota.commit': SHA}]))
            if args == ('image', 'rm', tag):
                # Scheduled second-client retarget immediately before rm(TAG).
                target[tag] = foreign
                removed.append(target.pop(tag))
                return tag
            raise AssertionError(args)
        with patch.object(clean, 'safety', return_value=[]), \
             patch.object(clean, 'docker', side_effect=daemon):
            record = clean.cleanup()
        self.assertEqual(record['status'], 'RED')
        self.assertEqual(removed, [])
        self.assertEqual(target, {tag: image})

    def test_offline_entries_deny_before_filesystem_or_docker(self):
        from scripts import rc6_offline_current_image_cleanup as current
        from scripts import rc6_offline_failed_image_cleanup as failed
        for clean in (current, failed):
            with self.subTest(module=clean.__name__), \
                 patch.object(clean, 'ROOT', Path('/nonexistent-a865-offhost')), \
                 patch.object(clean, 'docker') as docker:
                with self.assertRaisesRegex(
                        RuntimeError, 'DOCKER_NAMESPACE_EXCLUSIVITY_UNVERIFIED'):
                    clean.cleanup()
                self.assertEqual(docker.call_count, 0)

    def test_direct_docker_wrappers_reject_mutators_before_subprocess(self):
        from scripts import rc6_pretransfer_image_cleanup as pretransfer
        from scripts import rc6_offline_current_image_cleanup as current
        from scripts import rc6_offline_failed_image_cleanup as failed
        commands = [('image', 'rm', 'porota-predeploy-v2:' + SHA),
                    ('tag', 'old', 'new'), ('system', 'prune'),
                    ('rm', 'foreign-container'), ('volume', 'rm', 'foreign'),
                    ('network', 'rm', 'foreign'), ('exec', 'foreign', 'true')]
        for clean in (pretransfer, current, failed):
            for command in commands:
                with self.subTest(module=clean.__name__, command=command), \
                     patch.object(clean.subprocess, 'run', return_value=
                                  SimpleNamespace(returncode=0, stdout='')) as process:
                    with self.assertRaisesRegex(
                            (RuntimeError, ValueError), 'DOCKER_MUTATION_UNAUTHORIZED'):
                        clean.docker(*command)
                    self.assertEqual(process.call_count, 0)

    def test_same_repository_digest_without_exact_receipt_is_protected(self):
        from scripts import rc6_pretransfer_image_cleanup as clean
        image = 'sha256:' + '1' * 64
        with patch.object(clean, 'references', return_value=set()), \
             patch.object(clean, 'inspect_image', return_value=(
                 ['porota-predeploy-v2:' + SHA],
                 ['porota-predeploy-v2@sha256:' + 'f' * 64],
                 {'porota.predeploy': 'v2', 'porota.commit': SHA})):
            self.assertEqual(clean.eligibility(image)[1],
                             'DIGEST_ATTEMPT_PROVENANCE_UNVERIFIED')

    def test_offline_inspectors_do_not_attest_unbound_repository_digests(self):
        from scripts import rc6_offline_current_image_cleanup as current
        from scripts import rc6_offline_failed_image_cleanup as failed
        record = {'Id': failed.IMAGE, 'RepoTags': list(failed.TAGS),
                  'RepoDigests': ['porota-predeploy-v2@sha256:' + 'f' * 64],
                  'Config': {'Labels': {'porota.predeploy': 'v2',
                                        'porota.commit': failed.FAILED}}}
        with self.subTest(consumer='failed'), \
             patch.object(failed, 'docker', return_value=json.dumps([record])), \
             patch.object(failed, 'references', return_value=set()):
            with self.assertRaisesRegex(ValueError, 'ALIASED'):
                failed.eligible_tags()
        with self.subTest(consumer='current'):
            with self.assertRaisesRegex(ValueError, 'OWNERSHIP_UNVERIFIED'):
                current._require_candidate_oci(record, failed.FAILED)

    def test_finalizer_old_protocol_denies_before_reading_supplied_fd(self):
        from scripts import porota_apply_host_control_plane_v2 as host
        with patch.dict(os.environ, {'RC6_DEPLOY_MUTEX_FD': '9'}), \
             patch.object(host.os, 'fstat') as inspect_fd:
            with self.assertRaisesRegex(
                    RuntimeError, 'MUTEX_CONSUMER_PROTOCOL_UNMIGRATED'):
                host._require_inherited_finalizer_mutex()
            self.assertEqual(inspect_fd.call_count, 0)

    def test_helper_cli_launch_forms_emit_red_receipt_and_nonzero_exit(self):
        for name, args in (
                ('scripts.rc6_offline_current_image_cleanup', ['2147483648']),
                ('scripts.rc6_offline_failed_image_cleanup', []),
                ('scripts.rc6_pretransfer_image_cleanup', [])):
            for launch in (['-m', name], [name.replace('.', '/') + '.py']):
                with self.subTest(module=name, launch=launch):
                    result = subprocess.run([sys.executable, '-B', *launch, *args],
                        cwd=Path(__file__).resolve().parents[1],
                        capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    record = json.loads(result.stdout)
                    self.assertEqual(record['status'], 'RED')
                    self.assertEqual(record['removed_tags'], [])
                    self.assertEqual(record['space_recovered'], 0)


if __name__ == '__main__':
    unittest.main()

