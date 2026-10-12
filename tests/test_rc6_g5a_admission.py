"""Economic contract/control regressions; no ROOT or native gate is qualified."""
from datetime import timedelta
from types import SimpleNamespace

import pytest

from scripts import rc6_g5_contract as g5
from scripts import rc6_material_pr_admission as admission
from tests import test_rc6_contract_revisions as decisions


def accepted(monkeypatch):
    binding = {**decisions.BINDING, 'revision': g5.REVISION, 'scope': g5.SCOPE,
               'contract_sha256': g5.CONTRACT_SHA256}
    monkeypatch.setattr(decisions, 'BINDING', binding)
    fixture = decisions.DecisionFixture(monkeypatch)
    return fixture, fixture.issue(), binding


def test_fresh_contract_selects_pending_native_capacity_without_claiming_complete_horizon(monkeypatch):
    _fixture, context, binding = accepted(monkeypatch)
    result = admission.g5a_preparation_capacity(context, source_sha=binding['source_sha'],
        source_tree=binding['source_tree'], now=decisions.NOW)
    assert result['backing_image_bytes'] == 26 * 1024**3
    assert result['workload_project_hard_bytes'] == result['closed_raw_stream_hard_bytes'] == 20 * 1024**3
    assert result['archive_hard_bytes'] == 512 * 1024**2
    assert result['residual_reserve_bytes'] == 4 * 1024**3
    assert result['minimum_free_inode_fraction'] == '1/10'
    assert result['G0_G4_actual_artifacts_required'] and result['accepted_5_512_then_26_20_required']
    assert not any(result[name] for name in ('native_quota_verified', 'capacity_admitted',
        'G5_GREEN', 'original_RC6_stage_complete_claimed'))
    assert result['future_cleanup_credit_bytes'] == result['future_GC_credit_bytes'] == 0


@pytest.mark.parametrize('forged', [None, True, {}, {'APPROVED': True}])
def test_pending_capacity_cannot_be_selected_by_json_or_flags(forged):
    with pytest.raises(ValueError, match='AUTHENTICATED_OWNER_ACCEPTANCE'):
        admission.g5a_preparation_capacity(forged, source_sha='a'*40, source_tree='b'*40,
                                         now=decisions.NOW)


@pytest.mark.parametrize('field', ['source_sha', 'source_tree'])
def test_acquisition_capacity_cannot_borrow_other_source_acceptance(monkeypatch, field):
    _fixture, context, binding = accepted(monkeypatch)
    values = {name: binding[name] for name in ('source_sha', 'source_tree')}
    values[field] = 'd'*40
    with pytest.raises(ValueError, match='BINDING_REBOUND'):
        admission.g5a_preparation_capacity(context, now=decisions.NOW, **values)


def test_expired_initial_acceptance_never_admits_a_new_acquisition(monkeypatch):
    _fixture, context, binding = accepted(monkeypatch)
    with pytest.raises(ValueError, match='EXPIRED'):
        admission.g5a_preparation_capacity(context, source_sha=binding['source_sha'],
            source_tree=binding['source_tree'], now=decisions.NOW + timedelta(minutes=11))


def test_ordinary_horizon_admission_still_blocks_before_source_or_api(monkeypatch):
    monkeypatch.setenv('GITHUB_REPOSITORY', admission.REPO)
    monkeypatch.setenv('GITHUB_REPOSITORY_ID', str(admission.REPO_ID))
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '1')
    monkeypatch.setattr(admission, '_admit_source_scope',
        lambda **kwargs: pytest.fail('ordinary hold must precede Source/API reads'))
    with pytest.raises(ValueError, match='HORIZON_ALL1202_PHYSICAL_MODEL_NO_VERIFICADO_BEFORE_MATERIAL'):
        admission.admit(source_sha='a'*40, source_tree='b'*40, gate='Horizon')


@pytest.mark.parametrize('context', [None, True, {}, SimpleNamespace(pid=1)])
def test_serialized_setup_decision_never_reconstructs_launch_context(context):
    with pytest.raises(ValueError, match='CURRENT_TYPED_SOURCE_STAGE_CONTEXT'):
        admission.validate_g5a_setup_launch(context, source_sha='a'*40, source_tree='b'*40,
            owner_session='UNIT_ONLY', launch_receipt_url=decisions.URL, now=decisions.NOW)


def test_public_and_object_constructor_cannot_create_setup_launch_authority():
    with pytest.raises(ValueError, match='FACTORY'):
        admission.G5ASetupLaunchContext()
    forged = object.__new__(admission.G5ASetupLaunchContext)
    with pytest.raises(ValueError, match='CURRENT_TYPED_SOURCE_STAGE_CONTEXT'):
        admission.validate_g5a_setup_launch(forged, source_sha='a'*40, source_tree='b'*40,
            owner_session='UNIT_ONLY', launch_receipt_url=decisions.URL)


@pytest.mark.parametrize('name,value', [('GITHUB_EVENT_NAME','pull_request'),
    ('GITHUB_RUN_ATTEMPT','2'), ('GITHUB_ACTIONS','false')])
def test_untrusted_setup_event_fails_before_contract_or_source_reads(monkeypatch, name, value):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'workflow_dispatch')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '1')
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(admission, 'api', lambda path: pytest.fail('untrusted event must not read API'))
    with pytest.raises(ValueError, match='ACTUAL_FIRST_MANUAL_ACTIONS'):
        admission.admit_g5a_preparation(source_sha='a'*40, source_tree='b'*40,
            owner_session='UNIT_ONLY', launch_receipt_url=decisions.URL,
            revision_approval_url=decisions.URL, now=decisions.NOW)
