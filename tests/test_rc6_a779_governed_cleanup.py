"""A779: adversarial OFFHOST tests; no Docker, systemd, or host mutation."""
from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_pretransfer_image_cleanup as cleanup

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = 'a' * 40
IMAGE = 'sha256:' + '1' * 64
TAG = 'porota-predeploy-v2:' + CANDIDATE
PROOF = {'candidate': CANDIDATE, 'run': '42329145989',
         'attempt': '1', 'phase': 'preclean'}


class GovernedCleanupAdversaries(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.folder = Path(temp)
        self.lock = self.folder / 'porota-rc6-deploy-v2.lock'
        self.barrier = self.folder / 'porota-rc6-deploy-v2-attempt.json'
        self.stack.enter_context(patch.object(cleanup, 'LOCK', self.lock))
        self.stack.enter_context(patch.object(cleanup, 'BARRIER', self.barrier))
        self.stack.enter_context(patch.object(cleanup, 'ROOT_UID', os.getuid()))
        # Exercise the dormant legacy validator with simulated Docker only.
        # A865's default-entry tests do NOT patch this unconditional veto.
        self.stack.enter_context(patch.object(cleanup, 'require_migrated_consumer_protocol'))
        self.fd = os.open(self.lock, os.O_CREAT | os.O_RDWR, 0o600)
        self.addCleanup(os.close, self.fd)
        self._set_proof(PROOF)
        self._set_barrier()

    def _set_proof(self, proof):
        self.stack.enter_context(patch.dict(os.environ, {
            cleanup.OWNER_FD: str(self.fd),
            cleanup.OWNER_PROOF: json.dumps(proof, sort_keys=True)}, clear=False))

    def _set_barrier(self, *, status='INFLIGHT', candidate=CANDIDATE):
        self.barrier.write_text(json.dumps({
            'candidate': candidate, 'run': PROOF['run'],
            'attempt': PROOF['attempt'], 'phase': PROOF['phase'],
            'status': status}), encoding='utf-8')
        self.barrier.chmod(0o600)

    def _held(self):
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _assert_rejects_without_docker(self, reason=None):
        with patch.object(cleanup, 'cleanup') as docker_mutator:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = cleanup.main()
            self.assertEqual(code, 2)
            record = json.loads(output.getvalue())
            self.assertEqual(record['status'], 'RED')
            if reason is not None:
                self.assertEqual(record.get('reason'), reason)
            docker_mutator.assert_not_called()

    def test_positive_same_open_file_description_and_exact_barrier(self):
        self._held()
        with cleanup.promotion_lock():
            pass

    def test_real_governed_mutex_accepts_every_legal_decimal_fd_class(self):
        """A790: many host wrappers have inherited FDs >=10, including 100+."""
        self._held()
        for target in (3, 9, 10, 13, 29, 100, 255):
            with self.subTest(fd=target):
                if target == self.fd:
                    with patch.dict(os.environ, {cleanup.OWNER_FD: str(target)}):
                        with cleanup.promotion_lock():
                            pass
                    continue
                try:
                    saved = os.dup(target)
                except OSError:
                    saved = None
                os.dup2(self.fd, target)
                try:
                    with patch.dict(os.environ, {cleanup.OWNER_FD: str(target)}):
                        with cleanup.promotion_lock():
                            pass
                finally:
                    if saved is not None:
                        os.dup2(saved, target)
                        os.close(saved)
                    else:
                        os.close(target)

    def test_malformed_or_out_of_range_fd_remains_fail_closed(self):
        self._held()
        for raw in ('0', '01', '-1', '2', '256', '999', 'abc'):
            with self.subTest(fd=raw), patch.dict(os.environ, {
                    cleanup.OWNER_FD: raw}):
                self._assert_rejects_without_docker()

    def test_closed_high_fd_and_foreign_open_description_stay_rejected(self):
        self._held()
        if self.fd == 220:
            self.skipTest('unusually high base fd')
        # Tests run beside other jobs; never close a preexisting foreign FD.
        try:
            saved_220 = os.dup(220)
        except OSError:
            saved_220 = None
        os.dup2(self.fd, 220)
        try:
            os.close(220)
            with patch.dict(os.environ, {cleanup.OWNER_FD: '220'}):
                self._assert_rejects_without_docker()
        finally:
            if saved_220 is not None:
                os.dup2(saved_220, 220)
                os.close(saved_220)
        outsider = os.open(self.lock, os.O_RDWR)
        try:
            if self.fd == 150 or outsider == 150:
                self.skipTest('unexpected occupied target fd')
            try:
                saved_150 = os.dup(150)
            except OSError:
                saved_150 = None
            os.dup2(outsider, 150)
            try:
                with patch.dict(os.environ, {cleanup.OWNER_FD: '150'}):
                    self._assert_rejects_without_docker('GOVERNED_MUTEX_OTHER_OWNER')
            finally:
                if saved_150 is None:
                    os.close(150)
                else:
                    os.dup2(saved_150, 150)
                    os.close(saved_150)
        finally:
            os.close(outsider)

    def test_negative_fd_adversaries_preserve_preexisting_unrelated_fds(self):
        """A790 QA: nested tests may run with unrelated FD 150/220 open."""
        self._held()
        with tempfile.TemporaryFile() as sentinel_150, \
             tempfile.TemporaryFile() as sentinel_220:
            targets = {150: sentinel_150, 220: sentinel_220}
            saved = {}
            for fd, sentinel in targets.items():
                try:
                    saved[fd] = os.dup(fd)
                except OSError:
                    saved[fd] = None
                os.dup2(sentinel.fileno(), fd)
            try:
                before = {fd: (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
                          for fd in targets}
                self.test_closed_high_fd_and_foreign_open_description_stay_rejected()
                for fd in targets:
                    actual = os.fstat(fd)
                    self.assertEqual((actual.st_dev, actual.st_ino), before[fd])
            finally:
                for fd, original in saved.items():
                    if original is None:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                    else:
                        os.dup2(original, fd)
                        os.close(original)

    def test_both_workflow_callsite_decimal_guards_admit_real_fd_range(self):
        workflow = (ROOT / '.github/workflows/porota-deploy-v2-promote.yml').read_text()
        patterns = re.findall(r'\[\[ "\$RC6_GOVERNED_FD" =~ (\^[^ ]+\$) \]\]', workflow)
        self.assertEqual(len(patterns), 2)
        self.assertIn('test "$RC6_GOVERNED_FD" -ge 3', workflow)
        self.assertIn('[ "$RC6_GOVERNED_FD" -ge 3 ]', workflow)
        valid = ('3', '9', '10', '13', '29', '100', '255')
        invalid = ('0', '01', '-1', '2', '256', '999', 'abc')
        for pattern in patterns:
            for raw in valid + invalid:
                with self.subTest(pattern=pattern, fd=raw):
                    command = f'[[ "$1" =~ {pattern} ]] && [ "$1" -ge 3 ] && [ "$1" -le 255 ]'
                    result = subprocess.run(['bash', '-c', command, '_', raw],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode == 0, raw in valid,
                                     f'{raw}: {result.stderr}')

    def test_successful_governed_main_can_enter_bounded_cleanup(self):
        self._held()
        def successful(report):
            report['status'] = 'GREEN'
            return report
        with patch.object(cleanup, 'cleanup', side_effect=successful) as work:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(cleanup.main(), 0)
            self.assertEqual(json.loads(output.getvalue())['status'], 'GREEN')
            work.assert_called_once()

    def test_missing_real_lock_cannot_be_bypassed_by_tmp_decoy(self):
        self._held()
        decoy = self.folder / 'tmp-porota-rc6-deploy-v2.lock'
        decoy.write_bytes(b'decoy')
        with decoy.open('rb') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock.unlink()
            self._assert_rejects_without_docker()

    def test_unlocked_inherited_descriptor_is_not_an_owner(self):
        self._assert_rejects_without_docker('GOVERNED_MUTEX_NOT_HELD')

    def test_competing_owner_different_descriptor_is_rejected(self):
        competing = os.open(self.lock, os.O_RDWR)
        self.addCleanup(os.close, competing)
        fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self._assert_rejects_without_docker('GOVERNED_MUTEX_OTHER_OWNER')

    def test_sudo_fd_loss_rejects_before_any_docker(self):
        self._held()
        with patch.dict(os.environ, {cleanup.OWNER_FD: '255'}, clear=False):
            self._assert_rejects_without_docker()

    def test_proof_missing_and_candidate_drift_block(self):
        self._held()
        with patch.dict(os.environ, {cleanup.OWNER_PROOF: ''}, clear=False):
            self._assert_rejects_without_docker('GOVERNED_MUTEX_PROOF_MISSING')
        self._set_barrier(candidate='b' * 40)
        self._assert_rejects_without_docker('GOVERNED_BARRIER_DRIFT')

    def test_stale_barrier_status_and_orphan_phase(self):
        self._held()
        self._set_barrier(status='IDLE')
        self._assert_rejects_without_docker('GOVERNED_BARRIER_DRIFT')
        self._set_proof(dict(PROOF, phase='orphan_gc'))
        self._assert_rejects_without_docker('GOVERNED_BARRIER_DRIFT')
        self.barrier.unlink()
        with cleanup.promotion_lock():
            pass

    def test_symlink_barrier_rejected_before_docker(self):
        self._held()
        original = self.folder / 'saved-barrier'
        self.barrier.rename(original)
        self.barrier.symlink_to(original)
        self._assert_rejects_without_docker()

    def test_symlink_mutex_rejected_even_when_real_target_held(self):
        self._held()
        target = self.folder / 'real-held-lock'
        self.lock.rename(target)
        self.lock.symlink_to(target)
        self._assert_rejects_without_docker('GOVERNED_MUTEX_IDENTITY_RED')

    def test_same_image_id_stopped_foreign_reference_remains_protected(self):
        with patch.object(cleanup, 'inspect_image',
                          return_value=([TAG], [], {
                              'porota.predeploy': 'v2',
                              'porota.commit': CANDIDATE})), \
             patch.object(cleanup, 'references', return_value={IMAGE}):
            self.assertEqual(cleanup.eligibility(IMAGE)[1],
                             'CONTAINER_REFERENCED')

    def test_shared_foreign_alias_and_digest_never_eligible(self):
        labels = {'porota.predeploy': 'v2', 'porota.commit': CANDIDATE}
        with patch.object(cleanup, 'references', return_value=set()), \
             patch.object(cleanup, 'inspect_image',
                          return_value=([TAG, 'boca:beta'], [], labels)):
            self.assertEqual(cleanup.eligibility(IMAGE)[1],
                             'PROTECTED_OR_NONCANDIDATE_ALIAS')
        with patch.object(cleanup, 'references', return_value=set()), \
             patch.object(cleanup, 'inspect_image',
                          return_value=([TAG], ['boca@sha256:' + 'c' * 64], labels)):
            self.assertEqual(cleanup.eligibility(IMAGE)[1],
                             'DIGEST_ATTEMPT_PROVENANCE_UNVERIFIED')

    def test_canonical_path_and_workflow_guards(self):
        src = (ROOT / 'scripts/rc6_deploy_host_mutex.py').read_text()
        workflow = (ROOT / '.github/workflows/porota-deploy-v2-promote.yml').read_text()
        helper = (ROOT / 'scripts/rc6_pretransfer_image_cleanup.py').read_text()
        self.assertIn("LOCK_PATH = LOCK_DIR / 'porota-rc6-deploy-v2.lock'", src)
        self.assertIn("LOCK = Path('/run/lock/porota-rc6-deploy-v2.lock')", helper)
        # Actual non-delegation is exercised with a real child in A865 tests,
        # not inferred from the spelling of pass_fds or environment assignments.
        self.assertIn("orphan_gc = phase == 'orphan_gc'", src)
        self.assertIn("--phase orphan_gc python3 -B -", workflow)
        self.assertGreaterEqual(workflow.count('RC6_DEPLOY_MUTEX_PROOF='), 2)
        self.assertNotIn('sudo -n flock -n -E 73 /run/lock/porota-rc6-deploy-v2.lock', workflow)


if __name__ == '__main__':
    unittest.main()


