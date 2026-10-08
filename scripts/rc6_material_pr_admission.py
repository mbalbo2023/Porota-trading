"""Read-only admission for the registered supplementary RC6 PR carrier.

Launch authority is immutable and exact-Source; live ownership is re-read. This
controller never dispatches, edits a ref, acquires deploy ownership or runs gates.
"""
import argparse
import base64
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import urllib.parse
import urllib.request

ROOT=Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

REPO='mbalbo2023/Porota-trading'
REPO_ID=1338680554
WORKSTREAM='WS-RC6-CONVERGENCE-468-469-470-20261005'
BRANCH='recovery/rc6-material-fix-forward-3091e93-20261006'
BASE='deploy/rc6-pr69-isolated-20260915'
BASE_SHA='da697c6e6c2274579f9e4a112fabc4327475dd35'
PR=476
GATES=('cheap','focal311','focal312','BIG-browser','Horizon','full-gov311','full-gov312','predeploy')
DIAGNOSTIC_GATES=('capacity-probe','capacity-calibration')
DIAGNOSTIC_BRANCH='wip/rc6-architectural-rca-20261008-1606UTC'
RECOVERY_HEAD='dfc240a478cf08e0c6a9b0ba1b760307b09a9565'
RECOVERY_TREE='192ea26348c8fe10db55857cdd6029edd8344708'
GUARDS_HEAD='756d37b93aa26bb6395dac481bf3c2dda9d034d7'
OPS='docs/automation/rc6-night-20261005/state.json'
OPS_REF='ops/rc6-night-supervisor-20261005'
CAP=1024*1024
CONTROL_CAP=256*1024
FIELD_KEYS={'WORKSTREAM_ID','SESSION','SESSION_SUCCESSOR','WRITE_OWNER','INTEGRATION_OWNER','DEPLOY_OWNER','RELEASED',
    'SOURCE_LEASE_EXPIRES_UTC','SOURCE_SHA','SOURCE_TREE','MODE','real_orders_sent',
    'RC6_MATERIAL_AUTOMATIC_PR_AUTHORIZATION','GATES_AUTHORIZED','CAUSE_EVIDENCE_URL',
    'RECONCILED_OPS_STATE_BLOB','RECONCILED_OPS_WRITE_OWNER','RECONCILIATION_RECEIPT_471',
    'RECONCILIATION_RECEIPT_473','RECONCILIATION_USER_STOP_CONFIRMED',
    'RECONCILIATION_RECEIPT_471_SHA256','RECONCILIATION_RECEIPT_473_SHA256',
    'RECONCILED_OPS_STATE_SHA256','CANONICAL_GOV311_CUSTODY_RECEIPT_URL',
    'SUPPLEMENTARY_GOV311_JUSTIFICATION_URL','PREREQUISITES_MANIFEST_JSON','CAPACITY_PEAKS_JSON',
    'CHEAP_FILES_JSON','RC6_PREDEPLOY_G7_AUTHORIZATION','SOURCE_MANIFEST_SHA256',
    'CAPACITY_COMPARISON_MANIFEST_JSON','RC6_CAPACITY_DIAGNOSTIC_AUTHORIZATION',
    'DIAGNOSTIC_MODE','SOURCE_WIP','READ_CONTRACT_SHA256','CAPACITY_DIAGNOSTIC_SCOPE_JSON',
    'CAPACITY_DIAGNOSTIC_PREREQUISITES_JSON'}
COMMENT_PAGES=50
RUN_PAGES=10
RECOVERY_OWNER='CODEX_RC6_CONTROLLED_RECOVERY_20261007_0015UTC'
RECOVERY_BODY_SHA256='388680bae2aa2739532b7b62a85e767361de3f38aae8149a676fe65695fa8b05'
RECOVERY_ANCHORS={471:(6027924708,'2026-10-07T00:13:37Z'),473:(6027924879,'2026-10-07T00:13:38Z')}
RECONCILED_BLOB='d52b4090f53c1897b735a903581b44e107e57020'
RECONCILED_RAW_SHA256='746551deb6f856c4000a1aea89d0e24cb371b4dc1c193e2c4b4f3f2d8842d0b7'
RECONCILED_OLD_OWNER='CODEX_SUCCESSOR_RC6_20261006_1246UTC'
USER_STOP_LITERAL='Martín autorizó esta recuperación/fix-forward y confirmó directamente en esta conversación que la sesión que tomó ownership como supervisor también quedó detenida.'
SUCCESSOR_OWNER='CODEX_RC6_ARCHITECTURAL_RCA_20261008_1205UTC'
SUCCESSOR_BODY_SHA256='3e172082018e88e7a255cc5ee9675898a1dd289f0b1abc98815a870c627504b6'
SUCCESSOR_ANCHORS={471:(6059477308,'2026-10-08T12:06:09Z'),473:(6059477667,'2026-10-08T12:06:10Z')}
STAT11=('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_blocks','st_atime_ns','st_mtime_ns','st_ctime_ns')
CAPACITY_COMPARISON_MANIFEST_SCHEMA='porota.rc6.capacity-comparison-manifest.v1'

def require(ok,reason):
    if not ok:raise ValueError(reason)
def wire(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def document(raw):
    def pairs(items):
        out={}
        for key,value in items:require(key not in out,'ADMISSION_DUPLICATE_JSON_KEY');out[key]=value
        return out
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:require(False,'ADMISSION_NONFINITE_JSON'))
def stamp(value):
    require(isinstance(value,str) and re.fullmatch(r'20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z',value),'EXACT_UTC_ADMISSION_STAMP_REQUIRED')
    return datetime.fromisoformat(value.replace('Z','+00:00'))
def fields(body):
    result={}
    for line in body.splitlines():
        item=re.fullmatch(r'([A-Z][A-Z0-9_]*|real_orders_sent)=([^\r\n]*)',line)
        if item and item[1] in FIELD_KEYS:
            require(item[1] not in result,'ADMISSION_DUPLICATE_RECEIPT_FIELD')
            result[item[1]]=item[2]
    return result

def frozen_source_inventory(repo,sha,tree):
    """Derive immutable Git payload identity, never workspace allocation/modes."""
    require(re.fullmatch('[0-9a-f]{40}',sha or '') and re.fullmatch('[0-9a-f]{40}',tree or ''),
        'CAPACITY_SOURCE_EXACT_GIT_SHA_TREE_REQUIRED')
    environment={**os.environ,'GIT_OPTIONAL_LOCKS':'0','GIT_CONFIG_NOSYSTEM':'1',
        'GIT_CONFIG_GLOBAL':'/dev/null','GIT_NO_LAZY_FETCH':'1'}
    command=['git','--no-replace-objects','-C',str(Path(repo).absolute())]
    actual=subprocess.run([*command,'rev-parse',sha+'^{tree}'],env=environment,
        capture_output=True,check=True,timeout=30).stdout.strip().decode('ascii')
    require(actual==tree,'CAPACITY_SOURCE_FROZEN_TREE_REBOUND')
    listing=subprocess.run([*command,'ls-tree','-r','--full-tree','-z',sha],env=environment,
        capture_output=True,check=True,timeout=30).stdout
    require(bool(listing) and len(listing)<=16*1024**2,'CAPACITY_SOURCE_GIT_INVENTORY_REQUIRED')
    entries=[]
    for literal in listing.rstrip(b'\0').split(b'\0'):
        header,path=literal.split(b'\t',1);mode,kind,blob=header.decode('ascii').split(' ')
        name=path.decode('utf-8')
        require(mode in ('100644','100755') and kind=='blob' and re.fullmatch('[0-9a-f]{40}',blob)
            and name and not name.startswith('/') and all(part not in ('','.','..') for part in name.split('/')),
            'CAPACITY_SOURCE_GIT_MEMBER_MODE_OR_IDENTITY_INVALID')
        entries.append((name,mode,blob))
    require(len(entries)==len({name for name,_mode,_blob in entries}),'CAPACITY_SOURCE_GIT_PATH_DUPLICATED')
    process=subprocess.Popen([*command,'cat-file','--batch'],env=environment,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    files=[];cache={}
    try:
        for name,mode,blob in sorted(entries):
            if blob not in cache:
                process.stdin.write((blob+'\n').encode('ascii'));process.stdin.flush()
                header=process.stdout.readline(256).strip().split(b' ')
                require(len(header)==3 and header[0].decode('ascii')==blob and header[1]==b'blob'
                    and header[2].isdigit(),'CAPACITY_SOURCE_GIT_BLOB_HEADER_INVALID')
                size=int(header[2]);require(size<=128*1024**2,'CAPACITY_SOURCE_GIT_BLOB_LIMIT')
                sha256=hashlib.sha256();sha1=hashlib.sha1(b'blob '+str(size).encode('ascii')+b'\0');remaining=size
                while remaining:
                    raw=process.stdout.read(min(1024**2,remaining))
                    require(bool(raw),'CAPACITY_SOURCE_GIT_BLOB_TRUNCATED')
                    sha256.update(raw);sha1.update(raw);remaining-=len(raw)
                require(process.stdout.read(1)==b'\n' and sha1.hexdigest()==blob,
                    'CAPACITY_SOURCE_GIT_BLOB_BYTES_REBOUND')
                cache[blob]=(size,sha256.hexdigest())
            size,body_sha=cache[blob]
            files.append({'path':name,'mode':mode,'git_blob':blob,'bytes':size,'sha256':body_sha})
        process.stdin.close()
        require(process.wait(timeout=30)==0,'CAPACITY_SOURCE_GIT_READER_EXIT_RED')
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)
        if process.stdin and not process.stdin.closed:process.stdin.close()
        process.stdout.close()
    return {'schema':'porota.rc6.git-source-inventory.v1','source_sha':sha,'source_tree':tree,'files':files}

def verify_capacity_comparison_manifest(f,sha,tree,*,repo=ROOT):
    """Unknown graph costs block; owner/hash-only receipts cannot qualify V2."""
    peaks=document(f.get('CAPACITY_PEAKS_JSON','null'))
    require(type(peaks) is dict,'EXACT_COMPARABLE_CAPACITY_PEAKS_REQUIRED')
    v2=any(type(value) is dict and value.get('schema')=='porota.rc6.comparable-capacity-envelope.v2'
        for value in peaks.values())
    if peaks and not v2 and 'CAPACITY_COMPARISON_MANIFEST_JSON' not in f:
        # Legacy measured V1 remains a separate API; its real preflight still
        # checks exact producer/runner/metric and evidence digest at launch.
        require(all(type(value) is dict and value.get('schema')=='porota.rc6.comparable-capacity-peak.v1'
            for value in peaks.values()),'CAPACITY_UNKNOWN_COMPARISON_SCHEMA')
        return {'schema':'porota.rc6.capacity-source-admission.v1','scope':'MEASURED_V1_SEPARATE_NO_V2_GRAPH_CLAIM'}
    manifest=document(f.get('CAPACITY_COMPARISON_MANIFEST_JSON','null'))
    require(type(manifest) is dict and manifest.get('schema')==CAPACITY_COMPARISON_MANIFEST_SCHEMA
        and manifest.get('source_sha')==sha and manifest.get('source_tree')==tree,
        'CAPACITY_AUTHENTIC_COMPLETE_COMPARISON_MANIFEST_REQUIRED')
    expected=f.get('SOURCE_MANIFEST_SHA256')
    require(re.fullmatch('[0-9a-f]{64}',expected or '')
        and manifest.get('source_manifest_sha256')==expected,'CAPACITY_SOURCE_MANIFEST_DIGEST_REQUIRED')
    inventory=frozen_source_inventory(repo,sha,tree)
    require(hashlib.sha256(wire(inventory)).hexdigest()==expected,'CAPACITY_EXACT_GIT_SOURCE_MANIFEST_DIGEST_MISMATCH')
    from scripts.rc6_architectural_gates import validate_g1_files
    cheap=validate_g1_files(document(f.get('CHEAP_FILES_JSON','null')))
    require(manifest.get('cheap_files_sha256')==hashlib.sha256(wire(cheap)).hexdigest(),
        'CAPACITY_REVIEWED_CHEAP_GRAPH_FINGERPRINT_MISMATCH')
    unknown=manifest.get('unknown_components')
    require(type(unknown) is list and all(type(name) is str and 0<len(name)<=2048 for name in unknown)
        and len(unknown)==len(set(unknown)),'CAPACITY_UNKNOWN_GRAPH_COMPONENT_INVENTORY_REQUIRED')
    require(manifest.get('status') in ('BLOCKED_UNKNOWN_COMPONENTS','VERIFIED'),
        'CAPACITY_COMPARISON_MANIFEST_STATUS_REQUIRED')
    if unknown or manifest['status']=='BLOCKED_UNKNOWN_COMPONENTS':
        raise ValueError('CAPACITY_COMPARISON_UNKNOWN_COMPONENTS_BEFORE_BOOTSTRAP')
    records=manifest.get('records')
    require(type(records) is list and bool(records),'CAPACITY_HASH_ONLY_COMPARISON_EVIDENCE_BLOCKED')
    total=0;seen=set()
    for record in records:
        require(type(record) is dict and re.fullmatch(
            r'https://github\.com/mbalbo2023/Porota-trading/blob/[0-9a-f]{40}/[^?#\r\n]+',record.get('uri','')),
            'CAPACITY_ORIGINAL_IMMUTABLE_REPOSITORY_EVIDENCE_REQUIRED')
        require(record['uri'] not in seen and re.fullmatch('[0-9a-f]{64}',record.get('sha256','')),
            'CAPACITY_EVIDENCE_DUPLICATED_OR_DIGEST_MISSING');seen.add(record['uri'])
        require(('raw_utf8' in record)^('raw_base64' in record),'CAPACITY_ORIGINAL_RAW_BYTES_REQUIRED')
        if 'raw_utf8' in record:
            require(type(record['raw_utf8']) is str,'CAPACITY_ORIGINAL_RAW_UTF8_REQUIRED');raw=record['raw_utf8'].encode('utf-8')
        else:
            require(type(record['raw_base64']) is str,'CAPACITY_ORIGINAL_RAW_BASE64_REQUIRED')
            raw=base64.b64decode(record['raw_base64'],validate=True)
        total+=len(raw);require(0<len(raw) and total<=128*1024,'CAPACITY_COMPARISON_RAW_BOUND')
        require(hashlib.sha256(raw).hexdigest()==record['sha256'],'CAPACITY_ORIGINAL_RAW_DIGEST_MISMATCH')
    # Inline coherence is insufficient. No transport loader/exhaustive graph
    # predicate is claimed until original Git container+index+member provenance
    # and every bootstrap/producer cost are implemented and independently tested.
    raise ValueError('CAPACITY_VERIFIED_COMPARISON_PROOF_BLOCK_UNSUPPORTED')

def api(path):
    token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    require(bool(token),'ACTIONS_READONLY_TOKEN_REQUIRED')
    print(json.dumps({'scope':'READ_ONLY_GITHUB_SOURCE_OWNER_CONTROL',
        'resource':path.partition('?')[0],'method':'GET','payload_reads':0}),flush=True)
    request=urllib.request.Request('https://api.github.com/repos/'+REPO+path,
        headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
    with urllib.request.urlopen(request,timeout=30) as response:raw=response.read(CAP+1)
    require(len(raw)<=CAP,'ADMISSION_API_RESPONSE_BOUND')
    return document(raw)
def comment(url,issue,*,get=None):
    match=re.fullmatch(r'https://github\.com/mbalbo2023/Porota-trading/issues/'+str(issue)+r'#issuecomment-([0-9]+)',url or '')
    require(match is not None,'EXACT_OWNER_RECEIPT_URL_REQUIRED')
    row=(get or api)('/issues/comments/'+match[1])
    require(row['html_url']==url and row['issue_url'].endswith('/issues/'+str(issue))
        and row['user']['login']=='mbalbo2023','AUTHENTIC_REPOSITORY_OWNER_RECEIPT_REQUIRED')
    return row

def recovery_anchors(owner,*,get=None):
    require(owner==RECOVERY_OWNER,'EXACT_CONTROLLED_RECOVERY_SESSION_REQUIRED')
    result={}
    for issue,(identifier,created) in RECOVERY_ANCHORS.items():
        url='https://github.com/'+REPO+'/issues/'+str(issue)+'#issuecomment-'+str(identifier)
        row=comment(url,issue,get=get);f=fields(row['body'])
        require(row['id']==identifier and row['created_at']==row['updated_at']==created
            and hashlib.sha256(row['body'].encode()).hexdigest()==RECOVERY_BODY_SHA256
            and f.get('WORKSTREAM_ID')==WORKSTREAM and f.get('SESSION')==owner
            and f.get('WRITE_OWNER')==owner and f.get('INTEGRATION_OWNER')==owner
            and f.get('DEPLOY_OWNER')=='NOT_ACQUIRED' and f.get('RELEASED')=='false'
            and RECONCILED_OLD_OWNER in row['body'] and 'state blob'+RECONCILED_BLOB in row['body']
            and USER_STOP_LITERAL in row['body'],'AUTHENTIC_LITERAL_CONTROLLED_RECOVERY_ANCHOR_REQUIRED')
        result[issue]=row
    return result

def successor_anchors(owner,*,get=None):
    require(owner==SUCCESSOR_OWNER,'EXACT_ARCHITECTURAL_SUCCESSOR_SESSION_REQUIRED')
    result={}
    for issue,(identifier,created) in SUCCESSOR_ANCHORS.items():
        url='https://github.com/'+REPO+'/issues/'+str(issue)+'#issuecomment-'+str(identifier)
        row=comment(url,issue,get=get);f=fields(row['body'])
        require(row['id']==identifier and row['created_at']==row['updated_at']==created
            and hashlib.sha256(row['body'].encode()).hexdigest()==SUCCESSOR_BODY_SHA256
            and f.get('WORKSTREAM_ID')==WORKSTREAM and f.get('SESSION_SUCCESSOR')==owner
            and f.get('WRITE_OWNER')==owner and f.get('INTEGRATION_OWNER')==owner
            and f.get('DEPLOY_OWNER')=='NOT_ACQUIRED' and f.get('RELEASED')=='false'
            and RECONCILED_BLOB in row['body'] and RECONCILED_RAW_SHA256 in row['body']
            and all(str(anchor[0]) in row['body'] for anchor in RECOVERY_ANCHORS.values()),
            'AUTHENTIC_ARCHITECTURAL_SUCCESSOR_ANCHOR_REQUIRED')
        result[issue]=row
    return result

def effective_stamp(row):
    created=stamp(row['created_at']);updated=stamp(row['updated_at'])
    require(created<=updated,'COMMENT_CREATED_UPDATED_CUSTODY_INVALID')
    return updated

def recent(issue,anchor,now,*,get=None):
    # GitHub issue-comments `since` selects last-updated timestamps. A comment
    # created before the controlled anchor but edited afterwards is included.
    # No moving two-hour window and no silently truncated page are permitted.
    cutoff=stamp(anchor['created_at'])
    since=(cutoff-timedelta(seconds=1)).isoformat().replace('+00:00','Z')
    rows=[]
    ids=set()
    for page in range(1,COMMENT_PAGES+1):
        batch=(get or api)('/issues/'+str(issue)+'/comments?'+urllib.parse.urlencode({'since':since,'per_page':100,'page':page}))
        require(isinstance(batch,list) and len(batch)<=100,'ACTUAL_COMMENT_ARRAY_REQUIRED')
        for row in batch:
            require(row['id'] not in ids and row['issue_url'].endswith('/issues/'+str(issue)),
                'COMMENT_PAGINATION_DUPLICATED_OR_REBOUND')
            ids.add(row['id']);updated=effective_stamp(row)
            require(updated<=now,'OWNERSHIP_COMMENT_FUTURE_TIMESTAMP')
            if updated>=cutoff:rows.append(row)
        if len(batch)<100:break
    else:raise ValueError('OWNERSHIP_PAGINATION_INCOMPLETE_FAIL_CLOSED')
    require(anchor['id'] in ids,'ACTUAL_CONTROLLED_ANCHOR_MISSING_FROM_UPDATED_TIMELINE')
    return sorted(rows,key=lambda row:(effective_stamp(row),row['id']))

def actual_event(gate):
    require(os.environ.get('GITHUB_EVENT_NAME') in ('pull_request','workflow_dispatch'),'ACTUAL_PR_OR_SCOPED_DISPATCH_EVENT_REQUIRED')
    literal=os.environ['GITHUB_EVENT_PATH'];path=Path(literal)
    require(path.is_absolute() and literal==os.path.abspath(literal),'CANONICAL_ABSOLUTE_EVENT_PATH_REQUIRED')
    # Hold every directory component with NOFOLLOW. Read the file relative to
    # its held parent, then compare FD and literal-path all11 before/after.
    directory=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    held=[directory];parents=[]
    try:
        for component in path.parts[1:-1]:
            next_fd=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=directory)
            info=os.fstat(next_fd);require(stat.S_ISDIR(info.st_mode),'EVENT_ANCESTOR_DIRECTORY_REQUIRED')
            parents.append((directory,component,next_fd,{key:getattr(info,key) for key in ('st_dev','st_ino','st_uid','st_gid','st_mode')}))
            held.append(next_fd);directory=next_fd
        path_before=os.stat(path.name,dir_fd=directory,follow_symlinks=False)
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NOATIME|os.O_CLOEXEC,dir_fd=directory)
        try:
            info=os.fstat(fd);before={key:getattr(info,key) for key in STAT11}
            require(stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid() and info.st_nlink==1
                and 0<info.st_size<=CAP and before=={key:getattr(path_before,key) for key in STAT11},
                'ACTUAL_OWNED_PR_EVENT_FILE_REQUIRED')
            chunks=[];size=0
            while part:=os.read(fd,min(65536,CAP+1-size)):
                chunks.append(part);size+=len(part);require(size<=CAP,'PR_EVENT_CAPTURE_BOUND')
            raw=b''.join(chunks);after={key:getattr(os.fstat(fd),key) for key in STAT11}
            path_after={key:getattr(os.stat(path.name,dir_fd=directory,follow_symlinks=False),key) for key in STAT11}
            literal_after={key:getattr(path.lstat(),key) for key in STAT11}
            require(len(raw)==info.st_size and before==after==path_after==literal_after,
                'PR_EVENT_FD_AND_PATH_ALL11_CHANGED')
            for parent_fd,component,child_fd,identity in parents:
                require(identity=={key:getattr(os.fstat(child_fd),key) for key in identity}
                    =={key:getattr(os.stat(component,dir_fd=parent_fd,follow_symlinks=False),key) for key in identity},
                    'PR_EVENT_ANCESTOR_REBOUND')
            custody={'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
                'before_all11':before,'after_fd_all11':after,'after_path_all11':literal_after,
                'every_ancestor_opened_NOFOLLOW':True,'capture_flags':['O_NOFOLLOW','O_NOATIME','O_CLOEXEC'],
                'payload_producer_started':False}
        finally:os.close(fd)
    finally:
        for descriptor in reversed(held):os.close(descriptor)
    event=document(raw)
    if os.environ['GITHUB_EVENT_NAME']=='workflow_dispatch':
        expected_ref=DIAGNOSTIC_BRANCH if gate in DIAGNOSTIC_GATES else BRANCH
        require(event['repository']['id']==REPO_ID and event['repository']['full_name']==REPO
            and event['sender']['login']=='mbalbo2023' and event.get('inputs',{}).get('gate')==gate
            and event.get('ref') in (expected_ref,'refs/heads/'+expected_ref),
            'SOLE_OWNED_SCOPED_DISPATCH_EVENT_REQUIRED')
        return event,custody
    pr=event['pull_request']
    require(event['repository']['id']==REPO_ID and event['repository']['full_name']==REPO
        and event['number']==PR and pr['number']==PR and pr['state']=='open'
        and pr['draft'] is (gate!='predeploy')
        and pr['user']['login']=='mbalbo2023' and pr['head']['repo']['id']==REPO_ID
        and pr['head']['repo']['owner']['login']=='mbalbo2023' and pr['head']['ref']==BRANCH
        and pr['base']['repo']['id']==REPO_ID and pr['base']['ref']==BASE and pr['base']['sha']==BASE_SHA,
        'SOLE_OWNED_DRAFT_RECOVERY_PR_EVENT_REQUIRED')
    require((gate=='cheap' and event['action'] in ('opened','synchronize','reopened'))
        or (gate=='predeploy' and event['action']=='ready_for_review'),
        'PR_PUSH_AUTHORIZES_CHEAP_ONLY_READY_FOR_REVIEW_AUTHORIZES_G7_ONLY')
    return event,custody

def fresh_source(sha,tree,gate=None,*,get=None):
    require(re.fullmatch('[0-9a-f]{40}',sha or ''),'EXACT_HEAD_SOURCE_SHA_REQUIRED')
    reader=get or api
    repo=reader('');ref=reader('/git/ref/heads/'+BRANCH);commit=reader('/git/commits/'+sha);pr=reader('/pulls/'+str(PR))
    require(repo['id']==REPO_ID and ref['object']['sha']==sha and commit['sha']==sha
        and pr['number']==PR and pr['state']=='open' and pr['draft'] is (gate!='predeploy')
        and pr['user']['login']=='mbalbo2023' and pr['head']['sha']==sha and pr['head']['ref']==BRANCH
        and pr['head']['repo']['id']==REPO_ID and pr['head']['repo']['owner']['login']=='mbalbo2023'
        and pr['base']['repo']['id']==REPO_ID and pr['base']['ref']==BASE and pr['base']['sha']==BASE_SHA,
        'FRESH_EXACT_SOURCE_AUTHORITY_REBOUND')
    actual=commit['tree']['sha'];require(re.fullmatch('[0-9a-f]{40}',actual),'ACTUAL_TREE_SHA_REQUIRED')
    if tree:require(tree==actual,'EXACT_TREE_AUTHORITY_REBOUND')
    return actual

def launch_fields(row,sha,tree,session=None,gate=None,*,get=None):
    require(row['user']['login']=='mbalbo2023' and row['issue_url'].endswith('/issues/471')
        and row['created_at']==row['updated_at'],'IMMUTABLE_OWNER_LAUNCH_AUTHOR_REQUIRED')
    f=fields(row['body']);owner=f.get('SESSION_SUCCESSOR')
    gates=f.get('GATES_AUTHORIZED','').split(',')
    require(f.get('RC6_MATERIAL_AUTOMATIC_PR_AUTHORIZATION')=='APPROVED' and f.get('WORKSTREAM_ID')==WORKSTREAM
        and f.get('SOURCE_SHA')==sha and f.get('SOURCE_TREE')==tree
        and owner==SUCCESSOR_OWNER
        and (session is None or session==owner) and f.get('WRITE_OWNER')==owner
        and f.get('INTEGRATION_OWNER')==owner
        and f.get('DEPLOY_OWNER')=='NOT_ACQUIRED' and f.get('RELEASED')=='false'
        and f.get('MODE')=='PRODUCTION_PAPER / SIMULATION' and f.get('real_orders_sent')=='0'
        and len(gates)==1 and gates[0] in GATES and (gate is None or gates[0]==gate),
        'EXACT_IMMUTABLE_SINGLE_GATE_LAUNCH_RECEIPT_REQUIRED')
    require(re.fullmatch(r'https://github\.com/mbalbo2023/Porota-trading/issues/(?:471|473)#issuecomment-[0-9]+',f.get('CAUSE_EVIDENCE_URL','')),
        'EVIDENCED_NEW_SOURCE_REPAIR_REQUIRED_BEFORE_MATERIAL_RETRY')
    require(BRANCH in row['body'],'LAUNCH_RECEIPT_SOLE_SUCCESSOR_BRANCH_REQUIRED')
    comment(f['CAUSE_EVIDENCE_URL'],int(f['CAUSE_EVIDENCE_URL'].split('/issues/',1)[1].split('#',1)[0]),get=get)
    require(type(document(f.get('CAPACITY_PEAKS_JSON','null'))) is dict,'EXACT_COMPARABLE_CAPACITY_PEAKS_REQUIRED')
    if gates==['cheap']:
        cheap=document(f.get('CHEAP_FILES_JSON','null'))
        from scripts.rc6_architectural_gates import G1_HEAVY_FILES, validate_g1_files
        validate_g1_files(cheap)
        require(type(cheap) is list and cheap and len(cheap)==len(set(cheap))
            and all(type(path) is str and re.fullmatch(r'tests/test_[a-zA-Z0-9_]+\.py',path) for path in cheap)
            and G1_HEAVY_FILES.isdisjoint(cheap),'EXACT_REVIEWED_CHEAP_ONLY_SCOPE_REQUIRED')
    else:
        manifest=document(f.get('PREREQUISITES_MANIFEST_JSON','null'))
        require(type(manifest) is dict and manifest.get('source_sha')==sha and manifest.get('source_tree')==tree,
            'HEAVY_GATE_REQUIRES_SAME_SHA_ORDERED_ARTIFACT_MANIFEST')
    if gates==['predeploy']:
        require(f.get('RC6_PREDEPLOY_G7_AUTHORIZATION')=='APPROVED','PREDEPLOY_EXPLICIT_G7_AUTHORIZATION_REQUIRED')
    return f

def dedup_admission(sha,gate):
    current=int(os.environ['GITHUB_RUN_ID']);seen=[];ids=set()
    workflow='porota-predeploy-v2.yml' if gate=='predeploy' else 'rc6-unified-candidate-tests.yml'
    for page in range(1,RUN_PAGES+1):
        runs=api('/actions/workflows/'+workflow+'/runs?'+urllib.parse.urlencode(
            {'head_sha':sha,'per_page':100,'page':page}))
        batch=runs.get('workflow_runs')
        require(isinstance(batch,list) and len(batch)<=100 and isinstance(runs.get('total_count'),int)
            and runs['total_count']<RUN_PAGES*100,'WORKFLOW_RUN_PAGINATION_INCOMPLETE_FAIL_CLOSED')
        for run in batch:
            require(run['id'] not in ids and run['head_sha']==sha,'WORKFLOW_RUN_PAGINATION_DUPLICATED_OR_REBOUND')
            ids.add(run['id'])
            if run['id']==current:continue
            title='RC6 material '+gate+' @ '+sha
            if gate=='predeploy' or (gate=='cheap' and run['event']=='pull_request') or run.get('display_title')==title:
                seen.append({'id':run['id'],'event':run['event'],'status':run['status'],'conclusion':run['conclusion']})
        if len(batch)<100:break
    else:raise ValueError('WORKFLOW_RUN_PAGINATION_INCOMPLETE_FAIL_CLOSED')
    require(not seen,'PR_OR_GATE_ALREADY_ATTEMPTED_FOR_EXACT_SOURCE_REQUIRES_NEW_EVIDENCED_SHA')
    return {'prior_same_Source_PR_or_same_gate_runs':seen,'examined_run_ids':sorted(ids),
        'pagination_complete':True,'no_prior_run_reclassified_as_PASS':True}

def latest_writer(rows,issue,sha,tree,owner,now):
    records=[]
    for row in rows:
        f=fields(row.get('body',''))
        if any(key in f for key in ('WRITE_OWNER','INTEGRATION_OWNER','DEPLOY_OWNER','SOURCE_LEASE_EXPIRES_UTC','RELEASED')):
            require(row['user']['login']=='mbalbo2023' and row['issue_url'].endswith('/issues/'+str(issue)),
                'OWNERSHIP_RECORD_AUTHOR_OR_ISSUE_REBOUND')
            for key in ('WRITE_OWNER','INTEGRATION_OWNER'):
                if key in f and f[key] not in (owner,'RELEASED','NOT_ACQUIRED','NONE','null'):
                    # Expiration is evidence of lease expiry, never release.
                    # An active, unknown or unbounded foreign scope blocks.
                    expires=f.get('SOURCE_LEASE_EXPIRES_UTC')
                    require(f.get('RELEASED')=='true' or (expires is not None and stamp(expires)<=now),
                        'FOREIGN_ACTIVE_OR_UNKNOWN_WRITER_AFTER_SUCCESSOR_ANCHOR')
            require('DEPLOY_OWNER' not in f or f['DEPLOY_OWNER'] in ('NOT_ACQUIRED','RELEASED','NONE','null'),
                'ANY_DEPLOY_OWNER_AFTER_CONTROLLED_ANCHOR')
            records.append((row,f))
    require(records,'FRESH_OWNER_RECORDS_REQUIRED_BOTH_ISSUES')
    integrations=[(row,f) for row,f in records if 'INTEGRATION_OWNER' in f]
    require(integrations and integrations[-1][1]['INTEGRATION_OWNER']==owner,
        'LATEST_INTEGRATION_OWNER_NOT_CONTROLLED_RECOVERY')
    row,f=records[-1]
    require(row['user']['login']=='mbalbo2023' and row['issue_url'].endswith('/issues/'+str(issue))
        and f.get('WRITE_OWNER')==owner and f.get('SESSION_SUCCESSOR',f.get('SESSION'))==owner
        and f.get('DEPLOY_OWNER')=='NOT_ACQUIRED' and f.get('RELEASED')=='false'
        and f.get('SOURCE_SHA')==sha and f.get('SOURCE_TREE')==tree
        and f.get('MODE')=='PRODUCTION_PAPER / SIMULATION' and f.get('real_orders_sent')=='0',
        'LATEST_WRITER_OR_DEPLOY_OWNER_CONFLICT')
    expires=stamp(f.get('SOURCE_LEASE_EXPIRES_UTC'))
    require(stamp(row['created_at'])<=effective_stamp(row)<=now<expires
        and timedelta(0)<expires-stamp(row['created_at'])<=timedelta(minutes=20),
        'LATEST_WRITER_LEASE_EXPIRED_OR_UNBOUNDED')
    return {'comment_id':row['id'],'url':row['html_url'],'author':'mbalbo2023','fields':f,
        'body_sha256':hashlib.sha256(row['body'].encode()).hexdigest(),'lease_expires_utc':expires.isoformat().replace('+00:00','Z'),
        'latest_integration_owner_comment_id':integrations[-1][0]['id'],
        'all_post_anchor_owner_record_ids':[item[0]['id'] for item in records],
        'all_post_anchor_records_and_old_comment_edits_checked':True,'maximum_lease_seconds':1200}

def ops_admission(auth,owner,now,anchors,*,get=None):
    response=(get or api)('/contents/'+OPS+'?'+urllib.parse.urlencode({'ref':OPS_REF}))
    require(response['type']=='file' and response['path']==OPS and response['encoding']=='base64','ACTUAL_OPS_STATE_FILE_REQUIRED')
    raw=base64.b64decode(response['content'],validate=False)
    require(len(raw)<=CONTROL_CAP and hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==response['sha'],
        'EXACT_BOUNDED_OPS_STATE_GIT_BLOB_REQUIRED')
    state=document(raw);require(state['repository_id']==REPO_ID and state['repository']==REPO
        and state['deploy_owner'] is None
        and state['real_orders_sent']==0,'NO_DEPLOY_OWNER_ACTUAL_OPS_REQUIRED')
    lease=state['lease']
    if lease is not None:
        require(isinstance(lease,dict) and stamp(lease['expires_at'])<=now,'FOREIGN_OR_UNKNOWN_SUPERVISOR_LEASE_ACTIVE')
    writer=state['write_owner']
    require(writer is None or isinstance(writer,dict),'ACTUAL_OPS_WRITER_OBJECT_REQUIRED')
    if writer is not None and writer.get('session')!=owner and writer.get('released') is not True:
        require(auth.get('RECONCILED_OPS_STATE_BLOB')==response['sha']==RECONCILED_BLOB
            and auth.get('RECONCILED_OPS_STATE_SHA256')==hashlib.sha256(raw).hexdigest()==RECONCILED_RAW_SHA256
            and auth.get('RECONCILED_OPS_WRITE_OWNER')==writer.get('session')==RECONCILED_OLD_OWNER
            and auth.get('RECONCILIATION_USER_STOP_CONFIRMED')=='true',
            'FOREIGN_OPS_WRITER_REQUIRES_EXACT_RAW_AUTHENTIC_RECONCILIATION')
        proofs=[]
        for issue in (471,473):
            row=anchors[issue];body_digest=hashlib.sha256(row['body'].encode()).hexdigest()
            require(auth.get('RECONCILIATION_RECEIPT_'+str(issue))==row['html_url']
                and auth.get('RECONCILIATION_RECEIPT_'+str(issue)+'_SHA256')==body_digest==RECOVERY_BODY_SHA256
                and USER_STOP_LITERAL in row['body'] and 'state blob'+response['sha'] in row['body']
                and writer['session'] in row['body'],'EXACT_AUTHENTIC_USER_STOP_AND_STALE_STATE_RECEIPTS_REQUIRED')
            proofs.append({'issue':issue,'comment_id':row['id'],'url':row['html_url'],'body_sha256':body_digest,
                'unchanged_created_updated':row['created_at'],'user_stop_confirmation_literal_verified':True})
    else:proofs=[]
    return {'blob':response['sha'],'sha256':hashlib.sha256(raw).hexdigest(),'lease':lease,
        'write_owner':writer,'deploy_owner':None,'exact_reconciliation_receipts':proofs,'ops_modified':False,
        'supervisor_released_claimed':False,'lease_expiration_treated_as_release':False}

def owned_gate_control_scope(*,source_sha,source_tree,launch_receipt_url,owner_session,gate,now,get,
                             run_id=None,run_attempt=None):
    """API-only ownership observation; safe while one native producer is live.

    The injected reader has a single total HTTP deadline and records every
    returned document. It creates no child/thread and never reads checkout,
    event, Source or producer RAW. Full admission remains a separate barrier.
    """
    run_attempt=int(os.environ['GITHUB_RUN_ATTEMPT']) if run_attempt is None else run_attempt
    run_id=int(os.environ['GITHUB_RUN_ID']) if run_id is None else run_id
    require(gate in GATES and owner_session==SUCCESSOR_OWNER and type(run_attempt) is int and run_attempt==1
        and type(run_id) is int and run_id>0,'OWNED_GATE_FIRST_EXACT_SUCCESSOR_SCOPE_REQUIRED')
    anchors=recovery_anchors(RECOVERY_OWNER,get=get)
    successor=successor_anchors(owner_session,get=get)
    fresh_source(source_sha,source_tree,gate,get=get)
    launch=comment(launch_receipt_url,471,get=get)
    auth=launch_fields(launch,source_sha,source_tree,owner_session,gate,get=get)
    timelines={issue:recent(issue,successor[issue],now,get=get) for issue in (471,473)}
    owners={str(issue):latest_writer(timelines[issue],issue,source_sha,source_tree,owner_session,now)
        for issue in (471,473)}
    require(all(fields(row['body']).get('WORKSTREAM_ID')==WORKSTREAM
        for issue in (471,473) for row in timelines[issue]
        if row['id']==owners[str(issue)]['comment_id']),'OWNED_GATE_WORKSTREAM_REBOUND')
    ops=ops_admission(auth,owner_session,now,anchors,get=get)
    run=get('/actions/runs/'+str(run_id)+'/attempts/1')
    require(run.get('id')==run_id and run.get('run_attempt')==1 and run.get('head_sha')==source_sha
        and run.get('status')=='in_progress' and run.get('conclusion') is None
        and run.get('repository',{}).get('id')==REPO_ID
        and run.get('repository',{}).get('full_name')==REPO
        and run.get('head_repository',{}).get('id')==REPO_ID
        and run.get('path')==('.github/workflows/porota-predeploy-v2.yml' if gate=='predeploy'
            else '.github/workflows/rc6-unified-candidate-tests.yml'),
        'OWNED_GATE_ACTIONS_CANCELLED_OR_REBOUND')
    return {'source_sha':source_sha,'source_tree':source_tree,'owner_session':owner_session,'gate':gate,
        'run_id':run_id,'run_attempt':1,'read_utc':now.isoformat().replace('+00:00','Z'),
        'launch_receipt_url':launch_receipt_url,'launch_body_sha256':hashlib.sha256(launch['body'].encode()).hexdigest(),
        'fresh_ownership':owners,'ops':ops,'real_orders_sent':0,'DEPLOY_OWNER':'NOT_ACQUIRED'}

def admit(*,source_sha,source_tree=None,launch_receipt_url=None,owner_session=None,gate,now=None):
    require(os.getuid()==os.geteuid()>0 and os.environ.get('GITHUB_REPOSITORY')==REPO
        and os.environ.get('GITHUB_REPOSITORY_ID')==str(REPO_ID),'ACTUAL_NONROOT_CANONICAL_ACTIONS_REQUIRED')
    require(gate in GATES and os.environ.get('GITHUB_RUN_ATTEMPT')=='1','FIRST_SCOPED_RUN_ATTEMPT_REQUIRED_NO_BLIND_RERUN')
    event,event_custody=actual_event(gate)
    if os.environ['GITHUB_EVENT_NAME']=='pull_request':
        require(event['pull_request']['head']['sha']==source_sha,'EVENT_HEAD_SHA_REBOUND')
    else:require(event['inputs'].get('source_sha')==source_sha,'DISPATCH_HEAD_SHA_REBOUND')
    now=now or datetime.now(timezone.utc);tree=fresh_source(source_sha,source_tree,gate)
    anchors=recovery_anchors(RECOVERY_OWNER)  # unchanged user-stop/raw-ops authority
    successor=successor_anchors(SUCCESSOR_OWNER)
    timelines={issue:recent(issue,successor[issue],now) for issue in (471,473)}
    if launch_receipt_url:launch=comment(launch_receipt_url,471);auth=launch_fields(launch,source_sha,tree,owner_session,gate)
    else:
        matching=[]
        for row in timelines[471]:
            f=fields(row.get('body',''))
            if f.get('RC6_MATERIAL_AUTOMATIC_PR_AUTHORIZATION')=='APPROVED' and f.get('SOURCE_SHA')==source_sha and f.get('GATES_AUTHORIZED')==gate:
                auth=launch_fields(row,source_sha,tree,owner_session,gate);matching.append((row,auth))
        require(len(matching)==1,'ONE_EXACT_PREPUSH_LAUNCH_AUTHORITY_REQUIRED')
        launch,auth=matching[0]
    # Event delivery follows push: authorization must already exist.
    if os.environ['GITHUB_EVENT_NAME']=='pull_request':
        require(stamp(launch['created_at'])<=stamp(event['pull_request']['updated_at']),
            'DURABLE_LAUNCH_AUTHORIZATION_MUST_PRECEDE_PR_SOURCE_UPDATE')
    gates=auth['GATES_AUTHORIZED'].split(',')
    require(gate in gates,'GATE_NOT_AUTHORIZED_IN_IMMUTABLE_PREPUSH_SCOPE')
    owner=auth['SESSION_SUCCESSOR'];owners={str(issue):latest_writer(timelines[issue],issue,source_sha,tree,owner,now) for issue in (471,473)}
    ops=ops_admission(auth,owner,now,anchors)
    capacity_source=verify_capacity_comparison_manifest(auth,source_sha,tree)
    dedup=dedup_admission(source_sha,gate)
    return {'schema':'rc6.material-automatic-pr-admission.v2','status':'ADMITTED_NATIVE_NOT_STARTED','source_sha':source_sha,
        'source_tree':tree,'gate':gate,'owner_session':owner,'launch_receipt_url':launch['html_url'],
        'launch_receipt_created_at':launch['created_at'],'launch_body_sha256':hashlib.sha256(launch['body'].encode()).hexdigest(),
        'fresh_ownership':owners,'ops':ops,'dedup':dedup,'event_custody':event_custody,
        'gates_authorized':gates,'material_gates':[],
        'capacity_peaks':document(auth['CAPACITY_PEAKS_JSON']),
        'capacity_source_comparison':capacity_source,
        'cheap_files':document(auth['CHEAP_FILES_JSON']) if gate=='cheap' else None,
        'prerequisites_manifest':document(auth['PREREQUISITES_MANIFEST_JSON']) if gate!='cheap' else None,
        'Gov311_scope_pointer':auth.get('CANONICAL_GOV311_CUSTODY_RECEIPT_URL') or auth.get('SUPPLEMENTARY_GOV311_JUSTIFICATION_URL'),
        'omitted_Gov311_PASS_or_artifact_claimed':False,'source_truth':'CANONICAL_GITHUB_HEAD_COMMIT_TREE_PR',
        'workflow_ref':os.environ.get('GITHUB_WORKFLOW_REF'),'workflow_sha':os.environ.get('GITHUB_WORKFLOW_SHA'),
        'GITHUB_SHA_is_candidate_SHA_claimed':False,'read_utc':now.isoformat().replace('+00:00','Z'),
        'same_read_lease_valid_for_whole_long_gate_claimed':False,'real_orders_sent':0,
        'DEPLOY_OWNER':'NOT_ACQUIRED','canonical_artifact_pipeline':'PREDEPLOY_V2_ONLY','final_candidate_eligible':False}

def diagnostic_scope(gate,value):
    require(gate in DIAGNOSTIC_GATES and type(value) is dict,'EXACT_CAPACITY_DIAGNOSTIC_MODE_REQUIRED')
    image,hard_limit=(5*1024**3,512*1024**2) if gate=='capacity-probe' else (26*1024**3,20*1024**3)
    expected={'schema':'porota.rc6.capacity-diagnostic-scope.v1','mode':gate,
        'backing_image_bytes':image,'project_hard_limit_bytes':hard_limit,'residual_reserve_bytes':4*1024**3,
        'financial_tick_allowed':False,'qualification_claimed':False}
    require(value==expected and all(type(value[key]) is int for key in
        ('backing_image_bytes','project_hard_limit_bytes','residual_reserve_bytes')),
        'CAPACITY_DIAGNOSTIC_QUOTA_SCOPE_REBOUND')
    return expected

def diagnostic_launch_control_fields(row,sha,tree,session,gate):
    """Immutable diagnostic authority, without Git/Source/event/file access."""
    require(row['user']['login']=='mbalbo2023' and row['issue_url'].endswith('/issues/471')
        and row['created_at']==row['updated_at'],'IMMUTABLE_OWNER_DIAGNOSTIC_AUTHOR_REQUIRED')
    f=fields(row['body'])
    require(f.get('RC6_CAPACITY_DIAGNOSTIC_AUTHORIZATION')=='APPROVED'
        and f.get('WORKSTREAM_ID')==WORKSTREAM and f.get('DIAGNOSTIC_MODE')==gate
        and f.get('SOURCE_WIP')==DIAGNOSTIC_BRANCH and f.get('SOURCE_SHA')==sha and f.get('SOURCE_TREE')==tree
        and f.get('SESSION_SUCCESSOR')==session==SUCCESSOR_OWNER
        and f.get('WRITE_OWNER')==f.get('INTEGRATION_OWNER')==session
        and f.get('DEPLOY_OWNER')=='NOT_ACQUIRED' and f.get('RELEASED')=='false'
        and f.get('MODE')=='PRODUCTION_PAPER / SIMULATION' and f.get('real_orders_sent')=='0',
        'EXACT_IMMUTABLE_CAPACITY_DIAGNOSTIC_AUTHORITY_REQUIRED')
    require(all(re.fullmatch('[0-9a-f]{64}',f.get(key,'')) for key in
        ('READ_CONTRACT_SHA256','SOURCE_MANIFEST_SHA256')),'DIAGNOSTIC_PINNED_SOURCE_CONTRACT_DIGEST_REQUIRED')
    scope=diagnostic_scope(gate,document(f.get('CAPACITY_DIAGNOSTIC_SCOPE_JSON','null')))
    prior=document(f.get('CAPACITY_DIAGNOSTIC_PREREQUISITES_JSON','null'))
    require((gate=='capacity-probe' and prior is None) or (gate=='capacity-calibration' and type(prior) is dict),
        'DIAGNOSTIC_CALIBRATION_REQUIRES_ACTUAL_CAPABILITY_ARTIFACT')
    return f,scope,prior

def diagnostic_launch_fields(row,sha,tree,session,gate,*,repo=ROOT):
    f,scope,prior=diagnostic_launch_control_fields(row,sha,tree,session,gate)
    from scripts.rc6_architectural_gates import productive_contract
    require(f.get('READ_CONTRACT_SHA256')==productive_contract(repo),'DIAGNOSTIC_PRODUCTIVE_CONTRACT_REBOUND')
    require(hashlib.sha256(wire(frozen_source_inventory(repo,sha,tree))).hexdigest()==f.get('SOURCE_MANIFEST_SHA256'),
        'DIAGNOSTIC_FROZEN_GIT_SOURCE_MANIFEST_REBOUND')
    return f,scope,prior

def fresh_diagnostic_source(sha,tree,*,get=None):
    require(re.fullmatch('[0-9a-f]{40}',sha or '') and re.fullmatch('[0-9a-f]{40}',tree or ''),
        'DIAGNOSTIC_EXACT_WIP_SHA_TREE_REQUIRED')
    reader=get or api
    repository=reader('');ref=reader('/git/ref/heads/'+DIAGNOSTIC_BRANCH);commit=reader('/git/commits/'+sha)
    require(repository.get('id')==REPO_ID and repository.get('full_name')==REPO
        and repository.get('private') is False and ref.get('object',{}).get('sha')==sha
        and commit.get('sha')==sha and commit.get('tree',{}).get('sha')==tree,
        'DIAGNOSTIC_REMOTE_WIP_HEAD_TREE_REBOUND')
    lineage=[]
    for _ in range(32):
        if commit['sha']==RECOVERY_HEAD:break
        parents=commit.get('parents')
        require(type(parents) is list and len(parents)==1 and re.fullmatch('[0-9a-f]{40}',parents[0].get('sha','')),
            'DIAGNOSTIC_ISOLATED_FIX_FORWARD_LINEAGE_REQUIRED')
        lineage.append(commit['sha']);commit=reader('/git/commits/'+parents[0]['sha'])
        require(commit.get('sha')==parents[0]['sha'],'DIAGNOSTIC_GIT_PARENT_IDENTITY_REBOUND')
    else:raise ValueError('DIAGNOSTIC_FIX_FORWARD_LINEAGE_LIMIT')
    require(bool(lineage) and commit.get('tree',{}).get('sha')==RECOVERY_TREE,
        'DIAGNOSTIC_WIP_MUST_DESCEND_FROM_UNCHANGED_RECOVERY_HEAD')
    for number,head,branch in ((476,RECOVERY_HEAD,BRANCH),
        (477,GUARDS_HEAD,'governance/rc6-error-learning-runner-20261007')):
        pr=reader('/pulls/'+str(number))
        require(pr.get('number')==number and pr.get('state')=='open' and pr.get('draft') is True
            and pr.get('merged') is False and pr.get('merged_at') is None
            and pr.get('user',{}).get('login')=='mbalbo2023' and pr.get('head',{}).get('sha')==head
            and pr['head'].get('ref')==branch and pr['head'].get('repo',{}).get('id')==REPO_ID
            and pr.get('base',{}).get('ref')==(BASE if number==476 else BRANCH),
            'DIAGNOSTIC_MUST_NOT_MOVE_OR_PROMOTE_PR476_OR_PR477')
    return {'wip_branch':DIAGNOSTIC_BRANCH,'source_sha':sha,'source_tree':tree,
        'single_parent_fix_forward_lineage':lineage,'PR476_unchanged_sha':RECOVERY_HEAD,
        'PR477_unchanged_sha':GUARDS_HEAD,'repository_public_verified':True,
        'anonymous_source_origin':'https://github.com/'+REPO+'.git','qualification_claimed':False}

def diagnostic_control_binding(row):
    """Source pins established by full admission, reused by API-only polling."""
    require(type(row) is dict and row.get('schema')=='porota.rc6.capacity-diagnostic-admission.v1'
        and row.get('qualification_claimed') is False,'DIAGNOSTIC_FULL_ADMISSION_REQUIRED_FOR_MONITOR')
    result={key:row.get(key) for key in ('launch_body_sha256','source_manifest_sha256','read_contract_sha256',
        'scope','capability_prerequisite')}
    require(all(re.fullmatch('[0-9a-f]{64}',result.get(key) or '') for key in
        ('launch_body_sha256','source_manifest_sha256','read_contract_sha256')),
        'DIAGNOSTIC_FULL_ADMISSION_PINNED_DIGEST_REQUIRED')
    diagnostic_scope(row['gate'],result['scope'])
    return result

def owned_diagnostic_control_scope(*,source_sha,source_tree,launch_receipt_url,owner_session,gate,now,get,
                                   diagnostic_binding,run_id=None,run_attempt=None):
    """WIP control-plane read while Native is live; no checkout/Source/event."""
    run_attempt=int(os.environ['GITHUB_RUN_ATTEMPT']) if run_attempt is None else run_attempt
    run_id=int(os.environ['GITHUB_RUN_ID']) if run_id is None else run_id
    require(gate in DIAGNOSTIC_GATES and owner_session==SUCCESSOR_OWNER
        and type(run_attempt) is int and run_attempt==1 and type(run_id) is int and run_id>0,
        'OWNED_DIAGNOSTIC_FIRST_EXACT_SUCCESSOR_SCOPE_REQUIRED')
    anchors=recovery_anchors(RECOVERY_OWNER,get=get);successor=successor_anchors(owner_session,get=get)
    source=fresh_diagnostic_source(source_sha,source_tree,get=get)
    launch=comment(launch_receipt_url,471,get=get)
    auth,scope,prior=diagnostic_launch_control_fields(launch,source_sha,source_tree,owner_session,gate)
    require(type(diagnostic_binding) is dict and diagnostic_binding=={
        'launch_body_sha256':hashlib.sha256(launch['body'].encode()).hexdigest(),
        'source_manifest_sha256':auth['SOURCE_MANIFEST_SHA256'],'read_contract_sha256':auth['READ_CONTRACT_SHA256'],
        'scope':scope,'capability_prerequisite':prior},'OWNED_DIAGNOSTIC_FULL_ADMISSION_PINS_REBOUND')
    timelines={issue:recent(issue,successor[issue],now,get=get) for issue in (471,473)}
    owners={str(issue):latest_writer(timelines[issue],issue,source_sha,source_tree,owner_session,now)
        for issue in (471,473)}
    require(all(fields(row['body']).get('WORKSTREAM_ID')==WORKSTREAM
        for issue in (471,473) for row in timelines[issue]
        if row['id']==owners[str(issue)]['comment_id']),'OWNED_DIAGNOSTIC_WORKSTREAM_REBOUND')
    ops=ops_admission(auth,owner_session,now,anchors,get=get)
    run=get('/actions/runs/'+str(run_id)+'/attempts/1')
    require(run.get('id')==run_id and run.get('run_attempt')==1 and run.get('head_sha')==source_sha
        and run.get('head_branch')==DIAGNOSTIC_BRANCH and run.get('event')=='workflow_dispatch'
        and run.get('status')=='in_progress' and run.get('conclusion') is None
        and run.get('repository',{}).get('id')==REPO_ID and run['repository'].get('full_name')==REPO
        and run.get('head_repository',{}).get('id')==REPO_ID
        and run.get('path')=='.github/workflows/rc6-unified-candidate-tests.yml',
        'OWNED_DIAGNOSTIC_ACTIONS_CANCELLED_OR_REBOUND')
    return {'source_sha':source_sha,'source_tree':source_tree,'owner_session':owner_session,'gate':gate,
        'run_id':run_id,'run_attempt':1,'read_utc':now.isoformat().replace('+00:00','Z'),
        'launch_receipt_url':launch_receipt_url,'launch_body_sha256':diagnostic_binding['launch_body_sha256'],
        'diagnostic_binding':diagnostic_binding,'source':source,'fresh_ownership':owners,'ops':ops,
        'real_orders_sent':0,'DEPLOY_OWNER':'NOT_ACQUIRED','qualification_claimed':False}

def diagnostic_actions_origin(sha,gate):
    require(os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch' and os.environ.get('GITHUB_RUN_ATTEMPT')=='1'
        and os.environ.get('GITHUB_SHA')==os.environ.get('GITHUB_WORKFLOW_SHA')==sha
        and os.environ.get('GITHUB_REF')=='refs/heads/'+DIAGNOSTIC_BRANCH
        and os.environ.get('GITHUB_WORKFLOW_REF')==REPO+'/.github/workflows/rc6-unified-candidate-tests.yml@refs/heads/'+DIAGNOSTIC_BRANCH
        and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted' and os.environ.get('RUNNER_OS')=='Linux'
        and os.environ.get('RUNNER_ARCH')=='X64','DIAGNOSTIC_EXACT_FIRST_ATTEMPT_STANDARD_ACTIONS_REQUIRED')
    identifier=int(os.environ['GITHUB_RUN_ID']);run=api('/actions/runs/'+str(identifier))
    require(run.get('id')==identifier and run.get('head_sha')==sha and run.get('head_branch')==DIAGNOSTIC_BRANCH
        and run.get('run_attempt')==1 and run.get('event')=='workflow_dispatch'
        and run.get('status')=='in_progress' and run.get('repository',{}).get('id')==REPO_ID
        and run.get('path')=='.github/workflows/rc6-unified-candidate-tests.yml',
        'DIAGNOSTIC_ACTUAL_ACTIONS_RUN_ORIGIN_REBOUND')
    jobs=api('/actions/runs/'+str(identifier)+'/attempts/1/jobs?per_page=100')
    require(jobs.get('total_count')==1 and type(jobs.get('jobs')) is list and len(jobs['jobs'])==1,
        'DIAGNOSTIC_SINGLE_ACTIONS_JOB_REQUIRED')
    job=jobs['jobs'][0]
    require(job.get('run_id')==identifier and type(job.get('id')) is int and job['id']>0
        and job.get('status')=='in_progress' and job.get('labels')==['ubuntu-24.04']
        and job.get('runner_group_name')=='GitHub Actions'
        and type(job.get('runner_name')) is str and job['runner_name'].startswith('GitHub Actions '),
        'DIAGNOSTIC_ACTUAL_STANDARD_HOSTED_RUNNER_REQUIRED')
    return {'run':run,'job':job,'dedup':dedup_admission(sha,gate)}

def admit_diagnostic(*,source_sha,source_tree,launch_receipt_url,owner_session,gate,now=None):
    require(os.getuid()==os.geteuid()>0 and os.environ.get('GITHUB_REPOSITORY')==REPO
        and os.environ.get('GITHUB_REPOSITORY_ID')==str(REPO_ID),'ACTUAL_NONROOT_CANONICAL_ACTIONS_REQUIRED')
    require(gate in DIAGNOSTIC_GATES and owner_session==SUCCESSOR_OWNER,
        'CAPACITY_DIAGNOSTIC_SEPARATE_WIP_AUTHORITY_REQUIRED')
    event,custody=actual_event(gate)
    require(event.get('inputs',{}).get('source_sha')==source_sha and event['inputs'].get('source_tree')==source_tree
        and event['inputs'].get('launch_receipt_url')==launch_receipt_url
        and event['inputs'].get('owner_session')==owner_session,'DIAGNOSTIC_EVENT_LAUNCH_BINDING_REBOUND')
    now=now or datetime.now(timezone.utc);source=fresh_diagnostic_source(source_sha,source_tree)
    origin=diagnostic_actions_origin(source_sha,gate)
    recovery=recovery_anchors(RECOVERY_OWNER);successor=successor_anchors(owner_session)
    timelines={issue:recent(issue,successor[issue],now) for issue in (471,473)}
    launch=comment(launch_receipt_url,471)
    auth,scope,prior=diagnostic_launch_fields(launch,source_sha,source_tree,owner_session,gate)
    owners={str(issue):latest_writer(timelines[issue],issue,source_sha,source_tree,owner_session,now) for issue in (471,473)}
    ops=ops_admission(auth,owner_session,now,recovery)
    return {'schema':'porota.rc6.capacity-diagnostic-admission.v1','status':'ADMITTED_DIAGNOSTIC_NOT_STARTED',
        'source_sha':source_sha,'source_tree':source_tree,'gate':gate,'owner_session':owner_session,
        'launch_receipt_url':launch_receipt_url,'launch_body_sha256':hashlib.sha256(launch['body'].encode()).hexdigest(),
        'source_manifest_sha256':auth['SOURCE_MANIFEST_SHA256'],'read_contract_sha256':auth['READ_CONTRACT_SHA256'],
        'scope':scope,'capability_prerequisite':prior,'fresh_ownership':owners,'ops':ops,'event_custody':custody,
        'source':source,'actions_origin':origin,'material_gates':[],'qualification_claimed':False,
        'mode':'PRODUCTION_PAPER / SIMULATION','real_orders_sent':0,'real_routes':'NOT_CALLED',
        'ppi_watch':'UNTOUCHED','DEPLOY_OWNER':'NOT_ACQUIRED','runtime_validated':False,'final_candidate_eligible':False}

def publish_new(root,row):
    root=Path(os.path.abspath(root));require(not any(p.is_symlink() for p in (root,*root.parents))
        and not os.path.lexists(root),'FRESH_ADMISSION_CONTROL_NAMESPACE_REQUIRED')
    root.mkdir(mode=0o700);raw=wire(row);require(len(raw)<=CONTROL_CAP,'ADMISSION_CONTROL_LIMIT')
    fd=os.open(root/'admission.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    return root/'admission.json'

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source-sha',required=True);parser.add_argument('--source-tree')
    parser.add_argument('--launch-receipt-url');parser.add_argument('--owner-session');parser.add_argument('--gate',choices=GATES+DIAGNOSTIC_GATES,required=True)
    parser.add_argument('--control-root',type=Path,required=True);args=parser.parse_args()
    try:
        controller=admit_diagnostic if args.gate in DIAGNOSTIC_GATES else admit
        row=controller(source_sha=args.source_sha,source_tree=args.source_tree,launch_receipt_url=args.launch_receipt_url,
            owner_session=args.owner_session,gate=args.gate);path=publish_new(args.control_root,row)
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a') as stream:
                for key in ('source_sha','source_tree','owner_session','launch_receipt_url'):stream.write(key+'='+row[key]+'\n')
                stream.write('material_gates_json='+json.dumps(row['material_gates'],separators=(',',':'))+'\n')
                stream.write('admission_path='+str(path)+'\ncontrol_root='+str(path.parent)+'\n')
        print(json.dumps({'status':row['status'],'source_sha':row['source_sha'],'gate':row['gate'],'real_orders_sent':0}),flush=True)
        return 0
    except BaseException as error:
        literal=str(error).partition(':')[0];reason=literal if re.fullmatch('[A-Z][A-Z0-9_]{0,191}',literal) else 'NON_LITERAL_ADMISSION_FAILURE'
        row={'status':'RED','reason':reason,'class':type(error).__name__,'gate':args.gate,'native_started':False,'payload_reads':0,'real_orders_sent':0}
        if not os.path.lexists(args.control_root):publish_new(args.control_root,row)
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a') as stream:stream.write('control_root='+str(args.control_root)+'\n')
        print(json.dumps(row,sort_keys=True),flush=True);return 1
if __name__=='__main__':raise SystemExit(main())
