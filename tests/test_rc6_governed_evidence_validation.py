"""Small G6 native phase/RAW regressions with real owned FIN, no full producer.

Native phase tests use an exact tiny Git tree and real pytest. Only dependency
closure/admitted corpus are explicit unit boundaries: five distributions and a
single control test never satisfy a product or canonical G6 qualification.
Carrier controls use synthetic logical reports; both nested and outer native
FIN and their hash-verified RAW capture are genuine.
"""
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from scripts import rc6_authenticated_fixture_lifecycle as lifecycle
from scripts import rc6_controlled_governed_runner as governed
from scripts import rc6_material_carrier as carrier
from tests.test_rc6_focal_raw_custody import NativeControlRun, _PHASE_WRITER, carrier_control, real_binding


ROOT=Path(__file__).absolute().parents[1]
_DIRECT_PHASE=r'''
import hashlib,json,runpy,sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
from scripts import rc6_controlled_governed_runner as g
source=Path(sys.argv[2]);output=Path(sys.argv[3]);binding=json.loads(sys.argv[6])
g.installed_closure=lambda root:{'installed_total':5,'explicit_unit_only_not_product_closure':True}
g.approved_scope=lambda root,sha:(['tests/test_tiny_control.py'],['UNIT_CORPUS_ONLY_NOT_PRODUCT'],{})
g.publish(output/'launch.json',g.canonical({'runner_sha256':g.digest(g.capture(g.__file__)[0]),
    'authenticated_namespace_binding':binding,'execution_id':'unit-native-phase-only'}))
result=g.phase_child(SimpleNamespace(repo_root=source,output_root=output,phase='execution',
    source_sha=sys.argv[4],source_tree=sys.argv[5]))
print('original tiny G6 native phase returned '+str(result),flush=True)
sys.exit(result)
'''
_TINY_PHASE_CASES={
    'unchanged': "def test_original_tiny_identity():\n    assert 1 + 1 == 2\n",
    'source_drift': "from pathlib import Path\n"
        "def test_original_tiny_source_metadata_drift():\n"
        "    target=Path(__file__)\n    before=target.stat().st_ctime_ns\n"
        "    target.chmod(0o644)\n    assert target.stat().st_ctime_ns != before\n",
    'lifecycle_declaration': "import pytest\n"
        "@pytest.fixture\ndef declared_controls(tmp_path_factory):\n"
        "    tmp_path_factory.mktemp('tiny-invalid-declaration')\n"
        "    return {'launcher_receipt':{'schema':'rc6.browser-native-preparation-transport.v1'},"
        "'receipt':{'schema':'INVALID_EXPLICIT_UNIT_DECLARATION'}}\n"
        "def test_original_tiny_fixture_result_stays_passed(declared_controls):\n"
        "    assert isinstance(declared_controls,dict)\n",
}


def native_tiny_phase(tmp_path,case):
    source_owner=tempfile.TemporaryDirectory(prefix='u',dir=Path.home())
    source=Path(source_owner.name)/'tiny-git';source.mkdir(mode=0o700)
    tests=source/'tests';tests.mkdir()
    test_path=tests/'test_tiny_control.py';test_path.write_text(_TINY_PHASE_CASES[case]);test_path.chmod(0o644)
    environment=dict(os.environ,GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,
        PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONDONTWRITEBYTECODE='1')
    def git(*arguments):
        return subprocess.check_output(['git','-C',str(source),*arguments],env=environment).decode().strip()
    git('init','--template=','-q');git('config','user.name','Explicit native G6 unit')
    git('config','user.email','fixture@example.invalid');git('add','-A');git('commit','-qm','tiny native phase control')
    binding=real_binding();binding.update(candidate_sha=git('rev-parse','HEAD'),candidate_tree=git('rev-parse','HEAD^{tree}'))
    # Source, AF_UNIX namespace and capture share this genuinely created own
    # parent/filesystem. No arbitrary ancestor or alias escapes the marker.
    namespace=lifecycle.create_namespace(Path(source_owner.name),binding)
    output=namespace.path/'native-phase';output.mkdir(mode=0o700)
    claim=lifecycle.namespace_receipt(namespace)
    command=[sys.executable,'-I','-B','-c',_DIRECT_PHASE,str(ROOT),str(source),str(output),
        binding['candidate_sha'],binding['candidate_tree'],json.dumps(claim)]
    kernel,fin=lifecycle.execute_owned(namespace,command,cwd=source,environ=environment,
        log_relative='original-native.log',timeout_seconds=30,fin_label='tiny-G6')
    assert fin.manager['managed_custody_closed'](kernel) is True
    # No producer output is read before the real same-parent owned FIN.
    observations_path=output/'execution.observations.json'
    assert observations_path.is_file(), (namespace.path/'original-native.log').read_text()
    observations=json.loads(observations_path.read_bytes())
    final=json.loads((output/'execution.child-finalization.json').read_bytes())
    assert final['status']=='GREEN' and final['kernel_echild_before_phase_return'] is True
    controls=[path.relative_to(namespace.path).as_posix() for path in output.rglob('*') if path.is_file()]
    captured=lifecycle.capture_required_evidence(namespace,fin,Path(source_owner.name)/'retained-original-controls',
        [*controls,'original-native.log'])
    cleanup=lifecycle.cleanup_namespace(namespace,fin,captured)
    assert cleanup['namespace_removed'] is True
    return kernel,observations,captured,source_owner


def original_capture(captured,relative):
    rows=[row for row in captured.files if row['relative_source']==relative]
    assert len(rows)==1
    wire=(captured.path/rows[0]['capture_file']).read_bytes()
    assert hashlib.sha256(wire).hexdigest()==rows[0]['sha256']
    return wire


@pytest.mark.parametrize('case,expected_exit',[('unchanged',0),('source_drift',1),('lifecycle_declaration',1)])
def test_g6_native_phase_preserves_true_pytest_rc_and_vetoes_post_fin_source_or_lifecycle_red(tmp_path,case,expected_exit):
    kernel,report,captured,source_owner=native_tiny_phase(tmp_path,case)
    assert kernel['returncode']==expected_exit
    assert report['pytest_exit_code']==0
    assert report['native_execution_coverage_exact'] is True
    assert report['closure_before_fixture']['installed_total']==5
    assert report['closure_before_fixture']['explicit_unit_only_not_product_closure'] is True
    assert len(report['items'])==len(report['actual_execution_started_nodeids'])==1
    before_raw=original_capture(captured,'native-phase/execution.source-before.index.json')
    before=json.loads(before_raw)
    assert hashlib.sha256(before_raw).hexdigest()==report['source_before_index_sha256']
    assert before['source_sha']==report['source_sha'] and before['source_tree']==report['source_tree']
    xml=original_capture(captured,'native-phase/porota-governed-tests.xml')
    cases=list(ET.fromstring(xml).iter('testcase'))
    assert len(cases)==1 and cases[0].find('failure') is None and cases[0].find('error') is None
    if case=='source_drift':
        assert report['source_namespace_exact_before_after'] is False
        assert report['post_fin_source_validation_error'] is not None
        assert 'GOVERNED_SOURCE_VALIDATION_RED' in report['phase_validation_errors']
        after_raw=original_capture(captured,'native-phase/execution.source-after.index.json')
        assert hashlib.sha256(after_raw).hexdigest()==report['source_after_index_sha256']
        after=json.loads(after_raw)
        assert before['files']['tests/test_tiny_control.py']['sha256']==after['files']['tests/test_tiny_control.py']['sha256']
        assert before['files']['tests/test_tiny_control.py']['stat_fields']['st_ctime_ns'] != after['files']['tests/test_tiny_control.py']['stat_fields']['st_ctime_ns']
    elif case=='lifecycle_declaration':
        assert report['source_namespace_exact_before_after'] is True
        summary=report['original_tmp_path_scoped_lifecycle']
        assert summary['evidence_declaration_failures']==1
        assert summary['original_factory_context_failures']
        assert 'GOVERNED_EVIDENCE_DECLARATION_FAILURES_RED' in report['phase_validation_errors']
        assert 'GOVERNED_ORIGINAL_FACTORY_CONTEXT_FAILURES_RED' in report['phase_validation_errors']
    else:
        assert report['phase_validation_errors']==[] and report['source_namespace_exact_before_after'] is True
    assert b'INTERNALERROR' not in original_capture(captured,'original-native.log')
    source_owner.cleanup()


@pytest.mark.parametrize('field,bad_value',[
    ('original_factory_context_failures',[{'classification':'EXPLICIT_CONTROL_RED'}]),
    ('evidence_declaration_failures',1),('required_raw_scopes_preserved',1),
])
def test_each_lifecycle_failure_and_missing_summary_field_is_permanently_red(field,bad_value):
    source={'source_namespace_exact_before_after':True,'post_fin_source_validation_error':None}
    summary={'original_factory_context_failures':[],'evidence_declaration_failures':0,'required_raw_scopes_preserved':0}
    assert governed.phase_validation_errors(source,summary,True)==[]
    summary[field]=bad_value
    assert 'GOVERNED_'+field.upper()+'_RED' in governed.phase_validation_errors(source,summary,True)
    del summary[field]
    assert 'GOVERNED_'+field.upper()+'_RED' in governed.phase_validation_errors(source,summary,True)


def test_original_partial_execution_and_unknown_source_never_qualify_native_phase():
    summary={'original_factory_context_failures':[],'evidence_declaration_failures':0,'required_raw_scopes_preserved':0}
    errors=governed.phase_validation_errors({},summary,False)
    assert 'GOVERNED_SOURCE_VALIDATION_RED' in errors
    assert 'GOVERNED_NATIVE_EXECUTION_COVERAGE_MISMATCH' in errors


_GOV_PHASE_WRITER=_PHASE_WRITER.replace('focal-tests.xml','porota-governed-tests.xml').replace("output/'original-unique-control.raw'",
    "output/('original-unique-control-'+phase+'.raw')").replace(
    "'control_only_not_product_or_gate_qualification':True}",
    "'control_only_not_product_or_gate_qualification':True,'source_sha':sys.argv[5],'source_tree':sys.argv[6],"
    "'closure_before_fixture':{'installed_total':5,'explicit_non_product_unit':True},"
    "'original_tmp_path_scoped_lifecycle':{'original_factory_context_failures':[],"
    "'evidence_declaration_failures':0,'required_raw_scopes_preserved':0}}")
_GOV_WRITER=r'''
import json,os,runpy,sys
from pathlib import Path
manager=runpy.run_path(sys.argv[1]);output=Path(sys.argv[2]);output.mkdir(mode=0o700)
writer=sys.argv[3];case=sys.argv[4];gpath=sys.argv[5];source_sha=sys.argv[6];source_tree=sys.argv[7]
assert manager['pre_capture_kernel_state']()['kernel_echild_verified'] is True
for phase in ('collection','execution'):
    command=[sys.executable,'-I','-B','-c',writer,gpath,str(output),phase,case,source_sha,source_tree]
    kernel=manager['managed_native_child'](command,output,output/(phase+'.native.log'),dict(os.environ),5400)
    assert manager['managed_custody_closed'](kernel) is True
    assert manager['pre_capture_kernel_state']()['kernel_echild_verified'] is True
    kernel['phase_acceptance_deadline_seconds']=5400
    (output/(phase+'.kernel.json')).write_text(json.dumps(kernel))
print('original synthetic whole-G6 log',flush=True)
sys.exit(1)
'''


class NativeGovernedControlRun(NativeControlRun):
    def __init__(self,root,case,args):
        super().__init__(root,case);self.args=args

    def __call__(self,command,*,cwd,label,limit,env,namespace):
        self.namespaces.append(namespace)
        output=Path(command[command.index('--output-root')+1])
        child=[sys.executable,'-I','-B','-c',_GOV_WRITER,str(lifecycle.DRIVER),str(output),
            _GOV_PHASE_WRITER,self.case,str(ROOT/'scripts/rc6_controlled_governed_runner.py'),
            self.args.source_sha,self.args.source_tree]
        kernel,fin=lifecycle.execute_owned(namespace,child,cwd=cwd,environ=dict(os.environ),
            log_relative=label+'.native.log',timeout_seconds=limit,fin_label=label)
        self.fins[namespace.nonce]=fin
        return {'kernel':kernel,'returncode':kernel['returncode']}


@pytest.mark.parametrize('case,expected_reason',[
    ('missing_observations','SEALED_RAW_LITERAL_PATH_MISSING_OR_DUPLICATE'),
    ('network_red','FULL_GOV_NATIVE_SOURCE_CLOSURE_OR_OFFLINE_RED'),
])
def test_g6_missing_report_or_network_red_keeps_raw_after_actual_nested_and_outer_FIN(tmp_path,monkeypatch,case,expected_reason):
    args,_unused,artifacts,prepared,_interpreters=carrier_control(tmp_path,monkeypatch,case)
    args.gate='governed312'
    args.verified_prerequisites={'authenticated_receipts':[{'gate':'G0',
        'preserved_heavy_corpora':{'312':[]},'explicit_unit_fixture_not_qualification':True}]}
    blocked=artifacts/'explicit-unit-blocked-binding.json'
    blocked.write_text(json.dumps({'interpreter':sys.executable,'explicit_unit_fixture_not_qualification':True}))
    prepared[0]['blocked_binding']=str(blocked)
    helper=artifacts/'drivers/governed_outer.py'
    helper.write_text(helper.read_text()+'from scripts import rc6_controlled_native_child_manager as manager\n'
        'def native_kernel_closed(value): return manager.managed_custody_closed(value)\n')
    own_controls=artifacts/'real-governed-controls';own_controls.mkdir(mode=0o700)
    run=NativeGovernedControlRun(own_controls,case,args)
    # The real run's controls parent is separate from the helper's unused run.
    code,result=carrier.run_gov(args,run,artifacts,prepared,None,None,None)
    assert code==1 and result['reason']==expected_reason
    assert result['native_FIN_payload_safe'] is True and result['whole_Gov_claim'] is False
    assert len(run.namespaces)==1 and not run.namespaces[0].path.exists()
    destination=Path(result['raw_root'])
    manifest=json.loads((destination/'manifest.json').read_bytes())
    assert manifest['actual_owned_fin_closed'] is True
    for phase in ('collection','execution'):
        phase_kernel=json.loads(carrier.captured_file(destination,'native-gov/'+phase+'.kernel.json').read_bytes())
        assert phase_kernel['actual_child_reaped'] is True
        assert phase_kernel['supervisor_pid']==result['native_phases'][phase]['kernel']['supervisor_pid']
        wire=carrier.captured_file(destination,'native-gov/original-unique-control-'+phase+'.raw').read_bytes()
        assert wire==b'RAW-original-'+phase.encode()+b'\x00\xff\n'
    assert b'EXPLICIT_LITERAL_RED' in carrier.captured_file(destination,'native-gov/porota-governed-tests.xml').read_bytes()
    assert carrier.captured_file(destination,'fullGov312.native.log').read_bytes()==b'original synthetic whole-G6 log\n'
    assert all(hashlib.sha256((destination/row['capture_file']).read_bytes()).hexdigest()==row['sha256']
               for row in manifest['files'])
