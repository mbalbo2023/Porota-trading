"""Failure cleanup uses only attempt-owned, unreferenced resources."""
import json
import fcntl
import os
from pathlib import Path
import re
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
        self.digests = {}
        self.images = {IMG, STABLE}
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
        elif args == ['image', 'ls', '-aq', '--no-trunc']:
            value = '\n'.join(sorted(self.images))
        elif args[:2] == ['image', 'ls']:
            value = (args[-1] if args[-1] in self.images else
                     self.tags.get(args[-1], ''))
        elif args[:3] == ['image', 'inspect', '--format']:
            image = args[-1]
            if image not in self.images:
                return SimpleNamespace(returncode=1, stdout='')
            value = '|'.join(json.dumps(item) for item in (
                image, [tag for tag, identity in self.tags.items() if identity == image],
                self.digests.get(image, []),
            ))
        elif args[:2] == ['image', 'rm']:
            if args[-1] in self.images:
                self.images.remove(args[-1])
            else:
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
        self.assertEqual(result['removed_image_ids'], [IMG])
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

    def test_external_alias_or_digest_preserves_image_and_reports_why(self):
        self.docker.tags['ppi-watch:current'] = IMG
        result = cleanup.cleanup(SHA)
        self.assertEqual(result['status'], 'GREEN')
        self.assertEqual(result['removed_tags'], [])
        self.assertEqual(set(result['retained_reasons'].values()),
                         {'NONCANDIDATE_ALIAS_OR_DIGEST'})
        self.assertTrue(all(call[:3] != ['docker', 'image', 'rm']
                            for call in self.docker.calls))
        self.docker.tags.pop('ppi-watch:current')
        self.docker.digests[IMG] = ['foreign@sha256:' + 'e' * 64]
        self.docker.calls.clear()
        result = cleanup.cleanup(SHA)
        self.assertEqual(result['removed_tags'], [])
        self.assertEqual(len(result['retained_tags']), 2)

    def test_same_repository_digest_without_attempt_provenance_is_retained(self):
        self.docker.digests[IMG] = ['porota-predeploy-v2@sha256:' + 'e' * 64]
        result = cleanup.cleanup(SHA)
        self.assertEqual(result['status'], 'GREEN')
        self.assertEqual(result['removed_tags'], [])
        self.assertEqual(result['removed_image_ids'], [])
        self.assertEqual(set(result['retained_reasons'].values()),
                         {'NONCANDIDATE_ALIAS_OR_DIGEST'})
        self.assertIn(IMG, self.docker.images)
        self.assertFalse(self.staging.exists())

    def test_malformed_image_alias_inventory_fails_closed(self):
        original = self.docker.run
        def malformed(command, **kwargs):
            if command[1:4] == ['image', 'inspect', '--format']:
                return SimpleNamespace(returncode=0, stdout='invalid')
            return original(command, **kwargs)
        with patch.object(cleanup.subprocess, 'run', side_effect=malformed):
            result = cleanup.cleanup(SHA)
        self.assertEqual(result['status'], 'RED')
        self.assertEqual(result['removed_tags'], [])
        self.assertEqual(len(result['errors']), 2)

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
        self.assertIn('host_disk_before', report)
        self.assertIn('host_disk_after', report)
        self.assertIn('host_space_recovered', report)

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

    def test_cleanup_selects_exact_run_attempt_and_preserves_same_sha_other_attempt(self):
        first = self.root / f'porota-deploy-v2-{SHA}-1234-1'
        other = self.root / f'porota-deploy-v2-{SHA}-1234-2'
        first.mkdir(); other.mkdir()
        (other / 'keep').write_text('different attempt')
        result = cleanup.cleanup(SHA, '1234', '1')
        self.assertEqual(result['status'], 'GREEN')
        self.assertFalse(first.exists())
        self.assertEqual((other / 'keep').read_text(), 'different attempt')
        with self.assertRaisesRegex(cleanup.CleanupRejected, 'ATTEMPT_IDENTITY_INVALID'):
            cleanup.cleanup(SHA, '../', '1')

    def test_failure_cleanup_step_survives_failed_promotion_and_has_bounded_ssh(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/porota-deploy-v2-promote.yml').read_text()
        import yaml
        steps = yaml.safe_load(workflow)['jobs']['promote']['steps']
        ids = {step.get('id') for step in steps}
        self.assertIn('transfer', ids)
        self.assertIn('promotion', ids)
        block = workflow.split('      - name: Failure-safe host cleanup', 1)[1].split('      - name:', 1)[0]
        self.assertIn('always()', block)
        self.assertIn('timeout-minutes: 11', block)
        self.assertNotIn("steps.host_reconcile.outcome == 'failure'", block)
        self.assertNotIn("steps.classify.outcome == 'failure'", block)
        self.assertNotIn("steps.diskguard.outcome == 'failure'", block)
        self.assertIn("steps.transfer.outcome == 'failure'", block)
        self.assertIn("steps.promotion.outcome == 'failure'", block)
        self.assertIn("steps.promotion.outcome == 'cancelled'", block)
        self.assertIn('timeout 180 ssh', block)
        self.assertIn('--phase cleanup_final timeout 150 python3 - --candidate-sha', block)
        self.assertIn('--run-id', block)
        self.assertIn('--attempt', block)
        self.assertIn('RC6_FAILURE_CLEANUP_MUTEX_OR_WRITER=RED', block)
        self.assertIn('RC6_FAILURE_RECOVERY=RED', block)
        self.assertIn('POROTA_DEPLOY_FAILURE_CLEANUP=RED|candidate=', block)
        self.assertIn('< scripts/rc6_deploy_failure_cleanup.py', block)
        # bash checks the actual runner block syntax; no SSH command executes.
        shell = block.split('        run: |\n', 1)[1]
        shell = '\n'.join(line[10:] if line.startswith('          ') else line for line in shell.splitlines())
        syntax = PROCESS_RUN(['bash', '-n'], input=shell, capture_output=True, text=True, timeout=10)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        self.assertLess(workflow.index('Failure-safe host cleanup'), workflow.index('Runner cleanup'))
        self.assertLess(workflow.index('Pin exact DigitalOcean Droplet before host mutation'),
                        workflow.index('Reconcile only exact A138 cleanly exited host barrier'))

    def test_failed_recovery_or_restore_reclaims_payload_without_rearming(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    '.github/workflows/porota-deploy-v2-promote.yml').read_text()
        block = workflow.split('      - name: Failure-safe host cleanup', 1)[1].split(
            '      - name:', 1)[0]
        shell = block.split('        run: |\n', 1)[1]
        shell = '\n'.join(line[10:] if line.startswith('          ') else line
                          for line in shell.splitlines())
        with tempfile.TemporaryDirectory(prefix='rc6-failed-payload-') as folder:
            fake_ssh = Path(folder) / 'ssh'
            fake_ssh.write_text('''#!/usr/bin/python3
import os,re,sys
from pathlib import Path
command = ' '.join(sys.argv[1:])
phase = re.search(r'--phase (cleanup_[a-z_]+)', command).group(1)
with open(os.environ['RC6_FAKE_LOG'], 'a') as output:
    output.write(phase + '\\n')
if phase == 'cleanup_recover':
    print('{"status":"ORIGINAL_RUNTIME_RESUMED"}')
if phase in os.environ['RC6_FAKE_FAILURES'].split(','):
    sys.exit(43 if phase == 'cleanup_restore' else 42)
''')
            fake_ssh.chmod(0o755)
            log = Path(folder) / 'ssh.log'
            env = dict(os.environ, PATH=folder + ':/usr/bin:/bin', HOME=folder,
                       DO_USER='fixture', DO_HOST='fixture', RC6_MUTEX_B64='eA==',
                       CANDIDATE_SHA=SHA, GITHUB_RUN_ID='1234', GITHUB_RUN_ATTEMPT='1',
                       RC6_QUIESCENCE_MODE='CLOSED', RC6_FAKE_LOG=str(log))
            cases = (
                ('cleanup_recover', 42, ['cleanup_contract', 'cleanup_recover',
                                         'cleanup_payload']),
                ('cleanup_restore', 43, ['cleanup_contract', 'cleanup_recover',
                                         'cleanup_restore', 'cleanup_payload']),
                ('cleanup_recover,cleanup_payload', 42,
                 ['cleanup_contract', 'cleanup_recover', 'cleanup_payload']),
                ('cleanup_contract', 42, ['cleanup_contract']),
            )
            for failures, expected_rc, expected_phases in cases:
                log.unlink(missing_ok=True)
                result = PROCESS_RUN(['bash', '-c', shell], capture_output=True,
                                     text=True, env=dict(env, RC6_FAKE_FAILURES=failures),
                                     timeout=15, cwd=Path(__file__).resolve().parents[1])
                self.assertEqual(result.returncode, expected_rc, (failures, result.stderr))
                self.assertEqual(log.read_text().splitlines(), expected_phases)
                self.assertNotIn('cleanup_final', log.read_text())
                self.assertNotIn('timers_restore', log.read_text())
                if failures != 'cleanup_contract':
                    self.assertIn('barrier_retained=true', result.stdout)

    def test_classify_red_with_sixteen_held_timers_only_accounts_disk(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    '.github/workflows/porota-deploy-v2-promote.yml').read_text()
        pre = workflow.split('      - name: Account pre-suspend failure without recovery or timer writes', 1)[1].split('      - name:', 1)[0]
        full = workflow.split('      - name: Failure-safe host cleanup', 1)[1].split('      - name:', 1)[0]
        outcomes = {'host_reconcile': 'success', 'classify': 'failure',
                    'a151_intraday_auth': 'skipped',
                    'pre_suspend_snapshot': 'skipped',
                    'restatement_continuity': 'skipped', 'diskguard': 'skipped',
                    'suspend': 'skipped', 'drain': 'skipped',
                    'transfer': 'skipped', 'promotion': 'skipped',
                    'flat_book': 'skipped', 'timers_restore': 'skipped',
                    'timers_restored': 'skipped', 'backup_retire': 'skipped',
                    'finalize_runtime': 'skipped'}

        def selected(block):
            expression = block.split('if: ${{', 1)[1].split('}}', 1)[0]
            expression = re.sub(r"steps\.([a-z0-9_]+)\.outcome == '([a-z]+)'",
                                lambda match: str(outcomes[match[1]] == match[2]),
                                expression)
            expression = expression.replace('always()', 'True').replace('&&', ' and ').replace('||', ' or ')
            self.assertRegex(expression, r'^[\s()TrueFalsandor]+$')
            return eval(expression, {'__builtins__': {}}, {})

        self.assertTrue(selected(pre))
        self.assertFalse(selected(full))
        timers = {f'porota-rc6-{number:02d}.timer': 'inactive'
                  for number in range(16)}
        with tempfile.TemporaryDirectory(prefix='rc6-held-timers-') as folder:
            state = Path(folder) / 'timers.json'
            state.write_text(json.dumps(timers, sort_keys=True))
            fake_ssh = Path(folder) / 'ssh'
            fake_ssh.write_text('''#!/usr/bin/python3
import sys
command = ' '.join(sys.argv[1:])
assert 'shutil.disk_usage' in command
assert all(word not in command for word in ('systemctl', 'docker', 'sudo', '--action', 'rm -rf'))
print('RC6_PRE_SUSPEND_DISK_FREE=4184719360')
''')
            fake_ssh.chmod(0o755)
            shell = pre.split('        run: |\n', 1)[1]
            shell = '\n'.join(line[10:] if line.startswith('          ') else line
                              for line in shell.splitlines())
            env = dict(os.environ, PATH=folder + ':/usr/bin:/bin', HOME=folder,
                       DO_USER='fixture', DO_HOST='fixture')
            result = PROCESS_RUN(['bash', '-c', shell], capture_output=True,
                                 text=True, env=env, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('RC6_PRE_SUSPEND_DISK_FREE=4184719360', result.stdout)
            self.assertEqual(json.loads(state.read_text()), timers)
        outcomes.update(classify='success', pre_suspend_snapshot='success',
                        diskguard='failure')
        self.assertTrue(selected(pre))
        self.assertFalse(selected(full))
        retire = workflow.split('      - name: Retire only unchanged own pre-suspend attempt barrier', 1)[1].split('      - name:', 1)[0]
        outcomes.update(a151_intraday_auth='failure', diskguard='skipped')
        self.assertTrue(selected(pre))
        self.assertFalse(selected(full))
        self.assertIn("steps.a151_intraday_auth.outcome == 'failure'", retire)
        outcomes.update(a151_intraday_auth='success', restatement_continuity='failure')
        self.assertTrue(selected(pre))
        self.assertFalse(selected(full))
        self.assertIn("steps.restatement_continuity.outcome == 'failure'", retire)
        self.assertIn("steps.diskguard.outcome == 'failure'", retire)
        self.assertIn("steps.suspend.outcome == 'skipped'", retire)
        self.assertIn("steps.pre_suspend_account.outcome == 'success'", retire)
        self.assertIn('--phase cleanup_presuspend python3 -', retire)
        self.assertIn('rc6_pre_suspend_fingerprint.py', retire)
        self.assertNotIn('systemctl start', retire)
        self.assertNotIn('--action restore', retire)


if __name__ == '__main__':
    unittest.main()
