"""Small native pytest/source-control regressions; no financial producer."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import xml.etree.ElementTree as ET

import pytest

from scripts import rc6_architectural_gates as gates
from scripts import rc6_controlled_governed_runner as governed
from scripts import rc6_material_focal as focal


def closed_fin():
    return {'status':'GREEN','finalization_thread_finished':True,
        'kernel_echild_before_phase_return':True,'signal_guard_installed_and_witnessed':True,
        'termination_signal_attempts':[],'forced_termination_attempted':False,'signal_vetoed':False,
        'wall_seconds':0.01,'errors':[]}


def metadata_pin():
    fields={name:1 for name in governed.FIELDS}
    return {'files':{'rc6_tiny_metadata.py':{'bytes':4,'sha256':hashlib.sha256(b'PAPER').hexdigest(),
        'git_blob':'a'*40,'git_mode':'100644','stat_fields':fields}},
        'physical_directories':{'.':dict(fields)},'git_metadata':{}}


def source_control(tmp_path, monkeypatch, *, pin_error=None, compare_error=None):
    root=tmp_path/'explicit-source-metadata';root.mkdir()
    output=tmp_path/'post-FIN-controls';output.mkdir()
    before=metadata_pin();after=json.loads(json.dumps(before));events=[]
    module=ModuleType('rc6_tiny_metadata');module.__file__=str(root/'rc6_tiny_metadata.py')
    monkeypatch.setitem(sys.modules,module.__name__,module)

    def pin(*arguments):
        events.append('source-pin')
        if pin_error is not None:raise pin_error
        return after

    def compare(original,current):
        events.append('compare-source')
        if compare_error is not None:raise compare_error
        return governed.compare_source(original,current)

    def duplicate_capture(*arguments):
        raise AssertionError('A post-pin import performed a duplicate payload capture')

    g={'require':governed.require,'publish':governed.publish,'canonical':governed.canonical,
        'source_pin':pin,'compare_source':compare,'safe_path':governed.safe_path,'capture':duplicate_capture}
    observations={'unexpected_product_imports':[],'actual_product_imports':[]}
    return root,output,before,after,g,observations,events


@pytest.mark.parametrize('field,value',[
    ('status','RED'),('finalization_thread_finished',False),('kernel_echild_before_phase_return',False),
    ('signal_guard_installed_and_witnessed',False),('termination_signal_attempts',[{'signal':15}]),
    ('forced_termination_attempted',True),('signal_vetoed',True),('wall_seconds',5.01),
    ('errors',[{'reason':'ORIGINAL_FIN_RED'}]),
])
def test_unknown_real_fin_vetoes_all_post_source_reads_and_publication(tmp_path,monkeypatch,field,value):
    root,output,before,_after,g,observations,events=source_control(tmp_path,monkeypatch)
    final=closed_fin();final[field]=value
    with pytest.raises(ValueError,match='^PHASE_CHILD_INFRASTRUCTURE_NOT_GENUINELY_FINALIZED$'):
        focal.capture_post_fin_source(g,root,before,source_sha='a'*40,source_tree='b'*40,
            output=output,phase='execution',finalization=final,observations=observations)
    assert events==[] and list(output.iterdir())==[]


def test_post_fin_import_closure_reuses_one_complete_source_capture(tmp_path,monkeypatch):
    root,output,before,_after,g,observations,events=source_control(tmp_path,monkeypatch)
    result=focal.capture_post_fin_source(g,root,before,source_sha='a'*40,source_tree='b'*40,
        output=output,phase='execution',finalization=closed_fin(),observations=observations)
    assert result['source_namespace_exact_before_after'] is True
    assert result['post_fin_source_validation_error'] is None
    assert events==['source-pin','compare-source']
    assert observations['actual_product_imports']==[{'module':'rc6_tiny_metadata',
        'path':'rc6_tiny_metadata.py','sha256':before['files']['rc6_tiny_metadata.py']['sha256']}]
    assert json.loads((output/'execution.source-before.index.json').read_bytes())==before
    assert json.loads((output/'execution.source-validation.json').read_bytes())==result


def test_source_compare_red_retains_original_indexes_and_vetoes_import_consumption(tmp_path,monkeypatch):
    root,output,before,after,g,observations,events=source_control(tmp_path,monkeypatch)
    after['files']['rc6_tiny_metadata.py']['stat_fields']['st_blocks']+=8
    result=focal.capture_post_fin_source(g,root,before,source_sha='a'*40,source_tree='b'*40,
        output=output,phase='execution',finalization=closed_fin(),observations=observations)
    assert result['source_namespace_exact_before_after'] is False
    assert result['observed_CODE_atime_changes'] is None
    assert result['post_fin_source_validation_error']=={'class':'ValueError',
        'reason':'SOURCE_BYTES_MODE_BLOB_OR_STABLE_CUSTODY_CHANGED:rc6_tiny_metadata.py'}
    assert events==['source-pin','compare-source']
    assert observations['actual_product_imports']==[]
    assert json.loads((output/'execution.source-before.index.json').read_bytes())==before
    assert json.loads((output/'execution.source-after.index.json').read_bytes())==after


def test_source_pin_failure_preserves_before_without_inventing_after_or_retry(tmp_path,monkeypatch):
    root,output,before,_after,g,observations,events=source_control(tmp_path,monkeypatch,
        pin_error=ValueError('TRACKED_SOURCE_CHANGED'))
    result=focal.capture_post_fin_source(g,root,before,source_sha='a'*40,source_tree='b'*40,
        output=output,phase='execution',finalization=closed_fin(),observations=observations)
    assert result['source_namespace_exact_before_after'] is False
    assert result['post_fin_source_validation_error']['reason']=='TRACKED_SOURCE_CHANGED'
    assert events==['source-pin'] and observations['actual_product_imports']==[]
    assert (output/'execution.source-before.index.json').is_file()
    assert not (output/'execution.source-after.index.json').exists()


_NATIVE_OBSERVER = r'''
import json
from pathlib import Path
import sys
sys.path.insert(0,sys.argv[1])
import pytest
from scripts.rc6_material_focal import FocalObserver
suite=Path(sys.argv[2]);stop_early=sys.argv[3]=='yes'
common=['--noconftest','-c','/dev/null','-q','-p','no:cacheprovider','-o','junit_family=legacy',str(suite)]
collection=FocalObserver('cheap')
collected=int(pytest.main([*common,'--collect-only'],plugins=[collection]))
execution=FocalObserver('cheap')
executed=int(pytest.main([*common,*(['-x'] if stop_early else []),
    '--junitxml='+str(suite/'original-native.xml')],plugins=[execution]))
(suite/'original-native-identities.json').write_text(json.dumps({
    'collection':{'items':collection.items,'pytest_exit_code':collected},
    'execution':{'items':execution.items,'pytest_exit_code':executed},
    'started':execution.started_nodeids,'finished':execution.finished_nodeids,
    'reports':execution.reports,'coverage_exact':execution.execution_coverage_exact()}))
'''


@pytest.mark.parametrize('stop_early',[False,True])
def test_native_red_junit_and_actual_execution_identities_are_preserved(tmp_path,stop_early):
    suite=tmp_path/'tiny-native-pytest';suite.mkdir()
    (suite/'test_literal.py').write_text("import pytest\n"
        "def test_original_red():\n    raise AssertionError('LITERAL_NATIVE_RED')\n"
        "@pytest.mark.parametrize('value',['dot.name','slash/value'])\n"
        "def test_original_identity(value):\n    assert value\n")
    process=subprocess.run([sys.executable,'-I','-B','-c',_NATIVE_OBSERVER,
        str(Path(__file__).absolute().parents[1]),str(suite),'yes' if stop_early else 'no'],
        env=dict(os.environ,PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONDONTWRITEBYTECODE='1'),
        capture_output=True,text=True,timeout=20)
    assert process.returncode==0,process.stdout+process.stderr
    originals=json.loads((suite/'original-native-identities.json').read_bytes())
    raw=(suite/'original-native.xml').read_bytes();before_hash=hashlib.sha256(raw).hexdigest()
    assert originals['collection']['pytest_exit_code']==0
    assert originals['execution']['pytest_exit_code']==1
    assert originals['collection']['items']==originals['execution']['items']
    assert len(originals['collection']['items'])==3
    assert originals['started']==originals['finished']
    assert len(originals['started'])==(1 if stop_early else 3)
    assert originals['coverage_exact'] is (not stop_early)
    assert any(row['when']=='call' and row['outcome']=='failed' for row in originals['reports'])
    cases=list(ET.fromstring(raw).iter('testcase'))
    assert len(cases)==len(originals['started'])
    if stop_early:
        with pytest.raises(ValueError,match='^GATE_COLLECTION_JUNIT_IDENTITY_MISMATCH$'):
            gates.validate_junit(originals['collection'],originals['execution'],raw,require_green=False)
    else:
        result=gates.validate_junit(originals['collection'],originals['execution'],raw,require_green=False)
        assert result['cases']==3 and result['failure']==1
    assert hashlib.sha256((suite/'original-native.xml').read_bytes()).hexdigest()==before_hash


_NATIVE_CAS_REPORTS = r'''
import json, runpy, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import pytest
from scripts.rc6_material_focal import FocalObserver
from scripts import rc6_archive_reader_review as readers, rc6_cas_original_comparison as comparison
g = runpy.run_path(str(Path(sys.argv[1])/'scripts/rc6_controlled_governed_runner.py'))
initial = g['child_infrastructure_snapshot']()
observer = FocalObserver('cheap')
nodes = sorted(readers.REQUIRED_NODEIDS) + ['tests/test_rc6_cas_original_comparison.py::'+name for name in sorted(comparison.ACK_CASES)]
rc = int(pytest.main(['--noconftest','-c','/dev/null','--rootdir='+sys.argv[1],'-q',
    '-p','no:cacheprovider','-o','junit_family=legacy','--basetemp='+sys.argv[2]+'/p',*nodes], plugins=[observer]))
fin = g['finalize_child_infrastructure'](initial)
# These are original executed reports/FIN. Source fields are explicitly unit
# metadata; this control cannot authenticate a native G1 Source or artifact.
report = {'phase':'execution','source_sha':sys.argv[3],'source_tree':sys.argv[4],
    'source_namespace_exact_before_after':True,'pytest_exit_code':rc,
    'native_execution_coverage_exact':observer.execution_coverage_exact(),
    'inet_socket_attempts':[],'unexpected_product_imports':[],'phase_validation_errors':[],
    'child_infrastructure_finalization':fin,'items':observer.items,
    'actual_execution_started_nodeids':observer.started_nodeids,
    'actual_execution_finished_nodeids':observer.finished_nodeids,
    'original_pytest_reports':observer.reports,'scope':'LOCAL_REAL_PYTEST_REPORTS_WITH_UNIT_SOURCE_FIELDS_NOT_G1'}
Path(sys.argv[2]+'/original.json').write_bytes(g['canonical'](report))
sys.exit(rc)
'''


def test_original_green_pytest_reports_and_fin_drive_captured_private_reviews_without_gate_approval(tmp_path):
    from scripts import rc6_archive_reader_review as readers
    from scripts import rc6_controlled_native_child_manager as manager
    from tests.test_rc6_archive_reader_review import _pinned_source
    work = tmp_path/'real-cas-controls';work.mkdir()
    root = Path(__file__).absolute().parents[1]
    source_sha = governed.git(root, 'rev-parse', 'HEAD').decode().strip()
    source_tree = governed.git(root, 'rev-parse', 'HEAD^{tree}').decode().strip()
    log = work/'original-native.log'
    kernel = manager.managed_native_child(
        [sys.executable,'-I','-B','-c',_NATIVE_CAS_REPORTS,str(root),str(work),source_sha,source_tree],
        root, log, dict(os.environ,PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONDONTWRITEBYTECODE='1'), 90)
    assert manager.managed_phase_green(kernel), log.read_text()
    wire = (work/'original.json').read_bytes()
    report = json.loads(wire)
    pin, _ = _pinned_source(tmp_path)
    pin.update(source_sha=source_sha, source_tree=source_tree)
    g = {'require':governed.require,'canonical':governed.canonical,'capture':governed.capture,'publish':governed.publish}
    records = focal.emit_cas_reviews(g,tmp_path,pin,report,wire,work,readers.CAS_REVIEW_MODULES)
    assert readers.REQUIRED_NODEIDS <= {row['nodeid'] for row in report['items']}
    assert all(row['outcome'] == 'passed' for row in report['original_pytest_reports'])
    assert all(record['execution_sha256'] == hashlib.sha256(wire).hexdigest() for record in records.values())
    assert records['cas_private_ack_review']['contract_review_approved'] is False
    assert records['cas_reader_review']['native_writer_contract_approved'] is False


def _cas_review_emission(tmp_path):
    from tests.test_rc6_archive_reader_review import _pinned_source
    from tests.test_rc6_cas_original_comparison import _ack_execution_metadata
    from scripts import rc6_archive_reader_review as readers
    pin, _ = _pinned_source(tmp_path)
    report = _ack_execution_metadata()  # Explicit unit metadata, not G1/artifact authority.
    output = tmp_path / 'records'
    output.mkdir()
    g = {'require': governed.require, 'canonical': governed.canonical,
         'capture': governed.capture, 'publish': governed.publish}
    wire = governed.canonical(report)
    governed.publish(output/'execution.observations.json', wire)
    return pin, report, wire, output, g, readers


def test_cas_review_emitter_preserves_original_wire_and_captured_references(tmp_path):
    from scripts import rc6_material_carrier as carrier
    pin, report, wire, output, g, readers = _cas_review_emission(tmp_path)
    records = focal.emit_cas_reviews(g, tmp_path, pin, report, wire, output, readers.CAS_REVIEW_MODULES)
    sealed = tmp_path/'sealed';sealed.mkdir()
    rows = []
    for number, (role, filename) in enumerate(readers.CAS_REVIEW_FILES.items()):
        raw = (output/filename).read_bytes()
        capture_file = f'{number:04d}.raw'
        (sealed/capture_file).write_bytes(raw)
        rows.append({'relative_source': 'focal/'+filename, 'capture_file': capture_file,
                     'sha256': hashlib.sha256(raw).hexdigest()})
    (sealed/'manifest.json').write_text(json.dumps({'files': rows}))
    refs = carrier.captured_cas_review_references(sealed, source=tmp_path, source_pin=pin,
        execution_wire=wire, capture=governed.capture)
    assert set(refs) == set(records) == set(readers.CAS_REVIEW_FILES)
    assert (output/'execution.observations.json').read_bytes() == wire
    assert all(record.get('native_v4_write_enabled') is False for record in records.values())
    (sealed/'0001.raw').write_bytes(b'changed')
    with pytest.raises(ValueError, match='SEALED_RAW_BYTES_OR_LITERAL_PATH_REBOUND'):
        carrier.captured_cas_review_references(sealed, source=tmp_path, source_pin=pin,
            execution_wire=wire, capture=governed.capture)


@pytest.mark.parametrize('attack', ('partial', 'fin', 'source', 'skip', 'setup', 'teardown', 'wire'))
def test_cas_review_emitter_never_reads_source_or_emits_positive_for_incomplete_evidence(tmp_path, attack):
    pin, report, wire, output, g, readers = _cas_review_emission(tmp_path)
    original_wire = (output/'execution.observations.json').read_bytes()
    selected = set(readers.CAS_REVIEW_MODULES)
    if attack == 'partial': selected.pop()
    elif attack == 'fin': report['child_infrastructure_finalization']['status'] = 'RED'
    elif attack == 'source': report['source_tree'] = 'c'*40
    elif attack in ('skip', 'setup', 'teardown'):
        phase = 'call' if attack == 'skip' else attack
        next(row for row in report['original_pytest_reports'] if row['when'] == phase)['outcome'] = 'skipped' if attack == 'skip' else 'failed'
    elif attack == 'wire': wire += b'{}'
    if attack not in ('wire', 'partial'): wire = governed.canonical(report)
    g['capture'] = lambda *args, **kw: pytest.fail('PREMATURE_SOURCE_READ')
    if attack == 'partial':
        assert focal.emit_cas_reviews(g, tmp_path, pin, report, wire, output, selected) == {}
    else:
        with pytest.raises((ValueError, json.JSONDecodeError, KeyError)):
            focal.emit_cas_reviews(g, tmp_path, pin, report, wire, output, selected)
    assert (output/'execution.observations.json').read_bytes() == original_wire
    assert not any((output/name).exists() for name in readers.CAS_REVIEW_FILES.values())
