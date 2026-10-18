#!/usr/bin/env python3
"""Archive one exact failed COLD backlog while the owned runtime is stopped.

This is a one-time, fail-closed migration for #624. It cannot make the old cut
valid: its manifest disappears from the active namespace, while its exact
original pack and failure receipt remain durable in the deploy archive.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from datetime import datetime, timezone

ROOT=Path('/opt/porota-trading')
IDENT='89e7f81dbc9b4a628bdc30edf86a17b0'
MANIFEST_SHA='4d8e93f529e4c7385710dbcff58c52bea764762eafc0190907b63b1b214f1c6d'
CUT='b6687dbca06ab97bc9c93d3f7940c61bba577f68d4ff4b9f887332638c0b7cb8'
OLD_SHA='b346fcc8ae6edc51be20ac7325cd068cdeb60832'
FILES={IDENT+'.pack','manifest-'+IDENT+'.json','receipt-'+IDENT+'.json','queue.json'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
        default=str,ensure_ascii=True,allow_nan=False).encode('ascii')).hexdigest()


def enveloped(path, *, cold=False):
    raw=path.read_bytes()
    if len(raw)>256*1024:raise ValueError('COLD_ENVELOPE_BOUND')
    value=json.loads(raw)
    key='sha256' if cold else 'digest'
    if (type(value) is not dict or set(value)!={'payload',key} or
            type(value['payload']) is not dict or digest(value['payload'])!=value[key]):
        raise ValueError('COLD_ENVELOPE_HASH')
    return value['payload']


def regular(path):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)&0o077:
        raise ValueError('COLD_ALIAS_OR_PERMISSIONS')
    return info


def checked(directory,shadow):
    info=directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode)&0o077:
        raise ValueError('COLD_DIRECTORY_UNSAFE')
    if {p.name for p in directory.iterdir()}!=FILES:
        raise ValueError('COLD_UNEXPECTED_NAMESPACE')
    rows={name:regular(directory/name) for name in FILES}
    manifest=enveloped(directory/('manifest-'+IDENT+'.json'),cold=True)
    receipt=enveloped(directory/('receipt-'+IDENT+'.json'),cold=True)
    queue=enveloped(directory/'queue.json',cold=True)
    if (manifest.get('snapshot_id')!=IDENT or manifest.get('pack')!=IDENT+'.pack' or
            digest(manifest)!=MANIFEST_SHA or manifest.get('pack_bytes')!=rows[IDENT+'.pack'].st_size or
            receipt.get('snapshot_id')!=IDENT or receipt.get('state')!='FAILED' or
            receipt.get('manifest_sha256')!=MANIFEST_SHA or receipt.get('error_class')!='ValueError' or
            queue.get('snapshot_id')!=IDENT or queue.get('manifest_sha256')!=MANIFEST_SHA or
            queue.get('state')!='FAILED' or
            not manifest['deadline_at']<=receipt['failed_at']<=manifest['deadline_at']+.01):
        raise ValueError('COLD_FAILURE_IDENTITY_MISMATCH')
    pack=directory/(IDENT+'.pack')
    info=rows[IDENT+'.pack']
    if ([info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns]
            !=manifest.get('pack_identity')):
        raise ValueError('COLD_PACK_IDENTITY_CHANGED')
    with pack.open('rb') as stream:
        h=hashlib.sha256()
        while chunk:=stream.read(1024*1024):h.update(chunk)
    if h.hexdigest()!=manifest['pack_sha256']:
        raise ValueError('COLD_PACK_HASH_CHANGED')
    pointer=enveloped(shadow/'CURRENT.json')
    cut=enveloped_gzip(shadow/('cut-'+CUT+'.json.gz'))
    live=enveloped(shadow/'status.json')
    if (pointer.get('generation_id')!=CUT or pointer.get('file')!='cut-'+CUT+'.json.gz' or
            cut.get('report',{}).get('computed_report',{}).get('snapshot_id')!=IDENT or
            live.get('status')!='FAIL_CLOSED' or type(live.get('real_orders_sent')) is not int or
            live['real_orders_sent']!=0):
        raise ValueError('COLD_LIVE_PRECONDITION_CHANGED')
    return {'pack_sha256':h.hexdigest(),'pack_bytes':info.st_size,
            'manifest_sha256':MANIFEST_SHA,'snapshot_id':IDENT,
            'failed_at':receipt['failed_at'],'deadline_at':manifest['deadline_at'],
            'revoked_cut':CUT}


def enveloped_gzip(path):
    import gzip
    regular(path)
    with gzip.open(path,'rb') as stream:raw=stream.read(4*1024*1024+1)
    if len(raw)>4*1024*1024:raise ValueError('COLD_CUT_BOUND')
    value=json.loads(raw)
    if (type(value) is not dict or set(value)!={'payload','digest'} or
            digest(value['payload'])!=value['digest']):raise ValueError('COLD_CUT_HASH')
    return value['payload']


def write(path,value):
    fd,name=tempfile.mkstemp(prefix='.rc6-cold-fix-forward-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,sort_keys=True,indent=2);stream.write('\n')
            stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(name):os.unlink(name)


def sync_directory(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def run(candidate):
    if not re.fullmatch('[0-9a-f]{40}',candidate) or candidate==OLD_SHA:
        raise ValueError('COLD_NEW_CANDIDATE_REQUIRED')
    shadow=ROOT/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'
    source=shadow/'cold-audit'
    deploy=ROOT/'data/deploy'
    archive=deploy/('rc6-failed-cold-'+IDENT)
    receipt_path=deploy/('rc6-cold-fix-forward-'+candidate+'.json')
    if source.is_symlink() or archive.is_symlink() or deploy.is_symlink() or shadow.is_symlink():
        raise ValueError('COLD_PATH_ALIAS')
    if not deploy.is_dir() or os.stat(shadow).st_dev!=os.stat(deploy).st_dev:
        raise ValueError('COLD_ARCHIVE_SAME_DEVICE_REQUIRED')
    # The canonical deploy quiescence has stopped BOTH owned containers.
    # Recheck the exact durable receipt rather than assuming a stop succeeded.
    quiescence=json.loads((deploy/('quiescence-'+candidate+'.json')).read_text())
    if (quiescence.get('status') not in {'DRAINED','HOST_INSTALLATION_STARTED'} or
            quiescence.get('candidate_sha')!=candidate or
            quiescence.get('real_orders_sent')!=0 or
            set(quiescence.get('stop_evidence',{}))!={'porota_production_observer','porota_production_dashboard'}):
        raise ValueError('COLD_OWNED_RUNTIME_NOT_QUIESCENT')
    for name, original in quiescence['containers'].items():
        if name not in {'porota_production_observer','porota_production_dashboard'}:
            raise ValueError('COLD_UNEXPECTED_CONTAINER')
        raw=subprocess.check_output(['docker','inspect','--format',
            '{{.Id}}|{{.State.Status}}|{{.Image}}',name],text=True,timeout=10).strip()
        if raw!=original['id']+'|exited|'+original['image']:
            raise ValueError('COLD_CONTAINER_NOT_STOPPED_OR_CHANGED')
    if source.exists() and archive.exists():raise ValueError('COLD_AMBIGUOUS_ARCHIVE')
    where=source if source.exists() else archive
    if not where.exists():raise ValueError('COLD_ORIGINAL_MISSING')
    evidence=checked(where,shadow)
    if where==source:
        write(receipt_path,{'schema':'rc6.issue624.failed-cold-fix-forward.v1',
            'state':'VALIDATED_FOR_ARCHIVE','old_sha':OLD_SHA,'candidate_sha':candidate,**evidence})
        os.rename(source,archive)
        sync_directory(shadow);sync_directory(deploy)
    assert not source.exists()
    checked(archive,shadow)
    write(receipt_path,{'schema':'rc6.issue624.failed-cold-fix-forward.v1',
        'state':'ARCHIVED_OLD_CUT_STILL_REVOKED','old_sha':OLD_SHA,
        'candidate_sha':candidate,'archive':str(archive),
        'recorded_at':datetime.now(timezone.utc).isoformat(),**evidence})
    print(json.dumps({'status':'GREEN','candidate_sha':candidate,'archive':str(archive),
        'original_pack_sha256':evidence['pack_sha256'],'old_cut_revoked':True,
        'ppi_watch_accessed':False,'real_orders_sent':0},sort_keys=True))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--candidate-sha',required=True)
    run(parser.parse_args().candidate_sha)
