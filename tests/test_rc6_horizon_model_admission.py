"""Cheap physical counterexamples for premature Horizon admission."""
import sys
import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts import rc6_architectural_gates as gates
from scripts import rc6_material_carrier as carrier
from scripts import rc6_material_pr_admission as admission


def private_template_fixture():
    from scripts.rc6_cas_original_comparison import CONTRACT
    prerequisites={'source_sha':'a'*40,'source_tree':'b'*40,'scope':'UNIT_FIXTURE_ONLY'}
    template={'schema':'rc6.original-cas-comparison-admission.v2','source_sha':'a'*40,
        'source_tree':'b'*40,'producer_namespace_root':'OWNED_HORIZON_DATA_ROOT',
        'qualification_scope':'PRIVATE_DEVELOPMENT_ONLY_NOT_G5',
        'original_contract':json.loads(json.dumps(dict(CONTRACT))),'prerequisites':prerequisites,
        'reader_review':{'gate':'G1.311','path':'reader-review.json','sha256':'c'*64},
        'private_producer_ack_review':{'gate':'G1.312','path':'ack-review.json','sha256':'d'*64}}
    f={'WORKSTREAM_ID':'WS-UNIT-ONLY','SESSION_SUCCESSOR':'CODEX_UNIT_ONLY',
        'WRITE_OWNER':'CODEX_UNIT_ONLY','INTEGRATION_OWNER':'CODEX_UNIT_ONLY',
        'DEPLOY_OWNER':'NOT_ACQUIRED','RELEASED':'false','SOURCE_SHA':'a'*40,'SOURCE_TREE':'b'*40,
        'MODE':'PRODUCTION_PAPER / SIMULATION','real_orders_sent':'0','GATES_AUTHORIZED':'Horizon',
        'RC6_MATERIAL_AUTOMATIC_PR_AUTHORIZATION':'APPROVED',
        'RC6_PRIVATE_ORIGINAL_CAS_COMPARISON_AUTHORIZATION':'APPROVED',
        'CAS_COMPARISON_TEMPLATE_JSON':json.dumps(template),
        'PREREQUISITES_MANIFEST_JSON':json.dumps(prerequisites)}
    body='\n'.join(key+'='+value for key,value in f.items())
    twin={'id':17,'body':body,'created_at':'2026-10-10T00:00:00Z','updated_at':'2026-10-10T00:00:00Z',
        'user':{'login':'mbalbo2023'},'issue_url':'https://api.github.com/repos/'+admission.REPO+'/issues/473',
        'html_url':'https://github.com/'+admission.REPO+'/issues/473#issuecomment-17'}
    f.update(OWNER_RECEIPT_473='17',OWNER_RECEIPT_473_SHA256=hashlib.sha256(body.encode()).hexdigest())
    return template,f,twin


def test_private_template_requires_dual_original_authority_without_g5_credit():
    template,f,twin=private_template_fixture()
    def get(path):
        assert path=='/issues/comments/17'
        return twin
    assert admission.private_cas_template_scope(f,'a'*40,'b'*40,'Horizon',get=get)==template
    assert admission.private_cas_template_scope({},'a'*40,'b'*40,'Horizon',
        get=lambda path:pytest.fail('Normal Horizon must not fetch private authority')) is None


@pytest.mark.parametrize('field,value',[
    ('source_sha','e'*40),('source_tree','e'*40),('producer_namespace_root','/foreign'),
    ('qualification_scope','G5_GREEN'),('prerequisites',{'scope':'REBOUND'}),
    ('original_contract',{'archive_limit_bytes':1024}),
])
def test_rebound_private_template_stops_before_any_remote_or_producer(field,value):
    template,f,twin=private_template_fixture();template[field]=value
    f['CAS_COMPARISON_TEMPLATE_JSON']=json.dumps(template)
    with pytest.raises(ValueError,match='PRIVATE_CAS_'):
        admission.private_cas_template_scope(f,'a'*40,'b'*40,'Horizon',
            get=lambda path:pytest.fail('Invalid template must stop before remote/fixtures'))


@pytest.mark.parametrize('change', ['wrong_gate','missing_approval','edited_twin','rebound_twin'])
def test_private_template_cannot_be_enabled_by_single_receipt_or_wrong_gate(change):
    template,f,twin=private_template_fixture();gate='Horizon'
    if change=='wrong_gate':gate='full-gov311'
    if change=='missing_approval':f.pop('RC6_PRIVATE_ORIGINAL_CAS_COMPARISON_AUTHORIZATION')
    if change=='edited_twin':twin['updated_at']='2026-10-10T00:00:01Z'
    if change=='rebound_twin':twin['body']=twin['body'].replace('SOURCE_SHA='+'a'*40,'SOURCE_SHA='+'e'*40)
    with pytest.raises(ValueError,match='PRIVATE_CAS_'):
        admission.private_cas_template_scope(f,'a'*40,'b'*40,gate,get=lambda path:twin)


def test_owned_private_manifest_is_forwarded_byte_exact_without_source_or_fixture_copy(tmp_path):
    from scripts import rc6_authenticated_fixture_lifecycle as owned
    from scripts import rc6_material_horizon as wrapper
    template,_,_=private_template_fixture()
    binding={'candidate_sha':'a'*40,'candidate_tree':'b'*40,'producer':'UNIT_ONLY',
        'attempt_id':'UNIT_ONLY','owner_id':'UNIT_ONLY','runner_class':'DIAGNOSTIC',
        'workload_fingerprint':'e'*64}
    namespace=owned.create_namespace(tmp_path,binding);epoch=namespace.path/'epoch';epoch.mkdir()
    source=tmp_path/'source';source.mkdir()
    args=SimpleNamespace(private_cas_comparison_template=template,repo_root=source,
        source_sha='a'*40,source_tree='b'*40,capacity_peaks={'Horizon-private-original-cas':{'scope':'UNIT_ONLY'}})
    control=carrier.private_cas_control(args,namespace,epoch)
    manifest=json.loads(control['path'].read_bytes())
    assert manifest['producer_namespace_root']==str(epoch/'native-data')
    assert template['producer_namespace_root']=='OWNED_HORIZON_DATA_ROOT'
    assert not (epoch/'native-data').exists() and list(source.iterdir())==[]
    assert wrapper.comparison_command_args(control['path'],control['sha256'],source_root=source,
        data_root=epoch/'native-data',source_sha='a'*40,source_tree='b'*40)==[
            '--cas-comparison-manifest',str(control['path'])]
    with pytest.raises(ValueError,match='PRIVATE_CAS_MANIFEST_BYTES_OR_HASH_CHANGED'):
        wrapper.comparison_command_args(control['path'],'f'*64,source_root=source,
            data_root=epoch/'native-data',source_sha='a'*40,source_tree='b'*40)
    assert not (epoch/'native-data').exists()


def test_normal_horizon_peak_cannot_authorize_private_comparison_storage(tmp_path):
    from scripts import rc6_authenticated_fixture_lifecycle as owned
    template,_,_=private_template_fixture()
    binding={'candidate_sha':'a'*40,'candidate_tree':'b'*40,'producer':'UNIT_ONLY',
        'attempt_id':'UNIT_ONLY','owner_id':'UNIT_ONLY','runner_class':'DIAGNOSTIC','workload_fingerprint':'e'*64}
    namespace=owned.create_namespace(tmp_path,binding);epoch=namespace.path/'epoch';epoch.mkdir()
    source=tmp_path/'source';source.mkdir()
    args=SimpleNamespace(private_cas_comparison_template=template,repo_root=source,
        source_sha='a'*40,source_tree='b'*40,capacity_peaks={'Horizon':{'scope':'UNIT_ONLY'}})
    with pytest.raises(ValueError,match='PRIVATE_CAS_AGGREGATE_COMPARISON_PEAK_REQUIRED'):
        carrier.private_cas_control(args,namespace,epoch)
    assert not (epoch/'private-cas-admission.json').exists()
    assert carrier.workload_fingerprint('Horizon')!=carrier.workload_fingerprint('Horizon-private-original-cas')


REASON = 'HORIZON_ALL1202_PHYSICAL_MODEL_NO_VERIFICADO_BEFORE_MATERIAL'


@pytest.mark.parametrize('gate', ['Horizon', 'G5'])
def test_partial_closed_samples_cannot_admit_the_original_horizon(gate):
    with pytest.raises(ValueError, match=REASON):
        gates.require_horizon_model_before_material(gate)


def test_a_caller_cannot_rewrite_the_model_hold_in_place():
    with pytest.raises(TypeError):
        gates.HORIZON_MODEL_CLOSURE['global_retained_footprint_proved'] = True


@pytest.mark.parametrize('name,value', [
    ('HORIZON_MODEL_CLOSED', 'true'),
    ('HORIZON_CAPACITY_PROOF_SHA256', 'a' * 64),
    ('HORIZON_CAPACITY_FORECAST_BYTES', '499376128'),
    ('HORIZON_ALL1202_PROVED', 'true'),
    ('RC6_MATERIAL_AUTOMATIC_PR_AUTHORIZATION', 'APPROVED'),
    ('GATES_AUTHORIZED', 'Horizon'),
])
def test_environment_or_a_partial_forecast_does_not_authorize_horizon(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=REASON):
        gates.require_horizon_model_before_material('Horizon')


def test_admission_stops_before_event_api_and_source_reads(monkeypatch, tmp_path):
    marker = tmp_path / 'expensive-admission-started'

    def forbidden(*args, **kwargs):
        marker.write_text('called')
        raise AssertionError('event/API/Source admission must not begin')

    for key, value in {
        'GITHUB_REPOSITORY': admission.REPO,
        'GITHUB_REPOSITORY_ID': str(admission.REPO_ID),
        'GITHUB_RUN_ATTEMPT': '1',
    }.items():
        monkeypatch.setenv(key, value)
    for name in ('actual_event', 'fresh_source', 'api'):
        monkeypatch.setattr(admission, name, forbidden)
    with pytest.raises(ValueError, match=REASON):
        admission.admit(source_sha='a' * 40, source_tree='b' * 40, gate='Horizon',
                        launch_receipt_url='https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-1',
                        owner_session=admission.SUCCESSOR_OWNER)
    assert not marker.exists()


def test_direct_horizon_entry_cannot_export_or_launch_a_producer(monkeypatch, tmp_path):
    marker = tmp_path / 'producer-started'

    def forbidden(*args, **kwargs):
        marker.write_text('called')
        raise AssertionError('export/producer must not begin')

    monkeypatch.setattr(carrier, 'exported', forbidden)
    with pytest.raises(ValueError, match=REASON):
        carrier.horizon(None, forbidden, None, None, None)
    assert not marker.exists()


def test_cli_stops_before_bootstrap_fetch_namespace_or_tooling(monkeypatch, tmp_path):
    marker = tmp_path / 'bootstrap-started'

    def forbidden(*args, **kwargs):
        marker.write_text('called')
        raise AssertionError('bootstrap must not begin')

    monkeypatch.setattr(carrier, 'bootstrap', forbidden)
    monkeypatch.setattr(sys, 'argv', ['rc6_material_carrier.py', '--repo-root', str(tmp_path),
        '--source-sha', 'a' * 40, '--source-tree', 'b' * 40, '--launch-receipt-url',
        'https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-1',
        '--owner-session', admission.SUCCESSOR_OWNER, '--python311', sys.executable,
        '--python312', sys.executable, '--gate', 'Horizon', '--require-pr-admission'])
    with pytest.raises(ValueError, match=REASON):
        carrier.main()
    assert not marker.exists()
