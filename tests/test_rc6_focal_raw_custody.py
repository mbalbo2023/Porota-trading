"""Real owned child FIN/capture with tiny carrier controls on logical RED.

The child writes explicit synthetic phase controls only. Git/capacity/receipt
stubs are test boundaries; no product, canonical Actions or gate qualifies.
The namespace, native supervisor FIN, capture hashes and cleaner are genuine.
"""
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import rc6_authenticated_fixture_lifecycle as lifecycle
from scripts import rc6_controlled_governed_runner as governed
from scripts import rc6_material_carrier as carrier


ROOT=Path(__file__).absolute().parents[1]
_PHASE_WRITER=r'''
import json,runpy,sys
from pathlib import Path
g=runpy.run_path(sys.argv[1]);output=Path(sys.argv[2]);phase=sys.argv[3];case=sys.argv[4]
initial=g['child_infrastructure_snapshot']()
assert all(value is None for value in initial.values())
(output/'original-unique-control.raw').write_bytes(b'RAW-original-'+phase.encode()+b'\x00\xff\n')
identity={'nodeid':'tests/tiny.py::test_literal','classname':'tests.tiny','name':'test_literal'}
observed={'items':[identity],'complete_corpus':[identity],'deferred_material_nodes':[],
    'pytest_exit_code':1 if phase=='execution' else 0,
    'inet_socket_attempts':[{'explicit_synthetic_network_red':True}]
        if phase=='execution' and case=='network_red' else [],
    'unexpected_product_imports':[],'source_namespace_exact_before_after':not (phase=='execution' and case=='source_red'),
    'phase_validation_errors':['EXPLICIT_SYNTHETIC_SOURCE_RED'] if phase=='execution' and case=='source_red' else [],
    'native_execution_coverage_exact':True,'control_only_not_product_or_gate_qualification':True}
if phase=='execution':
    (output/'focal-tests.xml').write_bytes(b'<testsuites><testsuite tests="1" failures="1" errors="0" skipped="0">'
        b'<testcase classname="tests.tiny" name="test_literal"><failure message="EXPLICIT_LITERAL_RED"/>'
        b'</testcase></testsuite></testsuites>')
final=g['finalize_child_infrastructure'](initial)
assert final['status']=='GREEN' and final['kernel_echild_before_phase_return'] is True
if case=='fin_unknown': final['status']='RED'
g['publish'](output/(phase+'.child-finalization.json'),g['canonical'](final))
if not (phase=='execution' and case=='missing_observations'):
    g['publish'](output/(phase+'.observations.json'),g['canonical'](observed))
print('original synthetic native log '+phase,flush=True)
sys.exit(1 if phase=='execution' else 0)
'''


def real_binding():
    return {'candidate_sha':'a'*40,'candidate_tree':'b'*40,'producer':'UNIT_FOCAL_CUSTODY_CONTROL',
        'attempt_id':'control-attempt','owner_id':'control-unit-only','runner_class':'DIAGNOSTIC',
        'workload_fingerprint':hashlib.sha256(b'UNIT_METADATA_ONLY_NO_FINANCIAL_WORKLOAD').hexdigest()}


class NativeControlRun:
    def __init__(self,root,case):
        self.case=case;self.fins={};self.sealed_groups=[];self.namespaces=[]
        self.control=root/'controls';self.control.mkdir(mode=0o700)
        self.manager=runpy.run_path(str(lifecycle.DRIVER))

    def __call__(self,command,*,cwd,label,limit,env,namespace):
        self.namespaces.append(namespace)
        phase=command[command.index('--phase')+1]
        output=Path(command[command.index('--output-root')+1])
        child=[sys.executable,'-I','-B','-c',_PHASE_WRITER,
            str(ROOT/'scripts/rc6_controlled_governed_runner.py'),str(output),phase,self.case]
        kernel,fin=lifecycle.execute_owned(namespace,child,cwd=cwd,environ=dict(os.environ),
            log_relative=label+'.native.log',timeout_seconds=limit,fin_label=label)
        self.fins[namespace.nonce]=fin
        return {'kernel':kernel,'returncode':kernel['returncode']}


def carrier_control(tmp_path,monkeypatch,case):
    namespaces=tmp_path/'own-namespaces';namespaces.mkdir(mode=0o700)
    artifacts=tmp_path/'original-sealed-controls';artifacts.mkdir(mode=0o700)
    drivers=artifacts/'drivers';drivers.mkdir()
    (drivers/'governed_outer.py').write_text('from scripts.rc6_material_focal import require_original_phase_fin\n'
        'from scripts.rc6_controlled_governed_runner import require\n'
        'def native_finalization_closed(value):\n'
        '    try: require_original_phase_fin({"require":require},value)\n'
        '    except (ValueError,KeyError,TypeError): return False\n'
        '    return True\n')
    source=tmp_path/'explicit-tiny-git';source.mkdir(mode=0o700)
    scripts=source/'scripts';scripts.mkdir()
    (scripts/'rc6_controlled_governed_runner.py').write_bytes((ROOT/'scripts/rc6_controlled_governed_runner.py').read_bytes())
    (scripts/'rc6_controlled_governed_runner.py').chmod(0o644)
    environment=dict(os.environ,GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull)
    def git(*arguments):
        return subprocess.check_output(['git','-C',str(source),*arguments],env=environment).decode().strip()
    git('init','--template=','-q');git('config','user.name','Explicit custody unit')
    git('config','user.email','fixture@example.invalid');git('add','-A');git('commit','-qm','tiny control input')
    args=SimpleNamespace(gate='focal312',source_sha=git('rev-parse','HEAD'),source_tree=git('rev-parse','HEAD^{tree}'),
        repo_root=source,owner_session='UNIT_ONLY',capacity_peaks={})
    binding=real_binding();binding.update(candidate_sha=args.source_sha,candidate_tree=args.source_tree)
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:namespaces))
    monkeypatch.setattr(carrier,'capacity_binding',lambda a,producer:dict(binding))
    monkeypatch.setattr(carrier,'readmit_automatic_pr',lambda *args:None)
    monkeypatch.setattr(carrier,'heavy_preflight',lambda *args:(dict(binding),{},
        {'capacity':{'status':'GREEN'},'explicit_unit_boundary_only':True}))
    monkeypatch.setattr(carrier,'owned_lease_evidence',lambda *args:{'explicit_unit_boundary_only':True})
    monkeypatch.setattr(carrier.architectural,'productive_contract',lambda root:'c'*64)
    monkeypatch.setattr(carrier.architectural,'receipt_base',lambda gate,**kwargs:
        {'scope':'UNIT_CARRIER_CONTROL_ONLY_NOT_GATE_QUALIFICATION','gate':gate,**kwargs})
    run=NativeControlRun(artifacts,case)
    return args,run,artifacts,[{'epoch':'312','source_root':str(source)}],{'312':sys.executable}


@pytest.mark.parametrize('case,reason',[
    ('missing_observations','SEALED_RAW_LITERAL_PATH_MISSING_OR_DUPLICATE'),
    ('network_red','FOCAL_NATIVE_IMPORT_OR_NETWORK_RED'),
    ('source_red','FOCAL_NATIVE_SOURCE_VALIDATION_RED'),
])
def test_logical_red_keeps_original_raw_after_authentic_FIN_before_validation(tmp_path,monkeypatch,case,reason):
    args,run,artifacts,prepared,interpreters=carrier_control(tmp_path,monkeypatch,case)
    code,result=carrier.focal(args,run,artifacts,prepared,interpreters)
    assert code==1 and result['native_FIN_payload_safe'] is True
    assert result['epochs'][0]['reason']==reason
    assert len(run.namespaces)==2
    assert all(not namespace.path.exists() for namespace in run.namespaces)
    assert len(run.sealed_groups)==2
    for phase in ('collection','execution'):
        destination=artifacts/('focal312-'+phase+'-sealed')
        manifest=json.loads((destination/'manifest.json').read_bytes())
        assert manifest['actual_owned_fin_closed'] is True
        assert all(hashlib.sha256((destination/row['capture_file']).read_bytes()).hexdigest()==row['sha256']
                   for row in manifest['files'])
        preserved=carrier.captured_file(destination,'focal/original-unique-control.raw')
        assert preserved.read_bytes()==b'RAW-original-'+phase.encode()+b'\x00\xff\n'
        log=carrier.captured_file(destination,'focal312-'+phase+'.native.log')
        assert log.read_bytes()==b'original synthetic native log '+phase.encode()+b'\n'
    execution=artifacts/'focal312-execution-sealed'
    assert b'EXPLICIT_LITERAL_RED' in carrier.captured_file(execution,'focal/focal-tests.xml').read_bytes()
    assert result['epochs'][0]['architectural_receipt']['native_exit_code']==1
    assert result['epochs'][0]['architectural_receipt']['scope']=='UNIT_CARRIER_CONTROL_ONLY_NOT_GATE_QUALIFICATION'


def test_unknown_original_phase_fin_preserves_namespace_and_vetoes_capture(tmp_path,monkeypatch):
    args,run,artifacts,prepared,interpreters=carrier_control(tmp_path,monkeypatch,'fin_unknown')
    with pytest.raises(ValueError,match='^FOCAL_FINALIZER_UNKNOWN_PAYLOAD_VETO$'):
        carrier.focal(args,run,artifacts,prepared,interpreters)
    assert len(run.namespaces)==1 and run.namespaces[0].path.is_dir()
    assert run.sealed_groups==[]
    assert not (artifacts/'focal312-collection-sealed').exists()
    assert (run.namespaces[0].path/'focal/original-unique-control.raw').read_bytes()==b'RAW-original-collection\x00\xff\n'
