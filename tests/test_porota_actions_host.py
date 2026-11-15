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


if __name__ == '__main__':
    unittest.main()
