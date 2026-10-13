"""Host-stage READ_ONLY samples/pure guards; no ROOT or qualification launch."""
import copy
import os
from pathlib import Path

import pytest

from scripts import rc6_capacity_calibration as calibration
from scripts import rc6_g5a_host_capacity as host

ROOT=Path(__file__).absolute().parents[1]


def sample(stage='preparation'):
    return {'stage':stage,'actual_tree_allocated_bytes':0,'review_short_path_bytes':40,
        'original_home_filesystem_matches':True,'filesystem':{'available_bytes':24*host.GIB,
            'total_inodes':100000,'free_inodes':10000,'fragment_bytes':4096}}


def test_source_inventory_reads_actual_original_flags_and_exact_four_review_modules():
    result=host.source_risk_inventory(ROOT)
    assert result['exact_review_modules']==sorted(host.CAS_REVIEW_MODULES)
    assert set(result['source_members_sha256'])==host.SOURCE_MEMBERS
    assert result['original157_source_build_exceptions']==['msgpack','ppi-client','signalrcoreppi','ta']
    assert result['installer_can_execute_locked_source_builds'] is True
    assert result['original_manager_progress_poll_seconds']==5
    assert result['original_failed_phase_cleanup_seconds']==5
    assert result['complete_write_burst_bound_proved'] is False
    assert result['aggregate_native_enforcement_observed'] is False
    assert result['launch_authorized'] is result['G5_GREEN'] is result['qualification_claimed'] is False
    calls={call for values in result['actual_source_call_sites'].values() for call in values}
    assert 'multiprocessing.get_context' in calls and 'shutil.copytree' in calls


@pytest.mark.parametrize('stage',sorted(host.STAGES))
def test_measured_exact_reserves_do_not_make_capacity_or_host_enforcement_green(stage):
    result=host.preflight_observation(sample(stage))
    assert result['hard_stage_bytes']==20*host.GIB and result['physical_reserve_bytes']==4*host.GIB
    assert result['minimum_free_inode_fraction']=='1/10'
    assert result['capacity_launch_authorized'] is False and result['reason']==host.BARRIER
    assert result['G5_GREEN'] is result['qualification_claimed'] is False
    assert all(result[key]==0 for key in ('future_cleanup_credit_bytes','future_GC_credit_bytes','compression_credit_bytes'))


@pytest.mark.parametrize('fault',('one_byte_reserve_short','one_inode_short','one_byte_stage_over',
    'bool_allocated','unknown_fragment','unknown_total','long_review_path','foreign_home','bool_path_length'))
def test_original20_4_inodes10_and50_path_constraints_remain_blocking(fault):
    observed=sample('reader-execution');expected='CALIBRATION_|G5A_HOST_STAGE_'
    if fault=='one_byte_reserve_short':observed['filesystem']['available_bytes']-=1
    elif fault=='one_inode_short':observed['filesystem']['free_inodes']-=1
    elif fault=='one_byte_stage_over':observed['actual_tree_allocated_bytes']=20*host.GIB+1
    elif fault=='bool_allocated':observed['actual_tree_allocated_bytes']=True
    elif fault=='unknown_fragment':observed['filesystem']['fragment_bytes']=0
    elif fault=='unknown_total':observed['filesystem']['total_inodes']=0
    elif fault=='long_review_path':observed['review_short_path_bytes']=51
    elif fault=='foreign_home':observed['original_home_filesystem_matches']=False
    else:observed['review_short_path_bytes']=True
    with pytest.raises(ValueError,match=expected):host.preflight_observation(observed)


@pytest.mark.parametrize('stage',sorted(host.STAGES))
@pytest.mark.parametrize('proof',(None,True,{}, {'status':'GREEN','aggregate_limit_bytes':20*host.GIB},
    {'sampled_maximum':host.GIB,'poll_seconds':5,'actual_FIN':True},
    {'private_ROOT_writer_quota':True,'hard_bytes':20*host.GIB},object()))
def test_owner_flags_polling_samples_and_private_root_quota_never_confer_host_scope(stage,proof):
    with pytest.raises(ValueError,match='^G5A_HOST_STAGE_AGGREGATE_ENFORCEMENT_NOT_VERIFIED$'):
        host.require_host_stage_admission(stage=stage,enforcement=proof,
            owner_acceptance={'revision_approved':True,'can_ignore_guard':True})


def test_readonly_actual_inode_sample_does_not_read_payload_or_follow_external_alias(tmp_path):
    own=tmp_path/'own';own.mkdir(mode=0o700)
    raw=own/'original';raw.write_bytes(b'ORIGINAL'*1024);raw.chmod(0o600)
    foreign=tmp_path/'foreign';foreign.mkdir(mode=0o700);(foreign/'must-not-follow').write_bytes(b'x'*10000)
    alias=own/'lib64';alias.symlink_to(foreign)
    before=raw.lstat();result=host.allocation_observation(own,stage='preparation');after=raw.lstat()
    assert result['actual_retained_entries']==3
    assert result['actual_tree_allocated_bytes']==sum(p.lstat().st_blocks*512 for p in (own,raw,alias))
    assert before.st_atime_ns==after.st_atime_ns and raw.read_bytes()==b'ORIGINAL'*1024
    assert result['metric']=='SAMPLED_ST_BLOCKS_NOT_CONTINUOUS_HIGH_WATER'
    assert result['unlinked_open_bytes_accounted'] is result['launch_authorized'] is result['G5_GREEN'] is False


def test_unlinked_open_allocation_demonstrates_why_sample_is_not_aggregate_enforcement(tmp_path):
    own=tmp_path/'own';own.mkdir(mode=0o700);path=own/'unlinked-original'
    with path.open('xb') as stream:
        stream.write(b'x'*8192);stream.flush();os.fsync(stream.fileno());path.unlink()
        hidden=os.fstat(stream.fileno()).st_blocks*512
        result=host.allocation_observation(own,stage='preparation')
        assert hidden>0 and result['actual_retained_entries']==1
        assert result['actual_tree_allocated_bytes']==own.lstat().st_blocks*512
        assert result['unlinked_open_bytes_accounted'] is False
        with pytest.raises(ValueError,match=host.BARRIER):
            host.require_host_stage_admission(stage='preparation',enforcement=result)


@pytest.mark.parametrize('fault',('allocated','reserve','inodes'))
def test_diagnostic_cutoff_raises_to_original_manager_without_signalling_or_cleanup(fault,tmp_path,monkeypatch):
    observed=sample()
    if fault=='allocated':observed['actual_tree_allocated_bytes']=20*host.GIB+1
    elif fault=='reserve':observed['filesystem']['available_bytes']=4*host.GIB-1
    else:observed['filesystem']['free_inodes']=9999
    monkeypatch.setattr(host,'allocation_observation',lambda *a,**kw:copy.deepcopy(observed))
    monkeypatch.setattr(os,'kill',lambda *a,**kw:pytest.fail('ONLY_ORIGINAL_MANAGER_MAY_SIGNAL'))
    cutoff=host.DiagnosticCutoff(tmp_path,stage='preparation')
    with (tmp_path/'own-log').open('xb') as stream:
        with pytest.raises(ValueError,match='LIVE_SAMPLE_CUTOFF|LIVE_RESERVE_CUTOFF|INODE_CUTOFF'):
            cutoff.progress('poll',42,0,10,stream.fileno())
    assert cutoff.documentary['hard_aggregate_enforcement_proved'] is False
    assert cutoff.documentary['launch_authorized'] is False


def test_cutoff_composes_current_owner_progress_and_cannot_hide_an_unseen_burst(tmp_path,monkeypatch):
    samples=[sample(),sample()];samples[0]['actual_tree_allocated_bytes']=host.GIB
    samples[1]['actual_tree_allocated_bytes']=2*host.GIB
    monkeypatch.setattr(host,'allocation_observation',lambda *a,**kw:samples.pop(0))
    calls=[];cutoff=host.DiagnosticCutoff(tmp_path,stage='preparation',previous=lambda *args:calls.append(args))
    with (tmp_path/'own-log').open('xb') as stream:
        cutoff.progress('started',42,0,10,stream.fileno());cutoff.progress('poll',42,0,10,stream.fileno())
    assert len(calls)==2 and cutoff.documentary['sample_count']==2
    assert cutoff.documentary['sampled_allocated_maximum_bytes']==2*host.GIB
    assert cutoff.documentary['metric']=='SAMPLED_ALLOCATED_MAXIMUM_NOT_CONTINUOUS_PEAK'
    with pytest.raises(ValueError,match=host.BARRIER):
        host.require_host_stage_admission(stage='preparation',enforcement=cutoff.documentary)


def test_one_precise_owner_proposal_preserves_limits_and_requires_real_all_writer_enforcement():
    proposal=host.owner_proposal()
    assert proposal['hard_stage_bytes']==20*host.GIB and proposal['physical_reserve_bytes']==4*host.GIB
    assert proposal['required_scope']==sorted(host.STAGES)
    assert proposal['required_review_modules']==sorted(host.CAS_REVIEW_MODULES)
    assert 'actual EDQUOT control' in proposal['mandatory_native_proof']
    assert proposal['polling_only_contract_acceptable'] is False
    assert proposal['approval_alone_authorizes_launch'] is False
    assert proposal['limits_relaxed'] is proposal['G5_GREEN'] is proposal['qualification_claimed'] is False
