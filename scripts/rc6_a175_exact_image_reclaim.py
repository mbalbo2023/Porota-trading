#!/usr/bin/env python3
"""Remove only A174 and A171 orphaned RC6 image IDs after A175 failure.

Predeploy/Deploy provenance is verified by the runner. A175 RO #40798107395
found zero all-container refs and exact candidate-only aliases for both old
images. Recheck all facts under host mutex, with no force/prune or other writes.
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
ROOT_LOCK = Path('/run/lock/porota-rc6-deploy-v2.lock')
BARRIER = Path('/run/lock/porota-rc6-deploy-v2-attempt.json')
DB = Path('/opt/porota-trading/data/paper_v17/observer_v17.db')
STABLE = 'porota-trading-bot:17.0.0-rc6'
ROOT = Path('/opt/porota-trading')
A175_SHA = '18ba517a846c6798f0d12f39072465fc02fa03b5'
A175_DEPLOY_SHA = '3c5210fef9d4f1d4c95b286b0dbc61b6654fe055'
ACTIVE = 'sha256:9841cba8df3345fa22e39457645ba250431ad1f465ce4204d92f73f7816215b9'
CRITICAL = 'sha256:586464c20b74ab3a7ba842df8dfafcd76f21eb4c7bed553f23f6f187924ff735'
TARGETS = (
    ('b6e6ce75f29fe117aaa42b0bfcd9386ea93a0aa0',
     'sha256:9ca6e7ca98f11bbb82353434f54ffab15b218ec54319c83ae1661aaf897a40e9'),
    ('4ce63a68c668cadd2a0aa7e8aded44ae4a3c46a6',
     'sha256:e61052af13c01d9ce76e92f38b50c794dac8a146427e132d43a8a80554a879aa'),
)
OWNED = {
    '72ed632d0514ec1835e3dcb7983aa2b13cfbcf2b46c5a62132e5c739f7df86eb': ACTIVE,
    '9b01cf17b7c3e46d96474d998840fc0e4c4c4f828dd825bb44744710b99686bb': ACTIVE,
    '0d566777f5dca7342d0fbcb3ff538248445069d126064e88af963c3c8d023a21': CRITICAL,
}
RECEIPT_SHA = '5ddf0d5e340f7cac96bcbb339eeaeea3ed4aad635a2201fd55fffe375c961022'
SCHEDULER_SHA = '64a8afc3591c42d4e58f3acb34905043c0ffe323b695ecf3337f99b66d6b0b6e'
MARKER_SHA = '42055f5225def0560453b2c40b5e4695082fa3235fc42a4596210cf0c1b8b161'
STAGING = Path('/tmp') / f'porota-deploy-v2-{A175_SHA}-40796450488-1'
IMAGE = re.compile(r'sha256:[0-9a-f]{64}')
DIGEST = re.compile(r'(porota-predeploy-v2|porota-trading-bot)@sha256:[0-9a-f]{64}')


class Rejected(RuntimeError):
    pass


def metadata():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    values = []
    for key in ('id', 'region'):
        with opener.open('http://169.254.169.254/metadata/v1/' + key, timeout=3) as response:
            if response.status != 200:
                raise Rejected('DROPLET_METADATA_HTTP')
            value = response.read(65)
            if not value or len(value) > 64:
                raise Rejected('DROPLET_METADATA_INVALID')
            values.append(value.decode('ascii').strip())
    if tuple(values) != EXPECTED_DROPLET:
        raise Rejected('DROPLET_IDENTITY_MISMATCH')
    return dict(zip(('id', 'region'), values))


def docker(*args):
    result = subprocess.run(['docker', *args], text=True, capture_output=True, timeout=15)
    if result.returncode or len(result.stdout) > 1048576:
        raise Rejected('DOCKER_COMMAND_FAILED')
    return result.stdout.strip()


def read_barrier_absent():
    try:
        fd = os.open(BARRIER, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return
    else:
        os.close(fd)
        raise Rejected('DEPLOY_BARRIER_PRESENT')


def current_image_ids():
    ids = docker('image', 'ls', '-aq', '--no-trunc').splitlines()
    if len(ids) > 64 or any(not IMAGE.fullmatch(i) for i in ids):
        raise Rejected('IMAGE_INVENTORY_INVALID')
    return set(ids)


def image_record(image_id):
    output = docker('image', 'inspect', '--format',
        '{{json .Id}}|{{json .RepoTags}}|{{json .RepoDigests}}|{{json .RootFS.Layers}}', image_id)
    parts = output.split('|')
    if len(parts) != 4:
        raise Rejected('IMAGE_INSPECT_INVALID')
    try:
        got, tags, digests, layers = map(json.loads, parts)
    except (ValueError, TypeError) as exc:
        raise Rejected('IMAGE_INSPECT_INVALID') from exc
    tags, digests, layers = tags or [], digests or [], layers or []
    if (got != image_id or not all(type(v) is list and len(v) <= 256 for v in
                                   (tags, digests, layers)) or
            any(type(v) is not str or len(v) > 512 for v in tags + digests) or
            any(type(v) is not str or not IMAGE.fullmatch(v) for v in layers)):
        raise Rejected('IMAGE_INSPECT_INVALID')
    return tags, digests, layers


def exact_tag_set(sha):
    return {'porota-predeploy-v2:' + sha,
            'porota-trading-bot:17.0.0-rc6-candidate-' + sha}


def exact_target_aliases(sha, tags, digests, *, initial):
    expected = exact_tag_set(sha)
    if (len(tags) != len(set(tags)) or
            (set(tags) != expected if initial else not set(tags).issubset(expected)) or
            len(digests) > 2 or len(digests) != len(set(digests)) or
            any(type(d) is not str or DIGEST.fullmatch(d) is None for d in digests)):
        raise Rejected('TARGET_ALIAS_OR_DIGEST_DRIFT')
    repos = {d.split('@', 1)[0] for d in digests}
    valid_repos = {'porota-predeploy-v2', 'porota-trading-bot'}
    if (repos != valid_repos if initial else not repos.issubset(valid_repos)):
        raise Rejected('TARGET_DIGEST_REPOSITORY_DRIFT')
    return expected


def paper_mode():
    with sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True, timeout=.5) as conn:
        conn.execute('PRAGMA query_only=ON')
        row = conn.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
    if not row or row[0] != 'PRODUCTION_PAPER' or type(row[1]) is not int or row[1] != 0:
        raise Rejected('PAPER_ZERO_UNVERIFIED')


def sealed_state():
    expected = (
        (ROOT/'data/deploy/CURRENT_STATE_V2.json', MARKER_SHA,
         {'candidate_sha':A175_SHA,'deploy_sha':A175_DEPLOY_SHA,
          'image_id':ACTIVE,'validation_status':'DEPLOYED_VALIDATION_PENDING',
          'mode':'PRODUCTION_PAPER','real_orders_sent':0,
          'real_order_routes':'NOT_CALLED','ppi_watch_untouched':True}),
        (ROOT/'data/deploy'/('quiescence-'+A175_SHA+'.json'),RECEIPT_SHA,
         {'candidate_sha':A175_SHA,'status':'FIX_FORWARD_VALIDATION_REQUIRED',
          'a174_pending_adopted':True,'installation_started':True,
          'a174_origin_sha256':'364c9bb9f013da603345c90219e7c889d5b04ecff951afe08f4bfc7ec03c1528',
          'ppi_watch_accessed':False,'real_orders_sent':0}),
        (ROOT/'data/deploy'/('scheduler-suspend-'+A175_SHA+'.json'),SCHEDULER_SHA,
         {'candidate_sha':A175_SHA,'status':'SUSPENDED',
          'paused_from':'b6e6ce75f29fe117aaa42b0bfcd9386ea93a0aa0',
          'a174_running_origin':True,
          'prior_receipt_sha256':'e0ddc6a0b50a83510b172a7653e5e26cad309aaa4a49207c9be7aa4cded6cec6'}),
    )
    for path,digest,fields in expected:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            st=os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or st.st_nlink!=1 or
                    st.st_size<2 or st.st_size>65536 or stat.S_IMODE(st.st_mode)&0o022):
                raise Rejected('DURABLE_PATH_UNSAFE')
            raw=os.read(fd,65537)
            if (len(raw)!=st.st_size or hashlib.sha256(raw).hexdigest()!=digest):
                raise Rejected('DURABLE_RECEIPT_DIGEST_CHANGED')
            row=json.loads(raw)
            if type(row) is not dict or any(row.get(k)!=v for k,v in fields.items()):
                raise Rejected('DURABLE_RECEIPT_FIELDS_CHANGED')
        finally:
            os.close(fd)
    if STAGING.is_symlink() or STAGING.exists():
        raise Rejected('A175_ATTEMPT_STAGING_REAPPEARED')


def protected_state(expected_ids):
    read_barrier_absent()
    paper_mode()
    sealed_state()
    ids = docker('ps', '-aq', '--no-trunc').splitlines()
    if len(ids) != 3 or set(ids) != set(OWNED):
        raise Rejected('CONTAINER_SET_CHANGED')
    for cid in ids:
        output = docker('inspect', '--format',
            '{{json .Id}}|{{json .Image}}|{{json .State.Status}}|{{json .State.Running}}', cid)
        try:
            got, image, status, running = map(json.loads, output.split('|'))
        except (ValueError, TypeError) as exc:
            raise Rejected('CONTAINER_INSPECT_INVALID') from exc
        if got != cid or image != OWNED[cid] or status != 'running' or running is not True:
            raise Rejected('CONTAINER_IDENTITY_CHANGED')
    if current_image_ids() != expected_ids:
        raise Rejected('IMAGE_SET_CHANGED')
    tags, _, _ = image_record(ACTIVE)
    if STABLE not in tags or not exact_tag_set(A175_SHA).issubset(tags):
        raise Rejected('ACTIVE_STABLE_TAG_CHANGED')
    no_shared_digest_aliases(expected_ids)
    return True


def initial_layers(expected_ids):
    rows = {iid: set(image_record(iid)[2]) for iid in expected_ids}
    active = rows[ACTIVE] | rows[CRITICAL]
    for _, iid in TARGETS:
        raw = image_record(iid)[2]
        # A172 measured eight distinct diffIDs outside the REFERENCED image
        # union. Two old targets may share those same eight diffIDs.
        if (len(raw), len(rows[iid]), len(rows[iid] & active), len(rows[iid] - active)) != (27, 13, 5, 8):
            raise Rejected('LAYER_BASELINE_DRIFT')


def no_shared_digest_aliases(expected_ids):
    rows={iid:image_record(iid) for iid in expected_ids}
    for _,iid in TARGETS:
        if iid not in expected_ids:
            continue
        own=set(rows[iid][1])
        if any(own & set(row[1]) for other,row in rows.items() if other!=iid):
            raise Rejected('TARGET_SHARED_DIGEST_ALIAS')


def inode_free():
    vfs=os.statvfs('/')
    return {'free':vfs.f_ffree,'total':vfs.f_files}


def reclaim(report):
    report.update(status='RED', removed_tags=[], removed_digests_count=0,
                  removed_image_ids=[], steps=[])
    report['droplet'] = metadata()
    fd = os.open(ROOT_LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or
                stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1):
            raise Rejected('HOST_MUTEX_UNSAFE')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Rejected('HOST_MUTEX_BUSY') from exc
        expected_ids = {ACTIVE, CRITICAL} | {iid for _, iid in TARGETS}
        protected_state(expected_ids)
        initial_layers(expected_ids)
        no_shared_digest_aliases(expected_ids)
        for sha, iid in TARGETS:
            tags, digests, _ = image_record(iid)
            exact_target_aliases(sha, tags, digests, initial=True)
        report['disk_before'] = shutil.disk_usage('/').free
        report['inodes_before'] = inode_free()
        for sha, iid in TARGETS:
            pre = shutil.disk_usage('/').free
            pre_inode = inode_free()
            pending = exact_tag_set(sha)
            original_digests = set(image_record(iid)[1])
            for tag in sorted(pending):
                protected_state(expected_ids)
                tags, digests, _ = image_record(iid)
                exact_target_aliases(sha, tags, digests, initial=False)
                if set(tags) != pending:
                    raise Rejected('TARGET_TAG_SET_CHANGED')
                if not set(digests).issubset(original_digests):
                    raise Rejected('TARGET_DIGEST_CHANGED')
                docker('image', 'rm', '--no-prune', tag)  # Deliberately no --force.
                report['removed_tags'].append(tag)
                pending.remove(tag)
                if pending:
                    protected_state(expected_ids)
                # The final untag may remove an image automatically. Both
                # possibilities are checked below; never infer reclaimed bytes.
                now = current_image_ids()
                if pending:
                    if now != expected_ids:
                        raise Rejected('IMAGE_REMOVED_PREMATURELY')
                    got_tags, got_digests, _ = image_record(iid)
                    exact_target_aliases(sha, got_tags, got_digests, initial=False)
                    if set(got_tags) != pending:
                        raise Rejected('TARGET_RETAGGED')
                elif now not in (expected_ids, expected_ids - {iid}):
                    raise Rejected('IMAGE_SET_CHANGED_AFTER_LAST_TAG')
            if iid in current_image_ids():
                protected_state(expected_ids)
                tags, digests, _ = image_record(iid)
                exact_target_aliases(sha, tags, digests, initial=False)
                if tags:
                    raise Rejected('TARGET_STILL_TAGGED')
                for digest in sorted(digests):
                    protected_state(expected_ids)
                    tags_now, digests_now, _ = image_record(iid)
                    exact_target_aliases(sha, tags_now, digests_now, initial=False)
                    if tags_now or digest not in digests_now or not set(digests_now).issubset(original_digests):
                        raise Rejected('TARGET_DIGEST_CHANGED')
                    # RepoDigests are names, too. Remove each exact verified
                    # RC6 digest without force, rechecking the active pair.
                    docker('image', 'rm', '--no-prune', digest)
                    report['removed_digests_count'] += 1
                    now = current_image_ids()
                    if now not in (expected_ids, expected_ids - {iid}):
                        raise Rejected('IMAGE_SET_CHANGED_AFTER_DIGEST')
                    if iid not in now:
                        break
                if iid in current_image_ids():
                    protected_state(expected_ids)
                    tags_now, digests_now, _ = image_record(iid)
                    exact_target_aliases(sha, tags_now, digests_now, initial=False)
                    if tags_now or digests_now:
                        raise Rejected('TARGET_ALIASES_REMAIN')
                    docker('image', 'rm', '--no-prune', iid)  # Deliberately no --force.
            expected_ids.remove(iid)
            protected_state(expected_ids)
            after = shutil.disk_usage('/').free
            after_inode=inode_free()
            report['removed_image_ids'].append(iid)
            report['steps'].append({'candidate_sha': sha, 'image_id': iid,
                'disk_before': pre, 'disk_after': after, 'observed_delta_bytes': after - pre,
                'inodes_before':pre_inode,'inodes_after':after_inode,
                'protected_containers_unchanged': True})
        final_free = shutil.disk_usage('/').free
        report.update(status='GREEN', disk_after=final_free,
                      inodes_after=inode_free(),
                      observed_delta_bytes=final_free-report['disk_before'],
                      real_orders_sent=0, ppi_watch='NOT_ACCESSED')
        return report
    finally:
        os.close(fd)


def main():
    report = {'schema': 'RC6_A175_EXACT_IMAGE_RECLAIM_V1', 'status': 'RED'}
    try:
        reclaim(report)
    except (Rejected, OSError, ValueError, TypeError, UnicodeError,
            sqlite3.Error, subprocess.TimeoutExpired) as exc:
        report['reason'] = str(exc) if isinstance(exc, Rejected) else type(exc).__name__
        if 'disk_before' in report:
            report['disk_after'] = shutil.disk_usage('/').free
            report['observed_delta_bytes'] = report['disk_after'] - report['disk_before']
            report['inodes_after'] = inode_free()
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'GREEN' else 2


if __name__ == '__main__':
    raise SystemExit(main())
