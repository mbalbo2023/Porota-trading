"""Adversarial tests for an A160 diagnostic that never grants PAPER GO."""
import base64
from contextlib import redirect_stdout
import fcntl
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

SOURCE=Path(__file__).resolve().parents[1]/'scripts/rc6_a160_held_preflight_ro.py'
spec=importlib.util.spec_from_file_location('a160_host',SOURCE)
host=importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
RUNNER_SOURCE=SOURCE.with_name('rc6_a160_preflight_runner.py')
rspec=importlib.util.spec_from_file_location('a160_runner',RUNNER_SOURCE)
runner=importlib.util.module_from_spec(rspec)
rspec.loader.exec_module(runner)

SHA='a'*40
TREE='b'*40
HOST_POLICY=base64.b64encode(b'{"units":{}}').decode()
DISK_POLICY=base64.b64encode(b'{"disk":{"min_inode_free_percent":10}}').decode()


class HostProbeTests(unittest.TestCase):
    def result(self,*args):
        output=io.StringIO()
        with redirect_stdout(output):
            rc=host.main(*args)
        lines=output.getvalue().splitlines()
        self.assertEqual(len(lines),1)
        self.assertTrue(lines[0].startswith('RC6_A160_HELD_PREFLIGHT_RO='))
        return rc,json.loads(lines[0].split('=',1)[1])

    def test_wrong_droplet_fails_before_lock_or_docker(self):
        with patch.object(host,'metadata',side_effect=['594077618','nyc1']), \
             patch.object(host,'read_lock',side_effect=AssertionError('HOST_LOCK_TOUCHED')):
            code,receipt=self.result(SHA,TREE,HOST_POLICY,DISK_POLICY,100)
        self.assertEqual(code,2)
        self.assertEqual(receipt['error_code'],'DROPLET_IDENTITY_MISMATCH')
        self.assertNotIn('admission',receipt)

    def test_existing_lock_only_no_create_or_barrier_write(self):
        with tempfile.TemporaryDirectory() as directory:
            lock=Path(directory)/'existing.lock'
            barrier=Path(directory)/'barrier.json'
            lock.write_bytes(b'original-lock-byte-sequence')
            lock.chmod(0o600)
            before=(lock.stat().st_mtime_ns,lock.read_bytes())
            with patch.object(host,'LOCK',lock), patch.object(host,'BARRIER',barrier), \
                 patch.object(host.os,'geteuid',return_value=0):
                fd=host.read_lock()
                self.assertFalse(barrier.exists())
                probe=os.open(lock,os.O_RDONLY)
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
                os.close(probe)
                os.close(fd)
                self.assertEqual(before,(lock.stat().st_mtime_ns,lock.read_bytes()))
                with barrier.open('w') as stream:
                    json.dump({'status':'INFLIGHT'},stream)
                barrier.chmod(0o600)
                with self.assertRaisesRegex(ValueError,'BARRIER_PRIOR_ATTEMPT_REVIEW_REQUIRED'):
                    host.read_lock()
                barrier.unlink()
                lock.unlink()
                with self.assertRaises(FileNotFoundError):
                    host.read_lock()
                self.assertFalse(lock.exists())

    def test_staged_exact_source_rejects_digest_tampering(self):
        source=b'VALUE=1\n'+b'# padding\n'*2500
        compressed=base64.b64encode(gzip.compress(source,mtime=0)).decode()
        with self.assertRaisesRegex(ValueError,'STAGED_SOURCE_HASH_OR_SIZE_RED'):
            host.stage(compressed,'0'*64,'safe',30000)
        module=host.stage(compressed,hashlib.sha256(source).hexdigest(),'safe',30000)
        self.assertEqual(module.VALUE,1)

    def test_success_projection_contains_no_raw_container_or_account(self):
        fake_q=types.SimpleNamespace(command=None,
            a143_pending_running_proof=lambda **kw:{
                'source_sha':host.EXPECTED_OLD_SHA,
                'operator_hold':{'operator_receipt_sha256':'d'*64,
                                 'operator_operation_id':'A151-123-1'},
                'marker_sha256':'e'*64,'frozen_sha256':'f'*64,
                'prior_receipt_sha256':'a'*64,
                'critical_admission':{'capability':'issues_only','private':'ACCOUNT_SECRET'},
                'flat_book':{'process_state':'DEGRADED',
                             'session_state':'BOOT_CALENDAR','real_orders_sent':0},
                'containers':{'observer':{'id':'raw-container-id-PRIVATE'}}})
        fake_s=types.SimpleNamespace(exact_a151_operator_preopen_pause=lambda:None,
                                     operator_preopen_timer_disabled=lambda:True)
        timer={'all_held_for_migration':True,'a138_a143_chain_exact':True,
               'owned_count':16}
        with patch.object(host,'metadata',side_effect=['594077619','nyc1']), \
             patch.object(host,'read_lock',return_value=os.open('/dev/null',os.O_RDONLY)), \
             patch.multiple(host,STAGED_Q_GZIP_B64='q',STAGED_Q_SHA256='a'*64,
                            STAGED_S_GZIP_B64='s',STAGED_S_SHA256='b'*64,create=True), \
             patch.object(host,'stage',side_effect=[fake_q,fake_s]), \
             patch.object(host,'scheduler_receipt',return_value=timer), \
             patch.object(host,'guardian_state',return_value={'state':'NO_VERIFICADO'}), \
             patch.object(host.shutil,'disk_usage',return_value=types.SimpleNamespace(free=500)), \
             patch.object(host.os,'statvfs',return_value=types.SimpleNamespace(f_ffree=95,f_files=100)):
            rc,receipt=self.result(SHA,TREE,HOST_POLICY,DISK_POLICY,400)
        self.assertEqual(rc,0,receipt)
        self.assertEqual(receipt['admission']['mode'],'A151_HELD_OPEN')
        self.assertEqual(receipt['disk']['margin_bytes'],100)
        self.assertNotIn('ACCOUNT_SECRET',json.dumps(receipt))
        self.assertNotIn('raw-container-id',json.dumps(receipt))
        self.assertFalse(receipt['admission']['paper_execution_authorized'])

    def test_inode_gate_red_even_with_enough_bytes(self):
        fake_q=types.SimpleNamespace(command=None,a143_pending_running_proof=lambda **kw:{
            'source_sha':host.EXPECTED_OLD_SHA,'operator_hold':None,
            'marker_sha256':'e'*64,'frozen_sha256':'f'*64,
            'prior_receipt_sha256':'a'*64,
            'critical_admission':{'capability':'issues_only'},
            'flat_book':{'process_state':'DEGRADED','session_state':'MARKET_CLOSED',
                         'real_orders_sent':0}})
        fake_s=types.SimpleNamespace(exact_a151_operator_preopen_pause=lambda:None,
                                     operator_preopen_timer_disabled=lambda:True)
        with patch.object(host,'metadata',side_effect=['594077619','nyc1']), \
             patch.object(host,'read_lock',return_value=os.open('/dev/null',os.O_RDONLY)), \
             patch.multiple(host,STAGED_Q_GZIP_B64='q',STAGED_Q_SHA256='a'*64,
                            STAGED_S_GZIP_B64='s',STAGED_S_SHA256='b'*64,create=True), \
             patch.object(host,'stage',side_effect=[fake_q,fake_s]), \
             patch.object(host,'scheduler_receipt',return_value={
                 'all_held_for_migration':True,'a138_a143_chain_exact':True}), \
             patch.object(host,'guardian_state',return_value={'state':'NO_VERIFICADO'}), \
             patch.object(host.shutil,'disk_usage',return_value=types.SimpleNamespace(free=500)), \
             patch.object(host.os,'statvfs',return_value=types.SimpleNamespace(f_ffree=5,f_files=100)):
            rc,receipt=self.result(SHA,TREE,HOST_POLICY,DISK_POLICY,400)
        self.assertEqual(rc,2)
        self.assertTrue(receipt['disk']['bytes_gate'],receipt)
        self.assertFalse(receipt['disk']['inodes_gate'])

    def test_critical_command_only_issues_health_and_no_python_pycache(self):
        argv=['docker','exec','porota_critical_approval_rc6','python','-c',
              "print('RC6_CRITICAL_ISSUES_ONLY=GREEN')"]
        with patch.object(host.subprocess,'run',return_value=types.SimpleNamespace(
                 returncode=0,stdout='RC6_CRITICAL_ISSUES_ONLY=GREEN')) as run:
            host.bounded_quiescence_command(argv)
        called=run.call_args.args[0]
        self.assertEqual(called[:7],['docker','exec','-e','PYTHONDONTWRITEBYTECODE=1',
                                      'porota_critical_approval_rc6','python','-B'])


class RunnerBindingTests(unittest.TestCase):
    def test_frozen_artifact_and_exact_source_bindings_without_host(self):
        candidate_root=SOURCE.parents[1]
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            environment=root/'github-env'
            environment.write_text('')
            source_paths=['scripts/rc6_deploy_quiescence.py',
                          'scripts/rc6_deploy_scheduler_suspend.py',
                          'scripts/rc6_disk_space_guard.py',
                          'ops/policy/rc6-disk-housekeeping-v1.json',
                          'ops/policy/host-control-plane-reconciliation-v2.json']
            rows=[{'path':p,'sha256':runner.digest(candidate_root/p),
                   'bytes':(candidate_root/p).stat().st_size} for p in source_paths]
            image=b'exact-image-tar-content'
            bundle=b'exact-bundle-content'
            manifest={'files':rows,'bundle_sha256':hashlib.sha256(bundle).hexdigest()}
            frozen={'candidate_sha':SHA,'candidate_tree_sha':TREE,
                    'paper_mode_required':'PRODUCTION_PAPER',
                    'real_orders_sent_required':0,
                    'real_order_capability_required':'BLOCKED','build_once':True,
                    'image_tar_sha256':hashlib.sha256(image).hexdigest(),
                    'image_size_bytes':100}
            contents={'porota-frozen-candidate.json':json.dumps(frozen).encode(),
                      'porota-predeploy-image.tar.gz':image,
                      'porota-deploy-bundle-v2.tgz':bundle,
                      'porota-deploy-bundle-v2-manifest.json':json.dumps(manifest).encode(),
                      'porota-host-manifest-v2.json':json.dumps({
                          'status':'GREEN','source_git_sha':SHA}).encode(),
                      'porota-host-policy-v2.json':b'{"status":"GREEN"}',
                      'governed-test-receipt.json':b'{}'}
            archive=root/'artifact.zip'
            with zipfile.ZipFile(archive,'w') as zip_out:
                for name,data in contents.items():
                    zip_out.writestr(name,data)
            def fake_api(url,token,*,dest=None):
                if dest is not None:
                    shutil.copyfile(archive,dest)
                elif url.endswith('/runs/40708749022'):
                    return {'name':'Porota Predeploy V2','head_sha':SHA,
                            'conclusion':'success'}
                else:
                    return {'name':'porota-predeploy-v2-'+SHA,'expired':False,
                            'workflow_run':{'id':40708749022},
                            'digest':'sha256:'+runner.digest(archive)}
            output=io.StringIO()
            with patch.object(runner,'api',side_effect=fake_api), \
                 patch.dict(os.environ,{'GITHUB_ENV':str(environment)}), \
                 redirect_stdout(output):
                runner.prepare(SHA,TREE,'40708749022','12345','mbalbo2023/Porota-trading',
                               'test-token',candidate_root,candidate_root,root/'prepared')
            prepared=(root/'prepared/host-stdin.py').read_bytes()
            compile(prepared,'diagnostic-stdin','exec')
            self.assertIn(b'STAGED_Q_GZIP_B64=',prepared)
            self.assertIn(b'STAGED_S_GZIP_B64=',prepared)
            self.assertIn('RC6_A160_REQUIRED_BYTES=',environment.read_text())
            self.assertIn('RC6_A160_ARTIFACT_BOUND=GREEN',output.getvalue())


if __name__=='__main__':
    unittest.main()
