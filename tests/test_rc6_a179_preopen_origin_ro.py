"""Pure tests for the A179 read-only diagnostic receipt boundaries."""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.rc6_a179_preopen_origin_ro import (
    QUIESCENCE_SHA256, bounded_json, capture, staged_readonly_probe)


class A179RoGuardTests(TestCase):
    def test_exact_bounded_receipt(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)/'receipt.json'
            path.write_text('{"status":"SUSPENDED"}')
            obj, digest = bounded_json(path)
            self.assertEqual(obj, {'status':'SUSPENDED'})
            self.assertEqual(len(digest), 64)

    def test_reject_symlink_receipt(self):
        with TemporaryDirectory() as temp:
            actual = Path(temp)/'actual.json'
            actual.write_text('{}')
            link = Path(temp)/'link.json'
            link.symlink_to(actual)
            with self.assertRaisesRegex(ValueError, 'UNSAFE_RECEIPT'):
                bounded_json(link)

    def test_reject_oversized_receipt(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)/'large.json'
            path.write_text(' '*65537)
            with self.assertRaisesRegex(ValueError, 'UNSAFE_RECEIPT'):
                bounded_json(path)

    def test_reject_non_object_receipt(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)/'list.json'
            path.write_text('[]')
            with self.assertRaisesRegex(ValueError, 'RECEIPT_NOT_DICT'):
                bounded_json(path)

    def test_capture_exception_reason(self):
        def fail():
            raise ValueError('STAGED_REQUIRED_UNTIL_VALIDATED_RUNTIME')
        out = capture('helper', fail)
        self.assertEqual(out['reason'], 'STAGED_REQUIRED_UNTIL_VALIDATED_RUNTIME')
        self.assertFalse(out['ok'])

    def test_pinned_installed_source_is_exact_candidate_blob(self):
        source = Path(__file__).resolve().parents[1]/'scripts/rc6_deploy_quiescence.py'
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), QUIESCENCE_SHA256)

    def test_source_mismatch_blocks_before_any_helper_call(self):
        with TemporaryDirectory() as temp:
            script = Path(temp)/'scripts'
            script.mkdir()
            (script/'rc6_deploy_quiescence.py').write_bytes(b'wrong source')
            with self.assertRaisesRegex(ValueError, 'SOURCE_SHA_RED'):
                staged_readonly_probe(Path(temp), object(), object())

