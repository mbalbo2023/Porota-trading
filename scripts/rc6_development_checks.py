"""Bounded development regressions; never a Product157 or G0–G8 receipt."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from scripts import rc6_authenticated_fixture_lifecycle as lifecycle
from scripts import rc6_controlled_governed_runner as governed
from scripts import rc6_material_pr_admission as admission
from scripts.rc6_actions_custody import Github, verify_owner, canonical, digest

DEPENDENCIES = frozenset(('certifi','charset-normalizer','hypothesis','idna','iniconfig','packaging',
                         'pluggy','pygments','pytest','python-dotenv','pyyaml','requests','sortedcontainers','urllib3'))
HISTORY_REPOSITORY='https://github.com/mbalbo2023/Porota-trading.git'
HISTORY_FILE_LIMIT=256*1024**2
HISTORY_GIT_LIMIT=512*1024**2
HISTORY_ENTRY_LIMIT=100000
HISTORY_SECONDS=60
HISTORY_FREE_FLOOR=4*1024**3+3*HISTORY_FILE_LIMIT


def require_history_nonroot():
    capabilities=re.findall(r'^CapEff:\s*([0-9a-fA-F]+)$',Path('/proc/self/status').read_text(),re.MULTILINE)
    admission.require(os.getuid()==os.geteuid()>0 and len(capabilities)==1 and int(capabilities[0],16)==0,
        'DEVELOPMENT_HISTORY_ACTUAL_NONROOT_CAPEFF_ZERO_REQUIRED')


def history_requirements():
    from scripts import porota_artifact_provenance as provenance
    from scripts import rc6_archive_v3_image_smoke as smoke
    return {'raw_anchor':provenance.RAW_CUSTODY_SOURCE_SHA,'raw_tree':provenance.RAW_CUSTODY_TREE_SHA,
        'raw_roots':list(provenance.RAW_EVIDENCE_ROOTS),'legacy_anchor':smoke.LEGACY_REACHABLE_ANCHOR,
        'legacy_blobs':dict(smoke.LEGACY_GIT_BLOBS),'legacy_sha256':dict(smoke.BASELINE['source_sha256'])}


def history_git_inventory(repo):
    """Observe allocated native Git blocks; aliases never extend this scope."""
    root=Path(repo)/'.git';entries=0;allocated=0
    admission.require(root.is_dir() and not root.is_symlink(),'DEVELOPMENT_HISTORY_NATIVE_GIT_REQUIRED')
    def visit(path):
        nonlocal entries,allocated
        descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            info=os.fstat(descriptor)
            admission.require(info.st_uid==os.geteuid(),'DEVELOPMENT_HISTORY_GIT_OWNER_REQUIRED')
            allocated+=info.st_blocks*512;entries+=1
            admission.require(allocated<=HISTORY_GIT_LIMIT and entries<=HISTORY_ENTRY_LIMIT,
                'DEVELOPMENT_HISTORY_AGGREGATE_GIT_BOUND')
            for name in os.listdir(descriptor):
                row=os.stat(name,dir_fd=descriptor,follow_symlinks=False)
                admission.require(row.st_uid==os.geteuid() and not stat.S_ISLNK(row.st_mode),
                    'DEVELOPMENT_HISTORY_GIT_ALIAS_OR_OWNER_REBOUND')
                if stat.S_ISDIR(row.st_mode):visit(path/name)
                else:
                    admission.require(stat.S_ISREG(row.st_mode) and row.st_nlink==1,
                        'DEVELOPMENT_HISTORY_GIT_REGULAR_EXCLUSIVE_REQUIRED')
                    admission.require(not (path/name).relative_to(root).as_posix() in
                        ('objects/info/alternates','objects/info/http-alternates','info/grafts') or row.st_size==0,
                        'DEVELOPMENT_HISTORY_EXTERNAL_OBJECT_AUTHORITY_FORBIDDEN')
                    allocated+=row.st_blocks*512;entries+=1
                    admission.require(row.st_size<=HISTORY_FILE_LIMIT and allocated<=HISTORY_GIT_LIMIT
                        and entries<=HISTORY_ENTRY_LIMIT,'DEVELOPMENT_HISTORY_AGGREGATE_GIT_BOUND')
        finally:os.close(descriptor)
    visit(root)
    return {'allocated_bytes':allocated,'entries':entries,'maximum_allocated_bytes':HISTORY_GIT_LIMIT,
        'maximum_entries':HISTORY_ENTRY_LIMIT,'maximum_file_bytes':HISTORY_FILE_LIMIT,
        'aggregate_bound_kind':'OBSERVED_SAMPLES_ONLY_NOT_KERNEL_QUOTA','continuous_peak_proved':False}


def history_storage(repo,output):
    from scripts import rc6_heavy_test_preflight as preflight
    rows=[preflight.measure_filesystem(path) for path in (Path(repo),Path(repo)/'.git',Path(output))]
    for row in rows:
        admission.require(all(type(row.get(key)) is int for key in
            ('free_bytes','free_inodes','total_inodes','allocation_unit_bytes','filesystem_device','mount_id'))
            and row['allocation_unit_bytes']>0 and bool(row.get('filesystem_type'))
            and row['free_bytes']>=HISTORY_FREE_FLOOR and row['total_inodes']>0
            and row['total_inodes']<=10*row['free_inodes']<=10*row['total_inodes'],
            'DEVELOPMENT_HISTORY_FRESH_STORAGE_BOUND')
    admission.require(len({(row['filesystem_device'],row['mount_id']) for row in rows})==1,
        'DEVELOPMENT_HISTORY_SAME_FILESYSTEM_REQUIRED')
    return {'observations':rows,'required_free_bytes':HISTORY_FREE_FLOOR,'minimum_free_inode_ratio':0.1,
        'nominal_runner_capacity_not_used':True,'quota_enforcement_claimed':False}


def history_environment():
    return {**{key:os.environ[key] for key in ('PATH','LANG','LC_ALL') if key in os.environ},
        'PYTHONDONTWRITEBYTECODE':'1','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null',
        'GIT_NO_LAZY_FETCH':'1','GIT_NO_REPLACE_OBJECTS':'1','GIT_OPTIONAL_LOCKS':'0',
        'GIT_TERMINAL_PROMPT':'0','GIT_TRACE':'0','GIT_TRACE_CURL':'0'}


def history_git(repo,args,deadline,*,fetch=False,missing_ok=False,maximum=32*1024**2):
    admission.require(time.monotonic()<deadline,'DEVELOPMENT_HISTORY_DEADLINE')
    prefix=['git','--no-replace-objects','-C',str(repo),'-c','core.hooksPath=/dev/null',
        '-c','credential.helper=','-c','protocol.allow=never']
    if fetch:prefix+=['-c','protocol.https.allow=always','-c','fetch.unpackLimit=1',
        '-c','http.followRedirects=false']
    result=subprocess.run(prefix+list(args),env=history_environment(),stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,timeout=max(0.01,deadline-time.monotonic()))
    admission.require(len(result.stdout)<=maximum,'DEVELOPMENT_HISTORY_GIT_OUTPUT_BOUND')
    admission.require(missing_ok or result.returncode==0,'DEVELOPMENT_HISTORY_GIT_COMMAND_FAILED')
    return result


def history_fetch_command(sha,*,unshallow=False):
    admission.require(re.fullmatch('[0-9a-f]{40}',sha or ''),'DEVELOPMENT_HISTORY_EXACT_OBJECT_REQUIRED')
    return ['fetch',*(['--unshallow'] if unshallow else []),'--filter=blob:none','--no-tags',
        '--no-write-fetch-head','--no-auto-maintenance','--recurse-submodules=no',HISTORY_REPOSITORY,sha]


def history_worker(payload):
    """Only the admitted parent starts this bounded, owned NONROOT bootstrap."""
    import resource
    require_history_nonroot()
    resource.setrlimit(resource.RLIMIT_FSIZE,(HISTORY_FILE_LIMIT,HISTORY_FILE_LIMIT))
    admission.require(resource.getrlimit(resource.RLIMIT_FSIZE)==(HISTORY_FILE_LIMIT,HISTORY_FILE_LIMIT),
        'DEVELOPMENT_HISTORY_KERNEL_FILE_LIMIT_REBOUND')
    repo=Path(payload['repo']);pins=payload['requirements'];deadline=time.monotonic()+HISTORY_SECONDS
    def git(*args,**kwargs):return history_git(repo,args,deadline,**kwargs)
    forbidden=git('config','--local','--name-only','--get-regexp',
        r'^(include\..*|includeif\..*|url\..*\.(insteadof|pushinsteadof)|http(\..*)?\.proxy|remote\..*\.proxy)$',
        missing_ok=True)
    admission.require(forbidden.returncode==1 and not forbidden.stdout,
        'DEVELOPMENT_HISTORY_FIXED_REMOTE_CONFIG_REQUIRED')
    def exact_source():
        admission.require(git('rev-parse','HEAD').stdout.decode().strip()==payload['source_sha']
            and git('rev-parse','HEAD^{tree}').stdout.decode().strip()==payload['source_tree'],
            'DEVELOPMENT_HISTORY_SOURCE_REBOUND')
    exact_source();history_git_inventory(repo)
    shallow=git('rev-parse','--is-shallow-repository').stdout.strip()
    admission.require(shallow in (b'true',b'false'),'DEVELOPMENT_HISTORY_SHALLOW_STATUS_UNKNOWN')
    fetched=shallow==b'true'
    if fetched:history_git(repo,history_fetch_command(payload['source_sha'],unshallow=True),deadline,fetch=True)
    admission.require(git('rev-parse','--is-shallow-repository').stdout.strip()==b'false',
        'DEVELOPMENT_HISTORY_COMPLETE_GRAPH_REQUIRED')
    for anchor in (pins['raw_anchor'],pins['legacy_anchor']):git('merge-base','--is-ancestor',anchor,payload['source_sha'])
    commits=int(git('rev-list','--count',payload['source_sha'],maximum=1024).stdout)
    admission.require(0<commits<=HISTORY_ENTRY_LIMIT,'DEVELOPMENT_HISTORY_COMMIT_GRAPH_BOUND')
    commit=git('cat-file','commit',pins['raw_anchor']).stdout
    admission.require(hashlib.sha1(b'commit '+str(len(commit)).encode()+b'\0'+commit).hexdigest()==pins['raw_anchor'],
        'DEVELOPMENT_HISTORY_RAW_COMMIT_REBOUND')
    admission.require(git('rev-parse',pins['raw_anchor']+'^{tree}').stdout.decode().strip()==pins['raw_tree'],
        'DEVELOPMENT_HISTORY_RAW_TREE_REBOUND')
    def listing(anchor,paths):
        rows={}
        for line in git('ls-tree','-rz','--full-tree',anchor,'--',*paths).stdout.split(b'\0'):
            if not line:continue
            header,name=line.split(b'\t',1);mode,kind,oid=header.decode().split();name=os.fsdecode(name)
            admission.require(kind=='blob' and mode in ('100644','100755') and name not in rows,
                'DEVELOPMENT_HISTORY_LITERAL_RAW_INVENTORY_REQUIRED')
            rows[name]=(mode,oid)
        return rows
    raw=listing(pins['raw_anchor'],pins['raw_roots'])
    admission.require(raw and raw==listing(payload['source_sha'],pins['raw_roots']),
        'DEVELOPMENT_HISTORY_RAW_SOURCE_INVENTORY_REBOUND')
    ancestors={parent.as_posix() for name in raw for parent in Path(name).parents if parent.as_posix()!='.'}
    trees={'':pins['raw_tree']}
    for line in git('ls-tree','-rtz','--full-tree',pins['raw_anchor']).stdout.split(b'\0'):
        if not line:continue
        header,name=line.split(b'\t',1);_mode,kind,oid=header.decode().split();name=os.fsdecode(name)
        if kind=='tree' and name in ancestors:trees[name]=oid
    admission.require(set(trees)==ancestors|{''},'DEVELOPMENT_HISTORY_RAW_TREE_CLOSURE_MISSING')
    for oid in trees.values():
        value=git('cat-file','tree',oid).stdout
        admission.require(hashlib.sha1(b'tree '+str(len(value)).encode()+b'\0'+value).hexdigest()==oid,
            'DEVELOPMENT_HISTORY_RAW_TREE_BYTES_REBOUND')
    legacy=listing(pins['legacy_anchor'],list(pins['legacy_blobs']))
    admission.require(legacy=={name:('100644',oid) for name,oid in pins['legacy_blobs'].items()},
        'DEVELOPMENT_HISTORY_LEGACY_BLOB_PROVENANCE_REBOUND')
    objects=set(oid for _,oid in raw.values())|set(pins['legacy_blobs'].values());hydrated=[];blobs={}
    for oid in sorted(objects):
        if git('cat-file','-e',oid,missing_ok=True).returncode:
            history_git(repo,history_fetch_command(oid),deadline,fetch=True);hydrated.append(oid)
        size=int(git('cat-file','-s',oid,maximum=1024).stdout)
        admission.require(0<=size<=32*1024**2,'DEVELOPMENT_HISTORY_BLOB_SIZE_BOUND')
        value=git('cat-file','blob',oid).stdout
        admission.require(len(value)==size and hashlib.sha1(b'blob '+str(len(value)).encode()+b'\0'+value).hexdigest()==oid,
            'DEVELOPMENT_HISTORY_ORIGINAL_BLOB_REBOUND')
        blobs[oid]={'bytes':len(value),'sha256':digest(value)}
        history_git_inventory(repo)
    for name,oid in pins['legacy_blobs'].items():
        admission.require(blobs[oid]['sha256']==pins['legacy_sha256'][name],
            'DEVELOPMENT_HISTORY_LEGACY_BYTES_REBOUND')
    exact_source()
    result={'status':'PASS_GIT_HISTORY_ONLY','source_sha':payload['source_sha'],'source_tree':payload['source_tree'],
        'unshallow_fetched':fetched,'hydrated_original_blob_oids':hydrated,'raw_file_count':len(raw),
        'verified_original_blobs':blobs,'git':history_git_inventory(repo),'requirements':pins,
        'reachable_commit_count':commits,'verified_raw_trees':len(trees),'kernel_file_limit_bytes':HISTORY_FILE_LIMIT,
        'lazy_fetch_during_tests_allowed':False,'G0_G8_qualification':False,'real_orders_sent':0}
    with Path(payload['result']).open('xb') as stream:stream.write(canonical(result))


def prepare_history(repo,output,*,source_sha,source_tree,owner_session,plan_sha256,owner_check):
    """Bootstrap Git before Source pin; unknown FIN retains its namespace."""
    result={'schema':'porota.rc6.development-history.v1','status':'RED','source_sha':source_sha,
        'source_tree':source_tree,'G0_G8_qualification':False,'quota_enforcement_claimed':False,
        'cleanup_credit_claimed':False,'real_orders_sent':0,'recurring_additional_cost_usd':0}
    namespace=None
    try:
        require_history_nonroot()
        owner_check();result['fresh_storage']=history_storage(repo,output)
        result['git_before']=history_git_inventory(repo)
        binding={'candidate_sha':source_sha,'candidate_tree':source_tree,'producer':'DEVELOPMENT_GIT_HISTORY',
            'attempt_id':os.environ['GITHUB_RUN_ID']+'-1','owner_id':owner_session,
            'runner_class':'DIAGNOSTIC','workload_fingerprint':plan_sha256}
        namespace=lifecycle.create_namespace(short_control_parent(repo),binding)
        payload={'repo':str(repo),'source_sha':source_sha,'source_tree':source_tree,
            'requirements':history_requirements(),'result':str(namespace.path/'history-worker.json')}
        code='import json,sys;sys.path.insert(0,sys.argv[1]);from scripts.rc6_development_checks import history_worker;history_worker(json.loads(sys.argv[2]))'
        observations=[]
        def progress(*args):
            owner_check();observations.append(history_git_inventory(repo));history_storage(repo,output)
        kernel,fin=lifecycle.execute_owned(namespace,[sys.executable,'-I','-B','-c',code,str(ROOT),json.dumps(payload)],
            cwd=repo,environ=history_environment(),log_relative='history-native.log',timeout_seconds=HISTORY_SECONDS,
            fin_label='development-history',progress=progress)
        lifecycle.require_fin(namespace,fin)
        result.update(kernel=kernel,actual_owned_fin_closed=True,git_observations=observations)
        required=['history-native.log','producer-owned-fin-development-history.json']
        if (namespace.path/'history-worker.json').is_file():required.append('history-worker.json')
        capture=lifecycle.capture_required_evidence(namespace,fin,Path(output)/'history-owned',required)
        result['capture_manifest_sha256']=capture.manifest_sha256
        worker=next((row for row in capture.files if row['relative_source']=='history-worker.json'),None)
        if worker:result['worker']=json.loads((capture.path/worker['capture_file']).read_bytes())
        result['cleanup']=lifecycle.cleanup_namespace(namespace,fin,capture)
        owner_check();result['git_after']=history_git_inventory(repo)
        result['fresh_storage_after']=history_storage(repo,output)
        deadline=time.monotonic()+HISTORY_SECONDS
        source_after={key:history_git(repo,args,deadline).stdout.decode().strip() for key,args in (
            ('source_sha',['rev-parse','HEAD']),('source_tree',['rev-parse','HEAD^{tree}']))}
        admission.require(source_after=={'source_sha':source_sha,'source_tree':source_tree},
            'DEVELOPMENT_HISTORY_PARENT_SOURCE_REBOUND')
        result['parent_source_after']=source_after
        admission.require(lifecycle._phase_green(fin) and result.get('worker',{}).get('status')=='PASS_GIT_HISTORY_ONLY'
            and result['worker']['source_sha']==source_sha and result['worker']['source_tree']==source_tree,
            'DEVELOPMENT_HISTORY_ORIGINAL_NATIVE_PHASE_RED')
        result['status']='PASS_GIT_HISTORY_ONLY'
    except (ValueError,OSError,KeyError,TypeError,subprocess.SubprocessError) as error:
        signature=str(error)
        result.update(error_class=type(error).__name__,error_signature=signature if re.fullmatch(
            '[A-Z][A-Z0-9_]*(?::[0-9]+)?',signature) else type(error).__name__)
        if namespace is not None:
            result.update(namespace_path=str(namespace.path),actual_owned_fin_closed=result.get('actual_owned_fin_closed',False),
                unknown_fin_retained=result.get('actual_owned_fin_closed') is not True)
    with (Path(output)/'history-result.json').open('xb') as stream:stream.write(canonical(result))
    admission.require(result['status']=='PASS_GIT_HISTORY_ONLY','DEVELOPMENT_HISTORY_BOOTSTRAP_RED')
    return result


def require_canonical_umask():
    """Observe the kernel's inherited mask; never normalize Source after freeze."""
    matches=re.findall(r'^Umask:\s*([0-7]{4})$',Path('/proc/self/status').read_text(),re.MULTILINE)
    admission.require(matches==['0022'],'DEVELOPMENT_CANONICAL_UMASK_0022_REQUIRED')
    return matches[0]


def short_control_parent(repo):
    """Borrow only a writable short parent on the actually measured mount.

    A parent path is never deletion authority. Each native child still needs
    its exclusively created namespace, opaque FIN and verified external copy.
    Original AF_UNIX controls impose the same 50-character path bound.
    """
    from scripts import porota_predeploy_cleanup as custody
    with custody.directory(repo) as source_fd:
        source_dev=os.fstat(source_fd).st_dev;source_mount=custody.mount_id(source_fd)
    for parent in (Path('/tmp'),Path.home(),repo.parent):
        if len(os.fsencode(parent))>12 or not os.access(parent,os.W_OK):continue
        with custody.directory(parent) as descriptor:
            details=os.fstat(descriptor)
            if (details.st_uid==os.geteuid() and details.st_dev==source_dev
                    and custody.mount_id(descriptor)==source_mount):
                return parent
    raise ValueError('DEVELOPMENT_SHORT_SAME_MOUNT_PARENT_UNAVAILABLE')


def tooling_lock(raw, names):
    """Select exact, hashed canonical lock blocks; never resolve a new version."""
    admission.require(set(names)==DEPENDENCIES and len(names)==len(DEPENDENCIES),
        'DEVELOPMENT_EXACT_CHEAP_DEPENDENCIES_REQUIRED')
    found={}
    for block in re.split(r'(?m)(?=^[A-Za-z0-9_.-]+==)',raw):
        match=re.match(r'([A-Za-z0-9_.-]+)==([^\s\\]+)',block)
        if match is None:continue
        name=re.sub('[-_.]+','-',match[1]).lower()
        if name not in DEPENDENCIES:continue
        lines=block.strip().splitlines()
        admission.require(name not in found and re.fullmatch(
            r'[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+\s*\\',lines[0]) and all(re.fullmatch(
            r'\s*--hash=sha256:[0-9a-f]{64}(?:\s*\\)?',line) for line in lines[1:])
            and len(lines)>1 and not lines[-1].endswith('\\')
            and all(line.endswith('\\') for line in lines[:-1]),'DEVELOPMENT_HASHED_CANONICAL_LOCK_REQUIRED')
        found[name]='\n'.join(lines)+'\n'
    admission.require(set(found)==DEPENDENCIES,'DEVELOPMENT_CANONICAL_DEPENDENCY_MISSING')
    return ''.join(found[name] for name in sorted(found))


def junit_facts(raw):
    admission.require(len(raw)<=16*1024**2,'DEVELOPMENT_JUNIT_BOUND')
    root=ET.fromstring(raw)
    cases=root.findall('.//testcase')
    ids=[(case.get('classname'),case.get('name')) for case in cases]
    admission.require(cases and len(ids)==len(set(ids)),'DEVELOPMENT_JUNIT_IDENTITY_DUPLICATED_OR_EMPTY')
    facts={'cases':len(cases),'identities':ids,'failures':sum(case.find('failure') is not None for case in cases),
            'errors':sum(case.find('error') is not None for case in cases),
            'skipped':sum(case.find('skipped') is not None for case in cases),'sha256':digest(raw)}
    suites=[root] if root.tag=='testsuite' else root.findall('.//testsuite')
    admission.require(len(suites)==1 and all(int(suites[0].get(field,'-1'))==facts[key]
        for field,key in (('tests','cases'),('failures','failures'),('errors','errors'),('skipped','skipped'))),
        'DEVELOPMENT_JUNIT_HEADER_REBOUND')
    return facts


def control_summary(result):
    """Publish facts from the preserved native result, never replace its RAW."""
    summary={key:result.get(key) for key in ('status','source_sha','source_tree',
        'identities_equal','fullSource_unchanged','source_validation_error','next_epoch_launched')}
    summary.update(epochs=[],G0_G8_qualification=False,Product157_qualified=False,deployed=False,real_orders_sent=0)
    for epoch in result['epochs']:
        entry={key:epoch.get(key) for key in ('epoch','passed','retirement_error','junit_validation_error')}
        entry['kernel']={key:epoch.get('kernel',{}).get(key) for key in ('returncode','timed_out','wall_seconds',
            'peak_rss_bytes','remaining_owned_children','actual_child_reaped','owned_children_exhaustion_verified',
            'process_group_absent_after_reap','supervisor_errors')}
        entry['cleanup']={key:(epoch.get('cleanup') or {}).get(key) for key in ('actual_owned_fin_closed',
            'namespace_removed','original_namespace_removed','foreign_paths_removed','capture_manifest_sha256',
            'post_fin_owned_directory_owner_write_changes')}
        entry['junit']={key:epoch['junit'].get(key) for key in ('cases','failures','errors','skipped','sha256')} if epoch.get('junit') else None
        summary['epochs'].append(entry)
    return summary


def preserve_result(output,result):
    raw=canonical(result)
    (output/'result.json').write_bytes(raw)
    summary=control_summary(result)
    summary['original_result_sha256']=digest(raw)
    print('RC6_DEVELOPMENT_NATIVE_RESULT='+canonical(summary).decode().strip(),flush=True)


def preserve_admission_rejection(output, error, *, source_sha, source_tree, plan_sha256):
    """Keep a bounded RED control even when admission rejects before tooling.

    This grants no namespace, fixture, Source qualification or cleanup authority.
    Every ancestor is held NOFOLLOW; a prior control is never overwritten.
    """
    output=Path(output)
    admission.require(output.is_absolute() and len(output.parts)<=64
        and str(output)==os.path.abspath(output) and not output.is_relative_to(ROOT)
        and not ROOT.is_relative_to(output),'DEVELOPMENT_REJECTION_OUTPUT_OUTSIDE_SOURCE_REQUIRED')
    descriptor=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    held=[descriptor]
    try:
        for component in output.parts[1:-1]:
            descriptor=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,
                dir_fd=descriptor)
            held.append(descriptor)
        try:os.mkdir(output.name,mode=0o700,dir_fd=descriptor)
        except FileExistsError:pass
        descriptor=os.open(output.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,
            dir_fd=descriptor)
        held.append(descriptor)
        info=os.fstat(descriptor)
        admission.require(info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o700,
            'DEVELOPMENT_REJECTION_OWNED_PRIVATE_CONTROL_REQUIRED')
        signature=str(error)
        if re.fullmatch(r'[A-Z][A-Z0-9_]*(?::[0-9]+)?',signature) is None:
            signature=type(error).__name__
        row={'schema':'porota.rc6.development-admission-rejection.v1','status':'RED_ADMISSION',
            'source_sha':source_sha,'source_tree':source_tree,'plan_sha256':plan_sha256,
            'error_signature':signature,'error_class':type(error).__name__,
            'G0_G8_qualification':False,'Product157_qualified':False,'workload_started':False,
            'fixture_created':False,'launch_authorized':False,'cleanup_authorized':False,
            'real_orders_sent':0}
        fd=os.open('admission-rejected.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,
            0o600,dir_fd=descriptor)
        with os.fdopen(fd,'wb') as stream:
            stream.write(canonical(row));stream.flush();os.fsync(stream.fileno())
        os.fsync(descriptor)
        return row
    finally:
        for fd in reversed(held):os.close(fd)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--source-sha',required=True);parser.add_argument('--source-tree',required=True)
    parser.add_argument('--owner-session',required=True);parser.add_argument('--launch-receipt-url',required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--admit-only',action='store_true')
    parser.add_argument('--python311');parser.add_argument('--python312')
    args=parser.parse_args()
    admission.require(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('GITHUB_REPOSITORY')==admission.REPO
        and os.environ.get('GITHUB_ACTOR')=='mbalbo2023' and os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch'
        and os.environ.get('GITHUB_RUN_ATTEMPT')=='1','DEVELOPMENT_EXPLICIT_CANONICAL_DISPATCH_REQUIRED')
    admission.require(governed.git(ROOT,'rev-parse','HEAD').decode().strip()==args.source_sha
        and governed.git(ROOT,'rev-parse','HEAD^{tree}').decode().strip()==args.source_tree,
        'DEVELOPMENT_EXACT_FROZEN_SOURCE_REQUIRED')
    require_canonical_umask()
    raw=args.plan.read_bytes();plan=json.loads(raw)
    admission.require(plan['schema']=='porota.rc6.development-checks-plan.v1'
        and plan['scope']=='DEVELOPMENT_REGRESSIONS_ONLY_NOT_G0_G8_OR_PRODUCT157'
        and plan['material_gate_launched'] is plan['full_gov_claimed'] is False,
        'DEVELOPMENT_SCOPE_REQUIRED')
    admission.require(type(plan['suites']) is list and 0<len(plan['suites'])<=128
        and len(set(plan['suites']))==len(plan['suites']) and all(re.fullmatch(
            r'tests/test_[A-Za-z0-9_]+\.py(?:::[A-Za-z0-9_]+)?',name) for name in plan['suites']),
        'DEVELOPMENT_BOUNDED_CHEAP_SUITES_REQUIRED')
    github=Github(os.environ.get('GH_TOKEN'))
    def owner_check():
        return verify_owner(github,args.launch_receipt_url,sha=args.source_sha,tree=args.source_tree,
            owner=args.owner_session,plan_sha256=digest(raw),authorization_field='RC6_DEVELOPMENT_CHECKS_AUTHORIZATION')
    try:
        owner_check()
        admission.dedup_admission(args.source_sha,'development-checks')
    except (ValueError,OSError) as error:
        preserve_admission_rejection(args.output,error,source_sha=args.source_sha,
            source_tree=args.source_tree,plan_sha256=digest(raw))
        raise
    output=args.output.absolute()
    admission.require(not output.is_relative_to(ROOT),'DEVELOPMENT_OUTPUT_OUTSIDE_SOURCE_REQUIRED')
    if args.admit_only:
        output.mkdir(mode=0o700)
        history=prepare_history(ROOT,output,source_sha=args.source_sha,source_tree=args.source_tree,
            owner_session=args.owner_session,plan_sha256=digest(raw),owner_check=owner_check)
        (output/'tooling.lock.txt').write_text(tooling_lock((ROOT/'requirements.lock.txt').read_text(),plan['dependency_names']))
        (output/'admission.json').write_bytes(canonical({'scope':plan['scope'],'source_sha':args.source_sha,
            'source_tree':args.source_tree,'owner_body_sha256':owner_check(),'qualification_claimed':False,
            'history_sha256':digest(canonical(history))}))
        return 0
    admission.require(output.is_dir() and not output.is_symlink(),'DEVELOPMENT_ADMITTED_OUTPUT_REQUIRED')
    result={'schema':'porota.rc6.development-checks.v1','scope':plan['scope'],'source_sha':args.source_sha,
        'source_tree':args.source_tree,'plan_sha256':digest(raw),'epochs':[],
        'G0_G8_qualification':False,'Product157_qualified':False,'build_once_artifact_created':False,
        'deployed':False,'real_orders_sent':0}
    before=governed.source_pin(ROOT,args.source_sha,args.source_tree)
    (output/'source-before.index.json').write_bytes(canonical(before))
    control_parent=short_control_parent(ROOT)
    for epoch,interpreter in (('311',args.python311),('312',args.python312)):
        owner_check()
        admission.require(interpreter and Path(interpreter).is_absolute(),'DEVELOPMENT_EXACT_INTERPRETER_REQUIRED')
        namespace=lifecycle.create_namespace(control_parent,{'candidate_sha':args.source_sha,'candidate_tree':args.source_tree,
            'producer':'DEVELOPMENT_CONTROLS_'+epoch,'attempt_id':os.environ['GITHUB_RUN_ID']+'-1',
            'owner_id':args.owner_session,'runner_class':'DIAGNOSTIC','workload_fingerprint':digest(raw)})
        (namespace.path/'t').mkdir(mode=0o700)
        env={**{key:value for key,value in os.environ.items() if not any(
            word in key.upper() for word in ('TOKEN','SECRET','PASSWORD','API_KEY','ACCESS_KEY'))},
             'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1','PYTHONDONTWRITEBYTECODE':'1',
             'TMPDIR':str(namespace.path/'t'),'RC6_UNIT_NAMESPACE_PARENT':str(control_parent)}
        command=[interpreter,'-B','-m','pytest','--noconftest','-c','/dev/null','--rootdir='+str(ROOT),
            '-p','no:cacheprovider','-o','junit_family=legacy','--basetemp='+str(namespace.path/'p'),
            '--junitxml='+str(namespace.path/'junit.xml'),'-q',*plan['suites']]
        kernel,fin=lifecycle.execute_owned(namespace,command,cwd=ROOT,environ=env,
            log_relative='native.log',timeout_seconds=180,fin_label='development-'+epoch)
        # Only a genuine live FIN may authorize reads or retirement. Logical
        # RED still captures its original JUnit and log before cleanup.
        required=['native.log','producer-owned-fin-development-'+epoch+'.json']
        if (namespace.path/'junit.xml').is_file():required.append('junit.xml')
        captured=lifecycle.capture_required_evidence(namespace,fin,output/epoch,required)
        rows={row['relative_source']:row for row in captured.files}
        facts=None
        validation_error=None
        if 'junit.xml' in rows:
            try: facts=junit_facts((output/epoch/rows['junit.xml']['capture_file']).read_bytes())
            except (ValueError,ET.ParseError) as error:validation_error=type(error).__name__
        cleanup=None
        retirement_error=None
        try:
            cleanup=lifecycle.cleanup_namespace(namespace,fin,captured)
        except (ValueError,OSError) as error:
            retirement_error={'type':type(error).__name__,'errno':getattr(error,'errno',None)}
        result['epochs'].append({'epoch':epoch,'interpreter':interpreter,'kernel':kernel,
            'cleanup':cleanup,'retirement_error':retirement_error,'junit':facts,'junit_validation_error':validation_error,
            'passed':kernel['returncode']==0 and facts is not None
            and retirement_error is None and not any(facts[key] for key in ('failures','errors','skipped'))})
        preserve_result(output,result)
        if retirement_error is not None:
            result.update(status='RED_CONTROL_RETIREMENT',fullSource_unchanged=None,
                next_epoch_launched=False,cleanup_credit_claimed=False)
            preserve_result(output,result)
            return 1
    result['identities_equal']=all(e['junit'] is not None for e in result['epochs']) and (
        result['epochs'][0]['junit']['identities']==result['epochs'][1]['junit']['identities'])
    after=governed.source_pin(ROOT,args.source_sha,args.source_tree)
    (output/'source-after.index.json').write_bytes(canonical(after))
    try:
        result['source_atime_observations']=governed.compare_source(before,after)
        result['fullSource_unchanged']=True
    except (ValueError,OSError) as error:
        result['fullSource_unchanged']=False
        result['source_validation_error']=type(error).__name__
    result['status']='PASS_DEVELOPMENT_ONLY' if result['identities_equal'] and result['fullSource_unchanged'] and all(
        e['passed'] for e in result['epochs']) else 'RED'
    preserve_result(output,result)
    return int(result['status']=='RED')


if __name__=='__main__':
    sys.dont_write_bytecode=True
    raise SystemExit(main())
