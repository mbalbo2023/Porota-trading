"""Physical elapsed time, exact priority identity and publication completion."""
from copy import deepcopy
from datetime import timedelta
import inspect
import json

import pytest

from scripts import rc6_deploy_generation_gate as gate
from tests.test_rc6_deploy_generation_gate import AT, SHA, TREE, HEALTH, cut


@pytest.mark.parametrize('first_seconds,second_seconds,expected', [
    (51.231,51.231,'RED'), (89.084,112.068,'RED'), (35,35,'GREEN'),
])
def test_gate_accumulates_compute_publish_poll_and_observation_time(
        tmp_path,first_seconds,second_seconds,expected):
    (tmp_path/'runtime-health.json').write_text(json.dumps(HEALTH))
    elapsed=[first_seconds]
    second_start=first_seconds+30
    second_commit=second_start+second_seconds
    def reader(*args,**kwargs):
        value=cut()
        second=elapsed[0]>=second_commit
        source=second_start if second else 0
        value['status']['as_of']=(AT+timedelta(seconds=source)).isoformat()
        value['status']['generation_timing']['tick_seconds']=second_seconds if second else first_seconds
        value['pointer'].update(sequence=2 if second else 1,generation_id=('4' if second else '1')*64)
        return value
    def wait(seconds):elapsed[0]+=seconds
    result=gate.collect(tmp_path/'dynamic-shadow',SHA,TREE,timeout=180,
        clock=lambda:elapsed[0],wait=wait,now=lambda:AT+timedelta(seconds=elapsed[0]),reader=reader)
    assert result['status']==expected
    if expected=='GREEN':assert [r['sequence'] for r in result['generations']]==[1,2]


@pytest.mark.parametrize('age,valid', [(120,True),(120.000001,False),(-.000001,False)])
def test_source_ceiling_boundary_is_exact(age,valid):
    value=cut();value['status']['generation_timing']['tick_seconds']=45
    value['status']['as_of']=(AT-timedelta(seconds=age)).isoformat()
    if valid:gate.receipt(value,HEALTH,SHA,TREE,AT)
    else:
        with pytest.raises(ValueError,match='GENERATION_STALE_OR_FUTURE'):
            gate.receipt(value,HEALTH,SHA,TREE,AT)


def test_open_priority_index_is_built_once_per_engine(tmp_path):
    from rc6_shadow_runtime import funnel
    from tests.test_rc6_shadow_operational_funnel import bootstrap,plan,START
    _,checkpoint=bootstrap(tmp_path)
    report=plan(START+timedelta(seconds=10),state='HOT',promoted=START+timedelta(seconds=1),opened=True)
    engine=report['engines']['EQUITY_SPOT']
    engine['telemetry']*=40
    class Counted(list):
        scans=0
        def __iter__(self):
            self.scans+=1
            return super().__iter__()
    priorities=Counted(engine['opened_priority'])
    engine['opened_priority']=priorities
    funnel._planner_stages(checkpoint,report,START+timedelta(seconds=10))
    assert priorities.scans==1


def test_priority_index_preserves_legacy_output_for_full_monetary_identity(tmp_path):
    from rc6_shadow_runtime import funnel
    from tests.test_rc6_shadow_operational_funnel import bootstrap,plan,START
    _,checkpoint=bootstrap(tmp_path)
    at=START+timedelta(seconds=10)
    report=plan(at,state='HOT',promoted=START+timedelta(seconds=1),opened=True)
    engine=report['engines']['EQUITY_SPOT']
    variants=[]
    for currency,market,settlement in [('ARS','BYMA','A-24HS'),('USD','BYMA','A-24HS'),
            ('ARS','OTHER','A-24HS'),('ARS','BYMA','INMEDIATA')]:
        row=deepcopy(engine['telemetry'][0]);row['identity']=['José 🚀','ACCIONES',market,currency,settlement]
        variants.append(row)
    engine['telemetry']=variants
    engine['opened_priority']=[variants[0]['identity'],variants[0]['identity'],variants[3]['identity']]
    # Preserve a literal reference implementation, independent of the index.
    source=inspect.getsource(funnel._planner_stages)
    source=source.replace('def _planner_stages(', 'def legacy(')
    source=source.replace('opened_priority = tuple(ident) in opened_priority_index',
        'opened_priority = list(ident) in [[k[0], k[1], k[4], k[3], k[2]] for k in plan.get("opened_priority", [])]')
    namespace=dict(funnel.__dict__);exec(source,namespace)
    old=deepcopy(checkpoint);new=deepcopy(checkpoint)
    assert namespace['legacy'](old,report,at)==funnel._planner_stages(new,report,at)
    assert old==new


def test_publication_cost_includes_pointer_write_latency(tmp_path,monkeypatch):
    from rc6_shadow_runtime.worker import ShadowRuntime
    from rc6_shadow_runtime import worker as runtime
    from rc6_shadow_runtime.persistence import EvidenceFiles,read_committed_generation
    from tests.test_rc6_shadow_runtime_wiring import make_store,PRE
    store,_=make_store(tmp_path,count=3)
    worker=ShadowRuntime(store.path,evidence_root=tmp_path/'shadow',source_roots=[])
    monotonic=runtime.time.monotonic
    extra=[0.0]
    monkeypatch.setattr(runtime.time,'monotonic',lambda:monotonic()+extra[0])
    write=EvidenceFiles.write
    def delayed(self,name,payload,**kwargs):
        result=write(self,name,payload,**kwargs)
        if name=='CURRENT.json':extra[0]+=7
        return result
    monkeypatch.setattr(EvidenceFiles,'write',delayed)
    worker.tick(PRE)
    cut=read_committed_generation(worker.root)
    publication=worker.files.read('publication.json')
    assert publication['generation_id']==cut['pointer']['generation_id']
    assert publication['source_as_of']==PRE.isoformat()
    assert publication['total_seconds']>=7
    assert publication['total_seconds']>=cut['status']['generation_timing']['tick_seconds']+6.9
    assert publication['publish_seconds']>=7


def test_gate_retains_intermediate_rejection_even_when_last_read_is_valid(tmp_path):
    (tmp_path/'runtime-health.json').write_text(json.dumps(HEALTH))
    elapsed=[0];calls=[0]
    def reader(*args,**kwargs):
        calls[0]+=1
        if calls[0]==2:raise ValueError('GENERATION_REVOKED_OR_PUBLICATION_IN_PROGRESS')
        return cut()
    def wait(seconds):elapsed[0]+=seconds
    r=gate.collect(tmp_path/'dynamic-shadow',SHA,TREE,timeout=9,
        clock=lambda:elapsed[0],wait=wait,now=lambda:AT,reader=reader)
    assert r['status']=='RED' and r['error_reason'] is None
    assert r['rejection_count']==1
    assert [e['reason'] for e in r['observations'] if e['kind']=='REJECTED']==[
        'GENERATION_REVOKED_OR_PUBLICATION_IN_PROGRESS']


def test_gate_history_is_bounded_and_private_exception_text_is_absent(tmp_path):
    (tmp_path/'runtime-health.json').write_text(json.dumps(HEALTH))
    elapsed=[0]
    def reader(*args,**kwargs):raise ValueError('PRIVATE_TEST_PAYLOAD')
    def wait(seconds):elapsed[0]+=seconds
    r=gate.collect(tmp_path/'dynamic-shadow',SHA,TREE,timeout=400,
        clock=lambda:elapsed[0],wait=wait,now=lambda:AT,reader=reader)
    assert len(r['observations'])==128 and r['rejection_count']==200
    assert 'PRIVATE_TEST_PAYLOAD' not in json.dumps(r)


def test_gate_requires_matching_publication_identity_and_measured_total():
    value=cut();value['status']['generation_timing']['tick_measurement_boundary']='BEFORE_STATUS_AND_CURRENT'
    with pytest.raises(ValueError,match='GENERATION_PUBLICATION_COST_UNVERIFIED'):
        gate.receipt(value,HEALTH,SHA,TREE,AT)
    value['publication']={'generation_id':value['pointer']['generation_id'],
        'source_as_of':value['status']['as_of'],'total_seconds':2.0,'poll_seconds':30}
    assert gate.receipt(value,HEALTH,SHA,TREE,AT)
    for k,v in [('generation_id','f'*64),('total_seconds',float('nan')),('total_seconds',True),('poll_seconds',31)]:
        changed=deepcopy(value);changed['publication'][k]=v
        with pytest.raises(ValueError,match='GENERATION_PUBLICATION_COST_UNVERIFIED'):
            gate.receipt(changed,HEALTH,SHA,TREE,AT)
