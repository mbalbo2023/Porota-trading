"""Negative tests for the exact 10-minute cancelled-attempt auditor."""
import hashlib
import inspect
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts import rc6_a179_cancelled_native_ro as audit


class ExactCancelRoTests(TestCase):
    def test_fingerprint_rejects_drift(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'receipt.json'
            raw=b'{"state":"INFLIGHT"}'
            path.write_bytes(raw)
            self.assertEqual(audit.hashed_json(path,hashlib.sha256(raw).hexdigest()),
                             {'state':'INFLIGHT'})
            with self.assertRaisesRegex(ValueError,'DIGEST_DRIFT'):
                audit.hashed_json(path,'0'*64)

    def test_symlink_refused(self):
        with TemporaryDirectory() as temp:
            target=Path(temp)/'original'
            target.write_text('{}')
            alias=Path(temp)/'alias'
            alias.symlink_to(target)
            with self.assertRaisesRegex(ValueError,'RECEIPT_UNSAFE'):
                audit.hashed_json(alias,hashlib.sha256(b'{}').hexdigest())

    def test_diagnostic_has_no_file_or_runtime_mutation(self):
        source=inspect.getsource(audit)
        for forbidden in ('os.unlink(','.unlink(','.replace(','docker run',
                          'systemctl start','systemctl restart','systemctl stop',
                          'docker image rm','shutil.rmtree'):
            self.assertNotIn(forbidden,source)
        self.assertEqual(audit.CANDIDATE,'7d62d2f4ed353231da8566a360a1179e99bcc7eb')
        self.assertEqual(audit.RUN,'40916351644')
