"""No network or host access; permissions remain independent from readiness."""
from pathlib import Path
import sys
import unittest
import urllib.error
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import rc6_deploy_resume_preparation as prepare

class PublicationTests(unittest.TestCase):
    def test_only_blob_endpoint_is_used_and_hashes_verified(self):
        calls = []
        def post(path, payload):
            calls.append(path)
            return {'sha': prepare.resume.git_blob(payload['content'])}
        status, rows = prepare.stage_blobs({'a.py': 'x=1\n', '.github/workflows/a.yml': 'name: test\n'}, post)
        self.assertEqual(status, 'BLOBS_STAGED')
        self.assertEqual(calls, ['/git/blobs', '/git/blobs'])
        self.assertEqual(len(rows), 2)

    def test_403_is_explicit_artifact_only_not_successful_publication(self):
        def forbidden(path, payload):
            raise urllib.error.HTTPError('https://api.github.com/example', 403, 'Forbidden', {}, None)
        status, rows = prepare.stage_blobs({'test.py': 'pass\n'}, forbidden)
        self.assertEqual(status, 'ARTIFACT_ONLY')
        self.assertEqual(rows[0]['sha'], prepare.resume.git_blob('pass\n'))

    def test_wrong_blob_hash_is_blocking(self):
        with self.assertRaisesRegex(RuntimeError, 'STAGED_BLOB_MISMATCH'):
            prepare.stage_blobs({'a': 'a'}, lambda path, payload: {'sha': '0' * 40})

    def test_other_http_errors_are_not_hidden(self):
        def failed(path, payload):
            raise urllib.error.HTTPError('https://api.github.com/example', 500, 'Failed', {}, None)
        with self.assertRaises(urllib.error.HTTPError):
            prepare.stage_blobs({'a': 'a'}, failed)

    def test_pinned_resume_module_contains_original_safety_checks(self):
        actual = prepare.resume.git_blob(Path(prepare.resume.__file__).read_text())
        self.assertEqual(actual, prepare.BASE_MODULE_BLOB)
        for code in ['PRODUCT_HEAD_DRIFT', 'RUNTIME_PROVENANCE_DRIFT', 'READINESS_REGRESSION', 'PLAN_HASH_MISMATCH']:
            self.assertIn(code, Path(prepare.resume.__file__).read_text())

if __name__ == '__main__':
    unittest.main()
