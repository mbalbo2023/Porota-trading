"""Exact-ID COLD2 archive crash and adversarial fixture, never a host action."""
import gzip
import hashlib
import json
import os
import io
import builtins
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts import rc6_cold2_quarantine as q

EXPECTED = {'porota_production_observer':'a'*64,
            'porota_production_dashboard':'b'*64}


def put(path, payload, *, cold=False, zipped=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw=json.dumps({'payload':payload,('sha256' if cold else 'digest'):q.digest(payload)},
                   sort_keys=True,separators=(',',':')).encode()
    if zipped:
        with gzip.open(path,'wb') as stream:stream.write(raw)
    else:path.write_bytes(raw)
    path.chmod(0o600)


@pytest.fixture
def state(tmp_path,monkeypatch):
    root=tmp_path/'host';root.mkdir(mode=0o700)
    shadow=root/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'
    source=shadow/'cold-audit';source.mkdir(parents=True,mode=0o700)
    shadow.chmod(0o700)
    deploy=root/'data/deploy';deploy.mkdir(parents=True,mode=0o700)
    raw=b'exact failed original input bytes\x00\xff'
    monkeypatch.setattr(q,'PACK_BYTES',len(raw))
    monkeypatch.setattr(q,'PACK_SHA',hashlib.sha256(raw).hexdigest())
    pack=source/q.PACK;pack.write_bytes(raw);pack.chmod(0o600)
    st=pack.stat()
    manifest={'snapshot_id':q.IDENT,'pack':q.PACK,'pack_bytes':len(raw),
              'pack_sha256':q.PACK_SHA,'pack_identity':[st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns],
              'sealed_at':100.0,'deadline_at':130.0}
    monkeypatch.setattr(q,'MANIFEST_SHA',q.digest(manifest))
    put(source/q.MANIFEST,manifest,cold=True)
    put(source/q.RECEIPT,{'snapshot_id':q.IDENT,'manifest_sha256':q.MANIFEST_SHA,
        'state':'FAILED','error_class':'ValueError','failed_at':102.28},cold=True)
    put(source/q.QUEUE,{'snapshot_id':q.IDENT,'manifest_sha256':q.MANIFEST_SHA,'state':'FAILED'},cold=True)
    monkeypatch.setattr(q,'ORIGINAL_SHA',{name:q.sha(source/name) for name in q.FILES})
    for name in q.OTHER:
        ident=name.split('-')[1].split('.')[0]
        put(source/name,{'snapshot_id':ident,'state':'COMPLETE'},cold=True)
    put(shadow/'CURRENT.json',{'generation_id':q.CUT,'file':'cut-'+q.CUT+'.json.gz'})
    put(shadow/('cut-'+q.CUT+'.json.gz'),{'report':{'computed_report':{'snapshot_id':q.IDENT,'state':'PENDING'}},
        'status':{'real_orders_sent':0,'real_routes':'NOT_CALLED'}},zipped=True)
    put(shadow/'status.json',{'status':'FAIL_CLOSED','real_orders_sent':0})
    return root,source,deploy/('rc6-failed-cold-'+q.IDENT),raw


def admitted():
    return {'status':'DRAINED','owned_stopped':True,'market_closed':True,
            'no_positions':True,'lock_held':True,'ppi_watch_accessed':False,'real_orders_sent':0,
            'mode':'PRODUCTION_PAPER','real_routes':'NOT_CALLED',
            'order_routing_blocked':True,'inflight_absent':True,
            'critical_healthy':True,'owned_ids':EXPECTED.copy()}


def test_closed_pending_paper_caucion_requires_exact_cold2_admission(state):
    root,*_=state
    carry={'open_count':1,'maturity_at':'2026-10-23T17:00:00-03:00',
           'allocation_reconciled':True,'sha256':'a'*64,
           'audit_source_sha256':
               'b1d711899a5c85f4efc72e2632a9d81cdddf0a6dc71dee82d5c42923b5639865'}
    exact={**admitted(),'no_positions':False,'pending_caucion':True,
           'market_open_suspended':False,'open_paper_carryover':False,
           'intraday_caucion':False,'spot_carryover':None,'caucion_carryover':carry}
    q._admit(root,lambda:exact,EXPECTED)
    for changed in ({'no_positions':True},{'market_closed':False},
                    {'pending_caucion':False},{'caucion_carryover':{**carry,'sha256':'0'}},
                    {'caucion_carryover':{**carry,'audit_source_sha256':'0'*64}}):
        with pytest.raises(ValueError,match='COLD2_QUIESCENCE_REQUIRED'):
            q._admit(root,lambda x=changed:{**exact,**x},EXPECTED)


def test_plan_then_archive_preserves_original_bytes_and_revocation(state):
    root,source,archive,raw=state
    originals={name:(source/name).read_bytes() for name in q.FILES}
    ino=(source/q.PACK).stat().st_ino
    before=q.inspect(root)
    assert before['queue_failed'] and not archive.exists()
    result=q.quarantine(root,admission=admitted,expected_owned_ids=EXPECTED)
    assert result['state']=='QUARANTINED_FAILED' and result['old_cut_revoked']
    assert (archive/q.PACK).read_bytes()==raw and (archive/q.PACK).stat().st_ino==ino
    assert {name:(archive/name).read_bytes() for name in q.FILES}==originals
    assert {p.name for p in source.iterdir()}==q.OTHER
    assert q.inspect(root)['archive_complete'] and not q.inspect(root)['queue_failed']
    assert q.envelope(root/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow/status.json')['status']=='FAIL_CLOSED'
    assert q.envelope(root/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'/('cut-'+q.CUT+'.json.gz'),zipped=True)['report']['computed_report']['state']=='PENDING'
    assert q.quarantine(root,admission=admitted,expected_owned_ids=EXPECTED)['state']=='ALREADY_QUARANTINED'


def test_existing_worker_backpressure_is_removed_only_after_durable_archive(state):
    from rc6_shadow_runtime import cold_audit
    from rc6_shadow_runtime.persistence import EvidenceFiles
    root,source,archive,_=state
    shadow=source.parent
    with EvidenceFiles(shadow) as files:
        with pytest.raises(ValueError,match='SHADOW_COLD_FAILED_BACKLOG_REQUIRES_FIX_FORWARD'):
            cold_audit.ensure_idle(files)
    assert (source/q.QUEUE).exists()
    q.quarantine(root,admission=admitted,expected_owned_ids=EXPECTED)
    with EvidenceFiles(shadow) as files:
        assert cold_audit.ensure_idle(files) is None
    assert q.envelope(shadow/'status.json')['status']=='FAIL_CLOSED'
    assert q.envelope(archive/q.RECEIPT,cold=True)['state']=='FAILED'


@pytest.mark.parametrize('stage',['archive_'+name for name in q.FILES]+[
    'pack_moved','source_'+q.MANIFEST+'_removed','source_'+q.RECEIPT+'_removed',
    'receipt_durable','queue_removed'])
def test_every_crash_boundary_resumes_without_unblocking_early(state,stage):
    root,source,archive,raw=state
    def crash(step):
        if step==stage:raise RuntimeError('simulated crash')
    with pytest.raises(RuntimeError,match='simulated crash'):
        q.quarantine(root,admission=admitted,expected_owned_ids=EXPECTED,checkpoint=crash)
    intermediate=q.inspect(root)
    if stage!='queue_removed':
        assert intermediate['queue_failed'] and (source/q.QUEUE).exists()
    else:assert intermediate['archive_complete']
    assert q.quarantine(root,admission=admitted,expected_owned_ids=EXPECTED)['old_cut_revoked']
    assert (archive/q.PACK).read_bytes()==raw
    assert q.envelope(archive/q.RECEIPT,cold=True)['state']=='FAILED'


@pytest.mark.parametrize('change',['pack','receipt','alias','current','status','admission'])
def test_preflight_rejects_divergence_without_archive(state,change):
    root,source,archive,_=state
    shadow=root/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'
    if change=='pack':
        with (source/q.PACK).open('r+b') as stream:stream.write(b'X')
    elif change=='receipt':put(source/q.RECEIPT,{'state':'COMPLETE'},cold=True)
    elif change=='alias':
        (source/q.PACK).unlink();(source/q.PACK).symlink_to(root/'other')
    elif change=='current':put(shadow/'CURRENT.json',{'generation_id':'0'*64,'file':'cut-'+'0'*64+'.json.gz'})
    elif change=='status':put(shadow/'status.json',{'status':'HEALTHY','real_orders_sent':0})
    admission=(lambda:{**admitted(),'real_orders_sent':1}) if change=='admission' else admitted
    with pytest.raises((ValueError,FileNotFoundError)):
        q.quarantine(root,admission=admission,expected_owned_ids=EXPECTED)
    assert not archive.exists()
    assert (source/q.QUEUE).exists()


def test_market_window_change_after_durable_receipt_keeps_failed_queue(state):
    root,source,archive,_=state
    gate={'open':False}
    def admission():return {**admitted(),'market_closed':not gate['open']}
    def checkpoint(stage):
        if stage=='receipt_durable':gate['open']=True
    with pytest.raises(ValueError,match='COLD2_QUIESCENCE_REQUIRED'):
        q.quarantine(root,admission=admission,expected_owned_ids=EXPECTED,checkpoint=checkpoint)
    assert (source/q.QUEUE).exists()
    assert q.inspect(root)['queue_failed']
    gate['open']=False
    assert q.quarantine(root,admission=admission,expected_owned_ids=EXPECTED)['old_cut_revoked']


@pytest.mark.parametrize('key,value',[
    ('mode','PRODUCTION_LIVE'), ('real_routes','CALLED'),
    ('order_routing_blocked',False), ('inflight_absent',False),
    ('critical_healthy',False), ('owned_ids',{'porota_production_observer':'a'*64}),
    ('owned_stopped',False), ('market_closed',False), ('no_positions',False),
    ('lock_held',False), ('ppi_watch_accessed',True), ('real_orders_sent',True),
])
def test_exact_live_admission_rejects_every_missing_safety_fact(state,key,value):
    root,source,archive,_=state
    gate=lambda:{**admitted(),key:value}
    with pytest.raises(ValueError,match='COLD2_QUIESCENCE_REQUIRED'):
        q.quarantine(root,admission=gate,expected_owned_ids=EXPECTED)
    assert not archive.exists() and (source/q.QUEUE).exists()


def test_expected_ids_are_mandatory_and_bound_to_exact_containers(state):
    root,source,archive,_=state
    with pytest.raises(ValueError,match='COLD2_EXPECTED_OWNED_IDS_REQUIRED'):
        q.quarantine(root,admission=admitted,expected_owned_ids={})
    with pytest.raises(ValueError,match='COLD2_QUIESCENCE_REQUIRED'):
        q.quarantine(root,admission=admitted,expected_owned_ids={**EXPECTED,
            'porota_production_dashboard':'c'*64})
    assert not archive.exists() and (source/q.QUEUE).exists()


@pytest.mark.parametrize('resource',['disk','inodes','memory'])
def test_resource_reserve_blocks_before_archive(state,monkeypatch,resource):
    root,source,archive,_=state
    if resource=='disk':
        monkeypatch.setattr(q.shutil,'disk_usage',lambda path:SimpleNamespace(free=q.RESERVE))
        code='COLD2_DISK_RESERVE_BREACHED'
    elif resource=='inodes':
        original_statvfs=q.os.statvfs
        def low_inodes(path):
            st=original_statvfs(path)
            return SimpleNamespace(f_favail=15,f_bavail=st.f_bavail,
                f_bfree=st.f_bfree,f_frsize=st.f_frsize,f_blocks=st.f_blocks)
        monkeypatch.setattr(q.os,'statvfs',low_inodes)
        code='COLD2_INODE_RESERVE_BREACHED'
    else:
        original_open=builtins.open
        def low_mem(path,*args,**kwargs):
            if str(path)=='/proc/meminfo':return io.StringIO('MemAvailable: 131072 kB\n')
            return original_open(path,*args,**kwargs)
        monkeypatch.setattr(builtins,'open',low_mem)
        code='COLD2_MEMORY_RESERVE_BREACHED'
    with pytest.raises(ValueError,match=code):
        q.quarantine(root,admission=admitted,expected_owned_ids=EXPECTED)
    assert not archive.exists() and (source/q.QUEUE).exists()
