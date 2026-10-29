"""Pure tests for the A179 read-only diagnostic receipt boundaries."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.rc6_a179_preopen_origin_ro import bounded_json, capture


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

