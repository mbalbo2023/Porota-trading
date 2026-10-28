#!/usr/bin/env python3
"""Archive one A138 host receipt, then reclaim only its failed attempt staging."""
from __future__ import annotations

import base64
import fcntl
import hashlib
import http.client
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

STAGE = Path('/tmp/porota-deploy-v2-dd22a1b0761cc4f231813edb6b502d1dd21bb8de-40592426296-2')
ARCHIVE_BASE = Path('/var/lib/porota-rc6-forensics')
ARCHIVE = ARCHIVE_BASE/'host-control-plane-applied-a138-dd22a1b0761cc4f231813edb6b502d1dd21bb8de-40592426296-2.json'
QUARANTINE = ARCHIVE_BASE/'quarantine-a138-dd22a1b0761cc4f231813edb6b502d1dd21bb8de-40592426296-2'
RECEIPT = 'host-control-plane-applied.json'
RECEIPT_HASH = '20c6a3dc6a9fa0377032d26d17d8e4626c2ef4745e4c866155f004fabd9b0f41'
HOST_DEV = 64769
STAGE_UID = 1000
FORENSIC_UID = 0
STAGE_INODE = 855
INNER_INODE = 3481
RECEIPT_INODE = 30841
FROZEN_SIZES = {
    'porota-frozen-candidate.json':610,
    'porota-predeploy-image.tar.gz':278661835,
    'porota-deploy-bundle-v2.tgz':1701819,
    'porota-deploy-bundle-v2-manifest.json':91356,
    'porota-host-manifest-v2.json':33622,
    'porota-host-policy-v2.json':184,
}
DIRECT = set(FROZEN_SIZES) | {'stage', RECEIPT}
ACTIVE = {
    'porota_production_observer': ('6b66ba77e9fc','sha256:90e54acb1189a03acbed4b9228943ab556bdbe3a01fd515285951fc90eda93e7'),
    'porota_production_dashboard': ('a854c6930cdd','sha256:90e54acb1189a03acbed4b9228943ab556bdbe3a01fd515285951fc90eda93e7'),
    'porota_critical_approval_rc6': ('0d566777f5dc','sha256:586464c20b74ab3a7ba842df8dfafcd76f21eb4c7bed553f23f6f187924ff735'),
}
LOCK = Path('/run/lock/porota-rc6-deploy-v2.lock')
BARRIER = LOCK.parent/'porota-rc6-deploy-v2-attempt.json'


def fail(code):
    raise RuntimeError(code)


def metadata(part):
    c=http.client.HTTPConnection('169.254.169.254',timeout=2)
    try:
        c.request('GET','/metadata/v1/'+part)
        r=c.getresponse()
        if r.status!=200: fail('DROPLET_METADATA_RED')
        return r.read(128).decode('ascii').strip()
    finally:
        c.close()


def free():
    v=os.statvfs('/')
    return {'bytes':v.f_bavail*v.f_frsize,'free_inodes':v.f_favail}


def sha_file(path,expected_bytes):
    h=hashlib.sha256()
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as f:
        s=os.fstat(f.fileno())
        if (not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or
            s.st_dev!=HOST_DEV or s.st_size!=expected_bytes):
            fail('HASH_INPUT_ALIAS_RED')
        remaining=expected_bytes
        while remaining:
            chunk=f.read(min(1024*1024,remaining))
            if not chunk: fail('HASH_INPUT_TRUNCATED_RED')
            h.update(chunk)
            remaining-=len(chunk)
        if f.read(1): fail('HASH_INPUT_GREW_RED')
    return h.hexdigest()


def artifact_hashes():
    value=os.environ.get('RC6_A159_FROZEN_HASHES_B64','')
    if not re.fullmatch(r'[A-Za-z0-9+/=]{300,1000}',value):
        fail('ARTIFACT_HASH_INPUT_RED')
    q=json.loads(base64.b64decode(value,validate=True))
    if (not isinstance(q,dict) or set(q)!=DIRECT-{'stage',RECEIPT} or
        any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for v in q.values())):
        fail('ARTIFACT_HASH_SET_RED')
    return q


def acquire_lock():
    d=os.lstat(LOCK.parent)
    if not stat.S_ISDIR(d.st_mode) or d.st_uid!=0: fail('LOCK_PARENT_RED')
    fd=os.open(LOCK,os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        s,p=os.fstat(fd),os.lstat(LOCK)
        if (not stat.S_ISREG(s.st_mode) or s.st_uid!=0 or
            stat.S_IMODE(s.st_mode)!=0o600 or s.st_nlink!=1 or
            (s.st_dev,s.st_ino)!=(p.st_dev,p.st_ino)):
            fail('LOCK_IDENTITY_RED')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (os.lstat(LOCK).st_dev,os.lstat(LOCK).st_ino)!=(s.st_dev,s.st_ino):
            fail('LOCK_REPLACED')
        barrier_safe()
        return fd
    except BaseException:
        os.close(fd)
        raise


def barrier_safe():
    if not BARRIER.exists() and not BARRIER.is_symlink():
        return 'ABSENT'
    s=os.lstat(BARRIER)
    if (not stat.S_ISREG(s.st_mode) or s.st_uid!=0 or
        stat.S_IMODE(s.st_mode)!=0o600 or s.st_nlink!=1 or s.st_size>1024):
        fail('BARRIER_IDENTITY_RED')
    q=json.loads(BARRIER.read_text())
    phases={'reconcile','classify','offline_preclean','preclean','suspend','drain',
            'transfer','promotion','flat_book','verify_carryover','timers_restore',
            'timers_restored','backup_retire','finalize','cleanup_contract',
            'cleanup_recover','cleanup_restore','cleanup_final'}
    if (not isinstance(q,dict) or
        not isinstance(q.get('candidate'),str) or
        not re.fullmatch('[0-9a-f]{40}',q['candidate']) or
        not isinstance(q.get('run'),str) or not re.fullmatch('[1-9][0-9]*',q['run']) or
        not isinstance(q.get('attempt'),str) or not re.fullmatch('[1-9][0-9]*',q['attempt']) or
        q.get('phase') not in phases or q.get('status') not in {'IDLE','INFLIGHT','CONTRACT_ONLY'}):
        fail('BARRIER_SCHEMA_RED')
    if q['status']!='IDLE': fail('BARRIER_ACTIVE')
    return 'IDLE'


def docker(args):
    p=subprocess.run(['docker',*args],capture_output=True,text=True,
                     timeout=5,check=False)
    if p.returncode: fail('DOCKER_INSPECT_RED')
    return p.stdout.strip()


def overlaps(a,b):
    return a==b or a in b.parents or b in a.parents


def container_snapshot():
    ids=docker(['container','ls','-aq','--no-trunc']).splitlines()
    if len(ids)!=3: fail('GLOBAL_CONTAINER_SET_RED')
    result={}
    mount_hits=0
    for cid in ids:
        raw=docker(['inspect','--type=container','--format',
                    '{{.Id}}|{{.Name}}|{{.Image}}|{{.State.Status}}|{{.RestartCount}}|{{json .Mounts}}',cid])
        parts=raw.split('|')
        if len(parts)!=6 or parts[0]!=cid: fail('CONTAINER_INSPECT_RED')
        name=parts[1].lstrip('/')
        if (name not in ACTIVE or not cid.startswith(ACTIVE[name][0]) or
            parts[2]!=ACTIVE[name][1] or parts[3]!='running'):
            fail('ACTIVE_CONTAINER_CHANGED')
        mounts=json.loads(parts[5])
        if not isinstance(mounts,list): fail('MOUNT_METADATA_RED')
        for m in mounts:
            src=m.get('Source')
            if isinstance(src,str) and src.startswith('/') and overlaps(STAGE,Path(src)):
                mount_hits+=1
        result[name]=(cid,parts[2],parts[3],parts[4])
    if set(result)!=set(ACTIVE) or mount_hits: fail('STAGE_MOUNT_OR_CONTAINER_RED')
    return result


def open_refs():
    deadline=time.monotonic()+4
    pids=fds=hits=0
    prefix=str(STAGE)+'/'
    try:
        for proc in os.scandir('/proc'):
            if not proc.name.isdigit(): continue
            pids+=1
            if pids>500 or time.monotonic()>deadline: fail('PROC_CAP')
            root=Path('/proc')/proc.name
            try:
                target=os.readlink(root/'cwd')
                hits+=int(target==str(STAGE) or target.startswith(prefix))
            except FileNotFoundError:
                pass
            try:
                with os.scandir(root/'fd') as entries:
                    per=0
                    for ent in entries:
                        per+=1;fds+=1
                        if per>256 or fds>5000 or time.monotonic()>deadline: fail('PROC_CAP')
                        try:
                            target=os.readlink(ent.path)
                            hits+=int(target==str(STAGE) or target.startswith(prefix))
                        except FileNotFoundError:
                            pass
            except FileNotFoundError:
                pass
    except OSError:
        fail('PROC_PERMISSION_RED')
    if hits: fail('STAGE_OPEN_FD_RED')
    return {'pids':pids,'fds':fds,'stage_refs':hits}


def validate_receipt(raw):
    if len(raw)!=11851 or hashlib.sha256(raw).hexdigest()!=RECEIPT_HASH:
        fail('HOST_RECEIPT_LOG_HASH_RED')
    q=json.loads(raw)
    if (set(q)!={'changed','count','legacy_retired_count','status','verification'} or
        q['status']!='GREEN' or q['count']!=44 or
        len(q['changed'])!=46 or len(q['verification'])!=46):
        fail('HOST_RECEIPT_SCHEMA_RED')
    return q


def stage_tree(base,dev):
    deadline=time.monotonic()+4
    out={'regular':0,'directories':0,'entries':0,'allocated':0,
         'backup_named':0,'db_files':0}
    stack=[base]
    regular={}
    while stack:
        p=stack.pop()
        with os.scandir(p) as entries:
            for ent in entries:
                out['entries']+=1
                if out['entries']>2000 or time.monotonic()>deadline: fail('STAGE_TREE_CAP')
                x=Path(ent.path);s=os.lstat(x)
                if s.st_dev!=dev or s.st_uid!=STAGE_UID: fail('STAGE_TREE_OWNER_RED')
                if stat.S_ISLNK(s.st_mode) or (stat.S_ISREG(s.st_mode) and s.st_nlink!=1):
                    fail('STAGE_TREE_ALIAS_RED')
                out['allocated']+=s.st_blocks*512
                if stat.S_ISDIR(s.st_mode):
                    if os.path.ismount(x): fail('STAGE_NESTED_MOUNT_RED')
                    out['directories']+=1;stack.append(x)
                elif stat.S_ISREG(s.st_mode):
                    out['regular']+=1
                    regular[x.relative_to(base).as_posix()]=s.st_size
                else: fail('STAGE_TREE_TYPE_RED')
                out['backup_named']+=int(any('backup' in part.lower() for part in x.parts))
                out['db_files']+=int(x.suffix.lower() in {'.db','.sqlite','.sqlite3'} or
                                     x.name.lower().endswith(('.db.gz','.sqlite.gz','.backup','.bak')))
    if (out['entries'],out['regular'],out['directories'],out['backup_named'],out['db_files'],out['allocated'])!=(562,547,15,10,0,7561216):
        fail('STAGE_TREE_DRIFT')
    return out,regular


def staged_bundle_matches_artifact(base,regular):
    manifest=json.loads((base/'porota-deploy-bundle-v2-manifest.json').read_bytes())
    if (manifest.get('schema_version')!=1 or manifest.get('status')!='GREEN' or
        manifest.get('file_count')!=546 or not isinstance(manifest.get('files'),list) or
        len(manifest['files'])!=546 or not re.fullmatch('[0-9a-f]{64}',manifest.get('bundle_sha256',''))):
        fail('BUNDLE_MANIFEST_SCHEMA_RED')
    if regular.get('POROTA_DEPLOY_BUNDLE_MANIFEST.json',100001)>100000:
        fail('EMBEDDED_MANIFEST_CAP')
    embedded=json.loads((base/'stage/POROTA_DEPLOY_BUNDLE_MANIFEST.json').read_bytes())
    without_bundle={k:v for k,v in manifest.items() if k!='bundle_sha256'}
    if embedded!=without_bundle: fail('STAGED_EMBEDDED_MANIFEST_DRIFT')
    expected={'POROTA_DEPLOY_BUNDLE_MANIFEST.json'}
    total_bytes=0
    for row in manifest['files']:
        if not isinstance(row,dict) or set(row)!={'path','sha256','bytes'}:
            fail('BUNDLE_MANIFEST_ROW_RED')
        rel=row['path']
        p=PurePosixPath(rel)
        if (not isinstance(rel,str) or rel!=str(p) or p.is_absolute() or
            not p.parts or any(x in ('.','..') for x in p.parts) or
            rel in expected or not re.fullmatch('[0-9a-f]{64}',row['sha256']) or
            not isinstance(row['bytes'],int) or row['bytes']<0 or row['bytes']>15000000):
            fail('BUNDLE_MANIFEST_PATH_RED')
        expected.add(rel)
        total_bytes+=row['bytes']
        if total_bytes>15000000: fail('STAGED_SOURCE_BYTES_CAP')
        if regular.get(rel)!=row['bytes'] or sha_file(base/'stage'/rel,row['bytes'])!=row['sha256']:
            fail('STAGED_BUNDLE_FILE_DRIFT')
    if set(regular)!=expected: fail('STAGED_BUNDLE_FILE_SET_DRIFT')
    if sha_file(base/'porota-deploy-bundle-v2.tgz',FROZEN_SIZES['porota-deploy-bundle-v2.tgz'])!=manifest['bundle_sha256']:
        fail('STAGED_BUNDLE_TAR_DRIFT')


def stage_snapshot(frozen,base=STAGE):
    s=os.lstat(base)
    if (s.st_ino,s.st_dev,s.st_uid,stat.S_IMODE(s.st_mode),s.st_nlink)!=(STAGE_INODE,HOST_DEV,STAGE_UID,0o700,3):
        fail('STAGE_INODE_DRIFT')
    if not stat.S_ISDIR(s.st_mode) or os.path.ismount(base): fail('STAGE_TYPE_OR_MOUNT_RED')
    entries=list(base.iterdir())
    if {x.name for x in entries}!=DIRECT: fail('STAGE_DIRECT_SET_DRIFT')
    for name,size in FROZEN_SIZES.items():
        f=os.lstat(base/name)
        if (not stat.S_ISREG(f.st_mode) or f.st_dev!=s.st_dev or
            f.st_uid!=STAGE_UID or f.st_nlink!=1 or f.st_size!=size):
            fail('FROZEN_STAGE_MEMBER_DRIFT')
        if sha_file(base/name,size)!=frozen[name]: fail('FROZEN_ARTIFACT_CONTENT_DRIFT')
    inner=os.lstat(base/'stage')
    if (not stat.S_ISDIR(inner.st_mode) or inner.st_ino!=INNER_INODE or
        inner.st_dev!=s.st_dev or inner.st_uid!=STAGE_UID or os.path.ismount(base/'stage')):
        fail('STAGE_INNER_DRIFT')
    tree,regular=stage_tree(base/'stage',s.st_dev)
    staged_bundle_matches_artifact(base,regular)
    receipt=base/RECEIPT
    f=os.lstat(receipt)
    if (not stat.S_ISREG(f.st_mode) or (f.st_ino,f.st_dev,f.st_uid,f.st_nlink,f.st_size)!=(RECEIPT_INODE,HOST_DEV,0,1,11851)):
        fail('HOST_RECEIPT_INODE_DRIFT')
    raw=receipt.read_bytes()
    validate_receipt(raw)
    return {'stage_inode':s.st_ino,'receipt_inode':f.st_ino,
            'receipt_sha256':RECEIPT_HASH,'tree':tree},raw


def archive_receipt(raw):
    parent=os.lstat(ARCHIVE_BASE.parent)
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=FORENSIC_UID or parent.st_dev!=HOST_DEV:
        fail('FORENSIC_PARENT_RED')
    created=not ARCHIVE_BASE.exists() and not ARCHIVE_BASE.is_symlink()
    if created:
        os.mkdir(ARCHIVE_BASE,0o700)
    base=os.lstat(ARCHIVE_BASE)
    if (not stat.S_ISDIR(base.st_mode) or base.st_uid!=FORENSIC_UID or base.st_dev!=HOST_DEV or
        stat.S_IMODE(base.st_mode)!=0o700):
        fail('FORENSIC_DIR_RED')
    d=os.open(ARCHIVE_BASE,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        if (os.fstat(d).st_dev,os.fstat(d).st_ino)!=(base.st_dev,base.st_ino):
            fail('FORENSIC_DIR_REPLACED')
        try:
            fd=os.open(ARCHIVE.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,
                       0o600,dir_fd=d)
        except FileExistsError:
            fd=None
        if fd is not None:
            with os.fdopen(fd,'wb') as f:
                f.write(raw);f.flush();os.fsync(f.fileno())
        copy=os.lstat(ARCHIVE)
        if (not stat.S_ISREG(copy.st_mode) or copy.st_uid!=FORENSIC_UID or
            copy.st_dev!=base.st_dev or copy.st_nlink!=1 or copy.st_size!=11851 or
            stat.S_IMODE(copy.st_mode)!=0o600 or sha_file(ARCHIVE,11851)!=RECEIPT_HASH):
            fail('ARCHIVED_RECEIPT_HASH_RED')
        os.fsync(d)
    finally:
        os.close(d)
    if created:
        p=os.open(ARCHIVE_BASE.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try: os.fsync(p)
        finally: os.close(p)
    return True


def execute():
    now=datetime.now(timezone.utc)
    receipt={'schema':'RC6_A159_EXACT_STAGE_RECLAIM_V1',
             'at_utc':now.isoformat(),
             'at_art':now.astimezone(ZoneInfo('America/Argentina/Buenos_Aires')).isoformat(),
             'stage_deleted':False,'quarantined':False,'forensic_receipt_archived':False,
             'docker_mutated':False,'ppi_watch_mutated':False}
    fd=None
    try:
        if (metadata('id'),metadata('region'))!=('594077619','nyc1'):
            fail('DROPLET_IDENTITY_RED')
        receipt['droplet_id'],receipt['region']='594077619','nyc1'
        fd=acquire_lock()
        receipt['before']=free()
        active=container_snapshot()
        frozen=artifact_hashes()
        snapshot,raw=stage_snapshot(frozen)
        receipt['preflight']={'stage':snapshot,'fd_scan':open_refs(),
                              'barrier':barrier_safe(),'containers':len(active)}
        # A138 Actions log job 122501294970 reconstructs the full receipt,
        # independently verified byte-exact by A158 RO #40702529332.
        archive_receipt(raw)
        receipt['forensic_receipt_archived']=True
        if (container_snapshot()!=active or barrier_safe() not in ('ABSENT','IDLE') or
            stage_snapshot(frozen)[0]!=snapshot):
            fail('STAGE_OR_RUNTIME_DRIFT_BEFORE_DELETE')
        open_refs()
        if not shutil.rmtree.avoids_symlink_attacks:
            fail('RMTREE_FD_SAFETY_UNAVAILABLE')
        if QUARANTINE.exists() or QUARANTINE.is_symlink():
            fail('EXACT_QUARANTINE_PATH_EXISTS')
        os.rename(STAGE,QUARANTINE)
        receipt['quarantined']=True
        moved=os.lstat(QUARANTINE)
        if ((moved.st_dev,moved.st_ino,moved.st_uid)!=(HOST_DEV,STAGE_INODE,STAGE_UID) or
            not stat.S_ISDIR(moved.st_mode) or STAGE.exists() or STAGE.is_symlink()):
            fail('STAGE_QUARANTINE_IDENTITY_RED')
        if stage_snapshot(frozen,QUARANTINE)[0]!=snapshot or container_snapshot()!=active:
            fail('QUARANTINED_STAGE_OR_RUNTIME_DRIFT')
        shutil.rmtree(QUARANTINE)
        receipt['stage_deleted']=not (STAGE.exists() or STAGE.is_symlink() or
                                      QUARANTINE.exists() or QUARANTINE.is_symlink())
        if (not receipt['stage_deleted'] or container_snapshot()!=active or
            sha_file(ARCHIVE,11851)!=RECEIPT_HASH):
            fail('POST_STAGE_OR_RUNTIME_RED')
        receipt['status']='GREEN'
    except Exception as exc:
        receipt['status']='RED';receipt['error']=type(exc).__name__+':'+str(exc)
    finally:
        receipt['after']=free()
        if 'before' in receipt:
            receipt['free_delta_bytes']=receipt['after']['bytes']-receipt['before']['bytes']
        if fd is not None: os.close(fd)
        print('RC6_A159_EXACT_STAGE_RECLAIM='+json.dumps(receipt,sort_keys=True))
    return 0 if receipt['status']=='GREEN' else 2


if __name__=='__main__':
    sys.exit(execute())
