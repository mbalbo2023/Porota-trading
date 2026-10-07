"""Reusable supplemental RC6 Actions carrier. No build, deploy or promotable artifact."""
import argparse
from collections import Counter
import ctypes
import errno
import hashlib
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
        and pr['base']['ref']=='deploy/rc6-pr69-isolated-20260915','FRESH_GITHUB_SOURCE_AUTHORITY_MISMATCH')
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
        self.raw.mkdir(mode=0o700);self.unknown=False;self.closed_logs=[]
    def __call__(self,argv,*,cwd,label,limit=300,env=None):
        need(re.fullmatch('[a-zA-Z0-9_.-]+',label),'LITERAL_COMMAND_LABEL_REQUIRED')
        need(type(limit) in (int,float) and math.isfinite(limit) and 0<limit<=21600,'DECLARED_FINITE_COMMAND_BOUND_REQUIRED')
        self.manager['pre_capture_kernel_state']();log=self.raw/(label+'.log')
        save(self.control/(label+'.launch-intent.json'),{'argv':argv,'owner_uid':os.geteuid(),'parent_pid':os.getpid(),
            'scope':'PREPARATION_OR_DECLARED_SUPPLEMENTARY_GATE','management_seconds':limit})
        last=[0.0]
        def progress(stage,pid,entered,deadline,fd):
            now=time.monotonic()
            if stage=='started' or now-last[0]>=40:
                last[0]=now;print(json.dumps({'label':label,'stage':stage,'actual_pid':pid,
                    'wall_seconds':now-entered,'scope':'OWN_FSTAT_ONLY_NO_ACTIVE_PAYLOAD_READ'}),flush=True)
        self.unknown=True
        kernel=self.manager['managed_native_child'](argv,cwd,log,clean_env(env),limit,progress=progress)
        save(self.control/(label+'.kernel.json'),kernel)
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
    save(run.control/('automatic-pr-'+stage+'.json'),row)

def installed_env(a,run,root):
    interpreters={}
    for epoch,base in (('311',a.python311),('312',a.python312)):
        private=root/('product'+epoch);private.mkdir(mode=0o700);venv_root=private/'venv'
        row=run([base,'-I','-B',str(a.repo_root/'scripts/rc6_material_environment.py'),'--root',str(venv_root),
            '--owner-uid',str(os.geteuid()),'--python-version',VERSIONS[epoch],'--receipt',str(private/'creation.json')],
            cwd=root,label='venv'+epoch,limit=300);need(row['returncode']==0,'PRODUCT_VENV_CONSTRUCTION_FAILED')
        py=str(venv_root/'bin/python')
        steps=[['--require-hashes','--only-binary=:all:','-r',str(a.repo_root/'requirements.build.lock.txt')],
               ['--require-hashes','--only-binary=:all:','--no-binary=msgpack,ppi-client,signalrcoreppi,ta',
                '--no-build-isolation','-r',str(a.repo_root/'requirements.lock.txt')]]
        for n,args in enumerate(steps):
            row=run([py,'-I','-B','-m','pip','install','--disable-pip-version-check',*args],cwd=root,
                label='locked'+epoch+'-'+str(n),limit=1800)
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
            result=run(['git','-C',str(a.repo_root),'-c','credential.helper=','fetch','--no-tags','--no-write-fetch-head',
                '--no-auto-maintenance',ORIGIN,row['sha']],cwd=root,label='original-fetch-'+str(n),limit=300,
                env={'GIT_ASKPASS':str(askpass),'RC6_READONLY_GIT_TOKEN':token})
            need(result['returncode']==0,'ORIGINAL_LITERAL_FETCH_FAILED')
        raw=git(a.repo_root,'cat-file','commit',row['sha'])
        need(digest(raw)==row['raw_commit_sha256'] and hashlib.sha1(b'commit '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==row['sha']
            and raw.splitlines()[0]==b'tree '+row['github_tree'].encode(),'ORIGINAL19_COMMIT_TREE_CHANGED')
    return objects

def prepare_gov(a,run,root,interpreters,auth,manager_pin,native_pin):
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
    epoch=a.gate[-3:];item=next(x for x in prepared if x['epoch']==epoch)
    binding=document(read(Path(item['blocked_binding'])))
    binding.update(stage='REMOTE_FINAL_SOURCE_FROZEN_AND_DURABLE_LAUNCH_AUTHORIZED',schema='rc6.material-fullGov-filled-binding.v1',
        remote_authority_file=str(auth_path),remote_authority_sha256=auth_sha,durable_launch_receipt_url=a.launch_receipt_url)
    path=root/('gov'+epoch+'-authorized.json');save(path,binding)
    # The reviewed outer is itself the actual parent of native collection/execution.
    result=run([binding['interpreter'],'-I','-B',derived['wrapper_path'],'--binding',str(path)],cwd=root,
        label='fullGov'+epoch,limit=21600)
    control=Path(binding['control_root'])
    if not (control/'native-phase-controls-closed.json').exists():
        # Explicit control files only; never wholeGov.log/Source/JUnit/observations.
        for name in ('launch-intent.json','outer-kernel.json','UNKNOWN-control-only.json','UNKNOWN-native-control-only.json'):
            if os.path.lexists(control/name):save_raw(run.control/('fullGov'+epoch+'-'+name),read(control/name,256*1024))
        for phase in ('collection','execution'):
            for suffix in ('.kernel.json','.child-finalization.json'):
                path=Path(binding['gov_output_root'])/(phase+suffix)
                if os.path.lexists(path):save_raw(run.control/('fullGov'+epoch+'-'+phase+suffix),read(path,256*1024))
        raise ValueError('FULL_GOV_NATIVE_PHASE_UNKNOWN_PAYLOAD_VETO')
    phases=document(read(control/'native-phase-controls-closed.json'))
    w=runpy.run_path(str(root/'drivers/governed_outer.py'))
    native_outer=document(read(control/'outer-kernel.json'))
    need(run.manager['managed_custody_closed'](native_outer) and w['outer_infrastructure_without_logical_exit_veto'](native_outer)
        and native_outer['supervisor_pid']==result['kernel']['pid']
        and native_outer['launcher_management_deadline_seconds']==21600,'FULL_GOV_ACTUAL_OUTER_CONTROL_REBOUND')
    need(phases['attempted_phases'] in (['collection'],['collection','execution']),'FULL_GOV_NATIVE_PHASE_LEDGER_REBOUND')
    for phase in phases['attempted_phases']:
        item=phases[phase];kernel=item['kernel'];final=item['finalization']
        native=Path(binding['gov_output_root'])
        kernel_raw=read(native/(phase+'.kernel.json'));final_raw=read(native/(phase+'.child-finalization.json'))
        expected=[binding['interpreter'],'-I','-B',str(Path(binding['source_root'])/'scripts/rc6_controlled_governed_runner.py'),
            '--repo-root',binding['source_root'],'--source-sha',a.source_sha,'--source-tree',a.source_tree,
            '--output-root',binding['gov_output_root'],'--timeout-seconds','5400','--phase',phase]
        need(w['native_kernel_closed'](kernel) and w['native_finalization_closed'](final)
            and document(kernel_raw)==kernel and digest(kernel_raw)==item['kernel_sha256']
            and document(final_raw)==final and digest(final_raw)==item['finalization_sha256']
            and kernel['command']==expected and kernel['supervisor_pid']==native_outer['pid']
            and kernel['phase_acceptance_deadline_seconds']==5400 and kernel['owned_cleanup_management_bound_seconds']==5
            and kernel['returncode'] in (0,1) and kernel['timed_out'] is False
            and not kernel['owned_group_signal_observations']
            and all(row['exit_code']==0 for row in kernel['adopted_descendants_reaped']),
            'FULL_GOV_NATIVE_ORIGINAL_FIN_CONTEXT_NOT_CLOSED')
    receipt=document(read(control/'receipt.json')) if (control/'receipt.json').exists() else {
        'status':'RED','classification':'ALL_NATIVE_FIN_CLOSED_OUTER_POSTVALIDATION_FAILED',
        'no_missing_receipt_fabricated':True,'native_wholeGov_GREEN':False}
    code=0 if result['returncode']==0 and receipt.get('native_wholeGov_GREEN') is True else 1
    return code,{'gate':a.gate,'native_phases':phases,'receipt':receipt,
        'raw_root':binding['gov_output_root'],'outer_control_root':str(control),'whole_Gov_claim':code==0,
        'native_FIN_payload_safe':True}

def focal(a,run,root,prepared,interpreters):
    w=runpy.run_path(str(root/'drivers/governed_outer.py'));summaries=[];code=0
    for epoch in ('311','312'):
        item=next(x for x in prepared if x['epoch']==epoch);source=Path(item['source_root']);output=Path(item['parent'])/'focal'
        output.mkdir(mode=0o700);g=runpy.run_path(str(source/'scripts/rc6_controlled_governed_runner.py'))
        before=g['source_pin'](source,a.source_sha,a.source_tree);save_raw(output/'parent-source-before.json',g['canonical'](before))
        phases={}
        for phase in ('collection','execution'):
            readmit_automatic_pr(a,run,'focal'+epoch+'-'+phase+'-prelaunch')
            command=[interpreters[epoch],'-I','-B',str(source/'scripts/rc6_material_focal.py'),'--repo-root',str(source),
                '--source-sha',a.source_sha,'--source-tree',a.source_tree,'--output-root',str(output),'--phase',phase]
            row=run(command,cwd=source,label='focal'+epoch+'-'+phase,limit=5400)
            need(run.manager['managed_custody_closed'](row['kernel'])
                 and row['kernel']['launcher_management_deadline_seconds']==5400,
                 'FOCAL_ORIGINAL_MANAGER_KERNEL_NOT_CLOSED')
            final=document(read(output/(phase+'.child-finalization.json')))
            need(w['native_finalization_closed'](final),'FOCAL_FINALIZER_UNKNOWN_PAYLOAD_VETO')
            phases[phase]=document(read(output/(phase+'.observations.json')))
            need(not phases[phase]['inet_socket_attempts'] and not phases[phase]['unexpected_product_imports'],
                 'FOCAL_NATIVE_IMPORT_OR_NETWORK_RED')
            if phase=='collection' and row['returncode']!=0:
                return 1,{'gate':'focal','epoch':epoch,'scope':'CLOSED_FOCAL_COLLECTION_RED_EXECUTION_NOT_LAUNCHED',
                    'collection':phases[phase],'raw_root':str(output),'whole_Gov_claim':False,'native_FIN_payload_safe':True}
            else:code=max(code,row['returncode'])
        after=g['source_pin'](source,a.source_sha,a.source_tree);atime=g['compare_source'](before,after)
        save_raw(output/'parent-source-after.json',g['canonical'](after))
        need(phases['collection']['items']==phases['execution']['items'] and phases['collection']['items'],'FOCAL_NODE_IDENTITY_MISMATCH')
        xml=read(output/'focal-tests.xml',16*1024**2);cases=list(ET.fromstring(xml).iter('testcase'))
        expected=Counter((x['classname'],x['name']) for x in phases['collection']['items']);actual=Counter((x.get('classname'),x.get('name')) for x in cases)
        need(expected==actual and all(v==1 for v in actual.values()),'FOCAL_COLLECTION_JUNIT_IDENTITY_MISMATCH')
        counts={tag:sum(c.find(tag) is not None for c in cases) for tag in ('failure','error','skipped')}
        logical_red=any(counts.values()) or phases['execution']['pytest_exit_code']!=0
        summaries.append({'epoch':epoch,'executed':len(cases),'counts':counts,'junit_sha256':digest(xml),'raw_root':str(output),
            'original_Source10_unchanged':True,'CODE_atime_observed':atime,'whole_Gov_claim':False})
        if logical_red:return 1,{'gate':'focal','epochs':summaries,'scope':'CLOSED_FOCAL_LOGICAL_RED_NOT_WHOLE_GOV','native_FIN_payload_safe':True}
    return code,{'gate':'focal','epochs':summaries,'scope':'FOCAL_ONLY_NOT_WHOLE_GOV_NO_ARTIFACT_RUNTIME_CLAIM','native_FIN_payload_safe':True}

def exported(a,run,root,prepared,interpreters):
    epoch=root/'material';epoch.mkdir(mode=0o700)
    repo=Path(next(x for x in prepared if x['epoch']=='311')['source_root'])
    # Original exporter creates source700 with Git644/755 files; full index, tar and raw commit remain separate.
    row=run([interpreters['311'],'-I','-B',str(repo/EXPORT_MEMBER),'--repo',str(repo),'--sha',a.source_sha,
        '--source',str(epoch/'source'),'--raw',str(epoch/'pin')],cwd=root,label='wholeGit-export',limit=300)
    need(row['returncode']==0,'WHOLE_GIT_EXPORT_FAILED')
    return epoch,repo

def big_browser(a,run,root,prepared,interpreters):
    epoch,repo=exported(a,run,root,prepared,interpreters);source=epoch/'source'
    # The original BIG supervisor expects a normal full Git repository sibling.
    row=run(['git','clone','--no-local','--no-hardlinks','--no-checkout',str(repo),str(epoch/'repository')],cwd=root,label='BIG-repository',limit=300)
    need(row['returncode']==0,'BIG_FULL_REPOSITORY_CLONE_FAILED')
    row=run(['git','-C',str(epoch/'repository'),'checkout','--detach',a.source_sha],cwd=root,label='BIG-detach',limit=300)
    need(row['returncode']==0,'BIG_EXACT_REPOSITORY_HEAD_FAILED')
    row=run(['git','-C',str(epoch/'repository'),'config','remote.origin.url',ORIGIN],cwd=root,label='BIG-origin',limit=300)
    need(row['returncode']==0,'BIG_CANONICAL_ORIGIN_FAILED')
    supervisor=source/'scripts/rc6_material_big.py'
    index_sha=digest(read(epoch/'pin/source.index.json'));tar_sha=file_sha(epoch/'pin/source.tar')
    row=run([interpreters['311'],'-I','-B',str(supervisor),'--epoch',str(epoch),'--python',interpreters['311'],
        '--sha',a.source_sha,'--tree',a.source_tree,'--index-sha256',index_sha,'--tar-sha256',tar_sha],
        cwd=root,label='canonical-BIG',limit=360)
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
    if not (row['returncode']==0 and proof['status']=='GREEN'
        and proof['canonical_big_and_observed_import_gates_closed'] is True and all(proof['checks'].values())):
        return 1,{'gate':a.gate,'BIG_receipt':proof,'browser':'NOT_STARTED_NATIVE_BIG_RED',
            'raw_root':str(epoch/'raw-slow'),'native_FIN_payload_safe':True,'artifact_validated':False}
    native=document(read(epoch/'raw-slow/native-big-result.json'))
    data=Path(native['database']);shadow=Path(native['evidence_root'])
    need(data==epoch/'data-slow/data/paper_v17/observer_v17.db'
        and shadow==data.parent/'artifacts/observer_v17.db/dynamic-shadow','ACTUAL_CANONICAL_BIG_PATHS_REBOUND')
    # Download/install is deliberately after the timed native BIG, no CPU/IO contention.
    driver_module=runpy.run_path(str(a.repo_root/'scripts/rc6_material_browser_driver.py'))
    driver=driver_module['prepare'](source_root=source,output_root=epoch/'browser-driver',python311=Path(interpreters['311']),owned_run=run)
    if isinstance(driver,Path):driver=document(read(driver))
    browser_output=epoch/'browser-raw'
    row=run([str(driver['driver_python']),'-I','-B',str(source/'tests/ci_rc6_projection_large_browser.py'),
        '--product-python',interpreters['311'],'--product-python-version','3.11','--index',str(epoch/'pin/source.index.json'),
        '--database',str(data),'--root',str(shadow),'--output',str(browser_output)],cwd=root,label='LARGE-browser',limit=1800,
        env={'PLAYWRIGHT_BROWSERS_PATH':str(driver['browsers_path'])})
    browser=document(read(browser_output/'browser-gate.json'))
    code=0 if row['returncode']==0 and browser['status']=='GREEN' else 1
    return code,{'gate':a.gate,'BIG_receipt':proof,'browser_receipt':browser,'data_root':str(epoch/'data-slow'),
        'raw_root':str(epoch/'raw-slow'),'browser_raw_root':str(browser_output),'artifact_validated':False,'native_FIN_payload_safe':True}

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
    epoch,repo=exported(a,run,root,prepared,interpreters)
    source=epoch/'source';control=root/'horizon-control'
    command=[interpreters['311'],'-I','-B',str(source/'scripts/rc6_material_horizon.py'),
        '--source-root',str(source),'--source-repo',str(repo),'--source-sha',a.source_sha,'--source-tree',a.source_tree,
        '--source-index',str(epoch/'pin/source.index.json'),'--raw-root',str(epoch/'native-raw'),
        '--data-root',str(epoch/'native-data'),'--python311',interpreters['311'],'--control-root',str(control)]
    row=run(command,cwd=root,label='Horizon-original1201',limit=21600)
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
    return code,report

def stage_raw(a,run,root,prepared,report):
    need(report.get('native_FIN_payload_safe') is True and not run.unknown,'ALL_NATIVE_FIN_REQUIRED_BEFORE_RAW_STAGE')
    module=runpy.run_path(str(a.repo_root/'scripts/rc6_material_raw.py'))
    groups=[('carrier',root),('drivers',root/'drivers'),('commands',root/'command-raw')]
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
    p=argparse.ArgumentParser();p.add_argument('--repo-root',type=Path,required=True);p.add_argument('--source-sha',required=True)
    p.add_argument('--source-tree',required=True);p.add_argument('--launch-receipt-url',required=True)
    p.add_argument('--owner-session',required=True)
    p.add_argument('--require-pr-admission',action='store_true')
    p.add_argument('--python311',required=True);p.add_argument('--python312',required=True)
    p.add_argument('--gate',choices=('focal','full-gov311','full-gov312','BIG-browser','Horizon'),required=True)
    a=p.parse_args();os.umask(0o022);boot=bootstrap();a.repo_root=safe_path(a.repo_root)
    need(os.environ.get('GITHUB_EVENT_NAME')!='pull_request' or a.require_pr_admission,'AUTOMATIC_PR_FRESH_ADMISSION_REQUIRED')
    need(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('GITHUB_REPOSITORY')==REPO
        and os.environ.get('GITHUB_REPOSITORY_ID')==str(REPO_ID),'CANONICAL_ACTIONS_REPOSITORY_REQUIRED')
    need(re.fullmatch('[0-9a-f]{40}',a.source_sha) and re.fullmatch('[0-9a-f]{40}',a.source_tree),'EXACT_LITERAL_SOURCE_SHA_TREE_REQUIRED')
    need(git(a.repo_root,'rev-parse','HEAD').decode().strip()==a.source_sha
        and git(a.repo_root,'rev-parse','HEAD^{tree}').decode().strip()==a.source_tree
        and git(a.repo_root,'rev-parse','--is-shallow-repository').strip()==b'false','EXACT_FULL_GIT_CHECKOUT_REQUIRED')
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
        auth=authority(a);interpreters=installed_env(a,run,root);fetch_originals(a,run,root)
        derived,prepared,auth_path,auth_sha=prepare_gov(a,run,root,interpreters,auth,manager_pin,native_pin)
        save(controls/'prepared.json',{'source_sha':a.source_sha,'source_tree':a.source_tree,'gate':a.gate,
            'epochs':[{k:v for k,v in x.items() if k in ('epoch','parent','source_root','blocked_binding_sha256')} for x in prepared],
            'original19_verified':True,'PRODUCT157_preflight_verified':True,'Gov_executed':False})
        readmit_automatic_pr(a,run,'after-preparation-before-native')
        if a.gate=='focal':code,report=focal(a,run,root,prepared,interpreters)
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
