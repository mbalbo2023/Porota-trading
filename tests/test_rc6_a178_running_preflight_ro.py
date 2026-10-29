"""A178 preflight must prove the failed host without a single host mutation."""
import base64
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request
import zipfile

from scripts import rc6_a178_running_preflight_ro as probe
from scripts import rc6_a178_preflight_runner as binder


class A178ReadOnlyPreflightTest(unittest.TestCase):
    def test_candidate_mismatch_blocks_before_metadata_or_host_lock(self):
        with patch.object(probe,'metadata',side_effect=AssertionError('host must not be contacted')):
            with self.assertRaisesRegex(ValueError,'A178_CANDIDATE_OR_DAY_CHANGED'):
                probe.prove('f'*40,probe.EXPECTED_TREE,base64.b64encode(b'{}').decode(),
                            4*1024**3)

    def test_staged_candidate_sha_and_size_are_strict(self):
        source=(Path(__file__).parents[1]/'scripts/rc6_deploy_quiescence.py').read_bytes()
        encoded=base64.b64encode(gzip.compress(source,mtime=0)).decode()
        digest=hashlib.sha256(source).hexdigest()
        module=probe.stage(encoded,digest,'a178_local_fixture',maximum=260000)
        self.assertEqual(module.A176_OPERATOR_NO_GO_RECEIPT_SHA256,probe.EXPECTED_SEAL)
        with self.assertRaisesRegex(ValueError,'STAGED_SOURCE_RED'):
            probe.stage(encoded,'0'*64,'a178_tampered',maximum=260000)
        with self.assertRaises(ValueError):
            probe.stage(encoded+'%',digest,'a178_nonbase64',maximum=260000)

    def test_host_mutex_is_read_only_and_barrier_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock=Path(tmp)/'lock'
            barrier=Path(tmp)/'barrier'
            lock.write_text('untouched')
            lock.chmod(0o600)
            before=lock.read_bytes()
            real_fstat=os.fstat
            real_lstat=os.lstat
            def root_stat(record):
                return SimpleNamespace(st_mode=record.st_mode,st_uid=0,
                    st_nlink=record.st_nlink,st_dev=record.st_dev,st_ino=record.st_ino)
            with patch.object(probe,'LOCK',lock),patch.object(probe,'BARRIER',barrier),\
                 patch.object(probe.os,'geteuid',return_value=0),\
                 patch.object(probe.os,'fstat',side_effect=lambda fd:root_stat(real_fstat(fd))),\
                 patch.object(probe.os,'lstat',side_effect=lambda path:root_stat(real_lstat(path))):
                fd=probe.read_lock()
                os.close(fd)
                self.assertEqual(lock.read_bytes(),before)
                barrier.write_text('{}')
                with self.assertRaisesRegex(ValueError,'BARRIER'):
                    probe.read_lock()
                barrier.unlink()
                lock.chmod(0o644)
                with self.assertRaisesRegex(ValueError,'MUTEX_IDENTITY'):
                    probe.read_lock()

    def test_disk_gate_rejects_space_and_inodes(self):
        policy=base64.b64encode(json.dumps({'disk':{'min_inode_free_percent':10}}).encode()).decode()
        with patch.object(probe.shutil,'disk_usage',return_value=SimpleNamespace(free=5*1024**3)),\
             patch.object(probe.os,'statvfs',return_value=SimpleNamespace(f_ffree=400,f_files=1000)):
            self.assertEqual(probe.capacity(4*1024**3,policy)['margin_bytes'],1024**3)
            with self.assertRaisesRegex(ValueError,'DISK_OR_INODES_RED'):
                probe.capacity(6*1024**3,policy)
        with patch.object(probe.shutil,'disk_usage',return_value=SimpleNamespace(free=5*1024**3)),\
             patch.object(probe.os,'statvfs',return_value=SimpleNamespace(f_ffree=40,f_files=1000)):
            with self.assertRaisesRegex(ValueError,'DISK_OR_INODES_RED'):
                probe.capacity(4*1024**3,policy)

    def test_docker_image_exactly_two_after_cleanup(self):
        good='\n'.join([probe.ACTIVE_IMAGE,probe.CRITICAL_IMAGE])
        with patch.object(probe.subprocess,'run',return_value=SimpleNamespace(stdout=good)):
            self.assertEqual(probe.read_docker_images(),{probe.ACTIVE_IMAGE,probe.CRITICAL_IMAGE})
        with patch.object(probe.subprocess,'run',return_value=SimpleNamespace(stdout=good+'\nsha256:'+'f'*64)):
            with self.assertRaisesRegex(ValueError,'IMAGE_INVENTORY_RED'):
                probe.read_docker_images()

    def test_closed_session_failure_emits_only_verified_paper_origin_fields(self):
        class FakeOrigin:
            A175_FAILED_CANDIDATE = probe.EXPECTED_CANDIDATE

            def rescue_flat_book(self):
                return {'process_state':'RUNNING','session_state':'MARKET_OPEN',
                        'source_sha':self.A175_FAILED_CANDIDATE,'real_orders_sent':0}

            def admit(self):
                raise ValueError('QUIESCENCE_CLOSED_REQUIRED')

            def critical_admission(self):
                return {'capability':'issues_only'}

        result=probe.diagnose_origin_book(FakeOrigin())
        self.assertEqual(result,{'flat_book':'VERIFIED','process_state':'RUNNING',
            'session_state':'MARKET_OPEN','source_is_a175':True,
            'real_orders_sent':0,'admission':'RED:QUIESCENCE_CLOSED_REQUIRED',
            'critical_capability':'issues_only'})

    def test_artifact_redirect_never_forwards_github_token(self):
        source=urllib.request.Request(
            'https://api.github.com/repos/mbalbo2023/Porota-trading/actions/artifacts/123/zip',
            headers={'Authorization':'Bearer SECRET'})
        good=binder.ArtifactRedirect().redirect_request(
            source,None,302,'Found',{},
            'https://productionresultssa0.blob.core.windows.net/actions-results/zip?sig=test')
        self.assertNotIn('Authorization',good.headers)
        self.assertEqual(good.get_method(),'GET')
        for host in ('example.net','blob.core.windows.net.evil.invalid'):
            with self.assertRaisesRegex(ValueError,'ARTIFACT_REDIRECT_ORIGIN_RED'):
                binder.ArtifactRedirect().redirect_request(source,None,302,'Found',{},
                    'https://'+host+'/artifact?sig=test')

    def test_exact_archive_members_reject_duplicate_and_parent_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'artifact.zip'
            output=Path(tmp)/'data'
            output.mkdir()
            with zipfile.ZipFile(target,'w') as archive:
                for name in binder.NAMES:
                    archive.writestr(name,b'safe')
            binder.extract_exact(target,output)
            self.assertEqual({p.name for p in output.iterdir()},binder.NAMES)
            with zipfile.ZipFile(target,'a') as archive:
                archive.writestr('../porota-frozen-candidate.json',b'malicious')
            with self.assertRaisesRegex(ValueError,'FROZEN_ARCHIVE_MEMBERS_RED'):
                binder.extract_exact(target,output)

    def test_frozen_artifact_binds_exact_sources_without_host_contact(self):
        root=Path(__file__).resolve().parents[1]
        candidate=probe.EXPECTED_CANDIDATE
        tree=probe.EXPECTED_TREE
        with tempfile.TemporaryDirectory() as tmp:
            temp=Path(tmp)
            image=b'fake immutable compressed image for offline test only'
            bundle=b'fake immutable deploy bundle for offline test only'
            paths=('scripts/rc6_deploy_quiescence.py',
                   'scripts/rc6_deploy_scheduler_suspend.py',
                   'scripts/rc6_disk_space_guard.py',
                   'ops/policy/rc6-disk-housekeeping-v1.json',
                   'ops/policy/host-control-plane-reconciliation-v2.json')
            file_rows=[{'path':name,'sha256':hashlib.sha256((root/name).read_bytes()).hexdigest(),
                        'bytes':(root/name).stat().st_size} for name in paths]
            manifest={'files':file_rows,'bundle_sha256':hashlib.sha256(bundle).hexdigest()}
            frozen={'candidate_sha':candidate,'candidate_tree_sha':tree,
                    'paper_mode_required':'PRODUCTION_PAPER','real_orders_sent_required':0,
                    'real_order_capability_required':'BLOCKED','build_once':True,
                    'image_id':'sha256:'+'a'*64,'image_size_bytes':1024*1024*1024,
                    'image_tar_sha256':hashlib.sha256(image).hexdigest()}
            archive=temp/'artifact.zip'
            entries={
                'porota-frozen-candidate.json':json.dumps(frozen).encode(),
                'porota-predeploy-image.tar.gz':image,
                'porota-deploy-bundle-v2.tgz':bundle,
                'porota-deploy-bundle-v2-manifest.json':json.dumps(manifest).encode(),
                'porota-host-manifest-v2.json':json.dumps(
                    {'status':'GREEN','source_git_sha':candidate}).encode(),
                'porota-host-policy-v2.json':b'{"status":"GREEN"}',
            }
            with zipfile.ZipFile(archive,'w') as output:
                for name,value in entries.items():
                    output.writestr(name,value)
            digest='sha256:'+hashlib.sha256(archive.read_bytes()).hexdigest()
            def fake_api(url,token,*,dest=None):
                if dest is not None:
                    shutil.copyfile(archive,dest)
                elif url.endswith('/runs/123'):
                    return {'name':'Porota Predeploy V2','head_sha':candidate,
                            'conclusion':'success'}
                elif url.endswith('/artifacts/456'):
                    return {'name':'porota-predeploy-v2-'+candidate,'expired':False,
                            'workflow_run':{'id':123},'digest':digest}
                else:
                    raise AssertionError('UNEXPECTED_ARTIFACT_ENDPOINT:'+url)
            envfile=temp/'github_env'
            envfile.write_text('')
            with patch.object(binder,'api',side_effect=fake_api),patch.dict(os.environ,{
                    'RC6_PREDEPLOY_ARTIFACT_DIGEST':digest,'GITHUB_ENV':str(envfile)}):
                binder.prepare(candidate,tree,'123','456','mbalbo2023/Porota-trading',
                               'not-a-real-token',root,root,temp/'rc6-a160')
            host=(temp/'rc6-a160/host-stdin.py').read_bytes()
            # Regression: a prefixed assignment BEFORE a __future__ statement
            # passed the artifact tests but fails on the real Python host.
            compile(host,'a178/frozen-host-stdin.py','exec')
            self.assertLess(host.index(b'from __future__ import annotations'),
                            host.index(b'STAGED_Q_GZIP_B64='))
            self.assertIn(b'STAGED_Q_GZIP_B64=',host)
            self.assertIn(b'STAGED_S_GZIP_B64=',host)
            self.assertIn(b'EXPECTED_CANDIDATE',host)
            self.assertIn(b'RC6_A160_REQUIRED_BYTES=',envfile.read_bytes())


if __name__ == '__main__':
    unittest.main()
