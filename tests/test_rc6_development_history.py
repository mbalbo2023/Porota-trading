"""Cheap history admission controls and real owned NONROOT Git-only FIN.

The miniature Git fixture has its own explicit test pins. It is never a
production Source, GitHub authorization or G0/G8 qualification receipt.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from scripts import rc6_development_checks as development


def mini_source(tmp_path):
    repo=tmp_path/'Source';repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git','--no-replace-objects','-C',str(repo),*args],
            env={**development.history_environment(),'GIT_AUTHOR_NAME':'Unit','GIT_COMMITTER_NAME':'Unit',
                'GIT_AUTHOR_EMAIL':'unit@invalid','GIT_COMMITTER_EMAIL':'unit@invalid'},stderr=subprocess.DEVNULL).decode().strip()
    git('init','-q');raw=repo/'notes/rc6-evidence/unit.raw';raw.parent.mkdir(parents=True);raw.write_bytes(b'unit original RAW\n')
    legacy=repo/'legacy.py';legacy.write_bytes(b'original = True\n')
    git('add','.');git('commit','-qm','original fixture');anchor=git('rev-parse','HEAD')
    pins={'raw_anchor':anchor,'raw_tree':git('rev-parse','HEAD^{tree}'),
        'raw_roots':['notes/rc6-evidence/'],'legacy_anchor':anchor,
        'legacy_blobs':{'legacy.py':git('rev-parse','HEAD:legacy.py')},
        'legacy_sha256':{'legacy.py':hashlib.sha256(legacy.read_bytes()).hexdigest()}}
    legacy.write_bytes(b'candidate = True\n');git('add','legacy.py');git('commit','-qm','candidate fixture')
    return repo,git('rev-parse','HEAD'),git('rev-parse','HEAD^{tree}'),pins


def bootstrap_fixture(tmp_path,monkeypatch):
    repo,sha,tree,pins=mini_source(tmp_path)
    output=tmp_path/'control';output.mkdir(mode=0o700)
    monkeypatch.setenv('GITHUB_RUN_ID','17')
    monkeypatch.setattr(development,'history_requirements',lambda:copy.deepcopy(pins))
    parent=Path(os.environ.get('RC6_UNIT_NAMESPACE_PARENT','/workspace'))
    monkeypatch.setattr(development,'short_control_parent',lambda repo:parent)
    original=development.lifecycle.execute_owned
    def offline_original(namespace,command,**kwargs):
        # The real supervisor/FIN/capture/cleanup remain unchanged. This test
        # child additionally denies all public network attempts.
        command=list(command)
        command[4]='import json,sys;sys.path.insert(0,sys.argv[1]);from scripts import rc6_development_checks as d;d.history_fetch_command=lambda *a,**k:(_ for _ in ()).throw(ValueError("UNIT_PUBLIC_NETWORK_FORBIDDEN"));d.history_worker(json.loads(sys.argv[2]))'
        return original(namespace,command,**kwargs)
    monkeypatch.setattr(development.lifecycle,'execute_owned',offline_original)
    kwargs={'source_sha':sha,'source_tree':tree,'owner_session':'UNIT_ONLY','plan_sha256':'a'*64,'owner_check':lambda:None}
    return repo,output,kwargs,pins


def test_original_owned_nonroot_positive_history_is_offline_and_preserves_code(tmp_path,monkeypatch):
    repo,output,kwargs,pins=bootstrap_fixture(tmp_path,monkeypatch)
    before={str(path.relative_to(repo)):path.read_bytes() for path in repo.rglob('*') if path.is_file() and '.git' not in path.parts}
    result=development.prepare_history(repo,output,**kwargs)
    after={str(path.relative_to(repo)):path.read_bytes() for path in repo.rglob('*') if path.is_file() and '.git' not in path.parts}
    assert before==after
    assert result['status']=='PASS_GIT_HISTORY_ONLY' and result['actual_owned_fin_closed'] is True
    assert result['cleanup']['namespace_removed'] is True and result['cleanup_credit_claimed'] is False
    assert result['worker']['unshallow_fetched'] is False and result['worker']['hydrated_original_blob_oids']==[]
    assert result['worker']['kernel_file_limit_bytes']==development.HISTORY_FILE_LIMIT
    assert result['parent_source_after']=={'source_sha':kwargs['source_sha'],'source_tree':kwargs['source_tree']}
    assert result['git_after']['continuous_peak_proved'] is False and result['G0_G8_qualification'] is False
    capture=json.loads((output/'history-owned/manifest.json').read_bytes())
    assert capture['actual_owned_fin_closed'] is True
    assert any(row['relative_source']=='producer-owned-fin-development-history.json' for row in capture['files'])


@pytest.mark.parametrize('fault',['source_sha','source_tree','raw_tree','legacy_blob','legacy_hash'])
def test_original_owned_history_red_preserves_native_raw_before_cleanup(tmp_path,monkeypatch,fault):
    repo,output,kwargs,pins=bootstrap_fixture(tmp_path,monkeypatch)
    if fault in ('source_sha','source_tree'):kwargs[fault]='0'*40
    elif fault=='raw_tree':pins['raw_tree']='0'*40
    elif fault=='legacy_blob':pins['legacy_blobs']['legacy.py']='0'*40
    else:pins['legacy_sha256']['legacy.py']='0'*64
    with pytest.raises(ValueError,match='HISTORY_BOOTSTRAP_RED'):
        development.prepare_history(repo,output,**kwargs)
    result=json.loads((output/'history-result.json').read_bytes())
    assert result['status']=='RED' and result['actual_owned_fin_closed'] is True
    assert result['kernel']['returncode']!=0 and result['cleanup']['namespace_removed'] is True
    assert (output/'history-owned/manifest.json').is_file()
    assert result['cleanup_credit_claimed'] is False and result['G0_G8_qualification'] is False


@pytest.mark.parametrize('key',['url.https://foreign.invalid/.insteadOf','include.path','http.proxy'])
def test_native_history_denies_remote_rewrite_and_external_config_before_fetch(tmp_path,monkeypatch,key):
    repo,output,kwargs,_=bootstrap_fixture(tmp_path,monkeypatch)
    subprocess.run(['git','-C',str(repo),'config','--local',key,'https://github.com/'],check=True,
        env=development.history_environment(),stderr=subprocess.DEVNULL)
    with pytest.raises(ValueError,match='HISTORY_BOOTSTRAP_RED'):
        development.prepare_history(repo,output,**kwargs)
    result=json.loads((output/'history-result.json').read_bytes())
    assert result['status']=='RED' and result['actual_owned_fin_closed'] is True
    assert result['kernel']['returncode']!=0 and result['cleanup']['namespace_removed'] is True
    manifest=json.loads((output/'history-owned/manifest.json').read_bytes())
    log=next(row for row in manifest['files'] if row['relative_source']=='history-native.log')
    assert b'DEVELOPMENT_HISTORY_FIXED_REMOTE_CONFIG_REQUIRED' in (output/'history-owned'/log['capture_file']).read_bytes()


@pytest.mark.parametrize('fault',['execute_unknown','opaque_unknown'])
def test_unknown_fin_never_authorizes_capture_cleanup_or_credit(tmp_path,monkeypatch,fault):
    repo,sha,tree,pins=mini_source(tmp_path);output=tmp_path/'control';output.mkdir(mode=0o700)
    namespace=SimpleNamespace(path=tmp_path/'unknown-owned');namespace.path.mkdir()
    monkeypatch.setenv('GITHUB_RUN_ID','17')
    monkeypatch.setattr(development,'short_control_parent',lambda repo:tmp_path)
    monkeypatch.setattr(development.lifecycle,'create_namespace',lambda *a:namespace)
    if fault=='execute_unknown':
        monkeypatch.setattr(development.lifecycle,'execute_owned',lambda *a,**k:(_ for _ in ()).throw(ValueError('UNKNOWN_FIN')))
    else:
        monkeypatch.setattr(development.lifecycle,'execute_owned',lambda *a,**k:({'returncode':0},object()))
        monkeypatch.setattr(development.lifecycle,'require_fin',lambda *a:(_ for _ in ()).throw(ValueError('UNKNOWN_FIN')))
    monkeypatch.setattr(development.lifecycle,'capture_required_evidence',lambda *a:pytest.fail('unknown capture'))
    monkeypatch.setattr(development.lifecycle,'cleanup_namespace',lambda *a:pytest.fail('unknown cleanup'))
    with pytest.raises(ValueError,match='HISTORY_BOOTSTRAP_RED'):
        development.prepare_history(repo,output,source_sha=sha,source_tree=tree,owner_session='UNIT_ONLY',
            plan_sha256='a'*64,owner_check=lambda:None)
    result=json.loads((output/'history-result.json').read_bytes())
    assert result['actual_owned_fin_closed'] is False and result['unknown_fin_retained'] is True
    assert namespace.path.exists() and result['cleanup_credit_claimed'] is False


@pytest.mark.parametrize('unshallow',[True,False])
def test_fetch_is_fixed_repository_exact_object_no_refs_tags_or_fetch_head(unshallow):
    command=development.history_fetch_command('a'*40,unshallow=unshallow)
    assert command[-2:]==[development.HISTORY_REPOSITORY,'a'*40]
    assert ('--unshallow' in command)==unshallow
    assert {'--filter=blob:none','--no-tags','--no-write-fetch-head','--no-auto-maintenance'}<=set(command)
    assert '--all' not in command and not any('refs/' in part for part in command)


@pytest.mark.parametrize('sha',['main','HEAD','a'*39,'A'*40,'a'*40+':refs/heads/evil','https://evil.invalid'])
def test_untrusted_fetch_provenance_is_rejected_before_network(sha):
    with pytest.raises(ValueError,match='EXACT_OBJECT_REQUIRED'):development.history_fetch_command(sha)


@pytest.mark.parametrize('fault',['alias','hardlink','file_size','entries','allocated','alternate'])
def test_physical_git_alias_and_bounds_fail_closed(tmp_path,monkeypatch,fault):
    repo=tmp_path/'Source';git=repo/'.git';git.mkdir(parents=True)
    member=git/'object';member.write_bytes(b'bounded')
    if fault=='alias':(git/'alias').symlink_to(member)
    elif fault=='hardlink':os.link(member,git/'link')
    elif fault=='file_size':member.open('wb').truncate(development.HISTORY_FILE_LIMIT+1)
    elif fault=='entries':monkeypatch.setattr(development,'HISTORY_ENTRY_LIMIT',1)
    elif fault=='allocated':monkeypatch.setattr(development,'HISTORY_GIT_LIMIT',1)
    else:
        alternate=git/'objects/info/alternates';alternate.parent.mkdir(parents=True);alternate.write_text('/foreign/objects\n')
    with pytest.raises(ValueError,match='HISTORY_'):development.history_git_inventory(repo)


@pytest.mark.parametrize('fault',['bytes','inodes','zero_inodes','unit','foreign_mount'])
def test_fresh_filesystem_observation_is_required_before_history_child(tmp_path,monkeypatch,fault):
    from scripts import rc6_heavy_test_preflight as preflight
    row={'free_bytes':development.HISTORY_FREE_FLOOR,'total_inodes':1000,'free_inodes':100,
        'allocation_unit_bytes':4096,'filesystem_device':1,'mount_id':7,'filesystem_type':'ext4'}
    if fault=='bytes':row['free_bytes']-=1
    elif fault=='inodes':row['free_inodes']=99
    elif fault=='zero_inodes':row['total_inodes']=0
    elif fault=='unit':row['allocation_unit_bytes']=0
    calls=[]
    def measured(path):
        value=dict(row);calls.append(path)
        if fault=='foreign_mount' and len(calls)==3:value['mount_id']=8
        return value
    monkeypatch.setattr(preflight,'measure_filesystem',measured)
    with pytest.raises(ValueError,match='FRESH_STORAGE_BOUND|SAME_FILESYSTEM'):
        development.history_storage(tmp_path,tmp_path)


def test_history_child_environment_has_no_auth_secrets_tracing_or_lazy_fetch(monkeypatch):
    for key in ('GH_TOKEN','GITHUB_TOKEN','GIT_TRACE','GIT_TRACE_CURL','GIT_CONFIG_PARAMETERS'):
        monkeypatch.setenv(key,'secret-unit-only')
    env=development.history_environment()
    assert 'GH_TOKEN' not in env and 'GITHUB_TOKEN' not in env and 'GIT_CONFIG_PARAMETERS' not in env
    assert env['GIT_TRACE']==env['GIT_TRACE_CURL']=='0' and env['GIT_NO_LAZY_FETCH']=='1'


def test_history_bootstrap_rejects_actual_root_before_namespace_or_git_mutation(tmp_path,monkeypatch):
    monkeypatch.setattr(development.os,'getuid',lambda:0)
    monkeypatch.setattr(development.os,'geteuid',lambda:0)
    with pytest.raises(ValueError,match='ACTUAL_NONROOT_CAPEFF_ZERO'):development.require_history_nonroot()
