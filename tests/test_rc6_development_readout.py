"""Synthetic byte-parser/transport adversaries; never a live FIN or RC6 proof."""
import io
import json
import zipfile

import pytest
from scripts import rc6_development_readout as readout
from scripts.rc6_actions_custody import canonical, digest


def container(change=None):
    xml=b'<testsuites><testsuite tests="1" failures="1" errors="0" skipped="0"><testcase classname="original" name="case"><failure message="original failure">original failure</failure></testcase></testsuite></testsuites>'
    result={'schema':'porota.rc6.development-checks.v1','source_sha':'a'*40,'source_tree':'b'*40,
        'status':'RED','G0_G8_qualification':False,'Product157_qualified':False,
        'fullSource_unchanged':True,'identities_equal':True,'epochs':[{'epoch':'311','passed':False,
        'kernel':{'returncode':1,'owned_children_exhaustion_verified':True},
        'cleanup':{'namespace_removed':True},'junit':{'cases':1,'failures':1,'errors':0,'skipped':0,'sha256':digest(xml)}}]}
    manifest={'schema':'porota.rc6.generated-fixture-required-capture.v1','actual_owned_fin_closed':True,
        'binding':{'candidate_sha':'a'*40,'candidate_tree':'b'*40},
        'files':[{'relative_source':'junit.xml','capture_file':'0001.raw','bytes':len(xml),'sha256':digest(xml)}]}
    if change=='source':result['source_sha']='c'*40
    if change=='qualification':result['G0_G8_qualification']=True
    if change=='manifest':manifest['files'][0]['sha256']='0'*64
    if change=='binding':manifest['binding']['candidate_tree']='d'*40
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        archive.writestr('result.json',canonical(result))
        archive.writestr('311/manifest.json',canonical(manifest))
        archive.writestr('311/0001.raw',xml)
    raw=stream.getvalue()
    origin={'artifact_id':7,'run_id':13,'source_sha':'a'*40,'source_tree':'b'*40,
        'bytes':len(raw),'sha256':digest(raw),'conclusion':'failure'}
    return raw,origin,result


def test_original_red_readout_preserves_binding_and_failure_without_new_producer():
    raw,origin,_=container()
    result=readout.inspect_sealed(raw,origin)
    assert result['status']=='RED' and result['source_sha']=='a'*40
    assert result['producer_reexecuted'] is result['FIN_reconstructed'] is result['G0_G8_qualification'] is False
    assert result['epochs'][0]['junit']['failures']==1
    assert result['epochs'][0]['failures'][0]['text'].count('original failure')==2


@pytest.mark.parametrize('change',['source','qualification','manifest','binding'])
def test_original_readout_rejects_rebound_or_unsealed_controls(change):
    raw,origin,_=container(change)
    with pytest.raises(ValueError,match='READOUT_'):
        readout.inspect_sealed(raw,origin)


def test_long_original_traceback_keeps_terminal_exception_without_unbounded_log():
    raw,origin,_=container()
    with zipfile.ZipFile(io.BytesIO(raw)) as original:
        files={i.filename:original.read(i) for i in original.infolist()}
    xml=files['311/0001.raw'].replace(b'original failure</failure>',
        b'ORIGINAL_TRACEBACK_START'+b'x'*9000+b'TERMINAL_ORIGINAL_EXCEPTION</failure>')
    files['311/0001.raw']=xml
    result=json.loads(files['result.json']);result['epochs'][0]['junit']['sha256']=digest(xml)
    files['result.json']=canonical(result)
    manifest=json.loads(files['311/manifest.json']);manifest['files'][0].update(bytes=len(xml),sha256=digest(xml))
    files['311/manifest.json']=canonical(manifest)
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        for name,body in files.items():archive.writestr(name,body)
    wire=stream.getvalue();origin.update(bytes=len(wire),sha256=digest(wire))
    text=readout.inspect_sealed(wire,origin)['epochs'][0]['failures'][0]['text']
    assert 'ORIGINAL_TRACEBACK_START' in text and text.endswith('TERMINAL_ORIGINAL_EXCEPTION')
    assert '[TRACEBACK_MIDDLE_OMITTED]' in text and len(text)<3600


@pytest.mark.parametrize('key',['bytes','sha256'])
def test_artifact_container_rebound_is_rejected_before_parsing(key):
    raw,origin,_=container();origin[key]=0 if key=='bytes' else '0'*64
    with pytest.raises(ValueError,match='ORIGINAL_CONTAINER'):
        readout.inspect_sealed(raw,origin)


def test_origin_metadata_rebound_prevents_any_zip_read():
    raw,origin,_=container();calls=[]
    class Reader:
        def request(self,path,**kwargs):
            calls.append(path)
            assert path=='/actions/artifacts/7'
            return {'digest':'sha256:'+digest(raw),'size_in_bytes':len(raw),'expired':False,
                'workflow_run':{'id':13,'head_sha':'c'*40}}
    plan={'schema':'porota.rc6.sealed-development-readout-plan.v1',
        'repository':'mbalbo2023/Porota-trading','origin':origin}
    with pytest.raises(ValueError,match='AUTHENTIC_ORIGIN'):
        readout.readout(Reader(),plan,lambda:None)
    assert calls==['/actions/artifacts/7']


def test_native_summary_hashes_original_preserved_result_and_retains_red(tmp_path,capsys):
    from scripts import rc6_development_checks as development
    _,_,original=container()
    development.preserve_result(tmp_path,original)
    raw=(tmp_path/'result.json').read_bytes()
    summary=json.loads(capsys.readouterr().out.split('RC6_DEVELOPMENT_NATIVE_RESULT=',1)[1])
    assert raw==canonical(original) and summary['original_result_sha256']==digest(raw)
    assert summary['status']=='RED' and summary['epochs'][0]['kernel']['returncode']==1
    assert summary['epochs'][0]['junit']['failures']==1
    assert summary['G0_G8_qualification'] is summary['Product157_qualified'] is False


def test_readout_is_manual_owner_admitted_and_has_no_tooling_or_producer_step():
    from pathlib import Path
    import yaml
    root=Path(__file__).absolute().parents[1]
    workflow=yaml.load((root/'.github/workflows/rc6-unified-candidate-tests.yml').read_text(),Loader=yaml.BaseLoader)
    job=workflow['jobs']['evidence-readout']
    assert 'workflow_dispatch' in job['if'] and "github.actor == 'mbalbo2023'" in job['if']
    assert job['timeout-minutes']=='5'
    assert all('setup-python' not in step.get('uses','') for step in job['steps'])
    run='\n'.join(step.get('run','') for step in job['steps'])
    assert '--launch-receipt-url' in run and 'rc6_development_readout.py' in run
    assert 'pip install' not in run and '-m pytest' not in run
