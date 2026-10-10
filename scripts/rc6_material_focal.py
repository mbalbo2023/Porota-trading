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


class FocalObserver:
    """Keep native collection and actual report identities as separate facts."""
    def __init__(self, stage):
        self.stage=stage
        self.items=[];self.corpus=[];self.deferred=[]
        self.started_nodeids=[];self.finished_nodeids=[];self.reports=[]

    def pytest_collection_modifyitems(self,session,config,items):
        from _pytest.junitxml import mangle_test_address
        pending=[];chosen=[]
        for item in items:
            address=mangle_test_address(item.nodeid)
            identity={'nodeid':item.nodeid,'classname':'.'.join(address[:-1]),'name':address[-1]}
            self.corpus.append(identity)
            classification=classify_node(item.nodeid)
            if self.stage=='focal' and classification:
                self.deferred.append({**identity,**classification});pending.append(item)
            else:chosen.append(item)
        items[:]=chosen
        if pending:config.hook.pytest_deselected(items=pending)

    def pytest_collection_finish(self,session):
        from _pytest.junitxml import mangle_test_address
        for item in session.items:
            address=mangle_test_address(item.nodeid)
            self.items.append({'nodeid':item.nodeid,'classname':'.'.join(address[:-1]),'name':address[-1]})

    def pytest_runtest_logstart(self,nodeid,location):
        self.started_nodeids.append(nodeid)

    def pytest_runtest_logfinish(self,nodeid,location):
        self.finished_nodeids.append(nodeid)

    def pytest_runtest_logreport(self,report):
        self.reports.append({'nodeid':report.nodeid,'when':report.when,'outcome':report.outcome,
            'location':list(report.location)})

    def execution_coverage_exact(self):
        expected=[row['nodeid'] for row in self.items]
        return self.started_nodeids==self.finished_nodeids==expected


def require_original_phase_fin(g,finalization):
    g['require'](finalization['status']=='GREEN' and finalization['finalization_thread_finished'] is True
        and finalization['kernel_echild_before_phase_return'] is True
        and finalization['signal_guard_installed_and_witnessed'] is True
        and not finalization['termination_signal_attempts']
        and finalization['forced_termination_attempted'] is False and finalization['signal_vetoed'] is False
        and finalization['wall_seconds']<=5 and not finalization['errors'],
        'PHASE_CHILD_INFRASTRUCTURE_NOT_GENUINELY_FINALIZED')


def product_source_names(before):
    return {Path(n).stem for n in before['files'] if '/' not in n and n.endswith('.py')} | {
        n.split('/')[0] for n in before['files'] if '/' in n and n.endswith('.py')
        and n.split('/')[0] not in ('tests','docs','.github','.agents')}


def capture_post_fin_source(g,root,before,*,source_sha,source_tree,output,phase,finalization,observations,
                           product_names=None):
    """Keep original indexes on Source RED, after genuine FIN, without retry.

    The complete post-pin already captures every original product blob. Reuse
    that capture for import closure instead of rereading every imported module.
    On drift, no further Source/import payload is consumed or relabelled GREEN.
    """
    require_original_phase_fin(g,finalization)
    g['publish'](output/(phase+'.source-before.index.json'),g['canonical'](before))
    result={'source_namespace_exact_before_after':False,'observed_CODE_atime_changes':None,
        'post_fin_source_validation_error':None}
    try:
        after=g['source_pin'](root,source_sha,source_tree)
        g['publish'](output/(phase+'.source-after.index.json'),g['canonical'](after))
        atime=g['compare_source'](before,after)
        expected_names=product_source_names(before)
        if product_names is None:product_names=expected_names
        g['require'](product_names==expected_names,'COMPLETE_PRODUCT_SOURCE_NAMES_REQUIRED')
        for name,module in list(sys.modules.items()):
            if name.split('.')[0] not in product_names:continue
            origin=getattr(module,'__file__',None)
            if origin is None:continue
            location=g['safe_path'](origin)
            if not location.is_relative_to(root):
                observations['unexpected_product_imports'].append(name);continue
            relative=location.relative_to(root).as_posix()
            expected=before['files'].get(relative);actual=after['files'].get(relative)
            g['require'](expected is not None and actual is not None
                and actual['sha256']==expected['sha256'],'PRODUCT_IMPORT_SOURCE_BYTES_MISMATCH')
            observations['actual_product_imports'].append({'module':name,'path':relative,'sha256':actual['sha256']})
        result.update(source_namespace_exact_before_after=True,observed_CODE_atime_changes=atime)
    except (OSError,ValueError,RuntimeError,KeyError,TypeError) as error:
        result['post_fin_source_validation_error']={'class':type(error).__name__,'reason':str(error)}
    g['publish'](output/(phase+'.source-validation.json'),g['canonical'](result))
    return result

def emit_cas_reviews(g, root, before, report, execution_wire, output, selected_files):
    """Publish private evidence from the original execution, after original FIN."""
    from scripts import rc6_archive_reader_review as readers
    from scripts import rc6_cas_original_comparison as comparison
    if not readers.CAS_REVIEW_MODULES.issubset(selected_files):
        return {}
    require_original_phase_fin(g, report.get('child_infrastructure_finalization', {}))
    g['require'](report.get('source_sha') == before.get('source_sha')
        and report.get('source_tree') == before.get('source_tree'), 'CAS_REVIEW_ORIGINAL_SOURCE_PIN_REBOUND')
    # Validate execution before any Source payload read.
    readers.executed_reader_nodes(report, source_sha=before['source_sha'], source_tree=before['source_tree'])
    ack = comparison.build_private_ack_review(report, execution_wire,
        source_sha=before['source_sha'], source_tree=before['source_tree'])
    inventory = readers.inventory_from_source_pin(root, before, capture=g['capture'])
    reader = readers.build_review(inventory, report, execution_wire,
        source_sha=before['source_sha'], source_tree=before['source_tree'])
    records = {'cas_reader_review': reader, 'cas_private_ack_review': ack}
    for role, record in records.items():
        g['publish'](output / readers.CAS_REVIEW_FILES[role], g['canonical'](record))
    return records


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
    if a.stage == 'cheap':
        # Load supplementary evidence code before the original Source pin.
        from scripts import rc6_archive_reader_review, rc6_cas_original_comparison
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
    observer=FocalObserver(a.stage)
    argv=['-q','-p','no:cacheprovider','-o','pythonpath=.','-o','junit_family=legacy',
          '--basetemp='+str(output/(a.phase+'-private')/'pytest'),*selected_files]
    if a.phase=='collection':argv.append('--collect-only')
    else:argv.append('--junitxml='+str(output/'focal-tests.xml'))
    from scripts.rc6_pytest_fixture_lifecycle import FixtureLifecyclePlugin
    fixture_lifecycle_plugin=FixtureLifecyclePlugin(authenticated_binding,
        output/(a.phase+'-fixture-lifecycle-controls'),candidate_sha=a.source_sha,candidate_tree=a.source_tree)
    try:rc=int(pytest.main(argv,plugins=[observer,fixture_lifecycle_plugin]))
    finally:
        finalization=g['finalize_child_infrastructure'](initial)
        g['publish'](output/(a.phase+'.child-finalization.json'),g['canonical'](finalization))
    # Verbatim original finalizer predicate. No Source/log/JUnit read before this barrier.
    require_original_phase_fin(g,finalization)
    fixture_lifecycle_plugin.retry_after_original_phase_finalization()
    fixture_lifecycle_report=fixture_lifecycle_plugin.summary()
    g['publish'](output/(a.phase+'.fixture-lifecycle.json'),g['canonical'](fixture_lifecycle_report))
    source_validation=capture_post_fin_source(g,root,before,source_sha=a.source_sha,source_tree=a.source_tree,
        output=output,phase=a.phase,finalization=finalization,observations=observations)
    execution_coverage_exact=None if a.phase=='collection' else observer.execution_coverage_exact()
    phase_errors=[]
    if fixture_lifecycle_report['original_factory_context_failures']:
        phase_errors.append('FOCAL_ORIGINAL_FACTORY_OR_RAW_DECLARATION_CONTEXT_RED')
    if fixture_lifecycle_report['evidence_declaration_failures']:
        phase_errors.append('FOCAL_REQUIRED_RAW_DECLARATION_RED')
    if fixture_lifecycle_report['required_raw_scopes_preserved']:
        phase_errors.append('FOCAL_REQUIRED_RAW_NOT_AUTHENTICATED_AND_CAPTURED')
    if source_validation['post_fin_source_validation_error'] is not None:
        phase_errors.append(source_validation['post_fin_source_validation_error']['reason'])
    if execution_coverage_exact is False:phase_errors.append('FOCAL_NATIVE_EXECUTION_COVERAGE_MISMATCH')
    report={'schema':'rc6.material-focal-owned-phase.v1','scope':'SUPPLEMENTARY_FOCAL_ONLY_NOT_WHOLE_GOV',
        'source_sha':a.source_sha,'source_tree':a.source_tree,'pid':os.getpid(),'phase':a.phase,
        'items':observer.items,'pytest_exit_code':rc,'literal_focal_files':list(selected_files),'closure_before_fixture':closure,
        'execution_stage':a.stage,'complete_corpus':observer.corpus,'deferred_material_nodes':observer.deferred,
        'complete_corpus_sha256':hashlib.sha256(g['canonical'](observer.corpus)).hexdigest(),
        'actual_execution_started_nodeids':observer.started_nodeids,
        'actual_execution_finished_nodeids':observer.finished_nodeids,'original_pytest_reports':observer.reports,
        'native_execution_coverage_exact':execution_coverage_exact,'phase_validation_errors':phase_errors,
        'deferred_nodes_claimed_executed':False,'final_G6_union_coverage_required':True,
        **source_validation,
        'offline_inet_creation_capability':capability,'child_infrastructure_finalization':finalization,
        'original_tmp_path_scoped_lifecycle':fixture_lifecycle_report,
        'phase_namespace':namespace,**observations,'real_orders_sent':0,'whole_Gov_claim':False}
    execution_wire = g['canonical'](report)
    g['publish'](output/(a.phase+'.observations.json'), execution_wire)
    review_error = False
    if a.stage == 'cheap' and a.phase == 'execution':
        try:
            emit_cas_reviews(g, root, before, report, execution_wire, output, selected_files)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            # Retain verbatim original reports; never rewrite a RED as a review.
            review_error = True
            g['publish'](output/'execution.cas-review-error.json', g['canonical']({
                'status': 'RED', 'class': type(error).__name__, 'reason': str(error),
                'contract_review_approved': False, 'native_v4_write_enabled': False, 'real_orders_sent': 0}))
    return rc or int(bool(phase_errors) or review_error)
if __name__=='__main__':raise SystemExit(main())
