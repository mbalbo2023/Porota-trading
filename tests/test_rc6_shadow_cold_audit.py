"""Actual-source durable audit, crash gaps, fail-closed readers and codec bounds."""
import gzip
import hashlib
import os
import struct
import subprocess
from datetime import timedelta
import pytest
from rc6_shadow_runtime import cold_audit as c
from rc6_shadow_runtime import persistence as p
from tests.rc6_native_storage import native_storage

@pytest.fixture(scope='module')
def compiled_codec(tmp_path_factory):
    return native_storage(tmp_path_factory.mktemp('audit-native'))

@pytest.fixture(autouse=True)
def codec(compiled_codec, monkeypatch):
    monkeypatch.setattr(p, '_native_json_bound', compiled_codec)
    return compiled_codec

def seal(files, value=None):
    if value is None:
        value = {'nested':[{'code':'PPI','amount':'1.2345','n':n} for n in range(400)]}
    with c.Snapshot(files) as snapshot:
        return snapshot.seal(value,source_as_of='2026-10-05T13:20:00+00:00',configuration='fixed')

def test_snapshot_original_values_and_backward_roles_survive_source_changes(tmp_path,monkeypatch):
    import sqlite3
    value = {'rows':[{'key':str(i),'amount':'0.00001'} for i in range(500)]}
    original = b''.join(p.canonical_ascii_chunks(value))
    with p.EvidenceFiles(tmp_path/'shadow') as files:
        with c.Snapshot(files) as snapshot:
            role = snapshot.stage('source_rows',value['rows'])
            ref = snapshot.seal({'rows':role},source_as_of='original',configuration='fixed')
        value['rows'][0]['amount'] = '9000'
        assert c.resolve(files,ref)['state']=='PENDING'
        monkeypatch.setattr(sqlite3,'connect',lambda *a,**k:pytest.fail('COLD MUST NOT REQUERY DB'))
        receipt = c.drain(files)
        assert gzip.open(files.root/receipt['computed_report']['file'],'rb').read()==original
        assert receipt['source_as_of']=='original'
        assert receipt['computed_report']['sha256']==hashlib.sha256(original).hexdigest()
        assert c.resolve(files,ref)==receipt and c.drain(files)==receipt

def test_snapshot_is_durable_before_pointer_and_queue_blocks_replacement(tmp_path):
    with p.EvidenceFiles(tmp_path) as files:
        ref = seal(files)
        assert not (tmp_path/'CURRENT.json').exists()
        with c.Directory(files) as directory:
            manifest = c._manifest(directory,ref)
            with directory.open(manifest['pack']) as stream:
                assert c._hash(stream)==manifest['pack_sha256']
        with pytest.raises(ValueError,match='BACKPRESSURE'):
            seal(files,{'replacement':True})
        assert c.resolve(files,ref)['state']=='PENDING'

def test_recovery_manifest_fsync_before_queue_fsync(tmp_path):
    with p.EvidenceFiles(tmp_path) as files:
        ref = seal(files)
        with c.Directory(files) as directory:
            directory.remove('queue.json')
    with p.EvidenceFiles(tmp_path) as files:
        receipt = c.drain(files)
        assert receipt['state']=='COMPLETE' and c.resolve(files,ref)==receipt

def test_recovery_receipt_fsync_before_queue_and_cleanup_is_idempotent(tmp_path,monkeypatch):
    with p.EvidenceFiles(tmp_path) as files:
        ref = seal(files); receipt = c.drain(files)
        with c.Directory(files) as directory:
            directory.write('queue.json',{'snapshot_id':ref['snapshot_id'],'manifest_sha256':ref['manifest_sha256'],'state':'PENDING'})
        monkeypatch.setattr(c.time,'time',lambda:receipt['completed_at']+1000)
        assert c.drain(files)==receipt and c.resolve(files,ref)==receipt

def test_expired_original_input_is_failed_and_never_replaced_by_probe(tmp_path,monkeypatch):
    with p.EvidenceFiles(tmp_path) as files:
        ref = seal(files)
        monkeypatch.setattr(c.time,'time',lambda:ref['deadline_at']+1)
        with pytest.raises(ValueError,match='DEADLINE'):c.drain(files)
        with c.Directory(files) as directory:
            first = directory.read('receipt-'+ref['snapshot_id']+'.json')
            assert first['state']=='FAILED'
        with pytest.raises(ValueError,match='REVOKED'):c.resolve(files,ref)
        for action in (lambda:c.drain(files),lambda:seal(files)):
            with pytest.raises(ValueError,match='FIX_FORWARD'):action()
        with c.Directory(files) as directory:
            assert directory.read('receipt-'+ref['snapshot_id']+'.json')==first

@pytest.mark.parametrize('change',['missing','corrupt','replace','hardlink','symlink'])
def test_pending_snapshot_changed_or_aliased_revokes_and_cannot_complete(tmp_path,change):
    with p.EvidenceFiles(tmp_path/'shadow') as files:
        ref = seal(files); path = files.root/'cold-audit'/(ref['snapshot_id']+'.pack')
        if change=='missing':path.unlink()
        elif change=='corrupt':
            with path.open('r+b') as stream:stream.write(b'X')
        elif change=='replace':
            raw=path.read_bytes();path.unlink();path.write_bytes(raw);path.chmod(0o600)
        elif change=='hardlink':os.link(path,tmp_path/'alias')
        else:path.unlink();path.symlink_to(tmp_path/'outside')
        with pytest.raises((ValueError,OSError)):c.resolve(files,ref)
        with pytest.raises((ValueError,OSError)):c.drain(files)
        with c.Directory(files) as directory:
            assert directory.read('receipt-'+ref['snapshot_id']+'.json')['state']=='FAILED'

def test_corrupt_existing_content_addressed_output_cannot_attest_complete(tmp_path):
    value={'full':['same']*300}
    with p.EvidenceFiles(tmp_path) as files:
        old=files.write_streamed_report(value);files.path(old['file']).write_bytes(b'corrupt-existing-address')
        ref=seal(files,value)
        with pytest.raises((ValueError,OSError,EOFError)):c.drain(files)
        with pytest.raises(ValueError,match='REVOKED'):c.resolve(files,ref)

def test_completed_audit_later_mutation_revokes_without_renewing_source(tmp_path):
    with p.EvidenceFiles(tmp_path) as files:
        ref=seal(files);receipt=c.drain(files);path=files.path(receipt['computed_report']['file'])
        with path.open('r+b') as stream:stream.seek(10);stream.write(b'changed')
        with pytest.raises(ValueError,match='CHANGED_AFTER'):c.resolve(files,ref)

@pytest.mark.parametrize('field,value',[('epoch','unknown'),('source_as_of','new'),('configuration_fingerprint','other'),('manifest_sha256','0'*64),('snapshot_id','../outside')])
def test_reference_rebinding_is_rejected(tmp_path,field,value):
    with p.EvidenceFiles(tmp_path) as files:
        ref=seal(files)
        with pytest.raises(ValueError):c.resolve(files,{**ref,field:value})

def test_pack_quota_does_not_grow_with_literal_delimiters(tmp_path,monkeypatch):
    with p.EvidenceFiles(tmp_path) as files:
        with c.Snapshot(files) as snapshot:
            snapshot.hasher=hashlib.sha256();snapshot.segment_hash=hashlib.sha256()
            for _ in range(20000):snapshot.literal(',')
            snapshot._flush()
            assert snapshot.total_bytes==20000+c.FRAME.size+32
        monkeypatch.setattr(c,'PACK_LIMIT',200)
        with pytest.raises(ValueError,match='QUOTA'):seal(files,{'values':list(range(1000))})
        assert not list((tmp_path/'cold-audit').glob('*.pack'))

def test_reserve_failure_rejects_before_complete_and_keeps_original_snapshot(tmp_path,monkeypatch):
    with p.EvidenceFiles(tmp_path) as files:
        ref=seal(files)
        monkeypatch.setattr(c,'_guard',lambda *a,**k:(_ for _ in ()).throw(ValueError('HOST_MEMORY_RESERVE')))
        with pytest.raises(ValueError,match='HOST_MEMORY_RESERVE'):c.drain(files)
        assert (tmp_path/'cold-audit'/(ref['snapshot_id']+'.pack')).exists()
        with pytest.raises(ValueError,match='REVOKED'):c.resolve(files,ref)

def test_native_readonly_actual_values_are_authority(codec):
    value=p.FrozenJSONDict({'price':'1.25','actual':None,'empty':[]})
    object.__setattr__(value,'_canonical_json','FORGED')
    encoder,decoder=codec.snapshot_context(p.FrozenJSONDict,p._FrozenEmptyList),codec.snapshot_context()
    first=codec.snapshot_encode(value,encoder)
    assert codec.snapshot_decode(first,decoder)['price']=='1.25'
    dict.__setitem__(value,'price','2.50')
    second=codec.snapshot_encode(value,encoder)
    assert codec.snapshot_decode(second,decoder)['price']=='2.50'

@pytest.mark.parametrize('raw',[b'',b'X',b'r\x00',b'R\x10\x00',b'S\xff\xff\xff\xff',b'U\x00\x00\x00\x01\xff',b'I\x00\x00\x00\x02-0',b'I\x00\x00\x00\x0201',b'L\x00\x00\x01\x01',b'O\x00\x00\x00\x01NN',b'D'+struct.pack('!d',float('inf')),b'Ntrailing',b'S'+struct.pack('!I',131073)+b'a',b'PICKLE\x80\x05'])
def test_malformed_codec_input_cannot_allocate_unbounded_or_reuse_context(codec,raw):
    context=codec.snapshot_context()
    with pytest.raises((ValueError,UnicodeError,OverflowError)):codec.snapshot_decode(raw,context)
    with pytest.raises(ValueError,match='POISONED'):codec.snapshot_decode(b'N',context)

def test_codec_native_has_only_cpython_dependencies(codec):
    binary=codec.__file__
    assert 'NEEDED' not in subprocess.check_output(['readelf','-d',binary],text=True)
    symbols=subprocess.check_output(['readelf','--dyn-syms','--wide',binary],text=True)
    names=[row.split()[7] for row in symbols.splitlines() if len(row.split())>=8 and row.split()[6]=='UND']
    assert names and all(n.startswith(('Py','_Py')) for n in names)

def test_worker_deferred_full_sha_checkpoint_and_business_parity(tmp_path):
    from rc6_shadow_runtime.worker import ShadowRuntime
    from tests.test_rc6_shadow_runtime_wiring import make_store,PRE
    store,_=make_store(tmp_path,count=25)
    baseline=ShadowRuntime(store.path,evidence_root=tmp_path/'baseline',source_roots=[],spool_audit=True)
    candidate=ShadowRuntime(store.path,evidence_root=tmp_path/'candidate',source_roots=[],spool_audit=True,defer_audit=True)
    for at in (PRE,PRE+timedelta(seconds=30)):
        baseline.tick(at);candidate.tick(at)
        pending=p.read_committed_generation(candidate.root)
        assert pending['computed_audit']['state']=='PENDING' and pending['status']['computed_report_digest'] is None
        with candidate.files as files:completed=c.drain(files)
        old=p.read_committed_generation(baseline.root);new=p.read_committed_generation(candidate.root)
        assert completed['computed_report']==old['report']['computed_report']
        assert new['computed_audit']==completed
        assert gzip.open(baseline.root/completed['computed_report']['file'],'rb').read()==gzip.open(candidate.root/completed['computed_report']['file'],'rb').read()
        a,b=dict(old['report']),dict(new['report'])
        for key in ('generation_id','computed_report'):a.pop(key,None);b.pop(key,None)
        assert a==b
        with baseline.files as files:checkpoint_a=files.read('checkpoint.json.gz')
        with candidate.files as files:assert files.read('checkpoint.json.gz')==checkpoint_a


def test_recovery_new_manifest_before_queue_replaces_only_previous_complete(tmp_path):
    with p.EvidenceFiles(tmp_path) as files:
        first=seal(files,{'v':1});c.drain(files)
        with c.Directory(files) as directory:old_queue=directory.read('queue.json')
        second=seal(files,{'v':2})
        with c.Directory(files) as directory:directory.write('queue.json',old_queue)
        completed=c.drain(files)
        assert completed['snapshot_id']==second['snapshot_id']
        assert c.resolve(files,first)['computed_report']['sha256']!=completed['computed_report']['sha256']


def test_failure_survives_lost_queue_and_keeps_first_failure_receipt(tmp_path,monkeypatch):
    with p.EvidenceFiles(tmp_path) as files:
        ref=seal(files)
        monkeypatch.setattr(c.time,'time',lambda:ref['deadline_at']+1)
        with pytest.raises(ValueError):c.drain(files)
        with c.Directory(files) as directory:
            before=directory.read('receipt-'+ref['snapshot_id']+'.json');directory.remove('queue.json')
        with pytest.raises(ValueError,match='FIX_FORWARD'):seal(files)
        with c.Directory(files) as directory:
            assert directory.read('queue.json')['state']=='FAILED'
            assert directory.read('receipt-'+ref['snapshot_id']+'.json')==before


def test_unpublished_pack_is_reconciled_with_aborted_receipt_not_fake_complete(tmp_path):
    ident='a'*32
    with p.EvidenceFiles(tmp_path) as files:
        with c.Directory(files,create=True) as directory:
            with directory.open(ident+'.pack',create=True) as stream:stream.write(b'unpublished original bytes')
        assert c.drain(files) is None
        with c.Directory(files) as directory:
            receipt=directory.read('receipt-'+ident+'.json')
            assert receipt['state']=='ABORTED_UNPUBLISHED'
            assert receipt['pack_sha256']==hashlib.sha256(b'unpublished original bytes').hexdigest()
            assert not (directory.root/(ident+'.pack')).exists()


def test_manifest_corruption_records_durable_failure_before_rejecting_new_tick(tmp_path):
    with p.EvidenceFiles(tmp_path) as files:
        ref=seal(files)
        with c.Directory(files) as directory:
            manifest=directory.read('manifest-'+ref['snapshot_id']+'.json')
            directory.write('manifest-'+ref['snapshot_id']+'.json',{**manifest,'epoch':'unknown'})
        # The existing PENDING queue binds the original manifest hash, so
        # reconciliation detects the changed hash before epoch decoding.
        with pytest.raises(ValueError,match='QUEUE_CONFLICT'):c.drain(files)
        with c.Directory(files) as directory:
            assert directory.read('queue.json')['state']=='FAILED'
            assert directory.read('receipt-'+ref['snapshot_id']+'.json')['state']=='FAILED'


def test_native_random_primitive_roundtrips_and_memo_budget(codec):
    import random
    rng=random.Random(624)
    atoms=[None,True,False,0,-1,2**100,1.25,-0.0,'','á漢字😀','\\\"\n\t\x00','\ud800']
    def value(depth=0):
        if depth>=4 or rng.randrange(3)==0:return rng.choice(atoms)
        count=rng.randrange(8)
        return [value(depth+1) for _ in range(count)] if rng.randrange(2) else {str(i):value(depth+1) for i in range(count)}
    encoder,decoder=codec.snapshot_context(),codec.snapshot_context()
    for _ in range(2000):
        actual=value();raw=codec.snapshot_encode(actual,encoder)
        assert raw is not None
        assert c.ENCODER.encode(codec.snapshot_decode(raw,decoder))==c.ENCODER.encode(actual)
    # Exhaust the bounded memo and continue via literal strings; neither
    # admission nor decoding may grow its memo beyond4096/256KiB.
    for n in range(5000):
        actual='unique_'+str(n)+'x'*60;raw=codec.snapshot_encode(actual,encoder)
        assert codec.snapshot_decode(raw,decoder)==actual


def test_existing_worker_consumes_cold_inside_poll_period_and_failure_waits(tmp_path,monkeypatch):
    from rc6_shadow_runtime import worker as runtime
    for failed in (False,True):
        clock=[100.0];calls=[];waits=[]
        class Worker:
            defer_audit=True
            pointer_committed_monotonic=0.0
            files=p.EvidenceFiles(tmp_path/str(failed))
            last_sqlite_diagnostics={}
            def tick(self,_at):
                self.pointer_committed_monotonic=clock[0]
                return {'status':'SHADOW_OBSERVING','phase':'CLOSED'}
        worker=Worker()
        def factory(*args,**kwargs):
            assert kwargs['defer_audit'] is True and kwargs['spool_audit'] is True
            return worker
        def drain(files):
            calls.append(1)
            if failed:raise ValueError('known failed backlog')
            if len(calls)==2:clock[0]+=12
        class Stop:
            def is_set(self):return bool(waits)
            def wait(self,seconds):waits.append(seconds)
        monkeypatch.setattr(runtime,'ShadowRuntime',factory)
        monkeypatch.setattr(runtime.os,'nice',lambda _:None)
        monkeypatch.setattr(runtime.time,'monotonic',lambda:clock[0])
        monkeypatch.setattr(runtime.cold_audit,'drain',drain)
        monkeypatch.setattr(runtime.cold_audit,'prune_completed',lambda _:None)
        monkeypatch.setattr(runtime,'release_idle_heap',lambda:{})
        runtime.run_worker(tmp_path/'paper.db',Stop(),clock_fn=lambda:'2026-10-05T13:20:00+00:00')
        assert waits==([30] if failed else [18])
        assert len(calls)==(1 if failed else 2)


@pytest.mark.parametrize('case',['unknown_reference','unknown_contract','missing_contract'])
def test_reader_cannot_downgrade_unknown_or_missing_cold_contract(tmp_path,case):
    with p.EvidenceFiles(tmp_path) as files:
        ref=seal(files)
        reference=dict(ref)
        report={'as_of':ref['source_as_of'],'status':'SHADOW_OBSERVING',
            'real_orders_sent':0,'real_routes':'NOT_CALLED','computed_report':reference}
        status={'as_of':report['as_of'],'status':report['status'],
            'configuration_fingerprint':'fixed','real_orders_sent':0,'real_routes':'NOT_CALLED',
            'computed_report_digest':None,'computed_audit_contract':'rc6.computed-audit-reference.v2'}
        if case=='unknown_reference':reference['schema']='unknown-v3'
        elif case=='unknown_contract':status['computed_audit_contract']='unknown-v3'
        else:status.pop('computed_audit_contract')
        status['report_digest']=p.digest(report)
        files.write('status.json',status);files.publish_generation(report,status)
        with pytest.raises(ValueError,match='CONTRACT_UNVERIFIED'):p.read_committed_generation(tmp_path)


def test_sealed_snapshot_rejects_all_further_capture(tmp_path):
    with p.EvidenceFiles(tmp_path) as files:
        with c.Snapshot(files) as snapshot:
            snapshot.seal({'value':1},source_as_of='original',configuration='fixed')
            with pytest.raises(ValueError,match='ALREADY_SEALED'):
                snapshot.stage('late',{'value':2})


def test_mutable_capture_has_no_path_and_seal_links_same_inode(tmp_path):
    with p.EvidenceFiles(tmp_path) as files:
        with c.Snapshot(files) as snapshot:
            before=os.fstat(snapshot.stream.fileno())
            assert before.st_nlink==0
            first=snapshot.stage('first',{'original':[1,2,3]})
            second=snapshot.stage('second',{'before':first,'after':'exact'})
            assert b''.join(second.iter_bytes())==b'{"after":"exact","before":{"original":[1,2,3]}}'
            assert not list((tmp_path/'cold-audit').iterdir())
            ref=snapshot.seal({'whole':second},source_as_of='original',configuration='fixed')
            sealed=(tmp_path/'cold-audit'/(ref['snapshot_id']+'.pack')).stat()
            assert (sealed.st_dev,sealed.st_ino)==(before.st_dev,before.st_ino)
            assert sealed.st_nlink==1
        assert c.drain(files)['state']=='COMPLETE'


def test_atomic_seal_cannot_overwrite_an_existing_file(tmp_path):
    with p.EvidenceFiles(tmp_path) as files:
        with c.Snapshot(files) as snapshot:
            target=tmp_path/'cold-audit'/snapshot.pack
            target.write_bytes(b'preexisting must remain');target.chmod(0o600)
            with pytest.raises(FileExistsError):
                snapshot.seal({'value':1},source_as_of='original',configuration='fixed')
            assert target.read_bytes()==b'preexisting must remain'
            assert not (tmp_path/'CURRENT.json').exists()


def test_foreign_partial_copy_is_rejected_before_seal_not_allowlisted(tmp_path):
    with p.EvidenceFiles(tmp_path) as files:
        with c.Snapshot(files) as snapshot:
            foreign=tmp_path/'cold-audit'/('.capture-'+snapshot.ident+'.tmp.foreign')
            foreign.write_bytes(b'foreign partial copy');foreign.chmod(0o600)
            with pytest.raises(ValueError,match='BASENAME_REQUIRED'):
                snapshot.seal({'value':1},source_as_of='original',configuration='fixed')
            assert foreign.read_bytes()==b'foreign partial copy'
            assert not (tmp_path/'CURRENT.json').exists()


def test_anonymous_link_capability_for_current_uid_without_capabilities():
    import shutil,sys,tempfile
    folder=tempfile.mkdtemp(prefix='rc6-anonymous-link-',dir='/tmp')
    # Local Work has a root-only UID map. The SAME case runs as1000 in
    # mandatory exact-image native gates; do not claim that UID locally.
    target_uid=os.getuid()
    try:
        os.chmod(folder,0o700)
        script='''import os,sys
root=sys.argv[1];expected=int(sys.argv[2]);assert os.getuid()==expected
cap=[x.split()[1] for x in open('/proc/self/status') if x.startswith('CapEff:')][0]
assert int(cap,16)==0,cap
d=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
f=os.open('.',os.O_RDWR|os.O_TMPFILE,0o600,dir_fd=d)
os.write(f,b'original values');os.fsync(f);before=os.fstat(f);assert before.st_nlink==0
os.link('/proc/self/fd/'+str(f),'sealed.pack',dst_dir_fd=d,follow_symlinks=True)
os.fsync(d);after=os.stat('sealed.pack',dir_fd=d,follow_symlinks=False)
assert (before.st_dev,before.st_ino)==(after.st_dev,after.st_ino) and after.st_nlink==1
os.close(f);os.close(d)
assert open(root+'/sealed.pack','rb').read()==b'original values'
print('ANONYMOUS_SAME_INODE_CURRENT_UID_CAP0_GREEN')
'''
        run=subprocess.run([sys.executable,'-c',script,folder,str(target_uid)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,text=True,timeout=20)
        assert run.returncode==0,run.stdout
        assert 'CURRENT_UID_CAP0_GREEN' in run.stdout
    finally:
        shutil.rmtree(folder)


def publish_reference(files,reference):
    report={'as_of':reference['source_as_of'],'status':'SHADOW_OBSERVING',
        'real_orders_sent':0,'real_routes':'NOT_CALLED','computed_report':reference}
    status={'as_of':report['as_of'],'status':report['status'],'configuration_fingerprint':'fixed',
        'real_orders_sent':0,'real_routes':'NOT_CALLED','report_digest':p.digest(report),
        'computed_report_digest':None,'computed_audit_contract':'rc6.computed-audit-reference.v2'}
    files.write('status.json',status);files.publish_generation(report,status)


def test_orderly_stop_cancels_only_unsealed_input_and_preserves_prior_complete(tmp_path):
    with p.EvidenceFiles(tmp_path) as files:
        original=seal(files);c.drain(files);publish_reference(files,original)
        with c.Snapshot(files,should_abort=lambda:True) as snapshot:
            with pytest.raises(ValueError,match='CAPTURE_CANCELLED'):
                snapshot.seal({'new':1},source_as_of='later',configuration='fixed')
        assert p.read_committed_generation(tmp_path)['computed_audit']['state']=='COMPLETE'
        with c.Directory(files) as directory:
            assert directory.read('queue.json')['snapshot_id']==original['snapshot_id']


def test_sealed_input_still_drains_after_stop_request(tmp_path):
    stopped=[False]
    with p.EvidenceFiles(tmp_path) as files:
        with c.Snapshot(files,should_abort=lambda:stopped[0]) as snapshot:
            ref=snapshot.seal({'v':1},source_as_of='original',configuration='fixed')
            stopped[0]=True
        assert c.drain(files)['state']=='COMPLETE'
        assert c.resolve(files,ref)['source_as_of']=='original'


def test_parent_sends_all_signals_first_and_grants_only_shadow_drain_grace():
    import bv_paper_runtime as runtime
    calls=[]
    class Child:
        def __init__(self,name):self.name=name
        def poll(self):return None
        def terminate(self):calls.append(('term',self.name))
        def wait(self,timeout):calls.append(('wait',self.name,timeout));return 0
        def kill(self):pytest.fail('bounded healthy drain was killed')
    parent=runtime.ChildProcesses({'scanner':[],'dynamic_shadow':[],'reader':[]})
    parent.processes={name:Child(name) for name in parent.commands}
    parent.close()
    assert calls[:3]==[('term','scanner'),('term','dynamic_shadow'),('term','reader')]
    assert calls[3:]==[('wait','scanner',5),('wait','dynamic_shadow',35),('wait','reader',5)]


def test_stress_requires_real_complete_cut_and_empty_pending_queue(tmp_path):
    from scripts.rc6_postdeploy_stress_host import stopped_shadow_audit
    with p.EvidenceFiles(tmp_path) as files:
        ref=seal(files);publish_reference(files,ref)
        with pytest.raises(ValueError,match='AUDIT_NOT_DRAINED'):stopped_shadow_audit(tmp_path)
        completed=c.drain(files)
        # A planned stop may have revoked live status after cancelling an
        # uncommitted next tick. Historical inspection cannot claim live health.
        files.write('status.json',{'status':'FAIL_CLOSED'})
        proof=stopped_shadow_audit(tmp_path)
        assert proof['computed_report']==completed['computed_report'] and proof['historical_after_stop']
        with c.Directory(files) as directory:
            queue=directory.read('queue.json');directory.write('queue.json',{**queue,'state':'PENDING'})
        with pytest.raises(ValueError,match='BACKLOG_NOT_DRAINED'):stopped_shadow_audit(tmp_path)


@pytest.mark.parametrize('reader_uid,write,allowed',[(0,False,True),(1000,False,True),(1001,False,False),(0,True,False),(1000,True,True)])
def test_checked_directory_owner_policy_root_read_only(tmp_path,monkeypatch,reader_uid,write,allowed):
    from types import SimpleNamespace
    with p.EvidenceFiles(tmp_path) as files:
        (tmp_path/'cold-audit').mkdir(mode=0o700)
        real_fstat=os.fstat
        def mapped(fd):
            s=real_fstat(fd)
            values={k:getattr(s,k) for k in dir(s) if k.startswith('st_')}
            values['st_uid']=1000
            return SimpleNamespace(**values)
        monkeypatch.setattr(c.os,'fstat',mapped)
        monkeypatch.setattr(c.os,'getuid',lambda:reader_uid)
        if allowed:
            with c.Directory(files,create=write) as directory:assert directory.owner_uid==1000
        else:
            with pytest.raises(ValueError,match='PRIVATE_DIRECTORY'):c.Directory(files,create=write)


def test_input_mutated_during_cold_cannot_publish_complete(tmp_path,monkeypatch):
    with p.EvidenceFiles(tmp_path) as files:
        ref=seal(files);write=files.write_streamed_report
        def mutate_after_materialization(payload):
            record=write(payload)
            with (tmp_path/'cold-audit'/(ref['snapshot_id']+'.pack')).open('ab') as stream:
                stream.write(b'changed after initial hash')
            return record
        monkeypatch.setattr(files,'write_streamed_report',mutate_after_materialization)
        with pytest.raises(ValueError,match='SEALED_INPUT_CHANGED'):c.drain(files)
        with c.Directory(files) as directory:
            assert directory.read('receipt-'+ref['snapshot_id']+'.json')['state']=='FAILED'


def test_valid_hash_does_not_allow_nondictionary_queue_payload(tmp_path):
    with p.EvidenceFiles(tmp_path) as files:
        with c.Directory(files,create=True) as directory:
            directory.write('queue.json',[])
            with pytest.raises(ValueError,match='MANIFEST_HASH'):directory.read('queue.json')


def test_native_prefix_batch_preserves_actual_values_and_unchanged_json_bounds(codec):
    rows=[{'id':n,'payload':'x'*14000} for n in range(16)]
    encoder,decoder=codec.snapshot_context(),codec.snapshot_context()
    raw,count=codec.snapshot_batch(rows,encoder)
    assert count==4
    actual=codec.snapshot_decode(raw,decoder)
    assert actual==rows[:count] and len(c.ENCODER.encode(actual).encode('ascii'))<=65536
    second,more=codec.snapshot_batch(rows[count:],encoder)
    assert more==4 and codec.snapshot_decode(second,decoder)==rows[count:count+more]


def test_encoded_string_memo_never_expands_batch_canonical_budget(codec):
    rows=['x'*20000]*16
    encoder,decoder=codec.snapshot_context(),codec.snapshot_context()
    first,count=codec.snapshot_batch(rows,encoder)
    second,again=codec.snapshot_batch(rows,encoder)
    assert count==again==3
    assert len(second)<len(first)
    assert codec.snapshot_decode(first,decoder)==codec.snapshot_decode(second,decoder)==rows[:3]


def test_unfitting_batch_item_does_not_mutate_string_memo(codec):
    encoder,decoder=codec.snapshot_context(),codec.snapshot_context()
    raw,count=codec.snapshot_batch([{'stable':'original'},{'new':object()}],encoder)
    assert count==1 and codec.snapshot_decode(raw,decoder)==[{'stable':'original'}]
    after=codec.snapshot_encode({'new':7},encoder)
    assert codec.snapshot_decode(after,decoder)=={'new':7}
    assert codec.snapshot_batch([{'not_inserted':object()}],encoder) is None
    final=codec.snapshot_encode({'not_inserted':8},encoder)
    assert codec.snapshot_decode(final,decoder)=={'not_inserted':8}


def test_native_batch_bounds_and_nonfinite_error_poison_partial_context(codec):
    assert codec.snapshot_batch([],codec.snapshot_context()) is None
    with pytest.raises(ValueError,match='BATCH_BOUND'):codec.snapshot_batch(list(range(17)),codec.snapshot_context())
    with pytest.raises(ValueError,match='BATCH_SEQUENCE'):codec.snapshot_batch({'not':'sequence'},codec.snapshot_context())
    context=codec.snapshot_context()
    with pytest.raises(ValueError,match='NONFINITE'):codec.snapshot_batch([{'already':'captured'},float('nan')],context)
    with pytest.raises(ValueError,match='POISONED'):codec.snapshot_encode(None,context)


def test_batch_oversized_first_item_uses_exact_recursive_fallback(tmp_path):
    value=[{'huge':'x'*100000},*({'small':str(n)} for n in range(400))]
    original=b''.join(p.canonical_ascii_chunks(value))
    with p.EvidenceFiles(tmp_path) as files:
        ref=seal(files,value);result=c.drain(files)
        assert gzip.open(files.root/result['computed_report']['file'],'rb').read()==original
        assert c.resolve(files,ref)==result


def test_zero_progress_batch_cannot_loop_or_publish(tmp_path):
    from types import SimpleNamespace
    with p.EvidenceFiles(tmp_path) as files:
        with c.Snapshot(files) as snapshot:
            native=snapshot.codec
            snapshot.codec=SimpleNamespace(snapshot_context=native.snapshot_context,
                snapshot_encode=native.snapshot_encode,snapshot_batch=lambda *args:(b'not consumed',0))
            with pytest.raises(ValueError,match='PROGRESS_REQUIRED'):
                snapshot.stage('rows',list(range(400)))
        assert not (tmp_path/'CURRENT.json').exists()


def test_large_rows_use_bounded_prefix_frames_instead_of_rescanned_singletons(tmp_path,monkeypatch):
    from collections import Counter
    rows=[{'id':n,'payload':'x'*14000} for n in range(400)]
    counts=Counter()
    with p.EvidenceFiles(tmp_path) as files:
        with c.Snapshot(files) as snapshot:
            frame=snapshot._frame
            def counted(kind,raw):counts[kind]+=1;return frame(kind,raw)
            monkeypatch.setattr(snapshot,'_frame',counted)
            captured=snapshot.stage('large_rows',rows)
            actual=hashlib.sha256()
            for block in captured.iter_bytes():actual.update(block)
            assert actual.hexdigest()==p.streamed_digest(rows)
            # Same64KiB budget accepts4 actual14KiB rows. The rejected fixed16
            # attempt used400 singletons; the bounded prefix path emits100
            # batches while preserving every canonical byte and budget.
            assert counts[b'A']==100 and counts[b'P']==0,dict(counts)


def test_storage_capture_has_one_actual_value_walk(codec):
    rows=[{'amount':str(n),'source':'PPI','ready':bool(n%2)} for n in range(16)]
    context=codec.snapshot_context(p.FrozenJSONDict,p._FrozenEmptyList,True)
    raw=codec.snapshot_encode({'rows':rows},context)
    bounds,encoded=codec._snapshot_capture_counts(context)
    assert codec.snapshot_decode(raw,codec.snapshot_context())=={'rows':rows}
    assert bounds==0 and encoded>0
    context=codec.snapshot_context(p.FrozenJSONDict,p._FrozenEmptyList,True)
    raw,count=codec.snapshot_batch(rows,context)
    bounds,encoded=codec._snapshot_capture_counts(context)
    assert bounds==0 and encoded>0 and count==16
    assert codec.snapshot_decode(raw,codec.snapshot_context())==rows


def test_soft_nonfit_restores_memo_after_encoding_actual_partial_values(codec):
    encoder,decoder=codec.snapshot_context(),codec.snapshot_context()
    first=codec.snapshot_encode({'retained':'value'},encoder)
    assert codec.snapshot_decode(first,decoder)=={'retained':'value'}
    for n in range(200):
        # Keys and early strings enter the tentative memo before the late
        # unsupported value. None must leave no ghost reference IDs behind.
        assert codec.snapshot_encode({'partial_'+str(n):['new_'+str(n),object()]},encoder) is None
        value={'accepted_'+str(n):'real_'+str(n)}
        raw=codec.snapshot_encode(value,encoder)
        assert codec.snapshot_decode(raw,decoder)==value


def test_batch_nonfit_restores_only_failed_item_and_keeps_accepted_prefix(codec):
    first={'retained':'a'*50000}
    second={'temporary':'new','payload':'b'*20000}
    encoder,decoder=codec.snapshot_context(),codec.snapshot_context()
    raw,count=codec.snapshot_batch([first,second],encoder)
    assert count==1 and codec.snapshot_decode(raw,decoder)==[first]
    after=codec.snapshot_encode(second,encoder)
    assert codec.snapshot_decode(after,decoder)==second


def test_capture_fit_matches_original_bounds_at_byte_node_and_width_edges(codec):
    cases=['x'*n for n in (0,65532,65533,65534,65535,65536)]
    cases+=['😀'*n for n in (5460,5461,5462)]
    cases += [[None]*n for n in (0,255,256,257)]
    cases += [{str(n):None for n in range(width)} for width in (255,256,257)]
    full={str(n):[None]*255 for n in range(16)}
    near={**full,'15':[None]*254}
    cases += [full,near]
    for value in cases:
        admitted=p._small_json_tree_python(value)
        raw=codec.snapshot_encode(value,codec.snapshot_context())
        assert (raw is not None)==admitted
        if raw is not None:
            assert c.ENCODER.encode(codec.snapshot_decode(raw,codec.snapshot_context()))==c.ENCODER.encode(value)


def test_deferred_hot_profile_never_contains_previous_cold_encode_cost(tmp_path):
    from rc6_shadow_runtime.worker import ShadowRuntime
    from tests.test_rc6_shadow_runtime_wiring import make_store,PRE
    store,_=make_store(tmp_path,count=25)
    worker=ShadowRuntime(store.path,evidence_root=tmp_path/'shadow',source_roots=[],spool_audit=True,defer_audit=True)
    worker.tick(PRE)
    with worker.files as files:
        first=c.drain(files)
    assert first['state']=='COMPLETE' and worker.files.report_write_profile['encode_cpu_seconds']>=0
    worker.tick(PRE+timedelta(seconds=30))
    cut=p.read_committed_generation(worker.root)
    hot=cut['status']['generation_timing']['storage_profile']['computed']
    assert hot['cpu_seconds']>=0 and hot['wall_seconds']>=0
    assert not {'encode_cpu_seconds','encode_seconds','compress_hash_cpu_seconds','compress_hash_seconds',
                'fsync_cpu_seconds','fsync_seconds','raw_bytes','compressed_bytes'} & hot.keys()
    with worker.files as files:
        second=c.drain(files)
    assert second['state']=='COMPLETE' and second['source_as_of']==(PRE+timedelta(seconds=30)).isoformat()
