import copy
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

    def test_cold_host_operation_requires_exact_deployed_source_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'scripts').mkdir()
            for name in ('porota_rootless_host.py', 'rc6_cold2_successor.py',
                         'rc6_cold2_quarantine.py'):
                (root/'scripts'/name).write_text('# original frozen fixture\n')
            with patch.object(host.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0,
                    b'wrong-branch\n', b'')):
                with self.assertRaisesRegex(ValueError, '^FROZEN_SOURCE_SHA_MISMATCH$'):
                    host.verify_frozen_cold_source(root)

    def test_cold_action_uses_private_stage_without_releasing_or_building_image(self):
        commands = []
        result = {'schema': 'rc6.rootless.cold-recovery.v1', 'operation': 'cold_inspect',
                  'state': 'READ_ONLY_OBSERVED', 'controller_roles': 8, 'real_orders_sent': 0,
                  'ppi_watch_touched': False, 'boca_touched': False,
                  'mutations': 0, 'pack_content_hash_verified': True,
                  'source_sha': '21b6d6b3882a35f1f5ad0469df739c5b0d6d2f76'}
        def remote(_connection, command, **kwargs):
            commands.append(command)
            if host.STAGE_CREATE in command:
                self.assertIn('staging-only', command)
                return subprocess.CompletedProcess([], 0,
                    b'/home/porotaadmin/.local/state/porota-rc6/stage.A12bc\n', b'')
            if host.STAGE_REMOVE in command:
                return subprocess.CompletedProcess([], 0, b'', b'')
            return subprocess.CompletedProcess([], 0, json.dumps(result).encode(), b'')
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)/'repo'
            frozen = Path(temporary)/'frozen'
            (repo/'scripts').mkdir(parents=True)
            (frozen/'scripts').mkdir(parents=True)
            (repo/'scripts'/'rc6_rootless_cold_recovery.py').write_text('# adapter\n')
            for name in ('porota_rootless_host.py','rc6_cold2_successor.py','rc6_cold2_quarantine.py'):
                (frozen/'scripts'/name).write_text('# pinned code\n')
            with patch.object(host,'verify_frozen_cold_source'), \
                 patch.object(host,'connection',return_value=nullcontext((['ssh'],['scp'],'user@host'))), \
                 patch.object(host,'ssh_run',side_effect=remote), \
                 patch.object(host.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'',b'')):
                received=host.execute('cold_inspect',repo,frozen_source=frozen)
        self.assertEqual(received['state'],'READ_ONLY_OBSERVED')
        self.assertTrue(any('rc6_rootless_cold_recovery.py' in ' '.join(c) for c in commands))

    def test_cold_mutation_never_accepts_readonly_or_unverified_receipt(self):
        base = {'schema': 'rc6.rootless.cold-recovery.v1', 'operation':'cold_recover',
                'state':'COLD_ARCHIVE_COMPLETE','controller_roles':8,
                'real_orders_sent':0,'ppi_watch_touched':False,'boca_touched':False,
                'mutations':1,'source_sha':'21b6d6b3882a35f1f5ad0469df739c5b0d6d2f76',
                'archive_complete':True,'cold_queue_failed':False,'runtime_validated':True}
        host.validate_cold_receipt(base,'cold_recover',0)
        for invalid in ({'state':'READ_ONLY_OBSERVED'}, {'controller_roles':6},
                        {'real_orders_sent':None}, {'cold_queue_failed':True},
                        {'runtime_validated':False}, {'ppi_watch_touched':True},
                        {'source_sha':'a'*40}, {'mutations':0}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                host.validate_cold_receipt({**base,**invalid},'cold_recover',0)

    def test_disconnected_recovery_has_exact_cid_resume_operation(self):
        receipt={'schema':'rc6.rootless.cold-recovery.v1','operation':'cold_resume',
                 'state':'RUNTIME_RESTORED','controller_roles':8,'real_orders_sent':0,
                 'ppi_watch_touched':False,'boca_touched':False,'runtime_validated':True,
                 'restoration':'RESTORED_ORIGINAL_RUNTIME','source_sha':host.FROZEN_COLD_SHA}
        host.validate_cold_receipt(receipt,'cold_resume',0)
        for field in ('restoration','controller_roles','runtime_validated'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                host.validate_cold_receipt({**receipt,field:None},'cold_resume',0)


if __name__ == '__main__':
    unittest.main()
