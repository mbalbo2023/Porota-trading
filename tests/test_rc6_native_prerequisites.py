"""Economic adversaries for authentic sealed-CI admission; no ROOT launch."""
import copy
import io
import json
import sys
import zipfile

import pytest
from scripts import rc6_native_prerequisites as native
from scripts.rc6_actions_custody import canonical,digest


def sealed_container(change=None):
    xml=b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="original" name="exact"/></testsuite></testsuites>'
    facts=native.development.junit_facts(xml)
    kernel={'returncode':0,'timed_out':False,'remaining_owned_children':[],
        'actual_child_reaped':True,'owned_children_exhaustion_verified':True,
        'process_group_absent_after_reap':True,'supervisor_errors':[],
        'pid':123,'wait4_reaped_pid':123,'kernel_pre_popen_echild_verified':True,
        'subreaper_activation_readback_verified':True,'subreaper_restore_attempted':True,
        'subreaper_restoration_readback_verified':True,'late_observed_main_reap_irreversible_red':False,
        'kernel_wait4_zero_observed_irreversible_red':False,'residual_descendants_observed':[],
        'owned_group_signal_observations':[],'wall_seconds':1.0,'adopted_descendants_reaped':[]}
    cleanup={'actual_owned_fin_closed':True,'namespace_removed':True,
        'original_namespace_removed':True,'foreign_paths_removed':0}
    result={'schema':'porota.rc6.development-checks.v1','source_sha':'a'*40,'source_tree':'b'*40,
        'status':'PASS_DEVELOPMENT_ONLY','fullSource_unchanged':True,'identities_equal':True,
        'real_orders_sent':0,'G0_G8_qualification':False,'Product157_qualified':False,
        'plan_sha256':digest((native.ROOT/'ops/policy/rc6-development-checks-v1.json').read_bytes()),
        'epochs':[{'epoch':epoch,'passed':True,'retirement_error':None,'junit_validation_error':None,
            'kernel':copy.deepcopy(kernel),'cleanup':copy.deepcopy(cleanup),'junit':copy.deepcopy(facts)}
            for epoch in ('311','312')]}
    index={'files':{'unit.py':{'git_blob':'e'*40,'git_mode':'100644','bytes':1,'sha256':'f'*64,
        'stat_fields':{key:0 for key in native.governed.FIELDS}}},
        'physical_directories':{},'git_metadata':{},'source_sha':'a'*40,'source_tree':'b'*40,
        'physical_namespace_exact_to_literal_tree':True,'overlay_count':0}
    files={'source-before.index.json':canonical(index),'source-after.index.json':canonical(index)}
    if change:change(result,files)
    for epoch in ('311','312'):
        row=next(item for item in result['epochs'] if item['epoch']==epoch)
        binding={'candidate_sha':'a'*40,'candidate_tree':'b'*40,'producer':'DEVELOPMENT_CONTROLS_'+epoch,
            'attempt_id':'13-1','owner_id':'UNIT_ONLY','runner_class':'DIAGNOSTIC',
            'workload_fingerprint':result['plan_sha256']}
        fin={'schema':'porota.rc6.generated-fixture-owned-fin.v1','kernel':row['kernel'],
            'binding':binding,'namespace_nonce':'unit-only','manager_sha256':native.development.lifecycle.DRIVER_SHA256,
            'actual_owned_fin_closed':True,'phase_green':True,'global_or_other_producer_FIN_claimed':False}
        fin_raw=canonical(fin)
        from scripts import rc6_heavy_test_preflight as preflight
        manifest={'schema':'porota.rc6.generated-fixture-required-capture.v1','actual_owned_fin_closed':True,
            'binding':binding,'namespace_nonce':'unit-only','phase_green':True,'owned_fin_kind':'NATIVE_WAIT4_OWNED_FIN',
            'kernel_sha256':preflight.digest(row['kernel']),'files':[
                {'relative_source':'junit.xml','capture_file':'0001.raw','bytes':len(xml),'sha256':digest(xml)},
                {'relative_source':'producer-owned-fin-development-'+epoch+'.json','capture_file':'0002.raw',
                    'bytes':len(fin_raw),'sha256':digest(fin_raw)}]}
        manifest_raw=canonical(manifest)
        row['cleanup'].update(namespace_nonce=manifest['namespace_nonce'],binding=binding,
            capture_manifest_sha256=digest(manifest_raw))
        files[epoch+'/manifest.json']=manifest_raw
        files[epoch+'/0001.raw']=xml
        files[epoch+'/0002.raw']=fin_raw
    files['result.json']=canonical(result)
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        for name,body in files.items():archive.writestr(name,body)
    raw=stream.getvalue()
    origin={'artifact_id':7,'run_id':13,'source_sha':'a'*40,'source_tree':'b'*40,
        'bytes':len(raw),'sha256':digest(raw),'conclusion':'success'}
    return raw,origin


def test_sealed_dual_parser_positive_requires_actual_source_comparison(monkeypatch):
    comparisons=[]
    monkeypatch.setattr(native.governed,'compare_source',lambda before,after:comparisons.append((before,after)))
    monkeypatch.setattr(native,'literal_inventory',lambda sha:{'unit.py':('100644','e'*40)})
    raw,origin=sealed_container()
    result=native.verify_positive_economics(raw,origin,source_sha='a'*40,source_tree='b'*40)
    assert len(comparisons)==1 and len(result['epochs'])==2
    assert result['G0_G8_qualification'] is False


@pytest.mark.parametrize('field,value',[
    ('status','RED'),('fullSource_unchanged',False),('identities_equal',False),
    ('real_orders_sent',1),('plan_sha256','0'*64)])
def test_sealed_claims_cannot_admit_a_red_or_foreign_source(field,value):
    raw,origin=sealed_container(lambda result,files:result.update({field:value}))
    with pytest.raises(ValueError,match='ACTUAL_DUAL_POSITIVE'):
        native.verify_positive_economics(raw,origin,source_sha='a'*40,source_tree='b'*40)


@pytest.mark.parametrize('section,field,value',[
    ('kernel','returncode',1),('kernel','timed_out',True),('kernel','remaining_owned_children',[123]),
    ('kernel','actual_child_reaped',False),('kernel','owned_children_exhaustion_verified',False),
    ('kernel','process_group_absent_after_reap',False),('kernel','supervisor_errors',['EPERM']),
    ('kernel','late_observed_main_reap_irreversible_red',True),
    ('kernel','kernel_wait4_zero_observed_irreversible_red',True),
    ('cleanup','actual_owned_fin_closed',False),('cleanup','original_namespace_removed',False),
    ('cleanup','foreign_paths_removed',True)])
def test_sealed_incomplete_fin_or_foreign_cleanup_cannot_authorize_root(section,field,value):
    raw,origin=sealed_container(lambda result,files:result['epochs'][0][section].update({field:value}))
    with pytest.raises(ValueError,match='ACTUAL_FIN|ORIGINAL_CLEANUP'):
        native.verify_positive_economics(raw,origin,source_sha='a'*40,source_tree='b'*40)


def test_resealed_junit_header_cannot_borrow_reported_zero_failures():
    raw,origin=sealed_container(lambda result,files:result['epochs'][0]['junit'].update(cases=2))
    with pytest.raises(ValueError,match='JUNIT_FACTS_REBOUND'):
        native.verify_positive_economics(raw,origin,source_sha='a'*40,source_tree='b'*40)


@pytest.mark.parametrize('field,value',[
    ('source_sha','c'*40),('source_tree','d'*40),('conclusion','failure')])
def test_historical_or_red_economics_cannot_authorize_a_new_source(field,value):
    raw,origin=sealed_container();origin[field]=value
    with pytest.raises(ValueError,match='SAME_SOURCE_ORIGIN'):
        native.verify_positive_economics(raw,origin,source_sha='a'*40,source_tree='b'*40)


@pytest.mark.parametrize('mutation',[
    {'head_sha':'c'*40},{'path':'.github/workflows/foreign.yml'},{'run_attempt':2},
    {'event':'pull_request'},{'conclusion':'failure'},{'actor':{'login':'foreign'}}])
def test_same_looking_control_never_borrows_canonical_actions_identity(mutation):
    run={'id':13,'head_sha':'a'*40,'run_attempt':1,'event':'workflow_dispatch','path':native.WORKFLOW,
        'actor':{'login':'mbalbo2023'},'repository':{'full_name':native.admission.REPO,'id':native.admission.REPO_ID},
        'head_repository':{'full_name':native.admission.REPO,'id':native.admission.REPO_ID},'status':'completed','conclusion':'success',
        'display_title':'RC6 material development-checks @ '+'a'*40}
    run.update(mutation)
    class Reader:
        def request(self,path):
            assert path=='/actions/runs/13/attempts/1'
            return run
    with pytest.raises(ValueError,match='CANONICAL_EXACT_RUN|POSITIVE_TERMINAL_RUN'):
        native.verify_standard_run(Reader(),13,sha='a'*40,gate='development-checks',
            job_name=native.ECONOMIC_JOB,completed=True)


def test_development_retry_guard_rejects_identical_red_without_launch(monkeypatch):
    from scripts import rc6_material_pr_admission as admission
    monkeypatch.setenv('GITHUB_RUN_ID','14')
    monkeypatch.setattr(admission,'api',lambda path:{'total_count':2,'workflow_runs':[
        {'id':13,'head_sha':'a'*40,'display_title':'RC6 material development-checks @ '+'a'*40,
            'event':'workflow_dispatch','status':'completed','conclusion':'failure'},
        {'id':14,'head_sha':'a'*40,'event':'workflow_dispatch'}]})
    with pytest.raises(ValueError,match='REQUIRES_NEW_EVIDENCED_SHA'):
        admission.dedup_admission('a'*40,'development-checks')


@pytest.mark.parametrize('kind',['source','existing','alias','source-parent'])
def test_rejected_control_path_never_receives_even_a_red_result(tmp_path,monkeypatch,kind):
    source=tmp_path/'Source';source.mkdir()
    existing=tmp_path/'shared';existing.mkdir(mode=0o777)
    alias=tmp_path/'alias';alias.symlink_to(tmp_path/'missing')
    destination={'source':source/'bad','existing':existing,'alias':alias,'source-parent':tmp_path}[kind]
    monkeypatch.setattr(native,'ROOT',source)
    monkeypatch.setattr(sys,'argv',['native','--source-sha','a'*40,'--source-tree','b'*40,
        '--owner-session','UNIT_ONLY','--launch-receipt-url','unit-only','--output',str(destination)])
    monkeypatch.setattr(native,'admit_native_prerequisites',lambda *args:pytest.fail('rejected path reached admission'))
    assert native.main()==1
    assert not list(tmp_path.rglob('native-prerequisites.json'))
    assert not list(tmp_path.rglob('admission-rejected.json'))


@pytest.mark.parametrize('mutation',[
    {'reference':{'ref':'refs/heads/fix/unit','object':{'sha':'c'*40}}},
    {'commit':{'sha':'a'*40,'tree':{'sha':'d'*40}}},
])
def test_advanced_branch_or_rebound_tree_cannot_authorize_native_probe(mutation):
    reference={'ref':'refs/heads/fix/unit','object':{'sha':'a'*40}}
    commit={'sha':'a'*40,'tree':{'sha':'b'*40}}
    class Reader:
        def request(self,path):
            if path=='/git/ref/heads/fix/unit':return mutation.get('reference',reference)
            assert path=='/git/commits/'+'a'*40
            return mutation.get('commit',commit)
    with pytest.raises(ValueError,match='FRESH_BRANCH_OR_TREE_REBOUND'):
        native.verify_native_branch(Reader(),'fix/unit',sha='a'*40,tree='b'*40)


def test_cancelled_native_run_stops_before_any_privileged_child(tmp_path,monkeypatch):
    from scripts import rc6_capacity_comparison as capacity
    from scripts import rc6_privileged_custody as privileged
    output=tmp_path/'fresh'
    monkeypatch.setattr(sys,'argv',['native','--source-sha','a'*40,'--source-tree','b'*40,
        '--owner-session','UNIT_ONLY','--launch-receipt-url','unit-only','--output',str(output)])
    calls=[]
    def fresh_check():
        calls.append('fresh')
        if calls.count('fresh')==2:raise ValueError('NATIVE_PREREQUISITES_CURRENT_LIVE_RUN_REQUIRED')
    monkeypatch.setattr(native,'admit_native_prerequisites',lambda *args:{
        'owner_check':fresh_check,'plan_sha256':'f'*64})
    monkeypatch.setattr(capacity,'readonly_runner_observation',lambda path:{'live_filesystem':{
        'filesystem_type':'ext4','allocation_unit_bytes':4096,'free_bytes':8*1024**3,
        'total_inodes':1000,'free_inodes':900}})
    monkeypatch.setattr(native.governed,'source_pin',lambda *args:{'unit-only':True})
    monkeypatch.setattr(native,'prove_filter_in_child',lambda **kwargs:calls.append('filter') or {})
    monkeypatch.setattr(privileged,'prove_custody',lambda **kwargs:pytest.fail('ROOT reached after cancellation'))
    # Native-contract construction is pure; this fixture never qualifies custody.
    monkeypatch.setattr(privileged,'native_contract',lambda *args:{'unit-only':True})
    assert native.main()==1
    assert calls==['fresh','filter','fresh']
    result=json.loads((output/'native-prerequisites.json').read_bytes())
    assert result['error_signature']=='NATIVE_PREREQUISITES_CURRENT_LIVE_RUN_REQUIRED'
    assert result['backing_allocated'] is result['material_gate_launched'] is False


def test_empty_resealed_source_inventory_is_not_literal_source_evidence(monkeypatch):
    monkeypatch.setattr(native,'literal_inventory',lambda sha:{'unit.py':('100644','e'*40)})
    def empty(result,files):
        for name in ('source-before.index.json','source-after.index.json'):
            index=json.loads(files[name]);index['files']={};files[name]=canonical(index)
    raw,origin=sealed_container(empty)
    with pytest.raises(ValueError,match='FULL_LITERAL_SOURCE_INVENTORY_REBOUND'):
        native.verify_positive_economics(raw,origin,source_sha='a'*40,source_tree='b'*40)


def test_root_start_failure_keeps_primary_red_and_original_diagnostic(tmp_path,monkeypatch):
    """Controller regression only; the fixture never launches or qualifies ROOT."""
    from scripts import rc6_capacity_comparison as capacity
    from scripts import rc6_privileged_custody as privileged
    output=tmp_path/'fresh'
    monkeypatch.setattr(sys,'argv',['native','--source-sha','a'*40,'--source-tree','b'*40,
        '--owner-session','UNIT_ONLY','--launch-receipt-url','unit-only','--output',str(output)])
    monkeypatch.setenv('GITHUB_RUN_ID','123')
    monkeypatch.setattr(native,'admit_native_prerequisites',lambda *args:{
        'owner_check':lambda:None,'plan_sha256':'f'*64})
    monkeypatch.setattr(capacity,'readonly_runner_observation',lambda path:{'live_filesystem':{
        'filesystem_type':'ext4','allocation_unit_bytes':4096,'free_bytes':8*1024**3,
        'total_inodes':1000,'free_inodes':900}})
    monkeypatch.setattr(native.governed,'source_pin',lambda *args:{'unit-only':True})
    monkeypatch.setattr(native,'prove_filter_in_child',lambda **kwargs:{'scope':'UNIT_FIXTURE_ONLY'})
    monkeypatch.setattr(privileged,'native_contract',lambda *args:{'scope':'UNIT_FIXTURE_ONLY'})
    diagnostic={'status':'RED','ROOT_FIN':'UNKNOWN','ROOT_custody_qualified':False,
        'cleanup_authorized':False,'reservation_recovery_credited_bytes':0,
        'original_client_FIN_sha256':'e'*64,'namespace_retained':'UNIT_FIXTURE_ONLY'}
    def fail(**kwargs):
        assert kwargs['capture_parent']==output/'custody-raw'
        raise privileged.CustodyFailure(ValueError('ROOT_CUSTODY_ACTUAL_CONTROLLER_PIDFD_REQUIRED'),diagnostic)
    monkeypatch.setattr(privileged,'prove_custody',fail)
    assert native.main()==1
    result=json.loads((output/'native-prerequisites.json').read_bytes())
    assert result['status']=='RED'
    assert result['error_signature']=='ROOT_CUSTODY_ACTUAL_CONTROLLER_PIDFD_REQUIRED'
    assert result['custody_failure_diagnostics']==diagnostic
    assert 'custody' not in result and 'fullSource_unchanged' not in result
    assert all(result[key] is False for key in ('G0_G8_qualification','quota_enforcement_proved',
        'product_resource_profile_proved','backing_allocated','material_gate_launched'))
