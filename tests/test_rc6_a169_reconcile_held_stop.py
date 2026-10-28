"""Exact attempt-only recovery guards; no host resources are accessed."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'scripts/rc6_a169_reconcile_held_stop.py'
SPEC = importlib.util.spec_from_file_location('rc6_a169', SOURCE)
a169 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(a169)


class ReconcileHeldStopGuards(unittest.TestCase):
    def test_barrier_rejects_other_attempt_and_inflight(self):
        with tempfile.TemporaryDirectory() as folder:
            barrier = Path(folder) / 'attempt.json'
            row = {'candidate': a169.CANDIDATE, 'run': a169.RUN,
                   'attempt': a169.ATTEMPT, 'phase': 'cleanup_payload', 'status': 'IDLE'}
            with patch.object(a169, 'BARRIER', barrier):
                barrier.write_text(json.dumps(row))
                barrier.chmod(0o600)
                self.assertEqual(a169.barrier_status('probe'), row)
                for key, value in [('attempt', '3'), ('status', 'INFLIGHT'),
                                   ('phase', 'promotion')]:
                    changed = dict(row, **{key: value})
                    barrier.write_text(json.dumps(changed))
                    with self.assertRaisesRegex(ValueError, 'NOT_EXACT_ATTEMPT'):
                        a169.barrier_status('probe')
                barrier.unlink()
                self.assertIsNone(a169.barrier_status('post'))

    def test_failed_install_receipt_requires_exact_preinstallation_state(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(a169, 'ROOT', Path(folder)):
                target = Path(folder) / 'data/deploy'
                target.mkdir(parents=True)
                path = target / ('quiescence-' + a169.CANDIDATE + '.json')
                row = {'candidate_sha': a169.CANDIDATE, 'status': 'RECOVERY_UNVERIFIED',
                       'installation_started': False, 'a143_pending_adopted': True,
                       'real_orders_sent': 0, 'ppi_watch_accessed': False,
                       'containers': {'a': {}, 'b': {}}, 'stop_evidence': {'a': {}, 'b': {}}}
                q = type('Q', (), {'OWNED': ('a', 'b')})
                path.write_text(json.dumps(row))
                self.assertEqual(a169.exact_receipt(q, 'probe')[1], row)
                for key, value in [('installation_started', True), ('status', 'DRAINED'),
                                   ('candidate_image_intent', {}), ('real_orders_sent', 1)]:
                    path.write_text(json.dumps(dict(row, **{key: value})))
                    with self.assertRaisesRegex(ValueError, 'NOT_PREINSTALL_FAILURE'):
                        a169.exact_receipt(q, 'probe')

    def test_workflow_serializes_single_exact_phase_after_probe(self):
        source = (SOURCE.parents[1] / '.github/workflows/rc6-a169-reconcile-exact-held-stop.yml').read_text()
        self.assertIn('group: rc6-unified-paper-deploy', source)
        self.assertIn('--phase cleanup_final', source)
        self.assertLess(source.index('id: probe'), source.index('--phase cleanup_final'))
        self.assertIn("gzip.compress(Path(path).read_bytes(),mtime=0)", source)
        self.assertNotIn('systemctl start', source)


if __name__ == '__main__':
    unittest.main()
