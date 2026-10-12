"""NONROOT one-shot G5A launcher under the original Actions control plane.

The typed Source/owner context is issued only by material admission. Actual
G0--G4 artifacts and the accepted5/512 then26/20 artifacts are authenticated
before an image. This launcher supplies no G5 GREEN, cleanup or deployment
authority. The separate Source parser reads the large stream only after the
original outer, ROOT worker and external guardian FIN.
"""
from __future__ import annotations

from datetime import datetime, timezone
import ctypes
import hashlib
import importlib.metadata
import os
from pathlib import Path
import platform
import resource
import stat
import sys
import time
import math

ROOT=Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts import rc6_capacity_calibration as calibration
from scripts import rc6_g5a_native_runner as runner
from scripts import rc6_g5_contract as g5

MEMBER='scripts/rc6_g5a_carrier.py'
GIB,MIB=1024**3,1024**2
_LAUNCHES={}
_NATIVE_RESULTS={}
_KEY=object()
INPUT_FIELDS=frozenset({'source_root','source_repo','source_index','source_index_sha256','python311',
    'interpreter_manifest_path','interpreter_manifest_sha256','g5a_prerequisites_manifest_path',
    'g5a_prerequisites_manifest_sha256','cas_comparison_manifest_path','cas_comparison_manifest_sha256',
    'g5a_revision_approval_url','g5a_revision_owner','filter_plan_sha256'})
LIVE_FIELDS=frozenset({'mount_namespace_inode','host_PID1','host_boundary','issuer','boundary_host_proc_device'})


def require(value,reason):
    if not value:raise ValueError(reason)


def clone(value):return calibration.decode(calibration.wire(value))


def control_bound(fragment_bytes,*,capture_phase=False):
    """One physical stream now; its copy only after actual backing retirement.

    The initial phase has no credit from a future unlink, GC or compression.
    The capture phase must independently measure actual free bytes AFTER the
    separate controller proves kernel release and retires its own image.
    """
    require(type(fragment_bytes) is int and 0<fragment_bytes<=65536,
        'G5A_CARRIER_ACTUAL_FILESYSTEM_FRAGMENT_REQUIRED')
    require(type(capture_phase) is bool,'G5A_CARRIER_FIXED_CAPACITY_PHASE_REQUIRED')
    components={'closed_original_stream_bytes':runner.MAX_STREAM_BYTES,
        'small_custody_and_continuation_controls_bytes':128*MIB,
        'capture_manifests_and_terminal_controls_bytes':16*MIB,
        'physical_rounding_and_directories_bytes':(4*runner.MAX_STREAM_ENTRIES+256)*fragment_bytes}
    return {'schema':'rc6.g5a-physical-control-bound.v1',
        'phase':'POST_ACTUAL_BACKING_RETIREMENT_CAPTURE' if capture_phase else 'BEFORE_ORIGINAL_IMAGE',
        'components':components,'total_bytes':sum(components.values()),
        'stream_copy_charged_only_against_actual_post_retirement_free_bytes':capture_phase,
        'future_cleanup_credit_bytes':0,'future_GC_credit_bytes':0,'compression_credit_bytes':0,
        'workload_project_hard_bytes':20*GIB,'archive_hard_bytes':512*MIB,
        'physical_reserve_bytes':4*GIB,'minimum_free_inode_fraction':'1/10'}


def preflight(observation,*,capture_phase=False):
    bound=control_bound(observation.get('fragment_bytes'),capture_phase=capture_phase)
    calibration.require_capacity(observation,1 if capture_phase else runner.LIMITS['backing_image_bytes'],
        control_bytes=bound['total_bytes'])
    return bound


def root_request(*,binding,namespace_receipt,inputs,image_identity,loop_birth_identity,
        loop_birth_pending_sha256,code_hashes,live):
    """Pure exact request construction; returned JSON grants no launch authority."""
    require(type(inputs) is dict and set(inputs)==INPUT_FIELDS
        and type(live) is dict and set(live)==LIVE_FIELDS,'G5A_CARRIER_CLOSED_REQUEST_INPUTS_REQUIRED')
    require(type(namespace_receipt) is dict and type(namespace_receipt.get('identity')) is list
        and len(namespace_receipt['identity'])==5
        and all(type(value) is int for value in namespace_receipt['identity'])
        and namespace_receipt['identity'][2]>0 and namespace_receipt['identity'][3]>0,
        'G5A_CARRIER_ORIGINAL_NONROOT_NAMESPACE_IDENTITY_REQUIRED')
    namespace=Path(namespace_receipt['path']);project=namespace/'mount/project'
    request={'schema':runner.REQUEST_SCHEMA,'operation_scope':runner.OPERATION_SCOPE,
        'limits':dict(runner.LIMITS),'g5a_quotas':dict(runner.QUOTAS),
        'owner_uid':namespace_receipt['identity'][2],'owner_gid':namespace_receipt['identity'][3],
        'source_sha':binding['candidate_sha'],'source_tree':binding['candidate_tree'],
        'binding':clone(binding),'namespace_receipt':clone(namespace_receipt),
        'image':str(namespace/'image.ext4'),'image_identity':clone(image_identity),
        'mountpoint':str(namespace/'mount'),'code_hashes':dict(code_hashes),
        'loop_birth_path':str(namespace/'loop-birth.json'),'loop_birth_identity':clone(loop_birth_identity),
        'loop_birth_pending_sha256':loop_birth_pending_sha256,'boundary_revision':'PRIVATE_PID_BOUNDARY_V2',
        'require_quota_ancestor_seal':True,'g5a_contract_sha256':g5.CONTRACT_SHA256,
        'g5a_oracle_metadata_path':str(namespace/'g5a-owner-oracle.json'),
        'original_workload_root':str(project),**clone(live),
        **{key:value for key,value in inputs.items() if key!='filter_plan_sha256'},
        'filter_binding':{'source_sha':binding['candidate_sha'],'source_tree':binding['candidate_tree'],
            'plan_sha256':inputs['filter_plan_sha256']}}
    runner.validate_request_shape(request)
    require(len(calibration.wire(request))<=runner.MAX_REQUEST,'G5A_CARRIER_ROOT_REQUEST_METADATA_LIMIT')
    return request


def _owned_directory(path):
    path=runner.canonical_path(str(path));info=path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.geteuid() and info.st_gid==os.getegid()
        and stat.S_IMODE(info.st_mode)==0o700,'G5A_CARRIER_OWN_PRIVATE_CONTROL_DIRECTORY_REQUIRED')
    return path


def _context(context):
    from scripts import rc6_material_pr_admission as admission
    require(type(context) is admission.G5ASetupLaunchContext,
        'G5A_CARRIER_ACTUAL_TYPED_SOURCE_API_SETUP_CONTEXT_REQUIRED')
    return admission.validate_g5a_setup_launch(context,source_sha=getattr(context,'source_sha',None),
        source_tree=getattr(context,'source_tree',None),owner_session=getattr(context,'owner_session',None),
        launch_receipt_url=getattr(context,'launch_receipt_url',None))


def _owner(context,get=None):
    from scripts import rc6_material_pr_admission as admission
    row=_context(context)
    current=admission.owned_gate_control_scope(source_sha=context.source_sha,source_tree=context.source_tree,
        owner_session=context.owner_session,launch_receipt_url=context.launch_receipt_url,gate='Horizon',
        now=datetime.now(timezone.utc),get=get or admission.api)
    require(current.get('source_sha')==row['source_sha'] and current.get('source_tree')==row['source_tree']
        and current.get('launch_body_sha256')==row['launch_body_sha256']
        and current.get('run_attempt')==1,'G5A_CARRIER_CURRENT_EXCLUSIVE_SOURCE_OWNER_REBOUND')
    return current


def _preexisting_source(*,context,source_root,source_repo,source_index,python311,interpreter_manifest):
    """Actual executing PRODUCT311 + whole original Git/export proof, no install."""
    from scripts import rc6_material_horizon as horizon
    source,repo,index,binary,manifest=map(lambda p:runner.canonical_path(str(p)),
        (source_root,source_repo,source_index,python311,interpreter_manifest))
    require(repo==ROOT and Path(__file__).absolute()==ROOT/MEMBER and source!=repo
        and not source.is_relative_to(repo) and not repo.is_relative_to(source),
        'G5A_CARRIER_EXECUTED_EXPORT_AND_DISTINCT_FULL_GIT_REQUIRED')
    require(index.name=='source.index.json' and not index.is_relative_to(source)
        and not manifest.is_relative_to(source) and not binary.is_relative_to(source),
        'G5A_CARRIER_EXTERNAL_EXISTING_SOURCE_INPUTS_REQUIRED')
    index_raw=runner.read_input(index);pin=calibration.decode(index_raw)
    actual,proof=horizon.verify_source(repo,source,context.source_sha,context.source_tree,pin)
    require(horizon.git(repo,'rev-parse','HEAD','HEAD^{tree}','--is-shallow-repository').splitlines()==
        [context.source_sha.encode(),context.source_tree.encode(),b'false']
        and not os.path.lexists(repo/'.git/objects/info/alternates'),
        'G5A_CARRIER_PREEXISTING_FULL_GIT_HEAD_TREE_HISTORY_REQUIRED')
    require(set(runner.CODE_MEMBERS)<=set(actual)
        and all(actual[name]['sha256']==calibration.digest(runner.read_input(source/name,
            maximum=calibration.CONTROLS)) for name in runner.CODE_MEMBERS),
        'G5A_CARRIER_EXECUTED_CLOSED_SOURCE_MEMBERS_REBOUND')
    require(sys.executable==str(binary) and platform.python_version()=='3.11.16'
        and platform.system()=='Linux' and platform.machine()=='x86_64'
        and platform.libc_ver()[0]=='glibc' and tuple(map(int,platform.libc_ver()[1].split('.')))>=(2,36)
        and sys.prefix!=sys.base_prefix and binary.is_relative_to(Path(sys.prefix)),
        'G5A_CARRIER_PREEXISTING_CURRENT_PRODUCT311_PROCESS_REQUIRED')
    manifest_raw=runner.read_input(manifest);declared=calibration.decode(manifest_raw)
    info=binary.lstat();packages=[[row.metadata['Name'],row.version] for row in importlib.metadata.distributions()]
    policy=calibration.decode(runner.read_input(source/'ops/policy/rc6-supply-chain-v1.json'))
    calibration.validate_product_closure(packages,policy)
    require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and stat.S_IMODE(info.st_mode)==0o755
        and (info.st_uid,info.st_gid)==(os.geteuid(),os.getegid())
        and type(declared) is dict and declared.get('schema')=='rc6.g5a-preexisting-product157-interpreter.v1'
        and declared.get('source_sha')==context.source_sha and declared.get('source_tree')==context.source_tree
        and declared.get('binding')==context.binding and declared.get('python_epoch')=='311'
        and declared.get('python_version')==platform.python_version() and declared.get('executable')==sys.executable
        and declared.get('prefix')==sys.prefix and declared.get('base_prefix')==sys.base_prefix
        and type(declared.get('installed_distribution_count')) is int and declared['installed_distribution_count']==157
        and declared.get('identity')==calibration.identity(info)
        and declared.get('executable_sha256')==calibration.digest(runner.read_input(binary,maximum=128*MIB))
        and declared.get('source_lock_sha256')=={name:calibration.digest(runner.read_input(source/name))
            for name in runner.SOURCE_LOCKS},'G5A_CARRIER_PREEXISTING_PRODUCT157_MANIFEST_OR_BINARY_REBOUND')
    for name,key in (('requirements.lock.txt','packages'),('requirements.build.lock.txt','build_tools')):
        locks=horizon.lock_rows(runner.read_input(source/name))
        expected={horizon.normalized(row['name']):row for row in policy[key]}
        require(set(locks)==set(expected) and all(locks[item]['version']==expected[item]['version']
            and locks[item]['hashes']=={row['sha256'] for row in expected[item]['distributions']}
            for item in locks),'G5A_CARRIER_ORIGINAL_HASH_LOCKED_PRODUCT157_REQUIRED')
    return {'source_root':str(source),'source_repo':str(repo),'source_index':str(index),
        'source_index_sha256':calibration.digest(index_raw),'python311':str(binary),
        'interpreter_manifest_path':str(manifest),'interpreter_manifest_sha256':calibration.digest(manifest_raw)},proof


class G5ASetupLaunch:
    """Same-process Source-issued one-shot control; never reconstructable JSON."""
    __slots__=('_key',)
    def __new__(cls,*args,**kwargs):raise ValueError('G5A_CARRIER_AUTHENTICATED_FACTORY_REQUIRED')
    def __setattr__(self,name,value):raise AttributeError('G5A_CARRIER_AUTHORITY_IMMUTABLE')
    @property
    def documentary(self):return clone(_registered(self)['documentary'])


def _birth():
    return {'pid':os.getpid(),'start_ticks':Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19],
        'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'uid':os.geteuid(),'gid':os.getegid()}


def _registered(authority):
    state=_LAUNCHES.get(id(authority))
    require(type(authority) is G5ASetupLaunch and state is not None and state['object'] is authority
        and authority._key is _KEY and state['birth']==_birth(),
        'G5A_CARRIER_CURRENT_IN_PROCESS_SETUP_AUTHORITY_REQUIRED')
    require(not state['consumed'],'G5A_CARRIER_SETUP_AUTHORITY_ALREADY_CONSUMED')
    return state


def _native_snapshot(result):
    return calibration.wire({key:result[key] for key in ('kernel','request','custody_request',
        'service_state','actual_root_service_FIN','operation_scope','G5_GREEN','qualification_claimed',
        'real_orders_sent','large_stream_parsed','backing_retirement_performed','cleanup_authority_granted',
        'operation_deadline_monotonic')})


def validate_native_result(result):
    """Validate actual same-process FIN issuance; JSON cannot recreate it."""
    from scripts import rc6_authenticated_fixture_lifecycle as owned
    registered=_NATIVE_RESULTS.get(id(result))
    require(type(result) is dict and registered is not None and registered['object'] is result
        and registered['birth']==_birth()
        and all(result.get(key) is value for key,value in registered['handles'].items())
        and _native_snapshot(result)==registered['snapshot'],
        'G5A_CARRIER_CURRENT_SOURCE_ISSUED_NATIVE_RESULT_REQUIRED')
    owned.require_fin(result['namespace'],result['fin'])
    require(result['kernel']==result['fin'].kernel,'G5A_CARRIER_ORIGINAL_NATIVE_FIN_KERNEL_REBOUND')
    return result


def authenticate_setup_launch(context,*,source_root,source_repo,source_index,python311,interpreter_manifest,
        prerequisites_manifest,parent,controls,filter_plan_sha256,get=None,download=None):
    """Authenticate actual inputs and both quota artifacts before any fixture."""
    from scripts import rc6_architectural_gates as gates
    require(os.getuid()==os.geteuid()>0 and os.getgid()==os.getegid()>0
        and len(os.listdir('/proc/self/task'))==1 and os.environ.get('GITHUB_ACTIONS')=='true',
        'G5A_CARRIER_ACTUAL_SINGLE_THREAD_NONROOT_ACTIONS_REQUIRED')
    row=_context(context);_owner(context,get)
    from scripts import rc6_g5a_source_context as export
    exported_source_context=export.observe_export(source_repo=source_repo,source_root=source_root,index=source_index,
        source_sha=context.source_sha,source_tree=context.source_tree,binding=context.binding)
    inputs,source_proof=_preexisting_source(context=context,source_root=source_root,source_repo=source_repo,
        source_index=source_index,python311=python311,interpreter_manifest=interpreter_manifest)
    parent=_owned_directory(parent);controls=_owned_directory(controls)
    require(parent!=controls and not parent.is_relative_to(controls) and not controls.is_relative_to(parent)
        and all(not parent.is_relative_to(Path(inputs[key])) and not controls.is_relative_to(Path(inputs[key]))
            for key in ('source_root','source_repo')),'G5A_CARRIER_SOURCE_CONTROL_NAMESPACE_OVERLAP')
    original=runner.read_input(prerequisites_manifest)
    manifest=calibration.decode(original)
    require(manifest==row.get('prerequisites_manifest'),'G5A_CARRIER_OWNER_ORIGINAL_G0_G4_MANIFEST_REBOUND')
    prerequisites=gates.verify_manifest(manifest,source_sha=context.source_sha,source_tree=context.source_tree,
        target_gate='G5',evidence_root=controls/'g0-g4-raw',get=get,download=download)
    require(prerequisites.get('status')=='GREEN' and prerequisites.get('target_gate')=='G5',
        'G5A_CARRIER_ORIGINAL_G0_G4_RAW_NOT_VERIFIED')
    bootstrap=gates.verify_capacity_bootstrap_artifact(row['g5a_bootstrap_reference'],
        capability_reference=row['g5a_capability_reference'],source_sha=context.source_sha,
        source_tree=context.source_tree,read_contract_sha256=gates.productive_contract(ROOT),
        evidence_root=controls/'accepted-capability-bootstrap',get=get,download=download,
        authenticated_branch=row['candidate_scope']['branch'],source_repository=inputs['source_repo'])
    _owner(context,get)  # Existing artifacts are charged in the live baseline.
    enriched={**manifest,'g5a_capability_reference':row['g5a_capability_reference'],
        'g5a_bootstrap_reference':row['g5a_bootstrap_reference']}
    manifest_path=controls/'g5a-prerequisites.json';calibration.publish(manifest_path,enriched)
    template=row.get('private_cas_comparison_template')
    require(type(template) is dict and template.get('source_sha')==context.source_sha
        and template.get('source_tree')==context.source_tree
        and template.get('producer_namespace_root')=='OWNED_HORIZON_DATA_ROOT'
        and template.get('qualification_scope')=='PRIVATE_DEVELOPMENT_ONLY_NOT_G5',
        'G5A_CARRIER_EXACT_AUTHORIZED_ORIGINAL_CAS_TEMPLATE_REQUIRED')
    require(type(filter_plan_sha256) is str and runner.SHA64.fullmatch(filter_plan_sha256),
        'G5A_CARRIER_EXACT_SOURCE_PLAN_DIGEST_REQUIRED')
    actual_plan=calibration.digest(runner.read_input(ROOT/'ops/policy/rc6-native-prerequisites-v1.json'))
    require(filter_plan_sha256==actual_plan,'G5A_CARRIER_EXACT_SOURCE_PLAN_REBOUND')
    inputs.update(g5a_prerequisites_manifest_path=str(manifest_path),
        g5a_prerequisites_manifest_sha256=calibration.digest(calibration.wire(enriched)),
        g5a_revision_approval_url=row['g5a_revision_approval_url'],g5a_revision_owner=context.owner_session,
        filter_plan_sha256=filter_plan_sha256)
    observation=calibration.filesystem(parent);bound=preflight(observation)
    source_fs=calibration.filesystem(ROOT)
    require(observation['device']==source_fs['device'] and observation['mount_id']==source_fs['mount_id'],
        'G5A_CARRIER_ORIGINAL_OUTER_SOURCE_FILESYSTEM_MISMATCH')
    result=object.__new__(G5ASetupLaunch);object.__setattr__(result,'_key',_KEY)
    state={'object':result,'birth':_birth(),'context':context,'binding':context.binding,'inputs':inputs,
        'parent':parent,'controls':controls,'template':clone(template),'get':get,'consumed':False,
        'exported_source_context':exported_source_context,
        'code_hashes':{member:calibration.digest(runner.read_input(Path(inputs['source_root'])/member,
            maximum=calibration.CONTROLS)) for member in runner.CODE_MEMBERS},
        'documentary':{'schema':'rc6.g5a-authenticated-setup-launch-controls.v1','scope':runner.OPERATION_SCOPE,
            'source_sha':context.source_sha,'source_tree':context.source_tree,'source_proof':source_proof,
            'verified_G0_G4':prerequisites,'verified_original_bootstrap':{key:value for key,value in bootstrap.items()
                if key not in ('receipt_raw','verified_capability_prerequisite')},
            'capacity_before_image':observation,'control_bound':bound,'real_orders_sent':0,
            'G5_GREEN':False,'qualification_claimed':False,'DEPLOY_OWNER':'NOT_ACQUIRED'}}
    _LAUNCHES[id(result)]=state
    calibration.publish(controls/'authenticated-setup-controls.json',result.documentary)
    return result


def remaining_native_budget(deadline):
    require(type(deadline) in (int, float) and math.isfinite(deadline),
        'G5A_CARRIER_ORIGINAL_OPERATION_DEADLINE_REQUIRED')
    remaining = deadline - time.monotonic()
    require(7 < remaining <= runner.HORIZON_SECONDS,
        'G5A_CARRIER_ORIGINAL21600_OPERATION_DEADLINE_EXHAUSTED')
    # TERM2/FIN5 remain inside the single operation's remaining budget.
    return int(remaining) - 7


def launch(authority,*,token,operation_deadline=None):
    """Run the original own namespace/manager once, retaining unknown FIN RAW.

    Returns original opaque namespace and FIN handles for the separate Source
    backing-retirement/capture finalizer. No parser, removal or GREEN occurs.
    """
    from scripts import rc6_authenticated_fixture_lifecycle as owned
    from scripts import rc6_ephemeral_api_credential as credential
    from scripts import rc6_owned_gate_lease as lease
    from scripts import rc6_privileged_custody as privileged
    state=_registered(authority);context=state['context'];owner_check=lambda:_owner(context,state['get'])
    operation_deadline = (time.monotonic() + runner.HORIZON_SECONDS
        if operation_deadline is None else operation_deadline)
    remaining_native_budget(operation_deadline)
    from scripts import rc6_g5a_source_context as export
    export.validate_export(state['exported_source_context'],source_repo=ROOT,
        source_root=state['inputs']['source_root'],index=state['inputs']['source_index'],
        source_sha=context.source_sha,source_tree=context.source_tree,binding=state['binding'])
    current_inputs,_=_preexisting_source(context=context,
        **{key:state['inputs'][key] for key in ('source_root','source_repo','source_index','python311')},
        interpreter_manifest=state['inputs']['interpreter_manifest_path'])
    require(all(state['inputs'][key]==value for key,value in current_inputs.items()),
        'G5A_CARRIER_PREEXISTING_INPUTS_CHANGED_BEFORE_IMAGE')
    owner_check()
    require(type(token) in (bytes,bytearray) and bool(token),'G5A_CARRIER_EPHEMERAL_IN_MEMORY_API_BYTES_REQUIRED')
    # Single use is irreversible even when preflight or allocation fails.
    state['consumed']=True
    require(ctypes.CDLL(None,use_errno=True).prctl(4,0,0,0,0)==0,
        'G5A_CARRIER_ISSUER_DUMPABLE_ZERO_REQUIRED')
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    before=calibration.filesystem(state['parent']);preflight(before)
    namespace=owned.create_namespace(state['parent'],state['binding'])
    claim=owned.namespace_receipt(namespace)
    monitor_root=namespace.path/'g5a-owner-monitor';monitor_root.mkdir(mode=0o700)
    monitor=lease.GateLeaseMonitor(monitor_root,source_sha=context.source_sha,source_tree=context.source_tree,
        owner_session=context.owner_session,launch_receipt_url=context.launch_receipt_url,gate='Horizon',
        g5a_namespace_receipt=claim,g5a_revision=context.revision,
        get=(lambda path,deadline:state['get'](path, deadline=deadline)) if state['get'] is not None else None)
    monitor.prelaunch('g5a-original1202')
    owner_check()
    image=namespace.path/'image.ext4';mount=namespace.path/'mount';mount.mkdir(mode=0o700)
    descriptor=os.open(image,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        os.posix_fallocate(descriptor,0,runner.LIMITS['backing_image_bytes']);os.fsync(descriptor)
    finally:os.close(descriptor)
    after=calibration.filesystem(state['parent'])
    calibration.require_capacity(after,control_bound(after['fragment_bytes'])['total_bytes'])
    require(image.lstat().st_blocks*512>=runner.LIMITS['backing_image_bytes'],
        'G5A_CARRIER_ACTUAL_ORIGINAL26_BACKING_RESERVATION_REQUIRED')
    pending={'schema':'porota.rc6.pending-loop-birth.v1','binding':state['binding'],
        'namespace_nonce':namespace.nonce,'image_identity':calibration.identity(image.lstat()),
        'loop':None,'qualification_claimed':False}
    birth=namespace.path/'loop-birth.json';birth_sha=calibration.publish(birth,pending)
    comparison=clone(state['template']);comparison['producer_namespace_root']=str(mount/'project')
    comparison['prerequisites']=calibration.decode(runner.read_input(state['inputs']['g5a_prerequisites_manifest_path']))
    comparison_path=state['controls']/'g5a-original-cas-admission.json'
    comparison_sha=calibration.publish(comparison_path,comparison)
    inputs={**state['inputs'],'cas_comparison_manifest_path':str(comparison_path),
        'cas_comparison_manifest_sha256':comparison_sha}
    live={'mount_namespace_inode':os.stat('/proc/self/ns/mnt').st_ino,
        'host_PID1':privileged.public_kernel_process(1),'host_boundary':privileged.host_boundary_snapshot(),
        'issuer':privileged.issuer_snapshot(privileged.kernel_process(os.getpid())),
        'boundary_host_proc_device':os.stat('/proc').st_dev}
    request=root_request(binding=state['binding'],namespace_receipt=claim,inputs=inputs,
        image_identity=calibration.identity(image.lstat()),loop_birth_identity=calibration.identity(birth.lstat()),
        loop_birth_pending_sha256=birth_sha,code_hashes=state['code_hashes'],live=live)
    calibration.publish(namespace.path/'root-request.json',request)
    keeper=credential.create(token,source_sha=context.source_sha,source_tree=context.source_tree,
        binding=state['binding'],owner_check=owner_check)
    try:
        # The token bytes never enter request, environment, command or diagnostics.
        if type(token) is bytearray:credential.wipe(token)
        request_path=namespace.path/'custody-request.json'
        custody_request=privileged.g5a_service_request(request,namespace_receipt=claim,source=Path(inputs['source_root']),
            code_hashes=state['code_hashes'],resource_cpu=min(os.sched_getaffinity(0)),credential_metadata=keeper.metadata,
            exported_source_context=state['exported_source_context'])
        calibration.publish(request_path,custody_request)
        command=privileged.service_command(Path(inputs['source_root']),request_path,nonce=namespace.nonce,control_root=namespace.path,
            runtime_seconds=custody_request['runtime_seconds'],broker_mode='g5a',resource_cpu=custody_request['resource_cpu'],
            credential_context=keeper,credential_owner_check=owner_check,
            exported_source_context=state['exported_source_context'])
        service_state={};kernel=None;fin=None
        kernel,fin=privileged.execute_service(namespace,command,cwd=Path(inputs['source_root']),
            environ={key:os.environ[key] for key in ('PATH','RUNNER_TOOL_CACHE','LANG','LC_ALL') if key in os.environ},
            progress=privileged._observer(namespace.path/'broker-birth.json',state['binding'],service_state,monitor),
            timeout_seconds=remaining_native_budget(operation_deadline),fin_label='g5a-original1202')
        actual_root_FIN=privileged._closed_service(service_state)
        monitor.after_fin(kernel)  # Same-operation current Source/lease; no expired approval remint.
        result={'namespace':namespace,'fin':fin,'kernel':kernel,'request':request,
            'custody_request':custody_request,'service_state':service_state,'actual_root_service_FIN':actual_root_FIN,
            'lease':monitor,'controls':state['controls'],'operation_scope':runner.OPERATION_SCOPE,
            'operation_deadline_monotonic':operation_deadline,
            'G5_GREEN':False,'qualification_claimed':False,'real_orders_sent':0,
            'large_stream_parsed':False,'backing_retirement_performed':False,'cleanup_authority_granted':False}
        owned.require_fin(namespace,fin)
        _NATIVE_RESULTS[id(result)]={'object':result,'birth':_birth(),'snapshot':_native_snapshot(result),
            'handles':{key:result[key] for key in ('namespace','fin','lease','controls')}}
        return result
    finally:
        # Closing a memfd cannot stop, remove, certify or hide a native child.
        keeper.close()
