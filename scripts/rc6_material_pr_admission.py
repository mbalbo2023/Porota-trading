"""Read-only admission for an exact owner-authorized RC6 candidate.

Launch authority is immutable and exact-Source; live ownership is re-read. This
controller never dispatches, edits a ref, acquires deploy ownership or runs gates.
"""
import argparse
import base64
from collections import OrderedDict
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
import urllib.error
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
GATES=('cheap','focal311','focal312','BIG-browser','Horizon','G5P-acquire','G5P-certify',
       'full-gov311','full-gov312','predeploy')
DIAGNOSTIC_GATES=('capacity-probe','capacity-calibration')
DIAGNOSTIC_BRANCH='wip/rc6-architectural-rca-20261008-1606UTC'
DIAGNOSTIC_AUTHORIZATION_SCHEMA='porota.rc6.capacity-diagnostic-launch.v2'
DIAGNOSTIC_ADMISSION_SCHEMA='porota.rc6.capacity-diagnostic-admission.v2'
DIAGNOSTIC_CONTRACT_LIFECYCLE='IMMUTABLE_DECISION_LIVE_OWNER_REVOCATION_V1'
RECOVERY_HEAD='dfc240a478cf08e0c6a9b0ba1b760307b09a9565'
RECOVERY_TREE='192ea26348c8fe10db55857cdd6029edd8344708'
GUARDS_HEAD='756d37b93aa26bb6395dac481bf3c2dda9d034d7'
OPS='docs/automation/rc6-night-20261005/state.json'
OPS_REF='ops/rc6-night-supervisor-20261005'
CAP=1024*1024
_HTTP_REVALIDATION_CACHE=OrderedDict()
_HTTP_REVALIDATION_LOCK=threading.Lock()
_HTTP_REVALIDATION_BYTES=0
_HTTP_REVALIDATION_MAX_BYTES=32*CAP
_HTTP_REVALIDATION_MAX_ENTRIES=128
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
    'CAPACITY_DIAGNOSTIC_PREREQUISITES_JSON','AUTHORIZATION_SCHEMA','SOURCE_PR','SOURCE_BRANCH',
    'BASE_BRANCH','BASE_SHA','BASE_TREE','OWNER_RECEIPT_471','OWNER_RECEIPT_473',
    'OWNER_RECEIPT_471_SHA256','OWNER_RECEIPT_473_SHA256','BRANCH','SUCCESSION_KIND',
    'PREDECESSOR_OWNER','PREDECESSOR_RELEASED','PREDECESSOR_RECEIPT_471','PREDECESSOR_RECEIPT_473',
    'HEAVY_GATES_AUTHORIZED','G0_G8_QUALIFICATION','FINAL_CANDIDATE_ELIGIBLE',
    'RC6_NATIVE_PREREQUISITES_AUTHORIZATION','NATIVE_PREREQUISITES_PLAN_SHA256','DEVELOPMENT_ORIGIN_JSON',
    'RC6_PRIVATE_ORIGINAL_CAS_COMPARISON_AUTHORIZATION','CAS_COMPARISON_TEMPLATE_JSON',
    'RC6_CONTRACT_REVISION_AUTHORIZATION','CONTRACT_DOCUMENT_SHA256','CONTRACT_ACCEPTANCE_SCHEMA',
    'CONTRACT_DECISION_ORIGIN','CONTRACT_REVISION','CONTRACT_SCOPE',
    'CONTRACT_GATES_ALREADY_GREEN','NATIVE_EQUIVALENCE_FROM_APPROVAL_CLAIMED','DEPLOY_AUTHORIZATION',
    'CONTRACT_ACCEPTANCE_LIFECYCLE','CONTRACT_REVISION_REVOKED','CONTRACT_DECISION_REVOKED',
    'G5A_REVISION_APPROVAL_URL','G5A_CAPABILITY_REFERENCE_JSON','G5A_BOOTSTRAP_REFERENCE_JSON',
    'RC6_G5A_ROOT_SETUP_AUTHORIZATION','RELEASE_PROFILE','G5P_REVISION_APPROVAL_URL',
    'G5P_CONTRACT_DOCUMENT_SHA256','RC6_G5P_MATERIAL_AUTHORIZATION',
    'G5P_ACQUISITION_REFERENCE_JSON','PATHS','PR','SCOPE','PAIR_ISSUE',
    'APPLY_RUN','APPLY_ATTEMPT','APPLY_ARTIFACT','APPLY_ZIP_SHA256','APPLY_JSON_SHA256',
    'POST_RUN','POST_ARTIFACT','PAPER'}
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
CANDIDATE_AUTHORIZATION_SCHEMA='porota.rc6.candidate-launch.v1'
CLOSURE_OWNER='CODEX_RC6_ARCHITECTURE_CLOSURE_20261009'
CLOSURE_BODY_SHA256='148283c2536677ff09cae1ad5a1ce015fdd02d8bf97f4fa38c3c9910a8634d00'
CLOSURE_ANCHORS={471:(6086143803,'2026-10-09T17:43:32Z'),473:(6086144145,'2026-10-09T17:43:34Z')}
PREDECESSOR_BODY_SHA256='1e44f471ed5c7b1d10a6dba9b29cdd8bc1bf959b8164f06a7d029ac00ff7fc01'
PREDECESSOR_ANCHORS={471:(6069926329,'2026-10-08T22:04:11Z'),473:(6069926700,'2026-10-08T22:04:12Z')}
EXPLICIT_SINGLE_WRITER_LITERAL='Sí, todas están detenidas; esta es la única sesión escritora.'
RELEASED_SUCCESSION_KIND='EXPLICIT_USER_AUTHORIZATION_AFTER_AUTHENTIC_RELEASE'
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
    # Consume the frozen inventory captured above once. The verifier fetches
    # original Git/container/index/member bytes and recomputes every cost;
    # dynamic writers still block until native aggregate custody is proved.
    from scripts.rc6_capacity_comparison import _verify_with_fresh_inventory
    result=_verify_with_fresh_inventory(manifest,peaks,inventory,cheap,repo=repo)
    require(result.get('scope')=='ORIGINAL_RC6_STAGE_COMPLETE',
        'CAPACITY_DIAGNOSTIC_PAYLOADS_NOT_RC6_ADMISSION')
    return result

def _conditional_cache_get(key):
    with _HTTP_REVALIDATION_LOCK:
        entry=_HTTP_REVALIDATION_CACHE.get(key)
        if entry is not None:_HTTP_REVALIDATION_CACHE.move_to_end(key)
        return entry


def _conditional_cache_put(key,etag,raw):
    global _HTTP_REVALIDATION_BYTES
    with _HTTP_REVALIDATION_LOCK:
        previous=_HTTP_REVALIDATION_CACHE.pop(key,None)
        if previous is not None:_HTTP_REVALIDATION_BYTES-=len(previous[1])
        _HTTP_REVALIDATION_CACHE[key]=(etag,raw)
        _HTTP_REVALIDATION_BYTES+=len(raw)
        while (_HTTP_REVALIDATION_BYTES>_HTTP_REVALIDATION_MAX_BYTES or
               len(_HTTP_REVALIDATION_CACHE)>_HTTP_REVALIDATION_MAX_ENTRIES):
            _discard,old=_HTTP_REVALIDATION_CACHE.popitem(last=False)
            _HTTP_REVALIDATION_BYTES-=len(old[1])


def api(path):
    token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    require(bool(token),'ACTIONS_READONLY_TOKEN_REQUIRED')
    # G5P's long owned run rechecks the same immutable comments and timeline
    # pages many times. A server-confirmed 304 is a fresh read of unchanged
    # bytes, not a local TTL or permission to ignore a changed owner/release.
    conditional=os.environ.get('RC6_GATE') in ('G5P-acquire','G5P-certify')
    key=(hashlib.sha256(token.encode()).digest(),path) if conditional else None
    cached=_conditional_cache_get(key) if conditional else None
    headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
             'X-GitHub-Api-Version':'2022-11-28'}
    if cached is not None:headers['If-None-Match']=cached[0]
    print(json.dumps({'scope':'READ_ONLY_GITHUB_SOURCE_OWNER_CONTROL',
        'resource':path.partition('?')[0],'method':'GET','payload_reads':0}),flush=True)
    request=urllib.request.Request('https://api.github.com/repos/'+REPO+path,
        headers=headers)
    try:
        with urllib.request.urlopen(request,timeout=30) as response:
            status=getattr(response,'status',200)
            if status==304:
                require(cached is not None,'ADMISSION_304_WITHOUT_VERIFIED_CACHE')
                return document(cached[1])
            require(status==200,'ADMISSION_API_HTTP_200_OR_VERIFIED_304_REQUIRED')
            raw=response.read(CAP+1)
            etag=response.headers.get('ETag') if conditional else None
    except urllib.error.HTTPError as error:
        if error.code==304:
            require(cached is not None,'ADMISSION_304_WITHOUT_VERIFIED_CACHE')
            return document(cached[1])
        raise
    require(len(raw)<=CAP,'ADMISSION_API_RESPONSE_BOUND')
    value=document(raw)
    if conditional and type(etag) is str and re.fullmatch(r'(?:W/)?"[A-Za-z0-9._:/+=-]{1,200}"',etag):
        _conditional_cache_put(key,etag,raw)
    return value
def comment(url,issue,*,get=None):
    match=re.fullmatch(r'https://github\.com/mbalbo2023/Porota-trading/issues/'+str(issue)+r'#issuecomment-([0-9]+)',url or '')
    require(match is not None,'EXACT_OWNER_RECEIPT_URL_REQUIRED')
    row=(get or api)('/issues/comments/'+match[1])
    require(row['html_url']==url and row['issue_url'].endswith('/issues/'+str(issue))
        and row['user']['login']=='mbalbo2023','AUTHENTIC_REPOSITORY_OWNER_RECEIPT_REQUIRED')
    return row

def immutable_comment_identity(row):
    """Bind one comment across REST collection and single-resource shapes.

    Decorative app metadata can differ by endpoint. The comment bytes,
    timestamps, repository URLs, author ID/login and app ID/slug cannot.
    """
    require(type(row) is dict and type(row.get('id')) is int and row['id']>0
        and all(type(row.get(key)) is str for key in
            ('body','created_at','updated_at','html_url','issue_url')),
        'IMMUTABLE_COMMENT_IDENTITY_REQUIRED')
    user=row.get('user')
    require(type(user) is dict and type(user.get('id')) is int and user['id']>0
        and user.get('login')=='mbalbo2023','IMMUTABLE_COMMENT_AUTHOR_ID_REQUIRED')
    app=row.get('performed_via_github_app')
    require(app is None or (type(app) is dict and type(app.get('id')) is int and app['id']>0
        and type(app.get('slug')) is str and bool(app['slug'])),
        'IMMUTABLE_COMMENT_APP_ID_REQUIRED')
    return (row['id'],row['body'],row['created_at'],row['updated_at'],row['html_url'],
        row['issue_url'],row.get('url'),user['id'],user['login'],
        None if app is None else (app['id'],app['slug']))

def same_immutable_comment(left,right):
    return immutable_comment_identity(left)==immutable_comment_identity(right)

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

def administrative_anchors(owner,*,get=None,now=None):
    """Authenticate succession; neither an expired lease nor a run grants it.

    This pinned owner transfer grants development only. Every future launch
    still needs an immutable, single-gate receipt and current dual leases.
    Keep the older recovery anchors intact as the stale ops-state authority.
    """
    if owner!=CLOSURE_OWNER:
        return released_successor_anchors(owner,get=get,now=now)
    successor_anchors(SUCCESSOR_OWNER,get=get)
    result={}
    for issue,(identifier,created) in CLOSURE_ANCHORS.items():
        old_id,old_created=PREDECESSOR_ANCHORS[issue]
        old=comment('https://github.com/'+REPO+'/issues/'+str(issue)+'#issuecomment-'+str(old_id),issue,get=get)
        previous=fields(old['body'])
        require(old['id']==old_id and old['created_at']==old['updated_at']==old_created
            and hashlib.sha256(old['body'].encode()).hexdigest()==PREDECESSOR_BODY_SHA256
            and previous.get('WRITE_OWNER')==previous.get('INTEGRATION_OWNER')==SUCCESSOR_OWNER
            and previous.get('RELEASED')=='false' and previous.get('DEPLOY_OWNER')=='NOT_ACQUIRED',
            'AUTHENTIC_UNRELEASED_PREDECESSOR_REQUIRED')
        row=comment('https://github.com/'+REPO+'/issues/'+str(issue)+'#issuecomment-'+str(identifier),issue,get=get)
        f=fields(row['body'])
        require(row['id']==identifier and row['created_at']==row['updated_at']==created
            and hashlib.sha256(row['body'].encode()).hexdigest()==CLOSURE_BODY_SHA256
            and f.get('WORKSTREAM_ID')==WORKSTREAM
            and f.get('SESSION_SUCCESSOR')==f.get('WRITE_OWNER')==f.get('INTEGRATION_OWNER')==owner
            and f.get('DEPLOY_OWNER')=='NOT_ACQUIRED' and f.get('RELEASED')=='false'
            and f.get('SUCCESSION_KIND')=='ADMINISTRATIVE_EXPLICIT_USER_AUTHORIZATION'
            and f.get('PREDECESSOR_OWNER')==SUCCESSOR_OWNER and f.get('PREDECESSOR_RELEASED')=='false'
            and all(f.get('PREDECESSOR_RECEIPT_'+str(number))==str(PREDECESSOR_ANCHORS[number][0])
                for number in (471,473))
            and f.get('RECONCILIATION_USER_STOP_CONFIRMED')=='true'
            and f.get('HEAVY_GATES_AUTHORIZED')=='false' and f.get('G0_G8_QUALIFICATION')=='false'
            and f.get('FINAL_CANDIDATE_ELIGIBLE')=='false'
            and f.get('MODE')=='PRODUCTION_PAPER / SIMULATION' and f.get('real_orders_sent')=='0'
            and EXPLICIT_SINGLE_WRITER_LITERAL in row['body']
            and RECONCILED_BLOB in row['body'] and RECONCILED_RAW_SHA256 in row['body'],
            'AUTHENTIC_EXPLICIT_USER_SUCCESSION_REQUIRED')
        result[issue]=row
    require(result[471]['body']==result[473]['body'],'ADMINISTRATIVE_DUAL_RECEIPT_BODY_MISMATCH')
    return result

def released_successor_anchors(owner,*,get=None,now=None):
    """Authenticate an actual released succession without adopting an old ID.

    A transfer is only development ownership. Its two immutable, owner-authored
    comments must reference two genuine explicit releases of the predecessor.
    Keep the pinned administrative checkpoint as the history floor: a newer
    anchor must never hide an old unresolved writer or an edited old comment.
    """
    require(isinstance(owner,str) and re.fullmatch(r'CODEX_[A-Z0-9_]{1,159}',owner)
        and owner not in (RECOVERY_OWNER,SUCCESSOR_OWNER,CLOSURE_OWNER),
        'EXACT_RELEASED_SUCCESSOR_SESSION_REQUIRED')
    current=now or datetime.now(timezone.utc)
    history=administrative_anchors(CLOSURE_OWNER,get=get)
    timelines={issue:recent(issue,history[issue],current,get=get) for issue in (471,473)}
    candidates=[row for row in timelines[471]
        if fields(row.get('body','')).get('SESSION_SUCCESSOR')==owner
        and fields(row.get('body','')).get('SUCCESSION_KIND')==RELEASED_SUCCESSION_KIND]
    require(candidates,'AUTHENTIC_RELEASED_SUCCESSOR_TRANSFER_REQUIRED')
    # A renewal can repeat the scope; the first transfer establishes identity.
    first=min(candidates,key=lambda row:(stamp(row['created_at']),row['id']))
    scope=fields(first['body']);number=scope.get('OWNER_RECEIPT_473','')
    require(re.fullmatch('[1-9][0-9]{0,9}',number), 'RELEASED_SUCCESSOR_DUAL_TRANSFER_REQUIRED')
    twin=comment('https://github.com/'+REPO+'/issues/473#issuecomment-'+number,473,get=get)
    # Captured API documents are immutable inputs to historical replay. Add
    # internal history pointers only to copies, never to the response cache.
    result={471:dict(first),473:dict(twin)}
    require(any(row['id']==twin['id'] and same_immutable_comment(row,twin) for row in timelines[473]),
        'RELEASED_SUCCESSOR_TRANSFER_MISSING_FROM_COMPLETE_TIMELINE')
    require(scope.get('OWNER_RECEIPT_473_SHA256')==hashlib.sha256(twin['body'].encode()).hexdigest(),
        'RELEASED_SUCCESSOR_TRANSFER_PAIR_DIGEST_REQUIRED')
    bindings=('WORKSTREAM_ID','SESSION_SUCCESSOR','WRITE_OWNER','INTEGRATION_OWNER','DEPLOY_OWNER',
        'RELEASED','BRANCH','BASE_SHA','SOURCE_SHA','SOURCE_TREE','PREDECESSOR_OWNER',
        'PREDECESSOR_RELEASED','PREDECESSOR_RECEIPT_471','PREDECESSOR_RECEIPT_473',
        'SUCCESSION_KIND','MODE','real_orders_sent','HEAVY_GATES_AUTHORIZED',
        'G0_G8_QUALIFICATION','FINAL_CANDIDATE_ELIGIBLE')
    previous=scope.get('PREDECESSOR_OWNER','')
    require(previous!=owner and re.fullmatch(r'CODEX_[A-Z0-9_]{1,159}',previous)
        and re.fullmatch(r'WS-RC6-[A-Z0-9-]{1,179}',scope.get('WORKSTREAM_ID','')),
        'RELEASED_SUCCESSOR_EXACT_PREDECESSOR_AND_WORKSTREAM_REQUIRED')
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,199}',scope.get('BRANCH',''))
        and scope['BRANCH'] not in ('main',BASE,BRANCH,DIAGNOSTIC_BRANCH)
        and all(part not in ('','.','..') and not part.endswith(('.','.lock'))
            for part in scope['BRANCH'].split('/')) and '..' not in scope['BRANCH']
        and re.fullmatch('[0-9a-f]{40}',scope.get('BASE_SHA',''))
        and scope.get('BASE_SHA')==scope.get('SOURCE_SHA')
        and re.fullmatch('[0-9a-f]{40}',scope.get('SOURCE_TREE','')),
        'RELEASED_SUCCESSOR_EXACT_ISOLATED_BASE_REQUIRED')
    for issue,row in result.items():
        f=fields(row['body'])
        require(row['user']['login']=='mbalbo2023'
            and row['issue_url'].endswith('/issues/'+str(issue))
            and row['html_url']=='https://github.com/'+REPO+'/issues/'+str(issue)
                +'#issuecomment-'+str(row['id'])
            and row['created_at']==row['updated_at']
            and all(f.get(key)==scope.get(key) for key in bindings)
            and f.get('SESSION_SUCCESSOR')==f.get('WRITE_OWNER')==f.get('INTEGRATION_OWNER')==owner
            and f.get('DEPLOY_OWNER')=='NOT_ACQUIRED' and f.get('RELEASED')=='false'
            and f.get('SUCCESSION_KIND')==RELEASED_SUCCESSION_KIND
            and f.get('PREDECESSOR_RELEASED')=='true'
            and f.get('HEAVY_GATES_AUTHORIZED')==f.get('G0_G8_QUALIFICATION')
                ==f.get('FINAL_CANDIDATE_ELIGIBLE')=='false'
            and f.get('MODE')=='PRODUCTION_PAPER / SIMULATION' and f.get('real_orders_sent')=='0',
            'AUTHENTIC_UNEDITED_RELEASED_SUCCESSOR_TRANSFER_REQUIRED')
        release_id=f.get('PREDECESSOR_RECEIPT_'+str(issue),'')
        require(re.fullmatch('[1-9][0-9]{0,9}',release_id),
            'RELEASED_SUCCESSOR_EXPLICIT_PREDECESSOR_RECEIPT_REQUIRED')
        release=comment('https://github.com/'+REPO+'/issues/'+str(issue)+'#issuecomment-'+release_id,
            issue,get=get);old=fields(release['body'])
        require(release['created_at']==release['updated_at']
            and any(item['id']==release['id'] and same_immutable_comment(item,release) for item in timelines[issue])
            and old.get('SESSION_SUCCESSOR',old.get('SESSION'))==old.get('WRITE_OWNER')==previous
            and old.get('INTEGRATION_OWNER',previous)==previous
            and old.get('DEPLOY_OWNER')=='NOT_ACQUIRED' and old.get('RELEASED')=='true'
            and old.get('SOURCE_SHA')==scope['BASE_SHA'] and old.get('SOURCE_TREE')==scope['SOURCE_TREE']
            and old.get('MODE')==f['MODE'] and old.get('real_orders_sent')=='0'
            and stamp(release['created_at'])<=min(stamp(item['created_at']) for item in result.values()),
            'AUTHENTIC_DUAL_PREDECESSOR_RELEASE_BEFORE_TRANSFER_REQUIRED')
        # The digest does not grant release. It binds the row already proven
        # authentic above; release status is never inferred from lease expiry.
        row['_rc6_history_anchor']=history[issue]
    releases=[fields(next(row['body'] for row in timelines[issue]
        if str(row['id'])==scope['PREDECESSOR_RECEIPT_'+str(issue)])) for issue in (471,473)]
    require(all(releases[0].get(key)==releases[1].get(key)
        for key in ('WORKSTREAM_ID','WRITE_OWNER','SOURCE_SHA','SOURCE_TREE','BASE_SHA','MODE','real_orders_sent')),
        'PREDECESSOR_RELEASE_PAIR_SCOPE_MISMATCH')
    return result

def candidate_scope(f,*,owner,anchors):
    """Derive PR/branch/base from authenticated launch bytes, never constants."""
    number=f.get('SOURCE_PR','');branch=f.get('SOURCE_BRANCH','')
    require(f.get('AUTHORIZATION_SCHEMA')==CANDIDATE_AUTHORIZATION_SCHEMA
        and owner==fields(anchors[471]['body']).get('SESSION_SUCCESSOR')
        and re.fullmatch('[1-9][0-9]{0,9}',number)
        and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,199}',branch)
        and all(part not in ('','.','..') and not part.endswith(('.','.lock')) for part in branch.split('/'))
        and '..' not in branch and branch not in ('main',BASE,BRANCH,DIAGNOSTIC_BRANCH)
        and f.get('BASE_BRANCH')==BASE
        and re.fullmatch('[0-9a-f]{40}',f.get('BASE_SHA',''))
        and re.fullmatch('[0-9a-f]{40}',f.get('BASE_TREE','')),
        'DYNAMIC_EXACT_CANDIDATE_SCOPE_REQUIRED')
    for issue in (471,473):
        row=anchors[issue]
        require(f.get('OWNER_RECEIPT_'+str(issue))==row['html_url']
            and f.get('OWNER_RECEIPT_'+str(issue)+'_SHA256')==hashlib.sha256(row['body'].encode()).hexdigest()
            and fields(row['body']).get('BRANCH')==branch,
            'CANDIDATE_CURRENT_OWNER_TRANSFER_BINDING_REQUIRED')
    return {'pr':int(number),'branch':branch,'base_branch':f['BASE_BRANCH'],
        'base_sha':f['BASE_SHA'],'base_tree':f['BASE_TREE'],
        'authorization_schema':CANDIDATE_AUTHORIZATION_SCHEMA}

def effective_stamp(row):
    created=stamp(row['created_at']);updated=stamp(row['updated_at'])
    require(created<=updated,'COMMENT_CREATED_UPDATED_CUSTODY_INVALID')
    return updated

def recent(issue,anchor,now,*,get=None):
    # GitHub issue-comments `since` selects last-updated timestamps. A comment
    # created before the controlled anchor but edited afterwards is included.
    # No moving two-hour window and no silently truncated page are permitted.
    transfer=anchor
    anchor=anchor.get('_rc6_history_anchor',anchor)
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
    require(transfer['id'] in ids,'ACTUAL_SUCCESSOR_TRANSFER_MISSING_FROM_UPDATED_TIMELINE')
    return sorted(rows,key=lambda row:(effective_stamp(row),row['id']))

BRIDGE_STAGES=frozenset(('focal311','focal312','BIG-browser','G5P-acquire',
    'G5P-certify','full-gov311','full-gov312'))


def authenticated_dispatch_bridge(event,gate,*,get=None,sleep=None):
    """A bot dispatch is valid only after its exact owner-authored PR bridge succeeds."""
    reader=get or api;wait=sleep or time.sleep
    sender=event.get('sender',{}).get('login')
    inputs=event.get('inputs',{})
    number=inputs.get('bridge_run_id','')
    if sender=='mbalbo2023':
        require(number in (None,''),'OWNER_DISPATCH_CANNOT_BORROW_BRIDGE_ORIGIN')
        return None
    require(sender=='github-actions[bot]' and gate in BRIDGE_STAGES
        and re.fullmatch(r'[1-9][0-9]{0,19}',number or '') is not None,
        'BOT_DISPATCH_EXACT_BRIDGE_RUN_REQUIRED')
    for attempt in range(16):
        bridge=reader('/actions/runs/'+number+'/attempts/1')
        require(bridge.get('id')==int(number) and bridge.get('run_attempt')==1
            and bridge.get('event')=='pull_request' and bridge.get('head_sha')==inputs.get('source_sha')
            and bridge.get('head_branch')==event.get('ref','').removeprefix('refs/heads/')
            and bridge.get('path')=='.github/workflows/rc6-unified-candidate-tests.yml'
            and bridge.get('actor',{}).get('login')=='mbalbo2023'
            and bridge.get('repository',{}).get('id')==REPO_ID
            and bridge.get('head_repository',{}).get('id')==REPO_ID
            and any(pr.get('number')==492 and pr.get('head',{}).get('sha')==inputs.get('source_sha')
                and pr.get('base',{}).get('ref')==BASE for pr in bridge.get('pull_requests',[])),
            'BOT_DISPATCH_BRIDGE_OWNER_SOURCE_PR_OR_WORKFLOW_REBOUND')
        if bridge.get('status')=='completed':break
        require(bridge.get('status') in ('queued','in_progress') and attempt<15,
            'BOT_DISPATCH_BRIDGE_NOT_COMPLETED_WITHIN_BOUND')
        wait(2)
    require(bridge.get('conclusion')=='success','BOT_DISPATCH_BRIDGE_NOT_SUCCESSFUL')
    jobs=reader('/actions/runs/'+number+'/attempts/1/jobs?per_page=100').get('jobs')
    require(type(jobs) is list and len(jobs)<100
        and len([job for job in jobs if job.get('name')=='Dispatch exact owner-approved stage without gate credit'
            and job.get('conclusion')=='success'])==1
        and all(job.get('conclusion')=='skipped' for job in jobs
            if job.get('name','').startswith('RC6 ') and job.get('name')!='Dispatch exact owner-approved stage without gate credit'),
        'BOT_DISPATCH_BRIDGE_JOB_OR_CHEAP_GATE_NOT_ISOLATED')
    return {'bridge_run_id':int(number),'bridge_attempt':1,
        'bridge_head_sha':bridge['head_sha'],'bridge_conclusion':'success',
        'bridge_only_no_gate_credit':True}


def actual_event(gate,*,diagnostic_branch=None):
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
        branch=event.get('ref','').removeprefix('refs/heads/')
        require(event['repository']['id']==REPO_ID and event['repository']['full_name']==REPO
            and event.get('inputs',{}).get('gate')==gate
            and bool(branch) and (gate not in DIAGNOSTIC_GATES or branch==(DIAGNOSTIC_BRANCH if diagnostic_branch is None else diagnostic_branch)),
            'SOLE_OWNED_SCOPED_DISPATCH_EVENT_REQUIRED')
        origin=authenticated_dispatch_bridge(event,gate)
        if origin is not None:custody['dispatch_bridge']=origin
        return event,custody
    pr=event['pull_request']
    require(event['repository']['id']==REPO_ID and event['repository']['full_name']==REPO
        and type(event['number']) is int and event['number']>0 and pr['number']==event['number'] and pr['state']=='open'
        and pr['draft'] is (gate!='predeploy')
        and event.get('sender',{}).get('login')=='mbalbo2023'
        and pr['user']['login']=='mbalbo2023' and pr['head']['repo']['id']==REPO_ID
        and pr['head']['repo']['owner']['login']=='mbalbo2023'
        and pr['base']['repo']['id']==REPO_ID and pr['base']['ref']==BASE,
        'SOLE_OWNED_CANDIDATE_PR_EVENT_REQUIRED')
    require((gate=='cheap' and event['action'] in ('opened','synchronize','reopened'))
        or (gate=='predeploy' and event['action']=='ready_for_review'),
        'PR_PUSH_AUTHORIZES_CHEAP_ONLY_READY_FOR_REVIEW_AUTHORIZES_G7_ONLY')
    return event,custody

def fresh_source(sha,tree,gate=None,*,get=None,candidate=None):
    require(re.fullmatch('[0-9a-f]{40}',sha or ''),'EXACT_HEAD_SOURCE_SHA_REQUIRED')
    reader=get or api
    scope=candidate or {'pr':PR,'branch':BRANCH,'base_branch':BASE,'base_sha':BASE_SHA}
    repo=reader('');ref=reader('/git/ref/heads/'+scope['branch']);commit=reader('/git/commits/'+sha)
    pr=reader('/pulls/'+str(scope['pr']))
    require(repo['id']==REPO_ID and ref['object']['sha']==sha and commit['sha']==sha
        and pr['number']==scope['pr'] and pr['state']=='open' and pr['draft'] is (gate!='predeploy')
        and pr['user']['login']=='mbalbo2023' and pr['head']['sha']==sha and pr['head']['ref']==scope['branch']
        and pr['head']['repo']['id']==REPO_ID and pr['head']['repo']['owner']['login']=='mbalbo2023'
        and pr['base']['repo']['id']==REPO_ID and pr['base']['ref']==scope['base_branch']
        and pr['base']['sha']==scope['base_sha'],
        'FRESH_EXACT_SOURCE_AUTHORITY_REBOUND')
    if candidate is not None:
        baseline=reader('/git/ref/heads/'+scope['base_branch']);base_commit=reader('/git/commits/'+scope['base_sha'])
        require(repo.get('full_name')==REPO and repo.get('private') is False
            and baseline.get('object',{}).get('sha')==scope['base_sha']
            and base_commit.get('sha')==scope['base_sha']
            and base_commit.get('tree',{}).get('sha')==scope['base_tree'],
            'CANDIDATE_CANONICAL_BASE_CHANGED_OR_UNVERIFIED')
    actual=commit['tree']['sha'];require(re.fullmatch('[0-9a-f]{40}',actual),'ACTUAL_TREE_SHA_REQUIRED')
    if tree:require(tree==actual,'EXACT_TREE_AUTHORITY_REBOUND')
    return actual

def private_cas_template_scope(f,sha,tree,gate,*,get=None):
    """Dual immutable authority for one private experiment, never G5 credit."""
    approval=f.get('RC6_PRIVATE_ORIGINAL_CAS_COMPARISON_AUTHORIZATION')
    raw=f.get('CAS_COMPARISON_TEMPLATE_JSON')
    if approval in (None,'NOT_GRANTED') and raw is None:
        return None
    require(approval=='APPROVED' and gate=='Horizon' and type(raw) is str,
        'PRIVATE_CAS_EXPLICIT_HORIZON_TEMPLATE_AUTHORITY_REQUIRED')
    template=document(raw)
    require(type(template) is dict and set(template)=={'schema','source_sha','source_tree',
        'producer_namespace_root','original_contract','prerequisites','reader_review',
        'private_producer_ack_review','qualification_scope'}
        and template.get('schema')=='rc6.original-cas-comparison-admission.v2'
        and template.get('source_sha')==sha and template.get('source_tree')==tree
        and template.get('producer_namespace_root')=='OWNED_HORIZON_DATA_ROOT'
        and template.get('qualification_scope')=='PRIVATE_DEVELOPMENT_ONLY_NOT_G5'
        and template.get('prerequisites')==document(f.get('PREREQUISITES_MANIFEST_JSON','null')),
        'PRIVATE_CAS_EXACT_SOURCE_ORDERED_TEMPLATE_REQUIRED')
    from scripts.rc6_cas_original_comparison import CONTRACT
    require(template['original_contract']==document(json.dumps(dict(CONTRACT)))
        and all(type(template[key]) is dict and set(template[key])=={'gate','path','sha256'}
            and template[key].get('gate') in ('G1.311','G1.312')
            and re.fullmatch('[0-9a-f]{64}',template[key].get('sha256',''))
            and type(template[key].get('path')) is str
            for key in ('reader_review','private_producer_ack_review')),
        'PRIVATE_CAS_ORIGINAL_CONTRACT_AND_REVIEW_REFS_REQUIRED')
    number=f.get('OWNER_RECEIPT_473','')
    require(re.fullmatch('[1-9][0-9]{0,9}',number),'PRIVATE_CAS_DUAL_TEMPLATE_AUTHORITY_REQUIRED')
    twin=comment('https://github.com/'+REPO+'/issues/473#issuecomment-'+number,473,get=get)
    other=fields(twin['body'])
    require(twin['created_at']==twin['updated_at']
        and hashlib.sha256(twin['body'].encode()).hexdigest()==f.get('OWNER_RECEIPT_473_SHA256')
        and all(other.get(key)==f.get(key) for key in ('WORKSTREAM_ID','SESSION_SUCCESSOR',
            'WRITE_OWNER','INTEGRATION_OWNER','DEPLOY_OWNER','RELEASED','SOURCE_SHA','SOURCE_TREE',
            'MODE','real_orders_sent','GATES_AUTHORIZED','RC6_MATERIAL_AUTOMATIC_PR_AUTHORIZATION',
            'RC6_PRIVATE_ORIGINAL_CAS_COMPARISON_AUTHORIZATION','CAS_COMPARISON_TEMPLATE_JSON')),
        'PRIVATE_CAS_AUTHENTIC_DUAL_EXACT_TEMPLATE_REQUIRED')
    return template


def launch_fields(row,sha,tree,session=None,gate=None,*,get=None,anchors=None):
    require(row['user']['login']=='mbalbo2023' and row['issue_url'].endswith('/issues/471')
        and row['created_at']==row['updated_at'],'IMMUTABLE_OWNER_LAUNCH_AUTHOR_REQUIRED')
    f=fields(row['body']);owner=f.get('SESSION_SUCCESSOR')
    current=None if owner==SUCCESSOR_OWNER else (anchors or administrative_anchors(owner,get=get))
    workstream=WORKSTREAM if current is None else fields(current[471]['body']).get('WORKSTREAM_ID')
    gates=f.get('GATES_AUTHORIZED','').split(',')
    require(f.get('RC6_MATERIAL_AUTOMATIC_PR_AUTHORIZATION')=='APPROVED' and f.get('WORKSTREAM_ID')==workstream
        and f.get('SOURCE_SHA')==sha and f.get('SOURCE_TREE')==tree
        and owner is not None
        and (session is None or session==owner) and f.get('WRITE_OWNER')==owner
        and f.get('INTEGRATION_OWNER')==owner
        and f.get('DEPLOY_OWNER')=='NOT_ACQUIRED' and f.get('RELEASED')=='false'
        and f.get('MODE')=='PRODUCTION_PAPER / SIMULATION' and f.get('real_orders_sent')=='0'
        and len(gates)==1 and gates[0] in GATES and (gate is None or gates[0]==gate),
        'EXACT_IMMUTABLE_SINGLE_GATE_LAUNCH_RECEIPT_REQUIRED')
    require(re.fullmatch(r'https://github\.com/mbalbo2023/Porota-trading/issues/(?:471|473)#issuecomment-[0-9]+',f.get('CAUSE_EVIDENCE_URL','')),
        'EVIDENCED_NEW_SOURCE_REPAIR_REQUIRED_BEFORE_MATERIAL_RETRY')
    if current is not None:
        candidate_scope(f,owner=owner,anchors=current)
        require(all(stamp(row['created_at'])>=stamp(anchor['created_at']) for anchor in current.values()),
            'CANDIDATE_AUTHORIZATION_PRECEDES_OWNER_TRANSFER')
    else:
        require('AUTHORIZATION_SCHEMA' not in f and BRANCH in row['body'],
            'LAUNCH_RECEIPT_SOLE_LEGACY_SUCCESSOR_BRANCH_REQUIRED')
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
    private_cas_template_scope(f,sha,tree,gates[0],get=get)
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


_DISJOINT_SRE_PATHS=frozenset((
    '.github/workflows/rc6-sre-deep-readonly-20261011.yml',
    '.github/workflows/rc6-sre-*-20261011.yml',
    'ops/rc6_deploy_critical_control_plane.sh',
    'scripts/ops/rc6_sre_*','tests/test_rc6_sre_*','docs/ops/RC6_SRE_*'))

# These two unedited owner records explicitly relinquish the one critical
# deploy operation while retaining the earlier, separately scoped SRE writer.
# Bind this historical spelling to the exact paired bytes; a generic
# NOT_ACQUIRED row is never a release of an earlier DEPLOY_OWNER.
_SRE_APPLY_RELEASE={
    471:(6112057220,6112200132,'139038315d1f6e8427085f1bd8baa069f2f5a99b0cab33f746468f8f7ae5971e'),
    473:(6112057075,6112200322,'a429849bd7a95a9d434161bdbd6287fae4c705fd8ce6b31469510f1a72eaedca')}

# A separate SRE owner relinquished its already released DEPLOY_OWNER and
# transferred only the disjoint PR #494 writer. The release names the old
# owner in SESSION and the incoming owner in SESSION_SUCCESSOR. Both unedited
# pairs are pinned; this never transfers G5P or global deploy authority.
_SRE_SUCCESSION={
    471:(6115863183,'67f3e0e61952512c41995cc69c75bf6a70b6439b00db7d07b9c3983e548dcd22',
         6115867410,'e52929e75c3677eb142720c3a73b9aa242eb720af616c6b39da9c43c6756bda0'),
    473:(6115863387,'9c5c75ce616ee6a6046805ac4244c1a0354145f9a4dc24f3b4b5d0edadb975df',
         6115867610,'4f30a2838049b49dcef6349536baaa229b0257bf767d3e8d81f5675e59e295e3')}
_SRE_OLD_OWNER='WORK_RC6_SRE_20261011_1740Z'
_SRE_NEW_OWNER='WORK_RC6_SRE_20261012_0100Z'


def exact_sre_apply_release(row,f,issue,prior_deploy,prior_scope):
    scope_id,release_id,body_sha=_SRE_APPLY_RELEASE[issue]
    session='WORK_RC6_SRE_20261011_1740Z'
    return (row['id']==release_id
        and hashlib.sha256(row['body'].encode()).hexdigest()==body_sha
        and prior_scope is not None and prior_scope[0]['id']==scope_id
        and prior_deploy is not None
        and prior_deploy[1].get('DEPLOY_OWNER')==session
        and prior_deploy[1].get('WORKSTREAM_ID')==prior_scope[1].get('WORKSTREAM_ID')
        ==f.get('WORKSTREAM_ID')=='WS-RC6-SRE-REPAIR-DISK-DEEP-20261011'
        and f.get('SESSION_SUCCESSOR')==f.get('SESSION')==session
        and f.get('WRITE_OWNER')==f.get('INTEGRATION_OWNER')==session
        and f.get('DEPLOY_OWNER')=='NOT_ACQUIRED' and f.get('RELEASED')=='false'
        and f.get('BRANCH')==prior_scope[1].get('BRANCH')=='ops/rc6-sre-deep-20261011'
        and f.get('SCOPE')=='Only SRE source/evidence; no further host mutation authorized by this receipt.'
        and f.get('APPLY_RUN')=='38163172961' and f.get('APPLY_ATTEMPT')=='1'
        and f.get('APPLY_ARTIFACT')=='11710093973'
        and f.get('APPLY_ZIP_SHA256')=='0287ef958307e29ba9165b99fe3406bd6b05b8bd75ab5b4bb88e09d54bc21ee5'
        and f.get('APPLY_JSON_SHA256')=='0042f2d17ed63cbb00004eeec2c1aa5787c5a9e7f3f04d57ab93914d0e8f0680'
        and f.get('PAIR_ISSUE')==str(issue)
        and f.get('MODE')=='PRODUCTION_PAPER / SIMULATION'
        and f.get('real_orders_sent')=='0')


def disjoint_sre_writer(f,source_sha,owner,*,successor_authenticated=False):
    """The separately scoped #491 writer has no claim on G5P Source or paths."""
    paths=f.get('PATHS','').split(',')
    session=f.get('SESSION_SUCCESSOR',f.get('SESSION'))
    return (owner not in (_SRE_OLD_OWNER,_SRE_NEW_OWNER)
        and f.get('WORKSTREAM_ID')=='WS-RC6-SRE-REPAIR-DISK-DEEP-20261011'
        and (session==_SRE_OLD_OWNER or successor_authenticated and session==_SRE_NEW_OWNER)
        and f.get('WRITE_OWNER')==f.get('INTEGRATION_OWNER')==session
        and f.get('DEPLOY_OWNER')=='NOT_ACQUIRED' and f.get('RELEASED')=='false'
        and f.get('BRANCH')=='ops/rc6-sre-deep-20261011'
        and f.get('BASE_SHA')==BASE_SHA
        and re.fullmatch('[0-9a-f]{40}',f.get('SOURCE_SHA','')) is not None
        and f['SOURCE_SHA']!=source_sha
        and f.get('PR')=='https://github.com/'+REPO+'/pull/494'
        and paths and len(paths)==len(set(paths)) and set(paths)<=_DISJOINT_SRE_PATHS
        and f.get('MODE')=='PRODUCTION_PAPER / SIMULATION' and f.get('real_orders_sent')=='0')


def exact_sre_succession_release(row,f,issue,prior_deploy,prior_writer,prior_scope):
    release_id,release_hash,_,_=_SRE_SUCCESSION[issue]
    return (row['id']==release_id
        and hashlib.sha256(row['body'].encode()).hexdigest()==release_hash
        and prior_scope is not None and prior_scope[0]['id']==_SRE_APPLY_RELEASE[issue][0]
        and prior_deploy is not None and prior_deploy[1].get('DEPLOY_OWNER')=='RELEASED'
        and prior_deploy[1].get('WORKSTREAM_ID')=='WS-RC6-SRE-REPAIR-DISK-DEEP-20261011'
        and prior_writer is not None and prior_writer[1].get('RELEASED')=='false'
        and prior_writer[1].get('WORKSTREAM_ID')=='WS-RC6-SRE-REPAIR-DISK-DEEP-20261011'
        and f.get('WORKSTREAM_ID')=='WS-RC6-SRE-REPAIR-DISK-DEEP-20261011'
        and f.get('SESSION')==_SRE_OLD_OWNER
        and f.get('SESSION_SUCCESSOR')==_SRE_NEW_OWNER
        and f.get('WRITE_OWNER')==f.get('INTEGRATION_OWNER')==f.get('DEPLOY_OWNER')=='RELEASED'
        and f.get('RELEASED')=='true' and f.get('BRANCH')=='ops/rc6-sre-deep-20261011'
        and f.get('SOURCE_SHA')=='f61d30dce6d86b7bec086a65f8b7a656797b9cf4'
        and f.get('APPLY_RUN')=='38163172961' and f.get('APPLY_ARTIFACT')=='11710093973'
        and f.get('POST_RUN')=='38163695961' and f.get('POST_ARTIFACT')=='11711606030'
        and f.get('PAPER')=='PRODUCTION_PAPER' and f.get('real_orders_sent')=='0'
        and f.get('PAIR_ISSUE')==str(issue))


def exact_sre_succession_writer(row,f,issue,release,source_sha,owner):
    _,_,writer_id,writer_hash=_SRE_SUCCESSION[issue]
    return (release is not None and row['id']==writer_id
        and hashlib.sha256(row['body'].encode()).hexdigest()==writer_hash
        and stamp(release['created_at'])<stamp(row['created_at'])
        and f.get('SESSION')==f.get('SESSION_SUCCESSOR')==_SRE_NEW_OWNER
        and f.get('BASE_BRANCH')==BASE and f.get('BASE_SHA')==BASE_SHA
        and f.get('PAIR_ISSUE')==str(issue)
        and disjoint_sre_writer(f,source_sha,owner,successor_authenticated=True))

def latest_writer(rows,issue,sha,tree,owner,now):
    records=[];states={};deploy_states={};sre_scope=None;sre_releases=[]
    sre_transfer_release=None;sre_successor_authenticated=False
    for row in sorted(rows,key=lambda item:(effective_stamp(item),item['id'])):
        f=fields(row.get('body',''))
        if any(key in f for key in ('WRITE_OWNER','INTEGRATION_OWNER','DEPLOY_OWNER','SOURCE_LEASE_EXPIRES_UTC','RELEASED')):
            require(row['user']['login']=='mbalbo2023' and row['issue_url'].endswith('/issues/'+str(issue)),
                'OWNERSHIP_RECORD_AUTHOR_OR_ISSUE_REBOUND')
            require(row['created_at']==row['updated_at'],'EDITED_HISTORICAL_OWNERSHIP_RECORD')
            scope_id,release_id,_body_sha=_SRE_APPLY_RELEASE[issue]
            if row['id']==scope_id:
                require(disjoint_sre_writer(f,sha,owner),'SRE_PRIOR_DISJOINT_PATH_SCOPE_REBOUND')
                sre_scope=(row,f)
            deploy=f.get('DEPLOY_OWNER')
            if row['id']==_SRE_SUCCESSION[issue][0]:
                require(exact_sre_succession_release(row,f,issue,
                    deploy_states.get(_SRE_OLD_OWNER),states.get(_SRE_OLD_OWNER),sre_scope),
                    'SRE_EXACT_DISJOINT_SUCCESSION_RELEASE_REBOUND')
                sre_transfer_release=row
            if row['id']==_SRE_SUCCESSION[issue][2]:
                require(exact_sre_succession_writer(row,f,issue,sre_transfer_release,sha,owner),
                    'SRE_EXACT_DISJOINT_SUCCESSION_WRITER_REBOUND')
                sre_successor_authenticated=True
            if row['id']==release_id:
                session='WORK_RC6_SRE_20261011_1740Z'
                require(exact_sre_apply_release(row,f,issue,deploy_states.get(session),sre_scope),
                    'SRE_EXACT_PAIRED_APPLY_RELEASE_REBOUND')
                # Scope is inherited from its immutable, narrower declaration;
                # identify the source rather than claiming these fields were in
                # the later release comment itself.
                f={**f,**{key:sre_scope[1][key] for key in ('PATHS','BASE_SHA','PR')},
                    '_SRE_SCOPE_RECEIPT_ID':str(scope_id)}
                deploy_states[session]=(row,{'DEPLOY_OWNER':'RELEASED','RELEASED':'true',
                    'WORKSTREAM_ID':f['WORKSTREAM_ID'],'_SRE_EXACT_RELEASE_SHA256':_body_sha})
                sre_releases.append({'comment_id':row['id'],'body_sha256':_body_sha,
                    'prior_scope_comment_id':scope_id,'apply_run_id':38163172961})
            if deploy not in (None,'NOT_ACQUIRED','NONE','null'):
                session=(f.get('SESSION') if deploy=='RELEASED' and f.get('SESSION')
                         else f.get('SESSION_SUCCESSOR',f.get('SESSION')))
                if deploy=='RELEASED':
                    prior=deploy_states.get(session)
                    require(prior is not None and f.get('RELEASED')=='true'
                        and f.get('WORKSTREAM_ID')==prior[1].get('WORKSTREAM_ID'),
                        'HISTORICAL_DEPLOY_OWNER_RELEASE_NOT_AUTHENTIC')
                else:
                    require(deploy==session and f.get('RELEASED')=='false'
                        and bool(f.get('WORKSTREAM_ID')),
                        'FOREIGN_OR_UNKNOWN_DEPLOY_OWNER_AFTER_ANCHOR')
                deploy_states[session]=(row,f)
            scoped={f[key] for key in ('WRITE_OWNER','INTEGRATION_OWNER')
                if key in f and f[key] not in ('RELEASED','NOT_ACQUIRED','NONE','null')}
            if f.get('RELEASED')=='true':
                # Historical owner records use both WRITE_OWNER=<session> and
                # WRITE_OWNER=RELEASED. In the latter form, bind the release
                # to its explicit SESSION and to the preceding workstream;
                # an unrelated comment must never release another writer.
                session=(f.get('SESSION') if f.get('SESSION') and f.get('WRITE_OWNER')=='RELEASED'
                         else f.get('SESSION_SUCCESSOR',f.get('SESSION')))
                if not scoped and f.get('WRITE_OWNER')=='RELEASED':
                    prior=states.get(session)
                    require(type(session) is str and prior is not None
                        and prior[1].get('WORKSTREAM_ID')==f.get('WORKSTREAM_ID')
                        and f.get('INTEGRATION_OWNER','NOT_ACQUIRED') in
                            ('NOT_ACQUIRED','RELEASED'),
                        'AUTHENTIC_BARE_RELEASE_SESSION_AND_WORKSTREAM_REQUIRED')
                    scoped={session}
                require(scoped and scoped=={session},
                    'AUTHENTIC_OWN_SESSION_EXPLICIT_RELEASE_REQUIRED')
                for identifier in scoped:
                    prior=states.get(identifier)
                    if prior is not None:
                        require(f.get('WORKSTREAM_ID')==prior[1].get('WORKSTREAM_ID'),
                            'EXPLICIT_RELEASE_WORKSTREAM_REBOUND')
                    states[identifier]=(row,f)
            else:
                for identifier in scoped:states[identifier]=(row,f)
            records.append((row,f))
    require(records,'FRESH_OWNER_RECORDS_REQUIRED_BOTH_ISSUES')
    # Evaluate the last authentic status for each writer, rather than demanding
    # that every historical lease already contain its future release. A later
    # false/unknown status reacquires the scope and blocks again. Expiry never
    # changes release state, including records older than a new transfer.
    disjoint=[]
    for identifier,(foreign_row,status) in states.items():
        if identifier!=owner:
            permitted=disjoint_sre_writer(status,sha,owner,
                successor_authenticated=sre_successor_authenticated)
            require(status.get('RELEASED')=='true' or permitted,
                'FOREIGN_ACTIVE_OR_UNKNOWN_WRITER_AFTER_SUCCESSOR_ANCHOR')
            if permitted:disjoint.append({'session':identifier,'comment_id':foreign_row['id'],
                'branch':status['BRANCH'],'paths':status['PATHS'].split(',')})
    require(all(status.get('DEPLOY_OWNER')=='RELEASED' and status.get('RELEASED')=='true'
                for _row,status in deploy_states.values()),
            'ACTIVE_DEPLOY_OWNER_AFTER_CONTROLLED_ANCHOR')
    integrations=[(row,f) for row,f in records if f.get('INTEGRATION_OWNER')==owner]
    require(integrations,
        'LATEST_INTEGRATION_OWNER_NOT_CONTROLLED_RECOVERY')
    owned=[(row,f) for row,f in records if f.get('WRITE_OWNER')==owner]
    require(owned,'LATEST_CONTROLLED_WRITER_REQUIRED')
    row,f=owned[-1]
    require(row['user']['login']=='mbalbo2023' and row['issue_url'].endswith('/issues/'+str(issue))
        and row['created_at']==row['updated_at']
        and f.get('WRITE_OWNER')==owner and f.get('SESSION_SUCCESSOR',f.get('SESSION'))==owner
        and f.get('DEPLOY_OWNER')=='NOT_ACQUIRED' and f.get('RELEASED')=='false'
        and f.get('SOURCE_SHA')==sha and f.get('SOURCE_TREE')==tree
        and f.get('MODE')=='PRODUCTION_PAPER / SIMULATION' and f.get('real_orders_sent')=='0',
        'LATEST_WRITER_OR_DEPLOY_OWNER_CONFLICT')
    expires=stamp(f.get('SOURCE_LEASE_EXPIRES_UTC'))
    require(stamp(row['created_at'])<=effective_stamp(row)<=now<expires
        and timedelta(0)<expires-stamp(row['created_at'])<=timedelta(minutes=20),
        'LATEST_WRITER_LEASE_EXPIRED_OR_UNBOUNDED')
    result={'comment_id':row['id'],'url':row['html_url'],'author':'mbalbo2023','fields':f,
        'body_sha256':hashlib.sha256(row['body'].encode()).hexdigest(),'lease_expires_utc':expires.isoformat().replace('+00:00','Z'),
        'latest_integration_owner_comment_id':integrations[-1][0]['id'],
        'authenticated_disjoint_active_writers':disjoint,
        'authenticated_sre_deploy_release_records':sre_releases,
        'all_post_anchor_owner_record_ids':[item[0]['id'] for item in records],
        'all_post_anchor_records_and_old_comment_edits_checked':True,'maximum_lease_seconds':1200}
    if sre_successor_authenticated:
        result['authenticated_sre_successor']={
            'released_session':_SRE_OLD_OWNER,'successor_session':_SRE_NEW_OWNER,
            'release_comment_id':sre_transfer_release['id'],
            'release_body_sha256':_SRE_SUCCESSION[issue][1],
            'successor_comment_id':_SRE_SUCCESSION[issue][2],
            'successor_body_sha256':_SRE_SUCCESSION[issue][3]}
    if owner not in (RECOVERY_OWNER,SUCCESSOR_OWNER,CLOSURE_OWNER):
        result['latest_status_by_owner']={identifier:{'comment_id':item[0]['id'],
            'released':item[1].get('RELEASED')=='true'} for identifier,item in sorted(states.items())}
    return result

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
    require(gate in GATES and isinstance(owner_session,str)
        and re.fullmatch(r'CODEX_[A-Z0-9_]{1,159}',owner_session)
        and type(run_attempt) is int and run_attempt==1
        and type(run_id) is int and run_id>0,'OWNED_GATE_FIRST_EXACT_SUCCESSOR_SCOPE_REQUIRED')
    anchors=recovery_anchors(RECOVERY_OWNER,get=get)
    successor=(successor_anchors(owner_session,get=get) if owner_session==SUCCESSOR_OWNER
        else administrative_anchors(owner_session,get=get,now=now))
    launch=comment(launch_receipt_url,471,get=get)
    auth=launch_fields(launch,source_sha,source_tree,owner_session,gate,get=get,anchors=successor)
    scope=candidate_scope(auth,owner=owner_session,anchors=successor) if owner_session!=SUCCESSOR_OWNER else None
    fresh_source(source_sha,source_tree,gate,get=get,candidate=scope)
    timelines={issue:recent(issue,successor[issue],now,get=get) for issue in (471,473)}
    owners={str(issue):latest_writer(timelines[issue],issue,source_sha,source_tree,owner_session,now)
        for issue in (471,473)}
    workstream=fields(successor[471]['body']).get('WORKSTREAM_ID')
    require(all(fields(row['body']).get('WORKSTREAM_ID')==workstream
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
    result={'source_sha':source_sha,'source_tree':source_tree,'owner_session':owner_session,'gate':gate,
        'run_id':run_id,'run_attempt':1,'read_utc':now.isoformat().replace('+00:00','Z'),
        'launch_receipt_url':launch_receipt_url,'launch_body_sha256':hashlib.sha256(launch['body'].encode()).hexdigest(),
        'fresh_ownership':owners,'ops':ops,'real_orders_sent':0,'DEPLOY_OWNER':'NOT_ACQUIRED'}
    # Existing archived V1 scope digests must replay byte for byte. New owners
    # record their authenticated workstream; the two legacy owners keep the
    # original return wire and original WORKSTREAM used by lease replay.
    if owner_session not in (SUCCESSOR_OWNER,CLOSURE_OWNER):result['workstream_id']=workstream
    return result

def admit(*,source_sha,source_tree=None,launch_receipt_url=None,owner_session=None,gate,now=None):
    require(os.getuid()==os.geteuid()>0 and os.environ.get('GITHUB_REPOSITORY')==REPO
        and os.environ.get('GITHUB_REPOSITORY_ID')==str(REPO_ID),'ACTUAL_NONROOT_CANONICAL_ACTIONS_REQUIRED')
    require(gate in GATES and os.environ.get('GITHUB_RUN_ATTEMPT')=='1','FIRST_SCOPED_RUN_ATTEMPT_REQUIRED_NO_BLIND_RERUN')
    if gate in ('G5P-acquire','G5P-certify'):
        require(launch_receipt_url is not None and owner_session is not None
                and source_tree is not None,
                'G5P_EXACT_MATERIAL_LAUNCH_OWNER_SOURCE_REQUIRED')
        return admit_g5p(source_sha=source_sha,source_tree=source_tree,
                         launch_receipt_url=launch_receipt_url,
                         owner_session=owner_session,gate=gate,now=now)
    if gate == 'predeploy' and launch_receipt_url is None:
        # The old automatic ready-for-review admission remains FULL_G5 only.
        from scripts.rc6_architectural_gates import require_horizon_model_before_material
        require_horizon_model_before_material(gate)
    else:
        from scripts.rc6_architectural_gates import require_horizon_model_before_material
        require_horizon_model_before_material(gate)
    if gate == 'predeploy':
        launch=comment(launch_receipt_url,471) if launch_receipt_url else None
        profile=fields(launch['body']).get('RELEASE_PROFILE') if launch else None
        if profile == 'PAPER_V3_ONLY':
            f=fields(launch['body'])
            approval=authenticate_g5p_revision(f,source_sha,source_tree,owner_session,now)
            result=_admit_source_scope(source_sha=source_sha,source_tree=source_tree,
                launch_receipt_url=launch_receipt_url,owner_session=owner_session,gate=gate,
                now=now,g5p_approval=approval)
            require(result['prerequisites_manifest'].get('release_profile')=='PAPER_V3_ONLY'
                    and result['prerequisites_manifest'].get('owner_session')==owner_session
                    and result['prerequisites_manifest'].get('g5p_revision_approval_url')==approval.approval_url,
                    'G7_PAPER_V3_EXACT_MANIFEST_CONTRACT_REBOUND')
            result.update(release_profile='PAPER_V3_ONLY',
                          g5p_revision_approval_url=approval.approval_url)
            return result
    return _admit_source_scope(source_sha=source_sha,source_tree=source_tree,
        launch_receipt_url=launch_receipt_url,owner_session=owner_session,gate=gate,now=now)

def _admit_source_scope(*,source_sha,source_tree=None,launch_receipt_url=None,owner_session=None,gate,now=None,
                        g5a_approval=None,g5p_approval=None):
    """Source/API admission only; this mapping grants no native G5A authority."""
    require(os.getuid()==os.geteuid()>0 and os.environ.get('GITHUB_REPOSITORY')==REPO
        and os.environ.get('GITHUB_REPOSITORY_ID')==str(REPO_ID),'ACTUAL_NONROOT_CANONICAL_ACTIONS_REQUIRED')
    require(gate in GATES and os.environ.get('GITHUB_RUN_ATTEMPT')=='1','FIRST_SCOPED_RUN_ATTEMPT_REQUIRED_NO_BLIND_RERUN')
    event,event_custody=actual_event(gate)
    if os.environ['GITHUB_EVENT_NAME']=='pull_request':
        require(event['pull_request']['head']['sha']==source_sha,'EVENT_HEAD_SHA_REBOUND')
    else:require(event['inputs'].get('source_sha')==source_sha,'DISPATCH_HEAD_SHA_REBOUND')
    now=now or datetime.now(timezone.utc)
    anchors=recovery_anchors(RECOVERY_OWNER)  # unchanged user-stop/raw-ops authority
    if launch_receipt_url:
        launch=comment(launch_receipt_url,471)
    else:
        # Find exact launch bytes from the original checkpoint, then derive
        # and authenticate their actual current owner. Never adopt an old
        # owner name simply because the workflow default has that value.
        history=administrative_anchors(CLOSURE_OWNER)
        search=recent(471,history[471],now)
        matching=[]
        for row in search:
            f=fields(row.get('body',''))
            if f.get('RC6_MATERIAL_AUTOMATIC_PR_AUTHORIZATION')=='APPROVED' and f.get('SOURCE_SHA')==source_sha and f.get('GATES_AUTHORIZED')==gate:
                matching.append(row)
        require(len(matching)==1,'ONE_EXACT_PREPUSH_LAUNCH_AUTHORITY_REQUIRED')
        launch=matching[0]
    claimed=fields(launch['body']);tree=source_tree or claimed.get('SOURCE_TREE')
    owner=claimed.get('SESSION_SUCCESSOR')
    require(owner!=SUCCESSOR_OWNER and (owner_session is None or owner==owner_session),
        'CURRENT_ADMINISTRATIVE_OWNER_REQUIRED')
    successor=administrative_anchors(owner,now=now)
    timelines={issue:recent(issue,successor[issue],now) for issue in (471,473)}
    auth=launch_fields(launch,source_sha,tree,owner_session,gate,anchors=successor)
    if gate in ('full-gov311','full-gov312','predeploy') and auth.get('RELEASE_PROFILE')=='PAPER_V3_ONLY' and g5p_approval is None:
        g5p_approval=authenticate_g5p_revision(auth,source_sha,tree,owner,now)
    scope=candidate_scope(auth,owner=owner,anchors=successor)
    if os.environ['GITHUB_EVENT_NAME']=='pull_request':
        pr=event['pull_request']
        require(event['number']==scope['pr'] and pr['head']['ref']==scope['branch']
            and pr['base']['sha']==scope['base_sha'],'EVENT_CANDIDATE_PR_BRANCH_OR_BASE_REBOUND')
    else:
        require(event.get('ref','').removeprefix('refs/heads/')==scope['branch']
            and event['inputs'].get('source_tree')==tree
            and event['inputs'].get('launch_receipt_url')==launch['html_url']
            and event['inputs'].get('owner_session')==owner,
            'DISPATCH_EXACT_CURRENT_OWNER_CANDIDATE_SCOPE_REBOUND')
    fresh_source(source_sha,tree,gate,candidate=scope)
    # Event delivery follows push: authorization must already exist.
    if os.environ['GITHUB_EVENT_NAME']=='pull_request':
        require(stamp(launch['created_at'])<=stamp(event['pull_request']['updated_at']),
            'DURABLE_LAUNCH_AUTHORIZATION_MUST_PRECEDE_PR_SOURCE_UPDATE')
    gates=auth['GATES_AUTHORIZED'].split(',')
    require(gate in gates,'GATE_NOT_AUTHORIZED_IN_IMMUTABLE_PREPUSH_SCOPE')
    owner=auth['SESSION_SUCCESSOR'];owners={str(issue):latest_writer(timelines[issue],issue,source_sha,tree,owner,now) for issue in (471,473)}
    ops=ops_admission(auth,owner,now,anchors)
    if g5p_approval is not None:
        # The complete read-only Source/owner/ops/dedup walk may exceed the
        # acceptance object's deliberately short 30-second in-process TTL.
        # Reauthenticate the same immutable decision and live owner here,
        # immediately before consuming a fresh context. Never extend TTL.
        g5p_approval=refresh_g5p_revision_for_final_admission(
            auth,g5p_approval,source_sha,tree,owner)
        require(g5p_approval.owner_session==owner,
                'G5P_APPROVAL_CANNOT_REBIND_EXCLUSIVE_OWNER')
        manifest=document(auth.get('PREREQUISITES_MANIFEST_JSON','null'))
        require(type(manifest) is dict and manifest.get('release_profile')=='PAPER_V3_ONLY'
                and manifest.get('owner_session')==owner
                and manifest.get('g5p_revision_approval_url')==g5p_approval.approval_url,
                'G5P_DOWNSTREAM_EXACT_PROFILE_MANIFEST_REQUIRED')
        capacity_source={'status':'V3_NATIVE_MEASUREMENT_REQUIRED_BEFORE_MATERIAL',
            'G5P_GREEN':False,'G5A_legacy_horizon_model_claimed':False,
            'native_v3_reserve_bytes':2*1024**3,'native_v3_minimum_free_inode_percent':10}
    elif g5a_approval is None:
        capacity_source=verify_capacity_comparison_manifest(auth,source_sha,tree)
    else:
        capacity_source=g5a_preparation_capacity(g5a_approval,source_sha=source_sha,source_tree=tree,now=now)
        require(gate=='Horizon' and g5a_approval.owner_session==owner,
            'G5A_SETUP_ACCEPTANCE_CANNOT_REBIND_SOURCE_OWNER_OR_GATE')
    dedup=dedup_admission(source_sha,gate)
    return {'schema':'rc6.material-automatic-pr-admission.v2','status':'ADMITTED_NATIVE_NOT_STARTED','source_sha':source_sha,
        'source_tree':tree,'gate':gate,'owner_session':owner,'launch_receipt_url':launch['html_url'],
        'candidate_scope':scope,
        'ownership_transfer':{'kind':fields(successor[471]['body'])['SUCCESSION_KIND'],
            'receipts':{str(issue):{'url':successor[issue]['html_url'],
                'body_sha256':hashlib.sha256(successor[issue]['body'].encode()).hexdigest()}
                for issue in (471,473)},
            'predecessor_released_claimed':fields(successor[471]['body']).get('PREDECESSOR_RELEASED')=='true',
            'lease_expiration_treated_as_release':False,'heavy_authority_from_transfer_claimed':False},
        'launch_receipt_created_at':launch['created_at'],'launch_body_sha256':hashlib.sha256(launch['body'].encode()).hexdigest(),
        'fresh_ownership':owners,'ops':ops,'dedup':dedup,'event_custody':event_custody,
        'gates_authorized':gates,'material_gates':[],
        'capacity_peaks':document(auth['CAPACITY_PEAKS_JSON']),
        'capacity_source_comparison':capacity_source,
        'cheap_files':document(auth['CHEAP_FILES_JSON']) if gate=='cheap' else None,
        'prerequisites_manifest':document(auth['PREREQUISITES_MANIFEST_JSON']) if gate!='cheap' else None,
        **({'private_cas_comparison_template':document(auth['CAS_COMPARISON_TEMPLATE_JSON'])}
            if auth.get('RC6_PRIVATE_ORIGINAL_CAS_COMPARISON_AUTHORIZATION')=='APPROVED' else {}),
        'Gov311_scope_pointer':auth.get('CANONICAL_GOV311_CUSTODY_RECEIPT_URL') or auth.get('SUPPLEMENTARY_GOV311_JUSTIFICATION_URL'),
        'omitted_Gov311_PASS_or_artifact_claimed':False,'source_truth':'CANONICAL_GITHUB_HEAD_COMMIT_TREE_PR',
        'workflow_ref':os.environ.get('GITHUB_WORKFLOW_REF'),'workflow_sha':os.environ.get('GITHUB_WORKFLOW_SHA'),
        'GITHUB_SHA_is_candidate_SHA_claimed':False,'read_utc':now.isoformat().replace('+00:00','Z'),
        'same_read_lease_valid_for_whole_long_gate_claimed':False,'real_orders_sent':0,
        'DEPLOY_OWNER':'NOT_ACQUIRED','canonical_artifact_pipeline':'PREDEPLOY_V2_ONLY','final_candidate_eligible':False,
        'release_profile':'PAPER_V3_ONLY' if g5p_approval is not None else 'FULL_G5',
        'g5p_revision_approval_url':g5p_approval.approval_url if g5p_approval is not None else None}


def authenticate_g5p_revision(fields_row,source_sha,source_tree,owner_session,now=None):
    from types import SimpleNamespace
    from scripts import rc6_contract_revisions as revisions
    from scripts.rc6_g5p_model import DOCUMENT_SHA256
    require(fields_row.get('RELEASE_PROFILE')=='PAPER_V3_ONLY'
            and fields_row.get('G5P_CONTRACT_DOCUMENT_SHA256')==DOCUMENT_SHA256
            and fields_row.get('RC6_G5P_MATERIAL_AUTHORIZATION')=='APPROVED'
            and type(fields_row.get('G5P_REVISION_APPROVAL_URL')) is str,
            'G5P_EXPLICIT_EXACT_DOCUMENT_MATERIAL_AUTHORIZATION_REQUIRED')
    return revisions.authenticate_revision(SimpleNamespace(request=api),
        fields_row['G5P_REVISION_APPROVAL_URL'],owner_session=owner_session,
        revision='PAPER_V3_ONLY_G5P_V1',scope='G5P_V3_MATERIAL',
        source_sha=source_sha,source_tree=source_tree,
        contract_sha256=DOCUMENT_SHA256,now=now)


def refresh_g5p_revision_for_final_admission(auth,original,source_sha,source_tree,owner):
    from scripts import rc6_contract_revisions as revisions
    from scripts.rc6_g5p_model import DOCUMENT_SHA256
    require(original.owner_session==owner
        and original.approval_url==auth.get('G5P_REVISION_APPROVAL_URL'),
        'G5P_INITIAL_ACCEPTANCE_OWNER_OR_POINTER_REBOUND')
    fresh=authenticate_g5p_revision(auth,source_sha,source_tree,owner)
    require(fresh.approval_url==original.approval_url
        and fresh.approval_body_sha256==original.approval_body_sha256
        and fresh.paired_comment_id==original.paired_comment_id
        and fresh.paired_body_sha256==original.paired_body_sha256,
        'G5P_DECISION_BYTES_CHANGED_DURING_LONG_ADMISSION')
    revisions.validate_revision_approval(fresh,revision='PAPER_V3_ONLY_G5P_V1',
        scope='G5P_V3_MATERIAL',source_sha=source_sha,source_tree=source_tree,
        contract_sha256=DOCUMENT_SHA256)
    return fresh


def validate_g5p_acquisition_manifest(manifest,source_sha,source_tree,owner,approval_url):
    """Acquisition gathers original V3 data; it cannot carry predecessor credit."""
    from scripts import rc6_architectural_gates as architectural
    expected={'schema':architectural.MANIFEST_SCHEMA,'source_sha':source_sha,
        'source_tree':source_tree,'release_profile':'PAPER_V3_ONLY',
        'owner_session':owner,'g5p_revision_approval_url':approval_url,
        'g5p_acquisition':None,'artifacts':[]}
    require(type(manifest) is dict and manifest==expected,
        'G5P_ACQUISITION_MUST_BE_EXACT_NONCERTIFYING_EMPTY_MANIFEST')
    return {'schema':'rc6.g5p-acquisition-prerequisites.v1',
        'source_sha':source_sha,'source_tree':source_tree,'authenticated_receipts':[],
        'material_gate_credit':False,'G0_G4_claimed':False}


def admit_g5p(*,source_sha,source_tree,launch_receipt_url,owner_session,gate,now=None):
    """Restart at most twice if a paired owner renewal arrives mid-read.

    Every attempt reauthenticates the original decision and complete live
    Source/owner/OPS state. An actual revocation, expiry or third concurrent
    update still fails; this never retries a producer or native command.
    """
    for attempt in range(3):
        try:
            row=_admit_g5p_once(source_sha=source_sha,source_tree=source_tree,
                launch_receipt_url=launch_receipt_url,owner_session=owner_session,
                gate=gate,now=now if attempt==0 else datetime.now(timezone.utc))
            if attempt:row['owner_read_consistency_restart_count']=attempt
            return row
        except ValueError as error:
            if (now is not None or attempt==2 or
                    str(error)!='OWNERSHIP_COMMENT_FUTURE_TIMESTAMP'):
                raise
    raise AssertionError('G5P_OWNER_READ_RESTART_UNREACHABLE')


def _admit_g5p_once(*,source_sha,source_tree,launch_receipt_url,owner_session,gate,now=None):
    require(gate in ('G5P-acquire','G5P-certify')
            and os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch'
            and os.environ.get('GITHUB_ACTIONS')=='true'
            and os.environ.get('GITHUB_RUN_ATTEMPT')=='1',
            'G5P_EXACT_FIRST_MANUAL_ACTIONS_STAGE_REQUIRED')
    launch=comment(launch_receipt_url,471)
    f=fields(launch['body'])
    require(f.get('GATES_AUTHORIZED')==gate,
            'G5P_SINGLE_STAGE_LAUNCH_RECEIPT_REQUIRED')
    accepted=authenticate_g5p_revision(f,source_sha,source_tree,owner_session,now)
    row=_admit_source_scope(source_sha=source_sha,source_tree=source_tree,
        launch_receipt_url=launch_receipt_url,owner_session=owner_session,
        gate=gate,now=now,g5p_approval=accepted)
    manifest=row['prerequisites_manifest']
    require(manifest.get('release_profile')=='PAPER_V3_ONLY'
            and manifest.get('owner_session')==owner_session
            and manifest.get('g5p_revision_approval_url')==accepted.approval_url,
            'G5P_EXACT_PROFILE_OWNER_AND_PRIOR_GATE_MANIFEST_REQUIRED')
    if gate=='G5P-acquire':
        row['acquisition_prerequisites']=validate_g5p_acquisition_manifest(
            manifest,source_sha,source_tree,owner_session,accepted.approval_url)
    else:
        require(type(manifest.get('artifacts')) is list and bool(manifest['artifacts']),
            'G5P_CERTIFICATION_REQUIRES_ACTUAL_G0_G4_ARTIFACTS')
    reference=document(f.get('G5P_ACQUISITION_REFERENCE_JSON','null'))
    require((gate=='G5P-acquire' and reference is None and manifest.get('g5p_acquisition') is None)
            or (gate=='G5P-certify' and type(reference) is dict
                and manifest.get('g5p_acquisition')==reference),
            'G5P_ACQUISITION_AND_CERTIFICATION_STATES_MUST_DIFFER')
    row.update(release_profile='PAPER_V3_ONLY',
               g5p_revision_approval_url=accepted.approval_url,
               g5p_acquisition_reference=reference,
               g5p_material_stage='ACQUISITION_ONLY' if gate=='G5P-acquire' else 'CERTIFICATION')
    return row


_G5A_SETUP_CONTEXTS = {}

def g5a_preparation_capacity(approval,*,source_sha,source_tree,now=None):
    """A pending native obligation, never a complete Horizon cost claim.

    Only a freshly accepted versioned contract selects this Source path. The
    carrier authenticates the actual G0--G4 and capability/bootstrap bytes,
    then measures its compound bound before allocating an image. The ROOT
    worker repeats those checks before quota setup. No ordinary material
    launch can consume this documentary mapping as capacity authority.
    """
    from scripts import rc6_contract_revisions as revisions
    from scripts import rc6_g5_contract as g5
    revisions.validate_revision_approval(approval,revision=g5.REVISION,scope=g5.SCOPE,
        source_sha=source_sha,source_tree=source_tree,contract_sha256=g5.CONTRACT_SHA256,now=now)
    return {'schema':'rc6.g5a-pending-native-compound-capacity.v2',
        'scope':'ACQUISITION_ONLY_NATIVE_CHECKS_REQUIRED_BEFORE_OWN_IMAGE',
        'source_sha':source_sha,'source_tree':source_tree,'contract_sha256':g5.CONTRACT_SHA256,
        'backing_image_bytes':26*1024**3,'workload_project_hard_bytes':20*1024**3,
        'archive_hard_bytes':512*1024**2,'closed_raw_stream_hard_bytes':20*1024**3,
        'residual_reserve_bytes':4*1024**3,'minimum_free_inode_fraction':'1/10',
        'G0_G4_actual_artifacts_required':True,'accepted_5_512_then_26_20_required':True,
        'live_compound_preflight_required':True,'per_write_physical_reservation_required':True,
        'future_cleanup_credit_bytes':0,'future_GC_credit_bytes':0,
        'native_quota_verified':False,'capacity_admitted':False,'G5_GREEN':False,
        'original_RC6_stage_complete_claimed':False,'real_orders_sent':0}

class G5ASetupLaunchContext:
    """Fresh Source/control authority for ROOT preparation, never G5 GREEN."""
    __slots__ = ('pid','source_sha','source_tree','owner_session','launch_receipt_url',
                 'revision','admission_wire','binding_wire')
    def __new__(cls, *args, **kwargs):
        raise ValueError('G5A_SETUP_ACTUAL_SOURCE_API_FACTORY_REQUIRED')
    def __setattr__(self, name, value):
        raise AttributeError('G5A_SETUP_CONTEXT_IMMUTABLE')
    @property
    def admission(self): return document(self.admission_wire)
    @property
    def binding(self): return document(self.binding_wire)


def admit_g5a_preparation(*, source_sha, source_tree, launch_receipt_url, owner_session,
                         revision_approval_url, now=None):
    """Authenticate only the proposed bounded ROOT preparation operation.

    Full Source/event/PR/owner/OPS/capacity/dedup predicates are unchanged.
    The carrier must authenticate G0--G4 and both quota artifacts before any
    image. The actual ROOT worker authenticates them again before its setup.
    """
    from scripts import rc6_contract_revisions as revisions
    from scripts import rc6_g5_contract as g5
    from scripts import rc6_material_carrier as carrier
    from types import SimpleNamespace
    require(os.getuid()==os.geteuid()>0 and os.environ.get('GITHUB_ACTIONS')=='true'
        and os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch'
        and os.environ.get('GITHUB_RUN_ATTEMPT')=='1', 'G5A_SETUP_ACTUAL_FIRST_MANUAL_ACTIONS_REQUIRED')
    now=now or datetime.now(timezone.utc)
    api_client=SimpleNamespace(request=api)
    accepted=revisions.authenticate_revision(api_client,revision_approval_url,owner_session=owner_session,
        revision=g5.REVISION,scope=g5.SCOPE,source_sha=source_sha,source_tree=source_tree,
        contract_sha256=g5.CONTRACT_SHA256,now=now)
    row=comment(launch_receipt_url,471)
    values=fields(row['body'])
    require(values.get('RC6_G5A_ROOT_SETUP_AUTHORIZATION')=='APPROVED'
        and values.get('G5A_REVISION_APPROVAL_URL')==revision_approval_url
        and values.get('GATES_AUTHORIZED')=='Horizon'
        and type(document(values.get('G5A_CAPABILITY_REFERENCE_JSON','null'))) is dict
        and type(document(values.get('G5A_BOOTSTRAP_REFERENCE_JSON','null'))) is dict,
        'G5A_SETUP_EXPLICIT_SOURCE_FIXED_STAGE_AND_NATIVE_QUOTA_REFERENCES_REQUIRED')
    observed=_admit_source_scope(source_sha=source_sha,source_tree=source_tree,
        launch_receipt_url=launch_receipt_url,owner_session=owner_session,gate='Horizon',now=now,
        g5a_approval=accepted)
    observed.update(g5a_capability_reference=document(values['G5A_CAPABILITY_REFERENCE_JSON']),
        g5a_bootstrap_reference=document(values['G5A_BOOTSTRAP_REFERENCE_JSON']),
        g5a_revision_approval_url=revision_approval_url,root_setup_only=True,
        G5_GREEN=False,native_archive_launch_authorized=False)
    args=SimpleNamespace(source_sha=source_sha,source_tree=source_tree,owner_session=owner_session,gate='Horizon')
    binding=carrier.capacity_binding(args,'Horizon-private-original-cas')
    result=object.__new__(G5ASetupLaunchContext)
    values=dict(pid=os.getpid(),source_sha=source_sha,source_tree=source_tree,owner_session=owner_session,
        launch_receipt_url=launch_receipt_url,revision=accepted,admission_wire=wire(observed),binding_wire=wire(binding))
    for name,value in values.items(): object.__setattr__(result,name,value)
    _G5A_SETUP_CONTEXTS[id(result)]=(result,values)
    return result


def validate_g5a_setup_launch(context,*,source_sha,source_tree,owner_session,launch_receipt_url,now=None):
    from scripts import rc6_contract_revisions as revisions
    from scripts import rc6_g5_contract as g5
    registered=_G5A_SETUP_CONTEXTS.get(id(context))
    require(type(context) is G5ASetupLaunchContext and registered is not None and registered[0] is context
        and context.pid==os.getpid() and all(getattr(context,name)==value for name,value in registered[1].items())
        and (context.source_sha,context.source_tree,context.owner_session,context.launch_receipt_url)
            ==(source_sha,source_tree,owner_session,launch_receipt_url),
        'G5A_SETUP_CURRENT_TYPED_SOURCE_STAGE_CONTEXT_REQUIRED')
    revisions.validate_revision_approval(context.revision,revision=g5.REVISION,scope=g5.SCOPE,
        source_sha=source_sha,source_tree=source_tree,contract_sha256=g5.CONTRACT_SHA256,now=now)
    return context.admission

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

def diagnostic_launch_control_fields(row,sha,tree,session,gate,*,get=None,anchors=None,now=None):
    """Immutable diagnostic authority, without Git/Source/event/file access."""
    if session!=SUCCESSOR_OWNER:
        return successor_diagnostic_control_fields(row,sha,tree,session,gate,get=get,anchors=anchors,now=now)
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

def diagnostic_launch_fields(row,sha,tree,session,gate,*,repo=ROOT,get=None,anchors=None,now=None,
                             native_finished=False):
    require(type(native_finished) is bool, 'DIAGNOSTIC_FIXED_CAPTURE_ONLY_ROLE_REQUIRED')
    f,scope,prior=(successor_diagnostic_control_fields(row,sha,tree,session,gate,
        get=get,anchors=anchors,now=now,authenticate_contract=False) if native_finished and session!=SUCCESSOR_OWNER
        else diagnostic_launch_control_fields(row,sha,tree,session,gate) if session==SUCCESSOR_OWNER
        else diagnostic_launch_control_fields(row,sha,tree,session,gate,get=get,anchors=anchors,now=now))
    from scripts.rc6_architectural_gates import productive_contract
    require(f.get('READ_CONTRACT_SHA256')==productive_contract(repo),'DIAGNOSTIC_PRODUCTIVE_CONTRACT_REBOUND')
    inventory=frozen_source_inventory(repo,sha,tree)
    require(hashlib.sha256(wire(inventory)).hexdigest()==f.get('SOURCE_MANIFEST_SHA256'),
        'DIAGNOSTIC_FROZEN_GIT_SOURCE_MANIFEST_REBOUND')
    if session!=SUCCESSOR_OWNER:
        require(f['CONTRACT_DOCUMENT_SHA256']==frozen_private_pid_contract(repo,inventory),
            'DIAGNOSTIC_V2_EXACT_SOURCE_CONTRACT_REBOUND')
        if gate=='capacity-calibration':
            authenticate_capability_reference(prior,sha,tree,f,reader=get or api,now=now)
    return f,scope,prior

def fresh_diagnostic_source(sha,tree,*,get=None,candidate=None):
    if candidate is not None:
        return fresh_successor_diagnostic_source(sha,tree,candidate,get=get)
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
    require(type(row) is dict and row.get('schema') in ('porota.rc6.capacity-diagnostic-admission.v1',DIAGNOSTIC_ADMISSION_SCHEMA)
        and row.get('qualification_claimed') is False,'DIAGNOSTIC_FULL_ADMISSION_REQUIRED_FOR_MONITOR')
    result={key:row.get(key) for key in ('launch_body_sha256','source_manifest_sha256','read_contract_sha256',
        'scope','capability_prerequisite')}
    require(all(re.fullmatch('[0-9a-f]{64}',result.get(key) or '') for key in
        ('launch_body_sha256','source_manifest_sha256','read_contract_sha256')),
        'DIAGNOSTIC_FULL_ADMISSION_PINNED_DIGEST_REQUIRED')
    diagnostic_scope(row['gate'],result['scope'])
    if row['schema']==DIAGNOSTIC_ADMISSION_SCHEMA:
        require(row.get('authorization_schema')==DIAGNOSTIC_AUTHORIZATION_SCHEMA
            and type(row.get('diagnostic_owner_scope')) is dict and type(row.get('contract_revision_binding')) is dict,
            'DIAGNOSTIC_V2_SOURCE_BOUND_CONTROLLER_PINS_REQUIRED')
        result.update(authorization_schema=DIAGNOSTIC_AUTHORIZATION_SCHEMA,
            diagnostic_owner_scope=row['diagnostic_owner_scope'],contract_revision_binding=row['contract_revision_binding'])
    return result

def owned_diagnostic_control_scope(*,source_sha,source_tree,launch_receipt_url,owner_session,gate,now,get,
                                   diagnostic_binding,run_id=None,run_attempt=None):
    """WIP control-plane read while Native is live; no checkout/Source/event."""
    if owner_session!=SUCCESSOR_OWNER:
        return owned_successor_diagnostic_control_scope(source_sha=source_sha,source_tree=source_tree,
            launch_receipt_url=launch_receipt_url,owner_session=owner_session,gate=gate,now=now,get=get,
            diagnostic_binding=diagnostic_binding,run_id=run_id,run_attempt=run_attempt)
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

def diagnostic_actions_origin(sha,gate,*,candidate=None,get=None):
    branch=DIAGNOSTIC_BRANCH if candidate is None else candidate['branch']
    require(os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch' and os.environ.get('GITHUB_RUN_ATTEMPT')=='1'
        and os.environ.get('GITHUB_SHA')==os.environ.get('GITHUB_WORKFLOW_SHA')==sha
        and os.environ.get('GITHUB_REF')=='refs/heads/'+branch
        and os.environ.get('GITHUB_WORKFLOW_REF')==REPO+'/.github/workflows/rc6-unified-candidate-tests.yml@refs/heads/'+branch
        and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted' and os.environ.get('RUNNER_OS')=='Linux'
        and os.environ.get('RUNNER_ARCH')=='X64','DIAGNOSTIC_EXACT_FIRST_ATTEMPT_STANDARD_ACTIONS_REQUIRED')
    reader=get or api
    identifier=int(os.environ['GITHUB_RUN_ID']);run=reader('/actions/runs/'+str(identifier))
    require(run.get('id')==identifier and run.get('head_sha')==sha and run.get('head_branch')==branch
        and run.get('run_attempt')==1 and run.get('event')=='workflow_dispatch'
        and run.get('status')=='in_progress' and run.get('repository',{}).get('id')==REPO_ID
        and run.get('path')=='.github/workflows/rc6-unified-candidate-tests.yml',
        'DIAGNOSTIC_ACTUAL_ACTIONS_RUN_ORIGIN_REBOUND')
    if candidate is not None:
        require(run.get('conclusion') is None and run.get('repository',{}).get('full_name')==REPO
            and run.get('head_repository',{}).get('id')==REPO_ID
            and run.get('display_title')=='RC6 material '+gate+' @ '+sha,
            'DIAGNOSTIC_V2_EXACT_OWNED_ACTIONS_SOURCE_REQUIRED')
    jobs=reader('/actions/runs/'+str(identifier)+'/attempts/1/jobs?per_page=100')
    require(jobs.get('total_count')==1 and type(jobs.get('jobs')) is list and len(jobs['jobs'])==1,
        'DIAGNOSTIC_SINGLE_ACTIONS_JOB_REQUIRED')
    job=jobs['jobs'][0]
    require(job.get('run_id')==identifier and type(job.get('id')) is int and job['id']>0
        and job.get('status')=='in_progress' and job.get('labels')==['ubuntu-24.04']
        and job.get('runner_group_name')=='GitHub Actions'
        and type(job.get('runner_name')) is str and job['runner_name'].startswith('GitHub Actions '),
        'DIAGNOSTIC_ACTUAL_STANDARD_HOSTED_RUNNER_REQUIRED')
    return {'run':run,'job':job,'dedup':dedup_admission(sha,gate)}

def admit_diagnostic(*,source_sha,source_tree,launch_receipt_url,owner_session,gate,now=None,native_finished=False):
    if owner_session!=SUCCESSOR_OWNER:
        return admit_successor_diagnostic(source_sha=source_sha,source_tree=source_tree,
            launch_receipt_url=launch_receipt_url,owner_session=owner_session,gate=gate,now=now,
            native_finished=native_finished)
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


class _ReadonlyRevisionGithub:
    """Adapter exposes only the injected authenticated GET reader."""
    def __init__(self,reader):self.reader=reader
    def request(self,path):return self.reader(path)


def successor_diagnostic_scope(auth,owner,anchors):
    """Scope is derived from authentic released-transfer and paired launch."""
    require(owner not in (RECOVERY_OWNER,SUCCESSOR_OWNER,CLOSURE_OWNER)
        and type(owner) is str and re.fullmatch(r'CODEX_[A-Z0-9_]{1,159}',owner),
        'DIAGNOSTIC_V2_AUTHENTIC_RELEASED_SUCCESSOR_REQUIRED')
    anchor=fields(anchors[471]['body']);branch=auth.get('SOURCE_BRANCH','')
    require(auth.get('AUTHORIZATION_SCHEMA')==DIAGNOSTIC_AUTHORIZATION_SCHEMA
        and anchor.get('SESSION_SUCCESSOR')==owner and anchor.get('SUCCESSION_KIND')==RELEASED_SUCCESSION_KIND
        and auth.get('WORKSTREAM_ID')==anchor.get('WORKSTREAM_ID')
        and branch==anchor.get('BRANCH') and branch not in ('main',BASE,BRANCH,DIAGNOSTIC_BRANCH)
        and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,199}',branch)
        and '..' not in branch and all(part not in ('','.','..') and not part.endswith(('.','.lock'))
            for part in branch.split('/'))
        and all(re.fullmatch('[0-9a-f]{40}',anchor.get(key,'')) for key in ('BASE_SHA','SOURCE_TREE')),
        'DIAGNOSTIC_V2_CURRENT_OWNER_BRANCH_WORKSTREAM_REQUIRED')
    require(auth.get('SOURCE_WIP') in (None,branch) and auth.get('SOURCE_PR') is None
        and auth.get('GATES_AUTHORIZED') is None
        and auth.get('RC6_MATERIAL_AUTOMATIC_PR_AUTHORIZATION') is None,
        'DIAGNOSTIC_V2_CANNOT_BORROW_CANDIDATE_OR_LEGACY_AUTHORITY')
    return {'authorization_schema':DIAGNOSTIC_AUTHORIZATION_SCHEMA,'branch':branch,
        'workstream_id':auth['WORKSTREAM_ID'],'owner_session':owner,
        'lineage_base_sha':anchor['BASE_SHA'],'lineage_base_tree':anchor['SOURCE_TREE']}


def _diagnostic_revision_pointer(auth,row,sha,tree,session,gate):
    return {'schema':'porota.rc6.contract-owner-source-pointer.v2','revision':'PRIVATE_PID_BOUNDARY_V2',
        'scope':gate,'source_sha':sha,'source_tree':tree,'owner_session':session,
        'contract_sha256':auth['CONTRACT_DOCUMENT_SHA256'],'approval_url':row['html_url'],
        'approval_body_sha256':hashlib.sha256(row['body'].encode()).hexdigest(),
        'serialized_pointer_grants_authority':False,'native_equivalence_claimed':False,'qualification_claimed':False}


def _authenticate_successor_revision(auth,row,sha,tree,session,gate,*,reader,now):
    from scripts import rc6_contract_revisions as revisions
    context=revisions.authenticate_revision(_ReadonlyRevisionGithub(reader),row['html_url'],
        owner_session=session,revision='PRIVATE_PID_BOUNDARY_V2',scope=gate,
        source_sha=sha,source_tree=tree,contract_sha256=auth['CONTRACT_DOCUMENT_SHA256'],now=now)
    revisions.validate_revision_approval(context,revision='PRIVATE_PID_BOUNDARY_V2',scope=gate,
        source_sha=sha,source_tree=tree,contract_sha256=auth['CONTRACT_DOCUMENT_SHA256'],now=now)
    require(context.approval_body_sha256==hashlib.sha256(row['body'].encode()).hexdigest(),
        'DIAGNOSTIC_V2_OWNER_ACCEPTANCE_BODY_REBOUND')
    return context


def successor_diagnostic_control_fields(row,sha,tree,session,gate,*,get=None,anchors=None,now=None,authenticate_contract=True):
    """API-only paired diagnostic/explicit contract decision; no Source reads."""
    reader=get or api;current=now or datetime.now(timezone.utc)
    require(gate in DIAGNOSTIC_GATES and all(re.fullmatch('[0-9a-f]{40}',value or '') for value in (sha,tree)),
        'DIAGNOSTIC_V2_EXACT_SOURCE_SCOPE_REQUIRED')
    require(session not in (RECOVERY_OWNER,SUCCESSOR_OWNER,CLOSURE_OWNER)
        and type(session) is str and re.fullmatch(r'CODEX_[A-Z0-9_]{1,159}',session),
        'DIAGNOSTIC_V2_AUTHENTIC_RELEASED_SUCCESSOR_REQUIRED')
    anchors=anchors or administrative_anchors(session,get=reader,now=current)
    endpoint=comment(row.get('html_url'),471,get=reader)
    require(same_immutable_comment(row,endpoint) and row['created_at']==row['updated_at'],
        'DIAGNOSTIC_V2_UNEDITED_ENDPOINT_IDENTITY_REQUIRED')
    auth=fields(row['body']);successor_diagnostic_scope(auth,session,anchors)
    expected={'RC6_CAPACITY_DIAGNOSTIC_AUTHORIZATION':'APPROVED','DIAGNOSTIC_MODE':gate,
        'SOURCE_SHA':sha,'SOURCE_TREE':tree,'SESSION_SUCCESSOR':session,'WRITE_OWNER':session,
        'INTEGRATION_OWNER':session,'DEPLOY_OWNER':'NOT_ACQUIRED','RELEASED':'false',
        'MODE':'PRODUCTION_PAPER / SIMULATION','real_orders_sent':'0',
        'CONTRACT_REVISION':'PRIVATE_PID_BOUNDARY_V2','CONTRACT_SCOPE':gate,
        'CONTRACT_ACCEPTANCE_SCHEMA':'porota.rc6.contract-revision-acceptance.v2',
        'CONTRACT_ACCEPTANCE_LIFECYCLE':DIAGNOSTIC_CONTRACT_LIFECYCLE,
        'CONTRACT_DECISION_ORIGIN':'EXPLICIT_OWNER_DECISION','RC6_CONTRACT_REVISION_AUTHORIZATION':'APPROVED',
        'CONTRACT_GATES_ALREADY_GREEN':'false','NATIVE_EQUIVALENCE_FROM_APPROVAL_CLAIMED':'false',
        'FINAL_CANDIDATE_ELIGIBLE':'false','DEPLOY_AUTHORIZATION':'NOT_GRANTED'}
    require(all(auth.get(key)==value for key,value in expected.items()),
        'DIAGNOSTIC_V2_EXPLICIT_SCOPED_OWNER_DECISION_REQUIRED')
    require(all(re.fullmatch('[0-9a-f]{64}',auth.get(key,'')) for key in
        ('READ_CONTRACT_SHA256','SOURCE_MANIFEST_SHA256','CONTRACT_DOCUMENT_SHA256')),
        'DIAGNOSTIC_V2_EXACT_SOURCE_CONTRACT_DIGEST_REQUIRED')
    twin_id=auth.get('OWNER_RECEIPT_473','')
    require(re.fullmatch('[1-9][0-9]{0,9}',twin_id),'DIAGNOSTIC_V2_DUAL_OPERATION_RECEIPT_REQUIRED')
    twin=comment('https://github.com/'+REPO+'/issues/473#issuecomment-'+twin_id,473,get=reader)
    twin_fields=fields(twin['body'])
    bindings=set(expected)|{'AUTHORIZATION_SCHEMA','WORKSTREAM_ID','SOURCE_BRANCH','READ_CONTRACT_SHA256',
        'SOURCE_MANIFEST_SHA256','CONTRACT_DOCUMENT_SHA256','CONTRACT_ACCEPTANCE_SCHEMA',
        'CAPACITY_DIAGNOSTIC_SCOPE_JSON','CAPACITY_DIAGNOSTIC_PREREQUISITES_JSON'}
    require(twin['created_at']==twin['updated_at']
        and hashlib.sha256(twin['body'].encode()).hexdigest()==auth.get('OWNER_RECEIPT_473_SHA256')
        and all(twin_fields.get(key)==auth.get(key) for key in bindings)
        and all(stamp(row['created_at'])>=stamp(anchor['created_at']) for anchor in anchors.values())
        and all(stamp(twin['created_at'])>=stamp(anchor['created_at']) for anchor in anchors.values()),
        'DIAGNOSTIC_V2_AUTHENTIC_PAIRED_OPERATION_REQUIRED')
    scope=diagnostic_scope(gate,document(auth.get('CAPACITY_DIAGNOSTIC_SCOPE_JSON','null')))
    prior=document(auth.get('CAPACITY_DIAGNOSTIC_PREREQUISITES_JSON','null'))
    require((gate=='capacity-probe' and prior is None) or (gate=='capacity-calibration' and type(prior) is dict),
        'DIAGNOSTIC_CALIBRATION_REQUIRES_ACTUAL_CAPABILITY_ARTIFACT')
    # A Boolean, CI result, transfer or native-prerequisite authorization
    # cannot replace the explicit paired contract decision authenticated here.
    if authenticate_contract:
        _authenticate_successor_revision(auth,row,sha,tree,session,gate,reader=reader,now=current)
    return auth,scope,prior


def frozen_private_pid_contract(repo,inventory):
    """Canonical proposed contract comes from this exact frozen Source file."""
    from scripts.rc6_private_pid_boundary import MEMBER
    root=Path(repo).absolute();path=root/MEMBER
    entry=next((item for item in inventory['files'] if item['path']==MEMBER),None)
    require(entry is not None and not any(item.is_symlink() for item in (path,*path.parents)),
        'DIAGNOSTIC_V2_FROZEN_CONTRACT_MODULE_REBOUND')
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NOATIME|os.O_CLOEXEC)
    try:
        st=os.fstat(descriptor);before={key:getattr(st,key) for key in STAT11}
        require(stat.S_ISREG(st.st_mode) and 0<st.st_size<=CONTROL_CAP,
            'DIAGNOSTIC_V2_FROZEN_CONTRACT_FILE_BOUND')
        raw=os.read(descriptor,CONTROL_CAP+1)
        require(len(raw)==st.st_size and hashlib.sha256(raw).hexdigest()==entry['sha256']
            and before=={key:getattr(os.fstat(descriptor),key) for key in STAT11}
            =={key:getattr(path.lstat(),key) for key in STAT11},
            'DIAGNOSTIC_V2_FROZEN_CONTRACT_MODULE_REBOUND')
    finally:os.close(descriptor)
    # Execute the one already authenticated capture, never reopen a mutable
    # path between digest verification and extracting the canonical contract.
    values={'__file__':str(path),'__name__':'_rc6_frozen_private_pid_contract'}
    exec(compile(raw,str(path),'exec'),values)
    require(values.get('REVISION')=='PRIVATE_PID_BOUNDARY_V2'
        and callable(values.get('contract_sha256')),'DIAGNOSTIC_V2_EXACT_CONTRACT_IMPLEMENTATION_REQUIRED')
    result=values['contract_sha256']()
    require(re.fullmatch('[0-9a-f]{64}',result or ''),'DIAGNOSTIC_V2_CANONICAL_CONTRACT_DIGEST_REQUIRED')
    return result


def fresh_successor_diagnostic_source(sha,tree,candidate,*,get=None):
    """READ_ONLY ref/commit lineage of the actual released successor branch."""
    require(type(candidate) is dict and set(candidate)=={'authorization_schema','branch','workstream_id',
        'owner_session','lineage_base_sha','lineage_base_tree'}
        and candidate['authorization_schema']==DIAGNOSTIC_AUTHORIZATION_SCHEMA
        and all(re.fullmatch('[0-9a-f]{40}',value or '') for value in
            (sha,tree,candidate['lineage_base_sha'],candidate['lineage_base_tree'])),
        'DIAGNOSTIC_V2_AUTHENTICATED_SOURCE_SCOPE_REQUIRED')
    reader=get or api;branch=candidate['branch']
    repository=reader('');ref=reader('/git/ref/heads/'+branch);commit=reader('/git/commits/'+sha)
    require(repository.get('id')==REPO_ID and repository.get('full_name')==REPO
        and repository.get('private') is False and ref.get('object',{}).get('sha')==sha
        and commit.get('sha')==sha and commit.get('tree',{}).get('sha')==tree,
        'DIAGNOSTIC_V2_CURRENT_BRANCH_HEAD_TREE_REBOUND')
    lineage=[]
    for _ in range(32):
        if commit['sha']==candidate['lineage_base_sha']:break
        parents=commit.get('parents')
        require(type(parents) is list and len(parents)==1 and re.fullmatch('[0-9a-f]{40}',parents[0].get('sha','')),
            'DIAGNOSTIC_V2_SINGLE_PARENT_FIX_FORWARD_REQUIRED')
        lineage.append(commit['sha']);commit=reader('/git/commits/'+parents[0]['sha'])
        require(commit.get('sha')==parents[0]['sha'],'DIAGNOSTIC_V2_GIT_PARENT_REBOUND')
    else:raise ValueError('DIAGNOSTIC_V2_FIX_FORWARD_LINEAGE_LIMIT')
    require(bool(lineage) and commit.get('tree',{}).get('sha')==candidate['lineage_base_tree'],
        'DIAGNOSTIC_V2_AUTHENTIC_RELEASE_BASE_TREE_REQUIRED')
    return {'wip_branch':branch,'source_sha':sha,'source_tree':tree,
        'single_parent_fix_forward_lineage':lineage,'lineage_base_sha':candidate['lineage_base_sha'],
        'lineage_base_tree':candidate['lineage_base_tree'],'repository_public_verified':True,
        'anonymous_source_origin':'https://github.com/'+REPO+'.git','qualification_claimed':False}


def authenticate_capability_reference(reference,sha,tree,auth,*,reader,now=None):
    """Authenticate origin only; native bytes are still mandatory in issuer.

    No producer/image may start until verify_capacity_capability_artifact has
    downloaded and replayed the original quota/EDQUOT/FIN controls. This API
    precheck never turns a receipt-shaped object or Actions success into proof.
    """
    require(type(reference) is dict and reference.get('source_sha')==sha and reference.get('source_tree')==tree
        and reference.get('contract_revision')=='PRIVATE_PID_BOUNDARY_V2'
        and reference.get('contract_document_sha256')==auth['CONTRACT_DOCUMENT_SHA256']
        and type(reference.get('artifact_id')) is int and reference['artifact_id']>0
        and type(reference.get('run_id')) is int and reference['run_id']>0
        and type(reference.get('run_attempt')) is int and reference['run_attempt']==1
        and re.fullmatch('sha256:[0-9a-f]{64}',reference.get('artifact_digest',''))
        and reference.get('receipt_member')=='diagnostic-result.json'
        and re.fullmatch('[0-9a-f]{64}',reference.get('receipt_sha256','')),
        'DIAGNOSTIC_V2_BOOTSTRAP_REQUIRES_EXACT_NATIVE_CAPABILITY_ARTIFACT')
    from scripts.rc6_architectural_gates import artifact_origin,actions_runner_origin
    artifact=reader('/actions/artifacts/'+str(reference['artifact_id']))
    run=reader('/actions/runs/'+str(reference['run_id']))
    attempt=reader('/actions/runs/'+str(reference['run_id'])+'/attempts/1')
    artifact_origin(reference,artifact,run,attempt,source_sha=sha)
    branch=auth['SOURCE_BRANCH']
    require(run.get('path')=='.github/workflows/rc6-unified-candidate-tests.yml'
        and run.get('event')=='workflow_dispatch' and run.get('head_branch')==branch
        and run.get('display_title')=='RC6 material capacity-probe @ '+sha,
        'DIAGNOSTIC_V2_CAPABILITY_ACTUAL_PROBE_BRANCH_REQUIRED')
    actions_runner_origin(reader('/actions/runs/'+str(reference['run_id'])+'/attempts/1/jobs?per_page=100'),
        run_id=reference['run_id'])
    return {'artifact_id':reference['artifact_id'],'run_id':reference['run_id'],
        'source_sha':sha,'source_tree':tree,'artifact_origin_authenticated':True,
        'native_payload_verification_required':True,'capability_proved_from_metadata':False,'qualification_claimed':False}


def reauthenticate_diagnostic_revision(admission_row,*,get=None,now=None,repo=ROOT):
    """Issue in-process approval after independently re-reading the pointer.

    The serialized admission is only a Source-bound lookup. It is never a
    constructor, native equivalence proof or quota-launch flag for an issuer.
    """
    require(type(admission_row) is dict and admission_row.get('schema')==DIAGNOSTIC_ADMISSION_SCHEMA
        and admission_row.get('status')=='ADMITTED_DIAGNOSTIC_NOT_STARTED'
        and admission_row.get('qualification_claimed') is False,
        'DIAGNOSTIC_V2_SOURCE_POINTER_REQUIRED')
    reader=get or api;current=now or datetime.now(timezone.utc)
    sha,tree,owner,gate=(admission_row[key] for key in ('source_sha','source_tree','owner_session','gate'))
    launch=comment(admission_row.get('launch_receipt_url'),471,get=reader)
    auth,scope,prior=diagnostic_launch_fields(launch,sha,tree,owner,gate,repo=repo,get=reader,now=current)
    anchors=administrative_anchors(owner,get=reader,now=current)
    owner_scope=successor_diagnostic_scope(auth,owner,anchors)
    require(admission_row.get('contract_revision_binding')==_diagnostic_revision_pointer(auth,launch,sha,tree,owner,gate)
        and admission_row.get('diagnostic_owner_scope')==owner_scope
        and admission_row.get('launch_body_sha256')==hashlib.sha256(launch['body'].encode()).hexdigest()
        and admission_row.get('scope')==scope and admission_row.get('capability_prerequisite')==prior,
        'DIAGNOSTIC_V2_SERIALIZED_SOURCE_POINTER_REBOUND')
    fresh_diagnostic_source(sha,tree,get=reader,candidate=owner_scope)
    return _authenticate_successor_revision(auth,launch,sha,tree,owner,gate,reader=reader,now=current)


def _successor_diagnostic_owners(auth,owner,sha,tree,*,anchors,reader,now):
    timelines={issue:recent(issue,anchors[issue],now,get=reader) for issue in (471,473)}
    owners={str(issue):latest_writer(timelines[issue],issue,sha,tree,owner,now) for issue in (471,473)}
    require(all(fields(row['body']).get('WORKSTREAM_ID')==auth['WORKSTREAM_ID']
        for issue in (471,473) for row in timelines[issue] if row['id']==owners[str(issue)]['comment_id']),
        'DIAGNOSTIC_V2_LIVE_WORKSTREAM_REBOUND')
    return owners


def _successor_diagnostic_decision_not_revoked(auth,launch,owner,*,anchors,reader,now):
    """Re-read updated history; a later lease cannot hide owner revocation."""
    since=stamp(launch['created_at'])
    for issue in (471,473):
        for row in recent(issue,anchors[issue],now,get=reader):
            value=fields(row['body'])
            if (row['user']['login']=='mbalbo2023' and effective_stamp(row)>=since
                    and value.get('SOURCE_SHA')==auth['SOURCE_SHA']
                    and value.get('CONTRACT_REVISION')=='PRIVATE_PID_BOUNDARY_V2'
                    and value.get('CONTRACT_SCOPE') in (auth['DIAGNOSTIC_MODE'],'ALL')):
                require(value.get('RC6_CONTRACT_REVISION_AUTHORIZATION') not in
                    ('REVOKED','NOT_GRANTED','REJECTED','CANCELLED')
                    and value.get('RC6_CAPACITY_DIAGNOSTIC_AUTHORIZATION') not in
                    ('REVOKED','NOT_GRANTED','REJECTED','CANCELLED'),
                    'DIAGNOSTIC_V2_OWNER_CONTRACT_OR_LAUNCH_REVOKED')


def owned_successor_diagnostic_control_scope(*,source_sha,source_tree,launch_receipt_url,owner_session,gate,
        now,get,diagnostic_binding,run_id=None,run_attempt=None):
    """API-only live V2 read; all authority is freshly authenticated."""
    attempt=int(os.environ['GITHUB_RUN_ATTEMPT']) if run_attempt is None else run_attempt
    identifier=int(os.environ['GITHUB_RUN_ID']) if run_id is None else run_id
    require(gate in DIAGNOSTIC_GATES and type(attempt) is int and attempt==1
        and type(identifier) is int and identifier>0,'DIAGNOSTIC_V2_FIRST_NATIVE_ATTEMPT_REQUIRED')
    anchors=administrative_anchors(owner_session,get=get,now=now)
    launch=comment(launch_receipt_url,471,get=get)
    # Initial/prelaunch admission separately mints the short-lived typed
    # approval. Polling preserves that launch's immutable explicit acceptance
    # only under its owner-approved lifecycle and a fresh independent lease.
    # It neither renews the expired lease in the original comment nor mints
    # an approval. Its exact pins must match the earlier full admission.
    auth,scope,prior=successor_diagnostic_control_fields(launch,source_sha,source_tree,owner_session,gate,
        get=get,anchors=anchors,now=now,authenticate_contract=False)
    owner_scope=successor_diagnostic_scope(auth,owner_session,anchors)
    pointer=_diagnostic_revision_pointer(auth,launch,source_sha,source_tree,owner_session,gate)
    expected={'launch_body_sha256':hashlib.sha256(launch['body'].encode()).hexdigest(),
        'source_manifest_sha256':auth['SOURCE_MANIFEST_SHA256'],'read_contract_sha256':auth['READ_CONTRACT_SHA256'],
        'scope':scope,'capability_prerequisite':prior,'authorization_schema':DIAGNOSTIC_AUTHORIZATION_SCHEMA,
        'diagnostic_owner_scope':owner_scope,'contract_revision_binding':pointer}
    require(type(diagnostic_binding) is dict and diagnostic_binding==expected,
        'DIAGNOSTIC_V2_FULL_ADMISSION_PINS_REBOUND')
    source=fresh_diagnostic_source(source_sha,source_tree,get=get,candidate=owner_scope)
    owners=_successor_diagnostic_owners(auth,owner_session,source_sha,source_tree,anchors=anchors,reader=get,now=now)
    _successor_diagnostic_decision_not_revoked(auth,launch,owner_session,anchors=anchors,reader=get,now=now)
    recovery=recovery_anchors(RECOVERY_OWNER,get=get)
    ops=ops_admission(auth,owner_session,now,recovery,get=get)
    run=get('/actions/runs/'+str(identifier)+'/attempts/1')
    require(run.get('id')==identifier and run.get('run_attempt')==1 and run.get('head_sha')==source_sha
        and run.get('head_branch')==owner_scope['branch'] and run.get('event')=='workflow_dispatch'
        and run.get('status')=='in_progress' and run.get('conclusion') is None
        and run.get('repository',{}).get('id')==REPO_ID and run['repository'].get('full_name')==REPO
        and run.get('head_repository',{}).get('id')==REPO_ID
        and run.get('path')=='.github/workflows/rc6-unified-candidate-tests.yml',
        'OWNED_DIAGNOSTIC_V2_ACTIONS_CANCELLED_OR_REBOUND')
    return {'source_sha':source_sha,'source_tree':source_tree,'owner_session':owner_session,'gate':gate,
        'run_id':identifier,'run_attempt':1,'read_utc':now.isoformat().replace('+00:00','Z'),
        'launch_receipt_url':launch_receipt_url,'launch_body_sha256':expected['launch_body_sha256'],
        'diagnostic_binding':diagnostic_binding,'source':source,'fresh_ownership':owners,'ops':ops,
        'workstream_id':owner_scope['workstream_id'],'real_orders_sent':0,'DEPLOY_OWNER':'NOT_ACQUIRED',
        'qualification_claimed':False}


def admit_successor_diagnostic(*,source_sha,source_tree,launch_receipt_url,owner_session,gate,now=None,
                               native_finished=False):
    """Separate V2 path; legacy accepted admission/replay bytes stay intact."""
    require(os.getuid()==os.geteuid()>0 and os.environ.get('GITHUB_REPOSITORY')==REPO
        and os.environ.get('GITHUB_REPOSITORY_ID')==str(REPO_ID),'ACTUAL_NONROOT_CANONICAL_ACTIONS_REQUIRED')
    require(gate in DIAGNOSTIC_GATES and owner_session not in (RECOVERY_OWNER,SUCCESSOR_OWNER,CLOSURE_OWNER)
        and type(owner_session) is str and re.fullmatch(r'CODEX_[A-Z0-9_]{1,159}',owner_session),
        'DIAGNOSTIC_V2_AUTHENTIC_RELEASED_SUCCESSOR_REQUIRED')
    current=now or datetime.now(timezone.utc)
    branch=os.environ.get('GITHUB_REF','').removeprefix('refs/heads/')
    event,event_custody=actual_event(gate,diagnostic_branch=branch)
    require(event.get('inputs',{}).get('source_sha')==source_sha and event['inputs'].get('source_tree')==source_tree
        and event['inputs'].get('launch_receipt_url')==launch_receipt_url
        and event['inputs'].get('owner_session')==owner_session,'DIAGNOSTIC_EVENT_LAUNCH_BINDING_REBOUND')
    anchors=administrative_anchors(owner_session,now=current)
    launch=comment(launch_receipt_url,471)
    auth,scope,prior=diagnostic_launch_fields(launch,source_sha,source_tree,owner_session,gate,
        anchors=anchors,now=current,native_finished=native_finished)
    owner_scope=successor_diagnostic_scope(auth,owner_session,anchors)
    require(branch==owner_scope['branch'],'DIAGNOSTIC_V2_DISPATCH_BRANCH_REBOUND')
    source=fresh_diagnostic_source(source_sha,source_tree,candidate=owner_scope)
    origin=diagnostic_actions_origin(source_sha,gate,candidate=owner_scope)
    owners=_successor_diagnostic_owners(auth,owner_session,source_sha,source_tree,anchors=anchors,reader=api,now=current)
    _successor_diagnostic_decision_not_revoked(auth,launch,owner_session,anchors=anchors,reader=api,now=current)
    recovery=recovery_anchors(RECOVERY_OWNER)
    ops=ops_admission(auth,owner_session,current,recovery)
    return {'schema':DIAGNOSTIC_ADMISSION_SCHEMA,'authorization_schema':DIAGNOSTIC_AUTHORIZATION_SCHEMA,
        'status':('CAPTURE_ONLY_NATIVE_ALREADY_FINISHED' if native_finished else
            'ADMITTED_DIAGNOSTIC_NOT_STARTED'),'source_sha':source_sha,'source_tree':source_tree,
        'gate':gate,'owner_session':owner_session,'workstream_id':owner_scope['workstream_id'],
        'diagnostic_owner_scope':owner_scope,'contract_revision_binding':_diagnostic_revision_pointer(auth,launch,source_sha,source_tree,owner_session,gate),
        'launch_receipt_url':launch_receipt_url,'launch_body_sha256':hashlib.sha256(launch['body'].encode()).hexdigest(),
        'source_manifest_sha256':auth['SOURCE_MANIFEST_SHA256'],'read_contract_sha256':auth['READ_CONTRACT_SHA256'],
        'scope':scope,'capability_prerequisite':prior,'fresh_ownership':owners,'ops':ops,'event_custody':event_custody,
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
