"""Supplementary focal producer; original offline/finalization guards, never whole-Gov evidence."""
import argparse
from pathlib import Path
import json
import os
import runpy
import sys

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
       'tests/test_rc6_artifact_fixture_storage.py')

def main():
    p=argparse.ArgumentParser();p.add_argument('--repo-root',required=True);p.add_argument('--source-sha',required=True)
    p.add_argument('--source-tree',required=True);p.add_argument('--output-root',required=True)
    p.add_argument('--phase',choices=('collection','execution'),required=True);a=p.parse_args();os.umask(0o022)
    root=Path(a.repo_root)
    g=runpy.run_path(str(root/'scripts/rc6_controlled_governed_runner.py'))
    req=g['require'];output=g['safe_path'](a.output_root)
    namespace=g['phase_namespace'](root,output,a.phase)
    g['publish'](output/(a.phase+'.namespace.json'),g['canonical'](namespace))
    capability=g['restrict_inet_creation']()
    g['publish'](output/(a.phase+'.offline-capability.json'),g['canonical'](capability))
    initial=g['child_infrastructure_snapshot']()
    req(all(v is None for v in initial.values()),'FRESH_PHASE_MULTIPROCESSING_INFRASTRUCTURE_REQUIRED')
    before=g['source_pin'](root,a.source_sha,a.source_tree)
    closure=g['installed_closure'](root)
    req(all(name in before['files'] for name in FILES),'FOCAL_LITERAL_SOURCE_FILE_MISSING')
    observations={'inet_socket_attempts':[],'subprocess_executable_counts':{},'actual_product_imports':[],
                  'unexpected_product_imports':[],'inet_socket_constructor_requests':[]}
    g['install_phase_audit'](observations)
    import pytest
    from _pytest.junitxml import mangle_test_address
    items=[]
    class Observer:
        def pytest_collection_finish(self,session):
            for item in session.items:
                address=mangle_test_address(item.nodeid)
                items.append({'nodeid':item.nodeid,'classname':'.'.join(address[:-1]),'name':address[-1]})
    argv=['-q','-p','no:cacheprovider','-o','pythonpath=.','-o','junit_family=legacy',
          '--basetemp='+str(output/(a.phase+'-private')/'pytest'),*FILES]
    if a.phase=='collection':argv.append('--collect-only')
    else:argv.append('--junitxml='+str(output/'focal-tests.xml'))
    try:rc=int(pytest.main(argv,plugins=[Observer()]))
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
        'items':items,'pytest_exit_code':rc,'literal_focal_files':list(FILES),'closure_before_fixture':closure,
        'source_namespace_exact_before_after':True,'observed_CODE_atime_changes':atime,
        'offline_inet_creation_capability':capability,'child_infrastructure_finalization':finalization,
        'phase_namespace':namespace,**observations,'real_orders_sent':0,'whole_Gov_claim':False}
    g['publish'](output/(a.phase+'.observations.json'),g['canonical'](report))
    return rc
if __name__=='__main__':raise SystemExit(main())
