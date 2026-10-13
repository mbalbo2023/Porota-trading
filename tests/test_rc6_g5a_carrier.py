"""Pure carrier/format regressions; no namespace/image/ROOT/native launch."""
import ast
import copy
from pathlib import Path

import pytest

from scripts import rc6_authenticated_fixture_lifecycle as owned
from scripts import rc6_capacity_calibration as calibration
from scripts import rc6_ephemeral_api_credential as credential
from scripts import rc6_g5a_carrier as carrier
from scripts import rc6_g5a_native_runner as runner
from scripts import rc6_material_pr_admission as admission
from scripts import rc6_privileged_custody as custody
from tests.test_rc6_g5a_native_runner import root_request as declared_native_request


def constructor_arguments(request):
    request['namespace_receipt']['identity']=[27,11,request['owner_uid'],request['owner_gid'],0o40700]
    inputs={key:request[key] for key in carrier.INPUT_FIELDS-{'filter_plan_sha256'}}
    inputs['filter_plan_sha256']=request['filter_binding']['plan_sha256']
    return {'binding':request['binding'],'namespace_receipt':request['namespace_receipt'],
        'inputs':inputs,'image_identity':request['image_identity'],'loop_birth_identity':request['loop_birth_identity'],
        'loop_birth_pending_sha256':request['loop_birth_pending_sha256'],'code_hashes':request['code_hashes'],
        'live':{key:request[key] for key in carrier.LIVE_FIELDS}}


def test_exact_request_matches_source_fixed_runner_and_has_no_token_or_authority(declared_native_request):
    row=carrier.root_request(**constructor_arguments(declared_native_request))
    assert row==declared_native_request
    assert set(row)==runner.REQUEST_FIELDS and row['require_quota_ancestor_seal'] is True
    assert row['operation_scope']=='G5A_EVIDENCE_ACQUISITION_ONLY' and 'mode' not in row
    assert not any('TOKEN' in key.upper() or 'SECRET' in key.upper() for key in row)


@pytest.mark.parametrize('fault',('TOKEN','accepted','skip_G0','resource_limits','foreign_loop','ENOENT','private_pid_ready'))
def test_request_has_no_boolean_token_or_alternate_setup_inputs(declared_native_request,fault):
    arguments=constructor_arguments(declared_native_request);arguments['inputs']=copy.deepcopy(arguments['inputs'])
    arguments['inputs'][fault]=True
    with pytest.raises(ValueError,match='CLOSED_REQUEST_INPUTS_REQUIRED'):
        carrier.root_request(**arguments)


@pytest.mark.parametrize('field,value',(('g5a_revision_owner','foreign'),('source_root','/foreign'),
    ('g5a_revision_approval_url','https://github.com/foreign/repo/issues/471#issuecomment-1'),
    ('g5a_prerequisites_manifest_sha256','unknown'),('filter_plan_sha256','unknown')))
def test_root_request_cannot_rebind_accepted_source_owner_or_inputs(declared_native_request,field,value):
    arguments=constructor_arguments(declared_native_request);arguments['inputs']=copy.deepcopy(arguments['inputs'])
    arguments['inputs'][field]=value
    if field=='source_root':
        # A different valid literal Source root is shape-only; a real launch
        # independently requires opaque complete Git/export registry proof.
        row=carrier.root_request(**arguments);assert row['source_root']=='/foreign'
    else:
        with pytest.raises(ValueError,match='G5A_RUNNER_'):
            carrier.root_request(**arguments)


def observation(free_bytes,*,free_inodes=100000,total_inodes=100000):
    return {'available_bytes':free_bytes,'free_inodes':free_inodes,'total_inodes':total_inodes,'fragment_bytes':4096}


def test_physical_initial_phase_charges_full_image_stream_controls_and_original_reserves():
    bound=carrier.control_bound(4096)
    measured=observation(26*carrier.GIB+bound['total_bytes']+4*carrier.GIB)
    assert carrier.preflight(measured)==bound
    assert bound['components']['closed_original_stream_bytes']==20*carrier.GIB
    assert bound['physical_reserve_bytes']==4*carrier.GIB
    assert all(bound[name]==0 for name in ('future_cleanup_credit_bytes','future_GC_credit_bytes','compression_credit_bytes'))
    assert bound['archive_hard_bytes']==512*carrier.MIB and bound['workload_project_hard_bytes']==20*carrier.GIB


@pytest.mark.parametrize('fault',('one_byte_short','inode_reserve','unknown_fragment','unknown_total'))
def test_preflight_rejects_unknown_or_insufficient_actual_capacity_before_fixture(fault):
    bound=carrier.control_bound(4096)
    measured=observation(26*carrier.GIB+bound['total_bytes']+4*carrier.GIB)
    if fault=='one_byte_short':measured['available_bytes']-=1
    elif fault=='inode_reserve':measured['free_inodes']=9999
    elif fault=='unknown_fragment':measured['fragment_bytes']=0
    else:measured['total_inodes']=0
    with pytest.raises(ValueError,match='CALIBRATION_|G5A_CARRIER_'):
        carrier.preflight(measured)


def test_capture_phase_charges_one_full_future_copy_against_actual_post_retirement_free_bytes():
    bound=carrier.control_bound(4096,capture_phase=True)
    assert carrier.preflight(observation(bound['total_bytes']+4*carrier.GIB+1),capture_phase=True)==bound
    assert bound['future_cleanup_credit_bytes']==0 and bound['compression_credit_bytes']==0
    assert bound['phase']=='POST_ACTUAL_BACKING_RETIREMENT_CAPTURE'
    # This pure capacity function cannot grant backing-retirement authority.
    assert 'actual_image_retirement_proved' not in bound


@pytest.mark.parametrize('value',(None,True,{}, {'admitted':True},object.__new__(admission.G5ASetupLaunchContext)))
def test_serialized_or_type_only_source_context_cannot_reach_fixtures(value,monkeypatch):
    monkeypatch.setattr(owned,'create_namespace',lambda *a,**kw:pytest.fail('NO_NAMESPACE_WITHOUT_SOURCE_AUTHORITY'))
    monkeypatch.setattr(custody,'service_command',lambda *a,**kw:pytest.fail('NO_ROOT_WITHOUT_SOURCE_AUTHORITY'))
    with pytest.raises(ValueError,match='G5A_CARRIER_|G5A_SETUP_CURRENT_TYPED_'):
        carrier._context(value)


@pytest.mark.parametrize('value',(None,True,{}, {'source_sha':'a'*40},object.__new__(carrier.G5ASetupLaunch)))
def test_carrier_serialization_cannot_recreate_current_launch_authority(value,monkeypatch):
    monkeypatch.setattr(owned,'create_namespace',lambda *a,**kw:pytest.fail('NO_NAMESPACE_WITHOUT_LAUNCH_AUTHORITY'))
    monkeypatch.setattr(credential,'create',lambda *a,**kw:pytest.fail('NO_CREDENTIAL_WITHOUT_LAUNCH_AUTHORITY'))
    with pytest.raises(ValueError,match='G5A_CARRIER_CURRENT_IN_PROCESS_'):
        carrier.launch(value,token=b'DECLARED_TEST_BYTES')


def test_launch_factory_context_and_original_raw_barriers_precede_any_fixture_operation():
    """Source graph regression protects the concrete two-stage launcher."""
    module=ast.parse(Path(carrier.__file__).read_text())
    functions={node.name:node for node in module.body if isinstance(node,ast.FunctionDef)}
    def calls(node):
        return sorted((child.lineno,ast.unparse(child.func)) for child in ast.walk(node) if isinstance(child,ast.Call))
    factory=calls(functions['authenticate_setup_launch']);factory_names=[name for _,name in factory]
    assert 'owned.create_namespace' not in factory_names and 'os.posix_fallocate' not in factory_names
    assert factory_names.index('_context')<factory_names.index('gates.verify_manifest')
    assert factory_names.index('gates.verify_manifest')<factory_names.index('gates.verify_capacity_bootstrap_artifact')
    launch=calls(functions['launch']);launch_names=[name for _,name in launch]
    assert launch_names.index('_registered')<launch_names.index('owned.create_namespace')
    assert launch_names.index('preflight')<launch_names.index('owned.create_namespace')<launch_names.index('os.posix_fallocate')
    assert launch_names.index('monitor.prelaunch')<launch_names.index('os.posix_fallocate')
    assert launch_names.index('credential.create')<launch_names.index('privileged.service_command')
    assert launch_names.index('privileged.execute_service')<launch_names.index('privileged._closed_service')<launch_names.index('monitor.after_fin')
    assert not any(name in launch_names for name in ('owned.cleanup_namespace','os.unlink','Path.unlink','runner.root_worker'))


def test_ephemeral_memfd_is_closed_on_property_or_native_failure_and_never_enters_child_environment():
    module=ast.parse(Path(carrier.__file__).read_text())
    launch=next(node for node in module.body if isinstance(node,ast.FunctionDef) and node.name=='launch')
    guarded=next(node for node in ast.walk(launch) if isinstance(node,ast.Try)
        and any(isinstance(child,ast.Call) and ast.unparse(child.func)=='privileged.service_command'
            for statement in node.body for child in ast.walk(statement)))
    assert any(isinstance(child,ast.Call) and ast.unparse(child.func)=='keeper.close'
        for statement in guarded.finalbody for child in ast.walk(statement))
    execute=next(node for node in ast.walk(launch) if isinstance(node,ast.Call)
        and ast.unparse(node.func)=='privileged.execute_service')
    environment=next(item.value for item in execute.keywords if item.arg=='environ')
    assert {'PATH','RUNNER_TOOL_CACHE','LANG','LC_ALL'}=={
        node.value for node in ast.walk(environment) if isinstance(node,ast.Constant) and isinstance(node.value,str)}
