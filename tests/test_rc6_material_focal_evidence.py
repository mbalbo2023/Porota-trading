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
