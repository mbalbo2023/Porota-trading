"""Direct owner intent: authenticated GitHub inputs and bounded, scoped transport."""
import copy
from contextlib import contextmanager
import importlib
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE, TREE, PRODUCT = 'a' * 40, 'b' * 40, 'c' * 40
REPOSITORY = 'mbalbo2023/Porota-trading'


class ActionsIntentTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('scripts.porota_actions_intent'),
                             'Direct Actions intent implementation is missing')
        self.mod = importlib.import_module('scripts.porota_actions_intent')
        self.env = {'GITHUB_REPOSITORY': REPOSITORY, 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                    'GITHUB_REF': 'refs/heads/deploy/rc6-pr69-isolated-20260915',
                    'GITHUB_SHA': PRODUCT, 'GITHUB_ACTOR': 'mbalbo2023',
                    'GITHUB_TRIGGERING_ACTOR': 'mbalbo2023', 'GITHUB_RUN_ID': '900'}
        self.comment = {'id': 123, 'issue_url': 'https://api.github.com/repos/' + REPOSITORY + '/issues/624',
                        'user': {'login': 'mbalbo2023'}, 'author_association': 'OWNER',
                        'created_at': '2026-11-15T12:00:00Z', 'updated_at': '2026-11-15T12:00:00Z',
                        'body': f'RC6_624_PAUSE_PAPER candidate={SOURCE} tree={TREE} deploy={PRODUCT}'}
        self.runtime = {'id': 456, 'run_attempt': 2, 'status': 'completed', 'conclusion': 'success',
                        'head_sha': PRODUCT, 'path': '.github/workflows/porota-deploy-v2-promote.yml',
                        'head_repository': {'full_name': REPOSITORY}, 'event': 'workflow_dispatch',
                        'actor': {'login': 'mbalbo2023'}, 'triggering_actor': {'login': 'mbalbo2023'},
                        'updated_at': '2026-11-15T11:59:00Z'}
        self.calls = []

    def api(self, path):
        self.calls.append(path)
        if path.startswith('/git/ref/heads/'):
            return {'object': {'sha': self.env['GITHUB_SHA']}}
        if path == '/issues/comments/123':
            return self.comment
        if path == '/actions/runs/456':
            return self.runtime
        self.fail('Unexpected API path: ' + path)

    def authorize(self, operation='pause', **changes):
        with patch.object(self.mod.release, 'api', side_effect=self.api), \
             patch.object(self.mod.release, 'git', return_value=PRODUCT), \
             patch.object(self.mod.release, 'promotion_source', return_value=(SOURCE, TREE)):
            return self.mod.authorize(operation, 123, Path.cwd(), env=self.env | changes)

    def resume_comment(self):
        self.comment['body'] = (f'RC6_624_APPROVE_PAPER_REARM candidate={SOURCE} tree={TREE} '
                                f'deploy={PRODUCT} runtime_run=456')

    def test_pause_authenticates_exact_owner_comment_without_runtime_dependency(self):
        result = self.authorize()
        self.assertEqual(result['source_sha'], SOURCE)
        self.assertEqual(result['source_tree'], TREE)
        self.assertEqual(result['operator_comment_id'], 123)
        self.assertIsNone(result['runtime_run'])
        self.assertFalse(any('/actions/runs/' in path for path in self.calls))

    def test_resume_binds_successful_runtime_before_owner_comment(self):
        self.resume_comment()
        result = self.authorize('resume')
        self.assertEqual((result['runtime_run'], result['runtime_attempt']), (456, 2))

    @contextmanager
    def product_history(self, changed_path='README.md'):
        with tempfile.TemporaryDirectory(prefix='porota-intent-history-') as temporary:
            repository = Path(temporary)

            def git(*arguments):
                return subprocess.check_output(['git', '-C', str(repository), *arguments],
                                               stderr=subprocess.DEVNULL, text=True).strip()

            git('init', '-b', 'product')
            git('config', 'user.name', 'Intent fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            (repository/'app.py').write_text('MODE = "PAPER"\n')
            (repository/'README.md').write_text('# PAPER runtime\n')
            git('add', '.')
            git('commit', '-m', 'Base')
            git('checkout', '-b', 'candidate')
            (repository/'app.py').write_text('MODE = "PRODUCTION_PAPER"\n')
            git('commit', '-am', 'Candidate')
            source, tree = git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')
            git('checkout', 'product')
            git('merge', '--no-ff', '-m', 'Merge deployed candidate', 'candidate')
            deployed = git('rev-parse', 'HEAD')
            git('checkout', '-b', 'followup')
            path = repository/changed_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# Updated documentation\n' if path.suffix == '.md' else 'CHANGED = True\n')
            git('add', '.')
            git('commit', '-m', 'Followup')
            git('checkout', 'product')
            git('merge', '--no-ff', '-m', 'Merge followup', 'followup')
            current = git('rev-parse', 'HEAD')
            self.env['GITHUB_SHA'] = current
            self.runtime['head_sha'] = deployed
            self.comment['body'] = (f'RC6_624_APPROVE_PAPER_REARM candidate={source} tree={tree} '
                                    f'deploy={current} runtime_run=456')
            yield repository, source, tree, deployed, current, git

    def test_resume_after_markdown_merge_keeps_the_deployed_release_identity(self):
        with self.product_history() as (repository, source, tree, deployed, current, _), \
             patch.object(self.mod.release, 'api', side_effect=self.api):
            result = self.mod.authorize('resume', 123, repository, env=self.env)
        self.assertEqual((result['source_sha'], result['source_tree']), (source, tree))
        self.assertEqual(result['product_sha'], current)
        self.assertEqual((result['runtime_run'], result['runtime_attempt']), (456, 2))
        self.assertNotEqual(deployed, current)

    def test_resume_after_code_or_configuration_change_cannot_reuse_old_runtime(self):
        for changed in ('app.py', 'ops/policy/runtime.yaml'):
            with self.subTest(changed=changed), self.product_history(changed) as history, \
                 patch.object(self.mod.release, 'api', side_effect=self.api), \
                 self.assertRaises(ValueError):
                self.mod.authorize('resume', 123, history[0], env=self.env)

    def test_markdown_head_rejects_a_run_from_an_unmerged_branch_even_with_same_release_tree(self):
        with self.product_history() as (repository, source, tree, _, _, git), \
             patch.object(self.mod.release, 'api', side_effect=self.api):
            self.runtime['head_sha'] = git('commit-tree', tree, '-p', source, '-m', 'Unmerged run')
            with self.assertRaises(ValueError):
                self.mod.authorize('resume', 123, repository, env=self.env)

    def test_markdown_head_rejects_a_run_or_comment_with_a_different_release_tree(self):
        for damage in ('run', 'comment'):
            with self.subTest(damage=damage), self.product_history() as history, \
                 patch.object(self.mod.release, 'api', side_effect=self.api):
                repository, source, tree, _, current, _ = history
                if damage == 'run':
                    self.runtime['head_sha'] = current
                else:
                    self.comment['body'] = self.comment['body'].replace('tree='+tree, 'tree='+'d'*40)
                with self.assertRaises(ValueError):
                    self.mod.authorize('resume', 123, repository, env=self.env)

    def test_foreign_or_untrusted_dispatch_is_rejected_before_api(self):
        for key, value in [('GITHUB_REPOSITORY', 'foreign/repo'), ('GITHUB_ACTOR', 'other'),
                           ('GITHUB_TRIGGERING_ACTOR', 'other'), ('GITHUB_EVENT_NAME', 'push'),
                           ('GITHUB_REF', 'refs/heads/main'), ('GITHUB_RUN_ID', '0'),
                           ('GITHUB_SHA', 'not-a-sha')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.authorize(**{key: value})
        self.assertEqual(self.calls, [])

    def test_edited_foreign_or_wrong_scope_comment_is_rejected(self):
        original = copy.deepcopy(self.comment)
        for key, value in [('id', True), ('user', {'login': 'other'}),
                           ('author_association', 'MEMBER'), ('issue_url', original['issue_url'] + '0'),
                           ('updated_at', '2026-11-15T12:01:00Z'), ('created_at', ''),
                           ('body', original['body'].replace(SOURCE, 'd' * 40)),
                           ('body', original['body'] + ' extra')]:
            self.comment = {**original, key: value}
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.authorize()

    def test_resume_rejects_unverified_foreign_later_or_wrong_runtime(self):
        self.resume_comment()
        original = copy.deepcopy(self.runtime)
        for key, value in [('id', 457), ('run_attempt', True), ('status', 'in_progress'),
                           ('conclusion', 'failure'), ('head_sha', 'd' * 40),
                           ('path', '.github/workflows/ci.yml'), ('event', 'pull_request'),
                           ('actor', {'login': 'other'}), ('triggering_actor', {'login': 'other'}),
                           ('head_repository', {'full_name': 'foreign/repo'}),
                           ('updated_at', '2026-11-15T12:00:00Z')]:
            self.runtime = {**original, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.authorize('resume')

    def test_product_head_drift_rejects_before_any_intent(self):
        with patch.object(self.mod.release, 'api', return_value={'object': {'sha': 'd' * 40}}), \
             patch.object(self.mod.release, 'promotion_source') as source:
            with self.assertRaisesRegex(ValueError, 'CURRENT_PRODUCT'):
                self.mod.authorize('pause', 123, Path.cwd(), env=self.env)
            source.assert_not_called()

    def test_checkout_must_be_the_authenticated_product_revision(self):
        with patch.object(self.mod.release, 'api', side_effect=self.api), \
             patch.object(self.mod.release, 'git', return_value='d' * 40), \
             patch.object(self.mod.release, 'promotion_source', return_value=(SOURCE, TREE)):
            with self.assertRaisesRegex(ValueError, 'CHECKOUT'):
                self.mod.authorize('pause', 123, Path.cwd(), env=self.env)

    @contextmanager
    def transport(self, *, operation='pause', failure=None, remote_result=None):
        if operation == 'resume':
            self.resume_comment()
        authorization = self.authorize(operation)
        result = remote_result or {'status': self.mod.STATUS[operation],
                                    'operator_comment_id': 123, 'run_id': 900}
        commands = []
        stage = '/home/porotaadmin/.local/state/porota-rc6/stage.Test123'

        @contextmanager
        def connection(_):
            yield ['ssh'], ['scp'], 'porotaadmin@fixture.invalid'

        def ssh(_ssh, command, **_):
            commands.append(command)
            if self.mod.host.STAGE_CREATE in command:
                return SimpleNamespace(returncode=0, stdout=(stage + '\n').encode())
            if self.mod.host.STAGE_REMOVE in command:
                return SimpleNamespace(returncode=0, stdout=b'')
            if failure == 'timeout':
                raise ValueError('SSH_TRANSPORT_FAILED')
            return SimpleNamespace(returncode=255 if failure == 'disconnect' else 1 if failure == 'exit' else 0,
                                   stdout=json.dumps(result).encode())

        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / 'scripts').mkdir()
            for name in ('porota_operator_intent.py', 'rc6_operator_paper_rearm.py', 'porota_rootless_host.py'):
                (repository / 'scripts' / name).write_text('# Fixture source\n')
            with patch.object(self.mod.host, 'connection', connection), \
                 patch.object(self.mod.host, 'ssh_run', side_effect=ssh), \
                 patch.object(self.mod.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as upload:
                yield authorization, repository, commands, upload

    def test_transport_stages_only_intent_sources_and_uses_no_runtime_operation(self):
        with self.transport() as (authorization, repository, calls, upload):
            result = self.mod.apply(authorization, repository, env=self.env)
        self.assertEqual(result['intent_status'], 'PAPER_PAUSED_BY_OPERATOR')
        self.assertEqual(result['runtime'], 'NO_VERIFICADO')
        self.assertEqual(result['transfer_cleanup'], 'COMPLETE')
        create = calls[0]
        self.assertEqual(create[-1], 'staging-only')
        self.assertLess(int(create[-2]), 1024 * 1024)
        command = calls[1]
        self.assertIn('pause', command)
        self.assertIn('--candidate-sha', command)
        self.assertIn(SOURCE, command)
        self.assertFalse(any(value in command for value in ('sudo', 'systemctl', 'docker')))
        self.assertTrue(upload.call_args.args[0][-1].endswith('/stage.Test123/'))

    def test_unknown_ssh_outcome_preserves_staging(self):
        for failure in ('timeout', 'disconnect'):
            with self.subTest(failure=failure), self.transport(failure=failure) as args:
                authorization, repository, calls, _ = args
                with self.assertRaises(self.mod.host.HostFailure) as raised:
                    self.mod.apply(authorization, repository, env=self.env)
                self.assertEqual(raised.exception.receipt['transfer_cleanup'], 'RETAINED_COMMAND_OUTCOME_UNKNOWN')
                self.assertFalse(any(self.mod.host.STAGE_REMOVE in command for command in calls))

    def test_resume_transports_the_verified_runtime_run_and_attempt(self):
        with self.transport(operation='resume') as (authorization, repository, calls, _):
            result = self.mod.apply(authorization, repository, env=self.env)
        command = calls[1]
        self.assertEqual(command[command.index('--runtime-run') + 1], '456')
        self.assertEqual(command[command.index('--runtime-attempt') + 1], '2')
        self.assertEqual(result['intent_status'], 'PAPER_REARM_AUTHORIZED')

    def test_transported_stage_executes_resume_with_private_release_and_explicit_backend(self):
        self.resume_comment()
        authorization = self.authorize('resume')
        run = subprocess.run
        # Only the account lookup is a fixture. Run the transported CLI and
        # real custody/hash readers in a fresh process without the repo path.
        bootstrap = '''import os,runpy,sys
from pathlib import Path
script,home,*arguments=sys.argv[1:]
sys.path.insert(0,str(Path(script).parents[1]))
from scripts import porota_rootless_host as host
def account(cls):
    assert os.environ.get('POROTA_RUNTIME_BACKEND') == 'ROOTLESS'
    return host.Identity(os.getuid(),os.getgid(),'fixture',Path(home))
host.Identity.current=classmethod(account)
sys.argv=[script,*arguments]
runpy.run_path(script,run_name='__main__')
'''
        with tempfile.TemporaryDirectory(prefix='porota-intent-stage-') as temporary:
            home = Path(temporary) / 'home'
            state = home / '.local/state/porota-rc6'
            stage = state / 'stage.Test123'
            stage.mkdir(parents=True, mode=0o700)
            operator = home / '.config/porota-rc6/operator'
            operator.mkdir(parents=True, mode=0o700)
            (operator/'migration.json').write_text('{}')
            release = {'schema': 'porota.release.v1', 'source_sha': SOURCE, 'source_tree': TREE,
                       'image_id': 'sha256:'+'d'*64, 'operating_mode': 'PRODUCTION_PAPER',
                       'execution_mode': 'SIMULATION', 'real_order_capability': 'BLOCKED',
                       'real_orders_sent_required': 0}
            raw = json.dumps(release).encode()
            manifest = state / 'releases' / SOURCE / 'release.json'
            manifest.parent.mkdir(parents=True, mode=0o700)
            manifest.write_bytes(raw)
            manifest.chmod(0o600)
            current = state / 'current.json'
            current.write_text(json.dumps({**release, 'schema': 'porota.rootless.deploy.v1',
                'state': 'VALIDADO_RUNTIME', 'real_orders_sent': 0,
                'release_manifest_sha256': hashlib.sha256(raw).hexdigest()}))
            current.chmod(0o600)

            @contextmanager
            def connection(_):
                yield ['ssh'], ['scp'], 'fixture@local.invalid'

            def upload(command, **_):
                self.assertEqual(command[0], 'scp')
                shutil.copytree(command[-2], stage/'source')
                return SimpleNamespace(returncode=0)

            def ssh(_ssh, command, **_):
                if self.mod.host.STAGE_CREATE in command:
                    return SimpleNamespace(returncode=0, stdout=(str(stage)+'\n').encode())
                if self.mod.host.STAGE_REMOVE in command:
                    return SimpleNamespace(returncode=0, stdout=b'')
                self.assertEqual(command[:2], ['/usr/bin/env', 'POROTA_RUNTIME_BACKEND=ROOTLESS'])
                script_index = command.index('-B') + 1
                # A direct CLI invocation must also override a stale backend
                # and derive its home from account identity rather than HOME.
                environment = {key: value for key, value in os.environ.items()
                               if key not in {'PYTHONPATH', 'PYTHONHOME'}}
                environment.update(HOME=str(Path(temporary)/'wrong-home'), POROTA_RUNTIME_BACKEND='LEGACY')
                return run([sys.executable, '-I', '-B', '-c', bootstrap,
                            command[script_index], str(home), *command[script_index+1:]],
                           cwd=stage, env=environment, capture_output=True, timeout=15)

            with patch.object(self.mod.host, 'connection', connection), \
                 patch.object(self.mod.host, 'ssh_run', side_effect=ssh), \
                 patch.object(self.mod.subprocess, 'run', side_effect=upload):
                result = self.mod.apply(authorization, Path(__file__).resolve().parents[1], env=self.env)
            self.assertEqual(result['intent_status'], self.mod.STATUS['resume'])
            self.assertEqual(result['transfer_cleanup'], 'COMPLETE')
            saved = json.loads((operator/'paper-rearm-v1.json').read_bytes())
            self.assertEqual(saved['image_id'], release['image_id'])
            self.assertEqual(saved['candidate_sha'], SOURCE)
            self.assertEqual(saved['candidate_tree_sha'], TREE)
            self.assertEqual(saved['runtime_validation_run_id'], 456)
            self.assertFalse((Path(temporary)/'wrong-home').exists())

    def test_nonzero_remote_exit_never_claims_persisted_intent(self):
        with self.transport(failure='exit') as (authorization, repository, calls, _):
            with self.assertRaises(ValueError):
                self.mod.apply(authorization, repository, env=self.env)
            self.assertTrue(any(self.mod.host.STAGE_REMOVE in command for command in calls))

    def test_result_must_match_action_comment_and_originating_run(self):
        authorization = self.authorize()
        result = {'status': 'PAPER_PAUSED_BY_OPERATOR', 'operator_comment_id': 123, 'run_id': 900}
        for key, value in [('status', 'PAPER_REARM_AUTHORIZED'), ('operator_comment_id', True),
                           ('operator_comment_id', 124), ('run_id', 899), ('run_id', 901)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.mod.validate_result({**result, key: value}, authorization, 0)
        self.mod.validate_result({**result, 'run_id': 899, 'idempotent': True}, authorization, 0)

    def test_workflow_dispatch_and_shell_syntax(self):
        import yaml
        path = Path(__file__).resolve().parents[1] / '.github/workflows/rc6-paper-rearm.yml'
        document = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(set(document['on']), {'workflow_dispatch'})
        self.assertEqual(document['concurrency'], {'group': 'rc6-unified-paper-deploy',
                                                  'cancel-in-progress': 'false'})
        for job in document['jobs'].values():
            self.assertIn("github.actor == 'mbalbo2023'", job['if'])
            self.assertIn("github.triggering_actor == 'mbalbo2023'", job['if'])
            for step in job['steps']:
                if 'run' in step:
                    subprocess.run(['bash', '-n'], input=step['run'], text=True, check=True)
                if 'authorize' in step.get('run', ''):
                    self.assertFalse(any('secrets.' in value for value in step.get('env', {}).values()))


if __name__ == '__main__':
    unittest.main()
