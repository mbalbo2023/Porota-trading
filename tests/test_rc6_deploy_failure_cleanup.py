"""Failure cleanup uses only attempt-owned, unreferenced resources."""
import json
import fcntl
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import rc6_deploy_failure_cleanup as cleanup

PROCESS_RUN = subprocess.run
SHA = 'a' * 40
IMG = 'sha256:' + 'b' * 64
STABLE = 'sha256:' + 'c' * 64
CID = 'd' * 12


class Docker:
    def __init__(self):
        self.tags = {
            cleanup.STABLE_IMAGE: STABLE,
            'porota-predeploy-v2:' + SHA: IMG,
            'porota-trading-bot:17.0.0-rc6-candidate-' + SHA: IMG,
        }
        self.containers = {CID: (STABLE, [])}
        self.calls = []
        self.failure = None

    def run(self, command, **kwargs):
        self.calls.append(command)
        self.assert_safe_command(command)
        args = command[1:]
        if self.failure == args[:2]:
            return SimpleNamespace(returncode=1, stdout='')
        if args == ['ps', '-aq']:
            value = '\n'.join(self.containers)
        elif args[:2] == ['inspect', '--format']:
            image, mounts = self.containers[args[-1]]
            value = image + '|' + json.dumps(mounts)
        elif args[:2] == ['image', 'ls']:
            value = self.tags.get(args[-1], '')
        elif args[:2] == ['image', 'rm']:
            self.tags.pop(args[-1])
            value = ''
        else:
            raise AssertionError('Unexpected Docker command')
        return SimpleNamespace(returncode=0, stdout=value)

    @staticmethod
    def assert_safe_command(command):
        if command[0] != 'docker' or 'prune' in command or '-f' in command:
            raise AssertionError('Unscoped or forced cleanup')


class FailureCleanupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='rc6-cleanup-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.staging = self.root / ('porota-deploy-v2-' + SHA)
        self.staging.mkdir()
        (self.staging / 'image.tar.gz').write_bytes(b'synthetic artifact')
        self.unrelated = self.root / ('porota-deploy-v2-' + 'e' * 40)
        self.unrelated.mkdir()
        (self.unrelated / 'keep').write_text('other attempt')
        self.docker = Docker()
        root_patch = patch.object(cleanup, 'REMOTE_ROOT', self.root)
        process_patch = patch.object(cleanup.subprocess, 'run', side_effect=self.docker.run)
        root_patch.start(); process_patch.start()
        self.addCleanup(root_patch.stop); self.addCleanup(process_patch.stop)

    def test_unused_owned_tags_and_staging_removed_other_attempt_preserved(self):
        result = cleanup.cleanup(SHA)
        self.assertEqual(result['status'], 'GREEN')
        self.assertEqual(len(result['removed_tags']), 2)
        self.assertEqual(result['shared_cache_action'], 'UNTOUCHED_NO_HOST_BUILD')
        self.assertIn('space_recovered', result)
        self.assertFalse(self.staging.exists())
        self.assertTrue((self.unrelated / 'keep').exists())
        self.assertEqual(self.docker.tags[cleanup.STABLE_IMAGE], STABLE)

    def test_running_or_stopped_owner_protects_image(self):
        # ps -aq, not ps -q, includes stopped owners as well.
        self.docker.containers['f' * 12] = (IMG, [])
        result = cleanup.cleanup(SHA)
        self.assertEqual(result['status'], 'GREEN')
        self.assertEqual(result['removed_tags'], [])
        self.assertEqual(len(result['retained_tags']), 2)
        self.assertTrue(all(call[:3] != ['docker', 'image', 'rm'] for call in self.docker.calls))

    def test_stable_alias_protected_even_without_container_reference(self):
        self.docker.tags[cleanup.STABLE_IMAGE] = IMG
        result = cleanup.cleanup(SHA)
        self.assertEqual(result['removed_tags'], [])
        self.assertEqual(len(result['retained_tags']), 2)

    def test_bind_mount_protects_staging_and_nested_data(self):
        source = self.staging / 'data'
        source.mkdir()
        (source / 'keep.db').write_bytes(b'opaque synthetic data')
        self.docker.containers[CID] = (STABLE, [{'Type': 'bind', 'Source': str(source)}])
        result = cleanup.cleanup(SHA)
        self.assertEqual(result['status'], 'RED')
        self.assertIn('STAGING_REFERENCED_BY_CONTAINER', result['errors'])
        self.assertTrue((source / 'keep.db').exists())

    def test_parent_bind_mount_protects_staging(self):
        self.docker.containers[CID] = (STABLE, [{'Type': 'bind', 'Source': str(self.root)}])
        result = cleanup.cleanup(SHA)
        self.assertEqual(result['status'], 'RED')
        self.assertTrue(self.staging.exists())

    def test_tmpfs_has_no_host_source_and_does_not_disable_cleanup(self):
        self.docker.containers[CID] = (STABLE, [{'Type': 'tmpfs', 'Source': ''}])
        result = cleanup.cleanup(SHA)
        self.assertEqual(result['status'], 'GREEN')
        self.assertFalse(self.staging.exists())

    def test_inventory_failure_is_not_interpreted_as_unreferenced(self):
        self.docker.failure = ['ps', '-aq']
        with self.assertRaises(cleanup.CleanupRejected):
            cleanup.cleanup(SHA)
        self.assertTrue(self.staging.exists())
        self.assertIn('porota-predeploy-v2:' + SHA, self.docker.tags)

    def test_unknown_mount_shape_blocks_before_any_deletion(self):
        for mounts in ([{}], [{'Type': 'bind', 'Source': ''}],
                       [{'Type': 'unknown', 'Source': '/somewhere'}]):
            self.docker.containers[CID] = (STABLE, mounts)
            with self.assertRaises(cleanup.CleanupRejected):
                cleanup.cleanup(SHA)
            self.assertTrue(self.staging.exists())
            self.assertIn('porota-predeploy-v2:' + SHA, self.docker.tags)

    def test_docker_removal_race_remains_nonforced_and_reports_red(self):
        self.docker.failure = ['image', 'rm']
        result = cleanup.cleanup(SHA)
        self.assertEqual(result['status'], 'RED')
        self.assertEqual(result['removed_tags'], [])
        self.assertFalse(self.staging.exists())
        self.assertIn('porota-predeploy-v2:' + SHA, self.docker.tags)

    def test_symlink_root_and_malformed_sha_cannot_expand_scope(self):
        with patch.object(cleanup.shutil, 'rmtree') as remove:
            for value in ('', '../elsewhere', 'a' * 39, 'A' * 40, None):
                with self.assertRaises(cleanup.CleanupRejected):
                    cleanup.cleanup(value)
            remove.assert_not_called()
        (self.staging / 'image.tar.gz').unlink()
        self.staging.rmdir()
        self.staging.symlink_to(self.unrelated, target_is_directory=True)
        with self.assertRaises(cleanup.CleanupRejected):
            cleanup.cleanup(SHA)
        self.assertTrue((self.unrelated / 'keep').exists())

    def test_timeout_and_failure_still_produce_nonzero_cli_result(self):
        with patch.object(cleanup, 'cleanup', side_effect=subprocess.TimeoutExpired(['docker'], 20)), \
             patch('builtins.print') as output:
            self.assertEqual(cleanup.main(['--candidate-sha', SHA]), 2)
        report = json.loads(output.call_args.args[0])
        self.assertEqual(report['status'], 'RED')

    def test_live_promoter_lock_blocks_cleanup_without_deleting_anything(self):
        lock = self.root / 'porota-rc6-deploy-v2.lock'
        with lock.open('w') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch('builtins.print') as output:
                self.assertEqual(cleanup.main(['--candidate-sha', SHA]), 2)
        report = json.loads(output.call_args.args[0])
        self.assertIn('PROMOTION_STILL_RUNNING', report['errors'])
        self.assertEqual(report['candidate_sha'], SHA)
        self.assertTrue(self.staging.exists())
        self.assertEqual(self.docker.calls, [])

    def test_released_promoter_lock_allows_cleanup_without_replacing_lock(self):
        lock = self.root / 'porota-rc6-deploy-v2.lock'
        lock.write_text('')
        inode = lock.stat().st_ino
        with patch('builtins.print'):
            self.assertEqual(cleanup.main(['--candidate-sha', SHA]), 0)
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertFalse(self.staging.exists())

    def test_failure_cleanup_step_survives_failed_promotion_and_has_bounded_ssh(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/porota-deploy-v2-promote.yml').read_text()
        block = workflow.split('      - name: Failure-safe host cleanup', 1)[1].split('      - name:', 1)[0]
        self.assertIn('always()', block)
        self.assertIn("steps.transfer.outcome == 'failure'", block)
        self.assertIn("steps.promotion.outcome == 'failure'", block)
        self.assertIn("steps.promotion.outcome == 'cancelled'", block)
        self.assertIn('timeout 180 ssh', block)
        self.assertIn('sudo -n timeout 150 python3 - --candidate-sha', block)
        self.assertIn('POROTA_DEPLOY_FAILURE_CLEANUP=RED|candidate=', block)
        self.assertIn('< scripts/rc6_deploy_failure_cleanup.py', block)
        # bash checks the actual runner block syntax; no SSH command executes.
        shell = block.split('        run: |\n', 1)[1]
        shell = '\n'.join(line[10:] if line.startswith('          ') else line for line in shell.splitlines())
        syntax = PROCESS_RUN(['bash', '-n'], input=shell, capture_output=True, text=True, timeout=10)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        self.assertLess(workflow.index('Failure-safe host cleanup'), workflow.index('Runner cleanup'))


if __name__ == '__main__':
    unittest.main()
