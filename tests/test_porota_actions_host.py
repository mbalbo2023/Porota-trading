import copy
import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import porota_actions_host as host


class HostTransportTests(unittest.TestCase):
    def setUp(self):
        self.release = {'source_sha': 'a'*40, 'source_tree': 'b'*40, 'image_id': 'sha256:'+'c'*64}
        self.receipt = {**self.release, 'schema': 'porota.rootless.deploy.v1', 'state': 'VALIDADO_RUNTIME',
                        'phase': 'COMPLETE', 'operating_mode': 'PRODUCTION_PAPER', 'execution_mode': 'SIMULATION',
                        'real_orders_sent': 0, 'roles': {'observer': 'd'*64, 'dashboard': 'e'*64},
                        'ppi_watch_touched': False, 'boca_touched': False,
                        'health': {'dashboard': 'healthy', 'observer': 'running', 'orders_guard': 'BLOCKED'},
                        'cleanup': {'state': 'COMPLETE', 'reclaimed_bytes': 0}}

    def test_only_complete_exact_paper_runtime_is_accepted(self):
        host.validate_runtime_receipt(self.receipt, self.release, 0)

    def test_green_exit_does_not_substitute_runtime_evidence(self):
        for key, value in [('state', 'DESPLEGADO'), ('phase', 'POSTFLIGHT'), ('source_sha', 'f'*40),
                           ('image_id', 'sha256:'+'f'*64), ('real_orders_sent', None),
                           ('real_orders_sent', False), ('real_orders_sent', 1),
                           ('ppi_watch_touched', True), ('boca_touched', True), ('roles', {}),
                           ('health', {'dashboard': 'starting'}), ('cleanup', {'state': 'INCOMPLETE'})]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                host.validate_runtime_receipt({**self.receipt, key: value}, self.release, 0)

    def test_nonzero_ssh_exit_is_failure_even_with_green_json(self):
        with self.assertRaises(ValueError):
            host.validate_runtime_receipt(self.receipt, self.release, 79)

    def test_promote_sends_only_the_verified_download_receipt(self):
        release = {**self.release, 'build_run_id': 70, 'build_run_attempt': 1, 'image_size_bytes': 1,
                   'members': {'porota-predeploy-image.tar.gz': {'bytes': 1},
                               'porota-deploy-bundle-v2.tgz': {'bytes': 1}}}
        download = {**self.release, 'schema': 'porota.release.download.v1',
                    'deployment_sha': 'd'*40, 'run_id': 70, 'run_attempt': 1,
                    'artifact_id': 88, 'artifact_digest': 'sha256:'+'e'*64}
        raw = json.dumps(download).encode()
        runtime = {**self.receipt, 'deployment_sha': download['deployment_sha'],
                   'download_receipt_sha256': hashlib.sha256(raw).hexdigest()}
        calls = []

        def remote(_connection, command, **_kwargs):
            calls.append(command)
            if host.STAGE_CREATE in command:
                return subprocess.CompletedProcess(command, 0,
                    b'/home/porotaadmin/.local/state/porota-rc6/stage.A12bc\n', b'')
            if host.SCHEDULER_UNIT_PROBE in command:
                self.assertEqual(command[-1], 'paper_opening')
                return subprocess.CompletedProcess(command, 0, json.dumps({
                    **self.release, 'schema': 'porota.rootless.job.v1',
                    'state': 'EXECUTED', 'real_orders_sent': 0, 'commands': [
                        {'source': 'PAPER_RELEASE_REVALIDATION', 'exit_code': 0,
                         'result': 'GO_PAPER_SCALPING', 'reason': 'PAPER_CURRENT_RELEASE_REVALIDATED'}],
                }).encode(), b'')
            return subprocess.CompletedProcess(command, 0, json.dumps(runtime).encode(), b'')

        def transfer(command, **_kwargs):
            self.assertEqual(Path(command[-2]).name, 'download.json')
            self.assertEqual(Path(command[-2]).read_bytes(), raw)
            return subprocess.CompletedProcess(command, 0, b'', b'')

        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo/'source').mkdir()
            release_dir = repo/'release'
            release_dir.mkdir()
            (release_dir/'release.json').write_text(json.dumps(release))
            (repo/'download.json').write_bytes(raw)
            env = {'GITHUB_RUN_ID': '99', 'GITHUB_RUN_ATTEMPT': '1'}
            with patch.object(host, 'connection', return_value=nullcontext((['ssh'], ['scp'], 'user@host'))), \
                 patch.object(host, 'ssh_run', side_effect=remote), \
                 patch.object(host.subprocess, 'run', side_effect=transfer), \
                 patch('scripts.porota_release.verify_release', return_value=release), \
                 patch('scripts.porota_actions_release.promotion_source',
                       return_value=(release['source_sha'], release['source_tree'])), \
                 patch('scripts.porota_actions_release.git', return_value='f'*40) as head:
                with self.assertRaisesRegex(ValueError, 'DOWNLOAD_DEPLOYMENT_CHECKOUT_MISMATCH'):
                    host.execute('deploy', repo, release_dir, env=env)
                self.assertEqual(calls, [])
                head.return_value = download['deployment_sha']
                observed = host.execute('deploy', repo, release_dir, env=env)
                opening = observed.pop('paper_opening')
                self.assertEqual(opening['result'], 'GO_PAPER_SCALPING')
                phases = observed.pop('deployment_phases')
                self.assertEqual(phases['physical_installation']['state'], 'DESPLEGADO')
                self.assertEqual(phases['initial_health']['state'], 'VALIDADO_RUNTIME')
                self.assertEqual(observed, {**runtime, 'transfer_cleanup': 'COMPLETE'})
                runtime.pop('download_receipt_sha256')
                with self.assertRaisesRegex(host.HostFailure, 'RUNTIME_DEPLOYMENT_PROVENANCE_MISMATCH'):
                    host.execute('deploy', repo, release_dir, env=env)

    def promotion_evidence(self):
        coordinates = {**self.release, 'attempt': '99-1'}
        runtime = {**self.receipt, **coordinates, 'market_provider': 'IOL_REST',
                   'paper_opening': {'state': 'OBSERVED', 'reason': 'BYMA_NON_OPERATIONAL_DAY'},
                   'installed_inventory': {**coordinates, 'scheduler': {'state': 'OBSERVED', 'requested_units': 5,
                       'observed_units': 5, 'operational_ppi_units': {}, 'scheduled_t10': {'state': 'NOT_DUE'},
                       'critical_bindings': {'state': 'VALIDADO_RUNTIME'}},
                       'runtime_metrics': {**coordinates, 'state': 'OBSERVED', 'real_orders_sent': 0}},
                   'synthetic_replay': {**coordinates, 'status': 'PASS', 'multi_instrument_replay': {'status': 'PASS'},
                       'network': 'NONE', 'operational_mounts': 0, 'real_orders_sent': 0}}
        dashboard = {**coordinates, 'state': 'VERIFICADO', 'schema': 'porota.dashboard.playwright.v1',
                     'dashboard_cid': runtime['roles']['dashboard'], 'read_only': True, 'real_orders_sent': 0}
        host.deployment_phases(runtime, host_verified=True)
        return runtime, dashboard

    def test_closed_market_keeps_technical_audit_valid_with_complete_evidence(self):
        runtime, dashboard = self.promotion_evidence()
        self.assertTrue(host.audit_promotion(runtime, dashboard, operation='deploy', host_succeeded=True,
                                            dashboard_succeeded=True))
        phases = runtime['deployment_phases']
        self.assertEqual(phases['integral_audit']['state'], 'VALIDADO_RUNTIME')
        self.assertTrue(all(phases['integral_audit']['checks'].values()))
        self.assertEqual(runtime['installed_inventory']['scheduler']['scheduled_t10']['state'], 'NOT_DUE')

    def test_late_failures_retain_physical_receipt_and_block_the_integral_audit(self):
        runtime, dashboard = self.promotion_evidence()
        physical = copy.deepcopy(runtime['deployment_phases']['physical_installation'])
        self.assertFalse(host.audit_promotion(runtime, dashboard, operation='deploy', host_succeeded=True,
                                             dashboard_succeeded=False))
        phases = runtime['deployment_phases']
        self.assertEqual(runtime['source_sha'], self.release['source_sha'])
        self.assertEqual(runtime['state'], 'VALIDADO_RUNTIME')
        self.assertEqual(phases['physical_installation'], physical)
        self.assertEqual(phases['integral_audit']['state'], 'BLOQUEADO')
        self.assertFalse(phases['integral_audit']['checks']['dashboard_playwright'])
        runtime['installed_inventory']['scheduler']['critical_bindings']['state'] = 'BLOQUEADO'
        self.assertFalse(host.audit_promotion(runtime, dashboard, operation='deploy', host_succeeded=True,
                                             dashboard_succeeded=True))
        self.assertTrue(phases['integral_audit']['checks']['dashboard_playwright'])
        self.assertFalse(phases['integral_audit']['checks']['critical_scheduler_bindings'])

    def test_audit_command_persists_verdict_before_returning_failure(self):
        runtime, _ = self.promotion_evidence()
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / 'runtime.json'
            receipt.write_text(json.dumps(runtime))
            argv = ['actions_host', 'audit-promotion', '--receipt', str(receipt),
                    '--dashboard-receipt', str(Path(temporary) / 'dashboard.json'),
                    '--promotion-operation', 'deploy', '--host-outcome', 'success', '--dashboard-outcome', 'failure']
            with patch('sys.argv', argv), self.assertRaises(SystemExit) as exc:
                host.main()
            self.assertEqual(exc.exception.code, 1)
            output = json.loads(receipt.read_text())
        self.assertEqual(output['deployment_phases']['integral_audit']['state'], 'BLOQUEADO')
        self.assertEqual(output['deployment_phases']['physical_installation']['state'], 'DESPLEGADO')
        self.assertEqual(output['source_sha'], self.release['source_sha'])

    def test_missing_iol_probe_is_advisory_but_explicit_wrong_runtime_blocks(self):
        with patch.object(host, 'ssh_run', return_value=subprocess.CompletedProcess([], 1, b'', b'private')):
            outcome = host.scheduler_postflight([], self.release, 'health', 'IOL_REST')
        self.assertEqual(outcome['state'], 'NO_VERIFICADO')
        self.assertEqual(outcome['reason'], 'SCHEDULER_PROBE_RECEIPT_INVALID')
        for changed, reason in (({'source_sha': 'd' * 40}, 'RUNTIME_RELEASE_MISMATCH'),
                                ({'real_orders_sent': 1}, 'RUNTIME_PAPER_ZERO_UNVERIFIED'),
                                ({'failure_signature': 'PAPER_DATABASE_SAFETY'}, 'PAPER_DATABASE_SAFETY')):
            data = {**self.release, 'state': 'BLOQUEADO', 'real_orders_sent': 0, **changed}
            with patch.object(host, 'ssh_run', return_value=subprocess.CompletedProcess([], 1, json.dumps(data).encode(), b'')):
                with self.assertRaisesRegex(ValueError, reason):
                    host.scheduler_postflight([], self.release, 'health', 'IOL_REST')

    def test_credentials_validate_without_echoing_their_values(self):
        env = {'DO_HOST': 'host.example.com', 'DO_USER': 'porotaadmin',
               'DO_SSH_KEY': 'private key', 'DO_KNOWN_HOSTS': 'host ssh-ed25519 test'}
        self.assertEqual(host.connection_values(env)[:2], ('host.example.com', 'porotaadmin'))
        for key, value in [('DO_HOST', '-oProxyCommand=bad'), ('DO_USER', 'x;id'), ('DO_SSH_KEY', '')]:
            with self.assertRaisesRegex(ValueError, '^SSH_CONFIGURATION_INVALID$'):
                host.connection_values({**env, key: value})

    def test_receipt_has_size_bound_and_rejects_duplicate_keys(self):
        self.assertEqual(host.decode_receipt(json.dumps(self.receipt)), self.receipt)
        for raw in ('{"state":"VALIDADO_RUNTIME","state":"NO_VERIFICADO"}', 'x'*131073, '[]'):
            with self.assertRaises(ValueError):
                host.decode_receipt(raw)

    def test_remote_stage_path_cannot_supply_shell_syntax(self):
        host.validate_stage('/home/porotaadmin/.local/state/porota-rc6/stage.A12bc')
        for path in ('/tmp/unrelated', '/home/user/../escape', '/home/user/.local/state/porota-rc6/stage.x;id'):
            with self.assertRaises(ValueError):
                host.validate_stage(path)

    def test_unknown_remote_outcome_preserves_live_stage(self):
        calls = []
        def remote(_connection, command, **_kwargs):
            calls.append(command)
            if host.STAGE_CREATE in command:
                return subprocess.CompletedProcess(command, 0,
                    b'/home/porotaadmin/.local/state/porota-rc6/stage.A12bc\n', b'')
            raise ValueError('SSH_TRANSPORT_FAILED')
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo/'scripts').mkdir()
            for name in ('porota_rootless_setup.py', 'porota_rootless_host.py'):
                (repo/'scripts'/name).write_text('# fixture\n')
            with patch.object(host, 'connection', return_value=nullcontext((['ssh'], ['scp'], 'user@host'))), \
                 patch.object(host, 'ssh_run', side_effect=remote), \
                 patch.object(host.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')):
                with self.assertRaises(host.HostFailure) as caught:
                    host.execute('prepare', repo)
        self.assertEqual(caught.exception.receipt['transfer_cleanup'], 'RETAINED_COMMAND_OUTCOME_UNKNOWN')
        self.assertFalse(any(host.STAGE_REMOVE in command for command in calls))

    def test_ssh_disconnect_exit_preserves_live_stage(self):
        calls = []
        def remote(_connection, command, **_kwargs):
            calls.append(command)
            if host.STAGE_CREATE in command:
                return subprocess.CompletedProcess(command, 0,
                    b'/home/porotaadmin/.local/state/porota-rc6/stage.A12bc\n', b'')
            return subprocess.CompletedProcess(command, 255, b'', b'disconnected')
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo/'scripts').mkdir()
            for name in ('porota_rootless_setup.py', 'porota_rootless_host.py'):
                (repo/'scripts'/name).write_text('# fixture\n')
            with patch.object(host, 'connection', return_value=nullcontext((['ssh'], ['scp'], 'user@host'))), \
                 patch.object(host, 'ssh_run', side_effect=remote), \
                 patch.object(host.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')):
                with self.assertRaises(host.HostFailure):
                    host.execute('prepare', repo)
        self.assertFalse(any(host.STAGE_REMOVE in command for command in calls))

    def test_preparation_failure_preserves_its_physical_reason(self):
        observed = {'state': 'BLOQUEADO', 'failure_signature': 'ROOTLESS_PACKAGES_REQUIRED'}
        def remote(_connection, command, **_kwargs):
            if host.STAGE_CREATE in command:
                return subprocess.CompletedProcess(command, 0,
                    b'/home/porotaadmin/.local/state/porota-rc6/stage.A12bc\n', b'')
            if host.STAGE_REMOVE in command:
                return subprocess.CompletedProcess(command, 0, b'', b'')
            return subprocess.CompletedProcess(command, 79, json.dumps(observed).encode(), b'')
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo/'scripts').mkdir()
            for name in ('porota_rootless_setup.py', 'porota_rootless_host.py'):
                (repo/'scripts'/name).write_text('# fixture\n')
            with patch.object(host, 'connection', return_value=nullcontext((['ssh'], ['scp'], 'user@host'))), \
                 patch.object(host, 'ssh_run', side_effect=remote), \
                 patch.object(host.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')):
                with self.assertRaises(host.HostFailure) as caught:
                    host.execute('prepare', repo)
        self.assertEqual(caught.exception.receipt['failure_signature'], 'ROOTLESS_PACKAGES_REQUIRED')
        self.assertEqual(caught.exception.receipt['transfer_cleanup'], 'COMPLETE')

    def test_cleanup_disconnect_cannot_discard_the_physical_receipt(self):
        for exitcode in (0, 79):
            with self.subTest(exitcode=exitcode), tempfile.TemporaryDirectory() as temporary:
                observed = {'state': 'DESARROLLADO' if exitcode == 0 else 'BLOQUEADO',
                            'failure_signature': None if exitcode == 0 else 'ROOTLESS_PACKAGES_REQUIRED',
                            'schema': 'porota.rootless.prepare.v1'}
                repo = Path(temporary)
                (repo/'scripts').mkdir()
                for name in ('porota_rootless_setup.py', 'porota_rootless_host.py'):
                    (repo/'scripts'/name).write_text('# fixture\n')

                def remote(_connection, command, **_kwargs):
                    if host.STAGE_CREATE in command:
                        return subprocess.CompletedProcess(command, 0,
                            b'/home/porotaadmin/.local/state/porota-rc6/stage.A12bc\n', b'')
                    if host.STAGE_REMOVE in command:
                        raise ValueError('SSH_TRANSPORT_FAILED')
                    return subprocess.CompletedProcess(command, exitcode, json.dumps(observed).encode(), b'')

                with patch.object(host, 'connection', return_value=nullcontext((['ssh'], ['scp'], 'user@host'))), \
                     patch.object(host, 'ssh_run', side_effect=remote), \
                     patch.object(host.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')):
                    with self.assertRaises(host.HostFailure) as caught:
                        host.execute('prepare', repo)
                self.assertEqual(caught.exception.receipt['schema'], observed['schema'])
                self.assertEqual(caught.exception.receipt['failure_signature'], observed['failure_signature'])
                self.assertEqual(caught.exception.receipt['transfer_cleanup'], 'NO_VERIFICADO')
                self.assertEqual(str(caught.exception),
                    'ROOTLESS_PREPARATION_FAILED' if exitcode else 'TRANSFER_CLEANUP_INCOMPLETE')


if __name__ == '__main__':
    unittest.main()
