import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts import porota_operator_intent as intent
from scripts import porota_rootless_host as host


class IntentMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir(mode=0o700)
        self.legacy = self.root / 'legacy'
        self.legacy.mkdir(mode=0o755)
        self.now = datetime(2026, 11, 15, tzinfo=ZoneInfo('America/Argentina/Buenos_Aires'))
        self.paused = {'schema': 'RC6_OPERATOR_PAPER_INTENT_V2', 'status': 'PAPER_PAUSED_BY_OPERATOR',
                       'candidate_sha': 'a'*40, 'candidate_tree_sha': 'b'*40,
                       'operator_comment_id': 123, 'operator_github_login': 'mbalbo2023',
                       'rearm_run_id': 456, 'issued_at_ar': '2026-11-14T10:00:00-03:00'}

    def test_migrates_exact_bytes_and_preserves_pause(self):
        raw = json.dumps(self.paused, indent=2).encode()
        receipt = self.legacy / 'paper-rearm-v1.json'
        receipt.write_bytes(raw)
        with patch.object(intent.legacy, '_root_owned_receipt', return_value=(self.paused, raw)) as reader:
            out = intent.migrate_intent(self.home, source=self.legacy, now=self.now)
        reader.assert_called_once_with(self.legacy, with_raw=True)
        target = intent.directory(self.home) / 'paper-rearm-v1.json'
        self.assertEqual(target.read_bytes(), raw)
        self.assertEqual(out['source_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(out['status'], 'PAPER_PAUSED_BY_OPERATOR')
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(receipt.read_bytes(), raw)

    def test_missing_intent_does_not_create_authorization(self):
        out = intent.migrate_intent(self.home, source=self.legacy, now=self.now)
        self.assertEqual(out['status'], 'ABSENT')
        self.assertFalse((intent.directory(self.home) / 'paper-rearm-v1.json').exists())

    def test_interrupted_copy_resumes_only_with_identical_legacy_provenance(self):
        raw = json.dumps(self.paused).encode()
        (self.legacy/'paper-rearm-v1.json').write_bytes(raw)
        write = intent.atomic_write
        def interrupted(path, value):
            if path.name == 'migration.json':
                raise OSError('simulated interruption')
            write(path, value)
        with patch.object(intent.legacy, '_root_owned_receipt', return_value=(self.paused, raw)):
            with patch.object(intent, 'atomic_write', side_effect=interrupted), self.assertRaises(OSError):
                intent.migrate_intent(self.home, source=self.legacy, now=self.now)
            out = intent.migrate_intent(self.home, source=self.legacy, now=self.now)
        self.assertEqual(out['source_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual((intent.directory(self.home)/'paper-rearm-v1.json').read_bytes(), raw)

    def test_partial_copy_never_overwrites_a_different_private_intent(self):
        raw = json.dumps(self.paused).encode()
        (self.legacy/'paper-rearm-v1.json').write_bytes(raw)
        target = intent.private_directory(intent.directory(self.home)) / 'paper-rearm-v1.json'
        changed = json.dumps({**self.paused, 'operator_comment_id': 999}).encode()
        intent.atomic_write(target, changed)
        with patch.object(intent.legacy, '_root_owned_receipt', return_value=(self.paused, raw)), \
             self.assertRaisesRegex(ValueError, 'EXISTING_INTENT_UNPROVEN'):
            intent.migrate_intent(self.home, source=self.legacy, now=self.now)
        self.assertEqual(target.read_bytes(), changed)

    def test_retry_never_overwrites_current_pause_with_legacy_change(self):
        path = self.legacy / 'paper-rearm-v1.json'
        path.write_text(json.dumps(self.paused))
        with patch.object(intent.legacy, '_root_owned_receipt', return_value=(self.paused, path.read_bytes())):
            intent.migrate_intent(self.home, source=self.legacy, now=self.now)
        path.write_text('{}')
        out = intent.migrate_intent(self.home, source=self.legacy, now=self.now)
        self.assertEqual(out['status'], 'ALREADY_MIGRATED')
        self.assertEqual(json.loads((intent.directory(self.home)/path.name).read_text()), self.paused)

    def test_unknown_existing_target_is_not_overwritten(self):
        target = intent.directory(self.home)
        target.mkdir(mode=0o700, parents=True)
        (target/'paper-rearm-v1.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'EXISTING_INTENT_UNPROVEN'):
            intent.migrate_intent(self.home, source=self.legacy, now=self.now)

    def test_symlink_target_is_rejected(self):
        target = intent.directory(self.home)
        target.parent.mkdir(mode=0o700, parents=True)
        target.symlink_to(self.legacy, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'INTENT_PATH_ALIAS'):
            intent.migrate_intent(self.home, source=self.legacy, now=self.now)

    def test_invalid_legacy_intent_is_not_copied(self):
        (self.legacy/'paper-rearm-v1.json').write_text('{}')
        with self.assertRaises(ValueError):
            intent.migrate_intent(self.home, source=self.legacy, now=self.now)
        self.assertFalse((intent.directory(self.home)/'paper-rearm-v1.json').exists())

    def test_private_receipt_rejects_group_write(self):
        target = intent.directory(self.home)
        target.mkdir(mode=0o700, parents=True)
        path = target/'paper-rearm-v1.json'
        path.write_text(json.dumps(self.paused))
        path.chmod(0o660)
        with self.assertRaisesRegex(ValueError, 'INTENT_FILE_UNSAFE'):
            intent.read_intent(self.home, now=self.now)

    def test_pause_is_available_without_a_healthy_release(self):
        intent.migrate_intent(self.home, source=self.legacy, now=self.now)
        result = intent.change_intent(self.home, action='pause', candidate_sha='a'*40,
                                      source_tree='b'*40, comment_id=123, run_id=456,
                                      root=self.root/'absent')
        self.assertEqual(result['status'], 'PAPER_PAUSED_BY_OPERATOR')

    def test_resume_requires_actual_runtime_validation(self):
        intent.migrate_intent(self.home, source=self.legacy, now=self.now)
        identity = host.Identity(os.getuid(), os.getgid(), 'fixture', self.home)
        with patch.dict(os.environ, {'POROTA_RUNTIME_BACKEND': 'ROOTLESS'}), \
             patch.object(host.Identity, 'current', return_value=identity), \
             self.assertRaisesRegex(ValueError, 'REQUIRED_FILE_UNREADABLE'):
            intent.change_intent(self.home, action='resume', candidate_sha='a'*40,
                                 source_tree='b'*40, comment_id=123, run_id=456,
                                 runtime_run=345, runtime_attempt=1, root=self.root/'absent')
        self.assertIsNone(intent.read_intent(self.home))

    def private_release(self):
        identity = host.Identity(os.getuid(), os.getgid(), 'fixture', self.home)
        release = {'schema': 'porota.release.v1', 'source_sha': 'a'*40,
                   'source_tree': 'b'*40, 'image_id': 'sha256:'+'c'*64,
                   'operating_mode': 'PRODUCTION_PAPER', 'execution_mode': 'SIMULATION',
                   'real_order_capability': 'BLOCKED', 'real_orders_sent_required': 0}
        raw = (json.dumps(release) + '\n').encode()
        path = identity.state / 'releases' / release['source_sha'] / 'release.json'
        path.parent.mkdir(parents=True, mode=0o700)
        path.write_bytes(raw)
        path.chmod(0o600)
        current = {**release, 'schema': 'porota.rootless.deploy.v1',
                   'state': 'VALIDADO_RUNTIME', 'real_orders_sent': 0,
                   'release_manifest_sha256': hashlib.sha256(raw).hexdigest()}
        current_path = identity.state / 'current.json'
        current_path.write_text(json.dumps(current))
        current_path.chmod(0o600)
        return identity, current_path, current

    def resume(self):
        return intent.change_intent(
            self.home, action='resume', candidate_sha='a'*40, source_tree='b'*40,
            comment_id=123, run_id=456, runtime_run=345, runtime_attempt=1, root=self.root)

    def test_resume_rejects_public_data_even_when_it_claims_a_valid_rootless_release(self):
        intent.migrate_intent(self.home, source=self.legacy, now=self.now)
        data = self.root / 'data/deploy'
        data.mkdir(parents=True)
        (data/'CURRENT_STATE_V2.json').write_text(json.dumps({
            'candidate_sha': 'a'*40, 'image_id': 'sha256:'+'c'*64,
            'validation_status': 'VALIDATED_RUNTIME', 'mode': 'PRODUCTION_PAPER',
            'real_orders_sent': 0, 'real_order_routes': 'NOT_CALLED',
            'ppi_watch_untouched': True, 'deployment_backend': 'ROOTLESS_SPLIT'}))
        (data/'porota-frozen-candidate.json').write_text(json.dumps({
            'candidate_sha': 'a'*40, 'candidate_tree_sha': 'b'*40,
            'real_order_capability_required': 'BLOCKED',
            'paper_mode_required': 'PRODUCTION_PAPER', 'build_once': True}))
        identity = host.Identity(os.getuid(), os.getgid(), 'fixture', self.home)
        for backend in ('', 'LEGACY', 'ROOTLESS'):
            with self.subTest(backend=backend), \
                 patch.dict(os.environ, {'POROTA_RUNTIME_BACKEND': backend}), \
                 patch.object(host.Identity, 'current', return_value=identity), \
                 self.assertRaises((ValueError, OSError)):
                self.resume()
            self.assertIsNone(intent.read_intent(self.home))

    def test_resume_uses_private_bound_release_without_public_data(self):
        intent.migrate_intent(self.home, source=self.legacy, now=self.now)
        identity, _, _ = self.private_release()
        with patch.dict(os.environ, {'POROTA_RUNTIME_BACKEND': 'ROOTLESS'}), \
             patch.object(host.Identity, 'current', return_value=identity):
            result = self.resume()
        self.assertEqual(result['status'], intent.legacy.REARM_STATUS)
        saved = intent.read_intent(self.home)
        self.assertEqual(saved['image_id'], 'sha256:'+'c'*64)
        self.assertEqual(saved['candidate_sha'], 'a'*40)
        self.assertEqual(saved['candidate_tree_sha'], 'b'*40)
        self.assertEqual(saved['runtime_validation_run_id'], 345)
        self.assertFalse((self.root/'data').exists())

    def test_resume_rejects_private_identity_counter_and_manifest_drift(self):
        intent.migrate_intent(self.home, source=self.legacy, now=self.now)
        identity, path, current = self.private_release()
        for change in ({'source_sha': 'd'*40}, {'source_tree': 'd'*40},
                       {'real_orders_sent': 1}, {'real_orders_sent': False},
                       {'release_manifest_sha256': 'd'*64}):
            path.write_text(json.dumps({**current, **change}))
            with self.subTest(change=change), \
                 patch.dict(os.environ, {'POROTA_RUNTIME_BACKEND': 'ROOTLESS'}), \
                 patch.object(host.Identity, 'current', return_value=identity), \
                 self.assertRaises((ValueError, OSError)):
                self.resume()
            self.assertIsNone(intent.read_intent(self.home))

    def test_old_owner_action_cannot_replace_pause(self):
        intent.migrate_intent(self.home, source=self.legacy, now=self.now)
        args = dict(action='pause', candidate_sha='a'*40, source_tree='b'*40,
                    comment_id=123, run_id=456)
        intent.change_intent(self.home, **args)
        with self.assertRaisesRegex(ValueError, 'OLD_OWNER_ACTION_REPLAY'):
            intent.change_intent(self.home, **{**args, 'run_id': 455})
        self.assertEqual(intent.read_intent(self.home)['rearm_run_id'], 456)

    def test_retry_exact_pause_preserves_its_original_receipt(self):
        intent.migrate_intent(self.home, source=self.legacy, now=self.now)
        args = dict(action='pause', candidate_sha='a'*40, source_tree='b'*40,
                    comment_id=123, run_id=456)
        intent.change_intent(self.home, **args)
        before = (intent.directory(self.home)/'paper-rearm-v1.json').read_bytes()
        self.assertTrue(intent.change_intent(self.home, **args)['idempotent'])
        self.assertEqual((intent.directory(self.home)/'paper-rearm-v1.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
