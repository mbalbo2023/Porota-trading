#!/usr/bin/env python3
"""Unwired, fail-closed plan and testable archive for #624 COLD2.

The CLI is read-only. A future separately authorized canonical workflow must
provide a quiescence admission callback and call quarantine() explicitly.
No FAILED receipt is converted to COMPLETE, and no live cut is republished.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat

IDENT = '7dc99ccd8f7c47849ca3705ae1cb2c49'
MANIFEST_SHA = '7657b124c222993f9b34471626d0c740508ee783e7fcd8ce07c85ad9495f23f3'
PACK_SHA = 'd19b7e9062327a4f9d013b57cff9f2d1824f9dba386d9756bc0e2a0b3240cf0c'
PACK_BYTES = 211783290
CUT = 'af86116a3a64a9214cfde7ccfc1e04d3165270cf6d6f6ae2b4f4ce6e13c89ad9'
PACK = IDENT + '.pack'
MANIFEST = 'manifest-' + IDENT + '.json'
RECEIPT = 'receipt-' + IDENT + '.json'
QUEUE = 'queue.json'
FILES = (MANIFEST, RECEIPT, QUEUE)
ORIGINAL_SHA = {
    MANIFEST:'a43b06a26726d7c72b88173fc153a6ba0e53ba1902111371a8eefdd5b37df0b6',
    RECEIPT:'97be9d105cd0e5ea019b45524cb823628a8a105bf4dba01d3438f46a734d2aff',
    QUEUE:'00613e3a5409ef15ea884d3ecb209d6987237dd9f07072b9c433800a1eea6278',
}
OTHER = {f'{kind}-{ident}.json' for ident in
         ('87f18ab0f25749c0bf3f0b3ad73a164b', 'b024ef43497d4108af829947f8855701')
         for kind in ('manifest', 'receipt')}
ROOT = Path('/opt/porota-trading')
RESERVE = 2 * 1024**3


def digest(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'),
                     default=str, ensure_ascii=True, allow_nan=False).encode('ascii')
    return hashlib.sha256(raw).hexdigest()


def directory(path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077
            or path.is_symlink()):
        raise ValueError('COLD2_DIRECTORY_UNSAFE')
    return info


def regular(path, *, bound=None):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            stat.S_IMODE(info.st_mode) & 0o077 or path.is_symlink() or
            bound is not None and info.st_size > bound):
        raise ValueError('COLD2_FILE_UNSAFE')
    return info


def envelope(path, *, cold=False, zipped=False):
    regular(path, bound=4*1024**2 if zipped else 256*1024)
    if zipped:
        with gzip.open(path, 'rb') as stream:raw = stream.read(4*1024**2 + 1)
        if len(raw) > 4*1024**2:raise ValueError('COLD2_CUT_BOUND')
    else:raw = path.read_bytes()
    outer = json.loads(raw)
    key = 'sha256' if cold else 'digest'
    if (type(outer) is not dict or set(outer) != {'payload', key} or
            type(outer['payload']) is not dict or digest(outer['payload']) != outer[key]):
        raise ValueError('COLD2_ENVELOPE_HASH')
    return outer['payload']


def sha(path):
    h = hashlib.sha256()
    with path.open('rb', buffering=0) as stream:
        while chunk := stream.read(1024*1024):h.update(chunk)
    return h.hexdigest()


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def paths(root):
    shadow = root/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'
    source = shadow/'cold-audit'
    archive = root/'data/deploy'/('rc6-failed-cold-'+IDENT)
    for path in (root, root/'data', root/'data/deploy'):
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
            raise ValueError('COLD2_PATH_ALIAS')
    for path in (shadow,source):directory(path)
    if archive.exists():directory(archive)
    elif archive.is_symlink():raise ValueError('COLD2_ARCHIVE_ALIAS')
    if os.stat(source).st_dev != os.stat(archive.parent).st_dev:
        raise ValueError('COLD2_SAME_DEVICE_REQUIRED')
    return shadow, source, archive


def inspect(root):
    """Read-only preflight; works during each interrupted archive phase."""
    shadow, source, archive = paths(root)
    names = {p.name for p in source.iterdir()}
    if names - (OTHER | set(FILES) | {PACK}):
        raise ValueError('COLD2_UNEXPECTED_SOURCE')
    for name in OTHER:
        envelope(source/name, cold=True)
    archived = {p.name for p in archive.iterdir()} if archive.exists() else set()
    allowed = set(FILES) | {PACK, 'quarantine.json'} | {'.write-'+n+'.tmp' for n in FILES+('quarantine.json',)}
    if archived - allowed:raise ValueError('COLD2_UNEXPECTED_ARCHIVE')
    for name in FILES:
        if name not in names and name not in archived:
            if name == QUEUE and 'quarantine.json' in archived:continue
            raise ValueError('COLD2_MISSING_EVIDENCE')
        if name in names and name in archived and (regular(source/name) and
                regular(archive/name) and sha(source/name) != sha(archive/name)):
            raise ValueError('COLD2_ARCHIVE_COPY_MISMATCH')
    get = lambda name: source/name if name in names else archive/name
    for name in FILES:
        if name in names or name in archived:
            if sha(get(name)) != ORIGINAL_SHA[name]:
                raise ValueError('COLD2_ORIGINAL_BYTES_CHANGED')
    manifest = envelope(get(MANIFEST), cold=True)
    receipt = envelope(get(RECEIPT), cold=True)
    queue = envelope(get(QUEUE), cold=True) if QUEUE in names or QUEUE in archived else None
    if (manifest.get('snapshot_id') != IDENT or manifest.get('pack') != PACK or
            digest(manifest) != MANIFEST_SHA or manifest.get('pack_sha256') != PACK_SHA or
            manifest.get('pack_bytes') != PACK_BYTES or
            receipt.get('state') != 'FAILED' or receipt.get('snapshot_id') != IDENT or
            receipt.get('manifest_sha256') != MANIFEST_SHA or receipt.get('error_class') != 'ValueError' or
            not manifest['sealed_at'] <= receipt['failed_at'] < manifest['deadline_at'] or
            queue is not None and (queue.get('snapshot_id') != IDENT or
                queue.get('state') != 'FAILED' or queue.get('manifest_sha256') != MANIFEST_SHA)):
        raise ValueError('COLD2_FAILED_BINDING')
    if PACK not in names and PACK not in archived:raise ValueError('COLD2_PACK_MISSING')
    if PACK in names and PACK in archived:raise ValueError('COLD2_PACK_DUPLICATED')
    pack = source/PACK if PACK in names else archive/PACK
    st = regular(pack)
    if st.st_size != PACK_BYTES or [st.st_dev,st.st_ino,st.st_size] != manifest['pack_identity'][:3] or sha(pack) != PACK_SHA:
        raise ValueError('COLD2_PACK_CHANGED')
    if PACK in names and [st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns] != manifest['pack_identity']:
        raise ValueError('COLD2_PACK_IDENTITY_CHANGED')
    pointer = envelope(shadow/'CURRENT.json')
    if pointer.get('generation_id') != CUT or pointer.get('file') != 'cut-'+CUT+'.json.gz':
        raise ValueError('COLD2_CURRENT_CHANGED')
    cut = envelope(shadow/pointer['file'], zipped=True)
    if (cut.get('report',{}).get('computed_report',{}).get('snapshot_id') != IDENT or
            cut['report']['computed_report'].get('state') != 'PENDING' or
            cut.get('status',{}).get('real_orders_sent') != 0 or
            cut.get('status',{}).get('real_routes') != 'NOT_CALLED'):
        raise ValueError('COLD2_CUT_NOT_REVOKED')
    status = envelope(shadow/'status.json')
    if status.get('status') != 'FAIL_CLOSED' or type(status.get('real_orders_sent')) is not int or status['real_orders_sent'] != 0:
        raise ValueError('COLD2_STATUS_CHANGED')
    if 'quarantine.json' in archived:
        record = envelope(archive/'quarantine.json')
        if record.get('state') != 'QUARANTINED_FAILED' or record.get('pack_sha256') != PACK_SHA:
            raise ValueError('COLD2_QUARANTINE_RECEIPT_INVALID')
    return {'snapshot_id':IDENT,'manifest_sha256':MANIFEST_SHA,'pack_sha256':PACK_SHA,
            'source_names':sorted(names),'archive_names':sorted(archived),
            'current_cut':CUT,'old_cut_revoked':True,'queue_failed':QUEUE in names,
            'archive_complete':all(name in archived for name in FILES+(PACK,'quarantine.json'))}


def verify_original_archive(root):
    """Attest the historical FAILED cut after later COLD generations exist.

    The original archive is immutable, while CURRENT and the source namespace
    legitimately advance after observer restart. Never use this check to
    dismiss a *new* failed queue; the successor guard handles that separately.
    """
    shadow, source, archive = paths(root)
    if not archive.exists() or {p.name for p in archive.iterdir()} != set(FILES) | {PACK, 'quarantine.json'}:
        raise ValueError('COLD2_ORIGINAL_ARCHIVE_INCOMPLETE')
    for name in FILES:
        regular(archive/name)
        if sha(archive/name) != ORIGINAL_SHA[name]:
            raise ValueError('COLD2_ORIGINAL_BYTES_CHANGED')
    manifest = envelope(archive/MANIFEST, cold=True)
    receipt = envelope(archive/RECEIPT, cold=True)
    queue = envelope(archive/QUEUE, cold=True)
    archived_receipt = envelope(archive/'quarantine.json')
    if (manifest.get('snapshot_id') != IDENT or digest(manifest) != MANIFEST_SHA or
            manifest.get('pack_sha256') != PACK_SHA or
            receipt.get('state') != 'FAILED' or receipt.get('manifest_sha256') != MANIFEST_SHA or
            queue.get('snapshot_id') != IDENT or queue.get('state') != 'FAILED' or
            archived_receipt.get('state') != 'QUARANTINED_FAILED' or
            archived_receipt.get('snapshot_id') != IDENT or
            archived_receipt.get('manifest_sha256') != MANIFEST_SHA or
            archived_receipt.get('pack_sha256') != PACK_SHA or
            archived_receipt.get('revoked_cut') != CUT or
            archived_receipt.get('old_cut_revoked') is not True or
            type(archived_receipt.get('real_orders_sent')) is not int or
            archived_receipt['real_orders_sent'] != 0 or
            any((source/name).exists() or (source/name).is_symlink()
                for name in (MANIFEST, RECEIPT, PACK))):
        raise ValueError('COLD2_ORIGINAL_ARCHIVE_CHANGED')
    pack_info = regular(archive/PACK)
    if (pack_info.st_size != PACK_BYTES or
            [pack_info.st_dev, pack_info.st_ino, pack_info.st_size] != manifest['pack_identity'][:3] or
            sha(archive/PACK) != PACK_SHA):
        raise ValueError('COLD2_ORIGINAL_PACK_CHANGED')
    old_cut = shadow/('cut-'+CUT+'.json.gz')
    if old_cut.exists() or old_cut.is_symlink():
        cut = envelope(old_cut, zipped=True)
        if (cut.get('report',{}).get('computed_report',{}).get('snapshot_id') != IDENT or
                cut['report']['computed_report'].get('state') != 'PENDING' or
                type(cut.get('status',{}).get('real_orders_sent')) is not int or
                cut.get('status',{}).get('real_orders_sent') != 0 or
                cut.get('status',{}).get('real_routes') != 'NOT_CALLED'):
            raise ValueError('COLD2_ORIGINAL_CUT_CHANGED')
    else:
        # The normal publisher retains only two immutable cuts. After three
        # later generations, the historical failed cut is legitimately GC'd;
        # the archived manifest/receipt/queue/pack remain its durable proof.
        pointer = envelope(shadow/'CURRENT.json')
        name = pointer.get('file')
        if (pointer.get('generation_id') == CUT or not isinstance(name, str) or
                re.fullmatch(r'cut-[a-f0-9]{64}\.json\.gz', name) is None or
                pointer.get('generation_id') != name[4:-8]):
            raise ValueError('COLD2_ORIGINAL_CUT_MISSING_WHILE_CURRENT')
        latest = envelope(shadow/name, zipped=True)
        if (type(latest.get('status',{}).get('real_orders_sent')) is not int or
                latest.get('status',{}).get('real_orders_sent') != 0 or
                latest.get('status',{}).get('real_routes') != 'NOT_CALLED'):
            raise ValueError('COLD2_ORIGINAL_SUCCESSOR_UNSAFE')
    return {'state':'ALREADY_QUARANTINED','old_cut_revoked':True,
            'pack_sha256':PACK_SHA,'manifest_sha256':MANIFEST_SHA}


def _atomic_copy(source, target):
    if target.exists():
        regular(target)
        if sha(source) != sha(target):raise ValueError('COLD2_ARCHIVE_COPY_MISMATCH')
        return
    temp = target.parent/('.write-'+target.name+'.tmp')
    if temp.exists():
        regular(temp)
        temp.unlink()  # only uncommitted staging, never an original
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd,'wb') as dst, source.open('rb') as src:
            shutil.copyfileobj(src,dst,64*1024)
            dst.flush();os.fsync(dst.fileno())
        if sha(temp) != sha(source):raise ValueError('COLD2_COPY_HASH')
        os.replace(temp,target);fsync_dir(target.parent)
    finally:
        if temp.exists():temp.unlink()


def _receipt(archive):
    record={'schema':'rc6.issue624.cold2.quarantine.v1','state':'QUARANTINED_FAILED',
            'snapshot_id':IDENT,'manifest_sha256':MANIFEST_SHA,'pack_sha256':PACK_SHA,
            'pack_bytes':PACK_BYTES,'revoked_cut':CUT,'old_cut_revoked':True,
            'original_receipt_state':'FAILED','real_orders_sent':0}
    target=archive/'quarantine.json'
    temp=archive/'.write-quarantine.json.tmp'
    if target.exists():
        if envelope(target)!=record:raise ValueError('COLD2_QUARANTINE_RECEIPT_CHANGED')
        return
    if temp.exists():regular(temp);temp.unlink()
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump({'payload':record,'digest':digest(record)},stream,sort_keys=True,separators=(',',':'))
            stream.flush();os.fsync(stream.fileno())
        os.replace(temp,target);fsync_dir(archive)
    finally:
        if temp.exists():temp.unlink()


OWNED = ('porota_production_observer', 'porota_production_dashboard')


def _admit(root, admission, expected_owned_ids):
    """Revalidate the live admission and reserve at every destructive boundary."""
    state=admission()
    ids=state.get('owned_ids') if type(state) is dict else None
    if (type(expected_owned_ids) is not dict or set(expected_owned_ids) != set(OWNED)
            or any(type(value) is not str or re.fullmatch('[a-f0-9]{64}',value) is None
                   for value in expected_owned_ids.values())):
        raise ValueError('COLD2_EXPECTED_OWNED_IDS_REQUIRED')
    if (type(state) is not dict or state.get('status') != 'DRAINED' or
            state.get('owned_stopped') is not True or state.get('market_closed') is not True or
            state.get('no_positions') is not True or state.get('lock_held') is not True or
            state.get('mode') != 'PRODUCTION_PAPER' or
            state.get('real_routes') != 'NOT_CALLED' or
            state.get('order_routing_blocked') is not True or
            state.get('inflight_absent') is not True or
            state.get('critical_healthy') is not True or
            type(ids) is not dict or ids != expected_owned_ids or
            state.get('ppi_watch_accessed') is not False or
            type(state.get('real_orders_sent')) is not int or state['real_orders_sent'] != 0):
        raise ValueError('COLD2_QUIESCENCE_REQUIRED')
    if shutil.disk_usage(root).free < RESERVE+1024**2:
        raise ValueError('COLD2_DISK_RESERVE_BREACHED')
    volume=os.statvfs(root)
    if volume.f_favail < 16:
        raise ValueError('COLD2_INODE_RESERVE_BREACHED')
    available=None
    with open('/proc/meminfo',encoding='ascii') as stream:
        for line in stream:
            if line.startswith('MemAvailable:'):
                available=int(line.split()[1])*1024
                break
    if available is None or available <= 128*1024**2:
        raise ValueError('COLD2_MEMORY_RESERVE_BREACHED')


def quarantine(root, *, admission, expected_owned_ids, checkpoint=lambda step:None):
    """Only a future authorized workflow may call this under its host lock.

    admission() must verify exact quiescence, stopped owned IDs, CLOSED/no open
    positions, PAPER/0, PPI preservation, and the caller's exclusive lock.
    """
    _admit(root, admission, expected_owned_ids)
    before=inspect(root)
    shadow,source,archive=paths(root)
    if before['archive_complete'] and QUEUE not in before['source_names']:
        return {'state':'ALREADY_QUARANTINED','old_cut_revoked':True}
    if not archive.exists():
        _admit(root, admission, expected_owned_ids)
        archive.mkdir(mode=0o700);fsync_dir(archive.parent)
    for name in FILES:
        _admit(root, admission, expected_owned_ids)
        src=source/name
        if src.exists():_atomic_copy(src,archive/name)
        else:regular(archive/name)
        checkpoint('archive_'+name)
    src, dst=source/PACK, archive/PACK
    if src.exists():
        _admit(root, admission, expected_owned_ids)
        if dst.exists():raise ValueError('COLD2_PACK_DUPLICATED')
        os.rename(src,dst);fsync_dir(source);fsync_dir(archive)
    inspect(root);checkpoint('pack_moved')
    for name in (MANIFEST,RECEIPT):
        src=source/name
        _admit(root, admission, expected_owned_ids)
        if src.exists():regular(src);src.unlink();fsync_dir(source)
        inspect(root);checkpoint('source_'+name+'_removed')
    _admit(root, admission, expected_owned_ids)
    _receipt(archive);inspect(root);checkpoint('receipt_durable')
    q=source/QUEUE
    _admit(root, admission, expected_owned_ids)
    if q.exists():regular(q);q.unlink();fsync_dir(source)
    final=inspect(root)
    if not final['archive_complete'] or final['queue_failed']:
        raise ValueError('COLD2_ARCHIVE_INCOMPLETE')
    checkpoint('queue_removed')
    return {'state':'QUARANTINED_FAILED','old_cut_revoked':True,
            'pack_sha256':PACK_SHA,'manifest_sha256':MANIFEST_SHA}


def main():
    parser=argparse.ArgumentParser(description='Read-only plan; apply is intentionally unavailable')
    parser.add_argument('--plan',action='store_true',required=True)
    args=parser.parse_args()
    print(json.dumps({'mode':'READ_ONLY','apply_available':False,**inspect(ROOT)},sort_keys=True))


if __name__=='__main__':main()
