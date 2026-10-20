"""Replay the 20/10 COMPLETE→COMPLETE→FAILED live COLD lineage."""
import hashlib
import json
import os
import pytest
from unittest.mock import patch

from scripts import rc6_cold2_quarantine as old
from scripts import rc6_cold2_successor as new
from scripts import rc6_cold2_apply_guard as guard
from tests.test_rc6_cold2_quarantine import state, admitted, EXPECTED, put
from tests.test_rc6_cold2_apply_guard import receipt as deploy_receipt, CANDIDATE

ID = 'e7f8eb9dfc954b4c99346a14ed280e4f'
CUT = 'b3d10eb98e51b04c2d11039dd3fd98394348b6078c1369f99e3a1371903a07eb'


@pytest.fixture
def successor(state):
    root, source, archive, _ = state
    old.quarantine(root, admission=admitted, expected_owned_ids=EXPECTED)
    assert old.verify_original_archive(root)['old_cut_revoked']
    for other in ('87f18ab0f25749c0bf3f0b3ad73a164b',
                  'b024ef43497d4108af829947f8855701'):
        complete={'snapshot_id':other,'pack':other+'.pack'}
        put(source/('manifest-'+other+'.json'),complete,cold=True)
        put(source/('receipt-'+other+'.json'),{'snapshot_id':other,
            'state':'COMPLETE','manifest_sha256':old.digest(complete)},cold=True)
    pack = source/(ID+'.pack')
    raw = b'actual sealed successor input' * 200
    pack.write_bytes(raw)
    pack.chmod(0o600)
    st = pack.stat()
    sealed = 1792485190.2664664
    manifest = {'epoch':'rc6.audit-actual-values.v1','snapshot_id':ID,'pack':pack.name,
                'pack_bytes':len(raw),'pack_sha256':hashlib.sha256(raw).hexdigest(),
                'pack_identity':[st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns],
                'source_as_of':'2026-10-20T08:32:29.850494+00:00',
                'configuration_fingerprint':'c'*64,
                'sealed_at':sealed,'deadline_at':sealed+30.0}
    digest = old.digest(manifest)
    put(source/('manifest-'+ID+'.json'),manifest,cold=True)
    put(source/('receipt-'+ID+'.json'),{'state':'FAILED','snapshot_id':ID,
        'manifest_sha256':digest,'error_class':'ValueError',
        'error_code':'SHADOW_COLD_DEADLINE_EXPIRED_OR_CLOCK_REGRESSED',
        'error_phase':'REPORT_WRITE','failed_at':sealed+30.04},cold=True)
    put(source/'queue.json',{'snapshot_id':ID,'manifest_sha256':digest,'state':'FAILED'},cold=True)
    shadow=source.parent
    put(shadow/'CURRENT.json',{'generation_id':CUT,'file':'cut-'+CUT+'.json.gz'})
    put(shadow/('cut-'+CUT+'.json.gz'),{
        'report':{'computed_report':{'schema':'rc6.computed-audit-reference.v2',
            'state':'PENDING','snapshot_id':ID,'manifest_sha256':digest,
            'deadline_at':sealed+30.0,'source_as_of':manifest['source_as_of'],
            'configuration_fingerprint':manifest['configuration_fingerprint']}},
        'status':{'real_orders_sent':0,'real_routes':'NOT_CALLED'}},zipped=True)
    put(shadow/'status.json',{'status':'FAIL_CLOSED','real_orders_sent':0,
                             'real_routes':'NOT_CALLED'})
    (shadow/('cut-'+old.CUT+'.json.gz')).unlink()  # bounded two-cut GC on host
    return root, source, root/'data/deploy'/('rc6-failed-cold-'+ID), raw


def test_new_failed_successor_is_archived_without_touching_complete_history(successor):
    root, source, archive, raw=successor
    complete={n:(source/n).read_bytes() for n in old.OTHER}
    with pytest.raises(ValueError,match='COLD2_UNEXPECTED_SOURCE'):
        old.inspect(root)
    before=new.inspect(root)
    assert before['snapshot_id']==ID and before['queue_failed']
    result=new.quarantine(root,admission=admitted,expected_owned_ids=EXPECTED)
    assert result['state']=='QUARANTINED_FAILED' and result['old_cut_revoked']
    assert (archive/(ID+'.pack')).read_bytes()==raw
    assert {n:(source/n).read_bytes() for n in old.OTHER}==complete
    assert not (source/'queue.json').exists()
    assert new.inspect(root)['archive_complete']
    assert new.quarantine(root,admission=admitted,expected_owned_ids=EXPECTED)['state']=='ALREADY_QUARANTINED'
    assert old.verify_original_archive(root)['old_cut_revoked']


def test_canonical_apply_admits_exact_successor_after_original_archive(successor):
    root,source,archive,_=successor
    value=deploy_receipt()
    deploy=root/'data/deploy'
    (deploy/'.deploy-quiescence.lock').touch()
    (deploy/('quiescence-'+CANDIDATE+'.json')).write_text(json.dumps(value))
    ids={name:value['containers'][name]['id'] for name in guard.OWNED}
    live={name:{'Id':ids[name],'Image':'sha256:'+'a'*64,
                'State':{'Status':'exited','ExitCode':0}} for name in guard.OWNED}
    live['porota_critical_approval_rc6']={'State':{'Status':'running',
        'Health':{'Status':'healthy'},'OOMKilled':False}}
    with (patch.object(guard,'ppi_units',return_value={}),
          patch.object(guard.drain,'admit',return_value='STOPPED'),
          patch.object(guard.drain,'inspect',side_effect=lambda name:live[name])):
        result=guard.apply(root,CANDIDATE)
    assert result['original']['state']=='ALREADY_QUARANTINED'
    assert result['successor']['state']=='QUARANTINED_FAILED'
    assert not (source/'queue.json').exists() and new.inspect(root)['archive_complete']


@pytest.mark.parametrize('stage',['archive_manifest-'+ID+'.json',
    'archive_receipt-'+ID+'.json','archive_queue.json','pack_moved',
    'source_manifest-'+ID+'.json_removed','source_receipt-'+ID+'.json_removed',
    'receipt_durable','queue_removed'])
def test_every_successor_crash_boundary_resumes(successor,stage):
    root,source,archive,raw=successor
    def crash(at):
        if at==stage:raise RuntimeError('crash')
    with pytest.raises(RuntimeError,match='crash'):
        new.quarantine(root,admission=admitted,expected_owned_ids=EXPECTED,checkpoint=crash)
    assert new.quarantine(root,admission=admitted,expected_owned_ids=EXPECTED)['old_cut_revoked']
    assert (archive/(ID+'.pack')).read_bytes()==raw
    assert {p.name for p in source.iterdir()}==old.OTHER


@pytest.mark.parametrize('change',['pack','queue','cut','extra_failed','alias'])
def test_divergence_refuses_without_archive(successor,change):
    root,source,archive,_=successor
    if change=='pack':
        with (source/(ID+'.pack')).open('r+b') as stream:stream.write(b'X')
    elif change=='queue':
        put(source/'queue.json',{'snapshot_id':ID,'manifest_sha256':'0'*64,'state':'FAILED'},cold=True)
    elif change=='cut':
        put(source.parent/'CURRENT.json',{'generation_id':'0'*64,'file':'cut-'+'0'*64+'.json.gz'})
    elif change=='extra_failed':
        put(source/('receipt-'+'f'*32+'.json'),{'state':'FAILED','snapshot_id':'f'*32},cold=True)
    else:
        (source/(ID+'.pack')).unlink()
        (source/(ID+'.pack')).symlink_to(root/'outside')
    with pytest.raises((ValueError,FileNotFoundError)):
        new.quarantine(root,admission=admitted,expected_owned_ids=EXPECTED)
    assert not archive.exists()
    assert (source/'queue.json').exists()


def test_memory_reserve_failed_successor_archives_with_exact_pack_binding(successor):
    root,source,archive,raw=successor
    path=source/('receipt-'+ID+'.json')
    receipt=old.envelope(path,cold=True)
    receipt['error_code']='SHADOW_COLD_HOST_MEMORY_RESERVE_BREACHED'
    receipt['error_phase']='PACK_HASH'
    receipt['failed_at']=1792485190.2664664+5
    put(path,receipt,cold=True)
    assert new.inspect(root)['snapshot_id']==ID
    result=new.quarantine(root,admission=admitted,expected_owned_ids=EXPECTED)
    assert result['state']=='QUARANTINED_FAILED'
    assert (archive/(ID+'.pack')).read_bytes()==raw
    assert not (source/'queue.json').exists()


@pytest.mark.parametrize('error_code,error_phase',[
    ('SHADOW_COLD_HOST_MEMORY_RESERVE_BREACHED','REPORT_WRITE'),
    ('SHADOW_COLD_DEADLINE_EXPIRED_OR_CLOCK_REGRESSED','PACK_HASH'),
    ('SHADOW_COLD_HOST_MEMORY_RESERVE_BREACHED','OTHER'),
])
def test_successor_rejects_unpaired_failure_reason(successor,error_code,error_phase):
    root,source,archive,_=successor
    path=source/('receipt-'+ID+'.json')
    receipt=old.envelope(path,cold=True)
    receipt['error_code']=error_code
    receipt['error_phase']=error_phase
    put(path,receipt,cold=True)
    with pytest.raises(ValueError,match='COLD2_SUCCESSOR_BINDING'):
        new.quarantine(root,admission=admitted,expected_owned_ids=EXPECTED)
    assert not archive.exists()
