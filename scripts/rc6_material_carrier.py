"""Reusable supplemental RC6 Actions carrier. No build, deploy or promotable artifact."""
import argparse
from collections import Counter
import ctypes
import errno
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import runpy
import stat
import subprocess
import sys
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts import rc6_architectural_gates as architectural
from scripts import rc6_heavy_test_preflight as capacity
from scripts import rc6_authenticated_fixture_lifecycle as fixture_lifecycle

REPO='mbalbo2023/Porota-trading';REPO_ID=1338680554
ORIGIN='https://github.com/'+REPO+'.git'
BRANCH='recovery/rc6-material-fix-forward-3091e93-20261006';PR=476
FIELDS=('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_blocks','st_atime_ns','st_mtime_ns','st_ctime_ns')
STABLE10=tuple(k for k in FIELDS if k!='st_atime_ns')
PREFIX='docs/audits/convergence/evidence/controlled-successor-20261006/'
OBJECTS_MEMBER=PREFIX+'checkpoint7-read-diagnostics/full-gov-prepared-only/original-required-19-objects.json'
OBJECTS_SHA='492a3c0de260501776ee235ea5472da373d6c5881a4dfafca05518297fc16579'
BIG_MEMBER=PREFIX+'checkpoint6-phase-child-barrier/native-big-0f73e2c/native_big_direct_supervisor.py.source'
HORIZON_MEMBER='docs/audits/rc6-convergence-persistence-evidence/run_full_horizon_1201_v2.py.source'
HORIZON_SHA='ac9ba0f457ee4592646d91c5654f037efbfa2bc4f758469e16b7cf51d87e7aec'
EXPORT_MEMBER='docs/audits/convergence/evidence/source-exporter-v2-controls-abc-20261006/source/rc6_export_whole_git_source_v2_candidate.py.source'
VERSIONS={'311':'3.11.16','312':'3.12.14'}
PREPARATION_SCOPE={'locked_python_epochs':['311','312'],'installed_distribution_count_per_epoch':157,
    'original_git_objects':19,'full_git':True,'full_source':True,
    'pip_fetch_source_and_git_preparation_included':True}

def workload_fingerprint(producer):
    """Comparable workload identity excludes machine/SHA and timing outcomes."""
    if producer in ('capacity-probe','capacity-calibration'):
        image,hard_limit=(5*1024**3,512*1024**2) if producer=='capacity-probe' else (26*1024**3,20*1024**3)
        return digest(canonical({'schema':'porota.rc6.capacity-diagnostic-workload.v1','producer':producer,
            'backing_image_bytes':image,'project_hard_limit_bytes':hard_limit,'residual_reserve_bytes':4*1024**3,
            'financial_tick_allowed':False,'qualification_claimed':False,
            'python_versions':[] if producer=='capacity-probe' else list(VERSIONS.values())}))
    return digest(canonical({'schema':'rc6.material-workload-comparison.v1','producer':producer,
        'catalog':12000,'observations':60000,'outer_seconds':90,'maximum_rss_bytes':2*1024**3,
        'maximum_evidence_bytes':128*1024**2,'maximum_retained_entries':100000,
        'factual_paper_exits':5,'automatic_Gov_scope':'repository-root-original-governed-exclusions'}))

def capacity_binding(a,producer):
    return {'candidate_sha':a.source_sha,'candidate_tree':a.source_tree,'producer':producer,
        'attempt_id':os.environ['GITHUB_RUN_ID']+':'+os.environ['GITHUB_RUN_ATTEMPT'],
        'owner_id':a.owner_session,'workload_fingerprint':workload_fingerprint(producer),
        'runner_class':architectural.RUNNER_CLASS}

def heavy_preflight(a,run,path,producer,label):
    peak=a.capacity_peaks.get(producer)
    need(type(peak) is dict,'KNOWN_COMPARABLE_PEAK_REQUIRED_BEFORE_PRODUCER')
    binding=capacity_binding(a,producer);policy=capacity.load_policy(a.repo_root/'ops/policy/rc6-heavy-test-governance-v1.json')
    receipt=capacity.build_receipt(policy=policy,path=path,expected_peak_bytes=capacity.envelope_allocated_bytes(peak),
        binding=binding,comparable_peak=peak)
    save(run.control/(label+'.capacity-before.json'),receipt)
    need(receipt['capacity']['status']=='GREEN','CAPACITY_PREFLIGHT_BLOCKED_BEFORE_PRODUCER')
    live=capacity.validate_live_receipt(path=path,receipt=receipt,binding=binding,policy=policy)
    save(run.control/(label+'.capacity-live.json'),live)
    record={'label':label,'binding':binding,'measured_path':str(path),
        'before':{'path':'controls/'+label+'.capacity-before.json','sha256':digest(canonical(receipt))},
        'live':{'path':'controls/'+label+'.capacity-live.json','sha256':digest(canonical(live))},
        'measured_at_unix_ns':live['measured_at_unix_ns']}
    if not hasattr(run,'capacity_records'):run.capacity_records=[]
    run.capacity_records.append(record);run.pending_capacity=[record]
    return binding,peak,live

def preparation_capacity(a,run,root,label,*,additional_storage=()):
    """Bootstrap is a compound producer, not a focal/epoch peak assumption."""
    # Its comparison must cover both locked environments and the complete
    # original Git/Source preparation. Unknown bootstrap capacity blocks before
    # an environment, fetch or clone starts; no later G0 receipt can repair it.
    peak=a.capacity_peaks.get('bootstrap')
    need(type(peak) is dict,'KNOWN_COMPARABLE_BOOTSTRAP_PEAK_REQUIRED_BEFORE_PREPARATION')
    need(peak.get('preparation_scope')==PREPARATION_SCOPE,
        'BOOTSTRAP_PEAK_COMPLETE_PREPARATION_SCOPE_NOT_VERIFIED')
    results=[]
    launch_proofs=[]
    for storage_role,path in (('bootstrap_namespace',root),('candidate_git_objects',a.repo_root),*additional_storage):
        if storage_role not in ('bootstrap_namespace','candidate_git_objects'):
            need(safe_path(path).is_relative_to(root),'BOOTSTRAP_TEMPORARY_ROOT_OUTSIDE_AUTHENTICATED_NAMESPACE')
        binding,comparison,live=heavy_preflight(a,run,path,'bootstrap',label+'-'+storage_role)
        launch_proofs.extend(run.pending_capacity)
        results.append({'storage_role':storage_role,'measured_path':str(path),'binding':binding,
            'comparable_peak':comparison,'capacity_live':live})
    run.pending_capacity=launch_proofs
    return results

def complete_full_git(a,run,root):
    """A shallow checkout is transport bootstrap, never fullGit qualification."""
    preparation_capacity(a,run,root,'before-authenticated-complete-Git-fetch')
    shallow=git(a.repo_root,'rev-parse','--is-shallow-repository').strip()
    need(shallow in (b'true',b'false'),'GIT_SHALLOW_STATE_UNKNOWN')
    if shallow==b'true':
        askpass=root/'full-git-askpass.py'
        save_raw(askpass,b'#!/usr/bin/env python3\nimport os,sys\nprint("x-access-token" if "username" in sys.argv[1].lower() else os.environ["RC6_READONLY_GIT_TOKEN"])\n')
        fd=os.open(askpass,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:os.fchmod(fd,0o700)
        finally:os.close(fd)
        token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN');need(bool(token),'FULL_GIT_READONLY_TOKEN_REQUIRED')
        native=run(['git','--no-replace-objects','-C',str(a.repo_root),'-c','credential.helper=',
            'fetch','--unshallow','--tags','--no-write-fetch-head','--no-auto-maintenance',ORIGIN,
            '+refs/heads/*:refs/remotes/origin/*'],cwd=root,label='fullGit-guarded-fetch',limit=1800,
            env={'GIT_ASKPASS':str(askpass),'RC6_READONLY_GIT_TOKEN':token})
        need(native['returncode']==0,'CAPACITY_ADMITTED_FULL_GIT_FETCH_FAILED')
    need(git(a.repo_root,'rev-parse','--is-shallow-repository').strip()==b'false'
        and git(a.repo_root,'rev-parse','HEAD').decode().strip()==a.source_sha
        and git(a.repo_root,'rev-parse','HEAD^{tree}').decode().strip()==a.source_tree,
        'SHALLOW_BOOTSTRAP_MUST_NOT_QUALIFY_AS_FULL_GIT')
    save(run.control/'fullGit-bootstrap-to-qualified.json',{'schema':'rc6.capacity-guarded-full-git-transition.v1',
        'source_sha':a.source_sha,'source_tree':a.source_tree,'bootstrap_was_shallow':shallow==b'true',
        'full_git_after_guarded_fetch':True,'same_HEAD_tree_unchanged':True,
        'native_fetch_label':'fullGit-guarded-fetch' if shallow==b'true' else None,
        'runtime_or_deploy_claimed':False,'real_orders_sent':0})

def tooling_capacity(admission_path,repo,environ):
    """No tooling download starts under a native-only capacity comparison."""
    from types import SimpleNamespace
    authority=document(read(admission_path))
    need(authority.get('status')=='ADMITTED_NATIVE_NOT_STARTED','CANDIDATE_TOOLING_REQUIRES_ACTUAL_ADMISSION')
    from scripts import rc6_material_pr_admission as controller
    authority=controller.admit(source_sha=authority['source_sha'],source_tree=authority['source_tree'],
        launch_receipt_url=authority['launch_receipt_url'],owner_session=authority['owner_session'],gate=authority['gate'])
    peak=authority.get('capacity_peaks',{}).get('bootstrap')
    expected={'setup_python_action_sha':'a26af69be951a213d495a4c3e4e4022e16d87065',
        'python_versions':['3.11.16','3.12.14'],'tool_cache_population_included':True}
    need(type(peak) is dict and peak.get('reviewed_cpython_tooling')==expected,
        'PYTHON_TOOLING_CAPACITY_UNKNOWN_BEFORE_ACTION_DOWNLOAD')
    args=SimpleNamespace(repo_root=Path(repo),source_sha=authority['source_sha'],source_tree=authority['source_tree'],
        owner_session=authority['owner_session'],capacity_peaks=authority['capacity_peaks'])
    root=Path(admission_path).parent;control=root/'tooling-controls';control.mkdir(mode=0o700)
    run=SimpleNamespace(control=control)
    for label,path in (('candidate_git',Path(repo)),('admission_controls',root),
        ('runner_temporary',safe_path(environ['RUNNER_TEMP'])),('reviewed_tool_cache',safe_path(environ['RUNNER_TOOL_CACHE']))):
        heavy_preflight(args,run,path,'bootstrap','before-reviewed-Python-'+label)
    save(root/'tooling-capacity.json',{'schema':'rc6.reviewed-python-tooling-capacity.v1','capacity_records':run.capacity_records,
        'source_sha':args.source_sha,'source_tree':args.source_tree,'qualification_claimed':False,
        'tooling_action_launched':False,'real_orders_sent':0})
    return run.capacity_records

def diagnostic_main(argv=None):
    """Isolated quota diagnosis is never a candidate gate or a product claim."""
    parser=argparse.ArgumentParser();parser.add_argument('--diagnostic-admission-json',type=Path,required=True)
    parser.add_argument('--repo-root',type=Path,required=True);args=parser.parse_args(argv)
    os.umask(0o022);repo=safe_path(args.repo_root);previous=document(read(args.diagnostic_admission_json))
    from scripts import rc6_material_pr_admission as controller
    need(previous.get('schema')=='porota.rc6.capacity-diagnostic-admission.v1'
        and previous.get('qualification_claimed') is False,'ISOLATED_CAPACITY_DIAGNOSTIC_ADMISSION_REQUIRED')
    authority=controller.admit_diagnostic(source_sha=previous['source_sha'],source_tree=previous['source_tree'],
        launch_receipt_url=previous['launch_receipt_url'],owner_session=previous['owner_session'],gate=previous['gate'])
    need(authority['launch_body_sha256']==previous['launch_body_sha256']
        and git(repo,'rev-parse','HEAD').decode().strip()==authority['source_sha']
        and git(repo,'rev-parse','HEAD^{tree}').decode().strip()==authority['source_tree']
        and not git(repo,'status','--porcelain').strip(),'DIAGNOSTIC_CHECKOUT_OR_IMMUTABLE_AUTHORITY_REBOUND')
    from types import SimpleNamespace
    actual=SimpleNamespace(source_sha=authority['source_sha'],source_tree=authority['source_tree'],
        owner_session=authority['owner_session'])
    binding=capacity_binding(actual,authority['gate']);namespace=fixture_lifecycle.create_namespace(Path.home(),binding)
    controls=namespace.path/'controls';controls.mkdir(mode=0o700);output=namespace.path/'diagnostic'
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as stream:stream.write('control_root='+str(controls)+'\nsafe_payload_upload=false\n')
    save(controls/'admission.json',authority)
    claim_path=controls/'namespace-receipt.json';save(claim_path,fixture_lifecycle.namespace_receipt(namespace))
    binding_path=controls/'binding.json';save(binding_path,binding)
    producer_pin=module_pin(repo,'scripts/rc6_capacity_calibration.py',authority['source_sha'])
    manager_pin=module_pin(repo,'scripts/rc6_controlled_native_child_manager.py',authority['source_sha'])
    manager=runpy.run_path(str(repo/manager_pin['path']));run=OwnedRunner(manager,controls)
    mode='capability' if authority['gate']=='capacity-probe' else 'bootstrap'
    command=[sys.executable,'-I','-B',str(repo/producer_pin['path']),'--mode',mode,'--source-root',str(repo),
        '--source-sha',authority['source_sha'],'--source-tree',authority['source_tree'],
        '--namespace-receipt',str(claim_path),'--output',str(output),'--binding-json',str(binding_path)]
    environment={key:value for key,value in os.environ.items() if key.startswith(('GITHUB_','RUNNER_'))}
    environment['RC6_CALIBRATION_ADMISSION_JSON']=str(controls/'admission.json')
    if mode=='bootstrap':
        token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        need(bool(token),'DIAGNOSTIC_SCOPED_READONLY_GIT_TOKEN_REQUIRED')
        environment['RC6_CALIBRATION_READONLY_GIT_TOKEN']=token
        evidence=architectural.verify_capacity_capability_artifact(authority['capability_prerequisite'],
            source_sha=authority['source_sha'],source_tree=authority['source_tree'],
            read_contract_sha256=authority['read_contract_sha256'],evidence_root=namespace.path/'capability-artifact')
        save(controls/'capability-origin.json',evidence['origin']);save_raw(controls/'capability-receipt.json',evidence['receipt_raw'])
        command+=['--capability-receipt',str(controls/'capability-receipt.json'),
            '--capability-artifact-origin',str(controls/'capability-origin.json')]
        cache=safe_path(os.environ['RUNNER_TOOL_CACHE'])
        for epoch,version in VERSIONS.items():
            interpreter=cache/'Python'/version/'x64/bin'/('python'+epoch[0]+'.'+epoch[1:])
            need(interpreter.is_file() and not interpreter.is_symlink() and os.access(interpreter,os.X_OK),
                'DIAGNOSTIC_PINNED_PREEXISTING_CPYTHON_TOOLING_MISSING')
            command+=['--python'+epoch,str(interpreter)]
    code=1
    try:
        native=run(command,cwd=repo,label='isolated-'+authority['gate'],limit=600 if mode=='capability' else 11400,
            env=environment,namespace=namespace)
        terminal=document(read(output/'calibration.json'))
        need(terminal.get('schema')=='porota.rc6.capacity-calibration.v1'
            and terminal.get('source_sha')==authority['source_sha'] and terminal.get('source_tree')==authority['source_tree']
            and terminal.get('mode')==mode and terminal.get('binding')==binding
            and terminal.get('qualification_claimed') is False,'DIAGNOSTIC_PRODUCER_RECEIPT_SOURCE_SCOPE_REBOUND')
        need(terminal.get('payload_upload_safe') is True and terminal.get('cleanup',{}).get('namespace_removed') is True
            and 'preserved_owned_namespace' not in terminal,'DIAGNOSTIC_INNER_LOOP_OR_NAMESPACE_UNKNOWN_OUTER_CLEANUP_VETO')
        required=[path.relative_to(namespace.path).as_posix() for path in sorted(output.rglob('*')) if path.is_file()]
        required+=['isolated-'+authority['gate']+'.native.log']
        payload=Path(args.diagnostic_admission_json).parent/'diagnostic-payload';payload.mkdir(mode=0o700)
        destination=payload/'sealed'
        captured=fixture_lifecycle.capture_required_evidence(namespace,run.fins[namespace.nonce],destination,required)
        cleanup=fixture_lifecycle.cleanup_namespace(namespace,run.fins[namespace.nonce],captured)
        code=0 if native['returncode']==0 and terminal.get('status')=='GREEN' and cleanup.get('namespace_removed') is True else 1
        fin_name=run.fins[namespace.nonce].control_name
        def captured_ref(name):
            selected=[record for record in captured.files if record['relative_source']==name]
            need(len(selected)==1,'DIAGNOSTIC_ORIGINAL_CAPTURE_REFERENCE_MISSING')
            record=selected[0];return {'path':'sealed/'+record['capture_file'],'sha256':record['sha256']}
        save(payload/'diagnostic-result.json',{'schema':'porota.rc6.capacity-diagnostic-result.v1',
            'status':'GREEN_DIAGNOSTIC_ONLY' if code==0 else 'BLOCKED','source_sha':authority['source_sha'],
            'source_tree':authority['source_tree'],'gate':authority['gate'],'producer_receipt':terminal,
            'producer_receipt_ref':captured_ref('diagnostic/calibration.json'),
            'outer_native_fin_ref':captured_ref(fin_name),
            'capture_manifest_ref':{'path':'sealed/manifest.json','sha256':captured.manifest_sha256},
            'actual_outer_cleanup':cleanup,'qualification_claimed':False,'G0_G8_claimed':False,
            'financial_tick_executed':False,'real_orders_sent':0,'runtime_validated':False,'final_candidate_eligible':False})
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a') as stream:
                stream.write('control_root='+str(Path(args.diagnostic_admission_json).parent)+'\nsafe_payload_upload=true\npayload_root='+str(payload)+'\n')
    except BaseException as error:
        reason=str(error).partition(':')[0];reason=reason if re.fullmatch('[A-Z][A-Z0-9_]{0,191}',reason) else 'NON_LITERAL_DIAGNOSTIC_FAILURE'
        target=controls if controls.exists() else Path(args.diagnostic_admission_json).parent
        save(target/'diagnostic-error.json',{'status':'BLOCKED','reason':reason,'class':type(error).__name__,
            'payload_reads_allowed':not run.unknown,'qualification_claimed':False,'G0_G8_claimed':False,'real_orders_sent':0})
    return code

def need(ok,reason):
    if not ok:raise ValueError(reason)
def digest(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def document(raw):
    def pairs(rows):
        out={}
        for k,v in rows:need(k not in out,'DUPLICATE_CONTROL_JSON_KEYS');out[k]=v
        return out
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda x:need(False,'NONFINITE_CONTROL_JSON'))
def attrs(i):return {k:getattr(i,k) for k in FIELDS}
def safe_path(p):
    p=Path(os.path.abspath(p));need(not any(q.is_symlink() for q in (p,*p.parents)),'CARRIER_ALIAS_FORBIDDEN');return p
def read(p,maximum=128*1024**2):
    p=safe_path(p);fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NOATIME|os.O_CLOEXEC)
    try:
        before=os.fstat(fd);need(stat.S_ISREG(before.st_mode) and before.st_uid==os.geteuid() and before.st_nlink==1
            and before.st_size<=maximum,'CARRIER_OWNED_BOUNDED_REGULAR_FILE_REQUIRED')
        out=[];n=0
        while part:=os.read(fd,min(1024**2,maximum+1-n)):
            out.append(part);n+=len(part);need(n<=maximum,'CARRIER_FILE_LIMIT')
        need(n==before.st_size and attrs(before)==attrs(os.fstat(fd))==attrs(p.lstat()),'CARRIER_CAPTURE_ALL11_CHANGED')
        return b''.join(out)
    finally:os.close(fd)
def save_raw(p,raw):
    p=safe_path(p);fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    return digest(raw)
def save(p,row):return save_raw(p,canonical(row))
def bootstrap():
    need(sys.platform=='linux' and platform.machine()=='x86_64' and os.getuid()==os.geteuid()>0,'REAL_NONROOT_LINUX_OWNER_REQUIRED')
    libc=ctypes.CDLL(None,use_errno=True);before=ctypes.c_int();after=ctypes.c_int()
    need(libc.prctl(37,ctypes.byref(before),0,0,0)==0 and before.value in (0,1),'CARRIER_PRCTL_READBACK_REQUIRED')
    try:found,_,_=os.wait4(-1,os.WNOHANG)
    except ChildProcessError as e:need(e.errno==errno.ECHILD,'CARRIER_ACTUAL_ECHILD_ERRNO_REQUIRED')
    else:raise ValueError('CARRIER_OWN_CHILD_PRESENT:'+str(found))
    need(libc.prctl(37,ctypes.byref(after),0,0,0)==0 and before.value==after.value,'CARRIER_SUBREAPER_CHANGED')
    return {'pid':os.getpid(),'owner_uid':os.geteuid(),'actual_ECHILD':True,'subreaper':before.value}
def mount(fd):
    with open('/proc/self/fdinfo/'+str(fd)) as stream:
        entries=[line.split(':',1)[1].strip() for line in stream if line.startswith('mnt_id:')]
    need(len(entries)==1 and entries[0].isdigit(),'ACTUAL_MOUNT_ID_REQUIRED');return int(entries[0])
def admission(root):
    root=safe_path(root);fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:i=os.fstat(fd);v=os.fstatvfs(fd);mid=mount(fd)
    finally:os.close(fd)
    need(i.st_uid==os.geteuid() and not stat.S_IMODE(i.st_mode)&0o022,'OWNED_NONWRITABLE_BY_OTHERS_ROOT_REQUIRED')
    with open('/proc/self/mountinfo') as stream:rows=[line.rstrip() for line in stream if line.split(' ',1)[0]==str(mid)]
    need(len(rows)==1,'ACTUAL_UNIQUE_MOUNT_REQUIRED');fs=rows[0].split(' - ',1)[1].split()[0]
    need(fs not in ('tmpfs','ramfs','devtmpfs'),'MATERIAL_GATE_REQUIRES_PHYSICAL_DISK_NOT_RAM')
    need(v.f_bavail*v.f_frsize>=2*1024**3 and v.f_files>0 and v.f_favail/v.f_files>=.1,'ORIGINAL_DISK_OR_INODE_FLOOR_NOT_MET')
    return {'root':str(root),'uid':i.st_uid,'device':i.st_dev,'mount_id':mid,'filesystem_type':fs,
        'free_bytes':v.f_bavail*v.f_frsize,'free_inodes':v.f_favail,'total_inodes':v.f_files,
        'original_free_floor_bytes':2*1024**3,'original_min_free_inode_fraction':.1}
def clean_env(extra=None):
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','LANG','TZ') or k.startswith('LC_')}
    env.update(PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',GIT_OPTIONAL_LOCKS='0',GIT_TERMINAL_PROMPT='0')
    if extra:env.update(extra)
    return env
def git(root,*args):
    row=subprocess.run(['git','-C',str(root),*args],env=clean_env(),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,check=True)
    return row.stdout
def module_pin(root,member,sha):
    raw=read(root/member);tree=git(root,'ls-tree',sha,'--',member).decode().strip().split()
    need(len(tree)==4 and tree[0]=='100644' and tree[1]=='blob' and tree[3]==member,'EXACT_TRACKED_MODULE_REQUIRED')
    need(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==tree[2],'TRACKED_MODULE_BLOB_CHANGED')
    return {'path':member,'sha256':digest(raw),'git_blob':tree[2]}
def api(path):
    token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN');need(bool(token),'READONLY_GITHUB_TOKEN_REQUIRED')
    request=urllib.request.Request('https://api.github.com/repos/'+REPO+path,
        headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
    with urllib.request.urlopen(request,timeout=30) as response:wire=response.read(1024**2+1)
    need(len(wire)<=1024**2,'GITHUB_AUTHORITY_BOUND');return document(wire)
def authority(a):
    repo=api('');ref=api('/git/ref/heads/'+BRANCH);commit=api('/git/commits/'+a.source_sha);pr=api('/pulls/'+str(PR))
    need(repo['id']==REPO_ID and repo['full_name']==REPO and ref['object']['sha']==a.source_sha
        and commit['sha']==a.source_sha and commit['tree']['sha']==a.source_tree
        and pr['state']=='open' and pr['draft'] is True and pr['head']['sha']==a.source_sha
        and pr['head']['ref']==BRANCH and pr['head']['repo']['id']==REPO_ID
        and pr['user']['login']=='mbalbo2023' and pr['head']['repo']['owner']['login']=='mbalbo2023'
        and pr['base']['ref']=='deploy/rc6-pr69-isolated-20260915'
        and pr['base']['sha']=='da697c6e6c2274579f9e4a112fabc4327475dd35','FRESH_GITHUB_SOURCE_AUTHORITY_MISMATCH')
    need(re.fullmatch(r'https://github\.com/mbalbo2023/Porota-trading/issues/471#issuecomment-[0-9]+',a.launch_receipt_url),
        'DURABLE_SCOPED_LAUNCH_RECEIPT_REQUIRED')
    comment=api('/issues/comments/'+a.launch_receipt_url.rsplit('-',1)[1])
    need(re.fullmatch('[a-zA-Z0-9_.:/-]{1,160}',a.owner_session),'EXACT_LITERAL_SUCCESSOR_SESSION_REQUIRED')
    need(comment['html_url']==a.launch_receipt_url and comment['issue_url'].endswith('/issues/471')
        and comment['user']['login']=='mbalbo2023'
        and a.source_sha in comment['body'] and a.source_tree in comment['body'] and BRANCH in comment['body']
        and re.search(r'(?m)^SESSION_SUCCESSOR='+re.escape(a.owner_session)+r'\s*$',comment['body']),
        'LAUNCH_RECEIPT_EXACT_SOURCE_CONTEXT_REQUIRED')
    return {'repo_id':REPO_ID,'repo':REPO,'canonical_origin':ORIGIN,'source_truth':'FRESH_CANONICAL_GITHUB_COMMIT_TREE_REF_PR_READS',
        'branch':BRANCH,'sha':a.source_sha,'tree':a.source_tree,'PR':PR,'PR_state':'open','PR_draft':True,
        'read_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
        'preparation_receipt_url':a.launch_receipt_url,'durable_launch_receipt_url':a.launch_receipt_url,
        'SESSION_SUCCESSOR':a.owner_session,
        'workflow_path':'.github/workflows/rc6-unified-candidate-tests.yml','workflow_run_id':os.environ['GITHUB_RUN_ID'],
        'workflow_run_attempt':os.environ['GITHUB_RUN_ATTEMPT']}

class OwnedRunner:
    def __init__(self,manager,control_root):
        self.manager=manager;self.control=control_root;self.raw=control_root.parent/'command-raw'
        self.raw.mkdir(mode=0o700);self.unknown=False;self.closed_logs=[];self.fins={};self.sealed_groups=[]
        self.capacity_records=[];self.pending_capacity=[];self.command_labels=[]
    def __call__(self,argv,*,cwd,label,limit=300,env=None,namespace=None):
        need(re.fullmatch('[a-zA-Z0-9_.-]+',label),'LITERAL_COMMAND_LABEL_REQUIRED')
        need(type(limit) in (int,float) and math.isfinite(limit) and 0<limit<=21600,'DECLARED_FINITE_COMMAND_BOUND_REQUIRED')
        self.manager['pre_capture_kernel_state']();log=self.raw/(label+'.log')
        launch_ns=time.time_ns();proofs=self.pending_capacity;self.pending_capacity=[]
        need(all(0<=launch_ns-item['measured_at_unix_ns']<=60*10**9 for item in proofs),
            'PRODUCER_CAPACITY_PROOF_STALE_BEFORE_ACTUAL_LAUNCH')
        save(self.control/(label+'.launch-intent.json'),{'argv':argv,'owner_uid':os.geteuid(),'parent_pid':os.getpid(),
            'scope':'PREPARATION_OR_DECLARED_SUPPLEMENTARY_GATE','management_seconds':limit,
            'intent_recorded_unix_ns':launch_ns,'capacity_launch_proofs':[item['label'] for item in proofs]})
        last=[0.0]
        def progress(stage,pid,entered,deadline,fd):
            now=time.monotonic()
            if stage=='started' or now-last[0]>=40:
                last[0]=now;print(json.dumps({'label':label,'stage':stage,'actual_pid':pid,
                    'wall_seconds':now-entered,'scope':'OWN_FSTAT_ONLY_NO_ACTIVE_PAYLOAD_READ'}),flush=True)
        self.unknown=True
        if namespace is None:
            kernel=self.manager['managed_native_child'](argv,cwd,log,clean_env(env),limit,progress=progress)
        else:
            native_log=label+'.native.log'
            kernel,fin=fixture_lifecycle.execute_owned(namespace,argv,cwd=cwd,environ=clean_env(env),
                log_relative=native_log,timeout_seconds=limit,fin_label=label)
            self.fins[namespace.nonce]=fin
            log=namespace.path/native_log
        save(self.control/(label+'.kernel.json'),kernel)
        self.command_labels.append(label)
        need(self.manager['managed_custody_closed'](kernel),'COMMAND_FIN_UNKNOWN_PAYLOAD_VETO')
        need(not kernel['timed_out'] and not kernel['supervisor_errors'] and not kernel['owned_group_signal_observations']
            and not kernel['kernel_wait4_zero_observed_irreversible_red'] and not kernel['late_observed_main_reap_irreversible_red']
            and not kernel['residual_descendants_observed']
            and kernel['launcher_management_deadline_seconds']==limit
            and kernel['owned_cleanup_management_bound_seconds']==5
            and type(kernel['termination_reap_restore_cleanup_seconds']) in (int,float)
            and math.isfinite(kernel['termination_reap_restore_cleanup_seconds'])
            and 0<=kernel['termination_reap_restore_cleanup_seconds']<=5
            and type(kernel['actual_launch_to_pid_reap_wall_seconds']) in (int,float)
            and math.isfinite(kernel['actual_launch_to_pid_reap_wall_seconds'])
            and 0<=kernel['actual_launch_to_pid_reap_wall_seconds']<=limit
            and all(type(x.get('exit_code')) is int and x['exit_code']==0 for x in kernel['adopted_descendants_reaped']),
            'COMMAND_INFRASTRUCTURE_RED_PAYLOAD_VETO')
        self.manager['pre_capture_kernel_state']()
        self.unknown=False;self.closed_logs.append(log)
        return {'returncode':kernel['returncode'],'kernel':kernel,'log_path':str(log)}


def readmit_automatic_pr(a,run,stage):
    if not a.require_pr_admission:return
    module_pin(a.repo_root,'scripts/rc6_material_pr_admission.py',a.source_sha)
    controller=runpy.run_path(str(a.repo_root/'scripts/rc6_material_pr_admission.py'))
    row=controller['admit'](source_sha=a.source_sha,source_tree=a.source_tree,
        launch_receipt_url=a.launch_receipt_url,owner_session=a.owner_session,gate=a.gate)
    a.capacity_peaks=row['capacity_peaks'];a.cheap_files=row['cheap_files'];a.prerequisites_manifest=row['prerequisites_manifest']
    save(run.control/('automatic-pr-'+stage+'.json'),row)
    return row

def installed_env(a,run,root):
    interpreters={}
    for epoch,base in (('311',a.python311),('312',a.python312)):
        preparation_capacity(a,run,root,'before-venv'+epoch)
        private=root/('product'+epoch);private.mkdir(mode=0o700);venv_root=private/'venv'
        temporary=private/'bootstrap-temp';temporary.mkdir(mode=0o700)
        prep_env={'TMPDIR':str(temporary),'TEMP':str(temporary),'TMP':str(temporary)}
        preparation_capacity(a,run,root,'before-venv'+epoch+'-temporary',
            additional_storage=(('bootstrap_temporary',temporary),))
        row=run([base,'-I','-B',str(a.repo_root/'scripts/rc6_material_environment.py'),'--root',str(venv_root),
            '--owner-uid',str(os.geteuid()),'--python-version',VERSIONS[epoch],'--receipt',str(private/'creation.json')],
            cwd=root,label='venv'+epoch,limit=300,env=prep_env);need(row['returncode']==0,'PRODUCT_VENV_CONSTRUCTION_FAILED')
        py=str(venv_root/'bin/python')
        steps=[['--require-hashes','--only-binary=:all:','-r',str(a.repo_root/'requirements.build.lock.txt')],
               ['--require-hashes','--only-binary=:all:','--no-binary=msgpack,ppi-client,signalrcoreppi,ta',
                '--no-build-isolation','-r',str(a.repo_root/'requirements.lock.txt')]]
        for n,args in enumerate(steps):
            preparation_capacity(a,run,root,'before-locked'+epoch+'-'+str(n),
                additional_storage=(('pip_temporary',temporary),))
            row=run([py,'-I','-B','-m','pip','install','--disable-pip-version-check','--no-cache-dir',*args],cwd=root,
                label='locked'+epoch+'-'+str(n),limit=1800,env=prep_env)
            need(row['returncode']==0,'HASHLOCKED_PRODUCT_INSTALL_FAILED')
        row=run([py,'-I','-B','-m','pip','check'],cwd=root,label='pipcheck'+epoch,limit=300)
        need(row['returncode']==0,'PRODUCT_PIP_CHECK_FAILED');interpreters[epoch]=py
    return interpreters

def fetch_originals(a,run,root):
    wire=read(a.repo_root/OBJECTS_MEMBER);need(digest(wire)==OBJECTS_SHA,'ORIGINAL19_BODY_CHANGED')
    objects=document(wire)['objects'];need(len(objects)==len({x['sha'] for x in objects})==19,'EXACT19_REQUIRED')
    askpass=root/'git-askpass.py';save_raw(askpass,b'#!/usr/bin/env python3\nimport os,sys\nprint("x-access-token" if "username" in sys.argv[1].lower() else os.environ["RC6_READONLY_GIT_TOKEN"])\n')
    # This newly exclusive executable has never been an existing resource.
    fd=os.open(askpass,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:os.fchmod(fd,0o700)
    finally:os.close(fd)
    token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    for n,row in enumerate(objects):
        exists=subprocess.run(['git','-C',str(a.repo_root),'cat-file','-e',row['sha']+'^{commit}'],
            env=clean_env(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
        if not exists:
            preparation_capacity(a,run,root,'before-original-fetch-'+str(n))
            result=run(['git','-C',str(a.repo_root),'-c','credential.helper=','fetch','--no-tags','--no-write-fetch-head',
                '--no-auto-maintenance',ORIGIN,row['sha']],cwd=root,label='original-fetch-'+str(n),limit=300,
                env={'GIT_ASKPASS':str(askpass),'RC6_READONLY_GIT_TOKEN':token})
            need(result['returncode']==0,'ORIGINAL_LITERAL_FETCH_FAILED')
        raw=git(a.repo_root,'cat-file','commit',row['sha'])
        need(digest(raw)==row['raw_commit_sha256'] and hashlib.sha1(b'commit '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==row['sha']
            and raw.splitlines()[0]==b'tree '+row['github_tree'].encode(),'ORIGINAL19_COMMIT_TREE_CHANGED')
    return objects

def prepare_gov(a,run,root,interpreters,auth,manager_pin,native_pin):
    preparation_capacity(a,run,root,'before-fullGit-original-inputs')
    derives=runpy.run_path(str(a.repo_root/'scripts/rc6_material_derive.py'))
    derived=derives['prepare'](a.repo_root,root/'drivers',python311=interpreters['311'],python312=interpreters['312'],
        owner_uid=os.geteuid(),read=read,save_raw=save_raw)
    save(root/'driver-derivations.json',derived);authority_path=root/'source-authority.json';authority_sha=save(authority_path,auth)
    binding={'stage':'FINAL_GITHUB_SOURCE_PREPARATION_AUTHORIZED_ONLY','repo_id':REPO_ID,'PAPER_SHADOW_ONLY':True,
        'real_orders_sent':0,'DEPLOY_OWNER':'NOT_ACQUIRED','preparation_receipt_url':a.launch_receipt_url,
        'source_sha':a.source_sha,'source_tree':a.source_tree,'GitHub_authority_file':str(authority_path),
        'GitHub_authority_sha256':authority_sha,'manager_source_root':str(a.repo_root),'manager':manager_pin,
        'native_runner':native_pin,'new_namespace_parent':str(root),'clone_transport':str(a.repo_root),
        'original_object_transport':str(a.repo_root),'builder_sha256':derived['builder_sha256']}
    preparation=root/'builder-binding.json';save(preparation,binding)
    row=run([interpreters['311'],'-I','-B',derived['builder_path'],'--preparation-binding',str(preparation)],
        cwd=root,label='fullGit-original-inputs',limit=1800);need(row['returncode']==0,'FULL_GIT_SOURCE_PREPARATION_FAILED')
    # One JSON terminal line is emitted by the builder after its actual child FIN.
    lines=read(Path(row['log_path'])).splitlines();documents=[]
    for line in lines:
        try:item=document(line)
        except (ValueError,UnicodeError):continue
        if isinstance(item,dict) and 'prepared' in item:documents.append(item)
    need(len(documents)==1,'ACTUAL_PREPARATION_RESULT_REQUIRED')
    return derived,documents[0]['prepared'],authority_path,authority_sha

def static_admission(a,run,root,prepared,interpreters):
    started=a.preparation_started_utc;evidence={};native=[];heavy_corpora={}
    g=runpy.run_path(str(a.repo_root/'scripts/rc6_controlled_governed_runner.py'))
    arguments,exclusions,inventory=g['approved_scope'](a.repo_root,a.source_sha)
    fip=inventory
    need(fip['candidate_sha']==a.source_sha and fip['candidate_tree']==a.source_tree
        and fip['software_status']=='INVENTORY_NOT_EXECUTED','G0_CLOSURE_INVENTORY_REBOUND')
    save(root/'G0.closure-matrix.json',fip)
    save(root/'G0.inventory.json',{'source_sha':a.source_sha,'source_tree':a.source_tree,
        'automatic_discovery_arguments':arguments,'original_governed_exclusions':exclusions,
        'inventory':{'source_union_paths':inventory['source_union_paths'],
            'requirements':len(inventory['requirements']),'scenarios':len(inventory['scenarios']),
            'software_status':inventory['software_status']}})
    for epoch in ('311','312'):
        source=Path(next(item for item in prepared if item['epoch']==epoch)['source_root'])
        preparation_capacity(a,run,root,'before-G0-full-Git-fsck'+epoch)
        check=run(['git','--no-replace-objects','-C',str(source),'fsck','--full','--strict','--no-reflogs'],
            cwd=root,label='G0-fsck'+epoch,limit=1800)
        need(check['returncode']==0,'G0_FULL_GIT_FSCK_FAILED')
        producer='full-gov'+epoch
        namespace=fixture_lifecycle.create_namespace(Path.home(),capacity_binding(a,producer))
        output=namespace.path/'admission';output.mkdir(mode=0o700)
        binding,peak,live=heavy_preflight(a,run,namespace.path,producer,'G0-'+epoch)
        env={'RC6_GOV_AUTHENTICATED_ROOT_JSON':json.dumps(fixture_lifecycle.namespace_receipt(namespace),separators=(',',':')),
            'RC6_GOV_CAPACITY_BINDING_JSON':json.dumps(binding,separators=(',',':')),
            'RC6_GOV_COMPARABLE_PEAK_JSON':json.dumps(peak,separators=(',',':'))}
        command=[interpreters[epoch],'-I','-B',str(source/'scripts/rc6_material_focal.py'),
            '--repo-root',str(source),'--source-sha',a.source_sha,'--source-tree',a.source_tree,
            '--output-root',str(output),'--phase','collection','--stage','admission']
        row=run(command,cwd=source,label='G0-admission'+epoch,limit=5400,env=env,namespace=namespace)
        final=document(read(output/'collection.child-finalization.json'))
        need(final['status']=='GREEN' and final['kernel_echild_before_phase_return'] is True,
            'G0_NATIVE_ADMISSION_FIN_UNKNOWN')
        report=document(read(output/'collection.observations.json'))
        heavy_corpora[epoch]=architectural.validate_preserved_heavy_corpus(report.get('preserved_heavy_corpus'))
        need(row['returncode']==report['native_exit_code']==0 and report['compiled_product_files']>0
            and report['source_namespace_exact_before_after'] is True and not report['inet_socket_attempts']
            and report['closure_before_fixture']['installed_total']==157,
            'G0_COMPILE_IMPORT_SOURCE_OR_CLOSURE_RED')
        required=[path.relative_to(namespace.path).as_posix() for path in sorted(output.iterdir()) if path.is_file()]
        required.append('G0-admission'+epoch+'.native.log')
        sealed,cleanup=seal_generated(a,run,namespace,root,'G0-'+epoch,required)
        evidence['admission'+epoch]=captured_reference(sealed,'admission/collection.observations.json')
        evidence['admission'+epoch+'_kernel']=captured_reference(sealed,'producer-owned-fin-G0-admission'+epoch+'.json')
        native.append({'epoch':epoch,'compiled':report['compiled_product_files'],
            'closure_before_fixture':report['closure_before_fixture'],'cleanup':cleanup,'capacity_live':live})
    policy=read(a.repo_root/'ops/policy/porota-policy.yaml').decode()
    need(all(literal in policy for literal in ('required_mode: PRODUCTION_PAPER','real_trading_allowed: false',
        'real_orders_sent_required: 0','rollback_allowed: false')),'G0_PAPER_POLICY_INVARIANT_CHANGED')
    changed=git(a.repo_root,'diff','--name-only','da697c6e6c2274579f9e4a112fabc4327475dd35',a.source_sha).decode().splitlines()
    need(not any(re.search(r'(^|/)ppi[-_]watch(/|\.|$)',path,re.IGNORECASE) for path in changed),'G0_PPI_WATCH_SOURCE_CHANGED')
    evidence['inventory']={'path':'carrier/G0.inventory.json','sha256':digest(read(root/'G0.inventory.json'))}
    evidence['FIP']={'path':'carrier/G0.closure-matrix.json','sha256':digest(read(root/'G0.closure-matrix.json'))}
    commands=[]
    for label in run.command_labels:
        intent=run.control/(label+'.launch-intent.json');kernel=run.control/(label+'.kernel.json')
        commands.append({'label':label,
            'intent':{'path':'controls/'+intent.name,'sha256':digest(read(intent))},
            'kernel':{'path':'controls/'+kernel.name,'sha256':digest(read(kernel))}})
    capacity_index={'schema':'rc6.G0-actual-capacity-launch-index.v1','source_sha':a.source_sha,
        'source_tree':a.source_tree,'capacity_records':run.capacity_records,'commands':commands,
        'preparation_scope':a.capacity_peaks['bootstrap']['preparation_scope'],
        'full_git_transition':{'path':'controls/fullGit-bootstrap-to-qualified.json',
            'sha256':digest(read(run.control/'fullGit-bootstrap-to-qualified.json'))}}
    save(root/'G0.capacity-index.json',capacity_index)
    evidence['capacity_index']={'path':'carrier/G0.capacity-index.json','sha256':digest(read(root/'G0.capacity-index.json'))}
    checks={name:True for name in ('ownership','inventory','closure_matrix','compile_import','paper_invariants',
        'ppi_watch_invariant','full_git_fsck','full_source','capacity_preflight')}
    row=architectural.receipt_base('G0',source_sha=a.source_sha,source_tree=a.source_tree,
        read_contract_sha256=architectural.productive_contract(a.repo_root),started_utc=started,checks=checks,
        native_evidence=evidence,static_native_receipts=native,preserved_heavy_corpora=heavy_corpora)
    save(root/'G0.receipt.json',row)
    return row

def preserve_controls(run,prefix,paths):
    """Bounded raw control files only; no Source, result, observations or logs."""
    for path in paths:
        path=Path(path)
        if not os.path.lexists(path):continue
        try:wire=read(path,256*1024)
        except BaseException as error:
            info=path.lstat()
            save(run.control/(prefix+'-'+path.name+'.unread-control.json'),{
                'status':'RED','reason':'OWNED_CONTROL_UNAVAILABLE_WITHIN_ORIGINAL_256KiB',
                'path':str(path),'lstat':attrs(info),'class':type(error).__name__,'payload_reads':0})
            raise
        save_raw(run.control/(prefix+'-'+path.name),wire)

def run_gov(a,run,root,prepared,derived,auth_path,auth_sha):
    """The public original native controller owns both full-root phases.

    Its exact FIN, Source/closure and JUnit controls are verified before RAW,
    independently of the logical returncode. No historical wrapper overlay.
    """
    epoch=a.gate[-3:];started=architectural.utc()
    source=Path(next(item for item in prepared if item['epoch']==epoch)['source_root'])
    namespace=fixture_lifecycle.create_namespace(Path.home(),capacity_binding(a,a.gate))
    output=namespace.path/'native-gov'
    binding,peak,live=heavy_preflight(a,run,namespace.path,a.gate,'fullGov'+epoch)
    env={'RC6_GOV_AUTHENTICATED_ROOT_JSON':json.dumps(fixture_lifecycle.namespace_receipt(namespace),separators=(',',':')),
        'RC6_GOV_CAPACITY_BINDING_JSON':json.dumps(binding,separators=(',',':')),
        'RC6_GOV_COMPARABLE_PEAK_JSON':json.dumps(peak,separators=(',',':'))}
    interpreter=document(read(Path(next(item for item in prepared if item['epoch']==epoch)['blocked_binding'])))['interpreter']
    command=[interpreter,'-I','-B',str(source/'scripts/rc6_controlled_governed_runner.py'),
        '--repo-root',str(source),'--source-sha',a.source_sha,'--source-tree',a.source_tree,
        '--output-root',str(output),'--timeout-seconds','5400']
    row=run(command,cwd=source,label='fullGov'+epoch,limit=21600,env=env,namespace=namespace)
    w=runpy.run_path(str(root/'drivers/governed_outer.py'));phases={}
    for phase in ('collection','execution'):
        kernel_path=output/(phase+'.kernel.json')
        if not kernel_path.exists():continue
        kernel=document(read(kernel_path,256*1024))
        final=document(read(output/(phase+'.child-finalization.json'),256*1024))
        need(w['native_kernel_closed'](kernel) and w['native_finalization_closed'](final)
            and kernel['supervisor_pid']==row['kernel']['pid']
            and kernel['phase_acceptance_deadline_seconds']==5400
            and kernel['owned_cleanup_management_bound_seconds']==5,
            'FULL_GOV_ACTUAL_NATIVE_PHASE_FIN_NOT_CLOSED')
        phases[phase]={'kernel':kernel,'finalization':final}
    need(phases and run.manager['managed_custody_closed'](row['kernel']),
        'FULL_GOV_NATIVE_PHASE_UNKNOWN_PAYLOAD_VETO')
    code=0 if row['returncode']==0 else 1;reason=None;counts={'cases':0,'failure':0,'error':0,'skipped':0};identity=False
    try:
        collection=document(read(output/'collection.observations.json'))
        execution=document(read(output/'execution.observations.json'))
        counts=architectural.validate_junit(collection,execution,read(output/'porota-governed-tests.xml'),require_green=False)
        identity=True
        need(all(node['source_namespace_exact_before_after'] is True
            and node['source_sha']==a.source_sha and node['source_tree']==a.source_tree
            and not node['inet_socket_attempts'] and not node['unexpected_product_imports']
            and node['closure_before_fixture']['installed_total']==157 for node in (collection,execution)),
            'FULL_GOV_NATIVE_SOURCE_CLOSURE_OR_OFFLINE_RED')
        need(not any(counts[key] for key in ('failure','error','skipped'))
            and collection['pytest_exit_code']==execution['pytest_exit_code']==0,'FULL_GOV_LOGICAL_RED')
        actual_nodes={node['nodeid'] for node in collection['items']}
        g0=next(prior for prior in a.verified_prerequisites['authenticated_receipts'] if prior['gate']=='G0')
        required_heavy=g0['preserved_heavy_corpora'][epoch]
        architectural.verify_preserved_heavy_coverage(required_heavy,collection,execution,
            read(output/'porota-governed-tests.xml'))
        for prior in a.verified_prerequisites['authenticated_receipts']:
            if prior['gate'] in ('G2','G3'):
                need(all(node['nodeid'] in actual_nodes for node in prior.get('complete_focal_corpus',[]))
                    and prior.get('complete_focal_corpus'),
                    'FULL_GOV_MISSING_ORIGINAL_DEFERRED_MATERIAL_NODE')
        proof=document(read(output/'porota-governed-tests.json'))
        need(proof['status']=='GREEN' and proof['discovered']==proof['executed']==counts['cases']
            and proof['junit_sha256']==counts['junit_sha256'],'FULL_GOV_NATIVE_PROOF_REBOUND')
        verifier=runpy.run_path(str(source/'scripts/rc6_convergence_provenance.py'))
        fip=verifier['verify'](source,a.source_sha,verifier['capture_junit'](output/'porota-governed-tests.xml'),fetch_source_refs=False)
        save(output/'native-guard-cohorts-FIP.json',fip)
    except (ValueError,OSError,KeyError,ET.ParseError) as error:
        reason=str(error).partition(':')[0];code=1
    required=[path.relative_to(namespace.path).as_posix() for path in sorted(output.iterdir())
        if path.is_file() and path.suffix in ('.json','.xml','.log','.py')]
    for phase in ('collection','execution'):
        required.extend(phase_diagnostic_controls(namespace,output,phase))
    required.append('fullGov'+epoch+'.native.log')
    sealed,cleanup=seal_generated(a,run,namespace,root,'fullGov'+epoch,required)
    native_evidence={role:captured_reference(sealed,'native-gov/'+filename) for role,filename in (
        ('collection','collection.observations.json'),('execution','execution.observations.json'),
        ('junit','porota-governed-tests.xml'),('governed','porota-governed-tests.json'),
        ('FIP','native-guard-cohorts-FIP.json'),('native_execution','governed-execution.receipt.json'),
        ('records_before','installed-records-before.json'),('records_after','installed-records-after.json'),
        ('source_before','source-before.index.json'),('source_after','source-after.index.json'),
        ('collection_kernel','collection.kernel.json'),
        ('execution_kernel','execution.kernel.json'),('collection_fin','collection.child-finalization.json'),
        ('execution_fin','execution.child-finalization.json')) if captured_file(sealed,'native-gov/'+filename,required=False) is not None}
    checks={'native_test_green':code==0,'node_identity':identity,'full_source':code==0,
        'closure_matrix':code==0,'native_original_FIN_closed':True,'capacity_live':live['capacity']['status']=='GREEN',
        'authenticated_cleanup':cleanup['namespace_removed'] is True,'original_deferred_union_coverage':code==0}
    receipt=architectural.receipt_base('G6.'+epoch,source_sha=a.source_sha,source_tree=a.source_tree,
        read_contract_sha256=architectural.productive_contract(a.repo_root),started_utc=started,checks=checks,
        native_exit_code=code,native_evidence=native_evidence,python_epoch=epoch,test_cases=counts['cases'],
        required_preserved_heavy_corpus=next(prior for prior in a.verified_prerequisites['authenticated_receipts']
            if prior['gate']=='G0')['preserved_heavy_corpora'][epoch],
        identity_verified=identity,failures=counts['failure'],errors=counts['error'],skipped=counts['skipped'],xfail=0,
        scope='repository-root automatic pytest discovery',cleanup_receipt=cleanup,reason=reason,
        source_unchanged=code==0)
    save(root/('G6.'+epoch+'.receipt.json'),receipt)
    return code,{'gate':a.gate,'native_phases':phases,'receipt':receipt,'reason':reason,
        'raw_root':str(sealed),'whole_Gov_claim':code==0,'native_FIN_payload_safe':True}

def seal_generated(a,run,namespace,root,label,required_paths):
    fin=run.fins.get(namespace.nonce)
    need(fin is not None,'ACTUAL_OWNED_GENERATED_FIN_REQUIRED')
    destination=root/(label+'-sealed')
    captured=fixture_lifecycle.capture_required_evidence(namespace,fin,destination,required_paths)
    cleanup=fixture_lifecycle.cleanup_namespace(namespace,fin,captured)
    save(run.control/(label+'.scoped-cleanup.json'),cleanup)
    run.sealed_groups.append((label+'-sealed',destination))
    for path in sorted(destination.iterdir()):
        if path.is_dir():run.sealed_groups.append((label+'-sealed-'+path.name,path))
    return destination,cleanup

def captured_file(destination,relative,*,required=True):
    manifest=document(read(destination/'manifest.json',4*1024**2))
    matches=[row for row in manifest['files'] if row['relative_source']==relative]
    if not required and not matches:return None
    need(len(matches)==1,'SEALED_RAW_LITERAL_PATH_MISSING_OR_DUPLICATE')
    row=matches[0];path=destination/row['capture_file']
    need(re.fullmatch(r'[0-9]{4}\.raw',row['capture_file']) and digest(read(path))==row['sha256'],
        'SEALED_RAW_BYTES_OR_LITERAL_PATH_REBOUND')
    return path

def captured_reference(destination,relative):
    path=captured_file(destination,relative)
    return {'path':destination.name+'/'+path.name,'sha256':digest(read(path))}


def focal(a,run,root,prepared,interpreters):
    w=runpy.run_path(str(root/'drivers/governed_outer.py'));summaries=[]
    epochs=('311','312') if a.gate=='cheap' else (a.gate[-3:],)
    for epoch in epochs:
        started=architectural.utc();producer='focal'+epoch
        item=next(x for x in prepared if x['epoch']==epoch);source=Path(item['source_root'])
        g=runpy.run_path(str(source/'scripts/rc6_controlled_governed_runner.py'))
        before=g['source_pin'](source,a.source_sha,a.source_tree)
        save_raw(root/('focal'+epoch+'.parent-source-before.json'),g['canonical'](before))
        phases={};sealed_phases={};cleanups=[];reason=None;code=0;live=[]
        for phase in ('collection','execution'):
            namespace=fixture_lifecycle.create_namespace(Path.home(),capacity_binding(a,producer))
            output=namespace.path/'focal';output.mkdir(mode=0o700)
            readmit_automatic_pr(a,run,'focal'+epoch+'-'+phase+'-prelaunch')
            binding,peak,capacity_live=heavy_preflight(a,run,namespace.path,producer,'focal'+epoch+'-'+phase)
            live.append(capacity_live)
            env={'RC6_GOV_AUTHENTICATED_ROOT_JSON':json.dumps(fixture_lifecycle.namespace_receipt(namespace),separators=(',',':')),
                'RC6_GOV_CAPACITY_BINDING_JSON':json.dumps(binding,separators=(',',':')),
                'RC6_GOV_COMPARABLE_PEAK_JSON':json.dumps(peak,separators=(',',':'))}
            if a.gate=='cheap' and phase=='execution':
                env['RC6_SCOPED_INFRASTRUCTURE_PROBE_DIR']=str(output/'execution-scoped-infrastructure-probe')
            command=[interpreters[epoch],'-I','-B',str(source/'scripts/rc6_material_focal.py'),'--repo-root',str(source),
                '--source-sha',a.source_sha,'--source-tree',a.source_tree,'--output-root',str(output),'--phase',phase,
                '--stage','cheap' if a.gate=='cheap' else 'focal']
            if a.gate=='cheap':command+=['--cheap-files-json',json.dumps(a.cheap_files,separators=(',',':'))]
            row=run(command,cwd=source,label='focal'+epoch+'-'+phase,limit=5400,env=env,namespace=namespace)
            need(run.manager['managed_custody_closed'](row['kernel'])
                 and row['kernel']['launcher_management_deadline_seconds']==5400,
                 'FOCAL_ORIGINAL_MANAGER_KERNEL_NOT_CLOSED')
            final=document(read(output/(phase+'.child-finalization.json')))
            need(w['native_finalization_closed'](final),'FOCAL_FINALIZER_UNKNOWN_PAYLOAD_VETO')
            phases[phase]=document(read(output/(phase+'.observations.json')))
            need(not phases[phase]['inet_socket_attempts'] and not phases[phase]['unexpected_product_imports'],
                 'FOCAL_NATIVE_IMPORT_OR_NETWORK_RED')
            required=[path.relative_to(namespace.path).as_posix() for path in sorted(output.iterdir()) if path.is_file()]
            required.extend(phase_diagnostic_controls(namespace,output,phase))
            if a.gate=='cheap' and phase=='execution':
                probe=output/'execution-scoped-infrastructure-probe/probe.json'
                if probe.exists():required.append(probe.relative_to(namespace.path).as_posix())
            required.append('focal'+epoch+'-'+phase+'.native.log')
            sealed,cleanup=seal_generated(a,run,namespace,root,'focal'+epoch+'-'+phase,required)
            sealed_phases[phase]=sealed;cleanups.append(cleanup)
            if phase=='collection' and row['returncode']!=0:
                reason='FOCAL_COLLECTION_RED_EXECUTION_NOT_LAUNCHED';code=1;break
            code=max(code,row['returncode'])
        # The barrier above establishes physical FIN independently of logical
        # identity/pytest outcome. Preserve original RAW on logical RED.
        after=g['source_pin'](source,a.source_sha,a.source_tree);atime=g['compare_source'](before,after)
        save_raw(root/('focal'+epoch+'.parent-source-after.json'),g['canonical'](after))
        counts={'cases':0,'failure':0,'error':0,'skipped':0};identity=False
        if 'execution' in phases:
            xml=read(captured_file(sealed_phases['execution'],'focal/focal-tests.xml'),16*1024**2)
            try:
                need(phases['collection'].get('complete_corpus')==phases['execution'].get('complete_corpus')
                    and phases['collection'].get('deferred_material_nodes')==phases['execution'].get('deferred_material_nodes'),
                    'FOCAL_STAGE_CORPUS_OR_DEFERRED_LEDGER_MISMATCH')
                counts=architectural.validate_junit(phases['collection'],phases['execution'],xml,require_green=False)
                identity=True
            except (ValueError,ET.ParseError) as error:
                reason=str(error).partition(':')[0];code=1
            if any(counts[name] for name in ('failure','error','skipped')) or phases['execution']['pytest_exit_code']!=0:code=1
        native_evidence={}
        for role,phase,filename in [('collection','collection','collection.observations.json'),
                ('execution','execution','execution.observations.json'),('junit','execution','focal-tests.xml')]:
            if phase in sealed_phases and captured_file(sealed_phases[phase],'focal/'+filename,required=False) is not None:
                native_evidence[role]=captured_reference(sealed_phases[phase],'focal/'+filename)
        for phase,destination in sealed_phases.items():
            native_evidence[phase+'_kernel']=captured_reference(destination,
                'producer-owned-fin-focal'+epoch+'-'+phase+'.json')
        probe_green=True
        if a.gate=='cheap':
            destination=sealed_phases.get('execution');probe_green=False
            if destination is not None:
                probe_member='focal/execution-scoped-infrastructure-probe/probe.json'
                if captured_file(destination,probe_member,required=False) is not None:
                    native_evidence['scoped_infrastructure_probe']=captured_reference(destination,probe_member)
                    for role,member in (('scoped_probe_diagnostics_archive','focal/execution.diagnostics.raw'),
                            ('scoped_probe_diagnostics_index','focal/execution.diagnostics-index.json')):
                        if captured_file(destination,member,required=False) is not None:
                            native_evidence[role]=captured_reference(destination,member)
                    try:
                        probe_green=architectural.verify_scoped_infrastructure_probe(
                            document(read(captured_file(destination,probe_member))),
                            {'gate':'G1.'+epoch,'python_epoch':epoch,'source_sha':a.source_sha,'source_tree':a.source_tree,
                             'run_id':int(os.environ['GITHUB_RUN_ID']),'run_attempt':int(os.environ['GITHUB_RUN_ATTEMPT'])},
                            diagnostics_raw=read(captured_file(destination,'focal/execution.diagnostics.raw')),
                            diagnostics_index=document(read(captured_file(destination,'focal/execution.diagnostics-index.json'))))
                    except (ValueError,OSError,KeyError,TypeError,zipfile.BadZipFile) as error:
                        reason=str(error).partition(':')[0];code=1
            if not probe_green:
                code=1;reason=reason or 'G1_ACTUAL_SCOPED_INFRASTRUCTURE_PROBE_MISSING_OR_BLOCKED'
        gate='G1.'+epoch if a.gate=='cheap' else ('G2' if epoch=='311' else 'G3')
        checks={'native_test_green':code==0,'node_identity':identity,'source_unchanged':True,
            'capacity_live':all(receipt['capacity']['status']=='GREEN' for receipt in live),
            'authenticated_cleanup':all(cleanup['namespace_removed'] is True for cleanup in cleanups),
            'original_FIN_closed':True,'offline':True}
        if a.gate=='cheap':checks['scoped_infrastructure_positive_probe']=probe_green
        receipt=architectural.receipt_base(gate,source_sha=a.source_sha,source_tree=a.source_tree,
            read_contract_sha256=architectural.productive_contract(a.repo_root),started_utc=started,checks=checks,
            native_exit_code=code,native_evidence=native_evidence,python_epoch=epoch,test_cases=counts['cases'],
            identity_verified=identity,failures=counts['failure'],errors=counts['error'],skipped=counts['skipped'],xfail=0,
            deferred_material_nodes=phases.get('collection',{}).get('deferred_material_nodes',[]),
            complete_focal_corpus=phases.get('collection',{}).get('complete_corpus',[]),
            deferred_nodes_claimed_executed=False,final_G6_union_coverage_required=True,
            cleanup_receipts=cleanups,reason=reason)
        save(root/(gate+'.receipt.json'),receipt)
        summaries.append({'epoch':epoch,'executed':counts['cases'],'counts':counts,'reason':reason,
            'junit_sha256':counts.get('junit_sha256'),'raw_root':str(sealed_phases.get('execution',sealed_phases['collection'])),
            'original_Source10_unchanged':True,'CODE_atime_observed':atime,'whole_Gov_claim':False,
            'scoped_cleanup':cleanups,'architectural_receipt':receipt})
        if code:return 1,{'gate':a.gate,'epochs':summaries,'scope':'CLOSED_FOCAL_LOGICAL_RED_NOT_WHOLE_GOV',
            'native_FIN_payload_safe':True}
    return 0,{'gate':a.gate,'epochs':summaries,'scope':'FOCAL_ONLY_NOT_WHOLE_GOV_NO_ARTIFACT_RUNTIME_CLAIM',
        'native_FIN_payload_safe':True}

def exported(a,run,root,prepared,interpreters):
    namespace=fixture_lifecycle.create_namespace(Path.home(),capacity_binding(a,a.gate))
    epoch=namespace.path
    heavy_preflight(a,run,epoch,a.gate,'material-export')
    repo=Path(next(x for x in prepared if x['epoch']=='311')['source_root'])
    # Original exporter creates source700 with Git644/755 files; full index, tar and raw commit remain separate.
    row=run([interpreters['311'],'-I','-B',str(repo/EXPORT_MEMBER),'--repo',str(repo),'--sha',a.source_sha,
        '--source',str(epoch/'source'),'--raw',str(epoch/'pin')],cwd=root,label='wholeGit-export',limit=300,
        namespace=namespace)
    need(row['returncode']==0,'WHOLE_GIT_EXPORT_FAILED')
    return namespace,epoch,repo

def material_evidence(namespace,groups):
    """Select producer controls/RAW only, never Source or fixture databases."""
    paths=[]
    for group in groups:
        if not group.exists():continue
        need(not group.is_symlink() and group.is_relative_to(namespace.path),'MATERIAL_RAW_NAMESPACE_REBOUND')
        for path in sorted(group.rglob('*')):
            need(not path.is_symlink(),'MATERIAL_RAW_ALIAS_FORBIDDEN')
            if path.is_file():paths.append(path.relative_to(namespace.path).as_posix())
    paths.extend(path.relative_to(namespace.path).as_posix() for path in sorted(namespace.path.glob('*.native.log')))
    return sorted(set(paths))

def phase_diagnostic_controls(namespace,output,phase):
    """Original lease/fixture controls and opted-in RAW, excluding fixtures."""
    selected=material_evidence(namespace,[output/(phase+'-fixture-lifecycle-controls'),
        output/(phase+'-scoped-infrastructure-probe'),
        output/(phase+'-private')/'porota-predeploy-evidence'/'issue465-stress'])
    temporary=namespace.path/('c' if phase=='collection' else 'e')
    if temporary.exists():
        count=0
        for parent,dirs,files in os.walk(temporary,followlinks=False):
            count+=len(dirs)+len(files)
            need(count<=100000,'PHASE_CONTROL_SCAN_RETENTION_LIMIT_EXCEEDED')
            if not re.fullmatch(r'readonly-complete-source-controls[0-9]+',Path(parent).name):continue
            for name in files:
                if re.fullmatch(r'lease-[0-9]+\.json',name):
                    path=Path(parent)/name
                    need(not path.is_symlink(),'PHASE_SOURCE10_LEASE_CONTROL_ALIAS')
                    selected.append(path.relative_to(namespace.path).as_posix())
    selected=sorted(set(selected))
    if not selected:return []
    return pack_phase_diagnostics(namespace,output,phase,selected)

def pack_phase_diagnostics(namespace,output,phase,selected):
    """Bounded lossless packing prevents one artifact member per fixture."""
    need(type(selected) is list and len(selected)==len(set(selected))<=100000,
        'DIAGNOSTIC_PACK_MEMBER_INVENTORY_INVALID')
    archive=output/(phase+'.diagnostics.raw');index=output/(phase+'.diagnostics-index.json')
    need(not os.path.lexists(archive) and not os.path.lexists(index),'DIAGNOSTIC_PACK_MUST_BE_NEW')
    rows=[];total=0
    fd=os.open(archive,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    with os.fdopen(fd,'wb') as stream:
        with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_STORED,allowZip64=False) as packed:
            for relative in selected:
                parsed=Path(relative)
                need(type(relative) is str and not parsed.is_absolute() and '..' not in parsed.parts
                    and parsed.as_posix()==relative and '\\' not in relative,
                    'DIAGNOSTIC_PACK_UNSAFE_MEMBER')
                path=namespace.path/relative;wire=read(path,128*1024**2);total+=len(wire)
                need(total<=128*1024**2,'DIAGNOSTIC_PACK_UNCOMPRESSED_EVIDENCE_QUOTA')
                item=zipfile.ZipInfo(relative,date_time=(1980,1,1,0,0,0))
                item.create_system=3;item.external_attr=(stat.S_IFREG|0o600)<<16
                packed.writestr(item,wire)
                rows.append({'path':relative,'bytes':len(wire),'sha256':digest(wire),
                    'original_all11':attrs(path.lstat())})
        stream.flush();os.fsync(stream.fileno())
    record={'schema':'rc6.phase-diagnostics-lossless-pack.v1','phase':phase,'binding':namespace.binding,
        'scope':'OWN_AUTHENTICATED_OPT_IN_RAW_FIXTURE_CONTROLS_AND_SOURCE10_LEASES',
        'member_count':len(rows),'uncompressed_bytes':total,'archive_sha256':file_sha(archive),
        'original_bytes_rewritten':False,'Source_or_fixture_payloads_selected':False,'members':rows}
    save(index,record)
    verify_diagnostic_pack(archive,record)
    return [archive.relative_to(namespace.path).as_posix(),index.relative_to(namespace.path).as_posix()]

def verify_diagnostic_pack(archive,index):
    """Replay exact bytes/digests/counters/CRC; never extract artifact paths."""
    archive_digest=digest(archive) if type(archive) is bytes else file_sha(archive)
    need(type(index) is dict and index.get('schema')=='rc6.phase-diagnostics-lossless-pack.v1'
        and type(index.get('members')) is list and type(index.get('member_count')) is int
        and index['member_count']==len(index['members'])<=100000
        and index.get('archive_sha256')==archive_digest,'DIAGNOSTIC_PACK_INDEX_OR_DIGEST_REBOUND')
    with zipfile.ZipFile(io.BytesIO(archive) if type(archive) is bytes else archive) as packed:
        members=packed.infolist();expected=index['members'];names=set();total=0
        need(len(members)==len(expected),'DIAGNOSTIC_PACK_MEMBER_COUNT_MISMATCH')
        for member,row in zip(members,expected):
            name=member.filename;parsed=Path(name);mode=member.external_attr>>16
            need(name not in names and name==row.get('path') and not parsed.is_absolute()
                and '..' not in parsed.parts and '\\' not in name and parsed.as_posix()==name
                and not any(ord(character)<32 for character in name) and not member.flag_bits&1
                and stat.S_ISREG(mode) and not member.is_dir(),'DIAGNOSTIC_PACK_UNSAFE_OR_REBOUND_MEMBER')
            names.add(name);total+=member.file_size
            need(total<=128*1024**2 and type(row.get('bytes')) is int and member.file_size==row['bytes'],
                'DIAGNOSTIC_PACK_BYTES_OR_QUOTA_REBOUND')
            identity=row.get('original_all11')
            need(type(identity) is dict and set(identity)==set(FIELDS)
                and all(type(value) is int for value in identity.values())
                and identity['st_size']==row['bytes'] and identity['st_nlink']==1
                and stat.S_ISREG(identity['st_mode']),'DIAGNOSTIC_PACK_ORIGINAL_ALL11_REBOUND')
            wire=packed.read(member)
            need(len(wire)==row['bytes'] and digest(wire)==row.get('sha256'),'DIAGNOSTIC_PACK_MEMBER_HASH_MISMATCH')
        need(total==index.get('uncompressed_bytes') and type(index.get('uncompressed_bytes')) is int,
            'DIAGNOSTIC_PACK_TOTAL_COUNTER_REBOUND')
    return {'status':'EXACT_RAW_BYTES_AND_CRC_REPLAYED','member_count':len(members),'bytes':total}

def big_browser(a,run,root,prepared,interpreters):
    started=architectural.utc()
    namespace,epoch,repo=exported(a,run,root,prepared,interpreters);source=epoch/'source'
    # The original BIG supervisor expects a normal full Git repository sibling.
    row=run(['git','clone','--no-local','--no-hardlinks','--no-checkout',str(repo),str(epoch/'repository')],cwd=root,label='BIG-repository',limit=300,namespace=namespace)
    need(row['returncode']==0,'BIG_FULL_REPOSITORY_CLONE_FAILED')
    row=run(['git','-C',str(epoch/'repository'),'checkout','--detach',a.source_sha],cwd=root,label='BIG-detach',limit=300,namespace=namespace)
    need(row['returncode']==0,'BIG_EXACT_REPOSITORY_HEAD_FAILED')
    row=run(['git','-C',str(epoch/'repository'),'config','remote.origin.url',ORIGIN],cwd=root,label='BIG-origin',limit=300,namespace=namespace)
    need(row['returncode']==0,'BIG_CANONICAL_ORIGIN_FAILED')
    supervisor=source/'scripts/rc6_material_big.py'
    index_sha=digest(read(epoch/'pin/source.index.json'));tar_sha=file_sha(epoch/'pin/source.tar')
    binding,peak,live=heavy_preflight(a,run,epoch,a.gate,'canonical-BIG')
    native_env={'RC6_GOV_AUTHENTICATED_ROOT_JSON':json.dumps(fixture_lifecycle.namespace_receipt(namespace),separators=(',',':')),
        'RC6_GOV_CAPACITY_BINDING_JSON':json.dumps(binding,separators=(',',':')),
        'RC6_GOV_COMPARABLE_PEAK_JSON':json.dumps(peak,separators=(',',':'))}
    row=run([interpreters['311'],'-I','-B',str(supervisor),'--epoch',str(epoch),'--python',interpreters['311'],
        '--sha',a.source_sha,'--tree',a.source_tree,'--index-sha256',index_sha,'--tar-sha256',tar_sha],
        cwd=root,label='canonical-BIG',limit=360,env=native_env,namespace=namespace)
    preserve_controls(run,'BIG',[epoch/'raw-slow'/name for name in (
        'kernel-supervision.json','native-big-result.json.owned-fin.json','native-fin-postread-decision.json')])
    decision=document(read(epoch/'raw-slow/native-fin-postread-decision.json',256*1024))
    need(decision['native_FIN_payload_safe'] is True and decision['actual_supervisor_pid']==row['kernel']['pid']
        and decision['source_sha']==a.source_sha
        and decision['source_tree']==a.source_tree and decision['source_index_sha256']==index_sha
        and decision['source_tar_sha256']==tar_sha,'BIG_NATIVE_ORIGINAL_FIN_UNKNOWN_PAYLOAD_VETO')
    proof=document(read(epoch/'raw-slow/harness-final.json'))
    need(proof['source_sha']==a.source_sha and proof['source_tree']==a.source_tree
        and proof['native_FIN_payload_safe'] is True,'BIG_RECEIPT_CONTEXT_REBOUND')
    green=(row['returncode']==0 and proof['status']=='GREEN'
        and proof['canonical_big_and_observed_import_gates_closed'] is True and all(proof['checks'].values()))
    native=document(read(epoch/'raw-slow/native-big-result.json')) if (epoch/'raw-slow/native-big-result.json').exists() else {}
    browser=None;code=0 if green else 1
    if green:
        data=Path(native['database']);shadow=Path(native['evidence_root'])
        need(data==epoch/'data-slow/data/paper_v17/observer_v17.db'
            and shadow==data.parent/'artifacts/observer_v17.db/dynamic-shadow','ACTUAL_CANONICAL_BIG_PATHS_REBOUND')
        # Driver setup and browser run begin only after the timed BIG is GREEN.
        heavy_preflight(a,run,epoch,a.gate,'LARGE-browser-prepare')
        def owned_driver(command,**keywords):
            return run(command,namespace=namespace,**keywords)
        driver_module=runpy.run_path(str(a.repo_root/'scripts/rc6_material_browser_driver.py'))
        driver=driver_module['prepare'](source_root=source,output_root=epoch/'browser-driver',python311=Path(interpreters['311']),owned_run=owned_driver)
        if isinstance(driver,Path):driver=document(read(driver))
        browser_output=epoch/'browser-raw'
        heavy_preflight(a,run,epoch,a.gate,'LARGE-browser')
        browser_row=run([str(driver['driver_python']),'-I','-B',str(source/'tests/ci_rc6_projection_large_browser.py'),
            '--product-python',interpreters['311'],'--product-python-version','3.11','--index',str(epoch/'pin/source.index.json'),
            '--database',str(data),'--root',str(shadow),'--output',str(browser_output)],cwd=root,label='LARGE-browser',limit=1800,
            env={'PLAYWRIGHT_BROWSERS_PATH':str(driver['browsers_path'])},namespace=namespace)
        browser=document(read(browser_output/'browser-gate.json'))
        code=0 if browser_row['returncode']==0 and browser['status']=='GREEN' else 1
    selected=material_evidence(namespace,[epoch/'raw-slow',epoch/'browser-raw',epoch/'browser-driver/receipts',epoch/'browser-driver/logs'])
    selected.extend(path.relative_to(epoch).as_posix() for path in (epoch/'browser-driver').glob('*.json'))
    sealed,cleanup=seal_generated(a,run,namespace,root,'BIG-browser',selected)
    resources=native.get('resource_gates',{})
    receipt=architectural.receipt_base('G4',source_sha=a.source_sha,source_tree=a.source_tree,
        read_contract_sha256=architectural.productive_contract(a.repo_root),started_utc=started,native_exit_code=code,
        checks={'canonical_BIG':green,'browser':browser is not None and browser.get('status')=='GREEN',
            'source_snapshot_single_capture_per_tick':proof['checks'].get('source_snapshot_single_capture_per_tick') is True,
            'capacity_live':live['capacity']['status']=='GREEN','authenticated_cleanup':cleanup['namespace_removed'] is True},
        native_evidence={'BIG':captured_reference(sealed,'raw-slow/harness-final.json'),
            'native_BIG':captured_reference(sealed,'raw-slow/native-big-result.json'),
            **({'browser':captured_reference(sealed,'browser-raw/browser-gate.json')} if browser else {})},
        resources={'catalog':native.get('catalog_count'),'observations':native.get('observations_materialized'),
            'factual_paper_exits':native.get('factual_exits',{}).get('closed'),'hard_limit_seconds':90,
            'qualification_limit_seconds':75,'elapsed_seconds':native.get('shadow',{}).get('elapsed_seconds'),
            'peak_rss_bytes':resources.get('actual_rss_bytes'),'evidence_bytes':resources.get('actual_evidence_bytes'),
            'retained_entries':cleanup['retained_entries_before']},cleanup_receipt=cleanup,
        required_browser_material_green=browser is not None and browser.get('status')=='GREEN',source_unchanged=green)
    save(root/'G4.receipt.json',receipt)
    return code,{'gate':a.gate,'receipt':receipt,'BIG_receipt':proof,'browser_receipt':browser,
        'raw_root':str(sealed),'artifact_validated':False,'native_FIN_payload_safe':True}

def file_sha(path):
    path=safe_path(path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NOATIME|os.O_CLOEXEC)
    try:
        before=os.fstat(fd);need(stat.S_ISREG(before.st_mode) and before.st_uid==os.geteuid() and before.st_nlink==1,'OWNED_SOURCE_TAR_REQUIRED')
        h=hashlib.sha256()
        while block:=os.read(fd,1024**2):h.update(block)
        need(attrs(before)==attrs(os.fstat(fd))==attrs(path.lstat()),'SOURCE_TAR_ALL11_CHANGED');return h.hexdigest()
    finally:os.close(fd)

def horizon(a,run,root,prepared,interpreters):
    # Kept in a separate module so the original21600/30/5 lifecycle can be reviewed independently.
    started=architectural.utc()
    namespace,epoch,repo=exported(a,run,root,prepared,interpreters)
    source=epoch/'source';control=epoch/'horizon-control'
    command=[interpreters['311'],'-I','-B',str(source/'scripts/rc6_material_horizon.py'),
        '--source-root',str(source),'--source-repo',str(repo),'--source-sha',a.source_sha,'--source-tree',a.source_tree,
        '--source-index',str(epoch/'pin/source.index.json'),'--raw-root',str(epoch/'native-raw'),
        '--data-root',str(epoch/'native-data'),'--python311',interpreters['311'],'--control-root',str(control)]
    binding,peak,live=heavy_preflight(a,run,epoch,a.gate,'Horizon-original1201')
    row=run(command,cwd=root,label='Horizon-original1201',limit=21600,namespace=namespace)
    preserve_controls(run,'Horizon',[control/name for name in ('producer-owned-fin.json','terminal.json','launch.json')])
    report=document(read(control/'terminal.json',256*1024));code=row['returncode']
    need(report.get('source_sha')==a.source_sha and report.get('source_tree')==a.source_tree,
         'HORIZON_TERMINAL_SOURCE_CONTEXT_REBOUND')
    if not (report.get('physical_custody_closed') is True and report.get('native_finalizer5_green_before_payload_reads') is True):
        raise ValueError('HORIZON_NATIVE_FIN_UNKNOWN_PAYLOAD_VETO')
    run.manager['pre_capture_kernel_state']()
    result_path=epoch/'native-data/result.json';wire=read(result_path)
    save_raw(epoch/'native-raw/native-horizon-result.json',wire)
    report['full_original_native_result_raw_capture']={'original_path':str(result_path),
        'raw_path':str(epoch/'native-raw/native-horizon-result.json'),'sha256':digest(wire),'bytes':len(wire),
        'stat_after':attrs(result_path.lstat()),'captured_only_after_native_and_outer_FIN':True}
    report['native_FIN_payload_safe']=True
    required=material_evidence(namespace,[control,epoch/'native-raw'])
    sealed,cleanup=seal_generated(a,run,namespace,root,'Horizon',required)
    prior=[node for node in a.verified_prerequisites['authenticated_receipts'] if node['gate']=='G4']
    browser_green=len(prior)==1 and prior[0].get('required_browser_material_green') is True
    accepted=(code==0 and report.get('native_horizon_validated') is True
        and report.get('original_native_acceptance_checks_executed') is True)
    code=0 if accepted and browser_green else 1
    receipt=architectural.receipt_base('G5',source_sha=a.source_sha,source_tree=a.source_tree,
        read_contract_sha256=architectural.productive_contract(a.repo_root),started_utc=started,native_exit_code=code,
        checks={'retention':accepted,'Horizon':accepted,'browser':browser_green,
            'capacity_live':live['capacity']['status']=='GREEN','authenticated_cleanup':cleanup['namespace_removed'] is True},
        required_material_gates=['retention','Horizon','browser'],
        native_evidence={'Horizon':captured_reference(sealed,'horizon-control/terminal.json'),
            'native_Horizon':captured_reference(sealed,'native-raw/native-horizon-result.json')},
        cleanup_receipt=cleanup,source_unchanged=accepted)
    save(root/'G5.receipt.json',receipt)
    return code,{**report,'receipt':receipt,'raw_root':str(sealed)}

def stage_raw(a,run,root,prepared,report):
    need(report.get('native_FIN_payload_safe') is True and not run.unknown,'ALL_NATIVE_FIN_REQUIRED_BEFORE_RAW_STAGE')
    module=runpy.run_path(str(a.repo_root/'scripts/rc6_material_raw.py'))
    groups=[('carrier',root),('controls',run.control),('drivers',root/'drivers'),
        ('commands',root/'command-raw'),*run.sealed_groups]
    for item in prepared:
        parent=Path(item['parent']);epoch=item['epoch']
        groups.extend([('preparation'+epoch,parent/'preparation'),('focal'+epoch,parent/'focal'),
            ('Gov'+epoch,parent/'native-gov'),('GovOuter'+epoch,parent/'outer-control')])
        # Original stress tests preserve typed RED receipts in this literal
        # RUNNER_TEMP child. Select only its regular evidence files after the
        # caller's original native/parent FIN; never traverse pytest fixtures.
        for label,producer in (('focal',parent/'focal'),('Gov',parent/'native-gov')):
            for phase in ('collection','execution'):
                groups.append((label+epoch+'-'+phase+'-issue465',
                    producer/(phase+'-private')/'porota-predeploy-evidence'/'issue465-stress'))
    material=root/'material'
    groups.extend([('pin',material/'pin'),('BIG',material/'raw-slow'),('browser',material/'browser-raw'),
        ('horizon',material/'native-raw'),('horizon-control',root/'horizon-control')])
    # Exact small DRIVER controls only; never cache/assets/wheels/driver-env.
    driver=material/'browser-driver';groups.extend([('driver',driver),('driver-receipts',driver/'receipts'),('driver-logs',driver/'logs')])
    return module['bundle'](namespace=root,output_root=root/'supplementary-raw',groups=groups,files=[],
        read=read,save_raw=save_raw,save=save,source_sha=a.source_sha,source_tree=a.source_tree,gate=a.gate)

def main():
    if '--diagnostic-admission-json' in sys.argv:return diagnostic_main()
    p=argparse.ArgumentParser();p.add_argument('--repo-root',type=Path,required=True);p.add_argument('--source-sha',required=True)
    p.add_argument('--source-tree',required=True);p.add_argument('--launch-receipt-url',required=True)
    p.add_argument('--owner-session',required=True)
    p.add_argument('--require-pr-admission',action='store_true')
    p.add_argument('--python311',required=True);p.add_argument('--python312',required=True)
    p.add_argument('--gate',choices=('cheap','focal311','focal312','full-gov311','full-gov312','BIG-browser','Horizon'),required=True)
    a=p.parse_args();os.umask(0o022);boot=bootstrap();a.repo_root=safe_path(a.repo_root)
    need(a.require_pr_admission,'EVERY_GATE_REQUIRES_FRESH_ORDERED_ADMISSION')
    need(os.environ.get('GITHUB_EVENT_NAME')!='pull_request' or a.gate=='cheap','PR_PUSH_MUST_NOT_LAUNCH_HEAVY_GATES')
    need(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('GITHUB_REPOSITORY')==REPO
        and os.environ.get('GITHUB_REPOSITORY_ID')==str(REPO_ID),'CANONICAL_ACTIONS_REPOSITORY_REQUIRED')
    need(re.fullmatch('[0-9a-f]{40}',a.source_sha) and re.fullmatch('[0-9a-f]{40}',a.source_tree),'EXACT_LITERAL_SOURCE_SHA_TREE_REQUIRED')
    need(git(a.repo_root,'rev-parse','HEAD').decode().strip()==a.source_sha
        and git(a.repo_root,'rev-parse','HEAD^{tree}').decode().strip()==a.source_tree,
        'EXACT_SOURCE_BOOTSTRAP_CHECKOUT_REQUIRED')
    need(not git(a.repo_root,'status','--porcelain').strip(),'CLEAN_ACTIONS_CHECKOUT_REQUIRED')
    home=safe_path(Path.home());need(len(os.fsencode(home))<=24 and home.lstat().st_uid==os.geteuid(),'SHORT_OWNED_ACTIONS_HOME_REQUIRED')
    import tempfile
    root=Path(tempfile.mkdtemp(prefix='r6m-',dir=home));need(len(os.fsencode(root))<=31,'SHORT_GOV_NAMESPACE_PARENT_REQUIRED')
    controls=root/'controls-only';controls.mkdir(mode=0o700)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as stream:stream.write('control_root='+str(controls)+'\nsafe_payload_upload=false\n')
    save(controls/'launch.json',{'schema':'rc6.supplementary-material-actions-launch.v1','source_sha':a.source_sha,
        'source_tree':a.source_tree,'gate':a.gate,'run_id':os.environ['GITHUB_RUN_ID'],'attempt':os.environ['GITHUB_RUN_ATTEMPT'],
        'boot_kernel':boot,'owner_uuid':uuid.uuid4().hex,'admission':admission(root),'real_orders_sent':0,
        'mode':'PRODUCTION_PAPER / SIMULATION','PPI_Watch':'UNTOUCHED','no_build_no_deploy':True,'promotable_artifact':False})
    manager_pin=module_pin(a.repo_root,'scripts/rc6_controlled_native_child_manager.py',a.source_sha)
    native_pin=module_pin(a.repo_root,'scripts/rc6_controlled_governed_runner.py',a.source_sha)
    manager=runpy.run_path(str(a.repo_root/manager_pin['path']));manager['pre_capture_kernel_state']()
    run=OwnedRunner(manager,controls);code=1
    try:
        readmit_automatic_pr(a,run,'before-preparation')
        if a.gate!='cheap':
            a.verified_prerequisites=architectural.verify_manifest(a.prerequisites_manifest,
                source_sha=a.source_sha,source_tree=a.source_tree,target_gate=architectural.ALIASES[a.gate],
                evidence_root=root/'verified-prerequisites')
            save(controls/'ordered-prerequisite-admission.json',a.verified_prerequisites)
        auth=authority(a)
        a.preparation_started_utc=architectural.utc()
        complete_full_git(a,run,root)
        preparation_capacity(a,run,root,'before-installed-environments')
        interpreters=installed_env(a,run,root)
        preparation_capacity(a,run,root,'before-original-Git-object-preparation')
        fetch_originals(a,run,root)
        preparation_capacity(a,run,root,'before-complete-Gov-Source-preparation')
        derived,prepared,auth_path,auth_sha=prepare_gov(a,run,root,interpreters,auth,manager_pin,native_pin)
        save(controls/'prepared.json',{'source_sha':a.source_sha,'source_tree':a.source_tree,'gate':a.gate,
            'epochs':[{k:v for k,v in x.items() if k in ('epoch','parent','source_root','blocked_binding_sha256')} for x in prepared],
            'original19_verified':True,'PRODUCT157_preflight_verified':True,'Gov_executed':False})
        readmit_automatic_pr(a,run,'after-preparation-before-native')
        if a.gate=='cheap':
            static=static_admission(a,run,root,prepared,interpreters)
            architectural.validate_chain([static],source_sha=a.source_sha,source_tree=a.source_tree,target_gate='G1.311')
            code,report=focal(a,run,root,prepared,interpreters)
        elif a.gate in ('focal311','focal312'):code,report=focal(a,run,root,prepared,interpreters)
        elif a.gate.startswith('full-gov'):code,report=run_gov(a,run,root,prepared,derived,auth_path,auth_sha)
        elif a.gate=='BIG-browser':code,report=big_browser(a,run,root,prepared,interpreters)
        else:code,report=horizon(a,run,root,prepared,interpreters)
        # Each adapter has independently proved every native phase FIN before returning payload.
        need(report.get('native_FIN_payload_safe') is True,'GATE_FIN_UNKNOWN_PAYLOAD_VETO')
        manager['pre_capture_kernel_state']();save(root/'supplementary-result.json',report)
        staged=stage_raw(a,run,root,prepared,report)
        save(controls/'final.json',{'status':'GREEN_SUPPLEMENTARY_ONLY' if code==0 else 'RED','gate':a.gate,
            'source_sha':a.source_sha,'source_tree':a.source_tree,'result_sha256':digest(canonical(report)),
            'final_candidate_eligible':False,'artifact_validated':False,'runtime_validated':False,'real_orders_sent':0,
            'final_capacity':admission(root),'resources_retained_for_ephemeral_runner_teardown':True,
            'GLOBAL_CLEANUP_GREEN':False,'lossless_RAW':staged})
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a') as stream:stream.write('safe_payload_upload=true\npayload_root='+staged['payload_root']+'\n')
        print(json.dumps({'gate':a.gate,'exit_code':code,'supplementary_only':True,'final_candidate_eligible':False}),flush=True)
    except BaseException as e:
        code=1
        reason=str(e).split(':',1)[0];reason=reason if re.fullmatch('[A-Z][A-Z0-9_]{0,191}',reason) else 'NON_LITERAL_CARRIER_FAILURE'
        save(controls/'error.json',{'status':'RED','class':type(e).__name__,'reason':reason,'gate':a.gate,
            'payload_upload_allowed':False,'original_RED_preserved':True,'real_orders_sent':0})
        print(json.dumps({'gate':a.gate,'status':'RED','reason':reason,'payload_upload_allowed':False}),flush=True)
    return code
if __name__=='__main__':raise SystemExit(main())
