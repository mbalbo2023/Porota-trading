"""Ten sustained native cuts and resource floors cannot be averaged GREEN."""
from datetime import timedelta
import json
import pytest
from scripts import rc6_native_soak as soak
from scripts.rc6_deploy_generation_gate import collect
from tests.test_rc6_deploy_generation_gate import AT, SHA, TREE, HEALTH, cut

def run_collection(tmp_path, *, stale_at=None, minimum=10):
    (tmp_path/'runtime-health.json').write_text(json.dumps(HEALTH))
    elapsed=[0]
    def reader(*args,**kwargs):
        value=cut();n=int(elapsed[0]//30)
        value['pointer'].update(sequence=n+1,generation_id=f'{n+1:064x}')
        value['status']['as_of']=(AT+timedelta(seconds=n*30)).isoformat()
        if stale_at is not None and n>=stale_at:
            value['status']['as_of']=(AT-timedelta(seconds=121)).isoformat()
        return value
    return collect(tmp_path/'dynamic-shadow',SHA,TREE,timeout=400,minimum_generations=minimum,
        strict=True,clock=lambda:elapsed[0],wait=lambda seconds:elapsed.__setitem__(0,elapsed[0]+seconds),
        now=lambda:AT+timedelta(seconds=elapsed[0]),reader=reader,sample=lambda:{'memory':128*1024**2})

def test_sustained_acceptance_requires_ten_distinct_ordered_cuts(tmp_path):
    result=run_collection(tmp_path)
    assert result['status']=='GREEN' and result['rejection_count']==0
    assert [r['sequence'] for r in result['generations']]==list(range(1,11))
    assert len(result['resources'])>100

def test_one_stale_cut_ends_the_soak_without_waiting_for_later_recovery(tmp_path):
    result=run_collection(tmp_path,stale_at=4)
    assert result['status']=='RED' and result['rejection_count']==1
    assert result['error_reason']=='GENERATION_STALE_OR_FUTURE'
    assert len(result['generations'])==4

@pytest.mark.parametrize('invalid',(True,1,101,2.0))
def test_generation_count_is_strictly_typed(tmp_path,invalid):
    with pytest.raises(ValueError,match='COLLECTION_CONTRACT_INVALID'):
        run_collection(tmp_path,minimum=invalid)

@pytest.mark.parametrize('available,valid',[(128*1024**2,True),(128*1024**2-1024,False)])
def test_memory_floor_is_enforced_in_every_sample(tmp_path,available,valid):
    memory=tmp_path/'meminfo'
    memory.write_text(f'MemAvailable: {available//1024} kB\nSwapTotal: 100 kB\nSwapFree: 50 kB\n')
    state=[{'status':'running','oom':False}]
    if valid:
        assert soak.resource_sample(tmp_path,state,state_fn=lambda:state,memory_path=memory)['available_memory_bytes']==available
    else:
        with pytest.raises(ValueError,match='RAM_RESERVE_BREACHED'):
            soak.resource_sample(tmp_path,state,state_fn=lambda:state,memory_path=memory)

def test_service_restart_is_a_red_sample(tmp_path):
    memory=tmp_path/'meminfo';memory.write_text('MemAvailable: 1048576 kB\n')
    before=[{'status':'running','oom':False,'restarts':0}]
    after=[{'status':'running','oom':False,'restarts':1}]
    with pytest.raises(ValueError,match='SERVICE_CHANGED'):
        soak.resource_sample(tmp_path,before,state_fn=lambda:after,memory_path=memory)

def test_stable_reader_never_retries_a_failed_worker_or_stale_cut(tmp_path,monkeypatch):
    from rc6_shadow_runtime.persistence import EvidenceFiles
    with EvidenceFiles(tmp_path) as files:files.write('status.json',{'status':'FAIL_CLOSED'})
    calls=[]
    def revoked(*args,**kwargs):
        calls.append(1);raise ValueError('GENERATION_REVOKED_OR_PUBLICATION_IN_PROGRESS')
    monkeypatch.setattr(soak,'read_committed_generation',revoked)
    with pytest.raises(ValueError):soak.stable_reader(tmp_path)
    assert len(calls)==1
    calls.clear()
    def stale(*args,**kwargs):
        calls.append(1);raise ValueError('GENERATION_STALE_OR_FUTURE')
    monkeypatch.setattr(soak,'read_committed_generation',stale)
    with pytest.raises(ValueError):soak.stable_reader(tmp_path)
    assert len(calls)==1

def test_storage_profile_measures_actual_full_audit_and_keeps_source_clock(tmp_path):
    from tests.test_rc6_shadow_runtime_wiring import make_store,PRE
    from rc6_shadow_runtime.worker import ShadowRuntime
    from rc6_shadow_runtime.persistence import read_committed_generation
    store,_=make_store(tmp_path,count=3)
    worker=ShadowRuntime(store.path,evidence_root=tmp_path/'shadow',source_roots=[])
    worker.tick(PRE)
    value=read_committed_generation(worker.root)
    profile=value['status']['generation_timing']['storage_profile']
    assert set(profile)=={'computed','latest','checkpoint'}
    assert profile['computed']['raw_bytes']==value['report']['computed_report']['raw_bytes']
    for name in ('encode_seconds','encode_cpu_seconds','compress_hash_seconds','fsync_seconds'):
        assert profile['computed'][name]>=0
    assert value['status']['as_of']==value['publication']['source_as_of']==PRE.isoformat()
