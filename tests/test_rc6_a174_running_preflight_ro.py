"""Adversarial A174 frozen artifact and exact running A171 preflight."""
import base64
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import fcntl
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
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'scripts/rc6_a174_running_preflight_ro.py'
spec = importlib.util.spec_from_file_location('a174_probe', SOURCE)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
rspec = importlib.util.spec_from_file_location('a174_runner', ROOT/'scripts/rc6_a160_preflight_runner.py')
runner = importlib.util.module_from_spec(rspec)
rspec.loader.exec_module(runner)
POLICY = base64.b64encode(b'{"units":{}}').decode()
DISK = base64.b64encode(b'{"disk":{"min_inode_free_percent":10}}').decode()


class HostTests(unittest.TestCase):
    def result(self, required=400, *, candidate=host.EXPECTED_CANDIDATE):
        output=io.StringIO()
        with redirect_stdout(output):
            rc=host.main(candidate,host.EXPECTED_TREE,POLICY,DISK,required)
        lines=output.getvalue().splitlines()
        self.assertEqual(len(lines),1)
        self.assertTrue(lines[0].startswith('RC6_A174_RUNNING_PREFLIGHT_RO='))
        return rc,json.loads(lines[0].split('=',1)[1])

    def test_wrong_candidate_or_droplet_precedes_lock(self):
        with patch.object(host,'read_lock',side_effect=AssertionError('LOCK_TOUCHED')):
            code,row=self.result(candidate='a'*40)
            self.assertEqual((code,row['error_code']),(2,'CANDIDATE_OR_DISK_INPUT_RED'))
            with patch.object(host,'metadata',side_effect=['594077618','nyc1']):
                code,row=self.result()
            self.assertEqual((code,row['error_code']),(2,'DROPLET_IDENTITY_MISMATCH'))

    def test_existing_root_mutex_no_create_and_barrier_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            lock=Path(d)/'mutex'
            barrier=Path(d)/'barrier'
            lock.write_bytes(b'keep'); lock.chmod(0o600)
            real_lstat=Path.lstat; real_fstat=os.fstat
            def owned(info):
                return types.SimpleNamespace(st_mode=info.st_mode,st_uid=0,
                    st_nlink=info.st_nlink,st_dev=info.st_dev,st_ino=info.st_ino)
            def lstat(path,*args,**kwargs):
                info=real_lstat(path,*args,**kwargs)
                return owned(info) if path in {lock,lock.parent} else info
            with patch.object(host,'LOCK',lock),patch.object(host,'BARRIER',barrier),\
                 patch.object(host.os,'geteuid',return_value=0),\
                 patch.object(Path,'lstat',lstat),\
                 patch.object(host.os,'fstat',side_effect=lambda fd:owned(real_fstat(fd))):
                fd=host.read_lock()
                rival=os.open(lock,os.O_RDONLY)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(rival,fcntl.LOCK_EX|fcntl.LOCK_NB)
                finally:
                    os.close(rival); os.close(fd)
                self.assertEqual(lock.read_bytes(),b'keep')
                barrier.write_text('{}')
                with self.assertRaisesRegex(ValueError,'BARRIER_PRIOR_ATTEMPT_REVIEW_REQUIRED'):
                    host.read_lock()
                barrier.unlink(); lock.unlink()
                with self.assertRaises(FileNotFoundError):
                    host.read_lock()

    def fixtures(self, *, prior_sha=host.EXPECTED_PRIOR_RECEIPT, guardian=True, timer=True):
        proof={'origin_candidate':host.EXPECTED_PRIOR,'origin_sha256':prior_sha,
               'image_id':host.EXPECTED_PRIOR_IMAGE,'operator_receipt_sha256':'d'*64,
               'flat_book':{'process_state':'WAITING_MARKET','session_state':'MARKET_CLOSED',
                            'real_orders_sent':0},
               'containers':{'porota_production_observer':{'id':'raw-id-secret'}}}
        q=types.SimpleNamespace(command=None,A171_FAILED_CANDIDATE=host.EXPECTED_PRIOR,
            A171_FAILED_RECEIPT_SHA256=host.EXPECTED_PRIOR_RECEIPT,
            A171_LOADED_IMAGE_ID=host.EXPECTED_PRIOR_IMAGE,
            a171_failed_running_proof=lambda *args,**kwargs:proof)
        s=types.SimpleNamespace(command=None,
            exact_a151_operator_preopen_pause=lambda:('d'*64,'A151-1-1'),
            operator_preopen_timer_disabled=lambda:True)
        timers={'all_held_for_migration':timer,'a171_chain_exact':timer,
                'ppi_snapshot_matches_prior':timer,'owned_count':16}
        state={'state':'AUTHENTICATED','fresh_under_1900s':guardian}
        return q,s,timers,state

    def probe(self, *, prior_sha=host.EXPECTED_PRIOR_RECEIPT, guardian=True,
              timer=True, disk=500, inodes=95):
        q,s,timers,state=self.fixtures(prior_sha=prior_sha,guardian=guardian,timer=timer)
        with patch.object(host,'metadata',side_effect=['594077619','nyc1']),\
             patch.object(host,'read_lock',return_value=os.open('/dev/null',os.O_RDONLY)),\
             patch.multiple(host,STAGED_Q_GZIP_B64='q',STAGED_Q_SHA256='a'*64,
                 STAGED_S_GZIP_B64='s',STAGED_S_SHA256='b'*64,create=True),\
             patch.object(host,'stage',side_effect=[q,s]),\
             patch.object(host,'scheduler_receipt',return_value=timers),\
             patch.object(host,'guardian_state',return_value=state),\
             patch.object(host.shutil,'disk_usage',return_value=types.SimpleNamespace(free=disk)),\
             patch.object(host.os,'statvfs',return_value=types.SimpleNamespace(f_ffree=inodes,f_files=100)):
            return self.result()

    def test_success_contains_no_provider_or_raw_container_payload_and_no_go(self):
        rc,row=self.probe()
        self.assertEqual(rc,0,row)
        self.assertEqual(row['status'],'GREEN')
        self.assertEqual(row['admission']['mode'],'CLOSED_A171_PENDING_FIX_FORWARD')
        self.assertEqual(row['admission']['prior_receipt_sha256'],host.EXPECTED_PRIOR_RECEIPT)
        self.assertFalse(row['operational_go'])
        self.assertEqual(row['host_mutations'],0)
        self.assertNotIn('raw-id-secret',json.dumps(row))

    def test_wrong_prior_receipt_and_failed_timer_or_guardian_never_green(self):
        rc,row=self.probe(prior_sha='a'*64)
        self.assertEqual((rc,row['status'],row['error_code']),(2,'RED','A171_RUNNING_PROOF_RED'))
        for flags in ({'timer':False},{'guardian':False},{'disk':399},{'inodes':9}):
            rc,row=self.probe(**flags)
            self.assertEqual((rc,row['status']),(2,'RED'),flags)

    def test_staged_source_hash_must_match(self):
        import gzip
        source=b'VALUE=1\n'+b'# padding\n'*2500
        encoded=base64.b64encode(gzip.compress(source,mtime=0)).decode()
        with self.assertRaisesRegex(ValueError,'STAGED_SOURCE_HASH_OR_SIZE_RED'):
            host.stage(encoded,'0'*64,'safe',30000)

    def test_exact_a171_timer_chain_and_guardian_job(self):
        names=['porota-preopen-rc6.timer','porota-iol-oauth-health-rc6.timer'] + [
            'porota-test%d-rc6.timer'%i for i in range(14)]
        policy=b'{"units":{"x":{}}}'
        ppi=[['ppi-watch.timer','enabled','active']]
        q=types.SimpleNamespace(A170_FAILED_CANDIDATE='a'*40,
            A169_PRIOR_RECEIPT_SHA256='b'*64,A170_FAILED_RECEIPT_SHA256='c'*64)
        prior={'candidate_sha':host.EXPECTED_PRIOR,'status':'SUSPENDED',
               'policy_sha256':hashlib.sha256(policy).hexdigest(),
               'timers':names,'ppi_before':ppi,'attempted':names,
               'paused_from':q.A170_FAILED_CANDIDATE,
               'a169_origin_sha256':q.A169_PRIOR_RECEIPT_SHA256,
               'a170_origin_sha256':q.A170_FAILED_RECEIPT_SHA256,
               'preserved_iol_timer':names[1],
               'operator_pause_timer':names[0],
               'operator_pause_receipt_sha256':'d'*64,
               'rescue_failure_disposition':'A145_NEW_CANDIDATE_UNVALIDATED_HELD'}
        def command(*args):
            name=args[2] if args[1]=='show' else args[2]
            if args[1]=='show': return name.removesuffix('.timer')+'.service',0
            if args[1]=='is-enabled': return ('disabled' if name==names[0] else 'enabled'),0
            if args[1]=='is-active':
                if name.endswith('.service'): return 'inactive',3
                return ('active' if name==names[1] else 'inactive'),0
            raise AssertionError(args)
        s=types.SimpleNamespace(command=command,timer_policy=lambda _:names,
            preserved_iol_guard=lambda _:names[1],IOL_OAUTH_GUARD_TIMER=names[1],
            OPERATOR_PREOPEN_TIMER=names[0],ROOT=Path('/unused'),
            ppi_snapshot=lambda:ppi,operator_preopen_hold_evidence=lambda:'d'*64,
            _operator_pause_json=lambda *args:(prior,'f'*64))
        proof={'flat_book':{'real_orders_sent':0}}
        row=host.scheduler_receipt(s,policy,proof,q)
        self.assertTrue(row['all_held_for_migration'] and row['a171_chain_exact'])
        prior['a170_origin_sha256']='wrong'
        self.assertFalse(host.scheduler_receipt(s,policy,proof,q)['a171_chain_exact'])
        prior['a170_origin_sha256']=q.A170_FAILED_RECEIPT_SHA256
        s.command=lambda *args:('failed',3) if args[1]=='is-active' and args[2]==names[1].removesuffix('.timer')+'.service' else command(*args)
        self.assertFalse(host.scheduler_receipt(s,policy,proof,q)['all_held_for_migration'])

    def test_guardian_state_requires_authenticated_recent_oneshot(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            path=root/'data/market/iol_oauth_guardian_state.json'
            path.parent.mkdir(parents=True)
            now=datetime.now(timezone.utc)
            payload={'schema':'POROTA_RC6_IOL_OAUTH_GUARDIAN_STATE_V1',
                     'state':'AUTHENTICATED','reason':'OFFHOURS_OAUTH_GUARDIAN',
                     'incident_seq':1,'checked_at':now.isoformat(),
                     'last_authenticated_at':now.isoformat()}
            path.write_text(json.dumps(payload))
            s=types.SimpleNamespace(IOL_OAUTH_GUARD_TIMER='porota-iol-oauth-health-rc6.timer',
                IOL_OAUTH_GUARD_SERVICE='porota-iol-oauth-health-rc6.service',
                command=lambda *args:('porota-iol-oauth-health-rc6.service',0))
            with patch.object(host,'ROOT',root):
                self.assertTrue(host.guardian_state(s,now)['fresh_under_1900s'])
                payload['checked_at']=(now-timedelta(seconds=1901)).isoformat()
                path.write_text(json.dumps(payload))
                with self.assertRaisesRegex(ValueError,'IOL_GUARDIAN_FRESHNESS_RED'):
                    host.guardian_state(s,now)
                payload['checked_at']=now.isoformat();payload['last_authenticated_at']=now.isoformat()
                payload['state']='REAUTH_REQUIRED';path.write_text(json.dumps(payload))
                with self.assertRaisesRegex(ValueError,'IOL_GUARDIAN_AUTH_STATE_RED'):
                    host.guardian_state(s,now)


class RunnerTests(unittest.TestCase):
    def test_artifact_redirect_never_forwards_bearer(self):
        req=urllib.request.Request('https://api.github.com/repos/a/b/actions/artifacts/1/zip',
                                   headers={'Authorization':'Bearer SECRET'})
        target='https://productionresultssa0.blob.core.windows.net/answers/artifact.zip?sig=x'
        redirect=runner.ArtifactRedirect().redirect_request(req,None,302,'Found',{},target)
        self.assertNotIn('SECRET',str(redirect.header_items()))
        with self.assertRaisesRegex(ValueError,'ARTIFACT_REDIRECT_ORIGIN_RED'):
            runner.ArtifactRedirect().redirect_request(req,None,302,'Found',{},'https://attacker.invalid/a')

    def test_pinned_artifact_digest_rejects_mismatch_before_download(self):
        def api(url,token,*,dest=None):
            if dest is not None: raise AssertionError('ZIP_DOWNLOAD_BEFORE_DIGEST')
            if '/runs/' in url:
                return {'name':'Porota Predeploy V2','head_sha':'a'*40,'conclusion':'success'}
            return {'name':'porota-predeploy-v2-'+'a'*40,'expired':False,
                    'workflow_run':{'id':123},'digest':'sha256:'+'b'*64}
        with tempfile.TemporaryDirectory() as d,patch.object(runner,'api',side_effect=api),\
             patch.dict(os.environ,{'RC6_PREDEPLOY_ARTIFACT_DIGEST':'sha256:'+'c'*64}):
            with self.assertRaisesRegex(ValueError,'PINNED_ARTIFACT_DIGEST_RED'):
                runner.prepare('a'*40,'d'*40,'123','456','a/b','token',
                               ROOT,ROOT,Path(d)/'out')

    def test_frozen_artifact_exact_source_and_disk_binding_without_host(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);env=root/'github-env';env.write_text('')
            paths=['scripts/rc6_deploy_quiescence.py','scripts/rc6_deploy_scheduler_suspend.py',
                   'scripts/rc6_disk_space_guard.py','ops/policy/rc6-disk-housekeeping-v1.json',
                   'ops/policy/host-control-plane-reconciliation-v2.json']
            rows=[{'path':p,'sha256':runner.digest(ROOT/p),'bytes':(ROOT/p).stat().st_size}
                  for p in paths]
            image=b'exact-image';bundle=b'exact-bundle'
            frozen={'candidate_sha':'a'*40,'candidate_tree_sha':'b'*40,
                    'paper_mode_required':'PRODUCTION_PAPER','real_orders_sent_required':0,
                    'real_order_capability_required':'BLOCKED','build_once':True,
                    'image_id':'sha256:'+'f'*64,'image_tar_sha256':hashlib.sha256(image).hexdigest(),
                    'image_size_bytes':100}
            payload={'porota-frozen-candidate.json':json.dumps(frozen).encode(),
                     'porota-predeploy-image.tar.gz':image,
                     'porota-deploy-bundle-v2.tgz':bundle,
                     'porota-deploy-bundle-v2-manifest.json':json.dumps({
                         'files':rows,'bundle_sha256':hashlib.sha256(bundle).hexdigest()}).encode(),
                     'porota-host-manifest-v2.json':b'{"status":"GREEN","source_git_sha":"'+b'a'*40+b'"}',
                     'porota-host-policy-v2.json':b'{"status":"GREEN"}'}
            archive=root/'artifact.zip'
            with zipfile.ZipFile(archive,'w') as writer:
                for name,value in payload.items():writer.writestr(name,value)
            digest='sha256:'+runner.digest(archive)
            def api(url,token,*,dest=None):
                if dest is not None:return shutil.copyfile(archive,dest)
                if '/runs/' in url:return {'name':'Porota Predeploy V2','head_sha':'a'*40,'conclusion':'success'}
                return {'name':'porota-predeploy-v2-'+'a'*40,'expired':False,
                        'workflow_run':{'id':123},'digest':digest}
            with patch.object(runner,'api',side_effect=api),\
                 patch.dict(os.environ,{'RC6_PREDEPLOY_ARTIFACT_DIGEST':digest,'GITHUB_ENV':str(env)}),\
                 redirect_stdout(io.StringIO()):
                runner.prepare('a'*40,'b'*40,'123','456','a/b','token',ROOT,ROOT,root/'out')
            stdin=(root/'out/host-stdin.py').read_bytes()
            compile(stdin,'host-stdin','exec')
            self.assertIn(b'RC6_A174_RUNNING_PREFLIGHT_RO=',stdin)
            self.assertIn('RC6_A160_REQUIRED_BYTES=',env.read_text())


if __name__ == '__main__':unittest.main()
