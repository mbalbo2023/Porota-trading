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
    foreign = record(471,12,'WRITE_OWNER=foreign\nDEPLOY_OWNER=NOT_ACQUIRED\nRELEASED='+foreign_released+'\n'
        +'SOURCE_LEASE_EXPIRES_UTC=2026-10-09T18:11:00Z\n')
    # The transfer chain has separate end-to-end tests. This transport exercises
    # the genuine complete-timeline parser and writer predicate used by custody.
    monkeypatch.setattr(admission,'administrative_anchors',lambda owner,get:{471:receipt,473:twin})
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
    job=workflow['jobs']['evidence-custody']
    assert 'workflow_dispatch' in job['if'] and "inputs.gate == 'evidence-custody'" in job['if']
    steps=job['steps']; names=[step['name'] for step in steps]
    assert names.index('Admit bounded development checks before tooling') < names.index('Prepare bounded Python311 development tooling')
    install=next(step['run'] for step in steps if step['name'].startswith('Install only the fourteen'))
    assert '--require-hashes' in install and '--only-binary=:all:' in install
    plan=json.loads((root/'ops/policy/rc6-development-checks-v1.json').read_text())
    assert plan['scope']=='DEVELOPMENT_REGRESSIONS_ONLY_NOT_G0_G8_OR_PRODUCT157'
    assert plan['full_gov_claimed'] is plan['material_gate_launched'] is False
    assert not any('test_real_pytest_central_definition' in item for item in plan['suites'])
