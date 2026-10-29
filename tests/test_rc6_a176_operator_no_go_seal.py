"""A176 administrative seal: single atomic write, never admission/rearm."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import rc6_a176_operator_no_go_seal as seal
from scripts import rc6_deploy_scheduler_suspend as suspend


NOW = datetime(2026, 10, 29, 12, 15, tzinfo=timezone.utc)


class A176OperatorSealTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'data/deploy'
        self.directory.mkdir(parents=True)
        self.lock = self.root / 'mutex.lock'
        self.lock.write_bytes(b'lock')
        self.lock.chmod(0o600)
        self.patches = [
            patch.object(seal, 'ROOT', self.root),
            patch.object(seal, 'DIR', self.directory),
            patch.object(seal, 'RECEIPT', self.directory / seal.RECEIPT.name),
            patch.object(seal, 'LOCK', self.lock),
            patch.object(seal, 'BARRIER', self.root/'attempt.json'),
            # GitHub-hosted runners are non-root. Mock *only* the filesystem
            # identity probe while retaining the production uid==0 invariant.
            patch.object(seal.os, 'geteuid', return_value=0),
            patch.object(seal, 'root_owned', return_value=True),
            patch.object(seal, 'trusted_deploy_directory', return_value=True),
            patch.object(seal, 'inspect_host'),
            patch.object(seal, 'observed_proof', return_value={
                'ppi_snapshot':[],
                'preopen_timer_state':{'active':'inactive','enabled':'disabled'},
                'guardian_timer_state':{'active':'active','enabled':'enabled'},
                'owned_timer_count':16}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_exactly_one_create_and_idempotent_read_only_retry(self):
        first = seal.seal('40799999999', '1', NOW)
        self.assertEqual(first['status'], 'SEALED_NO_GO')
        self.assertEqual(first['host_mutations'], 1)
        self.assertEqual(first['operation_id'], 'A176-40799999999-1')
        record = seal.read_exact(seal.RECEIPT, first['receipt_sha256'])
        self.assertEqual(record['status'], 'NO_GO_PENDING_EXPLICIT_REARM')
        self.assertIs(record['paper_execution_authorized'], False)
        self.assertEqual(record['real_orders_sent'], 0)
        self.assertEqual(record['ppi_snapshot'], [])
        self.assertEqual(stat.S_IMODE(seal.RECEIPT.stat().st_mode), 0o600)
        second = seal.seal('40800000000', '2', NOW)
        self.assertEqual(second['host_mutations'], 0)
        self.assertFalse(second['written_once'])
        self.assertEqual(second['receipt_sha256'], first['receipt_sha256'])
        self.assertEqual(second['operation_id'], first['operation_id'])

    def test_existing_changed_hold_fails_without_rewriting(self):
        result = seal.seal('40799999999', '1', NOW)
        row = json.loads(seal.RECEIPT.read_text())
        row['preopen_timer_state']['active'] = 'active'
        seal.RECEIPT.write_text(json.dumps(row))
        changed = seal.RECEIPT.read_bytes()
        with self.assertRaisesRegex(seal.Rejected, 'EXISTING_SEAL_DRIFT'):
            seal.seal('40800000000', '2', NOW)
        self.assertEqual(seal.RECEIPT.read_bytes(), changed)
        self.assertNotEqual(hashlib.sha256(changed).hexdigest(), result['receipt_sha256'])

    def test_wrong_day_missing_mutex_and_barrier_all_fail_closed(self):
        with self.assertRaisesRegex(seal.Rejected, 'A176_DAY_EXPIRED'):
            seal.seal('40799999999', '1', NOW.replace(day=30))
        with self.assertRaisesRegex(seal.Rejected, 'ACTION_ID_INVALID'):
            seal.seal('0', '1', NOW)
        seal.BARRIER.write_text('{}')
        with self.assertRaisesRegex(seal.Rejected, 'DEPLOY_BARRIER_PRESENT'):
            seal.seal('40799999999', '1', NOW)
        seal.BARRIER.unlink()
        self.lock.chmod(0o666)
        with self.assertRaisesRegex(seal.Rejected, 'HOST_MUTEX_UNSAFE'):
            seal.seal('40799999999', '1', NOW)
        self.assertFalse(seal.RECEIPT.exists())

    def test_only_pinned_service_directory_permissions_are_allowed(self):
        def check(uid, gid, mode, directory=True):
            fake = SimpleNamespace(st_mode=(stat.S_IFDIR if directory else stat.S_IFREG)|mode,
                                   st_uid=uid,st_gid=gid)
            return seal.service_dir_identity(fake)
        self.assertTrue(check(1000,1000,0o750))
        for args in ((0,1000,0o750),(1000,0,0o750),
                     (1000,1000,0o770),(1000,1000,0o751),
                     (1000,1000,0o750,False)):
            self.assertFalse(check(*args))
        with patch.object(seal, 'trusted_deploy_directory', return_value=False):
            with self.assertRaisesRegex(seal.Rejected, 'RECEIPT_DIRECTORY_UNSAFE'):
                seal.seal('40799999999', '1', NOW)

    def test_root_ownership_duplicate_key_and_symlink_fail_closed(self):
        path = self.directory / 'dup.json'
        raw = b'{"real_orders_sent":0,"real_orders_sent":1}'
        path.write_bytes(raw)
        with self.assertRaisesRegex(seal.Rejected, 'JSON_DUPLICATE_KEY'):
            seal.read_exact(path, hashlib.sha256(raw).hexdigest())
        shadow = self.directory / 'shadow.json'
        shadow.symlink_to(path)
        with self.assertRaises(OSError):
            seal.read_exact(shadow, hashlib.sha256(raw).hexdigest())
        path.chmod(0o666)
        with self.assertRaisesRegex(seal.Rejected, 'RECEIPT_FILE_UNSAFE'):
            seal.read_exact(path, hashlib.sha256(raw).hexdigest())
        path.chmod(0o600)
        with patch.object(seal, 'root_owned', return_value=False):
            with self.assertRaisesRegex(seal.Rejected, 'RECEIPT_FILE_UNSAFE'):
                seal.read_exact(path, hashlib.sha256(raw).hexdigest())

    def test_sqlite_reads_paper_zero_and_all_three_books_flat(self):
        base = self.root / 'data/paper_v17'
        base.mkdir()
        db = base/'observer_v17.db'
        with sqlite3.connect(db) as conn:
            conn.execute('CREATE TABLE observer_state(id INTEGER,mode TEXT,real_orders_sent INTEGER)')
            conn.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
            for table in ('paper_positions','paper_cauciones','paper_future_positions'):
                conn.execute(f'CREATE TABLE {table}(status TEXT)')
        seal.paper_zero_and_flat()
        with sqlite3.connect(db) as conn:
            conn.execute("INSERT INTO paper_future_positions VALUES('ACTIVE')")
        with self.assertRaisesRegex(seal.Rejected, 'PAPER_BOOK_NOT_FLAT'):
            seal.paper_zero_and_flat()
        with sqlite3.connect(db) as conn:
            conn.execute('DELETE FROM paper_future_positions')
            conn.execute('UPDATE observer_state SET real_orders_sent=1 WHERE id=1')
        with self.assertRaisesRegex(seal.Rejected, 'PAPER_ZERO_REQUIRED'):
            seal.paper_zero_and_flat()

    def test_historical_source_and_timer_policy_bound_by_exact_digests(self):
        rows = {
            'a151': ('operator-preopen-pause-20261028.json',
                     {'schema':'rc6.operator-preopen-pause.v2',
                      'status':'PAUSED_NO_GO','operation_id':'A151-40686116017-1',
                      'trade_day_ar':'2026-10-28',
                      'source_sha':'2325eba329878c62a61cb47bb66d33ccf279bd4a'}),
            'q': (f'quiescence-{seal.A175}.json',
                  {'candidate_sha':seal.A175,
                   'status':'FIX_FORWARD_VALIDATION_REQUIRED',
                   'installation_started':True,'ppi_watch_accessed':False,
                   'real_orders_sent':0}),
            'marker': ('CURRENT_STATE_V2.json',
                       {'candidate_sha':seal.A175,'deploy_sha':seal.DEPLOY,
                        'image_id':seal.IMAGE,
                        'validation_status':'DEPLOYED_VALIDATION_PENDING',
                        'mode':'PRODUCTION_PAPER','real_orders_sent':0,
                        'real_order_routes':'NOT_CALLED','ppi_watch_untouched':True}),
            'frozen': ('porota-frozen-candidate.json',
                       {'candidate_sha':seal.A175,'candidate_tree_sha':
                        '66c67bff66dd9f036cd7aa8de5ba312e04e53be6'}),
        }
        timers = ([seal.PREOPEN,seal.GUARDIAN] +
                  [f'porota-held-{i}-rc6.timer' for i in range(14)])
        policy=self.root/'ops/policy/host-control-plane-reconciliation-v2.json'
        policy.parent.mkdir(parents=True)
        policy.write_text('{"policy":"held"}')
        rows['scheduler'] = (f'scheduler-suspend-{seal.A175}.json',
             {'candidate_sha':seal.A175,'status':'SUSPENDED',
              'policy_sha256':hashlib.sha256(policy.read_bytes()).hexdigest(),
              'timers':timers,'attempted':timers,
              'preserved_iol_timer':seal.GUARDIAN,
              'operator_pause_timer':seal.PREOPEN})
        hashes={}
        for key,(filename,body) in rows.items():
            raw=json.dumps(body,sort_keys=True).encode()
            (self.directory/filename).write_bytes(raw)
            hashes[key]=hashlib.sha256(raw).hexdigest()
        with patch.dict(seal.SHA, hashes):
            self.assertEqual(seal.validate_origin(), timers)
            path=self.directory/rows['q'][0]
            path.write_text(path.read_text()+' ')
            with self.assertRaisesRegex(seal.Rejected, 'RECEIPT_DIGEST_CHANGED'):
                seal.validate_origin()
            path.write_text(json.dumps(rows['q'][1],sort_keys=True))
            policy.write_text('{"policy":"changed"}')
            with self.assertRaisesRegex(seal.Rejected, 'SCHEDULER_HOLD_POLICY_CHANGED'):
                seal.validate_origin()


class DailyInstalledA176ProofTest(unittest.TestCase):
    def test_no_environment_never_falls_back_before_validated_runtime(self):
        marker = {'validation_status':'DEPLOYED_VALIDATION_PENDING',
                  'mode':'PRODUCTION_PAPER', 'real_orders_sent':0,
                  'real_order_routes':'NOT_CALLED',
                  'candidate_sha':seal.A175}
        with patch.dict(os.environ, {'RC6_A145_QUIESCENCE_GZIP_B64':''}), \
             patch.object(suspend, '_operator_pause_json', return_value=(marker,'0'*64)):
            with self.assertRaisesRegex(ValueError, 'STAGED_REQUIRED'):
                suspend.a176_quiescence_helper()

    def test_after_validation_only_exact_local_installed_helper_is_accepted(self):
        from scripts import rc6_deploy_quiescence as q
        marker = {'validation_status':'VALIDATED_RUNTIME',
                  'mode':'PRODUCTION_PAPER','real_orders_sent':0,
                  'real_order_routes':'NOT_CALLED','candidate_sha':'f'*40}
        local_root=Path.cwd()/'data/deploy'
        with patch.dict(os.environ, {'RC6_A145_QUIESCENCE_GZIP_B64':''}), \
             patch.object(suspend, 'ROOT', local_root), \
             patch.object(suspend, '_operator_pause_json', return_value=(marker,'0'*64)), \
             patch.object(q, 'A176_OPERATOR_NO_GO_RECEIPT_SHA256', 'a'*64):
            self.assertIs(suspend.a176_quiescence_helper(), q)
            marker['real_orders_sent'] = 1
            with self.assertRaisesRegex(ValueError, 'STAGED_REQUIRED'):
                suspend.a176_quiescence_helper()
            marker['real_orders_sent'] = 0
            with patch.object(q, 'A176_OPERATOR_NO_GO_RECEIPT_SHA256','0'*64):
                with self.assertRaisesRegex(ValueError, 'IDENTITY_RED'):
                    suspend.a176_quiescence_helper()

    def test_deploy_staged_helper_remains_mandatory_when_env_present(self):
        with patch.dict(os.environ, {'RC6_A145_QUIESCENCE_GZIP_B64':'STAGED'}), \
             patch.object(suspend, 'a143_staged_module', return_value='exact-stage'):
            self.assertEqual(suspend.a176_quiescence_helper(), 'exact-stage')


if __name__ == '__main__':
    unittest.main()
