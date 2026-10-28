import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import rc6_a159_artifact_gate as gate


class ArtifactGateTests(unittest.TestCase):
    def metadata(self):
        run={'id':gate.RUN_ID,'head_sha':gate.CANDIDATE,
             'status':'completed','conclusion':'success'}
        a={'id':gate.ARTIFACT_ID,'name':'porota-predeploy-v2-'+gate.CANDIDATE,
           'digest':gate.ARTIFACT_DIGEST,'size_in_bytes':gate.ARTIFACT_SIZE,
           'expired':False,'expires_at':'2026-11-03T22:05:34Z',
           'workflow_run':{'id':gate.RUN_ID,'head_sha':gate.CANDIDATE}}
        return run,{'artifacts':[a]}

    def test_exact_unexpired_successful_run_and_artifact(self):
        run,payload=self.metadata()
        self.assertEqual(gate.verify_metadata(run,payload,now=1793210000),
                         '2026-11-03T22:05:34+00:00')
        for field,value in [('digest','sha256:'+'0'*64),('expired',True),
                            ('size_in_bytes',1)]:
            with self.subTest(field=field):
                x=json.loads(json.dumps(payload));x['artifacts'][0][field]=value
                with self.assertRaises(ValueError):gate.verify_metadata(run,x,now=1793210000)
        run['conclusion']='failure'
        with self.assertRaises(ValueError):gate.verify_metadata(run,payload,now=1793210000)
        run['conclusion']='success'
        with self.assertRaisesRegex(ValueError,'EXPIRING'):
            gate.verify_metadata(run,payload,now=1793743400)

    def zip_fixture(self,root,duplicate=False):
        f=root/'proof.zip'
        with zipfile.ZipFile(f,'w') as z:
            z.writestr('folder/one',b'abc')
            z.writestr('folder/two',b'defg')
            if duplicate:z.writestr('alternate/one',b'abc')
        return f

    def test_zip_bytes_and_members_match_exact_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            f=self.zip_fixture(Path(d))
            digest='sha256:'+hashlib.sha256(f.read_bytes()).hexdigest()
            with patch.multiple(gate,FILES={'one':3,'two':4},
                                ARTIFACT_SIZE=f.stat().st_size,ARTIFACT_DIGEST=digest):
                self.assertEqual(set(gate.verify_zip(f)),{'one','two'})
                with patch.object(gate,'ARTIFACT_DIGEST','sha256:'+'0'*64):
                    with self.assertRaisesRegex(ValueError,'DIGEST_RED'):gate.verify_zip(f)
            duplicate=self.zip_fixture(Path(d),duplicate=True)
            digest='sha256:'+hashlib.sha256(duplicate.read_bytes()).hexdigest()
            with patch.multiple(gate,FILES={'one':3,'two':4},
                                ARTIFACT_SIZE=duplicate.stat().st_size,ARTIFACT_DIGEST=digest):
                with self.assertRaisesRegex(ValueError,'COLLISION'):
                    gate.verify_zip(duplicate)


if __name__=='__main__':unittest.main()
