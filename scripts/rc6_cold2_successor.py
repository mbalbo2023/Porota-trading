"""Archive one active FAILED COLD successor under the existing deploy lock.

Historical COMPLETE generations are immutable witnesses, never quarantine
targets. No trading routes, source clock rewrite, or live cut publication.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from scripts import rc6_cold2_quarantine as old

IDENT = re.compile(r'[a-f0-9]{32}')
SHA = re.compile(r'[a-f0-9]{64}')
SOURCE_NAME = re.compile(r'(?:queue\.json|(?:manifest|receipt)-[a-f0-9]{32}\.json|[a-f0-9]{32}\.pack)')
MAX_NAMES = 128
MAX_PACK = 512 * 1024**2


def _names(path):
    names = {entry.name for entry in path.iterdir()}
    if len(names) > MAX_NAMES or any(SOURCE_NAME.fullmatch(name) is None for name in names):
        raise ValueError('COLD2_SUCCESSOR_NAMESPACE_UNSAFE')
    return names


def inspect(root, *, hash_pack=True):
    shadow, source, _ = old.paths(root)
    names = _names(source)
    pointer = old.envelope(shadow/'CURRENT.json')
    cut_name = pointer.get('file')
    if (not isinstance(cut_name, str) or
            re.fullmatch(r'cut-[a-f0-9]{64}\.json\.gz', cut_name) is None or
            pointer.get('generation_id') != cut_name[4:-8]):
        raise ValueError('COLD2_SUCCESSOR_CURRENT_UNSAFE')
    cut = old.envelope(shadow/cut_name, zipped=True)
    reference = cut.get('report', {}).get('computed_report', {})
    ident = reference.get('snapshot_id')
    if (not isinstance(ident, str) or IDENT.fullmatch(ident) is None or
            reference.get('schema') != 'rc6.computed-audit-reference.v2' or
            reference.get('state') != 'PENDING' or
            type(cut.get('status', {}).get('real_orders_sent')) is not int or
            cut.get('status', {}).get('real_orders_sent') != 0 or
            cut.get('status', {}).get('real_routes') != 'NOT_CALLED'):
        raise ValueError('COLD2_SUCCESSOR_CUT_UNSAFE')
    archive = root/'data/deploy'/('rc6-failed-cold-'+ident)
    if archive.exists():
        old.directory(archive)
    elif archive.is_symlink():
        raise ValueError('COLD2_SUCCESSOR_ARCHIVE_ALIAS')
    archived = {p.name for p in archive.iterdir()} if archive.exists() else set()
    manifest_name, receipt_name, pack_name = 'manifest-'+ident+'.json', 'receipt-'+ident+'.json', ident+'.pack'
    files = (manifest_name, receipt_name, 'queue.json')
    allowed_archive = set(files) | {pack_name, 'quarantine.json'} | {'.write-'+n+'.tmp' for n in files+('quarantine.json',)}
    if archived - allowed_archive:
        raise ValueError('COLD2_SUCCESSOR_ARCHIVE_UNEXPECTED')
    for name in files+(pack_name,):
        if name not in names and name not in archived:
            raise ValueError('COLD2_SUCCESSOR_MISSING_'+name.split('-')[0].upper())
        if name in names and name in archived:
            old.regular(source/name, bound=MAX_PACK if name==pack_name else 256*1024)
            old.regular(archive/name, bound=MAX_PACK if name==pack_name else 256*1024)
            if name==pack_name or old.sha(source/name)!=old.sha(archive/name):
                raise ValueError('COLD2_SUCCESSOR_DUPLICATE_MISMATCH')
    get = lambda name: source/name if name in names else archive/name
    manifest = old.envelope(get(manifest_name), cold=True)
    receipt = old.envelope(get(receipt_name), cold=True)
    queue = old.envelope(get('queue.json'), cold=True)
    manifest_sha = old.digest(manifest)
    pack_sha = manifest.get('pack_sha256')
    size = manifest.get('pack_bytes')
    original_identity = manifest.get('pack_identity')
    if (manifest.get('epoch') != 'rc6.audit-actual-values.v1' or
            manifest.get('snapshot_id') != ident or manifest.get('pack') != pack_name or
            type(size) is not int or not 0 < size <= MAX_PACK or
            not isinstance(pack_sha, str) or SHA.fullmatch(pack_sha) is None or
            type(original_identity) is not list or len(original_identity) != 5 or
            not all(type(x) is int and x >= 0 for x in original_identity) or
            type(manifest.get('sealed_at')) not in (float, int) or
            manifest.get('deadline_at') != manifest['sealed_at']+30.0 or
            reference.get('manifest_sha256') != manifest_sha or
            reference.get('deadline_at') != manifest['deadline_at'] or
            reference.get('source_as_of') != manifest.get('source_as_of') or
            reference.get('configuration_fingerprint') != manifest.get('configuration_fingerprint') or
            receipt.get('snapshot_id') != ident or receipt.get('state') != 'FAILED' or
            receipt.get('manifest_sha256') != manifest_sha or
            receipt.get('error_class') != 'ValueError' or
            (receipt.get('error_code'), receipt.get('error_phase')) not in {
                ('SHADOW_COLD_DEADLINE_EXPIRED_OR_CLOCK_REGRESSED', 'REPORT_WRITE'),
                ('SHADOW_COLD_HOST_MEMORY_RESERVE_BREACHED', 'PACK_HASH'),
            } or
            type(receipt.get('failed_at')) not in (float, int) or
            not manifest['sealed_at'] <= receipt['failed_at'] <= manifest['deadline_at']+5 or
            queue.get('snapshot_id') != ident or queue.get('state') != 'FAILED' or
            queue.get('manifest_sha256') != manifest_sha):
        raise ValueError('COLD2_SUCCESSOR_BINDING')
    pack = get(pack_name)
    st = old.regular(pack, bound=MAX_PACK)
    if (st.st_size != size or [st.st_dev, st.st_ino, st.st_size] != original_identity[:3] or
            pack_name in names and [st.st_dev, st.st_ino, st.st_size,
                                    st.st_mtime_ns, st.st_ctime_ns] != original_identity or
            hash_pack and old.sha(pack) != pack_sha):
        raise ValueError('COLD2_SUCCESSOR_PACK_CHANGED')
    # Other source rows must be complete, paired and hash-bound; any second
    # FAILED pack or receipt prevents archiving the selected successor.
    others = names - set(files) - {pack_name}
    ids = {n[9:-5] for n in others if n.startswith('manifest-')}
    if others != {n for other in ids for n in ('manifest-'+other+'.json','receipt-'+other+'.json')}:
        raise ValueError('COLD2_SUCCESSOR_OTHER_UNPAIRED')
    for other in ids:
        if other == ident:
            raise ValueError('COLD2_SUCCESSOR_ID_COLLISION')
        m = old.envelope(source/('manifest-'+other+'.json'), cold=True)
        r = old.envelope(source/('receipt-'+other+'.json'), cold=True)
        if (m.get('snapshot_id') != other or r.get('snapshot_id') != other or
                r.get('state') != 'COMPLETE' or r.get('manifest_sha256') != old.digest(m)):
            raise ValueError('COLD2_SUCCESSOR_OTHER_NOT_COMPLETE')
    status = old.envelope(shadow/'status.json')
    if (status.get('status') != 'FAIL_CLOSED' or status.get('real_routes') != 'NOT_CALLED' or
            type(status.get('real_orders_sent')) is not int or status['real_orders_sent'] != 0):
        raise ValueError('COLD2_SUCCESSOR_STATUS_UNSAFE')
    if 'quarantine.json' in archived:
        record = old.envelope(archive/'quarantine.json')
        if (record.get('state') != 'QUARANTINED_FAILED' or
                record.get('snapshot_id') != ident or record.get('pack_sha256') != pack_sha or
                record.get('manifest_sha256') != manifest_sha or
                record.get('revoked_cut') != pointer['generation_id']):
            raise ValueError('COLD2_SUCCESSOR_RECEIPT_CHANGED')
    return {'snapshot_id':ident,'manifest_sha256':manifest_sha,'pack_sha256':pack_sha,
            'pack_bytes':size,'cut':pointer['generation_id'],
            'source_names':sorted(names),'archive_names':sorted(archived),
            'archive_complete':all(n in archived for n in files+(pack_name,'quarantine.json')),
            'queue_failed': 'queue.json' in names}


def quarantine(root, *, admission, expected_owned_ids, checkpoint=lambda step: None):
    old._admit(root, admission, expected_owned_ids)
    before = inspect(root)
    ident = before['snapshot_id']
    archive = root/'data/deploy'/('rc6-failed-cold-'+ident)
    source = root/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow/cold-audit'
    if before['archive_complete'] and not before['queue_failed']:
        return {'state':'ALREADY_QUARANTINED','snapshot_id':ident,'old_cut_revoked':True}
    if not archive.exists():
        old._admit(root, admission, expected_owned_ids)
        archive.mkdir(mode=0o700)
        old.fsync_dir(archive.parent)
    names = ('manifest-'+ident+'.json','receipt-'+ident+'.json','queue.json')
    for name in names:
        old._admit(root, admission, expected_owned_ids)
        if (source/name).exists():
            old._atomic_copy(source/name, archive/name)
        else:
            old.regular(archive/name)
        checkpoint('archive_'+name)
    pack = ident+'.pack'
    if (source/pack).exists():
        old._admit(root, admission, expected_owned_ids)
        if (archive/pack).exists():
            raise ValueError('COLD2_SUCCESSOR_PACK_DUPLICATED')
        os.rename(source/pack, archive/pack)
        old.fsync_dir(source)
        old.fsync_dir(archive)
    inspect(root)
    checkpoint('pack_moved')
    for name in names[:2]:
        old._admit(root, admission, expected_owned_ids)
        if (source/name).exists():
            old.regular(source/name)
            (source/name).unlink()
            old.fsync_dir(source)
        inspect(root, hash_pack=False)
        checkpoint('source_'+name+'_removed')
    old._admit(root, admission, expected_owned_ids)
    record={'schema':'rc6.issue624.cold2.successor.v1','state':'QUARANTINED_FAILED',
            'snapshot_id':ident,'manifest_sha256':before['manifest_sha256'],
            'pack_sha256':before['pack_sha256'],'pack_bytes':before['pack_bytes'],
            'revoked_cut':before['cut'],'old_cut_revoked':True,'real_orders_sent':0}
    # The same atomic receipt helper as original COLD2, parameterized here
    # for a successor ID; a crash before its fsync keeps queue FAILED.
    target, temp = archive/'quarantine.json', archive/'.write-quarantine.json.tmp'
    if target.exists():
        if old.envelope(target) != record:
            raise ValueError('COLD2_SUCCESSOR_RECEIPT_CHANGED')
    else:
        if temp.exists():
            old.regular(temp)
            temp.unlink()
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            with os.fdopen(fd,'w') as stream:
                json.dump({'payload':record,'digest':old.digest(record)},stream,sort_keys=True,separators=(',',':'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp,target)
            old.fsync_dir(archive)
        finally:
            if temp.exists():
                temp.unlink()
    inspect(root, hash_pack=False)
    checkpoint('receipt_durable')
    old._admit(root, admission, expected_owned_ids)
    if (source/'queue.json').exists():
        old.regular(source/'queue.json')
        (source/'queue.json').unlink()
        old.fsync_dir(source)
    final=inspect(root)
    if not final['archive_complete'] or final['queue_failed']:
        raise ValueError('COLD2_SUCCESSOR_ARCHIVE_INCOMPLETE')
    checkpoint('queue_removed')
    return {'state':'QUARANTINED_FAILED','snapshot_id':ident,'old_cut_revoked':True,
            'pack_sha256':before['pack_sha256'],'manifest_sha256':before['manifest_sha256']}
