#!/usr/bin/env python3
"""Bounded, read-only Docker and durable receipt inventory after failed A175.

This is evidence for a separate exact cleanup review. It never estimates
reclaimable bytes from image Size or a count of layer diffIDs.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import urllib.request


EXPECTED_DROPLET = ('594077619', 'nyc1')
A175_CANDIDATE = '18ba517a846c6798f0d12f39072465fc02fa03b5'
A175_TREE = '66c67bff66dd9f036cd7aa8de5ba312e04e53be6'
A175_DEPLOY_SHA = '3c5210fef9d4f1d4c95b286b0dbc61b6654fe055'
A175_DEPLOY_RUN = '40796450488'
A175_IMAGE = 'sha256:9841cba8df3345fa22e39457645ba250431ad1f465ce4204d92f73f7816215b9'
A174_CANDIDATE = 'b6e6ce75f29fe117aaa42b0bfcd9386ea93a0aa0'
A174_IMAGE = 'sha256:9ca6e7ca98f11bbb82353434f54ffab15b218ec54319c83ae1661aaf897a40e9'
A174_RECEIPT = '364c9bb9f013da603345c90219e7c889d5b04ecff951afe08f4bfc7ec03c1528'
A174_SCHEDULER = 'e0ddc6a0b50a83510b172a7653e5e26cad309aaa4a49207c9be7aa4cded6cec6'
A171_CANDIDATE = '4ce63a68c668cadd2a0aa7e8aded44ae4a3c46a6'
A171_IMAGE = 'sha256:e61052af13c01d9ce76e92f38b50c794dac8a146427e132d43a8a80554a879aa'
A175_OBSERVER_ID = '72ed632d0514ec1835e3dcb7983aa2b13cfbcf2b46c5a62132e5c739f7df86eb'
STABLE = 'porota-trading-bot:17.0.0-rc6'
PINNED_CANDIDATES = {A171_CANDIDATE,A174_CANDIDATE,A175_CANDIDATE}
OWNED_CONTAINERS = {
    '/porota_production_observer', '/porota_production_dashboard',
    '/porota_critical_approval_rc6',
}
IMAGE = re.compile(r'sha256:[0-9a-f]{64}')
CONTAINER = re.compile(r'[0-9a-f]{64}')
CANDIDATE_TAG = re.compile(
    r'(?:porota-predeploy-v2:|porota-trading-bot:17\.0\.0-rc6-candidate-)([0-9a-f]{40})')
RC6_DIGEST = re.compile(r'(porota-predeploy-v2|porota-trading-bot)@sha256:[0-9a-f]{64}')
LOCK = Path('/run/lock/porota-rc6-deploy-v2.lock')
BARRIER = Path('/run/lock/porota-rc6-deploy-v2-attempt.json')
ROOT = Path('/opt/porota-trading')
STAGING = Path('/tmp') / f'porota-deploy-v2-{A175_CANDIDATE}-{A175_DEPLOY_RUN}-1'
HASH = re.compile(r'[0-9a-f]{64}')


class Unverified(RuntimeError):
    pass


def metadata():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    values = []
    for key in ('id', 'region'):
        with opener.open('http://169.254.169.254/metadata/v1/' + key, timeout=3) as response:
            if response.status != 200:
                raise Unverified('DROPLET_METADATA_HTTP')
            value = response.read(65)
            if not value or len(value) > 64:
                raise Unverified('DROPLET_METADATA_LENGTH')
            values.append(value.decode('ascii').strip())
    if tuple(values) != EXPECTED_DROPLET:
        raise Unverified('DROPLET_IDENTITY_MISMATCH')
    return dict(zip(('id', 'region'), values))


def docker(*args):
    result = subprocess.run(['docker', *args], text=True, capture_output=True, timeout=12)
    if result.returncode or len(result.stdout) > 1048576:
        raise Unverified('DOCKER_INVENTORY_UNAVAILABLE')
    return result.stdout.strip()


def fields(raw, count):
    parts = raw.split('|')
    if len(parts) != count:
        raise Unverified('DOCKER_INSPECT_SHAPE')
    try:
        return tuple(json.loads(field) for field in parts)
    except (TypeError, ValueError) as exc:
        raise Unverified('DOCKER_INSPECT_JSON') from exc


def inspect_containers():
    ids = docker('ps', '-aq', '--no-trunc').splitlines()
    if len(ids) > 128 or len(ids) != len(set(ids)) or any(not CONTAINER.fullmatch(i) for i in ids):
        raise Unverified('CONTAINER_IDS_INVALID')
    records = {}
    for cid in ids:
        rid, image, name, status, running = fields(docker('inspect', '--format',
            '{{json .Id}}|{{json .Image}}|{{json .Name}}|{{json .State.Status}}|{{json .State.Running}}', cid), 5)
        if (rid != cid or not isinstance(image, str) or not IMAGE.fullmatch(image)
                or not isinstance(name, str) or not name.startswith('/')
                or status not in {'running', 'created', 'exited', 'paused', 'restarting', 'dead'}
                or type(running) is not bool):
            raise Unverified('CONTAINER_RECORD_INVALID')
        records[cid] = (image, name, status, running)
    return records


def inspect_images():
    ids = docker('image', 'ls', '-aq', '--no-trunc').splitlines()
    if len(ids) > 64 or any(not IMAGE.fullmatch(i) for i in ids):
        raise Unverified('IMAGE_IDS_INVALID')
    records = {}
    for iid in sorted(set(ids)):
        got, tags, digests, size, layers = fields(docker('image', 'inspect', '--format',
            '{{json .Id}}|{{json .RepoTags}}|{{json .RepoDigests}}|{{json .Size}}|{{json .RootFS.Layers}}', iid), 5)
        tags, digests, layers = tags or [], digests or [], layers or []
        if got != iid:
            raise Unverified('IMAGE_RECORD_ID_MISMATCH')
        if type(size) is not int or size < 0 or size > 100 * 1024**3:
            raise Unverified('IMAGE_RECORD_SIZE_INVALID')
        if not all(type(v) is list and len(v) <= 256 for v in (tags, digests, layers)):
            raise Unverified('IMAGE_RECORD_ARRAY_INVALID')
        if any(type(v) is not str or len(v) > 512 for v in tags + digests):
            raise Unverified('IMAGE_RECORD_ALIAS_INVALID')
        if any(type(v) is not str or not IMAGE.fullmatch(v) for v in layers):
            raise Unverified('IMAGE_RECORD_LAYER_INVALID')
        # Equal diffIDs may occur at different positions in a layer chain.
        # Preserve raw count separately from distinct overlap; ownership is
        # proven by image ID, aliases, and all-container references.
        records[iid] = dict(tags=tags, digests=digests, size=size, layers=layers)
    return records


def classify_aliases(tags, digests):
    rc6_candidate_shas = []
    other_aliases = 0
    for tag in tags:
        match = CANDIDATE_TAG.fullmatch(tag)
        if match:
            rc6_candidate_shas.append(match.group(1))
        elif tag != STABLE:
            other_aliases += 1
    rc6_digests = sum(RC6_DIGEST.fullmatch(digest) is not None for digest in digests)
    return dict(stable_alias=STABLE in tags,
        pinned_candidate_alias_count=sum(sha in PINNED_CANDIDATES for sha in rc6_candidate_shas),
        candidate_sha_tokens=sorted(set(rc6_candidate_shas)),
        other_alias_count=other_aliases,
        rc6_repo_digest_count=rc6_digests,
        other_digest_count=len(digests) - rc6_digests)


def sealed_json(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or
                st.st_uid != 0 or st.st_size < 2 or st.st_size > limit or
                stat.S_IMODE(st.st_mode) & 0o022):
            raise Unverified('DURABLE_RECEIPT_PATH_UNSAFE')
        raw = os.read(fd, limit + 1)
        if len(raw) != st.st_size:
            raise Unverified('DURABLE_RECEIPT_SIZE_CHANGED')
        value = json.loads(raw)
        if type(value) is not dict:
            raise Unverified('DURABLE_RECEIPT_SHAPE')
        return value, hashlib.sha256(raw).hexdigest()
    finally:
        os.close(fd)


def durable_state():
    deploy = ROOT/'data/deploy'
    marker, marker_sha = sealed_json(deploy/'CURRENT_STATE_V2.json',65536)
    receipt, receipt_sha = sealed_json(deploy/('quiescence-'+A175_CANDIDATE+'.json'),65536)
    scheduler, scheduler_sha = sealed_json(deploy/('scheduler-suspend-'+A175_CANDIDATE+'.json'),65536)
    if (marker.get('candidate_sha') != A175_CANDIDATE or
            marker.get('deploy_sha') != A175_DEPLOY_SHA or
            marker.get('image_id') != A175_IMAGE or
            marker.get('validation_status') != 'DEPLOYED_VALIDATION_PENDING' or
            marker.get('mode') != 'PRODUCTION_PAPER' or
            marker.get('real_orders_sent') != 0 or
            marker.get('real_order_routes') != 'NOT_CALLED' or
            marker.get('ppi_watch_untouched') is not True or
            receipt.get('candidate_sha') != A175_CANDIDATE or
            receipt.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED' or
            receipt.get('installation_started') is not True or
            receipt.get('a174_pending_adopted') is not True or
            receipt.get('a174_origin_sha256') != A174_RECEIPT or
            receipt.get('ppi_watch_accessed') is not False or
            receipt.get('real_orders_sent') != 0 or
            scheduler.get('candidate_sha') != A175_CANDIDATE or
            scheduler.get('status') != 'SUSPENDED' or
            scheduler.get('paused_from') != A174_CANDIDATE or
            scheduler.get('a174_running_origin') is not True or
            scheduler.get('a174_origin_sha256') != A174_RECEIPT or
            scheduler.get('prior_receipt_sha256') != A174_SCHEDULER or
            len(scheduler.get('timers') or []) != 16):
        raise Unverified('A175_DURABLE_PROVENANCE_DRIFT')
    runtime=receipt.get('candidate_runtime_after_failure') or {}
    if (runtime.get('status') != 'running' or
            set(runtime.get('containers') or {}) !=
                {'porota_production_observer','porota_production_dashboard'} or
            runtime.get('flat_book',{}).get('real_orders_sent') != 0):
        raise Unverified('A175_FAILED_RUNTIME_RECEIPT_DRIFT')
    return dict(marker_sha256=marker_sha, quiescence_sha256=receipt_sha,
                scheduler_sha256=scheduler_sha, marker_status=marker['validation_status'],
                quiescence_status=receipt['status'], scheduler_status=scheduler['status'],
                scheduler_timer_count=16, recorded_container_ids={
                    '/' + name:runtime['containers'][name].get('id') for name in runtime['containers']},
                recorded_image_id=A175_IMAGE)


def stage_state():
    try:
        st=STAGING.lstat()
    except FileNotFoundError:
        return {'present':False}
    if not stat.S_ISDIR(st.st_mode) or st.st_nlink < 2:
        raise Unverified('A175_STAGING_TYPE_CHANGED')
    return {'present':True,'uid':st.st_uid,'mode_octal':oct(stat.S_IMODE(st.st_mode)),
            'inode':st.st_ino,'allocated_root_bytes':st.st_blocks*512}


def paper_state():
    path=ROOT/'data/paper_v17/observer_v17.db'
    connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA busy_timeout=500')
        row=connection.execute('SELECT mode,real_orders_sent,process_state,session_state '
                               'FROM observer_state WHERE id=1').fetchone()
    finally:
        connection.close()
    if (not row or row[0]!='PRODUCTION_PAPER' or type(row[1]) is not int or row[1]!=0):
        raise Unverified('PAPER_ZERO_UNVERIFIED')
    return dict(mode=row[0],real_orders_sent=row[1],
                process_state=row[2],session_state=row[3])


def barrier_status():
    try:
        fd = os.open(BARRIER, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return 'ABSENT'
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or
                stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1 or st.st_size > 1024):
            raise Unverified('BARRIER_UNSAFE')
        payload = json.loads(os.read(fd, 1025))
        if (not isinstance(payload, dict) or set(payload) !=
                {'candidate', 'run', 'attempt', 'phase', 'status'} or
                not re.fullmatch(r'[0-9a-f]{40}', payload['candidate']) or
                not re.fullmatch(r'[1-9][0-9]*', payload['run']) or
                not re.fullmatch(r'[1-9][0-9]*', payload['attempt']) or
                payload['status'] not in {'IDLE', 'INFLIGHT', 'CONTRACT_ONLY'}):
            raise Unverified('BARRIER_INVALID')
        return payload['status']
    finally:
        os.close(fd)


def snapshot():
    containers, images = inspect_containers(), inspect_images()
    refs = {iid: [] for iid in images}
    for cid, (iid, name, status, running) in containers.items():
        if iid not in refs:
            raise Unverified('CONTAINER_IMAGE_MISSING')
        refs[iid].append(cid)
    out = []
    for iid, row in sorted(images.items()):
        aliases = classify_aliases(row['tags'], row['digests'])
        sha_tokens = aliases['candidate_sha_tokens']
        digest_repos = {value.split('@',1)[0] for value in row['digests']}
        tag_repos = {value.split(':',1)[0] for value in row['tags']}
        shared_digest_count = sum(value in other['digests'] for value in row['digests']
                                  for oid,other in images.items() if oid != iid)
        exact_tags = ({'porota-predeploy-v2:'+sha_tokens[0],
                       'porota-trading-bot:17.0.0-rc6-candidate-'+sha_tokens[0]}
                      if len(sha_tokens)==1 else set())
        candidate_only = (len(sha_tokens)==1 and sha_tokens[0] in PINNED_CANDIDATES
            and bool(row['tags']) and set(row['tags']) <= exact_tags and
            aliases['other_alias_count']==0 and aliases['other_digest_count']==0 and
            digest_repos <= tag_repos and shared_digest_count==0)
        layers = set(row['layers'])
        other_layers = set().union(*(set(other['layers']) for oid, other in images.items() if oid != iid))
        other_referenced_layers = set().union(*(
            set(other['layers']) for oid, other in images.items()
            if oid != iid and refs[oid]))
        # This is strictly structural overlap. Docker Size is virtual image
        # size, and diffID counts are not measured reclaimable disk bytes.
        out.append(dict(image_id=iid, virtual_size_bytes=row['size'],
            layer_count=len(row['layers']), distinct_layer_count=len(layers),
            layers_shared_with_referenced_count=len(layers & other_referenced_layers),
            unique_diffid_count=len(layers - other_layers),
            all_container_refs=len(refs[iid]),
            ref_container_ids=sorted(refs[iid]),
            owned_container_refs=sum(containers[cid][1] in OWNED_CONTAINERS for cid in refs[iid]),
            aliases=dict(aliases,shared_digest_alias_count=shared_digest_count,
                         digest_repositories_paired_with_tags=digest_repos <= tag_repos),
            cleanup_class=('PROTECTED_REFERENCED' if refs[iid] else
                'PROTECTED_STABLE' if aliases['stable_alias'] else
                'PROTECTED_OTHER_ALIAS' if not candidate_only else
                'CANDIDATE_ONLY_REVIEW_PROVENANCE')))
    owned = {name: next(({'id': cid, 'image': value[0], 'status': value[2],
                         'running': value[3]}
                        for cid, value in containers.items() if value[1] == name), None)
             for name in sorted(OWNED_CONTAINERS)}
    if (any(value is None for value in owned.values()) or
            owned['/porota_production_observer']['image'] != A175_IMAGE or
            owned['/porota_production_dashboard']['image'] != A175_IMAGE or
            owned['/porota_production_observer']['id'] != A175_OBSERVER_ID or
            any(value['status'] != 'running' or value['running'] is not True for value in owned.values()) or
            A175_IMAGE not in images or STABLE not in images[A175_IMAGE]['tags']):
        raise Unverified('A175_RUNTIME_IDENTITY_DRIFT')
    all_containers = {cid: {'image_id':iid,'status':status,'running':running,
                            'owner':name if name in OWNED_CONTAINERS else 'OTHER'}
                      for cid,(iid,name,status,running) in containers.items()}
    return dict(owned_containers=owned, all_containers=all_containers,
                other_container_count=len(containers)-len(owned),
                images=out, raw_ids=(containers, images))


def audit():
    metadata_result = metadata()
    # The canonical lock is already created by Deploy V2. Opening read-only
    # and flocking it does not alter the lock or the durable attempt barrier.
    fd = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or
                stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1):
            raise Unverified('HOST_MUTEX_UNSAFE')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Unverified('HOST_MUTEX_BUSY') from exc
        before = shutil.disk_usage('/').free
        vfs_before = os.statvfs('/')
        barrier = barrier_status()
        if barrier != 'ABSENT':
            raise Unverified('DEPLOY_BARRIER_NOT_ABSENT')
        receipts = durable_state()
        stage = stage_state()
        paper = paper_state()
        first = snapshot()
        second = snapshot()
        after = shutil.disk_usage('/').free
        vfs_after = os.statvfs('/')
        if (first['raw_ids'] != second['raw_ids'] or barrier != barrier_status() or
                receipts != durable_state() or stage != stage_state() or
                paper != paper_state()):
            raise Unverified('DOCKER_OR_DURABLE_STATE_CHANGED_DURING_AUDIT')
        if {name:row['id'] for name,row in first['owned_containers'].items()
                if name in receipts['recorded_container_ids']} != receipts['recorded_container_ids']:
            raise Unverified('A175_RECEIPT_CONTAINER_IDS_CHANGED')
        first.pop('raw_ids')
        return dict(schema='RC6_A175_DOCKER_OWNERSHIP_RO_V1', status='GREEN_INVENTORY',
                    read_only=True, droplet=metadata_result, barrier_status=barrier,
                    free_bytes_before=before, free_bytes_after=after,
                    inode_free_before=vfs_before.f_ffree,
                    inode_free_after=vfs_after.f_ffree,
                    inode_total=vfs_after.f_files,
                    pretransfer_required_a175_bytes=4059036627,
                    current_margin_vs_a175_formula_bytes=after-4059036627,
                    reclaimable_bytes_verified=None, staging=stage, receipts=receipts,
                    paper=paper, ppi_watch='NOT_ACCESSED',host_mutations=0, **first)
    finally:
        os.close(fd)


def main():
    try:
        report = audit()
    except (Unverified, OSError, ValueError, TypeError, KeyError, UnicodeError,
            subprocess.TimeoutExpired, sqlite3.Error) as exc:
        reason = str(exc) if isinstance(exc, Unverified) else type(exc).__name__
        report = {'schema': 'RC6_A175_DOCKER_OWNERSHIP_RO_V1',
                  'status': 'UNVERIFIED', 'read_only': True, 'reason': reason}
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'GREEN_INVENTORY' else 2


if __name__ == '__main__':
    raise SystemExit(main())
