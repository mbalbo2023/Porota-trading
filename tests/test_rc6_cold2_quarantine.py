"""Exact-ID COLD2 archive crash and adversarial fixture, never a host action."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import pytest
import rc6_cold2_quarantine as q


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
            'no_positions':True,'lock_held':True,'ppi_watch_accessed':False,'real_orders_sent':0}


def test_plan_then_archive_preserves_original_bytes_and_revocation(state):
    root,source,archive,raw=state
    originals={name:(source/name).read_bytes() for name in q.FILES}
    ino=(source/q.PACK).stat().st_ino
    before=q.inspect(root)
    assert before['queue_failed'] and not archive.exists()
    result=q.quarantine(root,admission=admitted)
    assert result['state']=='QUARANTINED_FAILED' and result['old_cut_revoked']
    assert (archive/q.PACK).read_bytes()==raw and (archive/q.PACK).stat().st_ino==ino
    assert {name:(archive/name).read_bytes() for name in q.FILES}==originals
    assert {p.name for p in source.iterdir()}==q.OTHER
    assert q.inspect(root)['archive_complete'] and not q.inspect(root)['queue_failed']
    assert q.envelope(root/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow/status.json')['status']=='FAIL_CLOSED'
    assert q.envelope(root/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'/('cut-'+q.CUT+'.json.gz'),zipped=True)['report']['computed_report']['state']=='PENDING'
    assert q.quarantine(root,admission=admitted)['state']=='ALREADY_QUARANTINED'


@pytest.mark.parametrize('stage',['archive_'+name for name in q.FILES]+[
    'pack_moved','source_'+q.MANIFEST+'_removed','source_'+q.RECEIPT+'_removed',
    'receipt_durable','queue_removed'])
def test_every_crash_boundary_resumes_without_unblocking_early(state,stage):
    root,source,archive,raw=state
    def crash(step):
        if step==stage:raise RuntimeError('simulated crash')
    with pytest.raises(RuntimeError,match='simulated crash'):
        q.quarantine(root,admission=admitted,checkpoint=crash)
    intermediate=q.inspect(root)
    if stage!='queue_removed':
        assert intermediate['queue_failed'] and (source/q.QUEUE).exists()
    else:assert intermediate['archive_complete']
    assert q.quarantine(root,admission=admitted)['old_cut_revoked']
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
        q.quarantine(root,admission=admission)
    assert not archive.exists()
    assert (source/q.QUEUE).exists()
