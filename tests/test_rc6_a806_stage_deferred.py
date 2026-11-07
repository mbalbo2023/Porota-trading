"""A806 fail-first safety contract for deploy staging on a shared host.

Staging has no independent root-issued provenance yet.  A matching name,
same UID or DO_USER-written receipt must not authorize deletion.  A safe
deferred result is preferable to ever touching Boca or other applications.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from scripts import rc6_deploy_failure_cleanup as cleanup


CANDIDATE = "7" * 40
RUN = "44556677"
ATTEMPT = "2"


class SharedHostStagingDeferredTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="rc6-a806-deferred-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.stage = self.base / f"porota-deploy-v2-{CANDIDATE}-{RUN}-{ATTEMPT}"

    def run_cleanup(self):
        # Offhost fixtures simulate *only* candidate-specific inspection;
        # a Docker mutation would fail this test, not quietly be mocked away.
        with (patch.object(cleanup, "REMOTE_ROOT", self.base),
              patch.object(cleanup, "image_id", return_value=None),
              patch.object(cleanup, "container_inventory", return_value=(set(), [])),
              patch.object(cleanup, "host_mountpoints", return_value=[Path("/")]),
              patch.object(cleanup, "docker", side_effect=AssertionError("MUTATION_FORBIDDEN"))):
            return cleanup.cleanup(CANDIDATE, RUN, ATTEMPT)

    def test_foreign_exact_name_retained_without_root_attestation(self):
        self.stage.mkdir(mode=0o700)
        foreign = self.stage / "foreign-app-data"
        foreign.write_text("MUST_SURVIVE")
        result = self.run_cleanup()
        self.assertEqual(result["status"], "RED")
        self.assertIn("STAGING_CLEANUP_DEFERRED_UNVERIFIED", result["errors"])
        self.assertFalse(result["staging_removed"])
        self.assertEqual(foreign.read_text(), "MUST_SURVIVE")

    def test_do_user_self_receipt_does_not_allow_stage_delete(self):
        self.stage.mkdir(mode=0o700)
        nested = self.stage / "stage"
        nested.mkdir(mode=0o700)
        foreign = nested / "foreign-data"
        foreign.write_text("KEEP")
        # This receipt is *not* root attestation.  It can be forged by any
        # other process that shares the SSH user's UID.
        (self.stage / ".rc6-exact-stage-owner.json").write_text(json.dumps({
            "candidate": CANDIDATE, "run": RUN, "attempt": ATTEMPT,
            "root": {"uid": os.geteuid(), "ino": os.lstat(self.stage).st_ino},
        }))
        result = self.run_cleanup()
        self.assertEqual(result["status"], "RED")
        self.assertIn("STAGING_CLEANUP_DEFERRED_UNVERIFIED", result["errors"])
        self.assertEqual(foreign.read_text(), "KEEP")

    def test_missing_mutex_must_not_yield_write_access(self):
        self.stage.mkdir(mode=0o700)
        with patch.object(cleanup, "REMOTE_ROOT", self.base):
            with self.assertRaises(cleanup.CleanupRejected):
                with cleanup.promotion_lock():
                    self.fail("MISSING_MUTEX_MUST_NOT_YIELD")
        self.assertTrue(self.stage.is_dir())

    def test_no_stage_at_start_does_not_claim_this_cleanup_removed_it(self):
        result = self.run_cleanup()
        self.assertEqual(result['status'], 'RED')
        self.assertFalse(result['staging_removed'], 'No deletion took place')
        self.assertEqual(result['space_recovered'], 0)

    def test_external_disk_delta_does_not_claim_host_bytes_reclaimed(self):
        # The disk might change while a failed cleanup exits. It is not
        # legitimate to attribute that free-space delta to this no-op.
        with (patch.object(cleanup.shutil, 'disk_usage', side_effect=[
                    SimpleNamespace(free=1000), SimpleNamespace(free=1200)]),
              patch('builtins.print') as output):
            self.assertEqual(cleanup.main(['--candidate-sha', CANDIDATE]), 2)
        result = json.loads(output.call_args.args[0])
        self.assertEqual(result['host_space_recovered'], 0)

    def test_inline_preopen_has_no_recursive_or_file_delete(self):
        workflow = (Path(__file__).parents[1] / ".github" / "workflows" /
                    "porota-deploy-v2-promote.yml").read_text()
        block = workflow.split('RC6_ZERO_KNOWN_ERROR_IMMEDIATE=GREEN', 1)[1]
        block = block.split("DISK_PREOPEN_AFTER_SAFE_RECLAIM=", 1)[0]
        self.assertNotIn("shutil.rmtree", block)
        self.assertNotIn(".unlink(", block)
        self.assertIn("RC6_PREOPEN_EXACT_STAGE_RECLAIM=DEFERRED_UNVERIFIED", block)

    def test_inline_never_claims_green_reclaim_of_retained_stage(self):
        workflow = (Path(__file__).parents[1] / ".github" / "workflows" /
                    "porota-deploy-v2-promote.yml").read_text()
        block = workflow.split('RC6_ZERO_KNOWN_ERROR_IMMEDIATE=GREEN', 1)[1]
        block = block.split("# External source sampling", 1)[0]
        self.assertNotIn("RC6_PREOPEN_SAFE_RECLAIM=GREEN", block)
        self.assertIn("RC6_PREOPEN_SAFE_RECLAIM=DEFERRED_UNVERIFIED", block)
        self.assertIn('test "$DISK_PREOPEN_AFTER_SAFE_RECLAIM" -ge "$POST_CLEANUP_MIN_FREE"', block)

    def test_provenance_no_go_precedes_every_remote_host_contact(self):
        """A source-level RED must stop before reconcile/suspend/transfer/promote."""
        workflow_path = (Path(__file__).parents[1] / '.github' / 'workflows' /
                         'porota-deploy-v2-promote.yml')
        steps = yaml.safe_load(workflow_path.read_text())['jobs']['promote']['steps']
        names = [step.get('name', '') for step in steps]
        gate_matches = [(idx, step) for idx, step in enumerate(steps)
                        if step.get('id') == 'a806_staging_preadmission']
        self.assertEqual(len(gate_matches), 1, 'Exactly one pre-host staging safety gate')
        gate_idx, gate = gate_matches[0]
        self.assertEqual(gate_idx, 1, 'Gate must immediately follow runner checkout')
        self.assertIn('Checkout production promotion commit', names[0])
        self.assertNotIn('ssh ', gate['run'])
        self.assertNotIn('sudo ', gate['run'])
        self.assertNotIn('docker ', gate['run'])
        first_host = next(i for i, name in enumerate(names)
                          if 'DigitalOcean Droplet' in name or 'host barrier' in name)
        suspend = next(i for i, name in enumerate(names) if 'Suspend versioned' in name)
        transfer = next(i for i, name in enumerate(names) if 'Transfer frozen artifact' in name)
        promotion = next(i for i, name in enumerate(names) if 'Promote exact frozen' in name)
        self.assertLess(gate_idx, min(first_host, suspend, transfer, promotion))

        # Run only the early runner-local step, never downstream host writers.
        marker = subprocess.run(['bash', '-Eeuo', 'pipefail', '-c', gate['run']],
                                cwd=workflow_path.parents[2], capture_output=True,
                                text=True, timeout=5, check=False)
        self.assertEqual(marker.returncode, 79, marker.stdout + marker.stderr)
        self.assertIn('RC6_DEPLOY_V2=NO_GO_PREMUTATION_STAGING_UNVERIFIED',
                      marker.stdout)
        self.assertIn('STAGING_CLEANUP=DEFERRED_UNVERIFIED', marker.stdout)

    def test_early_provenance_failure_cannot_trigger_host_recovery(self):
        workflow_path = (Path(__file__).parents[1] / '.github' / 'workflows' /
                         'porota-deploy-v2-promote.yml')
        steps = yaml.safe_load(workflow_path.read_text())['jobs']['promote']['steps']
        gates = [idx for idx, step in enumerate(steps)
                 if step.get('id') == 'a806_staging_preadmission']
        self.assertEqual(len(gates), 1)
        gate_idx = gates[0]
        for step in steps[gate_idx + 1:]:
            if step.get('id') not in ('failure_cleanup', 'failure_orphan_cleanup'):
                continue
            expression = step.get('if', '')
            self.assertNotIn('steps.a806_staging_preadmission.outcome', expression)
            if step['id'] == 'failure_cleanup':
                self.assertIn('steps.suspend.outcome', expression)
            if step['id'] == 'failure_orphan_cleanup':
                self.assertIn("steps.failure_cleanup.outcome == 'success'", expression)
            self.assertNotIn('steps.a806_staging_preadmission.conclusion', expression)


class RootlessPromotionPrehostContainmentTests(unittest.TestCase):
    """A954: legacy rootful source can only remain behind an actual job veto.

    The positive expected outcome is a protected HOLD, never rootless approval.
    All mutations are isolated in memory; no host/Docker/Action is contacted.
    """

    @staticmethod
    def workflow():
        source = Path(__file__).parents[1] / '.github' / 'workflows' / 'porota-deploy-v2-promote.yml'
        return yaml.safe_load(source.read_text(encoding='utf-8'))

    @staticmethod
    def check(workflow):
        from scripts.rc6_deploy_legacy_host_guard import classify_promotion_host_boundary

        return classify_promotion_host_boundary(workflow)

    def assert_rejected(self, workflow, reason):
        from scripts.rc6_deploy_legacy_host_guard import LegacyHostBoundaryViolation

        with self.assertRaisesRegex(LegacyHostBoundaryViolation, reason):
            self.check(workflow)

    def test_existing_rootful_lifecycle_is_held_offhost_not_approved(self):
        contract = self.check(self.workflow())
        self.assertEqual(contract['status'], 'BLOQUEADO')
        self.assertEqual(contract['prehost_exit'], 79)
        self.assertGreaterEqual(contract['rootful_docker_references'], 71)
        self.assertFalse(contract['rootless_certified'])
        self.assertFalse(contract['host_contact_authorized'])

    def test_missing_early_gate_cannot_turn_legacy_deploy_green(self):
        workflow = self.workflow()
        del workflow['jobs']['promote']['steps'][1]
        self.assert_rejected(workflow, 'A806_GATE')

    def test_unreviewed_checkout_variant_cannot_replace_first_action(self):
        workflow = self.workflow()
        workflow['jobs']['promote']['steps'][0]['uses'] = 'actions/checkout@v4-unknown'
        self.assert_rejected(workflow, 'UNSAFE_PROMOTION_CHECKOUT')

    def test_checkout_cannot_expand_prehost_network_or_executable_scope(self):
        for field, value in (
            ('ssh-key', '${{ secrets.DO_SSH_KEY }}'),
            ('submodules', 'recursive'),
            ('repository', 'attacker/alternative'),
        ):
            with self.subTest(field=field):
                workflow = self.workflow()
                workflow['jobs']['promote']['steps'][0]['with'][field] = value
                self.assert_rejected(workflow, 'UNSAFE_PROMOTION_CHECKOUT')
        workflow = self.workflow()
        workflow['jobs']['promote']['steps'][0]['run'] = 'ssh -T "$DO_USER@$DO_HOST" true'
        self.assert_rejected(workflow, 'UNSAFE_PROMOTION_CHECKOUT')

    def test_workflow_and_job_envelopes_cannot_add_prehost_execution_surfaces(self):
        for scope in ('workflow_defaults', 'job_defaults', 'self_hosted', 'services',
                      'container', 'job_shell'):
            with self.subTest(scope=scope):
                workflow = self.workflow()
                job = workflow['jobs']['promote']
                if scope == 'workflow_defaults':
                    workflow['defaults'] = {'run': {'working-directory': '../'}}
                    error = 'UNSAFE_PROMOTION_WORKFLOW'
                else:
                    error = 'UNSAFE_PROMOTION_JOB'
                    if scope == 'job_defaults':
                        job['defaults'] = {'run': {'working-directory': '../'}}
                    elif scope == 'self_hosted':
                        job['runs-on'] = 'self-hosted'
                    elif scope == 'services':
                        job['services'] = {'daemon': {'image': 'docker:latest'}}
                    elif scope == 'container':
                        job['container'] = 'ubuntu:latest'
                    else:
                        job['shell'] = 'sh'
                self.assert_rejected(workflow, error)

    def test_duplicate_gate_cannot_hide_late_unprotected_path(self):
        workflow = self.workflow()
        steps = workflow['jobs']['promote']['steps']
        steps.insert(3, deepcopy(steps[1]))
        self.assert_rejected(workflow, 'A806_GATE')

    def test_early_gate_cannot_be_conditional_or_continued(self):
        for mutation in ('if', 'continue-on-error', 'shell', 'env'):
            with self.subTest(mutation=mutation):
                workflow = self.workflow()
                gate = workflow['jobs']['promote']['steps'][1]
                gate[mutation] = ('${{ false }}' if mutation == 'if' else
                                  True if mutation == 'continue-on-error' else
                                  'sh' if mutation == 'shell' else {'BASH_ENV': '/tmp/injected'})
                self.assert_rejected(workflow, 'A806_GATE')

    def test_job_level_failure_suppression_cannot_make_false_green(self):
        workflow = self.workflow()
        workflow['jobs']['promote']['continue-on-error'] = True
        self.assert_rejected(workflow, 'JOB_FAILURE_SUPPRESSION')

    def test_second_job_cannot_bypass_promotion_prehost_veto(self):
        workflow = self.workflow()
        workflow['jobs']['new-host-job'] = {
            'runs-on': 'ubuntu-24.04',
            'if': '${{ always() }}',
            'steps': [{'name': 'Bypass protected deploy job',
                       'run': 'ssh -T "$DO_USER@$DO_HOST" true'}],
        }
        self.assert_rejected(workflow, 'UNREVIEWED_PROMOTION_JOB')

    def test_inherited_shell_startup_env_cannot_run_before_prehost_veto(self):
        for scope in ('workflow', 'job', 'checkout'):
            for variable in ('BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS'):
                with self.subTest(scope=scope, variable=variable):
                    workflow = self.workflow()
                    if scope == 'workflow':
                        environment = workflow.setdefault('env', {})
                    elif scope == 'job':
                        environment = workflow['jobs']['promote'].setdefault('env', {})
                    else:
                        environment = workflow['jobs']['promote']['steps'][0].setdefault('env', {})
                    environment[variable] = '/tmp/hidden-shell-startup'
                    self.assert_rejected(workflow, 'UNTRUSTED_SHELL_ENV_BEFORE_A806')

    def test_early_gate_must_not_accept_echo_or_exit_zero(self):
        for body in ('set -Eeuo pipefail\necho exit 79\nexit 0\n',
                     'set -Eeuo pipefail\nexit 0\nexit 79\n'):
            with self.subTest(body=body):
                workflow = self.workflow()
                workflow['jobs']['promote']['steps'][1]['run'] = body
                self.assert_rejected(workflow, 'A806_GATE')

    def test_early_gate_must_not_execute_host_commands_before_veto(self):
        workflow = self.workflow()
        workflow['jobs']['promote']['steps'][1]['run'] = (
            'ssh -T "$DO_USER@$DO_HOST" true\n'
            + workflow['jobs']['promote']['steps'][1]['run'])
        self.assert_rejected(workflow, 'A806_GATE')

    def test_failure_orphan_cleanup_cannot_run_on_prehost_stop(self):
        workflow = self.workflow()
        next(step for step in workflow['jobs']['promote']['steps']
             if step.get('id') == 'failure_orphan_cleanup')['if'] = '${{ always() }}'
        self.assert_rejected(workflow, 'HOST_CONTACT_AFTER_A806')

    def test_reconcile_cannot_get_always_override(self):
        workflow = self.workflow()
        next(step for step in workflow['jobs']['promote']['steps']
             if step.get('id') == 'host_reconcile')['if'] = '${{ always() }}'
        self.assert_rejected(workflow, 'HOST_CONTACT_AFTER_A806')

    def test_reconcile_cannot_run_from_gate_failure_status(self):
        workflow = self.workflow()
        next(step for step in workflow['jobs']['promote']['steps']
             if step.get('id') == 'host_reconcile')['if'] = (
                 "${{ always() && steps.a806_staging_preadmission.outcome == 'failure' }}")
        self.assert_rejected(workflow, 'HOST_CONTACT_AFTER_A806')

    def test_reconcile_failure_status_cannot_run_host(self):
        workflow = self.workflow()
        next(step for step in workflow['jobs']['promote']['steps']
             if step.get('id') == 'host_reconcile')['if'] = '${{ failure() }}'
        self.assert_rejected(workflow, 'HOST_CONTACT_AFTER_A806')

    def test_intermediary_always_or_failure_step_cannot_unlock_later_host_step(self):
        for condition in ('${{ always() }}', '${{ failure() }}'):
            with self.subTest(condition=condition):
                workflow = self.workflow()
                workflow['jobs']['promote']['steps'].insert(2, {
                    'id': 'intermediary_step', 'name': 'Runs despite the veto',
                    'if': condition, 'run': 'exit 1',
                })
                self.assert_rejected(workflow, 'POST_A806_NONTERMINAL_EXECUTION')

    def test_indirect_network_after_gate_is_forbidden_without_command_blacklist(self):
        commands = (
            'curl --fail https://$DO_HOST/health',
            "python3 -c 'import socket,os;socket.create_connection((os.environ[\"DO_HOST\"],22))'",
            'docker --host tcp://$DO_HOST:2375 ps',
            "bash -c 'exec 3<>/dev/tcp/$DO_HOST/22'",
        )
        for command in commands:
            with self.subTest(command=command):
                workflow = self.workflow()
                step = next(s for s in workflow['jobs']['promote']['steps']
                            if s.get('id') == 'pre_suspend_account')
                step['if'], step['run'] = '${{ always() }}', command
                self.assert_rejected(workflow, 'POST_A806_NONTERMINAL_EXECUTION')

    def test_missing_env_and_cancelled_status_cannot_fool_static_skip_model(self):
        # GitHub resolves missing env vars to empty strings; a single
        # synthetic NONEMPTY value would falsely classify this as skipped.
        for condition in ("${{ always() && env.UNKNOWN == '' }}", '${{ cancelled() }}'):
            with self.subTest(condition=condition):
                workflow = self.workflow()
                step = next(s for s in workflow['jobs']['promote']['steps']
                            if s.get('id') == 'pre_suspend_account')
                step['if'] = condition
                step['run'] = 'python3 -c "import socket;socket.socket()"'
                self.assert_rejected(workflow, 'UNREVIEWED_POST_A806_STATUS_CONDITION')

    def test_runner_cleanup_must_be_terminal_after_prehost_veto(self):
        workflow = self.workflow()
        workflow['jobs']['promote']['steps'].append({
            'name': 'Later unexpected step', 'if': '${{ always() }}',
            'run': 'echo unexpected',
        })
        self.assert_rejected(workflow, 'POST_A806_NONTERMINAL_EXECUTION')

    def test_runner_cleanup_cannot_be_repurposed_to_contact_host(self):
        workflow = self.workflow()
        workflow['jobs']['promote']['steps'][-1]['run'] += (
            '\nssh -T "$DO_USER@$DO_HOST" true\n')
        self.assert_rejected(workflow, 'HOST_CONTACT_AFTER_A806')

    def test_runner_cleanup_after_hold_never_removes_unowned_fixed_tmp_paths(self):
        """A982: a job veto still runs its always() runner cleanup.

        Intercept `rm` before executing the real step: executing the historical
        literal `/tmp/porota-*` cleanup against this shared machine is unsafe.
        A separate foreign sentinel represents data that must remain untouched.
        """
        workflow = self.workflow()
        runner = workflow['jobs']['promote']['steps'][-1]
        self.assertEqual(runner['name'], 'Runner cleanup')
        self.assertEqual(runner['if'], 'always()')
        shell = runner['run']
        # Only this historically reviewed command vocabulary may be exercised
        # in a local subprocess. An unexpected executor must fail before launch.
        for line in shell.splitlines():
            command = line.strip()
            if command and not command.startswith('#'):
                self.assertTrue(command.startswith(('rm ', 'echo ', 'set ')), command)

        with tempfile.TemporaryDirectory(prefix='rc6-a982-foreign-') as temp:
            base = Path(temp)
            foreign = base / 'foreign-tmp' / 'porota-frozen-v2' / 'keep.txt'
            foreign.parent.mkdir(parents=True)
            foreign.write_text('FOREIGN_MUST_SURVIVE', encoding='utf-8')
            fake_bin = base / 'fake-bin'
            fake_bin.mkdir()
            fake_rm = fake_bin / 'rm'
            fake_rm.write_text('#!/bin/sh\nprintf "RM_ATTEMPT:%s\\n" "$*" >&2\nexit 97\n',
                               encoding='utf-8')
            fake_rm.chmod(0o755)
            env = {'PATH': f'{fake_bin}:/usr/bin:/bin', 'HOME': str(base), 'LC_ALL': 'C'}
            result = subprocess.run(['bash', '-Eeuo', 'pipefail', '-c', shell],
                                    cwd=base, env=env, capture_output=True,
                                    text=True, timeout=5, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn('RM_ATTEMPT:', result.stderr)
            self.assertIn('POROTA_DEPLOY_V2_RUNNER_CLEANUP=DEFERRED_UNVERIFIED',
                          result.stdout)
            self.assertNotIn('RUNNER_CLEANUP=GREEN', result.stdout)
            self.assertEqual(foreign.read_text(encoding='utf-8'),
                             'FOREIGN_MUST_SURVIVE')

    def test_runner_cleanup_cannot_run_an_arbitrary_action_after_veto(self):
        workflow = self.workflow()
        cleanup = workflow['jobs']['promote']['steps'][-1]
        del cleanup['run']
        cleanup['uses'] = 'some-remote-user/host-automation@v1'
        self.assert_rejected(workflow, 'UNREVIEWED_ALWAYS_ACTION_AFTER_A806')

    def test_always_runner_cleanup_cannot_add_indirect_python_host_call(self):
        workflow = self.workflow()
        workflow['jobs']['promote']['steps'][-1]['run'] += (
            "python -c 'import urllib.request; urllib.request.urlopen(\"https://example.invalid\")'\n")
        self.assert_rejected(workflow, 'UNREVIEWED_ALWAYS_TERMINAL')

    def test_rootless_string_rewrite_alone_does_not_certify_daemon(self):
        workflow = self.workflow()
        for step in workflow['jobs']['promote']['steps']:
            if 'run' in step:
                step['run'] = step['run'].replace('sudo -n docker', 'docker')
        self.assert_rejected(workflow, 'NO_INDEPENDENT_ROOTLESS_ATTESTATION')


if __name__ == "__main__":
    unittest.main()
