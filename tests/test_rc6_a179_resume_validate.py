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

