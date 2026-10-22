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
import sys
import tempfile
from datetime import datetime, timezone

ROOT=Path('/opt/porota-trading')
IDENT='89e7f81dbc9b4a628bdc30edf86a17b0'
MANIFEST_SHA='4d8e93f529e4c7385710dbcff58c52bea764762eafc0190907b63b1b214f1c6d'
CUT='b6687dbca06ab97bc9c93d3f7940c61bba577f68d4ff4b9f887332638c0b7cb8'
OLD_SHA='b346fcc8ae6edc51be20ac7325cd068cdeb60832'
PRIOR_ARCHIVE_SHA='d96cb68d177199317ee23239553517e04ee0a255'
PRIOR_CANCELLED_SHA='dc15f2af0bdcdf9c53dd26a645a6a131471ce879'
PRIOR_CANCELLED_RECEIPT_SHA='813ed6a0fb2e8b56fb9a9e1484f6976269bdf313082fc43a4c086f66bf841f80'
FILES={IDENT+'.pack','manifest-'+IDENT+'.json','receipt-'+IDENT+'.json','queue.json'}
CURRENT_FAILED_SUCCESSOR='3bfbfd6996a849f6b395f6c4a27ff15a'
CURRENT_FAILED_MANIFEST='9893c071a5c88cada7cda6ace0f534e1424c69623eaf0eda84f0af758280f8dc'
CURRENT_FAILED_REPORT_SHA='2416726d6ee67a1c52b1d87e5561003a3f717ddefb576aa80578c830b0db2718'


def proven_distinct_failed_successor(directory, generation, current_snapshot):
    """Keep the original archived cut revoked while a later exact FAILED cut waits.

    The successor is inspected read-only with its own manifest, receipt, pack,
    CURRENT and PAPER guards. Its separate fix-forward must still run later.
    """
    if current_snapshot != CURRENT_FAILED_SUCCESSOR:
        return False
    # Deploy V2 invokes this file by absolute path on the host. In that mode
    # Python places scripts/ rather than its parent on sys.path.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts import rc6_cold2_successor as successor
    try:
        evidence=successor.inspect(directory.parents[2])
    except (OSError,ValueError,KeyError,TypeError):
        return False
    return (evidence.get('snapshot_id')==CURRENT_FAILED_SUCCESSOR and
            evidence.get('manifest_sha256')==CURRENT_FAILED_MANIFEST and
            evidence.get('pack_bytes')==219680584 and
            evidence.get('failed_computed_report',{}).get('sha256')==CURRENT_FAILED_REPORT_SHA and
            evidence.get('cut')==generation and evidence.get('queue_failed') is True)


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


def proven_stop_cancellation(live, quiescence):
    """Recognize only the worker's COLD capture cancellation during SIGTERM.

    The worker records a fail-closed status immediately before its Docker clean
    exit. The immutable old cut still stays revoked; any other failure must
    keep blocking the archived recovery path.
    """
    if not isinstance(quiescence, dict):
        return False
    caucion=quiescence.get('caucion_carryover')
    intraday_caucion=(quiescence.get('intraday_caucion') is True and
        quiescence.get('market_open_suspended') is True and
        quiescence.get('paused_postclose') is False and
        quiescence.get('open_paper_carryover') is False and
        quiescence.get('spot_carryover') is None and
        isinstance(caucion,dict) and
        type(caucion.get('open_count')) is int and caucion['open_count']==1 and
        caucion.get('maturity_at')=='2026-10-21T17:00:00-03:00' and
        type(caucion.get('sha256')) is str and
        re.fullmatch('[0-9a-f]{64}',caucion['sha256']) is not None)
    pending_caucion=(quiescence.get('pending_caucion') is True and
        quiescence.get('paused_postclose') is False and
        quiescence.get('intraday_caucion') is False and
        quiescence.get('market_open_suspended') is False and
        quiescence.get('open_paper_carryover') is False and
        quiescence.get('spot_carryover') is None and
        isinstance(caucion,dict) and caucion.get('open_count')==1 and
        caucion.get('maturity_at')=='2026-10-23T17:00:00-03:00' and
        caucion.get('allocation_reconciled') is True and
        caucion.get('audit_source_sha256')==
            'b1d711899a5c85f4efc72e2632a9d81cdddf0a6dc71dee82d5c42923b5639865' and
        type(caucion.get('sha256')) is str and
        re.fullmatch('[0-9a-f]{64}',caucion['sha256']) is not None)
    postclose=(quiescence.get('paused_postclose') is True and
               quiescence.get('intraday_caucion') is not True)
    flat=(quiescence.get('paused_postclose') is False and
                 quiescence.get('intraday_caucion') is False and
                 type(quiescence.get('market_open_suspended')) is bool and
                 quiescence.get('open_paper_carryover') is False and
                 quiescence.get('spot_carryover') is None and
                 quiescence.get('caucion_carryover') is None)
    # The 2026-10-22 clean stop interrupted _frame() at line 265 while
    # flushing an 8 MiB segment. The older stage() cancellation at line 360
    # belongs to the separately proved postclose/caucion paths.
    expected_line=(265 if flat else 360 if (postclose or intraday_caucion or pending_caucion)
                   else None)
    if (expected_line is None or
            live.get('status')!='FAIL_CLOSED' or live.get('error_class')!='ValueError' or
            live.get('error_module')!='cold_audit' or
            type(live.get('error_line')) is not int or
            live['error_line']!=expected_line):
        return False
    proof=(quiescence.get('stop_evidence') or {}).get('porota_production_observer')
    if not isinstance(proof,dict) or proof.get('classification')!='CLEAN_EXIT':return False
    before,after=proof.get('before'),proof.get('after')
    if (not isinstance(before,dict) or not isinstance(after,dict) or
            before.get('Status')!='running' or after.get('Status')!='exited' or
            type(after.get('ExitCode')) is not int or after['ExitCode']!=0 or
            after.get('OOMKilled') is not False):return False
    try:
        stamp=datetime.fromisoformat(live['as_of'].replace('Z','+00:00'))
        started=datetime.fromisoformat(before['StartedAt'].replace('Z','+00:00'))
        finished=datetime.fromisoformat(after['FinishedAt'].replace('Z','+00:00'))
        return (all(value.tzinfo is not None for value in (stamp,started,finished)) and
                started<=stamp<=finished and 0<=(finished-stamp).total_seconds()<=15)
    except (KeyError,AttributeError,TypeError,ValueError):return False


def proven_prior_stop_cancellation(live, current, prior_path):
    """Adopt only the exact durable clean stop behind an unchanged live status.

    A restored observer can leave status.json at its last fail-closed write.
    A new quiescence has a different stop time; it must prove its own clean
    stop without pretending that the historical status came from that stop.
    """
    if prior_path is None or not isinstance(current,dict):return False
    try:
        if (prior_path.is_symlink() or regular(prior_path).st_size>65536 or
                current.get('candidate_sha')==PRIOR_CANCELLED_SHA or
                current.get('status') not in {'DRAINED','HOST_INSTALLATION_STARTED'} or
                current.get('real_orders_sent')!=0 or
                current.get('ppi_watch_accessed') is not False or
                not (current.get('paused_postclose') is False and
                     current.get('intraday_caucion') is False and
                     current.get('market_open_suspended') is False and
                     current.get('open_paper_carryover') is False and
                     current.get('spot_carryover') is None and
                     current.get('caucion_carryover') is None)):
            return False
        raw=prior_path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=PRIOR_CANCELLED_RECEIPT_SHA:
            return False
        prior=json.loads(raw)
        if (prior.get('candidate_sha')!=PRIOR_CANCELLED_SHA or
                prior.get('status')!='FIX_FORWARD_VALIDATION_REQUIRED' or
                prior.get('installation_started') is not True or
                prior.get('real_orders_sent')!=0 or
                prior.get('ppi_watch_accessed') is not False or
                live.get('real_orders_sent')!=0 or
                live.get('real_routes')!='NOT_CALLED' or
                not proven_stop_cancellation(live,prior)):
            return False
        stops=current.get('stop_evidence') or {}
        if set(stops)!={'porota_production_observer','porota_production_dashboard'}:
            return False
        prior_end=datetime.fromisoformat(prior['stop_evidence']['porota_production_observer']
             ['after']['FinishedAt'].replace('Z','+00:00'))
        for proof in stops.values():
            before,after=proof['before'],proof['after']
            if (proof.get('classification')!='CLEAN_EXIT' or
                    before.get('Status')!='running' or after.get('Status')!='exited' or
                    type(after.get('ExitCode')) is not int or after['ExitCode']!=0 or
                    after.get('OOMKilled') is not False):return False
            start=datetime.fromisoformat(before['StartedAt'].replace('Z','+00:00'))
            end=datetime.fromisoformat(after['FinishedAt'].replace('Z','+00:00'))
            if (any(t.tzinfo is None for t in (prior_end,start,end)) or
                    not prior_end<start<=end):return False
        return True
    except (OSError,KeyError,TypeError,ValueError):return False


def proven_inherited_flat_stop_cancellation(live, current, prior_path):
    """The next candidate can inherit the same exact clean stop, never a new one.

    Quiescence independently pins the stopped container IDs, image, PAPER flat
    book, critical approval and hash of its immediate predecessor's receipt.
    This predicate additionally binds the unchanged COLD cancellation to that
    predecessor's stop time and rejects any edited or unrelated receipt.
    """
    if prior_path is None or not isinstance(current,dict):return False
    try:
        if (prior_path.is_symlink() or regular(prior_path).st_size>65536 or
                current.get('fix_forward_from')!='83855c3b08647243dcd706ca7ead929378c4c11d' or
                current.get('candidate_sha')==current['fix_forward_from'] or
                current.get('status') not in {'DRAINED','HOST_INSTALLATION_STARTED'} or
                type(current.get('real_orders_sent')) is not int or current['real_orders_sent']!=0 or
                current.get('ppi_watch_accessed') is not False or
                current.get('paused_postclose') is not False or
                current.get('intraday_caucion') is not False or
                current.get('market_open_suspended') is not True or
                current.get('open_paper_carryover') is not False or
                current.get('spot_carryover') is not None or
                current.get('caucion_carryover') is not None):return False
        raw=prior_path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=current.get('prior_receipt_sha256'):
            return False
        prior=json.loads(raw)
        return (prior.get('candidate_sha')==current['fix_forward_from'] and
                prior.get('status')=='FIX_FORWARD_VALIDATION_REQUIRED' and
                prior.get('installation_started') is True and
                prior.get('inflight')=='ABSENT_OWNED_RUNTIME_STOPPED' and
                type(prior.get('real_orders_sent')) is int and prior['real_orders_sent']==0 and
                prior.get('ppi_watch_accessed') is False and
                all(prior.get(key)==current.get(key) for key in
                    ('paused_postclose','intraday_caucion','market_open_suspended',
                     'open_paper_carryover','spot_carryover','caucion_carryover',
                     'stop_evidence','containers')) and
                set(current.get('stop_evidence',{}))==
                    {'porota_production_observer','porota_production_dashboard'} and
                set(current.get('containers',{}))==
                    {'porota_production_observer','porota_production_dashboard'} and
                live.get('real_orders_sent')==0 and
                live.get('real_routes')=='NOT_CALLED' and
                proven_stop_cancellation(live,prior))
    except (OSError,KeyError,TypeError,ValueError):return False


def proven_inherited_pending_stop_cancellation(live, current, prior_path):
    """Bind a later candidate to the exact pending PAPER stop and prior receipt."""
    if prior_path is None or not isinstance(current,dict):return False
    try:
        previous_candidate=current.get('fix_forward_from')
        if (type(previous_candidate) is not str or
                re.fullmatch('[0-9a-f]{40}',previous_candidate) is None or
                prior_path.name!='quiescence-'+previous_candidate+'.json' or
                prior_path.is_symlink() or regular(prior_path).st_size>65536 or
                current.get('candidate_sha')==previous_candidate or
                current.get('status') not in {'DRAINED','HOST_INSTALLATION_STARTED'} or
                current.get('real_orders_sent')!=0 or
                current.get('ppi_watch_accessed') is not False or
                current.get('pending_caucion') is not True):return False
        raw=prior_path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=current.get('prior_receipt_sha256'):
            return False
        prior=json.loads(raw)
        return (prior.get('candidate_sha')==previous_candidate and
                prior.get('status')=='FIX_FORWARD_VALIDATION_REQUIRED' and
                prior.get('installation_started') is True and
                prior.get('inflight')=='ABSENT_OWNED_RUNTIME_STOPPED' and
                prior.get('real_orders_sent')==0 and
                prior.get('ppi_watch_accessed') is False and
                all(prior.get(key)==current.get(key) for key in
                    ('pending_caucion','paused_postclose','intraday_caucion',
                     'market_open_suspended','open_paper_carryover',
                     'spot_carryover','caucion_carryover','stop_evidence','containers')) and
                set(current.get('stop_evidence',{}))==
                    {'porota_production_observer','porota_production_dashboard'} and
                set(current.get('containers',{}))==
                    {'porota_production_observer','porota_production_dashboard'} and
                live.get('real_orders_sent')==0 and
                live.get('real_routes')=='NOT_CALLED' and
                proven_stop_cancellation(live,prior))
    except (OSError,KeyError,TypeError,ValueError):return False


def checked(directory,shadow, *, archived_recovery=False, quiescence=None,
            prior_quiescence=None, inherited_quiescence=None):
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
    generation=pointer.get('generation_id')
    name=pointer.get('file')
    if (type(generation) is not str or not re.fullmatch('[0-9a-f]{64}',generation) or
            name!='cut-'+generation+'.json.gz'):
        raise ValueError('COLD_CURRENT_POINTER_INVALID')
    current=enveloped_gzip(shadow/name)
    current_manifest=current.get('manifest',{})
    if (type(current_manifest) is not dict or
            digest(current_manifest)!=pointer.get('manifest_sha256') or
            current_manifest.get('generation_id')!=generation or
            current_manifest.get('sequence')!=pointer.get('sequence') or
            current.get('status',{}).get('real_orders_sent')!=0 or
            current.get('status',{}).get('real_routes')!='NOT_CALLED'):
        raise ValueError('COLD_CURRENT_CUT_INVALID')
    old_cut_path=shadow/('cut-'+CUT+'.json.gz')
    if old_cut_path.exists():
        old_cut=enveloped_gzip(old_cut_path)
        if old_cut.get('report',{}).get('computed_report',{}).get('snapshot_id')!=IDENT:
            raise ValueError('COLD_OLD_CUT_IDENTITY_MISMATCH')
    live=enveloped(shadow/'status.json')
    current_snapshot=current.get('report',{}).get('computed_report',{}).get('snapshot_id')
    if (not current_snapshot or (generation==CUT and not old_cut_path.exists()) or
            (generation!=CUT and current_snapshot==IDENT) or
            type(live.get('real_orders_sent')) is not int or live['real_orders_sent']!=0):
        raise ValueError('COLD_LIVE_PRECONDITION_CHANGED')
    if archived_recovery:
        # The earlier candidate already archived the failed input. A later
        # genuine cut can replace FAIL_CLOSED without rehabilitating that input.
        # During the exact clean stop, a live COLD capture publishes its own
        # fail-closed cancellation; this is bound to the stop receipt/time.
        if (generation==CUT or old_cut_path.exists() or current_snapshot==IDENT or
                (live.get('status')!='SHADOW_OBSERVING' and
                 not proven_stop_cancellation(live,quiescence) and
                 not proven_prior_stop_cancellation(live,quiescence,prior_quiescence) and
                 not proven_inherited_flat_stop_cancellation(live,quiescence,inherited_quiescence) and
                 not proven_inherited_pending_stop_cancellation(live,quiescence,
                     (ROOT/'data/deploy'/('quiescence-'+quiescence['fix_forward_from']+'.json'))
                     if isinstance(quiescence,dict) and
                        re.fullmatch('[0-9a-f]{40}',str(quiescence.get('fix_forward_from'))) else None) and
                 not (live.get('status')=='FAIL_CLOSED' and
                      proven_distinct_failed_successor(directory,generation,current_snapshot))) or
                live.get('real_routes')!='NOT_CALLED'):
            raise ValueError('COLD_ARCHIVED_RECOVERY_NOT_PROVEN')
    elif live.get('status')!='FAIL_CLOSED':
        raise ValueError('COLD_LIVE_PRECONDITION_CHANGED')
    return {'pack_sha256':h.hexdigest(),'pack_bytes':info.st_size,
            'manifest_sha256':MANIFEST_SHA,'snapshot_id':IDENT,
            'failed_at':receipt['failed_at'],'deadline_at':manifest['deadline_at'],
            'revoked_cut':CUT,'current_cut':generation,
            'old_cut_gc_complete':not old_cut_path.exists()}


def prior_archive_receipt(deploy,archive,evidence):
    path=deploy/('rc6-cold-fix-forward-'+PRIOR_ARCHIVE_SHA+'.json')
    if path.is_symlink() or not path.is_file() or path.stat().st_size>65536:
        raise ValueError('COLD_PRIOR_ARCHIVE_RECEIPT_MISSING')
    raw=path.read_bytes()
    prior=json.loads(raw)
    if (prior.get('schema')!='rc6.issue624.failed-cold-fix-forward.v1' or
            prior.get('state')!='ARCHIVED_OLD_CUT_STILL_REVOKED' or
            prior.get('candidate_sha')!=PRIOR_ARCHIVE_SHA or
            prior.get('archive')!=str(archive) or prior.get('snapshot_id')!=IDENT or
            prior.get('old_sha')!=OLD_SHA or
            prior.get('manifest_sha256')!=MANIFEST_SHA or
            prior.get('pack_sha256')!=evidence['pack_sha256'] or
            prior.get('pack_bytes')!=evidence['pack_bytes'] or
            prior.get('failed_at')!=evidence['failed_at'] or
            prior.get('deadline_at')!=evidence['deadline_at'] or
            prior.get('revoked_cut')!=CUT or prior.get('old_cut_gc_complete') is not True):
        raise ValueError('COLD_PRIOR_ARCHIVE_RECEIPT_CHANGED')
    return hashlib.sha256(raw).hexdigest()


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


def original_location(source,archive):
    """An archived ID may coexist with a live namespace for other IDs.

    The archive still goes through checked(), including pack inode and hash,
    before a receipt is recorded. Never touch unrelated active COLD records.
    """
    if not archive.exists():return source
    if source.exists():
        info=source.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode)&0o077:
            raise ValueError('COLD_DIRECTORY_UNSAFE')
        names={p.name for p in source.iterdir()}
        if names & (FILES-{'queue.json'}):
            raise ValueError('COLD_AMBIGUOUS_ARCHIVE')
        if 'queue.json' in names:
            regular(source/'queue.json')
            if enveloped(source/'queue.json',cold=True).get('snapshot_id')==IDENT:
                raise ValueError('COLD_AMBIGUOUS_ARCHIVE')
    return archive


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
    where=original_location(source,archive)
    if not where.exists():raise ValueError('COLD_ORIGINAL_MISSING')
    recovered_archive=where==archive and candidate!=PRIOR_ARCHIVE_SHA
    prior_quiescence=deploy/('quiescence-'+PRIOR_CANCELLED_SHA+'.json')
    inherited_quiescence=deploy/'quiescence-83855c3b08647243dcd706ca7ead929378c4c11d.json'
    evidence=checked(where,shadow,archived_recovery=recovered_archive,
                     quiescence=quiescence,prior_quiescence=prior_quiescence,
                     inherited_quiescence=inherited_quiescence)
    prior_receipt_sha=(prior_archive_receipt(deploy,archive,evidence)
                       if recovered_archive else None)
    if where==source:
        write(receipt_path,{'schema':'rc6.issue624.failed-cold-fix-forward.v1',
            'state':'VALIDATED_FOR_ARCHIVE','old_sha':OLD_SHA,'candidate_sha':candidate,**evidence})
        os.rename(source,archive)
        sync_directory(shadow);sync_directory(deploy)
    if source.exists() and source!=where:
        # A concurrent reintroduction of this ID must fail before certification.
        if original_location(source,archive)!=archive:
            raise ValueError('COLD_AMBIGUOUS_ARCHIVE')
    elif source.exists():
        raise ValueError('COLD_ARCHIVE_INCOMPLETE')
    checked(archive,shadow,archived_recovery=recovered_archive,
            quiescence=quiescence,prior_quiescence=prior_quiescence,
            inherited_quiescence=inherited_quiescence)
    write(receipt_path,{'schema':'rc6.issue624.failed-cold-fix-forward.v1',
        'state':'ARCHIVED_OLD_CUT_STILL_REVOKED','old_sha':OLD_SHA,
        'candidate_sha':candidate,'archive':str(archive),
        'recorded_at':datetime.now(timezone.utc).isoformat(),
        'adopted_prior_receipt_sha256':prior_receipt_sha,
        'archived_recovery':recovered_archive,**evidence})
    print(json.dumps({'status':'GREEN','candidate_sha':candidate,'archive':str(archive),
        'original_pack_sha256':evidence['pack_sha256'],'old_cut_revoked':True,
        'ppi_watch_accessed':False,'real_orders_sent':0},sort_keys=True))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--candidate-sha',required=True)
    run(parser.parse_args().candidate_sha)
