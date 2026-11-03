"""Deploy selects its immutable image before removing the old PAPER runtime."""
import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import porota_mode_manager as manager


SHA = 'a' * 40
TAG = f'porota-trading-bot:17.0.0-rc6-candidate-{SHA}'
IMAGE_ID = 'sha256:' + 'b' * 64


class CandidateImageSelectionTests(unittest.TestCase):
    def test_default_simulation_stays_on_stable_image(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(manager, 'run') as runner:
            self.assertEqual(manager.simulation_image(), manager.IMAGE)
            runner.assert_not_called()

    def test_partial_or_foreign_override_rejected_before_engine_stop(self):
        for values in ({'POROTA_DEPLOY_CANDIDATE_IMAGE': TAG},
                       {'POROTA_DEPLOY_CANDIDATE_IMAGE_ID': IMAGE_ID},
                       {'POROTA_DEPLOY_CANDIDATE_IMAGE': 'ppi-watch:current',
                        'POROTA_DEPLOY_CANDIDATE_IMAGE_ID': IMAGE_ID}):
            with self.subTest(values=values), patch.dict(os.environ, values, clear=True), \
                 patch.object(manager, 'stop_engines') as stop, \
                 patch.object(manager, 'run') as runner:
                with self.assertRaisesRegex(RuntimeError, 'CANDIDATE_IMAGE_INVALID'):
                    manager.simulation()
                stop.assert_not_called()
                runner.assert_not_called()

    def test_mismatched_candidate_id_rejected_before_engine_stop(self):
        with patch.dict(os.environ, {'POROTA_DEPLOY_CANDIDATE_IMAGE': TAG,
                                    'POROTA_DEPLOY_CANDIDATE_IMAGE_ID': IMAGE_ID}, clear=True), \
             patch.object(manager, 'stop_engines') as stop, \
             patch.object(manager, 'run', return_value=SimpleNamespace(
                 stdout='sha256:' + 'c' * 64)) as runner:
            with self.assertRaisesRegex(RuntimeError, 'CANDIDATE_IMAGE_ID_MISMATCH'):
                manager.simulation()
            stop.assert_not_called()
            runner.assert_called_once_with('docker', 'image', 'inspect', '-f',
                                           '{{.Id}}', TAG, capture=True)

    def test_simulation_uses_verified_candidate_for_both_split_containers(self):
        with tempfile.TemporaryDirectory(prefix='rc6-mode-image-') as folder:
            root = Path(folder)
            (root / '.secrets').mkdir()
            (root / '.secrets/ppi_production.json').write_text('{}')
            for filename in ('cf_intraday_scalping.py', 'co_market_sessions_hf6.py'):
                (root / filename).write_text(filename)
            calls = []

            def fake_run(*args, **kwargs):
                calls.append(args)
                if args[:3] == ('docker', 'image', 'inspect'):
                    return SimpleNamespace(stdout=IMAGE_ID + '\n')
                if args[:3] == ('docker', 'inspect', '-f'):
                    return SimpleNamespace(stdout='running\n')
                if args[:3] == ('docker', 'exec', 'porota_production_observer'):
                    source = root / Path(args[-1]).name
                    return SimpleNamespace(stdout=hashlib.sha256(source.read_bytes()).hexdigest() + '  file\n')
                return SimpleNamespace(stdout='synthetic-container-id\n')

            with patch.dict(os.environ, {'POROTA_DEPLOY_CANDIDATE_IMAGE': TAG,
                                        'POROTA_DEPLOY_CANDIDATE_IMAGE_ID': IMAGE_ID}, clear=True), \
                 patch.object(manager, 'ROOT', root), \
                 patch.object(manager, 'stop_engines') as stop, \
                 patch.object(manager, 'write_mode'), \
                 patch.object(manager, 'start_dashboard') as dashboard, \
                 patch.object(manager, 'observer_runtime_env', return_value=root / 'env'), \
                 patch.object(manager, 'operator_readonly_mount', return_value=(
                     '--mount', 'type=bind,source=/root-owned-fixture,target=/app/operator,readonly')) as authority_mount, \
                 patch.object(manager, 'notify', return_value='NO_CONFIGURADO'), \
                 patch.object(manager, 'run', side_effect=fake_run):
                manager.simulation()
            stop.assert_called_once_with()
            dashboard.assert_called_once_with('PRODUCTION_PAPER', image=TAG)
            self.assertEqual(calls[0][:3], ('docker', 'image', 'inspect'))
            observer = next(args for args in calls if args[:3] == ('docker', 'run', '-d'))
            self.assertEqual(observer[-3:], ('python', TAG, 'bv_paper_runtime.py'))
            authority_mount.assert_called_once_with()
            self.assertIn('type=bind,source=/root-owned-fixture,target=/app/operator,readonly',
                          observer)

    def test_dashboard_run_uses_selected_image_without_changing_other_modes(self):
        calls = []
        def fake_run(*args, **kwargs):
            calls.append(args)
            return SimpleNamespace(stdout='')
        with patch.object(manager, 'run', side_effect=fake_run), \
             patch.object(manager, 'dashboard_env', return_value=Path('/tmp/fixture.env')):
            manager.start_dashboard('PRODUCTION_PAPER', image=TAG)
            first = calls[-1]
            self.assertEqual(first[-3:], ('python', TAG, 'o_dashboard.py'))
            manager.start_dashboard('DETENIDO')
            self.assertEqual(calls[-1][-3:], ('python', manager.IMAGE, 'o_dashboard.py'))


if __name__ == '__main__':
    unittest.main()
