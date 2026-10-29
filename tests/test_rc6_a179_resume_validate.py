"""Fail-closed regression tests for A179 exact postfailure continuation."""
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from scripts import rc6_a179_resume_validate as resume


class A179ResumeGuardTests(TestCase):
    def test_exact_sha_and_json_bound(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'receipt.json'
            raw=b'{"status":"SUSPENDED"}'
            path.write_bytes(raw)
            self.assertEqual(resume.exact_json(path,hashlib.sha256(raw).hexdigest()),
                             {'status':'SUSPENDED'})
            with self.assertRaisesRegex(ValueError,'RECEIPT_DIGEST_CHANGED'):
                resume.exact_json(path,'0'*64)

    def test_symlink_refused(self):
        with TemporaryDirectory() as temp:
            source=Path(temp)/'source.json'
            source.write_text('{}')
            link=Path(temp)/'alias.json'
            link.symlink_to(source)
            with self.assertRaisesRegex(ValueError,'RECEIPT_UNSAFE'):
                resume.exact_json(link,hashlib.sha256(b'{}').hexdigest())

    def test_oversized_refused(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'receipt.json'
            path.write_bytes(b' '*65537)
            with self.assertRaisesRegex(ValueError,'RECEIPT_UNSAFE'):
                resume.exact_json(path,'0'*64)

    def test_installed_helper_source_matches_exact_product_candidate(self):
        source=Path(__file__).resolve().parents[1]/'scripts/rc6_deploy_quiescence.py'
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                         resume.QUIESCENCE_SOURCE_SHA256)

    def test_staged_env_blocks_unpinned_source(self):
        with TemporaryDirectory() as temp:
            scripts=Path(temp)/'scripts'
            scripts.mkdir()
            (scripts/'rc6_deploy_quiescence.py').write_text('wrong')
            with self.assertRaisesRegex(ValueError,'HELPER_SOURCE_CHANGED'):
                resume.staged_env(Path(temp))

    def test_staged_env_has_no_original_process_mutation(self):
        root=Path(__file__).resolve().parents[1]
        with mock.patch.dict(os.environ,{'RC6_A145_QUIESCENCE_GZIP_B64':''}):
            env=resume.staged_env(root)
            self.assertTrue(env['RC6_A145_QUIESCENCE_GZIP_B64'])
            self.assertFalse(os.environ['RC6_A145_QUIESCENCE_GZIP_B64'])
        with mock.patch.dict(os.environ,{'RC6_A145_QUIESCENCE_GZIP_B64':'bogus'}):
            with self.assertRaisesRegex(ValueError,'HELPER_WIRE_BOUNDS_RED'):
                resume.staged_env(root)

    def test_exact_book_fails_closed_before_other_queries(self):
        with mock.patch.object(resume,'sqlite3') as sql:
            connection=sql.connect.return_value.__enter__.return_value
            connection.execute.return_value.fetchone.return_value=('PRODUCTION_PAPER',1,'RUNNING','MARKET_OPEN')
            with self.assertRaisesRegex(ValueError,'A179_PAPER_BOOK_UNSAFE'):
                resume.read_book(Path('/tmp/nonexistent'))

    def test_native_mutation_never_runs_without_exact_mutex(self):
        with mock.patch.dict(os.environ,{'RC6_DEPLOY_MUTEX_FD':''}):
            with self.assertRaisesRegex(ValueError,'DEPLOY_HOST_MUTEX_REQUIRED'):
                resume.require_exact_mutex()
        with mock.patch.dict(os.environ,{'RC6_DEPLOY_MUTEX_FD':'5'}):
            with mock.patch.object(resume.os,'readlink',return_value='/tmp/fake-lock'):
                with self.assertRaisesRegex(ValueError,'DEPLOY_HOST_MUTEX_IDENTITY_RED'):
                    resume.require_exact_mutex()

    def test_native_proof_paths_are_run_scoped_and_never_reuse_legacy_green(self):
        with mock.patch.dict(os.environ,{'RC6_A179_RUN_ID':''}):
            with self.assertRaisesRegex(ValueError,'NATIVE_RUN_ID_REQUIRED'):
                resume.native_receipt_names()
        with mock.patch.dict(os.environ,{'RC6_A179_RUN_ID':'40916000000'}):
            first,soak,stability=resume.native_receipt_names()
        self.assertNotEqual(first,'native-generation-acceptance.json')
        self.assertNotEqual(soak,'native-ten-generation-soak.json')
        self.assertTrue(all('40916000000' in p and p.endswith('.json')
                            for p in [first,soak,*stability]))
        self.assertEqual(len(set([first,soak,*stability])),5)

    def test_native_pair_requires_strict_soak_and_exact_source(self):
        first={'status':'GREEN','acceptance_mode':'FRESH_GENERATIONS',
               'generations':[{'source_sha':resume.CANDIDATE,
                               'candidate_tree_sha':resume.TREE,
                               'real_orders_sent':0,'real_routes':'NOT_CALLED'}]*2}
        soak={'status':'GREEN','acceptance_mode':'FRESH_GENERATIONS',
              'strict':True,'all_services_sampled_running':True,
              'generations':first['generations']*5}
        self.assertEqual(resume.validate_native_pair(first,soak),'FRESH_GENERATIONS')
        with self.assertRaisesRegex(ValueError,'NATIVE_PAIR_STATUS_RED'):
            resume.validate_native_pair(first,dict(soak,strict=False))
        wrong=dict(first['generations'][-1],source_sha='0'*40)
        with self.assertRaisesRegex(ValueError,'NATIVE_SAFETY_OR_SOURCE_RED'):
            resume.validate_native_pair(first,dict(soak,generations=[wrong]*10))

    def test_closed_idle_never_masquerades_as_fresh(self):
        idle={'source_sha':resume.CANDIDATE,'candidate_tree_sha':resume.TREE,
              'real_orders_sent':0,'real_routes':'NOT_CALLED'}
        first={'status':'GREEN','acceptance_mode':'CLOSED_IDLE',
               'generations':[],'idle_observations':[idle]*2,
               'idle_heartbeat_count':2,'source_freshness_renewed':False}
        soak=dict(first,strict=True,all_services_sampled_running=True,
                  idle_observations=[idle]*10)
        self.assertEqual(resume.validate_native_pair(first,soak),'CLOSED_IDLE')
        with self.assertRaisesRegex(ValueError,'CLOSED_IDLE_CERTIFICATE_RED'):
            resume.validate_native_pair(first,dict(soak,source_freshness_renewed=True))

    def test_legacy_green_receipts_are_escrowed_without_data_loss(self):
        with TemporaryDirectory() as temp:
            directory=Path(temp)/'data/deploy'
            directory.mkdir(parents=True)
            proof={'run_id':'40916351644','mode':'FRESH_GENERATIONS'}
            prior={}
            replacements={}
            for canonical,fresh_key,digest_key in (
                ('native-generation-acceptance.json','first','first_sha256'),
                ('native-ten-generation-soak.json','soak','soak_sha256')):
                prior_bytes=b'{"status":"GREEN","source_sha":"OLDER"}\n'
                fresh_bytes=b'{"status":"GREEN","source_sha":"NEW"}\n'
                (directory/canonical).write_bytes(prior_bytes)
                fresh='a179-'+canonical
                (directory/fresh).write_bytes(fresh_bytes)
                prior[canonical]=hashlib.sha256(prior_bytes).hexdigest()
                replacements[canonical]=hashlib.sha256(fresh_bytes).hexdigest()
                proof[fresh_key]=fresh
                proof[digest_key]=replacements[canonical]
            with mock.patch.object(resume,'LEGACY_NATIVE_DIGESTS',prior):
                digest=resume.escrow_and_publish_current_native(Path(temp),proof)
                self.assertEqual(digest,
                    resume.escrow_and_publish_current_native(Path(temp),proof))
            self.assertEqual(len(digest),64)
            for canonical,previous_sha in prior.items():
                self.assertEqual(hashlib.sha256((directory/canonical).read_bytes()).hexdigest(),
                                 replacements[canonical])
                archive=(directory/(canonical.removesuffix('.json')+
                                    '.pre-a179-'+previous_sha[:16]+'.json'))
                self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),
                                 previous_sha)

    def test_legacy_receipt_drift_fails_without_touching_old_bytes(self):
        with TemporaryDirectory() as temp:
            directory=Path(temp)/'data/deploy'
            directory.mkdir(parents=True)
            original=directory/'native-generation-acceptance.json'
            original.write_bytes(b'{"status":"OTHER"}\n')
            fresh=directory/'some-new-source.json'
            fresh.write_bytes(b'{"status":"GREEN"}\n')
            proof={'first':fresh.name,
                   'first_sha256':hashlib.sha256(fresh.read_bytes()).hexdigest(),
                   'soak':'not-used','soak_sha256':'0'*64,
                   'run_id':'40916351644','mode':'FRESH_GENERATIONS'}
            with self.assertRaisesRegex(ValueError,'NATIVE_CANONICAL_DRIFT'):
                resume.escrow_and_publish_current_native(Path(temp),proof)
            self.assertEqual(original.read_bytes(),b'{"status":"OTHER"}\n')

