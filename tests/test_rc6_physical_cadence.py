"""Physical cadence adversaries; unit clocks are models, never host evidence."""
from datetime import timedelta
import gzip

import pytest

from scripts import rc6_postdeploy_stress as h
from tests.test_rc6_shadow_runtime_wiring import PRE


def model(mode='fresh'):
    result={'cadence_mode':mode,'required_generations':12,'catalog_count':14611,'ready_fixture_count':9902,
        'process_peak_rss_kib':180000,'real_orders_sent':0,'provider_requests':0,'real_routes':'NOT_CALLED',
        'nice':10,'database_unchanged':True,'observed_publications':12,'elapsed_seconds':455,
        'production_data_mounted':False,'actual_market_acceptance':False,
        'source_sha':'1'*40,'candidate_tree':'2'*40,'source_origin':PRE.isoformat(),
        'measurement_started_monotonic':100,'cuts':[]}
    for i in range(12):
        at=PRE+timedelta(seconds=i*40);raw={'raw_bytes':101*1024**2,'sha256':f'{i:064x}'}
        observation=h.cadence_observation(origin=PRE,started=100,published=110+i*40,observed=115+i*40,
            as_of=at,budget=80,previous=result['cuts'][-1]['physical_observation'] if i else None)
        result['cuts'].append({'generation_id':f'{i:064x}','sequence':i+1,'as_of':at.isoformat(),
            'freshness_budget_seconds':80,'worker_timing':{'poll_seconds':30,'tick_seconds':10},
            'computed_report_sha256':raw['sha256'],'computed_report_raw_bytes':raw['raw_bytes'],
            'cold_audit':{'state':'COMPLETE','wall_seconds':5,'source_as_of':at.isoformat(),'computed_report':raw},
            'physical_observation':observation})
    if mode=='legacy':
        result.update(legacy_seed={'standard_gzip':True,'raw_before':{'sha256':'f'*64,'bytes':100},
            'raw_after':{'sha256':'f'*64,'bytes':100}},first_checkpoint_reused=True)
    return result


def test_original_measured_pair_rejects_at_unchanged_120s_ceiling():
    # Actual historical7ce measurements, not a fabricated benchmark.
    first=49.789;second=48.318
    previous={'publication_monotonic':first,'source_as_of':PRE.isoformat(),'freshness_budget_seconds':120}
    with pytest.raises(ValueError,match='PREVIOUS_SOURCE_EXPIRED'):
        h.cadence_observation(origin=PRE,started=0,published=first+30+second,
            observed=first+30+second+1,as_of=PRE+timedelta(seconds=first+30),budget=120,previous=previous)


def test_cpu_ratio_is_not_a_substitute_for_measured_physical_continuity():
    # Algebraic counterexamples, NOT predictions of Droplet performance.
    assert 80/100==.8 and 80+30+80>120
    previous={'publication_monotonic':80,'source_as_of':PRE.isoformat(),'freshness_budget_seconds':120}
    with pytest.raises(ValueError,match='PREVIOUS_SOURCE_EXPIRED'):
        h.cadence_observation(origin=PRE,started=0,published=190,observed=191,
            as_of=PRE+timedelta(seconds=110),budget=120,previous=previous)
    assert 35/40>.8
    previous={'publication_monotonic':35,'source_as_of':PRE.isoformat(),'freshness_budget_seconds':120}
    measured=h.cadence_observation(origin=PRE,started=0,published=100,observed=105,
        as_of=PRE+timedelta(seconds=65),budget=120,previous=previous)
    assert measured['previous_age_at_publication_seconds']==100


def test_old_cut_continuity_ends_at_next_real_publication():
    previous={'publication_monotonic':40,'source_as_of':PRE.isoformat(),'freshness_budget_seconds':120}
    measured=h.cadence_observation(origin=PRE,started=0,published=110,observed=130,
        as_of=PRE+timedelta(seconds=70),budget=120,previous=previous)
    assert measured['previous_age_at_publication_seconds']==110
    assert measured['observed_source_age_seconds']==60


@pytest.mark.parametrize('mode',['fresh','legacy'])
def test_twelve_cut_complete_model_is_accepted_without_native_claim(mode):
    value=model(mode);assert h.verify_cadence_series(value)
    assert value['actual_market_acceptance']is False and value['production_data_mounted']is False


@pytest.mark.parametrize('field,value',[('required_generations',2),('nice',20),('database_unchanged',False),
    ('real_orders_sent',False),('real_orders_sent',1),('provider_requests',False),('real_routes','CALLED'),
    ('process_peak_rss_kib',193*1024),('process_peak_rss_kib',0),('elapsed_seconds',10),
    ('elapsed_seconds',float('nan')),('source_sha',None),('candidate_tree','wrong'),('observed_publications',2)])
def test_nonprotective_or_unverified_series_cannot_pass(field,value):
    receipt=model();receipt[field]=value
    with pytest.raises(ValueError):h.verify_cadence_series(receipt)


@pytest.mark.parametrize('change',['two','duplicate','cold_pending','cold_late','wrong_clock','small_audit',
    'wrong_sha','forged_age','old_cut_expired','virtual_fast_forward','wrong_poll'])
def test_each_adverse_cut_revokes_whole_cadence_series(change):
    receipt=model();cut=receipt['cuts'][1]
    if change=='two':receipt['cuts']=receipt['cuts'][:2]
    elif change=='duplicate':cut['generation_id']=receipt['cuts'][0]['generation_id']
    elif change=='cold_pending':cut['cold_audit']['state']='PENDING'
    elif change=='cold_late':cut['cold_audit']['wall_seconds']=30.01
    elif change=='wrong_clock':cut['cold_audit']['source_as_of']=PRE.isoformat()
    elif change=='small_audit':cut['computed_report_raw_bytes']=100*1024**2
    elif change=='wrong_sha':cut['cold_audit']['computed_report']['sha256']='f'*64
    elif change=='forged_age':cut['physical_observation']['observed_source_age_seconds']=0
    elif change=='old_cut_expired':receipt['cuts'][0]['freshness_budget_seconds']=1
    elif change=='virtual_fast_forward':cut['as_of']=(PRE+timedelta(seconds=1)).isoformat()
    elif change=='wrong_poll':cut['worker_timing']['poll_seconds']=0
    with pytest.raises(ValueError):h.verify_cadence_series(receipt)


@pytest.mark.parametrize('change',['not_standard','changed_raw','not_reused'])
def test_legacy_requires_actual_unchanged_standard_checkpoint_bootstrap(change):
    receipt=model('legacy')
    if change=='not_standard':receipt['legacy_seed']['standard_gzip']=False
    elif change=='changed_raw':receipt['legacy_seed']['raw_after']['sha256']='0'*64
    else:receipt['first_checkpoint_reused']=False
    with pytest.raises(ValueError,match='LEGACY_BOOTSTRAP'):h.verify_cadence_series(receipt)


def test_real_indexed_checkpoint_seed_preserves_every_raw_byte_and_only_causal_files(tmp_path):
    from rc6_shadow_runtime.persistence import EvidenceFiles
    source,target=tmp_path/'source',tmp_path/'legacy'
    payload={'as_of':PRE.isoformat(),'engines':{'actual':'é'},'funnel':{'nested':[1,2,'actual']}}
    with EvidenceFiles(source) as files:
        files.write_checkpoint(payload)
        files.write('preopen-2026-10-05.json.gz',{'actual':'source'})
        files.write('CURRENT.json',{'must_not_be_copied':True})
    (source/'cold-audit').mkdir();(source/'cold-audit'/'queue.json').write_text('not a valid completion')
    before=(source/'checkpoint.json.gz').read_bytes()
    receipt=h.standard_legacy_seed(source,target)
    assert receipt['raw_before']==receipt['raw_after'] and receipt['standard_gzip']is True
    assert (target/'checkpoint.json.gz').read_bytes()[:4]==b'\x1f\x8b\x08\x00'
    assert gzip.open(source/'checkpoint.json.gz','rb').read()==gzip.open(target/'checkpoint.json.gz','rb').read()
    assert (source/'checkpoint.json.gz').read_bytes()==before
    assert set(p.name for p in target.iterdir())=={'checkpoint.json.gz','preopen-2026-10-05.json.gz'}
    with pytest.raises(FileExistsError):h.standard_legacy_seed(source,target)


def test_cadence_cli_cannot_mix_smoke_or_two_cut_contract(tmp_path):
    import subprocess,sys
    for flag in ('--smoke','--full-catalog-check'):
        run=subprocess.run([sys.executable,str(h.ROOT/'scripts/rc6_postdeploy_stress.py'),
            '--output',str(tmp_path),'--cadence-stage','fresh',flag],capture_output=True,text=True,timeout=20)
        assert run.returncode==2 and 'cannot use smoke or two-cut mode' in run.stderr


@pytest.mark.parametrize('value',[True,float('nan'),float('inf'),-1])
def test_clock_and_budget_are_actual_finite_measured_values(value):
    with pytest.raises(ValueError):
        h.cadence_observation(origin=PRE,started=0,published=value,observed=20,as_of=PRE,budget=120)


def test_receipt_cannot_enlarge_original_measured_generation_budget():
    receipt=model();receipt['cuts'][0]['freshness_budget_seconds']=120
    with pytest.raises(ValueError,match='ORIGINAL_BUDGET_CHANGED'):h.verify_cadence_series(receipt)



def test_exact_artifact_workflow_runs_two_standalone_twelve_cut_stages():
    from pathlib import Path
    import yaml
    workflow=yaml.safe_load((h.ROOT/'.github/workflows/porota-predeploy-v2.yml').read_text())
    job=workflow['jobs']['artifact-gate']
    assert job['timeout-minutes']*60>=35*60+2*1500+5*60
    native=next(step['run'] for step in job['steps'] if step.get('name')=='Exact image native sizing and generated catalog proof')
    for stage in ('fresh','legacy'):
        assert 'run_native_proof cadence-'+stage+' python scripts/rc6_postdeploy_stress.py' in native
        assert '--cadence-stage '+stage+' --output /proof/cadence' in native
        assert 'porota-native-cadence-'+stage+'-$GITHUB_RUN_ID' in native
    assert 'cadence-fresh|cadence-legacy) rc6_timeout=1500' in native
    assert 'rc6_memory=(--memory 512m --memory-swap 512m)' in native
    assert 'if test "$rc6_label" = session; then' in native
    assert 'rc6_memory=(--memory 256m --memory-swap 256m)' in native
    assert '"${rc6_memory[@]}" --cpus .75' in native
    assert '--network none' in native and '--user 1000:1000' in native and '--cap-drop ALL' in native



def test_legacy_stage_cannot_use_another_legacy_receipt_as_its_fresh_proof(tmp_path,monkeypatch):
    import json
    prior=model('legacy');prior['status']='GREEN'
    monkeypatch.setenv('POROTA_BUILD_SHA',prior['source_sha'])
    monkeypatch.setenv('POROTA_CANDIDATE_TREE_SHA',prior['candidate_tree'])
    (tmp_path/'cadence-fresh.json').write_text(json.dumps(prior))
    with pytest.raises(ValueError,match='FROZEN_FRESH_SOURCE_REQUIRED'):
        h.cadence_stage_replay(tmp_path,'legacy')
