import hashlib
import io
import json
import urllib.request
import zipfile

import pytest
from scripts import rc6_actions_custody as custody


def sample():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr('original.xml', b'<testsuite failures="1"/>')
        z.writestr('native.json', b'{"status":"RED"}\n')
    raw = stream.getvalue()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        index = {(7, name): {'bytes': len(z.read(name)), 'sha256': custody.digest(z.read(name))}
                 for name in z.namelist()}
    return raw, {'artifact_id': 7, 'bytes': len(raw), 'sha256': custody.digest(raw), 'member_count': 2}, index


def test_original_red_zip_and_native_xml_are_retained_byte_exact():
    raw, artifact, index = sample()
    assert len(custody.verify_zip(raw, artifact, index)) == 2


@pytest.mark.parametrize('mutation', ['container', 'member', 'missing', 'extra', 'size'])
def test_container_and_complete_original_members_reject_tampering(mutation):
    raw, artifact, index = sample()
    if mutation == 'container': raw = raw[:-1] + bytes([raw[-1] ^ 1])
    if mutation == 'member': index[(7, 'native.json')]['sha256'] = '0' * 64
    if mutation == 'missing': del index[(7, 'native.json')]
    if mutation == 'extra': index[(7, 'foreign')] = {'bytes': 0, 'sha256': custody.digest(b'')}
    if mutation == 'size': index[(7, 'native.json')]['bytes'] += 1
    with pytest.raises(ValueError, match='CUSTODY_'):
        custody.verify_zip(raw, artifact, index)


def test_cross_host_redirect_does_not_forward_github_credential():
    request = urllib.request.Request(custody.API + '/actions/artifacts/7/zip',
                                     headers={'Authorization': 'Bearer synthetic-unit-only'})
    result = custody.SafeRedirect().redirect_request(request, None, 302, 'Found', {},
                                                     'https://example.invalid/owned.zip')
    assert result.get_header('Authorization') is None


def test_cleartext_redirect_is_blocked():
    request = urllib.request.Request(custody.API + '/actions/artifacts/7/zip')
    with pytest.raises(ValueError, match='NON_TLS'):
        custody.SafeRedirect().redirect_request(request, None, 302, 'Found', {}, 'http://example.invalid/raw')


def test_original_git_index_must_match_both_blob_and_sha256():
    import base64
    raw = b'artifact_id,path,uncompressed_bytes,sha256\n'
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    class Reader:
        def request(self, path):
            return {'sha': blob, 'content': base64.b64encode(raw).decode()}
    plan = {'index': {'source_sha': 'a' * 40, 'path': 'index.csv', 'git_blob': blob,
                      'sha256': '0' * 64, 'bytes': len(raw)}}
    with pytest.raises(ValueError, match='INDEX_REBOUND'):
        custody.load_original_index(Reader(), plan)


def test_duplicate_original_members_are_rejected_even_when_digests_match():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr('same', b'x')
        with pytest.warns(UserWarning): z.writestr('same', b'x')
    raw = stream.getvalue()
    artifact = {'artifact_id': 7, 'bytes': len(raw), 'sha256': custody.digest(raw), 'member_count': 2}
    with pytest.raises(ValueError, match='COMPLETE_MEMBER_SET'):
        custody.verify_zip(raw, artifact, {(7, 'same'): {'bytes': 1, 'sha256': custody.digest(b'x')}})


class CustodyTransport:
    """An in-memory transport with actual ZIP/CSV/asset bytes; no RC6 proof."""
    def __init__(self, *, corrupt_recovery=False):
        import base64
        import csv
        import io
        self.raw, artifact, index = sample()
        self.artifact = {**artifact, 'run_id': 13, 'source_sha': 'a'*40}
        table = io.StringIO()
        writer = csv.writer(table)
        writer.writerow(('artifact_id','path','uncompressed_bytes','sha256'))
        for (ident, name), entry in index.items():
            writer.writerow((ident, name, entry['bytes'], entry['sha256']))
        index_raw = table.getvalue().encode()
        blob = hashlib.sha1(b'blob '+str(len(index_raw)).encode()+b'\0'+index_raw).hexdigest()
        self.index = {'sha':blob,'content':base64.b64encode(index_raw).decode()}
        self.plan = {'schema':'porota.rc6.actions-custody-plan.v1','repository':custody.REPO,
            'draft_release_tag':'UNIT_RAW_ONLY','target_commitish':'a'*40,
            'index':{'source_sha':'b'*40,'path':'index.csv','git_blob':blob,
                     'sha256':custody.digest(index_raw),'bytes':len(index_raw)},
            'artifacts':[self.artifact]}
        self.release = None
        self.assets = {}
        self.posts = []
        self.corrupt_recovery = corrupt_recovery

    def request(self,path,*,method='GET',body=None,maximum=None,binary=False):
        import urllib.parse
        if method == 'POST': self.posts.append(path)
        if path.startswith('/contents/'):
            return self.index
        if path == '/releases?per_page=100':
            return [self.release] if self.release else []
        if path == '/releases' and method == 'POST':
            self.release = {**json.loads(body),'id':99,'html_url':'https://example.invalid/private-raw'}
            return self.release
        if path == '/actions/artifacts/7':
            return {'digest':'sha256:'+self.artifact['sha256'],'size_in_bytes':len(self.raw),
                    'expired':False,'workflow_run':{'id':13,'head_sha':'a'*40}}
        if path == '/actions/artifacts/7/zip': return self.raw
        if path == '/releases/99/assets?per_page=100':
            return [value[0] for value in self.assets.values()]
        if path.startswith('https://uploads.github.com/'):
            ident = len(self.assets)+1
            name = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)['name'][0]
            meta = {'id':ident,'size':len(body),'name':name}
            self.assets[ident] = (meta, body)
            return custody.canonical(meta) if binary else meta
        if path.startswith('/releases/assets/'):
            ident = int(path.rsplit('/',1)[1])
            raw = self.assets[ident][1]
            if self.corrupt_recovery and self.assets[ident][0]['name'].endswith('.zip'):
                return raw[:-1]+bytes([raw[-1]^1])
            return raw
        raise AssertionError(('unexpected transport path',path,method))


def test_custody_writes_exact_zip_and_durable_receipt_then_reads_both_back():
    transport = CustodyTransport()
    owner_reads = []
    result = custody.archive_plan(transport,transport.plan,owner_check=lambda:owner_reads.append(True))
    assert len(owner_reads) >= 4
    assert result['G0_G8_qualification'] is result['artifact_validated'] is result['deployed'] is False
    assert result['artifacts'][0]['recoverable_sha256'] == custody.digest(transport.raw)
    assert result['artifacts'][0]['recovery_read_verified'] is True
    receipt_id = result['durable_receipt']['asset_id']
    receipt_raw = transport.assets[receipt_id][1]
    assert custody.digest(receipt_raw) == result['durable_receipt']['sha256']
    assert json.loads(receipt_raw)['artifacts'][0]['members'][0]['sha256']
    assert transport.assets[1][1] == transport.raw
    assert transport.release['draft'] is True


def test_resuming_partial_custody_verifies_existing_assets_without_overwrite():
    transport = CustodyTransport()
    first = custody.archive_plan(transport,transport.plan)
    posts = list(transport.posts)
    second = custody.archive_plan(transport,transport.plan)
    assert first == second and transport.posts == posts


def test_corrupt_recovered_zip_never_creates_a_success_receipt_or_deletes_original():
    transport = CustodyTransport(corrupt_recovery=True)
    with pytest.raises(ValueError,match='CUSTODY_CONTAINER_BYTES_OR_DIGEST_MISMATCH'):
        custody.archive_plan(transport,transport.plan)
    assert transport.assets[1][1] == transport.raw and len(transport.assets) == 1
    assert transport.release['draft'] is True


def test_lost_owner_stops_before_creating_release_or_asset():
    transport = CustodyTransport()
    def lost(): raise ValueError('OWNER_SUPERSEDED')
    with pytest.raises(ValueError,match='OWNER_SUPERSEDED'):
        custody.archive_plan(transport,transport.plan,owner_check=lost)
    assert not transport.posts and transport.release is None


@pytest.mark.parametrize('foreign_released', ['false','true'])
def test_live_custody_receipt_cannot_override_an_unreleased_expired_foreign_writer(monkeypatch,foreign_released):
    from datetime import datetime, timezone
    from scripts import rc6_material_pr_admission as admission
    owner = admission.CLOSURE_OWNER
    def record(issue,ident,body):
        return {'id':ident,'html_url':f'https://github.com/{custody.REPO}/issues/{issue}#issuecomment-{ident}',
            'issue_url':custody.API+f'/issues/{issue}','user':{'login':'mbalbo2023'},'body':body,
            'created_at':'2026-10-09T18:10:00Z','updated_at':'2026-10-09T18:10:00Z'}
    common = f'WRITE_OWNER={owner}\nINTEGRATION_OWNER={owner}\nSESSION_SUCCESSOR={owner}\n'
    common += 'DEPLOY_OWNER=NOT_ACQUIRED\nRELEASED=false\nSOURCE_SHA='+('a'*40)+'\nSOURCE_TREE='+('b'*40)+'\n'
    common += 'SOURCE_LEASE_EXPIRES_UTC=2026-10-09T18:30:00Z\nMODE=PRODUCTION_PAPER / SIMULATION\nreal_orders_sent=0\n'
    twin = record(473,11,common)
    receipt = record(471,10,common+'RC6_EVIDENCE_CUSTODY_AUTHORIZATION=APPROVED\nOWNER_RECEIPT_473=11\n'
        +'OWNER_RECEIPT_473_SHA256='+custody.digest(twin['body'].encode())+'\n')
    foreign = record(471,12,'WRITE_OWNER=foreign\nSESSION=foreign\nDEPLOY_OWNER=NOT_ACQUIRED\nRELEASED='+foreign_released+'\n'
        +'SOURCE_LEASE_EXPIRES_UTC=2026-10-09T18:11:00Z\n')
    # The transfer chain has separate end-to-end tests. This transport exercises
    # the genuine complete-timeline parser and writer predicate used by custody.
    monkeypatch.setattr(admission,'administrative_anchors',lambda owner,get,now:{471:receipt,473:twin})
    class Reader:
        def request(self,path):
            if path == '/issues/comments/10': return receipt
            if path == '/issues/comments/11': return twin
            if path.startswith('/issues/471/comments?'): return [foreign,receipt]
            if path.startswith('/issues/473/comments?'): return [twin]
            raise AssertionError(path)
    if foreign_released == 'false':
        with pytest.raises(ValueError,match='FOREIGN_ACTIVE_OR_UNKNOWN_WRITER'):
            custody.verify_owner(Reader(),receipt['html_url'],sha='a'*40,tree='b'*40,owner=owner,
                now=datetime(2026,10,9,18,15,tzinfo=timezone.utc))
    else:
        # Explicit release still needs a fresh current-owner receipt for this
        # attempt. The released foreign record cannot be its latest owner.
        with pytest.raises(ValueError,match='LATEST_WRITER_OR_DEPLOY_OWNER_CONFLICT'):
            custody.verify_owner(Reader(),receipt['html_url'],sha='a'*40,tree='b'*40,owner=owner,
                now=datetime(2026,10,9,18,15,tzinfo=timezone.utc))


def test_development_tooling_uses_original_hashed_lock_and_exactly_fourteen_wheels():
    import re
    from pathlib import Path
    from scripts.rc6_development_checks import tooling_lock, DEPENDENCIES
    root=Path(__file__).absolute().parents[1]
    raw=(root/'requirements.lock.txt').read_text()
    selected=tooling_lock(raw,sorted(DEPENDENCIES))
    assert len(re.findall(r'(?m)^[A-Za-z0-9_.-]+==',selected))==14
    assert 'pytest==9.1.1' in selected and 'ppi-client==' not in selected
    for block in re.split(r'(?m)(?=^[A-Za-z0-9_.-]+==)',selected):
        if block:assert block in raw
    with pytest.raises(ValueError,match='HASHED_CANONICAL_LOCK'):
        tooling_lock(raw.replace('pytest==9.1.1','pytest==9.1.1 --index-url https://example.invalid'),sorted(DEPENDENCIES))
    with pytest.raises(ValueError,match='DEPENDENCY_MISSING'):
        tooling_lock(raw.replace('pytest==9.1.1','other==9.1.1'),sorted(DEPENDENCIES))
    with pytest.raises(ValueError,match='HASHED_CANONICAL_LOCK'):
        tooling_lock(raw+selected,sorted(DEPENDENCIES))


def test_development_control_parent_observes_short_same_device_and_mount():
    from pathlib import Path
    import os
    from scripts.rc6_development_checks import short_control_parent
    from scripts import porota_predeploy_cleanup as original
    repo=Path(__file__).absolute().parents[1]
    parent=short_control_parent(repo)
    with original.directory(repo) as source,original.directory(parent) as selected:
        assert os.fstat(source).st_dev==os.fstat(selected).st_dev
        assert os.fstat(selected).st_uid==os.geteuid()
        assert original.mount_id(source)==original.mount_id(selected)
    assert len(os.fsencode(parent))<=12


def test_development_control_parent_refuses_foreign_tmp_even_on_source_mount(monkeypatch):
    from contextlib import contextmanager
    from pathlib import Path
    from types import SimpleNamespace
    from scripts import rc6_development_checks as development
    from scripts import porota_predeploy_cleanup as custody_original
    repo=Path('/workspace/source')
    descriptors={repo:1,Path('/tmp'):2,Path('/home/runner'):3,repo.parent:4}
    @contextmanager
    def measured_directory(path):
        yield descriptors[path]
    with monkeypatch.context() as patch:
        patch.setattr(custody_original,'directory',measured_directory)
        patch.setattr(custody_original,'mount_id',lambda fd:17)
        patch.setattr(Path,'home',classmethod(lambda cls:Path('/home/runner')))
        patch.setattr(development.os,'access',lambda *args:True)
        patch.setattr(development.os,'geteuid',lambda:1001)
        patch.setattr(development.os,'fstat',lambda fd:SimpleNamespace(st_dev=29,st_uid=0 if fd==2 else 1001))
        assert development.short_control_parent(repo)==Path('/home/runner')


@pytest.mark.parametrize('rebound', [None, 'sha', 'tree'])
def test_development_admission_runs_real_git_identity_before_any_tooling(tmp_path, monkeypatch, rebound):
    from scripts import rc6_development_checks as development
    root=development.ROOT
    sha=development.governed.git(root,'rev-parse','HEAD').decode().strip()
    tree=development.governed.git(root,'rev-parse','HEAD^{tree}').decode().strip()
    expected={'sha':sha,'tree':tree}
    if rebound:expected[rebound]='0'*40
    for key,value in {'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':custody.REPO,
                      'GITHUB_ACTOR':'mbalbo2023','GITHUB_EVENT_NAME':'workflow_dispatch',
                      'GITHUB_RUN_ATTEMPT':'1','GH_TOKEN':'synthetic-unit-only'}.items():
        monkeypatch.setenv(key,value)
    owner_checks=[]
    def authorized_transport_only(*args,**kwargs):
        owner_checks.append(kwargs)
        return 'a'*64
    monkeypatch.setattr(development,'verify_owner',authorized_transport_only)
    monkeypatch.setattr(development.admission,'dedup_admission',lambda sha,gate:{'unit_transport_only':True})
    output=tmp_path/'admitted'
    monkeypatch.setattr(development.sys,'argv',['development','--plan',
        str(root/'ops/policy/rc6-development-checks-v1.json'),'--source-sha',expected['sha'],
        '--source-tree',expected['tree'],'--owner-session','UNIT_ONLY',
        '--launch-receipt-url','https://example.invalid/unit-only','--output',str(output),'--admit-only'])
    if rebound:
        with pytest.raises(ValueError,match='DEVELOPMENT_EXACT_FROZEN_SOURCE_REQUIRED'):
            development.main()
        assert not output.exists() and owner_checks==[]
    else:
        assert development.main()==0
        receipt=json.loads((output/'admission.json').read_bytes())
        assert receipt['source_sha']==sha and receipt['source_tree']==tree
        assert receipt['qualification_claimed'] is False
        assert len(owner_checks)==2
        assert (output/'tooling.lock.txt').read_text()==development.tooling_lock(
            (root/'requirements.lock.txt').read_text(),sorted(development.DEPENDENCIES))


@pytest.mark.parametrize('mask',[0o022,0o077])
def test_development_mask_admission_observes_actual_native_child_kernel(mask):
    import subprocess
    import sys
    from pathlib import Path
    root=Path(__file__).absolute().parents[1]
    code="""
import os,sys
sys.path.insert(0,sys.argv[1])
from scripts.rc6_development_checks import require_canonical_umask
mask=int(sys.argv[2]);os.umask(mask)
try:
    value=require_canonical_umask()
except ValueError as error:
    assert mask==0o077 and str(error)=='DEVELOPMENT_CANONICAL_UMASK_0022_REQUIRED'
else:
    assert mask==0o022 and value=='0022'
assert os.umask(mask)==mask
"""
    result=subprocess.run([sys.executable,'-I','-B','-c',code,str(root),str(mask)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=10)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('change',['duplicate','counter','failure','empty'])
def test_development_junit_facts_never_accept_changed_native_identity_or_headers(change):
    from scripts.rc6_development_checks import junit_facts
    case='<testcase classname="tests.original" name="test_literal"/>'
    tests=1
    if change=='duplicate':case+=case;tests=2
    if change=='counter':tests=2
    if change=='failure':case=case.replace('/>','><failure/></testcase>')
    if change=='empty':case='';tests=0
    raw=f'<testsuites><testsuite tests="{tests}" failures="0" errors="0" skipped="0">{case}</testsuite></testsuites>'.encode()
    with pytest.raises(ValueError,match='DEVELOPMENT_JUNIT_'):junit_facts(raw)


def test_development_ci_stays_manual_owner_admitted_and_distinct_from_product_gates():
    from pathlib import Path
    import yaml
    root=Path(__file__).absolute().parents[1]
    workflow=yaml.load((root/'.github/workflows/rc6-unified-candidate-tests.yml').read_text(),Loader=yaml.BaseLoader)
    job=workflow['jobs']['development-checks']
    assert 'workflow_dispatch' in job['if'] and "inputs.gate == 'development-checks'" in job['if']
    assert job['permissions']['contents']=='read'
    assert "inputs.gate != 'development-checks'" in workflow['jobs']['ordered-source-gate']['if']
    assert not any(step.get('id')=='development_admission' for step in workflow['jobs']['evidence-custody']['steps'])
    steps=job['steps']; names=[step['name'] for step in steps]
    assert names.index('Admit bounded development checks before tooling') < names.index('Prepare bounded Python311 development tooling')
    assert next(step for step in steps if step.get('id')=='development_admission')['if']=='always()'
    for step in steps:
        if step.get('id') in ('dev311','dev312') or step['name'].startswith('Install only the fourteen'):
            assert "steps.development_admission.outcome == 'success'" in step['if']
    install=next(step['run'] for step in steps if step['name'].startswith('Install only the fourteen'))
    assert '--require-hashes' in install and '--only-binary=:all:' in install
    plan=json.loads((root/'ops/policy/rc6-development-checks-v1.json').read_text())
    assert plan['scope']=='DEVELOPMENT_REGRESSIONS_ONLY_NOT_G0_G8_OR_PRODUCT157'
    assert plan['full_gov_claimed'] is plan['material_gate_launched'] is False
    assert not any('test_real_pytest_central_definition' in item for item in plan['suites'])


def test_development_retirement_error_preserves_native_red_controls_and_blocks_next_epoch(tmp_path,monkeypatch):
    """Real child/FIN/capture; injected EACCES is no native capacity proof."""
    import errno
    import subprocess
    import sys
    from pathlib import Path
    from scripts import rc6_development_checks as development
    product=Path(__file__).absolute().parents[1]
    repo=tmp_path/'original';repo.mkdir()
    (repo/'tests').mkdir()
    (repo/'tests/test_exact_native.py').write_text('def test_original_native_case():\n    assert True\n')
    (repo/'requirements.lock.txt').write_bytes((product/'requirements.lock.txt').read_bytes())
    plan=json.loads((product/'ops/policy/rc6-development-checks-v1.json').read_bytes())
    plan['suites']=['tests/test_exact_native.py']
    plan_path=repo/'plan.json';plan_path.write_bytes(custody.canonical(plan))
    def git(*args):
        return subprocess.check_output(['git','-C',str(repo),*args],text=True).strip()
    git('init','-q');git('config','user.email','native-unit@example.invalid')
    git('config','user.name','Native unit fixture');git('add','.')
    git('commit','-qm','Tiny original native regression')
    sha=git('rev-parse','HEAD');tree=git('rev-parse','HEAD^{tree}')
    output=tmp_path/'controls';output.mkdir(mode=0o700)
    for key,value in {'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':custody.REPO,
                      'GITHUB_ACTOR':'mbalbo2023','GITHUB_EVENT_NAME':'workflow_dispatch',
                      'GITHUB_RUN_ATTEMPT':'1','GITHUB_RUN_ID':'UNIT_NOT_ACTIONS'}.items():
        monkeypatch.setenv(key,value)
    monkeypatch.setattr(development,'ROOT',repo)
    monkeypatch.setattr(development,'Github',lambda token:object())
    monkeypatch.setattr(development,'verify_owner',lambda *args,**kwargs:'a'*64)
    monkeypatch.setattr(development.admission,'dedup_admission',lambda sha,gate:{'unit_transport_only':True})
    monkeypatch.setattr(sys,'argv',['development','--plan',str(plan_path),'--source-sha',sha,
        '--source-tree',tree,'--owner-session','UNIT_ONLY','--launch-receipt-url','https://example.invalid/unit',
        '--python311',sys.executable,'--python312',sys.executable,'--output',str(output)])
    original_cleanup=development.lifecycle.cleanup_namespace
    held=[]
    def retirement_denied(*args,**kwargs):
        held.append((args,kwargs))
        raise PermissionError(errno.EACCES,'Injected own retirement error')
    monkeypatch.setattr(development.lifecycle,'cleanup_namespace',retirement_denied)
    try:
        assert development.main()==1
        result=json.loads((output/'result.json').read_bytes())
        assert result['status']=='RED_CONTROL_RETIREMENT' and len(result['epochs'])==1
        assert result['next_epoch_launched'] is result['cleanup_credit_claimed'] is False
        assert result['fullSource_unchanged'] is None and (output/'source-before.index.json').is_file()
        epoch=result['epochs'][0]
        assert epoch['kernel']['returncode']==0 and epoch['junit']['cases']==1
        assert epoch['retirement_error']=={'type':'PermissionError','errno':errno.EACCES}
        assert epoch['passed'] is False and (output/'311/manifest.json').is_file()
        assert not (output/'312').exists()
    finally:
        for args,kwargs in held:original_cleanup(*args,**kwargs)


@pytest.mark.parametrize('route', ['actions_zip', 'asset_get', 'asset_upload'])
def test_binary_decoding_keeps_endpoint_specific_github_rest_negotiation(route):
    raw,artifact,index=sample()
    calls=[]
    class Boundary:
        def open(self,request,timeout):
            calls.append(request)
            return io.BytesIO(raw if route!='asset_upload' else b'{"id":7}')
    github=custody.Github('synthetic-unit-only');github.opener=Boundary()
    if route=='actions_zip':
        result=github.request('/actions/artifacts/7/zip',binary=True)
        assert result==raw and custody.verify_zip(result,artifact,index)
    elif route=='asset_get':
        assert github.request('/releases/assets/7',binary=True)==raw
    else:
        result=github.request('https://uploads.github.com/repos/'+custody.REPO+'/releases/7/assets?name=original.zip',
            method='POST',body=raw,binary=True)
        assert json.loads(result)=={'id':7}
        assert calls[0].get_header('Content-type')=='application/zip'
    assert calls[0].get_header('Accept')==('application/octet-stream' if route=='asset_get'
        else 'application/vnd.github+json')


def test_custody_cli_preserves_native_http_red_without_claiming_recovery(tmp_path,monkeypatch):
    import sys
    from pathlib import Path
    root=Path(__file__).absolute().parents[1]
    for key,value in {'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':custody.REPO,
        'GITHUB_ACTOR':'mbalbo2023','GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_RUN_ATTEMPT':'1'}.items():
        monkeypatch.setenv(key,value)
    monkeypatch.setattr(custody,'Github',lambda token:object())
    monkeypatch.setattr(custody,'verify_owner',lambda *args,**kwargs:'a'*64)
    def media_type_failure(*args,**kwargs):raise ValueError('CUSTODY_HTTP_415')
    monkeypatch.setattr(custody,'archive_plan',media_type_failure)
    output=tmp_path/'original-red.json'
    monkeypatch.setattr(sys,'argv',['custody','--plan',str(root/'ops/policy/rc6-actions-custody-20261009.json'),
        '--source-sha','a'*40,'--source-tree','b'*40,'--owner-session','UNIT_ONLY',
        '--launch-receipt-url','https://example.invalid/unit','--output',str(output)])
    with pytest.raises(ValueError,match='CUSTODY_HTTP_415'):custody.main()
    original=json.loads(output.read_bytes())
    assert original['schema']=='porota.rc6.actions-custody-attempt.v1' and original['status']=='RED'
    assert original['reason']=='CUSTODY_HTTP_415' and original['complete_custody_verified'] is False
    assert original['G0_G8_qualification'] is original['artifact_validated'] is original['deployed'] is False
