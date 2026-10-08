"""Supplementary focal producer; original offline/finalization guards, never whole-Gov evidence."""
import argparse
from pathlib import Path
import json
import os
import runpy
import sys
import hashlib
import importlib

FILES=('tests/test_rc6_controlled_governed_runner.py',
       'tests/test_rc6_controlled_native_child_manager.py',
       'tests/test_deploy_v2_frozen_artifact_contract.py',
       'tests/test_issue465_stress.py',
       'tests/test_rc6_stress_native_cli_lifecycle.py',
       'tests/test_rc6_native_archive_horizon_wrapper.py',
       'tests/test_rc6_runtime_row_decode.py',
       'tests/test_rc6_budget_source_custody.py',
       'tests/test_rc6_predeploy_scoped_cleanup.py',
       'tests/test_rc6_funnel_storage_codec.py',
       'tests/test_rc6_convergence_provenance.py',
       'tests/test_rc6_packed_storage.py',
       'tests/test_issue465_provenance.py',
       'tests/test_rc6_convergence_sre_binding.py',
       'tests/test_rc6_convergence_sre_published_evidence.py',
       'tests/test_rc6_convergence_sre_modes.py',
       'tests/test_rc6_artifact_fixture_storage.py',
       'tests/test_rc6_native_import_event_filter.py',
       'tests/test_rc6_material_big_missing_worker_proof.py',
       'tests/test_rc6_browser_family_health_coverage.py',
       'tests/test_rc6_browser_prepared.py',
       'tests/test_rc6_browser_product_ipc.py',
       'tests/test_rc6_readonly_complete_archive.py')

# Classification changes only execution order. Every original node remains in
# the corpus and is mandatory in the final automatically discovered G6 suite.
# G4's direct canonical BIG proves its own scenario; it never claims to have
# executed a deferred pytest node.
MATERIAL_NODES={
    'tests/test_issue465_stress.py::test_10x_catalog_5x_observations_exercises_all_shadow_labs_and_five_factual_exits':
        {'stage':'G6','reason':'EXACT_12000_60000_BIG_MUST_FOLLOW_DUAL_FOCAL'},
}


def classify_node(nodeid):
    plain=nodeid.split('[',1)[0]
    return MATERIAL_NODES.get(plain)

def collect_preserved_heavy_inventory(output):
    """Collect complete module identities before any heavy fixture can start."""
    import pytest
    from _pytest.junitxml import mangle_test_address
    from scripts.rc6_architectural_gates import G6_PRESERVED_HEAVY_FILES
    rows=[]
    class CollectionOnly:
        def pytest_collection_finish(self,session):
            for item in session.items:
                address=mangle_test_address(item.nodeid)
                rows.append({'nodeid':item.nodeid,'classname':'.'.join(address[:-1]),'name':address[-1]})
        def pytest_fixture_setup(self,fixturedef,request):
            raise RuntimeError('G0_HEAVY_IDENTITY_COLLECTION_MUST_NOT_START_FIXTURES')
    rc=int(pytest.main(['--collect-only','-q','-p','no:cacheprovider','-o','pythonpath=.',
        '--basetemp='+str(Path(output)/'heavy-collection-private'),*sorted(G6_PRESERVED_HEAVY_FILES)],
        plugins=[CollectionOnly()]))
    if rc!=0 or not rows:raise ValueError('G0_COMPLETE_HEAVY_MODULE_IDENTITY_COLLECTION_RED')
    if {row['nodeid'].split('::',1)[0] for row in rows}!=set(G6_PRESERVED_HEAVY_FILES):
        raise ValueError('G0_ORIGINAL_HEAVY_MODULE_OMITTED_FROM_COLLECTION')
    return rows

def main():
    p=argparse.ArgumentParser();p.add_argument('--repo-root',required=True);p.add_argument('--source-sha',required=True)
    p.add_argument('--source-tree',required=True);p.add_argument('--output-root',required=True)
    p.add_argument('--stage',choices=('focal','cheap','admission'),default='focal')
    p.add_argument('--cheap-files-json')
    p.add_argument('--phase',choices=('collection','execution'),required=True);a=p.parse_args();os.umask(0o022)
    root=Path(a.repo_root)
    g=runpy.run_path(str(root/'scripts/rc6_controlled_governed_runner.py'))
    req=g['require'];output=g['safe_path'](a.output_root)
    req('RC6_GOV_AUTHENTICATED_ROOT_JSON' in os.environ,'AUTHENTICATED_FOCAL_NAMESPACE_REQUIRED')
    authenticated_binding=json.loads(os.environ['RC6_GOV_AUTHENTICATED_ROOT_JSON'])
    lifecycle=g['fixture_lifecycle'](root)
    lifecycle.validate_consumer_receipt(authenticated_binding,candidate_sha=a.source_sha,candidate_tree=a.source_tree)
    namespace=g['phase_namespace'](root,output,a.phase,authenticated_binding=authenticated_binding)
    g['publish'](output/(a.phase+'.namespace.json'),g['canonical'](namespace))
    capability=g['restrict_inet_creation']()
    g['publish'](output/(a.phase+'.offline-capability.json'),g['canonical'](capability))
    initial=g['child_infrastructure_snapshot']()
    req(all(v is None for v in initial.values()),'FRESH_PHASE_MULTIPROCESSING_INFRASTRUCTURE_REQUIRED')
    before=g['source_pin'](root,a.source_sha,a.source_tree)
    closure=g['installed_closure'](root)
    selected_files=FILES
    if a.stage=='cheap':
        selected_files=json.loads(a.cheap_files_json or 'null')
        sys.path.insert(0,str(root))
        from scripts.rc6_architectural_gates import G1_HEAVY_FILES, validate_g1_files
        validate_g1_files(selected_files)
        req(type(selected_files) is list and selected_files
            and len(selected_files)==len(set(selected_files))
            and all(type(name) is str and name.startswith('tests/test_') and name.endswith('.py') for name in selected_files),
            'CHEAP_STAGE_EXPLICIT_REVIEWED_FILES_REQUIRED')
        req(G1_HEAVY_FILES.isdisjoint(selected_files),'G1_CANNOT_ADMIT_MATERIAL_STRESS_CORPUS')
    req(all(name in before['files'] for name in selected_files),'FOCAL_LITERAL_SOURCE_FILE_MISSING')
    observations={'inet_socket_attempts':[],'subprocess_executable_counts':{},'actual_product_imports':[],
                  'unexpected_product_imports':[],'inet_socket_constructor_requests':[]}
    g['install_phase_audit'](observations)
    if a.stage=='admission':
        compiled=0
        imported=[]
        try:
            for name in before['files']:
                if name.endswith('.py') and name.split('/')[0] not in ('tests','docs'):
                    compile(g['capture'](root/name)[0],name,'exec');compiled+=1
            req(compiled>0,'STATIC_COMPILE_RETURNED_ZERO_PRODUCT_FILES')
            for name in ('rc6_shadow_runtime.read_contract','rc6_shadow_runtime.source_reads',
                    'rc6_shadow_runtime.worker','rc6_shadow_runtime.persistence',
                    'rc6_shadow_runtime.publication_storage','rc6_trader_dashboard.projection'):
                imported.append(importlib.import_module(name).__name__)
            sys.path.insert(0,str(root))
            heavy_corpus=collect_preserved_heavy_inventory(output)
        finally:
            finalization=g['finalize_child_infrastructure'](initial)
            g['publish'](output/(a.phase+'.child-finalization.json'),g['canonical'](finalization))
        req(finalization['status']=='GREEN' and finalization['finalization_thread_finished'] is True
            and finalization['kernel_echild_before_phase_return'] is True
            and finalization['wall_seconds']<=5 and not finalization['errors'],
            'ADMISSION_IMPORT_CHILD_FINALIZATION_RED')
        after=g['source_pin'](root,a.source_sha,a.source_tree);atime=g['compare_source'](before,after)
        report={'schema':'rc6.architectural-native-static.v1','source_sha':a.source_sha,'source_tree':a.source_tree,
            'compiled_product_files':compiled,'imported_product_modules':imported,'closure_before_fixture':closure,
            'source_namespace_exact_before_after':True,'child_infrastructure_finalization':finalization,
            'observed_CODE_atime_changes':atime,'offline_inet_creation_capability':capability,**observations,
            'native_exit_code':0,'real_orders_sent':0,'native_fixtures_started':False,'whole_Gov_claim':False,
            'preserved_heavy_corpus':heavy_corpus,'preserved_heavy_collection_only':True}
        g['publish'](output/(a.phase+'.observations.json'),g['canonical'](report))
        return 0
    import pytest
    from _pytest.junitxml import mangle_test_address
    items=[];corpus=[];deferred=[]
    class Observer:
        def pytest_collection_modifyitems(self,session,config,items):
            pending=[];chosen=[]
            for item in items:
                address=mangle_test_address(item.nodeid)
                identity={'nodeid':item.nodeid,'classname':'.'.join(address[:-1]),'name':address[-1]}
                corpus.append(identity)
                classification=classify_node(item.nodeid)
                if a.stage=='focal' and classification:
                    deferred.append({**identity,**classification});pending.append(item)
                else:chosen.append(item)
            # Deliberate stage scheduling; no skip/xfail and no erasure from the
            # complete corpus. G6 must prove every deferred identity ran.
            items[:]=chosen
            if pending:config.hook.pytest_deselected(items=pending)
        def pytest_collection_finish(self,session):
            for item in session.items:
                address=mangle_test_address(item.nodeid)
                items.append({'nodeid':item.nodeid,'classname':'.'.join(address[:-1]),'name':address[-1]})
    argv=['-q','-p','no:cacheprovider','-o','pythonpath=.','-o','junit_family=legacy',
          '--basetemp='+str(output/(a.phase+'-private')/'pytest'),*selected_files]
    if a.phase=='collection':argv.append('--collect-only')
    else:argv.append('--junitxml='+str(output/'focal-tests.xml'))
    from scripts.rc6_pytest_fixture_lifecycle import FixtureLifecyclePlugin
    fixture_lifecycle_plugin=FixtureLifecyclePlugin(authenticated_binding,
        output/(a.phase+'-fixture-lifecycle-controls'),candidate_sha=a.source_sha,candidate_tree=a.source_tree)
    try:rc=int(pytest.main(argv,plugins=[Observer(),fixture_lifecycle_plugin]))
    finally:
        finalization=g['finalize_child_infrastructure'](initial)
        g['publish'](output/(a.phase+'.child-finalization.json'),g['canonical'](finalization))
    # Verbatim original finalizer predicate. No Source/log/JUnit read before this barrier.
    req(finalization['status']=='GREEN' and finalization['finalization_thread_finished'] is True
        and finalization['kernel_echild_before_phase_return'] is True
        and finalization['signal_guard_installed_and_witnessed'] is True
        and not finalization['termination_signal_attempts']
        and finalization['forced_termination_attempted'] is False and finalization['signal_vetoed'] is False
        and finalization['wall_seconds']<=5 and not finalization['errors'],
        'PHASE_CHILD_INFRASTRUCTURE_NOT_GENUINELY_FINALIZED')
    fixture_lifecycle_plugin.retry_after_original_phase_finalization()
    fixture_lifecycle_report=fixture_lifecycle_plugin.summary()
    g['publish'](output/(a.phase+'.fixture-lifecycle.json'),g['canonical'](fixture_lifecycle_report))
    product_names={Path(n).stem for n in before['files'] if '/' not in n and n.endswith('.py')}
    product_names|={n.split('/')[0] for n in before['files'] if '/' in n and n.endswith('.py')
                    and n.split('/')[0] not in ('tests','docs','.github','.agents')}
    for name,module in list(sys.modules.items()):
        if name.split('.')[0] not in product_names:continue
        origin=getattr(module,'__file__',None)
        if origin is None:continue
        location=g['safe_path'](origin)
        if not location.is_relative_to(root):observations['unexpected_product_imports'].append(name);continue
        relative=location.relative_to(root).as_posix();expected=before['files'].get(relative)
        wire,_=g['capture'](location)
        req(expected is not None and g['digest'](wire)==expected['sha256'],'PRODUCT_IMPORT_SOURCE_BYTES_MISMATCH')
        observations['actual_product_imports'].append({'module':name,'path':relative,'sha256':g['digest'](wire)})
    after=g['source_pin'](root,a.source_sha,a.source_tree);atime=g['compare_source'](before,after)
    g['publish'](output/(a.phase+'.source-before.index.json'),g['canonical'](before))
    g['publish'](output/(a.phase+'.source-after.index.json'),g['canonical'](after))
    report={'schema':'rc6.material-focal-owned-phase.v1','scope':'SUPPLEMENTARY_FOCAL_ONLY_NOT_WHOLE_GOV',
        'source_sha':a.source_sha,'source_tree':a.source_tree,'pid':os.getpid(),'phase':a.phase,
        'items':items,'pytest_exit_code':rc,'literal_focal_files':list(selected_files),'closure_before_fixture':closure,
        'execution_stage':a.stage,'complete_corpus':corpus,'deferred_material_nodes':deferred,
        'complete_corpus_sha256':hashlib.sha256(g['canonical'](corpus)).hexdigest(),
        'deferred_nodes_claimed_executed':False,'final_G6_union_coverage_required':True,
        'source_namespace_exact_before_after':True,'observed_CODE_atime_changes':atime,
        'offline_inet_creation_capability':capability,'child_infrastructure_finalization':finalization,
        'original_tmp_path_scoped_lifecycle':fixture_lifecycle_report,
        'phase_namespace':namespace,**observations,'real_orders_sent':0,'whole_Gov_claim':False}
    g['publish'](output/(a.phase+'.observations.json'),g['canonical'](report))
    return rc
if __name__=='__main__':raise SystemExit(main())
