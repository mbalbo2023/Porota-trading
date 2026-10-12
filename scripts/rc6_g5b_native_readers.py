"""Original reader/recovery corpus after actual ROOT and outer FIN.

Run on the NONROOT Actions parent using its existing exact whole Git Source and
PRODUCT157 interpreter. No Source copy, mount, dependency install, bearer or G5
qualification is performed here. The current-owner and live comparable-capacity
callbacks belong to the Source-fixed parent gate controller.
"""
from __future__ import annotations
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from scripts import rc6_capacity_calibration as calibration

ROLE_FILES = {'g5b_recovery_collection': 'collection.json', 'g5b_recovery_execution': 'execution.json',
    'g5b_recovery_junit': 'junit.xml', 'g5b_reader_review': 'reader-review.json',
    'g5b_private_ack_review': 'private-ack-review.json',
    'g5b_reader_collection_kernel': 'collection-kernel.json', 'g5b_reader_execution_kernel': 'execution-kernel.json'}


def require(ok, reason):
    if not ok: raise ValueError(reason)


def execute_post_fin_review(raw_capture, *, source_root, python311, control_root, deadline,
                            owner_check, capacity_preflight, monitor_factory,
                            aggregate_root=None, aggregate_probe=None,
                            aggregate_project_id=None):
    """Return actual lossless RAW role paths, without a certification factory.

    raw_capture is the original opaque parser object. It can exist only after
    the actual ROOT worker/guardian/outer FIN, complete lossless stream scan and
    measured retirement of this operation's own original backing image.
    """
    from scripts import rc6_g5a_raw_capture as captures
    from scripts import rc6_g5a_native_runner as runner
    from scripts import rc6_authenticated_fixture_lifecycle as lifecycle
    from scripts import rc6_archive_reader_review as readers
    from scripts import rc6_cas_original_comparison as comparison
    from scripts import rc6_controlled_governed_runner as governed
    from scripts import rc6_controlled_native_child_manager as manager
    from scripts import rc6_material_horizon as horizon
    from scripts import rc6_architectural_gates as gates
    from scripts import rc6_owned_gate_lease as leases
    from scripts import rc6_g5a_aggregate_issuer as aggregate
    state = captures._capture(raw_capture)
    documentary = state['documentary']; binding = documentary['binding']
    require(documentary.get('original_retirement', {}).get('original_outer_FIN_verified') is True
        and documentary['original_retirement'].get('actual_ROOT_worker_and_guardian_FIN_verified') is True,
        'G5B_READERS_ORIGINAL_OPAQUE_POST_ROOT_OUTER_FIN_REQUIRED')
    require(os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid() > 0,
        'G5B_READERS_ACTUAL_NONROOT_PARENT_REQUIRED')
    require(callable(owner_check) and callable(capacity_preflight) and callable(monitor_factory)
        and type(deadline) in (int, float)
        and 0 < deadline - time.monotonic() <= 21600, 'G5B_READERS_SOURCE_OWNER_CAPACITY_REMAINING_DEADLINE_REQUIRED')
    source, control = runner.canonical_path(str(source_root)), runner.canonical_path(str(control_root))
    require(aggregate_root is not None and aggregate_probe is not None
        and type(aggregate_project_id) is int and aggregate_project_id > 0,
        'G5A_HOST_STAGE_AGGREGATE_ENFORCEMENT_NOT_VERIFIED')
    aggregate_root = runner.canonical_path(str(aggregate_root))
    aggregate_probe = runner.canonical_path(str(aggregate_probe))
    aggregate.require_stage_topology(aggregate_root, stage='reader-collection',
        project_id=aggregate_project_id, write_roots={'readers': aggregate_root/'readers'})
    require(control.is_relative_to(aggregate_root),
        'G5B_READERS_CONTROL_OUTSIDE_AGGREGATE_PROJECT_FORBIDDEN')
    require(not os.path.lexists(control) and control.parent.lstat().st_uid == os.getuid()
        and not control.is_relative_to(source) and not source.is_relative_to(control),
        'G5B_READERS_FRESH_SOURCE_EXTERNAL_CONTROL_REQUIRED')
    require(source.stat().st_dev == Path.home().stat().st_dev
        and len(os.fsencode(Path.home()/('r6-'+'a'*22)/'e')) <= 50,
        'G5B_READERS_ORIGINAL_SOURCE_HOME_FILESYSTEM_AND_SHORT_PATH_REQUIRED')
    require(Path(__file__).absolute() == source/'scripts/rc6_g5b_native_readers.py',
        'G5B_READERS_EXACT_SOURCE_HELPER_IMPORT_REQUIRED')
    manager.pre_capture_kernel_state()
    owner_check('reader-product157-preflight')
    before = governed.source_pin(source, binding['candidate_sha'], binding['candidate_tree'])
    environment = {key: value for key, value in os.environ.items()
        if key not in {'GH_TOKEN', 'GITHUB_TOKEN', 'RC6_READONLY_GIT_TOKEN'}
        and not key.startswith(('POROTA_', 'PAPER_', 'PPI_', 'IOL_', 'BYMA_', 'PYTHON'))}
    environment.update(PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', GIT_OPTIONAL_LOCKS='0')
    control.mkdir(mode=0o700)
    # PRODUCT157 preflight is a real Python subprocess. It must have the same
    # original manager, project, sealed boundary, owner monitor and FIN as the
    # collection it precedes. No unsealed check_output may run in this parent.
    preflight_namespace = lifecycle.create_namespace(aggregate_root/'readers', binding)
    preflight_monitor = monitor_factory(preflight_namespace, 'g5-reader-product157-preflight')
    preflight_monitor.prelaunch('g5-reader-product157-preflight')
    preflight_env = aggregate.stage_environment(aggregate_root, environment,
        project_id=aggregate_project_id, stage='reader-collection')
    aggregate.require_stage_topology(aggregate_root, stage='reader-collection',
        project_id=aggregate_project_id,
        write_roots={'control': control, 'namespace': preflight_namespace.path})
    preflight_command = aggregate.guarded_stage_command(stage='reader-collection',
        root=aggregate_root, outside_control=aggregate_probe,
        project_id=aggregate_project_id, cwd=aggregate_root,
        argv=[str(python311), '-I', '-B', '-c', horizon.PREFLIGHT], python=str(python311))
    preflight_kernel, preflight_fin = lifecycle.execute_owned(preflight_namespace,
        preflight_command, cwd=aggregate_root, environ=preflight_env,
        log_relative='product157-preflight-native.log', timeout_seconds=45,
        fin_label='reader-product157-preflight', progress=preflight_monitor.progress)
    lifecycle.require_fin(preflight_namespace, preflight_fin)
    preflight_monitor.after_fin(preflight_kernel)
    require(manager.managed_phase_green(preflight_kernel),
        'G5B_READERS_ORIGINAL_MANAGED_PRODUCT157_PREFLIGHT_RED')
    qualification = horizon.preflight(Path(python311), '3.11.16', source,
        environment, wire=runner.read_input(
            preflight_namespace.path/'product157-preflight-native.log', maximum=1024**2))
    manager.pre_capture_kernel_state()
    records, live = {}, []
    selected_files = sorted(readers.CAS_REVIEW_MODULES)
    for phase in ('collection', 'execution'):
        owner_check('reader-'+phase+'-prelaunch')
        from scripts.rc6_g5a_host_capacity import require_host_stage_admission
        require_host_stage_admission(stage='reader-'+phase)
        require(time.monotonic() < deadline, 'G5B_READERS_ORIGINAL_HORIZON21600_DEADLINE_EXHAUSTED')
        namespace = lifecycle.create_namespace(aggregate_root/'readers', binding)
        output = namespace.path/'focal'; output.mkdir(mode=0o700)
        # This callback must use the already authenticated original comparable
        # corpus bound; an observation or synthetic reduction is insufficient.
        measured = capacity_preflight(namespace.path, phase)
        require(type(measured) is dict and measured.get('capacity', {}).get('status') == 'GREEN'
            and measured.get('binding') == binding, 'G5B_READERS_CURRENT_ORIGINAL_COMPARABLE_CAPACITY_REQUIRED')
        live.append(measured)
        env = {**environment, 'RC6_GOV_AUTHENTICATED_ROOT_JSON':
            calibration.wire(lifecycle.namespace_receipt(namespace)).decode()}
        label = 'g5-reader-'+phase
        monitor = monitor_factory(namespace, label)
        require(type(monitor) is leases.GateLeaseMonitor and monitor.g5a_revision is None
            and monitor.g5a_namespace_receipt is None, 'G5B_READERS_ORIGINAL_CURRENT_OWNER_MONITOR_REQUIRED')
        monitor.prelaunch(label)
        command = [str(python311), '-I', '-B', str(source/'scripts/rc6_material_focal.py'),
            '--repo-root', str(source), '--source-sha', binding['candidate_sha'], '--source-tree', binding['candidate_tree'],
            '--output-root', str(output), '--phase', phase, '--stage', 'reader-review', '--cheap-files-json',
            calibration.wire(selected_files).decode()]
        remaining = min(5400, int(deadline-time.monotonic()))
        require(remaining >= 1, 'G5B_READERS_ORIGINAL_HORIZON21600_DEADLINE_EXHAUSTED')
        stage = 'reader-'+phase
        aggregate.require_stage_topology(aggregate_root, stage=stage,
            project_id=aggregate_project_id,
            write_roots={'control': control, 'namespace': namespace.path, 'output': output})
        env = aggregate.stage_environment(aggregate_root, env,
            project_id=aggregate_project_id, stage=stage)
        sealed = aggregate.guarded_stage_command(stage=stage, root=aggregate_root,
            outside_control=aggregate_probe, project_id=aggregate_project_id,
            cwd=aggregate_root, argv=command, python=str(python311))
        kernel, fin = lifecycle.execute_owned(namespace, sealed, cwd=aggregate_root, environ=env,
            timeout_seconds=remaining, fin_label=label, progress=monitor.progress)
        lifecycle.require_fin(namespace, fin)
        monitor.after_fin(kernel)
        require(manager.managed_phase_green(kernel), 'G5B_READERS_ORIGINAL_NATIVE_MANAGER_RED')
        final = calibration.decode(runner.read_input(output/(phase+'.child-finalization.json'), maximum=1024**2))
        require(gates.finalization_closed(final), 'G5B_READERS_ORIGINAL_FINALIZER5_RED_NO_PAYLOAD_READ')
        raw = runner.read_input(output/(phase+'.observations.json'), maximum=16*1024**2)
        observed = calibration.decode(raw)
        require(observed.get('source_namespace_exact_before_after') is True
            and observed.get('phase_validation_errors') == []
            and observed.get('closure_before_fixture', {}).get('installed_total') == 157,
            'G5B_READERS_ORIGINAL_SOURCE_PRODUCT157_OR_FIXTURE_LIFECYCLE_RED')
        records[phase] = (raw, observed, output, namespace, fin, kernel, monitor)
    collection, execution = records['collection'][1], records['execution'][1]
    output = records['execution'][2]
    junit = runner.read_input(output/'focal-tests.xml', maximum=16*1024**2)
    gates.validate_junit(collection, execution, junit)
    inventory = readers.inventory_from_source_pin(source, before, capture=governed.capture)
    review = runner.read_input(output/readers.CAS_REVIEW_FILES['cas_reader_review'], maximum=16*1024**2)
    ack = runner.read_input(output/readers.CAS_REVIEW_FILES['cas_private_ack_review'], maximum=16*1024**2)
    readers.validate_review(calibration.decode(review), inventory, execution, records['execution'][0],
        source_sha=binding['candidate_sha'], source_tree=binding['candidate_tree'])
    comparison.validate_private_ack_review(calibration.decode(ack), execution, records['execution'][0],
        source_sha=binding['candidate_sha'], source_tree=binding['candidate_tree'])
    owner_check('reader-after-both-original-fin')
    governed.compare_source(before, governed.source_pin(source, binding['candidate_sha'], binding['candidate_tree']))
    manager.pre_capture_kernel_state()
    originals = {'g5b_recovery_collection': records['collection'][0], 'g5b_recovery_execution': records['execution'][0],
        'g5b_recovery_junit': junit, 'g5b_reader_review': review, 'g5b_private_ack_review': ack,
        'g5b_reader_collection_kernel': calibration.wire(records['collection'][5]),
        'g5b_reader_execution_kernel': calibration.wire(records['execution'][5])}
    total = sum(len(raw) for raw in originals.values())
    require(total <= 24*1024**2, 'G5B_READERS_ORIGINAL_CONTROL24MIB_BOUND')
    references = {}
    for role, raw in originals.items():
        path = control/ROLE_FILES[role]
        calibration.publish_bytes(path, raw)
        references[role] = {'path': str(path), 'bytes': len(raw), 'sha256': calibration.digest(raw)}
    return {'status': 'ACTUAL_POST_ROOT_FIN_NATIVE_READERS_CAPTURED_NOT_G5', 'roles': references,
        'source_sha': binding['candidate_sha'], 'source_tree': binding['candidate_tree'],
        'product157_qualification': qualification, 'actual_capacity': live,
        'current_owner_monitors': [records[phase][6] for phase in ('collection', 'execution')],
        'G5_GREEN': False,
        'qualification_claimed': False, 'real_orders_sent': 0}
